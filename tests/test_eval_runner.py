# -*- coding: utf-8 -*-
"""구현 순서 ⑦ 테스트 — 자동 채점기 자체 검증.

채점기가 틀리면 개발 방향이 오염되므로, 채점 규칙(태도·근거·내용 검사)을 가짜 답변으로 먼저
잠그고, 실제 시스템의 함정 16문항(함정 15 + M-29)이 채점기에서 '거절 정답'으로 읽히는지 확인한다.
"""
import datetime as dt
import io
import json
import os

import pytest

from evalset.eval_runner import (REFUSE_HEAD, compare_with_baseline, evaluate, evaluate_one,
                                 has_limit_note, is_refusal, load_checks, load_jsonl,
                                 looks_like_listing, number_matches, render_report, run_check,
                                 score_behavior, score_evidence, summarize, write_outputs)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "storage", "output", "products.duckdb")
needs_db = pytest.mark.skipif(not os.path.exists(DB_PATH), reason="products.duckdb 미생성")

REFUSAL = (REFUSE_HEAD + "\n- 사유: 'kimi'로 식별되는 상품·종목이 기준일 데이터에 없음\n"
           "(데이터 기준일: 마스터 2026-07-11 · 구성종목 2026-08-21)")
LISTING = ("[etp_top_aum] 결과 5건\n  1. KODEX 200 (pd_net_tamt=1)\n  2. TIGER 200 (pd_net_tamt=2)\n"
           "(데이터 기준일: 마스터 2026-07-11 · 구성종목 2026-08-21)")
SNEAKY = REFUSE_HEAD + "\n  1. KODEX AI로봇 ETF — 위험등급 3\n  2. TIGER AI로봇 — 위험등급 2"   # 거절문 뒤에 목록
CTX_OK = "[근거1 | 출처: PREF01N001 | 키: KR7069500007 | 채널: sql | 기준일: 2026-07-11] pd_abrv_nm=KODEX 200"


def _out(answer, ctx=CTX_OK, trace="stage=rule"):
    return {"question_id": "x", "question": "q", "retrieved_context": ctx, "think_trace": trace, "answer": answer}


# ---------------------------------------------------------------------------
# 1. 태도 판정 규칙 — 가짜 답변으로 잠금
# ---------------------------------------------------------------------------

def test_refusal_and_listing_detection():
    assert is_refusal(REFUSAL) and not looks_like_listing(REFUSAL)     # 사유 줄(- 사유:)은 목록이 아니다
    assert not is_refusal(LISTING) and looks_like_listing(LISTING)
    assert is_refusal(SNEAKY) and looks_like_listing(SNEAKY)


def test_score_behavior_matrix():
    assert score_behavior("refuse", _out(REFUSAL)) == (True, "ok")
    assert score_behavior("refuse", _out(LISTING)) == (False, "trap_error")      # 함정에 목록 = 함정 오답
    assert score_behavior("refuse", _out(SNEAKY)) == (False, "trap_error")       # 거절문 뒤 목록도 함정 오답
    assert score_behavior("answer", _out(LISTING)) == (True, "ok")
    assert score_behavior("answer", _out(REFUSAL)) == (False, "over_refuse")     # 정상 문항 거절 = 과잉 거절
    assert score_behavior("partial", _out(LISTING)) == (False, "missing_limit")  # 한계 문구 없음
    assert score_behavior("partial", _out(LISTING + "\n※ 보수 값 보유 상품은 일부입니다")) == (True, "ok")
    assert score_behavior("partial", _out(REFUSAL)) == (True, "ok")              # 확인 불가 명시도 한계 명시로 인정
    assert has_limit_note("전체의 12.5%만 값 보유", ["값 보유"]) and not has_limit_note("정상 답변", ["커버리지"])


def test_score_evidence():
    assert score_evidence(_out(LISTING))[0]
    assert not score_evidence(_out(LISTING, ctx="(근거 없음)"))[0]
    assert not score_evidence(_out("답변만 있고 날짜 표기가 빠짐"))[0]


def test_number_matches_formats():
    assert number_matches(11138, "공모펀드는 총 11,138개입니다")
    assert number_matches(11138, "총 11138 개")
    assert number_matches(33.03, "삼성전자 33.03%")
    assert number_matches(33.03, "삼성전자 33.030 %")
    assert not number_matches(11138, "총 11,139개")


# ---------------------------------------------------------------------------
# 2. 검사표 실행 — 작은 임시 DB로
# ---------------------------------------------------------------------------

@pytest.fixture
def mini_con():
    import duckdb
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t(name VARCHAR, aum DOUBLE)")
    con.execute("INSERT INTO t VALUES ('KODEX 200', 3), ('TIGER 200', 2), ('ACE 200', 1)")
    return con


def test_run_check_types(mini_con):
    out = _out("1. KODEX 200\n2. TIGER 200\n총 3건. 커버리지 일부.")
    ok = lambda c: run_check(c, out, mini_con)["ok"]
    assert ok({"type": "answer_has_any", "terms": ["kodex200", "없는말"]})          # 공백·대소문자 무시
    assert not ok({"type": "answer_has_all", "terms": ["KODEX 200", "없는말"]})
    assert ok({"type": "answer_has_none", "terms": ["Denmark"]})
    assert ok({"type": "answer_regex", "pattern": r"총\s*3\s*건"}) and not ok({"type": "answer_regex", "pattern": r"총\s*4\s*건"})
    assert ok({"type": "sql_names", "sql": "SELECT name FROM t ORDER BY aum DESC", "min_hit": 2, "top": 2, "ordered": True})
    assert not ok({"type": "sql_names", "sql": "SELECT name FROM t ORDER BY aum DESC", "min_hit": 3})
    assert ok({"type": "sql_number", "sql": "SELECT COUNT(*) FROM t"})
    assert ok({"type": "any_of", "checks": [{"type": "answer_has_any", "terms": ["zzz"]}, {"type": "note_any", "terms": ["커버리지"]}]})
    assert ok({"type": "evidence_source_any", "sources": ["PREF01N001"]})
    assert ok({"type": "evidence_min", "n": 1})
    bad = run_check({"type": "없는종류"}, out, mini_con)
    assert not bad["ok"] and "알 수 없는" in bad["detail"]                          # 설정 오류는 실패로 드러남
    err = run_check({"type": "sql_number", "sql": "SELECT * FROM 없는테이블"}, out, mini_con)
    assert not err["ok"] and "검사 오류" in err["detail"]


def test_evaluate_one_and_summary_and_report(tmp_path):
    rows = [{"id": "T-99", "level": "트랩", "category": "c", "question": "가짜 함정", "behavior": "refuse"},
            {"id": "L-99", "level": "하", "category": "c", "question": "가짜 정상", "behavior": "answer"}]
    r1 = evaluate_one(rows[0], _out(LISTING), 0.5, [], None)          # 함정에 목록 → 함정 오답
    r2 = evaluate_one(rows[1], _out(LISTING), 20.0, [{"type": "answer_has_any", "terms": ["KODEX 200"]}], None)
    assert r1["behavior_kind"] == "trap_error" and not r1["overall_ok"] and r1["content_ok"] is None
    assert r2["overall_ok"] and r2["content_ok"] is True
    s = summarize([r1, r2])
    assert s["trap_errors"] == ["T-99"] and s["overall_ok"] == 1 and s["time"]["over_target"] == 1
    md = render_report([r1, r2], s, "test", dt.datetime(2026, 8, 18, 12, 0))
    assert "함정 오답 1/1" in md and "T-99" in md and "종합 미통과" in md
    md_path, jsonl_path = write_outputs([r1, r2], s, "test", dt.datetime(2026, 8, 18, 12, 0), str(tmp_path), tag="unit")
    assert os.path.exists(md_path) and len(load_jsonl(jsonl_path)) == 2
    cmp = compare_with_baseline([r1, r2], [{"id": "T-99", "overall_ok": True}, {"id": "L-99", "overall_ok": False}])
    assert cmp["newly_fail"] == ["T-99"] and cmp["newly_pass"] == ["L-99"]


def test_checks_file_covers_all_questions_and_sql_runs():
    """검사표는 105문항 전부를 덮고, 모든 SQL 은 실행 가능해야 한다(설정 오류 조기 발견)."""
    evalset = load_jsonl(os.path.join(ROOT, "evalset", "evalset_v1.jsonl"))
    checks = load_checks(os.path.join(ROOT, "evalset", "checks_v1.jsonl"))
    missing = [r["id"] for r in evalset if r["id"] not in checks]
    assert not missing, missing
    if not os.path.exists(DB_PATH):
        pytest.skip("products.duckdb 미생성")
    import duckdb
    con = duckdb.connect(DB_PATH, read_only=True)
    def walk(cs):
        for c in cs:
            if c["type"] == "any_of":
                yield from walk(c["checks"])
            else:
                yield c
    for qid, cs in checks.items():
        for c in walk(cs):
            if "sql" in c:
                con.execute(c["sql"]).fetchall()          # 예외 없이 실행되면 통과
    con.close()


# ---------------------------------------------------------------------------
# 3. 실제 시스템으로 채점기 보정 — 거절이 정답인 16문항은 채점기에서 전부 '거절 정답'이어야 한다
# ---------------------------------------------------------------------------

@needs_db
def test_scorer_reads_real_refusals_as_correct():
    from evalset.eval_runner import InProcBackend
    backend = InProcBackend(kg_tables="none", with_vector=False, with_hcx=False)   # 규칙 엔진만(크레딧 0)
    rows = [r for r in load_jsonl(os.path.join(ROOT, "evalset", "evalset_v1.jsonl")) if r["behavior"] == "refuse"]
    checks = load_checks(os.path.join(ROOT, "evalset", "checks_v1.jsonl"))
    results = evaluate(rows, backend, checks)
    assert len(results) == 15                          # T-14는 8/28 r2 에서 정상 질의로 전환
    bad = [(r["id"], r["behavior_kind"], r["answer_head"]) for r in results if not r["behavior_ok"]]
    assert not bad, bad
    assert all(r["evidence_ok"] for r in results), [r["id"] for r in results if not r["evidence_ok"]]
    # 정상 문항 표본이 과잉 거절로 읽히지 않는지도 확인
    normal = [r for r in load_jsonl(os.path.join(ROOT, "evalset", "evalset_v1.jsonl")) if r["id"] in ("L-11", "L-21")]
    res2 = evaluate(normal, backend, checks)
    assert all(r["behavior_ok"] and r["overall_ok"] for r in res2), [(r["id"], r["behavior_kind"], r["checks"]) for r in res2]
