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
    intent_checker, finalizer = None, None
    if with_llm and has_key:
        from agent.clova_client import ClovaChatClient
        from engine.router_llm import make_llm_router
        llm_router = make_llm_router(ClovaChatClient(model="HCX-005", timeout=6.0))
    if with_generator and has_key:
        from agent.clova_client import ClovaChatClient
        from engine.generator import (make_hcx_finalizer, make_hcx_generator,
                                      make_hcx_intent_checker)
        generator = make_hcx_generator(ClovaChatClient(model="HCX-005", timeout=8.0))
        # 8/26 공지 준수 — 의도 분석(모든 질의)·답변 최종 출력(비생성 경로)의 HCX 필수 구간.
        intent_checker = make_hcx_intent_checker(ClovaChatClient(model="HCX-005", timeout=4.0))
        finalizer = make_hcx_finalizer(ClovaChatClient(model="HCX-005", timeout=8.0))
    return ctx, llm_router, generator, intent_checker, finalizer


# ---------------------------------------------------------------------------
# 앱 구성
# ---------------------------------------------------------------------------

def create_app(ctx, llm_router=None, generator=None, cache_path=CACHE_PATH_DEFAULT,
               intent_checker=None, finalizer=None):
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
                                  deadline=deadline,
                                  intent_checker=intent_checker, finalizer=finalizer)
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
<title>금융상품 질의응답 에이전트 — 질문 시험대</title>
<style>
 :root{--orange:#f47920;--ink:#1c1e21;--muted:#6b7280;--line:#e5e7eb;--bg:#f5f6f8;--card:#fff}
 *{box-sizing:border-box}
 body{font-family:'Pretendard','Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:0;background:var(--bg);color:var(--ink)}
 header{background:linear-gradient(135deg,#1f2937 0%,#0f172a 100%);color:#fff;padding:2.2rem 1rem 1.8rem}
 .wrap{max-width:900px;margin:0 auto;padding:0 1rem}
 header h1{margin:0;font-size:1.55rem;font-weight:800;letter-spacing:-.01em}
 header p{margin:.5rem 0 0;color:#cbd5e1;font-size:.92rem;line-height:1.55}
 header .tags{margin-top:.9rem;display:flex;flex-wrap:wrap;gap:.4rem}
 .tag{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:.2rem .65rem;font-size:.78rem;color:#e5e7eb}
 main{padding:1.4rem 0 3rem}
 form{display:flex;gap:.5rem;margin:0 0 .7rem}
 input{flex:1;padding:.85rem 1rem;font-size:1rem;border:1px solid #cfd4dc;border-radius:10px;background:#fff}
 input:focus{outline:2px solid var(--orange);border-color:transparent}
 button{padding:.85rem 1.3rem;font-size:1rem;font-weight:700;border:0;border-radius:10px;background:var(--orange);color:#fff;cursor:pointer;white-space:nowrap}
 button:disabled{background:#c7cbd1;cursor:default}
 .ex{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;font-size:.85rem;color:var(--muted);margin-bottom:1.2rem}
 .ex a{color:#1d4ed8;background:#eef2ff;border:1px solid #dbe3ff;border-radius:999px;padding:.22rem .7rem;text-decoration:none;cursor:pointer}
 .ex a:hover{background:#e0e7ff}
 .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:1.1rem 1.25rem;margin:.9rem 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}
 .head{display:flex;justify-content:space-between;align-items:baseline;gap:.6rem;flex-wrap:wrap;margin-bottom:.55rem}
 .q{font-weight:700;font-size:1.02rem}
 .meta{color:var(--muted);font-size:.8rem}
 .badge{display:inline-block;border-radius:999px;padding:.12rem .55rem;font-size:.75rem;font-weight:700;margin-left:.4rem;vertical-align:middle}
 .ok{background:#dcfce7;color:#166534} .no{background:#fee2e2;color:#991b1b}
 .answer{white-space:pre-wrap;word-break:break-word;margin:0;font-size:.95rem;line-height:1.65;font-family:inherit}
 .answer .note{color:var(--muted)}
 details{margin-top:.75rem;border-top:1px dashed var(--line);padding-top:.5rem}
 summary{cursor:pointer;color:#475569;font-size:.84rem;font-weight:600}
 details pre{white-space:pre-wrap;word-break:break-all;margin:.5rem 0 0;font-size:.8rem;line-height:1.5;color:#334155;background:#f8fafc;border-radius:8px;padding:.7rem}
 .empty{color:var(--muted);font-size:.9rem;text-align:center;padding:2rem 0}
 footer{color:var(--muted);font-size:.78rem;text-align:center;padding:1rem;border-top:1px solid var(--line)}
 footer code{background:#eef0f3;padding:.1rem .35rem;border-radius:4px}
 @media (max-width:600px){form{flex-direction:column} button{width:100%}}
</style></head><body>
<header><div class="wrap">
 <h1>금융상품 질의응답 에이전트</h1>
 <p>국내 ETF·ETN·채권·공모펀드와 해외 ETF 데이터를 근거로 질문에 답합니다. 확보한 데이터에 없는 내용은 추측하지 않고 "확인할 수 없음"으로 답합니다.</p>
 <div class="tags"><span class="tag">데이터 기준일 국내 2026-08-22 · 해외 2026-08-23</span><span class="tag">답변마다 근거·처리 과정 표시</span><span class="tag">평가 규격 GET /answer 그대로 호출</span></div>
</div></header>
<main><div class="wrap">
<form id="f"><input id="q" placeholder="예: 순자산총액 기준으로 국내 ETF 상위 5개 알려줘" autofocus autocomplete="off">
<button id="b">질문하기</button></form>
<div class="ex">예시 질문:
 <a onclick="ask('순자산총액 기준으로 국내 ETF 상위 5개 알려줘')">순자산 상위 ETF</a>
 <a onclick="ask('현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘')">채권 조건 조회</a>
 <a onclick="ask('삼성전자가 포함된 ETF 알려줘')">종목 편입 ETF</a>
 <a onclick="ask('KODEX 200 총보수와 위험등급 알려줘')">상품 상세</a>
 <a onclick="ask('반도체 산업에 집중 투자하는 해외 ETF는?')">해외 ETF 검색</a>
 <a onclick="ask('내일 코스피 오를까?')">답변 불가 예시</a></div>
<div id="out"><div class="empty">질문을 입력하거나 위 예시를 눌러 보세요.</div></div>
</div></main>
<footer>평가용 API: <code>GET /answer?question_id=…&amp;question=…</code> → JSON 5개 항목(question_id · question · retrieved_context · think_trace · answer) · 상태 확인 <code>/health</code></footer>
<script>
const f=document.getElementById('f'),q=document.getElementById('q'),
      b=document.getElementById('b'),out=document.getElementById('out');
let first=true;
function ask(t){q.value=t;f.requestSubmit();}
f.addEventListener('submit',async e=>{
  e.preventDefault(); const text=q.value.trim(); if(!text)return;
  b.disabled=true;b.textContent='답변 생성 중…';const t0=performance.now();
  try{
    const r=await fetch('/answer?question_id=web&question='+encodeURIComponent(text));
    const d=await r.json();const sec=((performance.now()-t0)/1000).toFixed(1);
    const refused=/확인할 수 없습니다|제공 범위 밖|답변 드리기 어렵|확인할 수 없음/.test(d.answer||'');
    const badge=refused?'<span class="badge no">답변 불가</span>':'<span class="badge ok">답변</span>';
    const html=`<div class="card"><div class="head"><div class="q">${esc(text)}${badge}</div><div class="meta">${sec}초 · ${new Date().toLocaleTimeString('ko-KR')}</div></div>
      <pre class="answer">${fmt(d.answer)}</pre>
      <details><summary>근거 자료 보기 (retrieved_context)</summary><pre>${esc(d.retrieved_context)}</pre></details>
      <details><summary>처리 과정 보기 (think_trace)</summary><pre>${esc(d.think_trace)}</pre></details></div>`;
    if(first){out.innerHTML='';first=false;}
    out.insertAdjacentHTML('afterbegin',html);
  }catch(err){if(first){out.innerHTML='';first=false;}
    out.insertAdjacentHTML('afterbegin',`<div class="card"><pre class="answer">요청 실패: ${esc(String(err))}</pre></div>`);}
  b.disabled=false;b.textContent='질문하기';
});
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function fmt(s){return esc(s).split('\\n').map(l=>/^[※(]/.test(l.trim())?`<span class="note">${l}</span>`:l).join('\\n');}
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
    ctx, llm_router, generator, intent_checker, finalizer = build_runtime(
        kg_tables=kg, with_vector=not args.light,
        with_llm=not args.light, with_generator=not args.light)
    print(f"[서버] 적재 완료 {time.perf_counter() - t0:.1f}초 — "
          f"이름 사전 {ctx.index.entries:,}건 · 그래프 "
          f"{getattr(ctx.kg_store, 'triples', 0):,}트리플 · "
          f"HCX {'켜짐' if llm_router else '꺼짐(키 없음/--light)'}")
    app = create_app(ctx, llm_router, generator,
                     intent_checker=intent_checker, finalizer=finalizer)

    import uvicorn
    print(f"[서버] http://localhost:{args.port}/ 에서 질문 시험대를 열 수 있습니다")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
