# -*- coding: utf-8 -*-
"""리더 세션 11바퀴(9/6) — 표현 변형 점검 7차 24문항(공식 예시 국내판·상세 항목·집계·기준일·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '국내 상장 ETF 중 총보수 낮고 순자산 큰 3개'(주최 p.4 예시의 국내판)가 순자산만으로 답함 ·
'KOSPI200 추종 ETF들의 총보수 비교' 폴백 · '상장주식수'가 상장일로, '연초 이후 수익률'이 1년 수익률로 · 환헤지/판매회사/레버리지 배수 질의에
근거 안내 없음 · '반도체/KODEX ETF 순자산 합계' 폴백 · '운용사별 ETF 개수/점유율' 전체 건수·폴백 · '테마별 ETF 개수' 전체 건수 ·
'채권 대분류별 건수' 목록 · '기준일이 언제야' 폴백 거절 · '지난달/최근 3개월 상장' 전체 · 함정('ETF랑 펀드 중 뭐가 더 좋아'·'삼성전자 목표가'·'지금 금리').
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import parse_listed_from, parse_listed_until, route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_TEMPLATES, TEMPLATES, run_template
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)
POLICY = load_policy()
_CATALOG_SHA256_FROZEN = "a3f8e65498b70ed5264da3fcf84f5336b52cb4b6448e483b0c9bce07ebf25855"


@pytest.fixture(scope="module")
def con():
    return duckdb.connect(DB_PATH_DEFAULT, read_only=True)


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


@pytest.fixture(scope="module")
def ctx(con, index):
    return RuntimeContext(con=con, index=index)


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


def _call(plan, op):
    return next((c for c in plan.calls if c.op == op), None)


def _rows(con, c):
    return run_template(con, c.op, resolve_raw_params(c.params)).rows


def _ask(ctx, q):
    out = answer_question(q, ctx, today=TODAY)
    return out if isinstance(out, dict) else serialize_answer(out)


# ── 1. 국내 보수×순자산 순위 합(주최 p.4 예시 국내판) · 지수 추종 보수 비교 ──────────────────────

@pytest.mark.parametrize("q", ["국내 상장 ETF 중 총보수 낮고 순자산 큰 3개", "총보수가 낮고 운용규모가 큰 국내 ETF 3개만"])
def test_domestic_fee_aum_rank(index, con, q):
    plan = _route(index, q)
    assert plan.intent == "etp_fee_aum_rank" and plan.behavior_hint == "partial"
    c = _call(plan, "etp_fee_aum_rank")
    assert c.params == {"instrument_type": "ETF", "limit": 3}
    rows = _rows(con, c)
    assert len(rows) == 3 and all(float(r["cu_charge_rt"]) > 0 for r in rows)
    exp = con.execute("SELECT pd_abrv_nm FROM (SELECT pd_abrv_nm, RANK() OVER (ORDER BY TRY_CAST(cu_charge_rt AS DOUBLE) ASC) "
                      "+ RANK() OVER (ORDER BY TRY_CAST(pd_net_tamt AS DOUBLE) DESC) AS s, TRY_CAST(pd_net_tamt AS DOUBLE) AS aum, pd_itm_no "
                      "FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND TRY_CAST(cu_charge_rt AS DOUBLE)>0 "
                      "AND TRY_CAST(pd_net_tamt AS DOUBLE)>0) ORDER BY s, aum DESC, pd_itm_no LIMIT 1").fetchone()[0]
    assert rows[0]["pd_abrv_nm"] == exp
    assert _call(plan, "coverage_check") is not None      # 총보수 값 보유 18.5% — 부분 답변 명시


def test_index_fee_compare(index, con):
    plan = _route(index, "KOSPI200을 추종하는 ETF들의 총보수 비교")
    c = _call(plan, "etp_low_fee")
    assert c is not None and c.params["name_pattern"] == "%KOSPI%200%"
    assert _rows(con, c)


# ── 2. 상세 항목 — 상장주식수·연초 이후 수익률·환헤지·판매회사·배수 ───────────────────────────

def test_detail_focus_fields(ctx):
    a = _ask(ctx, "KODEX 200 상장주식수 얼마야")["answer"]
    assert "상장주식수" in a and "상장일" not in a.split("※")[0].split("·")[0]   # 첫 항목이 상장주식수
    b = _ask(ctx, "TIGER 200 1년 수익률이랑 연초 이후 수익률")["answer"]
    assert "연초 이후 수익률" in b and "1년 수익률" in b


def test_detail_notes_for_missing_fields(index):
    p1 = _route(index, "TIGER 미국S&P500 환헤지 해?")
    assert p1.intent == "product_detail" and any("(H)" in n for n in p1.notes)
    p2 = _route(index, "미래에셋 TIGER 200 판매회사 어디야")
    assert any("판매회사" in n for n in p2.notes)
    p3 = _route(index, "KODEX 레버리지 몇 배 레버리지야")
    assert any("배수" in n for n in p3.notes)


# ── 3. 집계 — 순자산 합계·운용사별·대분류별 · 거절(테마별) ────────────────────────────────────

def test_aum_sum(index, con):
    plan = _route(index, "반도체 ETF 순자산 합계")
    assert plan.intent == "etp_aum_sum"
    c = _call(plan, "etp_aum_sum")
    assert c.params == {"instrument_type": "ETF", "name_pattern": "%반도체%"}
    row = _rows(con, c)[0]
    exp = con.execute("SELECT count(*), sum(TRY_CAST(pd_net_tamt AS DOUBLE)) FROM kr_etp WHERE drv_instrument_type='ETF' "
                      "AND drv_listing_status='active' AND (pd_nm ILIKE '%반도체%' OR pd_abrv_nm ILIKE '%반도체%')").fetchone()
    assert int(row["n"]) == exp[0] and abs(float(row["total_aum"]) - float(exp[1])) < 1
    assert _call(_route(index, "KODEX ETF 순자산 합계 얼마야"), "etp_aum_sum").params["name_pattern"] == "%KODEX%"


def test_manager_share_and_bond_class_dist(index):
    for q in ("운용사별 ETF 개수", "운용사별 순자산 점유율"):
        plan = _route(index, q)
        assert plan.intent == "mgmt_share" and _call(plan, "mgmt_top_share") is not None
    assert _route(index, "채권 대분류별 건수").intent == "bond_dist"
    assert _route(index, "테마별 ETF 개수").intent == "unsupported_field"


# ── 4. 기준일 · 상장 구간 표현 ─────────────────────────────────────────────────────────

def test_data_as_of(index, ctx):
    plan = _route(index, "기준일이 언제야")
    assert plan.intent == "data_as_of" and _call(plan, "data_as_of") is not None
    a = _ask(ctx, "데이터 기준일 알려줘")["answer"]
    assert "2026-08-22" in a and "2026-08-21" in a and "2026-08-23" in a
    # 조건이 붙은 조회는 가로채지 않는다(PM 시험 '데이터 기준일 기준으로 종가가 가장 높은 ETF 5개')
    assert _route(index, "데이터 기준일 기준으로 종가가 가장 높은 ETF 5개를 종가와 함께 보여줘").intent != "data_as_of"


def test_recent_listing_windows(index):
    today = datetime.date.today()
    assert parse_listed_from("최근 3개월 내 상장한 ETF 몇 개", "2026") == (today - datetime.timedelta(days=90)).isoformat()
    last_prev = today.replace(day=1) - datetime.timedelta(days=1)
    assert parse_listed_from("지난달 상장한 ETF", "2026") == last_prev.replace(day=1).isoformat()
    assert parse_listed_until("지난달 상장한 ETF") == last_prev.isoformat()
    c = _call(_route(index, "최근 3개월 내 상장한 ETF 몇 개"), "etp_count")
    assert c is not None and "min_listed_dt" in c.params
    plan = _route(index, "지난달 상장한 ETF")
    c2 = _call(plan, "etp_top_aum")
    assert c2 is not None and "min_listed_dt" in c2.params and "max_listed_dt" in c2.params


# ── 5. 함정 ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("ETF랑 펀드 중 뭐가 더 좋아", "action_request"),
    ("삼성전자 목표가 얼마야", "unsupported_field"),
    ("지금 금리 얼마야", "time_violation"),
])
def test_trap_variants_refuse(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


def test_pair_compare_still_answers(index):
    assert _route(index, "KODEX 200이랑 TIGER 200 중 뭐가 더 좋아").intent == "pair_compare"   # 사실 비교는 그대로


# ── 6. 숨김 조회문 — AI 라우터 목록 불변 ───────────────────────────────────────────────

def test_hidden_round11():
    text = _template_catalog_text()
    for tid in ("etp_fee_aum_rank", "etp_aum_sum", "data_as_of"):
        assert tid in TEMPLATES and tid in LLM_HIDDEN_TEMPLATES and f"- {tid}:" not in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
