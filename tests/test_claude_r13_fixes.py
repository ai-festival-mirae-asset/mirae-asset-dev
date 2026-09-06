# -*- coding: utf-8 -*-
"""리더 세션 13바퀴(9/6) — 표현 변형 점검 9차 44문항(복합 조건·상세 기간 항목·종목 비중 건수·채권 발행사·해외 지역 건수·판단 함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '2025년에 상장한 ETF 중 총보수 낮은 것'·'순자산 1조 이상이면서 총보수 0.1% 이하 ETF'가 보수 조건을 버리고
순자산 목록으로 답함 · '월배당이면서 순자산 1000억 이상 ETF'가 월배당 조건을 버림 · 'KODEX 200 최근 1개월 수익률'이 1년 수익률로,
'3개월 변동성'은 초점 없음 · '삼성전자 비중 20% 넘는 ETF 몇 개'가 10건 상한 · '한국전력/국민은행 채권'이 조건 없는 전체 목록 ·
'일본 해외 ETF 몇 개'가 전체 5,972 · 'KODEX 200 매수 타이밍 어때'가 상품 상세 · 거래량·상장주식수 노트가 쉼표 없는 숫자.
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_PARAMS, TEMPLATES, run_template
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


# ── 1. 보수 필터에 상장 구간·순자산 문턱 ─────────────────────────────────────────────────

def test_fee_filter_keeps_listing_window_and_aum_bounds(index):
    c = _call(_route(index, "2025년에 상장한 ETF 중 총보수 낮은 것"), "etp_low_fee")
    assert c is not None and c.params["min_listed_dt"] == "2025-01-01" and c.params["max_listed_dt"] == "2025-12-31"
    c2 = _call(_route(index, "순자산 1조 이상이면서 총보수 0.1% 이하 ETF"), "etp_low_fee")
    assert c2 is not None and c2.params["max_fee"] == 0.1 and c2.params["min_aum_ge"] == 1e12
    c3 = _call(_route(index, "순자산 1조원 이상인 ETF 목록"), "etp_top_aum")          # 보수 낱말 없으면 종전 순자산 목록
    assert c3 is not None and c3.params["min_aum_ge"] == 1e12


def test_fee_filter_sql_applies_bounds(index, con):
    c = _call(_route(index, "순자산 5000억 이상 ETF 중 총보수 낮은 5개"), "etp_low_fee")
    rows = _rows(con, c)
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                      "AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0 AND TRY_CAST(pd_net_tamt AS DOUBLE) >= 5e11").fetchone()[0]
    assert len(rows) == min(exp, c.params["limit"]) and exp > 0


# ── 2. 월배당 × 순자산 문턱 ───────────────────────────────────────────────────────────

def test_monthly_dividend_with_aum_bound(index, con):
    plan = _route(index, "월배당이면서 순자산 1000억 이상 ETF")
    c = _call(plan, "etp_by_dividend")
    assert plan.intent == "etp_dividend_rank" and c.params["min_pay_cnt"] == 12 and c.params["min_aum_ge"] == 1e11
    rows = _rows(con, c)
    assert rows and all(float(r["pd_net_tamt"]) >= 1e11 and int(float(r["pd_dvid_pay_cnt"])) >= 12 for r in rows)


# ── 3. 상세 — 기간별 수익률·변동성 초점 · 노트 정수 표기 ───────────────────────────────────

def test_detail_period_focus_and_integer_notes(ctx, con):
    a = _ask(ctx, "KODEX 200 최근 1개월 수익률")["answer"]
    v1m = con.execute("SELECT TRY_CAST(du_er_1m AS DOUBLE) FROM kr_etp WHERE pd_itm_no='KR7069500007'").fetchone()[0]
    assert "1개월 수익률" in a and f"{v1m:g}" in a and "※ 'KODEX 200' 1년 수익률" not in a
    b = _ask(ctx, "KODEX 200 3개월 변동성 얼마야")["answer"]
    assert "3개월 변동성" in b
    c = _ask(ctx, "KODEX 200 1일 거래량")["answer"]
    assert "33,240,038" in c and "33240038" not in c
    d = _ask(ctx, "TIGER 200 1년 수익률이랑 연초 이후 수익률")["answer"]        # r11 동작 유지
    assert "연초 이후 수익률" in d and "1년 수익률" in d


# ── 4. 종목 비중 문턱 건수 · 채권 발행사 · 해외 지역 건수 ────────────────────────────────

def test_weight_threshold_count(index, con):
    plan = _route(index, "삼성전자 비중 20% 넘는 ETF 몇 개")
    c = _call(plan, "constituent_weight_above")
    assert c.params["limit"] == 2000 and c.params["min_weight"] == 20.0 and plan.hints.get("display_rows") == 5
    assert any("결과 N건" in n for n in plan.notes) and len(_rows(con, c)) > 10
    assert _call(_route(index, "삼성전자 비중 20% 넘는 ETF"), "constituent_weight_above").params["limit"] < 2000


def test_bond_issuer_name_search(index, con):
    for q, tok in (("한국전력 채권 있어", "한국전력"), ("국민은행 채권 알려줘", "국민은행"), ("삼성전자가 발행한 채권 있어", "삼성전자")):
        plan = _route(index, q)
        c = _call(plan, "bond_filter")
        assert plan.intent == "bond_filter" and c.params["name_pattern"] == f"%{tok}%", q
        assert _call(plan, "bond_count").params["name_pattern"] == f"%{tok}%"
        assert any("발행사 표기" in n for n in plan.notes)
    rows = _rows(con, _call(_route(index, "한국전력 채권 있어"), "bond_filter"))
    assert rows and all("한국전력" in (r["PD_NM"] + (r["PD_ABRV_NM"] or "")) for r in rows)
    for q in ("회사채 몇 개", "AAA 회사채 중 만기 가장 긴 것", "달러 표시 채권 있어", "국고채 알려줘"):
        c = _call(_route(index, q), "bond_filter") or _call(_route(index, q), "bond_count")
        assert c is not None and "name_pattern" not in c.params, q


def test_global_count_with_region(index, con):
    plan = _route(index, "일본 해외 ETF 몇 개")
    c = _call(plan, "global_etf_count")
    assert plan.intent == "global_count" and c.params["region_pattern_raw"] == "Japan"
    rows = _rows(con, c)
    exp = con.execute("SELECT count(*) FROM global_etf WHERE drv_instrument_type='ETF' AND wu_inv_rgn ILIKE '%Japan%'").fetchone()[0]
    assert int(next(r["n"] for r in rows if r["drv_instrument_type"] == "ETF")) == exp
    assert "region_pattern_raw" not in _call(_route(index, "해외 ETF 몇 개"), "global_etf_count").params


# ── 5. 함정 ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["KODEX 200 매수 타이밍 어때", "지금 KODEX 200 살 때야?", "TIGER 200 팔 때인가"])
def test_buy_timing_refuse(index, q):
    plan = _route(index, q)
    assert plan.intent == "action_request" and plan.behavior_hint == "refuse", (q, plan.intent)


# ── 6. 숨김 파라미터 — AI 라우터 목록 불변 ──────────────────────────────────────────────

def test_hidden_round13():
    text = _template_catalog_text()
    for tid, p in (("etp_low_fee", "min_listed_dt"), ("etp_low_fee", "min_aum_ge"), ("etp_by_dividend", "min_aum_ge"),
                   ("bond_filter", "name_pattern"), ("bond_count", "name_pattern"), ("global_etf_count", "region_pattern")):
        assert (tid, p) in LLM_HIDDEN_PARAMS and p in [x.name for x in TEMPLATES[tid].params], (tid, p)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
