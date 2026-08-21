# -*- coding: utf-8 -*-
"""
API 서버 (구현 순서 ⑥) — 답변 조립기를 `GET /answer` 주소로 서비스한다.

무엇: 공식 평가 규격 그대로 — `GET /answer?question_id=&question=` 에 5필드
      (전부 문자열) JSON 을 돌려준다. 추가로 /health(상태 확인)와
      / (브라우저에서 질문을 직접 입력해 보는 테스트 화면)를 제공한다.
왜 이렇게:
  - 서버 시작 시 저장소(DuckDB·이름 사전·지식그래프·벡터)를 **한 번만** 올려두고
    모든 요청이 공유한다 — 질문마다 다시 읽으면 15초를 지킬 수 없다.
  - 요청마다 시간 예산(Deadline)을 걸어 늦어진 단계는 자동 강등한다.
  - 같은 질문은 캐시로 즉답하되, **실패·강등된 응답은 캐시에 넣지 않는다** —
    주최가 재시도(최대 2회)했을 때 같은 실패를 돌려주는 오염을 막기 위함.
  - 어떤 오류가 나도 HTTP 200 + 유효한 5필드 JSON 을 반환한다(전역 예외 처리) —
    채점 프로그램이 파싱에 실패하는 일 자체를 없앤다.

실행:
  python server/app.py                       # 기본: 그래프·벡터·HCX 전부 켜고 8000 포트
  python server/app.py --port 80             # 평가용 표준 포트(NCP 배포 시)
  python server/app.py --light               # 가볍게: 그래프·벡터·HCX 끄고 규칙 엔진만(개발용)
테스트 화면: 서버 켠 뒤 브라우저에서 http://localhost:8000/ 접속.
"""
import argparse
import dataclasses
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))            # server/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from config.env_loader import load_env  # noqa: E402
load_env()   # 저장소 최상위 .env 를 읽는다(운영체제 환경변수가 우선).

from fastapi import FastAPI                                   # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse      # noqa: E402

from engine.answer_service import answer_question, serialize_answer  # noqa: E402
from engine.channels import RuntimeContext                    # noqa: E402
from engine.deadline import Deadline                          # noqa: E402
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index  # noqa: E402

CACHE_PATH_DEFAULT = os.path.join(ROOT, "storage", "output", "answer_cache.jsonl")

# 응답이 이 표식들을 담고 있으면 "실패·강등"으로 보고 캐시하지 않는다
_DEGRADED_MARKERS = ("오류 ", "폴백", "강등", "생략", "전역 오류")

# 공식 규격(과제설명 PDF p.11)의 응답 헤더 — `application/json; charset=utf-8`.
# JSON 은 원래 UTF-8 이지만 charset 을 명시해 두면 채점 프로그램 쪽 해석 여지가 없다.
JSON_MEDIA_TYPE = "application/json; charset=utf-8"


def _json(payload):
    return JSONResponse(payload, media_type=JSON_MEDIA_TYPE)


def is_cacheable(out):
    """정상 완결 응답만 캐시 — 거절(확인 불가)은 정상 답변이므로 캐시 대상이다."""
    trace = out.get("think_trace", "")
    return not any(m in trace for m in _DEGRADED_MARKERS)


# ---------------------------------------------------------------------------
# 실행 자원 준비 — 서버 시작 시 1회
# ---------------------------------------------------------------------------

def build_runtime(kg_tables="all", with_vector=True, with_llm=True, with_generator=True):
    """저장소·AI 클라이언트를 미리 올린 (ctx, llm_router, generator).

    kg_tables: "all"(전체) | "none"(그래프 끔) | "kr_etf,constituents"(부분).
    HCX 클라이언트는 API 키가 없으면 자동으로 꺼진다(규칙 엔진만으로도 유효 응답).
    타임아웃: 계획 수립 6초·문장 생성 8초·임베딩 5초 — Deadline(생성 진입 한계 7초)
    강등과 함께 15초(무감점 경계 — 설명회 발화, 8/22 확인) 안에 끝난다.
    (8/22 오전 '정확도 우선 60초'로 올렸다가 감점 경계 확인으로 당일 복원.)
    """
    import duckdb
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    index = build_entity_index(con)
    ctx = RuntimeContext(con=con, index=index)

    if kg_tables and kg_tables != "none":
        from kg.kg_store import TripleStore
        tables = None if kg_tables == "all" else [t.strip() for t in kg_tables.split(",")]
        ctx.kg_store = TripleStore.from_dir(os.path.join(ROOT, "kg", "output"), tables)

    has_key = bool(os.environ.get("CLOVASTUDIO_API_KEY"))
    if with_vector and has_key:
        try:
            from agent.clova_embedding import ClovaEmbeddingClient
            from vector.vector_store import VectorStore
            ctx.vstore = VectorStore.load()
            ctx.embedder = ClovaEmbeddingClient(timeout=5.0).embed
        except Exception:
            pass                                      # 벡터 없이도 서비스 가능(키워드가 대체)

    llm_router, generator = None, None
    if with_llm and has_key:
        from agent.clova_client import ClovaChatClient
        from engine.router_llm import make_llm_router
        llm_router = make_llm_router(ClovaChatClient(model="HCX-005", timeout=6.0))
    if with_generator and has_key:
        from agent.clova_client import ClovaChatClient
        from engine.generator import make_hcx_generator
        generator = make_hcx_generator(ClovaChatClient(model="HCX-005", timeout=8.0))
    return ctx, llm_router, generator


# ---------------------------------------------------------------------------
# 앱 구성
# ---------------------------------------------------------------------------

def create_app(ctx, llm_router=None, generator=None, cache_path=CACHE_PATH_DEFAULT):
    app = FastAPI(title="금융상품 질의응답 에이전트", docs_url=None, redoc_url=None)

    # 캐시: 메모리 dict + 디스크 jsonl(재시작 생존). 실패·강등 응답은 저장 안 함.
    cache = {}
    if cache_path and os.path.exists(cache_path):
        with io.open(cache_path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    cache[row["question"]] = row
                except (json.JSONDecodeError, KeyError):
                    continue

    def _store_cache(key, out):
        cache[key] = out
        if cache_path:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with io.open(cache_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(out, ensure_ascii=False) + "\n")

    @app.get("/answer")
    def answer(question_id: str = "", question: str = ""):
        # 두 파라미터 모두 기본값을 두어 빠져도 422 가 아니라 200 + 5필드로 응답한다.
        # 규격에 없는 파라미터(예: &foo=bar)는 FastAPI 가 무시하므로 500 없이 처리된다
        # (과제설명 PDF p.11 "미정의 파라미터가 들어와도 500 없이 처리" — 테스트로 잠금).
        t0 = time.perf_counter()
        q = (question or "").strip()
        if not q:
            return _json(serialize_answer(
                question_id, question, [],
                "빈 질문 — 검증 없이 안내 응답",
                "질문이 비어 있습니다. question 파라미터에 질문을 담아 다시 호출해 주세요."))

        if q in cache:                                # 캐시 즉답(정상 완결 응답만 들어있음)
            out = dict(cache[q])
            out["question_id"] = str(question_id or out.get("question_id", ""))
            out["question"] = question                # 규격: 요청값을 그대로 돌려준다
            out["think_trace"] = out["think_trace"] + "\n(캐시 응답)"
            return _json(out)

        deadline = Deadline()
        req_ctx = dataclasses.replace(ctx, deadline=deadline)
        try:
            out = answer_question(q, req_ctx, question_id=question_id,
                                  llm_router=llm_router, generator=generator,
                                  deadline=deadline)
        except Exception as exc:                      # 전역 방어 — 어떤 오류에도 유효 5필드
            out = serialize_answer(
                question_id, q, [],
                f"전역 오류: {type(exc).__name__}: {exc}",
                "일시적인 내부 오류로 이번 요청을 처리하지 못했습니다. "
                "같은 질문으로 다시 시도해 주세요.")
        out["question"] = question                    # 규격: 요청값을 그대로 돌려준다(공백 포함)
        out["think_trace"] += f"\n응답 시간: {time.perf_counter() - t0:.2f}초"
        if is_cacheable(out):
            _store_cache(q, out)
        return _json(out)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "db": ctx.con is not None,
            "index_entries": getattr(ctx.index, "entries", 0),
            "graph_triples": getattr(ctx.kg_store, "triples", 0) if ctx.kg_store else 0,
            "vector": ctx.vstore is not None,
            "hcx_router": llm_router is not None,
            "hcx_generator": generator is not None,
            "cache_size": len(cache),
        }

    @app.get("/", response_class=HTMLResponse)
    def test_page():
        return _TEST_PAGE

    return app


# 브라우저 테스트 화면 — 질문을 입력하면 /answer 를 호출해 5필드를 보여준다
_TEST_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>금융상품 에이전트 — 질문 시험대</title>
<style>
 body{font-family:'Malgun Gothic',Apple SD Gothic Neo,sans-serif;max-width:860px;
      margin:2rem auto;padding:0 1rem;background:#f7f8fa;color:#1c1e21}
 h1{font-size:1.3rem} .hint{color:#667;font-size:.85rem}
 form{display:flex;gap:.5rem;margin:1rem 0}
 input{flex:1;padding:.7rem .9rem;font-size:1rem;border:1px solid #c8ccd4;border-radius:8px}
 button{padding:.7rem 1.2rem;font-size:1rem;border:0;border-radius:8px;
        background:#f77f00;color:#fff;cursor:pointer} button:disabled{background:#ccc}
 .card{background:#fff;border:1px solid #e3e6ec;border-radius:10px;padding:1rem;margin:.8rem 0}
 .label{font-weight:700;font-size:.8rem;color:#889;margin-bottom:.3rem}
 pre{white-space:pre-wrap;word-break:break-all;margin:0;font-size:.9rem;line-height:1.5}
 details{margin-top:.6rem} summary{cursor:pointer;color:#556;font-size:.85rem}
 .time{color:#889;font-size:.8rem}
 .ex{margin:.15rem 0;font-size:.85rem}
 .ex a{color:#0b62c4;text-decoration:none;cursor:pointer}
</style></head><body>
<h1>금융상품 질의응답 에이전트 — 질문 시험대</h1>
<p class="hint">질문을 입력하면 실제 평가와 동일한 경로(<code>GET /answer</code>)로 답변을 받아 보여줍니다.
데이터 기준일 2026-07-11 — 그 이후 정보나 없는 상품을 물으면 "확인할 수 없음"이 정답입니다.</p>
<form id="f"><input id="q" placeholder="예: 순자산총액 기준으로 국내 ETF 상위 5개 알려줘" autofocus>
<button id="b">질문하기</button></form>
<div class="ex">예시:
 <a onclick="ask('현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘')">채권 필터</a> ·
 <a onclick="ask('삼성전자가 포함된 ETF 알려줘')">종목 편입</a> ·
 <a onclick="ask('반도체 산업에 집중 투자하는 해외 ETF는?')">의미 검색</a> ·
 <a onclick="ask('kimi 관련 투자 상품 있어?')">함정(거절이 정답)</a></div>
<div id="out"></div>
<script>
const f=document.getElementById('f'),q=document.getElementById('q'),
      b=document.getElementById('b'),out=document.getElementById('out');
function ask(t){q.value=t;f.requestSubmit();}
f.addEventListener('submit',async e=>{
  e.preventDefault(); if(!q.value.trim())return;
  b.disabled=true;b.textContent='답변 중…';const t0=performance.now();
  try{
    const r=await fetch('/answer?question_id=test&question='+encodeURIComponent(q.value));
    const d=await r.json();const ms=((performance.now()-t0)/1000).toFixed(1);
    out.innerHTML=`<div class="card"><div class="label">답변 <span class="time">(${ms}초)</span></div>
      <pre>${esc(d.answer)}</pre>
      <details><summary>근거 보기 (retrieved_context)</summary><pre>${esc(d.retrieved_context)}</pre></details>
      <details><summary>처리 과정 보기 (think_trace)</summary><pre>${esc(d.think_trace)}</pre></details></div>`+out.innerHTML;
  }catch(err){out.innerHTML=`<div class="card"><pre>요청 실패: ${esc(String(err))}</pre></div>`+out.innerHTML;}
  b.disabled=false;b.textContent='질문하기';
});
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="금융상품 질의응답 API 서버")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--light", action="store_true",
                    help="가볍게 시작: 그래프·벡터·HCX 끄고 규칙 엔진만(개발용)")
    ap.add_argument("--kg", default=None,
                    help="그래프 적재 범위: all(기본)/none/슬러그 목록(kr_etf,constituents)")
    args = ap.parse_args(argv)

    kg = args.kg if args.kg is not None else ("none" if args.light else "all")
    t0 = time.perf_counter()
    print(f"[서버] 저장소 적재 중... (그래프: {kg})")
    ctx, llm_router, generator = build_runtime(
        kg_tables=kg, with_vector=not args.light,
        with_llm=not args.light, with_generator=not args.light)
    print(f"[서버] 적재 완료 {time.perf_counter() - t0:.1f}초 — "
          f"이름 사전 {ctx.index.entries:,}건 · 그래프 "
          f"{getattr(ctx.kg_store, 'triples', 0):,}트리플 · "
          f"HCX {'켜짐' if llm_router else '꺼짐(키 없음/--light)'}")
    app = create_app(ctx, llm_router, generator)

    import uvicorn
    print(f"[서버] http://localhost:{args.port}/ 에서 질문 시험대를 열 수 있습니다")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
