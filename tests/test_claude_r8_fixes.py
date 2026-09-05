# -*- coding: utf-8 -*-
"""리더 세션 8바퀴(9/6) — 표현 변형 점검 4차 53문항(채권·복합 조건·해외·펀드·표현·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '2025년에 상장한'이 하한만 걸려 2026년 상장분 포함 · 채권 '만기 짧은 순'·'발행일 최근'이 기본 정렬 ·
'투자적격 등급'·'만기 10년 넘는'이 전체 건수 · '2027년 만기'가 연도 조건 누락 · 이자 유형(할인채)·금리 유형(변동금리) 미지원 ·
'월배당 ETF 중 순자산 상위'가 분배수익률 순 · '커버드콜 ETF 분배수익률'이 전체 순위 · '분배금 12회 몇 개'가 목록 · 해외 'N배'·'인버스 레버리지'
조건 누락 · 'ARK 운용 ETF'가 국내 표 0건(고치자 Markets 오탐) · '기초지수 S&P 500 해외 ETF'가 국내 표 · '국내+해외 개수' 한쪽만 ·
'순자산/설정액 가장 큰 펀드' 폴백 · '펀드 이름에 X' ETF 테마 검색 · 함정(수익률 보장·계좌 잔고·양도세·종목 배당·사면 얼마·AI 예측).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.channels import resolve_raw_params
from engine.policy import load_policy
from engine.router import parse_listed_from, parse_listed_until, route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_ENUM_VALUES, LLM_HIDDEN_PARAMS, TEMPLATES, run_template
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
    return next((str(r[k]) for k in ("pd_abrv_nm", "pd_nm", "itm_abrv_nm", "itm_nm", "PD_ABRV_NM", "PD_NM") if r.get(k)), "")


# ── 1. 'YYYY년에 상장' = 그 해 안 ────────────────────────────────────────────────────────

def test_listed_exact_year_parsers():
    assert parse_listed_from("2025년에 상장한 ETF", "2026") == "2025-01-01"
    assert parse_listed_until("2025년에 상장한 ETF") == "2025-12-31"
    assert parse_listed_until("2024년 이후 상장한 ETF") is None
    assert parse_listed_until("2020년 이전에 상장한 ETF") == "2019-12-31"


def test_listed_exact_year_rank_and_count(index, con):
    plan = _route(index, "2025년에 상장한 ETF 중 순자산 1위")
    c = _call(plan, "etp_top_aum")
    assert c.params["min_listed_dt"] == "2025-01-01" and c.params["max_listed_dt"] == "2025-12-31"
    assert str(_rows(con, c)[0]["pd_lstg_dt"]).startswith("2025")            # 종전엔 2026년 상장분(SOL AI반도체TOP2플러스)
    c2 = _call(_route(index, "2025년에 상장한 ETN 몇 개"), "etp_count")
    assert c2.params == {"min_listed_dt": "2025-01-01", "max_listed_dt": "2025-12-31"}
    c3 = _call(_route(index, "2020년 이전에 상장한 ETF 몇 개"), "etp_count")
    assert c3.params == {"max_listed_dt": "2019-12-31"}
    c4 = _call(_route(index, "2024년 이후 상장한 배당 ETF 순자산 큰 3개"), "etp_top_aum")
    assert c4.params["min_listed_dt"] == "2024-01-01" and "max_listed_dt" not in c4.params


# ── 2. 채권 — 정렬·등급·연도·잔존만기 건수·이자/금리 유형 ─────────────────────────────────

def test_bond_maturity_and_issue_order(index, con):
    c = _call(_route(index, "만기 짧은 순으로 회사채 5개"), "bond_filter")
    assert c.params["order"] == "mat_asc" and c.params["bond_class"] == "회사채"
    mats = [r["MAT_DT"] for r in _rows(con, c)]
    assert mats == sorted(mats)
    c2 = _call(_route(index, "발행일 가장 최근 채권 5개"), "bond_filter")
    assert c2.params["order"] == "issue_desc"
    isu = [r.get("ISU_DT") for r in run_template(con, "bond_filter", resolve_raw_params(dict(c2.params))).rows if r.get("ISU_DT")]
    assert isu == sorted(isu, reverse=True) or not isu    # 목록 열에 ISU_DT 가 없으면 정렬만 확인


def test_investment_grade_count(index, con):
    plan = _route(index, "투자적격 등급 채권 몇 개")
    c = _call(plan, "bond_count")
    assert c.params == {"max_rating_rank": 10}
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE TRY_CAST(drv_crd_grd_rank AS INT) <= 10").fetchone()[0]
    assert int(_rows(con, c)[0]["n"]) == exp and any("투자적격" in n for n in plan.notes)


def test_exact_maturity_year(index, con):
    c = _call(_route(index, "2027년 만기 채권 표면금리 높은 순"), "bond_maturing_within")
    assert c.params["as_of_date"] == "2027-01-01" and c.params["until"] == "2027-12-31"
    assert all(str(r["MAT_DT"]).startswith("2027") for r in _rows(con, c))


def test_residual_maturity_counts(index, con):
    plan = _route(index, "만기 10년 넘는 채권 몇 개")
    c = _call(plan, "bond_maturing_within")
    assert c.params["as_of_date"] == "2036-09-06" and c.params["limit"] >= 30000
    assert plan.hints.get("skip_generation") is True and any("결과 N건" in n for n in plan.notes)
    c2 = _call(_route(index, "잔존만기 1년 이내 국공채 몇 개"), "bond_maturing_within")
    assert c2.params["limit"] >= 30000 and c2.params["bond_class"] == "국공채"


def test_bond_interest_and_rate_type(index, con):
    c = _call(_route(index, "변동금리 채권 몇 개"), "bond_count")
    assert c.params["rate_type"] == "변동금리"
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE BD_INRT_TCD='변동금리'").fetchone()[0]
    assert int(_rows(con, c)[0]["n"]) == exp
    plan = _route(index, "할인채 알려줘")
    c2 = _call(plan, "bond_filter")
    assert c2 is not None and c2.params["int_type"] == "할인채"
    assert _route(index, "이자 지급 주기가 3개월인 채권 있어").intent == "unsupported_field"


# ── 3. 분배 순위 — 순자산 정렬·테마 표기·건수 ─────────────────────────────────────────────

def test_dividend_rank_by_aum(index, con):
    c = _call(_route(index, "월배당 ETF 중 순자산 상위 5개"), "etp_by_dividend")
    assert c.params["order"] == "aum" and c.params["min_pay_cnt"] == 12
    aums = [float(r["pd_net_tamt"]) for r in _rows(con, c)]
    assert aums == sorted(aums, reverse=True)


def test_dividend_count_and_theme(index, con):
    plan = _route(index, "분배금 12회 지급하는 ETF 몇 개")
    c = _call(plan, "etp_by_dividend")
    assert c.params["limit"] >= 2000 and c.params["min_pay_cnt"] == 12 and c.params["max_pay_cnt"] == 12
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                      "AND TRY_CAST(pd_dvid_pay_cnt AS INT)=12 AND coalesce(TRY_CAST(pd_divd_amt_ann AS DOUBLE),0)<>0").fetchone()[0]
    assert len(_rows(con, c)) == exp
    c2 = _call(_route(index, "커버드콜 ETF 분배수익률 높은 순 3개"), "etp_by_dividend")
    assert c2.params["name_pattern"] == "%커버드콜%"
    assert all("커버드콜" in _name(r) for r in _rows(con, c2))
    assert "name_pattern" not in _call(_route(index, "배당수익률 높은 ETF 3개"), "etp_by_dividend").params


# ── 4. 해외 — N배·인버스 공통·기초지수·운용사 낱말 경계·국내+해외 건수 ────────────────────────

def test_global_leverage_factor_and_inverse(index, con):
    c = _call(_route(index, "해외 3배 레버리지 ETF 몇 개"), "global_etf_count")
    assert c.params == {"leveraged_only": "Y", "lev_abs": 3.0}
    exp = con.execute("SELECT count(*) FROM global_etf WHERE abs(TRY_CAST(cu_lev_fector AS DOUBLE))=3 AND drv_instrument_type='ETF'").fetchone()[0]
    assert next(int(r["n"]) for r in _rows(con, c) if r["drv_instrument_type"] == "ETF") == exp
    c2 = _call(_route(index, "해외 인버스 레버리지 ETF 순자산 큰 3개"), "global_etf_filter")
    assert c2.params["inverse_only"] == "Y" and c2.params["leveraged_only"] == "Y"
    assert _name(_rows(con, c2)[0]) == "SQQQ"
    c3 = _call(_route(index, "해외 인버스 ETF 순자산 큰 3개"), "global_etf_filter")
    assert c3.params["inverse_only"] == "Y"                # 종전엔 인버스 조건 없는 전체 순위


def test_global_manager_word_boundary(index, con):
    plan = _route(index, "ARK가 운용하는 ETF 알려줘")
    assert plan.intent == "global_filter"
    c = _call(plan, "global_etf_filter")
    assert c.params["mgmt_pattern_raw"] == "ARK" and "ARK" in c.params["brand_word"]
    names = [str(r.get("pd_nm")) for r in _rows(con, c)]
    assert names and all("ARK" in n.upper().replace("MARKET", "") for n in names) and not any("Vanguard" in n for n in names)


def test_global_base_index_rank(index, con):
    plan = _route(index, "해외 ETF 중 기초지수가 S&P 500인 것 순자산 큰 3개")
    assert plan.intent == "global_aum_rank"
    c = _call(plan, "global_etf_filter")
    assert c.params["name_pattern"] == "%S&P%500%"
    assert _name(_rows(con, c)[0]) == "VOO"
    assert _route(index, "S&P500 따라가는 ETF 중 순자산 제일 큰 거").intent == "index_products_rank"   # 국내는 7.5 유지


def test_domestic_and_global_counts_together(index):
    plan = _route(index, "국내 ETF 개수와 해외 ETF 개수 알려줘")
    assert _call(plan, "global_etf_count") is not None and _call(plan, "etp_count") is not None


# ── 5. 펀드 — 순자산/설정액 순위·이름에 X·이름×위험등급 ────────────────────────────────────

@pytest.mark.parametrize("q", ["순자산 가장 큰 펀드", "설정액 가장 큰 펀드"])
def test_fund_aum_rank_without_type(index, con, q):
    plan = _route(index, q)
    c = _call(plan, "fund_filter")
    assert c is not None and c.params["order"] == "aum" and c.params["limit"] == 5
    exp = con.execute("SELECT itm_nm FROM fund_master ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST, itm_no LIMIT 1").fetchone()[0]
    assert _rows(con, c)[0]["itm_nm"] == exp
    if "설정액" in q:
        assert any("설정액" in n for n in plan.notes)


def test_fund_name_contains_phrase(index, con):
    plan = _route(index, "펀드 이름에 배당 들어간 것 알려줘")
    assert plan.intent == "fund_name_search"
    c = _call(plan, "fund_filter")
    assert c.params["name_pattern_raw"] == "배당"
    assert all("배당" in (r["itm_nm"] + str(r.get("itm_abrv_nm"))) for r in _rows(con, c))


def test_fund_name_search_keeps_risk(index, con):
    c = _call(_route(index, "연금저축 펀드 중 위험등급 낮은 3개"), "fund_filter")
    assert c.params["name_pattern_raw"] == "연금저축" and c.params["min_risk"] == 5 and c.params["max_risk"] == 6
    assert all(int(r["drv_risk_grade"]) >= 5 for r in _rows(con, c))


# ── 6. 함정 변형 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("부동산 펀드 수익률 보장돼?", "action_request"),
    ("내 계좌 잔고 알려줘", "action_request"),
    ("ETF 양도세 얼마야", "action_request"),
    ("삼성전자 배당금 언제 줘", "unsupported_field"),
    ("지금 사면 얼마 벌어", "action_request"),
    ("AI가 예측하는 내년 코스피", "time_violation"),
    ("KODEX 200 공매도 잔고 알려줘", "unsupported_field"),   # v3 T 가족 유지 — 잔고 규칙이 가로채지 않는다
])
def test_trap_variants_refuse(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


def test_trap_words_do_not_overreach(index):
    assert _route(index, "KODEX 200 배당수익률이 얼마야").intent == "product_detail"
    assert _route(index, "부동산 펀드 1년 수익률 높은 3개").behavior_hint != "refuse"


# ── 7. 숨김 허용값·파라미터 — AI 라우터 목록 불변 ─────────────────────────────────────────

def test_hidden_values_round8():
    text = _template_catalog_text()
    assert set(LLM_HIDDEN_ENUM_VALUES[("bond_filter", "order")]) >= {"mat_asc", "mat_desc", "issue_desc", "issue_asc"}
    for tid, p in [("bond_filter", "int_type"), ("bond_filter", "rate_type"), ("bond_count", "int_type"), ("bond_count", "rate_type"),
                   ("etp_by_dividend", "order"), ("global_etf_filter", "lev_abs"), ("global_etf_count", "lev_abs"),
                   ("global_etf_filter", "brand_word"), ("global_etf_count", "brand_word")]:
        assert (tid, p) in LLM_HIDDEN_PARAMS and any(x.name == p for x in TEMPLATES[tid].params)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
