# -*- coding: utf-8 -*-
"""리더 세션 10바퀴(9/6) — 표현 변형 점검 6차 41문항(오타·구어체·숫자 조건·해외 숫자·교차 복합·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '-10% 이하'의 부호가 사라져 '10% 이하'로 조회(수익률 건수·펀드 수익률) · '분배수익률 3% 이상 5% 이하 ETF'
폴백(순위 낱말이 없음) · '총보수 0.5% 초과' 폴백(상한만 지원) · 'AA- 이상 AA+ 이하' 두 경계 중 하나만 · '펀드 순자산 1000억 이상 몇 개'가 전체 건수 ·
'클래스 A 펀드 몇 개' 폴백 · '1000억 달러 이상'의 '달러'가 통화 조건으로 오인(국내 표 목록으로도 새던 것) · '코스피 200 지수 지금 얼마'가 0건 답변 ·
'해외 ETF 중 총보수 1% 넘는 것 몇 개'가 전체 건수 · '삼성전자 담은 ETF 중 위험등급 1등급/2024년 이후 상장' 조건 누락 · '해외 ETF 중 한국 투자' 폴백 ·
함정('내 수익률 계산'·'원금 손실 가능성'·'총보수 0인 해외 ETF') · 'ETF 뭐 있어'·'펀드 뭐 있어' 폴백 거절.
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.channels import resolve_raw_params
from engine.policy import load_policy
from engine.router import detect_currency, extract_percents, route
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


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


def _call(plan, op):
    return next((c for c in plan.calls if c.op == op), None)


def _rows(con, c):
    return run_template(con, c.op, resolve_raw_params(c.params)).rows


def _name(r):
    return next((str(r[k]) for k in ("pd_abrv_nm", "pd_nm", "itm_abrv_nm", "itm_nm") if r.get(k)), "")


# ── 1. 음수 퍼센트 ────────────────────────────────────────────────────────────────────────

def test_negative_percent_parsing():
    assert extract_percents("1년 수익률 -10% 이하 ETF 몇 개") == [(-10.0, "return", "이하")]
    assert extract_percents("수익률 마이너스 5% 이하 펀드")[0][0] == -5.0
    assert extract_percents("총보수 0.3% 이하")[0] == (0.3, "fee", "이하")


def test_negative_return_thresholds(index, con):
    c = _call(_route(index, "1년 수익률 -10% 이하 ETF 몇 개"), "etp_count")
    assert c.params == {"max_er_1y": -10.0}                # 종전엔 부호가 빠져 10% 이하
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                      "AND TRY_CAST(du_er_1y AS DOUBLE) < -10").fetchone()[0]
    assert next(int(r["n"]) for r in _rows(con, c) if r["drv_instrument_type"] == "ETF" and r["drv_listing_status"] == "active") == exp
    c2 = _call(_route(index, "주식형 펀드 중 1년 수익률 -5% 이하"), "fund_top_return_1y")
    assert -5.0 < c2.params["max_return"] < -4.99 and all(float(r["fd_yr1_ern_r"]) <= -5.0 for r in _rows(con, c2))


# ── 2. 분배 구간 · 총보수 하한 · 등급 두 경계 ──────────────────────────────────────────────

def test_dividend_band_without_rank_word(index, con):
    c = _call(_route(index, "분배수익률 3% 이상 5% 이하 ETF"), "etp_by_dividend")
    assert c is not None and c.params["min_yield"] == 3.0 and 5.0 < c.params["max_yield"] < 5.01
    assert all(3.0 <= float(r["pd_dvid_yield"]) <= 5.0 for r in _rows(con, c))


def test_fee_lower_bound_uses_metric_rank(index, con):
    c = _call(_route(index, "총보수 0.5% 초과 ETF 알려줘"), "etp_metric_rank")
    assert c is not None and c.params["metric"] == "fee" and c.params["min_metric"] == 0.5 and c.params["direction"] == "desc"
    assert all(float(r["cu_charge_rt"]) > 0.5 for r in _rows(con, c))
    assert _call(_route(index, "총보수 0.3% 이하 ETF 알려줘"), "etp_low_fee") is not None   # 상한은 종전 규칙 유지


def test_rating_two_bounds(index, con):
    c = _call(_route(index, "신용등급 AA- 이상 AA+ 이하 회사채 몇 개"), "bond_count")
    assert c.params["max_rating_rank"] == 4 and c.params["min_rating_rank"] == 2 and c.params["bond_class"] == "회사채"
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 2 AND 4").fetchone()[0]
    assert int(_rows(con, c)[0]["n"]) == exp
    assert "min_rating_rank" not in _call(_route(index, "AA 이상 회사채 몇 개"), "bond_count").params


# ── 3. 펀드 — 순자산 문턱 건수 · 클래스 A/C ──────────────────────────────────────────────

def test_fund_count_with_aum_threshold(index, con):
    c = _call(_route(index, "펀드 순자산 1000억 이상 몇 개"), "fund_filter")
    assert c.params["min_aum"] == 1e11 and c.params["limit"] >= 30000
    exp = con.execute("SELECT count(*) FROM fund_master WHERE TRY_CAST(fd_nast_suma AS DOUBLE) >= 1e11").fetchone()[0]
    assert len(_rows(con, c)) == exp


def test_class_letter_mapping(index):
    plan = _route(index, "클래스 A 펀드 몇 개")
    c = _call(plan, "fund_class_count")
    assert c is not None and c.params["fee_type"] == "수수료선취" and c.params["channel_pattern"] == "%오프라인%"
    c2 = _call(_route(index, "Ce 클래스 펀드 알려줘"), "fund_class_by_fee")
    assert c2 is not None and c2.params["fee_type"] == "수수료미징구" and c2.params["channel_pattern"] == "%온라인%"
    assert _route(index, "펀드 클래스 A와 C 차이가 뭐야").intent == "fund_class_compare"   # 사전 비교는 그대로


# ── 4. 해외 — 금액 단위 '달러'·문턱 목록·보수 건수·한국 ─────────────────────────────────────

def test_dollar_amount_is_not_a_currency_condition():
    assert detect_currency("순자산 1000억 달러 이상 ETF") == (None, False)
    assert detect_currency("달러로 거래되는 ETF")[0] == "USD"


def test_global_threshold_list(index, con):
    plan = _route(index, "나스닥 상장 ETF 중 순자산 1000억 달러 이상")
    assert plan.intent == "global_filter"
    c = _call(plan, "global_etf_filter")
    assert c.params["min_aum_ge"] == 1e11
    rows = _rows(con, c)
    assert rows and all(float(r["du_last_aum"]) >= 1e11 for r in rows) and _name(rows[0]) == "VOO"


def test_global_count_with_fee_bound(index, con):
    c = _call(_route(index, "해외 ETF 중 총보수 1% 넘는 것 몇 개"), "global_etf_count")
    assert c.params == {"min_fee_gt": 1.0}
    exp = con.execute("SELECT count(*) FROM global_etf WHERE drv_instrument_type='ETF' AND TRY_CAST(cu_charge_rt AS DOUBLE) > 1").fetchone()[0]
    assert next(int(r["n"]) for r in _rows(con, c) if r["drv_instrument_type"] == "ETF") == exp


def test_global_korea_region(index, con):
    plan = _route(index, "해외 ETF 중 한국 투자하는 것")
    assert plan.intent == "global_filter"
    c = _call(plan, "global_etf_filter")
    assert c.params["region_pattern_raw"] == "Korea"
    rows = _rows(con, c)
    assert rows and all("Korea" in str(r["wu_inv_rgn"]) for r in rows)


# ── 5. 편입 ETF 에 위험등급·상장일 조건 ───────────────────────────────────────────────────

def test_holders_with_risk_and_listing(index, con):
    c = _call(_route(index, "삼성전자 담은 ETF 중 위험등급 1등급"), "constituent_holders")
    assert c.params["min_risk"] == 1 and c.params["max_risk"] == 1
    assert all(int(r["drv_risk_grade"]) == 1 for r in _rows(con, c))
    c2 = _call(_route(index, "삼성전자 담은 ETF 중 2024년 이후 상장"), "constituent_holders")
    assert c2.params["min_listed_dt"] == "2024-01-01"
    assert all(str(r["pd_lstg_dt"]).replace("-", "") >= "20240101" for r in _rows(con, c2))


# ── 6. 시세·함정·개요 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("코스피 200 지수 지금 얼마", "time_violation"),
    ("내 수익률 계산해줘", "action_request"),
    ("TIGER 200 원금 손실 가능성 있어?", "action_request"),
    ("총보수 0인 해외 ETF 몇 개", "unsupported_field"),
])
def test_trap_variants_refuse(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


def test_trap_words_do_not_overreach(index):
    assert _route(index, "총보수 0.3% 이하 ETF 알려줘").behavior_hint != "refuse"
    assert _route(index, "삼성전자 포함 ETF 중 총보수 0.1% 이하").behavior_hint != "refuse"
    assert _route(index, "코스피200 추종 ETF 몇 개 있어").intent == "index_products_count"


def test_overview_questions(index):
    plan = _route(index, "ETF 뭐 있어")
    assert plan.intent == "etp_overview" and _call(plan, "etp_top_aum") is not None and _call(plan, "etp_count") is not None
    plan2 = _route(index, "펀드 뭐 있어")
    assert plan2.intent == "fund_overview" and _call(plan2, "fund_counts") is not None
    assert _call(_route(index, "ETN 뭐 있어?"), "etp_top_aum").params["instrument_type"] == "ETN"


# ── 7. 숨김 파라미터 — AI 라우터 목록 불변 ──────────────────────────────────────────────

def test_hidden_round10():
    text = _template_catalog_text()
    for tid, p in [("fund_filter", "min_aum"), ("fund_filter", "max_aum"), ("global_etf_count", "min_fee_gt"),
                   ("global_etf_count", "max_fee_le"), ("constituent_holders", "min_risk"), ("constituent_holders", "min_listed_dt")]:
        assert (tid, p) in LLM_HIDDEN_PARAMS and any(x.name == p for x in TEMPLATES[tid].params)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
