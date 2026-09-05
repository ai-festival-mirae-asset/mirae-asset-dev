# -*- coding: utf-8 -*-
"""리더 세션 9바퀴(9/6) — 표현 변형 점검 5차 50문항(지표·기간·채권·펀드·해외·종목·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '3개월 수익률 가장 낮은'이 내림차순 · '추적오차 큰'이 오름차순('추적'의 '적' 오탐) · '거래량 많은' 폴백 ·
'반도체 ETF 중 위험등급 2등급 이하'가 테마 누락 · 'AAA 특수채'의 AAA 가 해외 티커로 오인 · '만기 2년 이하 회사채 세후수익률'이 만기 창·정렬 누락 ·
'회사채 신용등급 분포'가 목록 · '신용등급 없는 채권'이 전체 건수 · '위험등급별로 몇 개씩'이 항상 오류(잠복) · '국고채 3년물'이 일반 목록 ·
펀드(유형×등급 누락·비율·클래스 수·운용사+이름 조각·설정일·벤치마크 코스피) · 해외(VOO vs SPY 폴백·지역 목록의 보수 조건 누락·
iShares 평균이 국내 0건) · '삼성전자 안 담은 반도체 ETF'가 편입 목록 · 함정(주식 추천·부동산·목표가·상장폐지 절차) · 거래정지.
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.channels import resolve_raw_params
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_ENUM_VALUES, LLM_HIDDEN_PARAMS, LLM_HIDDEN_TEMPLATES, TEMPLATES, run_template
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


def _calls(plan, op):
    return [c for c in plan.calls if c.op == op]


def _rows(con, c):
    return run_template(con, c.op, resolve_raw_params(c.params)).rows


def _name(r):
    return next((str(r[k]) for k in ("pd_abrv_nm", "pd_nm", "itm_abrv_nm", "itm_nm", "PD_ABRV_NM", "PD_NM") if r.get(k)), "")


# ── 1. 국내 ETP 지표 — 방향·트리거·테마 ─────────────────────────────────────────────────────

def test_return_rank_lowest(index, con):
    plan = _route(index, "3개월 수익률 가장 낮은 ETF")
    c = _call(plan, "etp_top_return")
    assert c.params["metric"] == "3m" and c.params["order"] == "asc"
    vals = [float(r["du_er_3m"]) for r in _rows(con, c)]
    assert vals == sorted(vals) and vals[0] < 0
    assert "order" not in _call(_route(index, "1년 수익률 높은 ETF 5개"), "etp_top_return").params


def test_metric_direction_words(index):
    assert _call(_route(index, "추적오차 큰 ETF 5개"), "etp_metric_rank").params["direction"] == "desc"   # '추적'의 '적' 오탐
    assert _call(_route(index, "추적오차 적은 ETF 5개"), "etp_metric_rank").params["direction"] == "asc"
    c = _call(_route(index, "거래량 많은 ETF 5개"), "etp_metric_rank")
    assert c is not None and c.params["metric"] == "volume" and c.params["direction"] == "desc"


def test_risk_filter_keeps_theme(index, con):
    c = _call(_route(index, "반도체 ETF 중 위험등급 2등급 이하"), "etp_filter_risk")
    assert c.params["name_pattern"] == "%반도체%" and c.params["max_grade"] == 2
    rows = _rows(con, c)
    assert rows and all("반도체" in _name(r) for r in rows)
    assert "name_pattern" not in _call(_route(index, "위험등급 1등급 ETF 세 개만"), "etp_filter_risk").params


# ── 2. 채권 — 등급 기호·만기 창·정렬·분포·무등급·N년물 ─────────────────────────────────────

def test_rating_symbol_in_bond_context_is_not_a_ticker(index):
    plan = _route(index, "AAA 특수채 표면금리 높은 순 5개")
    c = _call(plan, "bond_filter")
    assert plan.intent == "bond_filter" and c.params["bond_class"] == "특수채"
    assert c.params["max_rating_rank"] == 1 and c.params["min_rating_rank"] == 1 and c.params["order"] == "coupon"
    assert _call(plan, "global_etf_detail") is None


def test_maturity_window_with_after_tax_order(index, con):
    plan = _route(index, "만기 2년 이하 회사채 세후수익률 높은 순 3개")
    c = _call(plan, "bond_maturing_within")
    assert c.params["until"] == "2028-09-06" and c.params["order"] == "after_tax" and c.params["limit"] == 3
    vals = [float(r["AFTER_TAX_YIELD"]) for r in _rows(con, c)]
    assert vals == sorted(vals, reverse=True) and len(vals) == 3


def test_bond_rating_distribution(index, con):
    plan = _route(index, "회사채 신용등급 분포 알려줘")
    assert plan.intent == "bond_dist"
    c = _call(plan, "bond_rating_dist")
    assert c.params == {"bond_class": "회사채", "maturity_status": "active"}
    rows = {r["drv_crd_grd_norm"]: int(r["n"]) for r in _rows(con, c)}
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND drv_maturity_status='active' "
                      "AND drv_crd_grd_norm='AAA'").fetchone()[0]
    assert rows["AAA"] == exp
    assert _route(index, "채권 신용등급별 몇 개씩 있어").intent == "bond_dist"   # 5.45 위험등급별 규칙이 가로채지 않는다


def test_unrated_bond_count(index, con):
    c = _call(_route(index, "신용등급 없는 채권 몇 개"), "bond_count")
    assert c.params == {"unrated_only": "Y"}
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE drv_crd_grd_rank IS NULL OR trim(coalesce(drv_crd_grd_norm,''))=''").fetchone()[0]
    assert int(_rows(con, c)[0]["n"]) == exp


def test_tenor_bucket_refused(index):
    plan = _route(index, "국고채 3년물 있어")
    assert plan.intent == "unsupported_field" and plan.behavior_hint == "refuse"


def test_risk_grade_distribution_no_longer_errors(index, con):
    plan = _route(index, "위험등급별로 몇 개씩 있어")
    assert plan.intent == "risk_grade_cross_counts"
    c = _call(plan, "risk_grade_dist")
    assert c is not None and c.params == {}
    rows = _rows(con, c)                                    # 종전 risk_grade_product_counts {} 는 필수 파라미터 누락 오류
    assert len(rows) >= 12 and {r["product_group"] for r in rows} >= {"국내채권", "국내ETF", "공모펀드"}


# ── 3. 펀드 — 유형×등급·비율·클래스 수·운용사+조각·설정일·벤치마크 ───────────────────────────

def test_fund_risk_filter_keeps_type(index, con):
    c = _call(_route(index, "위험등급 낮은 채권형 펀드 알려줘"), "fund_filter")
    assert c.params["attr_pattern_raw"] == "채권형" and c.params["min_risk"] == 5
    assert all(r["or_attr_desc"] == "채권형" for r in _rows(con, c))


def test_fund_ratio_counts(index, con):
    plan = _route(index, "주식형 펀드 중 위험등급 1등급 비율")
    assert plan.intent == "fund_ratio_counts"
    cs = _calls(plan, "fund_filter")
    assert len(cs) == 2 and cs[0].params["min_risk"] == 1 and "min_risk" not in cs[1].params
    n1, n2 = len(_rows(con, cs[0])), len(_rows(con, cs[1]))
    assert 0 < n1 < n2 and any("비율" in n for n in plan.notes)


def test_fund_class_count_rank(index, con):
    c = _call(_route(index, "클래스 수가 가장 많은 펀드"), "fund_filter")
    assert c.params["order"] == "classes"
    exp = con.execute("SELECT itm_nm FROM fund_master ORDER BY TRY_CAST(share_class_count AS INT) DESC NULLS LAST, itm_no LIMIT 1").fetchone()[0]
    assert _rows(con, c)[0]["itm_nm"] == exp


def test_fund_name_with_company(index, con):
    plan = _route(index, "미래에셋 TDF 펀드 알려줘")
    assert plan.intent == "fund_name_search"
    c = _call(plan, "fund_filter")
    assert c.params["name_pattern"] == "%미래에셋%TDF%"
    rows = _rows(con, c)
    assert rows and all("TDF" in r["itm_nm"].upper() and "미래에셋" in r["itm_nm"] for r in rows)


def test_fund_inception_refused_and_aum_kept(index):
    assert _route(index, "2020년 이후 설정된 펀드 몇 개").intent == "unsupported_field"
    assert _call(_route(index, "설정액 가장 큰 펀드"), "fund_filter").params["order"] == "aum"   # '설정액'은 순자산으로


def test_fund_benchmark_kospi(index):
    plan = _route(index, "벤치마크가 코스피인 펀드")
    assert plan.intent == "fund_by_benchmark" and _call(plan, "fund_by_benchmark") is not None


# ── 4. 해외 — 쌍 비교·지역 목록 보수 조건·영문 운용사 평균 ─────────────────────────────────

def test_global_pair_compare(index):
    plan = _route(index, "VOO랑 SPY 비교해줘")
    assert plan.intent == "global_pair_compare"
    assert {c.params["pd_itm_no"] for c in _calls(plan, "global_etf_detail")} == {"VOO", "SPY"}


def test_global_region_list_keeps_fee_bound(index, con):
    plan = _route(index, "미국 상장 ETF 중 총보수 0.03% 이하")
    cs = _calls(plan, "global_etf_filter")
    assert cs and all(c.params.get("max_fee_le") == 0.03 for c in cs)
    assert all(0 < float(r["cu_charge_rt"]) <= 0.03 for r in _rows(con, cs[0]))


def test_english_manager_fee_average_goes_global(index, con):
    plan = _route(index, "iShares ETF 총보수 평균")
    assert plan.intent == "global_metric_avg"
    c = _call(plan, "global_etf_metric_avg")
    assert c.params["mgmt_pattern_raw"] == "iShares" and c.params["metric"] == "fee"
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(cu_charge_rt AS DOUBLE)),2) FROM global_etf WHERE drv_instrument_type='ETF' "
                      "AND coalesce(TRY_CAST(cu_charge_rt AS DOUBLE),0)>0 AND (cu_fund_mgmt_co ILIKE '%iShares%' "
                      "OR regexp_matches(pd_nm, '(^|[^A-Za-z0-9])iShares([^A-Za-z0-9]|$)', 'i'))").fetchone()[0]
    assert float(row["avg_value"]) == float(exp)


# ── 5. 종목 미편입 ETF ────────────────────────────────────────────────────────────────

def test_constituent_non_holders(index, con):
    plan = _route(index, "삼성전자 안 담은 반도체 ETF")
    assert plan.intent == "constituent_non_holders"
    c = _call(plan, "constituent_non_holders")
    assert c.params["code"] == "005930" and c.params["name_pattern_raw"] == "반도체"
    holders = {r[0] for r in con.execute("SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='005930'").fetchall()}
    rows = _rows(con, c)
    assert rows and all(r["pd_itm_no"] not in holders and "반도체" in _name(r) for r in rows)
    assert _route(index, "삼성전자 담은 반도체 ETF").intent == "constituent_reverse"


# ── 6. 함정 · 거래정지 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("주식 추천해줘", "action_request"),
    ("부동산 가격 어때", "unsupported_asset"),
    ("KODEX 200 목표가", "unsupported_field"),
    ("ETF 상장폐지 되면 돈 돌려받아", "action_request"),
])
def test_trap_variants_refuse(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


def test_trap_words_do_not_overreach(index):
    assert _route(index, "반도체 ETF 추천해줘").behavior_hint != "refuse" or _route(index, "반도체 ETF 추천해줘").intent != "action_request"
    assert _route(index, "부동산 펀드 1년 수익률 높은 3개").behavior_hint != "refuse"


def test_suspended_etf_question(index):
    plan = _route(index, "거래정지된 ETF 있어")
    assert plan.intent == "etp_count" and any("suspended" in n for n in plan.notes)


# ── 7. 숨김 조회문·허용값·파라미터 — AI 라우터 목록 불변 ─────────────────────────────────────

def test_hidden_round9():
    text = _template_catalog_text()
    for tid in ("bond_rating_dist", "constituent_non_holders", "risk_grade_dist"):
        assert tid in TEMPLATES and tid in LLM_HIDDEN_TEMPLATES and f"- {tid}:" not in text
    assert set(LLM_HIDDEN_ENUM_VALUES[("bond_maturing_within", "order")]) >= {"after_tax", "after_tax_asc"}
    for tid, p in [("etp_top_return", "order"), ("etp_filter_risk", "name_pattern"), ("bond_filter", "unrated_only"), ("bond_count", "unrated_only")]:
        assert (tid, p) in LLM_HIDDEN_PARAMS and any(x.name == p for x in TEMPLATES[tid].params)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
