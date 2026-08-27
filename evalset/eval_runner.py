# -*- coding: utf-8 -*-
"""
자동 채점기 (구현 순서 ⑦) — 모의고사 105문항을 실행·채점해 성적표를 만든다.

무엇: evalset_v1.jsonl 의 문항을 하나씩 우리 시스템에 넣고, 응답(5필드)을 네 축으로 채점한다.
  ① 답변 태도(behavior) — 거절이 정답인 문항에 목록을 내면 "함정 오답"(최우선 집계),
     정상 문항을 거절하면 "과잉 거절", 한계 명시가 필수인 partial 문항은 한계 문구 확인
  ② 근거 표시 — retrieved_context 에 출처·기준일 블록, 답변 끝에 기준일
  ③ 내용 대조 — 문항별 검사표(checks_v1.jsonl). 기대값은 DuckDB 에 SQL 을 실행해 그때그때 계산
     (검사표가 없는 문항은 "수동 확인"으로 표기하고 내용 점수를 매기지 않는다)
  ④ 응답 시간 — 문항별 초 단위, 15초(우리 목표)·60초(주최 권장) 초과 집계
왜  : 코드를 고칠 때마다 "좋아졌나 나빠졌나"를 숫자로 확인하기 위한 회귀 도구이자 첫 정량 성적표.
      채점기가 틀린 점수를 내는 것이 채점을 못 하는 것보다 나쁘므로, 자동 검사가 확실한 것만 검사한다.
한계: 내용 검사의 기대값을 우리 SQL 로 계산하므로 "SQL 자체가 틀린" 오류는 못 잡는다 —
      성적표에 답변 발췌를 함께 실어 사람 검토와 병행한다.

실행:
  python evalset/eval_runner.py                       # 실전 구성(그래프 전체·벡터·HCX) — 서버와 같은 부품, HTTP 없이
  python evalset/eval_runner.py --light               # 규칙 엔진만(그래프·벡터·HCX 끔, 크레딧 0원)
  python evalset/eval_runner.py --no-hcx --kg kr_etf,constituents   # 그래프·벡터는 켜고 HCX 만 끔
  python evalset/eval_runner.py --mode http --base-url http://127.0.0.1:8000   # 켜져 있는 서버에 HTTP 로
  python evalset/eval_runner.py --ids T-01,T-04 --level 트랩 --limit 20 --baseline evalset/reports/이전.jsonl
결과: evalset/reports/eval_<시각>_<구성>.md (사람용 성적표) + 같은 이름 .jsonl (문항별 상세, 회귀 비교용)
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))              # evalset/
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EVALSET_DEFAULT = os.path.join(HERE, "evalset_v1.jsonl")
CHECKS_DEFAULT = os.path.join(HERE, "checks_v1.jsonl")
REPORT_DIR_DEFAULT = os.path.join(HERE, "reports")

REFUSE_HEAD = "요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"
# partial 문항의 "한계 명시" 기본 문구 — 문항별 note_any 검사가 있으면 그것을 우선한다
LIMIT_PHRASES = ("일부", "커버리지", "한계", "확인할 수 없", "확인 불가", "미수집", "보유하지 않",
                 "값 보유", "결측", "제공 범위", "확인이 필요", "밖", "없음", "없습니다", "주의")
TARGET_SEC = 15.0        # 우리 목표
RECOMMENDED_SEC = 60.0   # 주최 권장(과제설명 PDF p.11)


# ---------------------------------------------------------------------------
# 1. 순수 함수 — 판정 규칙 (테스트가 직접 import 한다)
# ---------------------------------------------------------------------------

def norm(text):
    """비교용 정규화 — 공백 제거·소문자. 상품명 띄어쓰기·대소문자 차이를 무시하기 위함."""
    return re.sub(r"\s+", "", str(text or "")).lower()


def is_refusal(answer):
    head = (answer or "").strip()[:120]
    return head.startswith(REFUSE_HEAD) or "확인할 수 없" in head


_NUMBERED_LINE = re.compile(r"^\s*\d{1,3}[.)]\s*\S", re.M)


def looks_like_listing(answer):
    """상품 목록을 낸 답변인가 — '결과 N건' 또는 번호 매긴 줄 2개 이상."""
    text = answer or ""
    if re.search(r"결과\s*\d+\s*건", text):
        return True
    return len(_NUMBERED_LINE.findall(text)) >= 2


def has_limit_note(answer, phrases=None):
    text = answer or ""
    return any(p in text for p in (phrases or LIMIT_PHRASES))


def observed_behavior(out):
    """관측 태도 — refuse(거절문) / partial(한계 문구 있는 답변) / answer."""
    ans = out.get("answer", "")
    if is_refusal(ans):
        return "refuse"
    return "partial" if has_limit_note(ans) else "answer"


def score_behavior(expected, out, note_phrases=None):
    """태도 채점 → (ok, kind). kind: ok / trap_error / over_refuse / missing_limit."""
    ans = out.get("answer", "")
    refused = is_refusal(ans)
    if expected == "refuse":
        if refused and not looks_like_listing(ans):
            return True, "ok"
        return False, "trap_error"                    # 거절이 정답인데 답(목록)을 냄 — 최우선 오류
    if expected == "answer":
        return (True, "ok") if not refused else (False, "over_refuse")
    if expected == "partial":
        if refused:
            # 확인 불가를 명시한 거절문도 '한계 명시'로는 인정한다(내용 검사가 나머지를 본다)
            return True, "ok"
        return (True, "ok") if has_limit_note(ans, note_phrases) else (False, "missing_limit")
    return False, f"unknown_expected:{expected}"


def score_evidence(out):
    """근거 표시 채점 — 근거 블록(출처·기준일) + 답변 안 기준일 표기."""
    ctx = out.get("retrieved_context", "") or ""
    ans = out.get("answer", "") or ""
    has_block = "[근거" in ctx and "출처:" in ctx and "기준일:" in ctx
    has_date = "기준일" in ans
    detail = []
    if not has_block:
        detail.append("근거 블록 없음")
    if not has_date:
        detail.append("답변에 기준일 없음")
    return has_block and has_date, "; ".join(detail) or "출처·기준일 표기 확인"


_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def numbers_in(text):
    out = []
    for m in _NUM_RE.findall(text or ""):
        s = m.replace(",", "").rstrip(".")
        try:
            out.append(float(s))
        except ValueError:
            continue
    return out


def number_matches(value, text):
    """기대 숫자가 답변 안에 있나 — 천 단위 콤마 유무·소수 둘째 자리 반올림까지 허용."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    for x in numbers_in(text):
        if abs(x - v) < 1e-9 or round(x, 2) == round(v, 2):
            return True
    return False


# ---------------------------------------------------------------------------
# 2. 검사표 실행 — 검사 종류별 판정
# ---------------------------------------------------------------------------

def _run_sql(con, sql):
    if con is None:
        raise RuntimeError("DB 연결 없음(HTTP 모드에서도 내용 검사에는 DuckDB 가 필요)")
    return con.execute(sql).fetchall()


def run_check(check, out, con):
    """검사 1건 → dict(name, ok, detail). 모르는 종류는 실패로 드러낸다(설정 오류 은폐 금지)."""
    kind = check.get("type")
    name = check.get("name") or kind
    ans = out.get("answer", "") or ""
    ctx = out.get("retrieved_context", "") or ""
    n_ans = norm(ans)
    try:
        if kind in ("answer_has_any", "note_any"):
            terms = check["terms"]
            hit = [t for t in terms if norm(t) in n_ans]
            return {"name": name, "ok": bool(hit), "detail": f"포함: {hit[:3]}" if hit else f"없음: {terms[:4]}"}
        if kind == "answer_has_all":
            miss = [t for t in check["terms"] if norm(t) not in n_ans]
            return {"name": name, "ok": not miss, "detail": f"누락: {miss}" if miss else "전부 포함"}
        if kind == "answer_has_none":
            found = [t for t in check["terms"] if norm(t) in n_ans]
            return {"name": name, "ok": not found, "detail": f"금지어 포함: {found}" if found else "금지어 없음"}
        if kind == "answer_regex":                       # 원문(공백 유지)에 정규식 — 표현 변형이 많은 값 확인용
            m = re.search(check["pattern"], ans, re.I | re.S)
            return {"name": name, "ok": bool(m), "detail": f"일치: {m.group(0)[:40]!r}" if m else f"불일치: {check['pattern']}"}
        if kind == "sql_names":
            rows = _run_sql(con, check["sql"])
            top = check.get("top")
            rows = rows[:top] if top else rows
            names = [[str(c) for c in r if c not in (None, "")] for r in rows]
            hits = [r[0] for r in names if any(norm(c) in n_ans for c in r)]
            min_hit = check.get("min_hit", 1)
            ok = len(hits) >= min_hit
            if ok and check.get("ordered") and len(hits) >= 2:
                pos = [min(n_ans.find(norm(c)) for c in r if norm(c) in n_ans)
                       for r in names if any(norm(c) in n_ans for c in r)]
                ok = pos == sorted(pos)
            return {"name": name, "ok": ok,
                    "detail": f"{len(hits)}/{len(names)} 일치(최소 {min_hit}): {hits[:4]}"}
        if kind == "sql_number":
            rows = _run_sql(con, check["sql"])
            value = rows[0][0] if rows and rows[0] else None
            ok = number_matches(value, ans)
            return {"name": name, "ok": ok, "detail": f"기대 {value} — {'포함' if ok else '없음'}"}
        if kind == "evidence_source_any":
            hit = [s for s in check["sources"] if f"출처: {s}" in ctx]
            return {"name": name, "ok": bool(hit), "detail": f"출처 {hit}" if hit else f"출처 없음 {check['sources']}"}
        if kind == "evidence_min":
            n = ctx.count("[근거")
            return {"name": name, "ok": n >= check.get("n", 1), "detail": f"근거 {n}건"}
        if kind == "any_of":
            subs = [run_check(c, out, con) for c in check["checks"]]
            ok = any(s["ok"] for s in subs)
            return {"name": name, "ok": ok, "detail": " | ".join(f"{s['name']}:{'O' if s['ok'] else 'X'}" for s in subs)}
        return {"name": name, "ok": False, "detail": f"알 수 없는 검사 종류: {kind}"}
    except Exception as exc:                            # 검사 자체의 오류도 성적표에 드러낸다
        return {"name": name, "ok": False, "detail": f"검사 오류: {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 3. 실행 백엔드 — 서버와 같은 부품(in-process) 또는 HTTP
# ---------------------------------------------------------------------------

class InProcBackend:
    """server.app.build_runtime 과 같은 부품으로 answer_question 을 직접 호출한다."""

    def __init__(self, kg_tables="all", with_vector=True, with_hcx=True):
        from server.app import build_runtime
        (self.ctx, self.llm_router, self.generator,
         self.intent_checker, self.finalizer) = build_runtime(
            kg_tables=kg_tables, with_vector=with_vector,
            with_llm=with_hcx, with_generator=with_hcx)
        self.con = self.ctx.con
        self.label = (f"inproc kg={kg_tables} vector={'on' if self.ctx.vstore else 'off'} "
                      f"hcx={'on' if self.llm_router else 'off'}")

    def answer(self, question, question_id):
        import dataclasses
        from engine.answer_service import answer_question
        from engine.deadline import Deadline
        deadline = Deadline()
        ctx = dataclasses.replace(self.ctx, deadline=deadline)
        return answer_question(question, ctx, question_id=question_id,
                               llm_router=self.llm_router, generator=self.generator,
                               deadline=deadline,
                               intent_checker=self.intent_checker, finalizer=self.finalizer)


class HttpBackend:
    """켜져 있는 서버의 GET /answer 를 부른다(원격 리허설용). 내용 검사용 DB 는 따로 연다."""

    def __init__(self, base_url, db_path=None):
        import duckdb
        from pipeline.entity_index import DB_PATH_DEFAULT
        self.base_url = base_url.rstrip("/")
        self.con = duckdb.connect(db_path or DB_PATH_DEFAULT, read_only=True)
        self.label = f"http {self.base_url}"

    def answer(self, question, question_id):
        import httpx
        r = httpx.get(f"{self.base_url}/answer",
                      params={"question_id": question_id, "question": question}, timeout=300.0)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# 4. 실행·집계·보고서
# ---------------------------------------------------------------------------

def load_jsonl(path):
    rows = []
    with io.open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_checks(path):
    """검사표: 한 줄 = {"id": "L-01", "checks": [ {...}, ... ]}"""
    table = {}
    if path and os.path.exists(path):
        for row in load_jsonl(path):
            table[row["id"]] = row.get("checks", [])
    return table


def evaluate_one(row, out, elapsed, checks, con):
    expected = row["behavior"]
    note_terms = None
    for c in checks or []:
        if c.get("type") == "note_any":
            note_terms = c["terms"]
            break
    b_ok, b_kind = score_behavior(expected, out, note_terms)
    e_ok, e_detail = score_evidence(out)
    check_results = [run_check(c, out, con) for c in (checks or [])]
    content_ok = None if not check_results else all(c["ok"] for c in check_results)
    overall = b_ok and e_ok and (content_ok is not False)
    ans = out.get("answer", "") or ""
    trace = out.get("think_trace", "") or ""
    return {
        "id": row["id"], "level": row["level"], "category": row.get("category", ""),
        "question": row["question"], "expected": expected,
        "observed": observed_behavior(out),
        "behavior_ok": b_ok, "behavior_kind": b_kind,
        "evidence_ok": e_ok, "evidence_detail": e_detail,
        "checks": check_results, "content_ok": content_ok,
        "overall_ok": overall,
        "elapsed": round(elapsed, 2), "cached": "(캐시 응답)" in trace,
        "degraded": any(m in trace for m in ("강등", "폴백", "생략", "전역 오류")),
        "answer_head": ans.strip().splitlines()[0][:160] if ans.strip() else "",
        "answer_excerpt": ans[:600],
        "trace_excerpt": trace[:500],
    }


def evaluate(rows, backend, checks_table, progress=None):
    results = []
    for i, row in enumerate(rows, 1):
        t0 = time.perf_counter()
        try:
            out = backend.answer(row["question"], row["id"])
        except Exception as exc:                        # 실행 실패도 한 행으로 남긴다
            out = {"question_id": row["id"], "question": row["question"], "retrieved_context": "",
                   "think_trace": f"전역 오류(채점기): {type(exc).__name__}: {exc}", "answer": ""}
        elapsed = time.perf_counter() - t0
        res = evaluate_one(row, out, elapsed, checks_table.get(row["id"]), getattr(backend, "con", None))
        results.append(res)
        if progress:
            progress(i, len(rows), res)
    return results


def _pct(a, b):
    return f"{(100.0 * a / b):.0f}%" if b else "-"


def summarize(results):
    levels = []
    for lv in ("하", "중", "상", "트랩"):
        rs = [r for r in results if r["level"] == lv]
        if not rs:
            continue
        with_checks = [r for r in rs if r["content_ok"] is not None]
        levels.append({
            "level": lv, "n": len(rs),
            "behavior_ok": sum(r["behavior_ok"] for r in rs),
            "evidence_ok": sum(r["evidence_ok"] for r in rs),
            "content_ok": sum(1 for r in with_checks if r["content_ok"]),
            "content_n": len(with_checks),
            "overall_ok": sum(r["overall_ok"] for r in rs),
            "avg_sec": round(statistics.mean(r["elapsed"] for r in rs), 2),
        })
    times = [r["elapsed"] for r in results if not r["cached"]] or [0.0]
    times_sorted = sorted(times)
    p = lambda q: times_sorted[min(len(times_sorted) - 1, int(round(q * (len(times_sorted) - 1))))]
    return {
        "n": len(results),
        "overall_ok": sum(r["overall_ok"] for r in results),
        "behavior_ok": sum(r["behavior_ok"] for r in results),
        "evidence_ok": sum(r["evidence_ok"] for r in results),
        "content_ok": sum(1 for r in results if r["content_ok"]),
        "content_n": sum(1 for r in results if r["content_ok"] is not None),
        "trap_errors": [r["id"] for r in results if r["behavior_kind"] == "trap_error"],
        "over_refuse": [r["id"] for r in results if r["behavior_kind"] == "over_refuse"],
        "missing_limit": [r["id"] for r in results if r["behavior_kind"] == "missing_limit"],
        "refuse_expected_n": sum(1 for r in results if r["expected"] == "refuse"),
        "levels": levels,
        "time": {"p50": round(p(0.5), 2), "p95": round(p(0.95), 2), "max": round(max(times), 2),
                 "mean": round(statistics.mean(times), 2),
                 "over_target": sum(1 for t in times if t > TARGET_SEC),
                 "over_recommended": sum(1 for t in times if t > RECOMMENDED_SEC),
                 "cached_n": sum(1 for r in results if r["cached"]),
                 "degraded_n": sum(1 for r in results if r["degraded"])},
        "no_checks": [r["id"] for r in results if r["content_ok"] is None],
    }


def compare_with_baseline(results, baseline_rows):
    """이전 실행(jsonl)과 종합 통과 여부를 비교 — 회귀 확인용."""
    prev = {r["id"]: r for r in baseline_rows}
    newly_pass = [r["id"] for r in results if r["overall_ok"] and r["id"] in prev and not prev[r["id"]]["overall_ok"]]
    newly_fail = [r["id"] for r in results if not r["overall_ok"] and r["id"] in prev and prev[r["id"]]["overall_ok"]]
    return {"newly_pass": newly_pass, "newly_fail": newly_fail, "compared": sum(1 for r in results if r["id"] in prev)}


def render_report(results, summary, label, started_at, baseline_cmp=None):
    s = summary
    t = s["time"]
    L = []
    L.append(f"# 자동 채점 성적표 — {started_at:%Y-%m-%d %H:%M} ({label})")
    L.append("")
    L.append("> 채점 4축: ① 답변 태도(거절이 정답인 문항에 목록을 내면 '함정 오답') ② 근거 표시 ③ 내용 검사(문항별 검사표, "
             "기대값은 DuckDB SQL 로 계산) ④ 응답 시간. **종합 통과 = 태도 O + 근거 O + 내용 검사(있으면) O.**")
    L.append("> 내용 검사가 없는 문항은 '수동 확인'으로 남긴다. 기대값을 우리 SQL 로 계산하므로 SQL 자체의 오류는 잡지 못한다 — 답변 발췌를 함께 실었다.")
    L.append("")
    L.append("## 0. 한 줄 요약")
    L.append("")
    L.append(f"- 전체 **{s['n']}문항 중 종합 통과 {s['overall_ok']} ({_pct(s['overall_ok'], s['n'])})** · "
             f"태도 일치 {s['behavior_ok']}/{s['n']} · 근거 표시 {s['evidence_ok']}/{s['n']} · "
             f"내용 검사 통과 {s['content_ok']}/{s['content_n']}(검사 있는 문항 기준)")
    L.append(f"- **함정 오답 {len(s['trap_errors'])}/{s['refuse_expected_n']}**"
             + (f" — {', '.join(s['trap_errors'])}" if s["trap_errors"] else " — 없음")
             + f" · 과잉 거절 {len(s['over_refuse'])}" + (f" — {', '.join(s['over_refuse'])}" if s["over_refuse"] else "")
             + f" · 한계 미명시(partial) {len(s['missing_limit'])}" + (f" — {', '.join(s['missing_limit'])}" if s["missing_limit"] else ""))
    L.append(f"- 응답 시간(캐시 응답 제외): 중앙값 {t['p50']}초 · 95% 지점 {t['p95']}초 · 최대 {t['max']}초 · 평균 {t['mean']}초 — "
             f"**15초 초과 {t['over_target']}건 · 60초 초과 {t['over_recommended']}건** (캐시 응답 {t['cached_n']}건, 강등·폴백 {t['degraded_n']}건)")
    if baseline_cmp:
        L.append(f"- 이전 실행 대비({baseline_cmp['compared']}문항 비교): 새로 통과 {len(baseline_cmp['newly_pass'])}"
                 + (f" ({', '.join(baseline_cmp['newly_pass'])})" if baseline_cmp["newly_pass"] else "")
                 + f" · **새로 실패 {len(baseline_cmp['newly_fail'])}**"
                 + (f" ({', '.join(baseline_cmp['newly_fail'])})" if baseline_cmp["newly_fail"] else ""))
    L.append("")
    L.append("## 1. 난이도별")
    L.append("")
    L.append("| 난이도 | 문항 | 태도 일치 | 근거 표시 | 내용 검사 통과/대상 | **종합 통과** | 평균 시간 |")
    L.append("|---|---|---|---|---|---|---|")
    for lv in s["levels"]:
        L.append(f"| {lv['level']} | {lv['n']} | {lv['behavior_ok']} | {lv['evidence_ok']} | "
                 f"{lv['content_ok']}/{lv['content_n']} | **{lv['overall_ok']} ({_pct(lv['overall_ok'], lv['n'])})** | {lv['avg_sec']}초 |")
    L.append("")
    L.append("## 2. 거절이 정답인 문항 (함정 15 + M-29)")
    L.append("")
    L.append("| 문항 | 관측 태도 | 판정 | 내용 검사 | 시간 | 답변 첫 줄 |")
    L.append("|---|---|---|---|---|---|")
    for r in results:
        if r["expected"] != "refuse":
            continue
        verdict = "✅ 거절" if r["behavior_ok"] else "❌ **함정 오답**"
        c = "-" if r["content_ok"] is None else ("O" if r["content_ok"] else "X")
        L.append(f"| {r['id']} | {r['observed']} | {verdict} | {c} | {r['elapsed']}초 | {r['answer_head'][:70].replace('|', '/')} |")
    L.append("")
    fails = [r for r in results if not r["overall_ok"]]
    L.append(f"## 3. 종합 미통과 문항 상세 ({len(fails)}건)")
    L.append("")
    if not fails:
        L.append("없음.")
    for r in fails:
        why = []
        if not r["behavior_ok"]:
            why.append(f"태도({r['behavior_kind']}: 기대 {r['expected']} / 관측 {r['observed']})")
        if not r["evidence_ok"]:
            why.append(f"근거({r['evidence_detail']})")
        bad = [c for c in r["checks"] if not c["ok"]]
        if bad:
            why.append("내용(" + "; ".join(f"{c['name']}: {c['detail']}" for c in bad) + ")")
        L.append(f"### {r['id']} [{r['level']}] {r['question']}")
        L.append("")
        L.append(f"- 실패 축: {' · '.join(why)}")
        L.append(f"- 시간 {r['elapsed']}초" + (" (강등·폴백 있음)" if r["degraded"] else ""))
        excerpt = r["answer_excerpt"].replace("\n", " ⏎ ")[:400]
        L.append(f"- 답변 발췌: {excerpt}")
        L.append("")
    L.append("## 4. 응답 시간")
    L.append("")
    slow = sorted((r for r in results if not r["cached"]), key=lambda r: -r["elapsed"])[:8]
    L.append("| 순위 | 문항 | 시간 | 강등 | 질문 |")
    L.append("|---|---|---|---|---|")
    for i, r in enumerate(slow, 1):
        L.append(f"| {i} | {r['id']} | {r['elapsed']}초 | {'예' if r['degraded'] else ''} | {r['question'][:50]} |")
    L.append("")
    L.append("## 5. 채점 범위 안내")
    L.append("")
    if s["no_checks"]:
        L.append(f"- 내용 검사표가 없어 태도·근거·시간만 채점한 문항(수동 확인 필요) {len(s['no_checks'])}건: {', '.join(s['no_checks'])}")
    else:
        L.append("- 105문항 전부에 내용 검사표가 있다(검사의 엄격도는 문항별로 다름 — checks_v1.jsonl 참조).")
    L.append("- 태도 판정 규칙: 거절 = 답변이 정해진 거절문으로 시작하거나 첫머리에 '확인할 수 없'이 있음 · 목록 = '결과 N건' 또는 번호 매긴 줄 2개 이상.")
    L.append("- 문항별 상세(검사 결과·처리 과정 발췌)는 같은 이름의 .jsonl 파일에 있다.")
    return "\n".join(L) + "\n"


def write_outputs(results, summary, label, started_at, out_dir, baseline_cmp=None, tag=""):
    os.makedirs(out_dir, exist_ok=True)
    stem = f"eval_{started_at:%Y%m%d_%H%M}_{tag or 'run'}"
    md_path = os.path.join(out_dir, stem + ".md")
    jsonl_path = os.path.join(out_dir, stem + ".jsonl")
    with io.open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_report(results, summary, label, started_at, baseline_cmp))
    with io.open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return md_path, jsonl_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="자동 채점기 — 모의고사 105문항 실행·채점")
    ap.add_argument("--mode", choices=("inproc", "http"), default="inproc")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--light", action="store_true", help="그래프·벡터·HCX 끄고 규칙 엔진만(크레딧 0)")
    ap.add_argument("--no-hcx", action="store_true", help="그래프·벡터는 켜고 HCX(계획·생성)만 끔")
    ap.add_argument("--kg", default=None, help="그래프 적재 범위: all(기본)/none/kr_etf,constituents")
    ap.add_argument("--evalset", default=EVALSET_DEFAULT)
    ap.add_argument("--checks", default=CHECKS_DEFAULT)
    ap.add_argument("--ids", default=None, help="쉼표 구분 문항 ID (예: T-01,T-04)")
    ap.add_argument("--level", default=None, help="하/중/상/트랩 중 하나")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--baseline", default=None, help="이전 실행 .jsonl — 회귀 비교")
    ap.add_argument("--out", default=REPORT_DIR_DEFAULT)
    ap.add_argument("--tag", default=None, help="보고서 파일명 꼬리표(기본: 구성 자동)")
    args = ap.parse_args(argv)

    rows = load_jsonl(args.evalset)
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        rows = [r for r in rows if r["id"] in want]
    if args.level:
        rows = [r for r in rows if r["level"] == args.level]
    if args.limit:
        rows = rows[:args.limit]
    checks_table = load_checks(args.checks)

    t0 = time.perf_counter()
    if args.mode == "http":
        backend = HttpBackend(args.base_url)
        tag = args.tag or "http"
    else:
        kg = args.kg if args.kg is not None else ("none" if args.light else "all")
        backend = InProcBackend(kg_tables=kg, with_vector=not args.light,
                                with_hcx=not (args.light or args.no_hcx))
        tag = args.tag or ("light" if args.light else ("nohcx" if args.no_hcx else "full"))
    print(f"[채점기] 준비 완료 {time.perf_counter() - t0:.1f}초 — {backend.label} · 문항 {len(rows)}개 · "
          f"검사표 {sum(1 for r in rows if r['id'] in checks_table)}개 문항")

    started_at = dt.datetime.now()

    def progress(i, n, res):
        mark = "OK " if res["overall_ok"] else ("!!TRAP" if res["behavior_kind"] == "trap_error" else "FAIL")
        print(f"  [{i:>3}/{n}] {res['id']:<5} {mark:<6} {res['elapsed']:>6.2f}s  {res['question'][:44]}")

    results = evaluate(rows, backend, checks_table, progress=progress)
    summary = summarize(results)
    baseline_cmp = compare_with_baseline(results, load_jsonl(args.baseline)) if args.baseline else None
    md_path, jsonl_path = write_outputs(results, summary, backend.label, started_at, args.out, baseline_cmp, tag)
    print(f"[채점기] 종합 통과 {summary['overall_ok']}/{summary['n']} · 함정 오답 {len(summary['trap_errors'])} · "
          f"과잉 거절 {len(summary['over_refuse'])} · p50 {summary['time']['p50']}s p95 {summary['time']['p95']}s")
    print(f"[채점기] 성적표: {md_path}\n[채점기] 상세: {jsonl_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
