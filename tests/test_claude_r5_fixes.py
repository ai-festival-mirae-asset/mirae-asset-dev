# -*- coding: utf-8 -*-
"""리더 세션 5바퀴(9/6) — 표현 변형 점검 80문항(공식 예시 변형·실전 유형 변형·함정 변형)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): 해외 순자산 순위가 국내 표로 · '합쳐서 연수익률'의 '서 연'이 종목 '서연'으로 오인식 →
교집합 오답 · 교차질의 표현 변형(높은 순 10개) 미인식 · '미국 주식형 ETF' 국내 오해석 · 개별 종목 주가 질문이 폴백 답변 ·
브랜드/운용사/수식어 건수 폴백 · '가장 위험한'·'큰 순으로' 낱말 · 지수 추종 검색에 거래종료 ETN · 'N년 안 남은' 만기 ·
테마 어순 변형(항공우주) · 'TDF 펀드 몇 개' 전체 건수 · 분배 지급횟수 · 상품명 조각이 종목 역질의 가로채기 ·
영문 종목명(Cambricon) 후보 안내로 축소 · 펀드 '설명' 표현 · 테마×수익률 / 채권형×보수 조건 누락.
수정마다 같은 뜻 다른 표현 2~3개를 시험한다(TEAM_IMPROVEMENT_GUIDE §5 수정 원칙 2).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.channels import resolve_raw_params
from engine.policy import load_policy
from engine.router import extract_risk_grades, route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import run_template
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index
from pipeline.themes import detect_theme_terms

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


# ── 1. 개체 경계: 두 글자 종목명이 앞말에 붙으면 이름이 아니다 ─────────────────────────────────

def test_two_char_name_glued_to_previous_word_is_not_grounded(index):
    names = [n for n, _ in index.scan("삼성전자 보유 ETF랑 공모펀드 합쳐서 연수익률 TOP 10")]
    assert "삼성전자" in names and "서연" not in names
    assert any(n == "서연" for n, _ in index.scan("서연 편입 ETF 알려줘"))          # 띄어쓰기·문장 처음이면 그대로 인정
    assert any(n == "서연" for n, _ in index.scan("삼성전자와 서연을 담은 ETF"))


@pytest.mark.parametrize("q", [
    "삼성전자 보유 ETF랑 공모펀드 합쳐서 연수익률 TOP 10",
    "삼성전자 들어간 ETF와 펀드 1년 수익률 높은 순 10개",
    "삼성전자를 보유한 국내/해외ETF 와 공모펀드를 연 수익률 기준 TOP10 알려줘",
])
def test_cross_query_variants_route_to_cross_holder(index, q):
    plan = _route(index, q)
    assert plan.intent == "cross_holder_top_return", plan.intent
    assert _call(plan, "constituent_holders_top_return").params["code"] == "005930"
    assert _call(plan, "fund_top_return_1y") is not None
    assert _call(plan, "constituent_intersection_top_aum") is None


# ── 2. 해외 ETF 순자산 순위 · '미국 주식형 ETF' 해석 ──────────────────────────────────────────

@pytest.mark.parametrize("q, n", [("해외 ETF 중 순자산 가장 큰 3개", 3), ("해외 ETF 규모 상위 5개 알려줘", 5),
                                  ("해외 주식형 ETF AUM 큰 순으로 3개", 3)])
def test_global_aum_rank(index, con, q, n):
    plan = _route(index, q)
    assert plan.intent == "global_aum_rank", plan.intent
    c = _call(plan, "global_etf_filter")
    assert c.params["limit"] == n and "order" not in c.params
    rows = _rows(con, c)
    top = con.execute("SELECT pd_abrv_nm FROM global_etf WHERE TRY_CAST(du_last_aum AS DOUBLE)>0 "
                      + ("AND wu_inv_ast_type='Equity' " if "주식형" in q else "")
                      + "ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC, pd_itm_no LIMIT 1").fetchone()[0]
    assert rows[0]["pd_abrv_nm"] == top
    assert _call(plan, "etp_top_aum") is None                       # 종전 오답 경로(국내 순위)


def test_global_aum_rank_smallest_uses_hidden_order(index, con):
    plan = _route(index, "해외 ETF 순자산 가장 작은 3개")
    c = _call(plan, "global_etf_filter")
    assert c.params.get("order") == "aum_asc"
    rows = _rows(con, c)
    aums = [float(r["du_last_aum"]) for r in rows]
    assert aums == sorted(aums) and all(a > 0 for a in aums)


@pytest.mark.parametrize("q, intent", [
    ("미국 주식형 ETF 총보수 낮고 AUM 큰 상품 세 개", "global_fee_aum_rank"),
    ("미국 채권형 ETF 순자산 큰 5개", "global_aum_rank"),
])
def test_us_typed_etf_is_global(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent, plan.intent
    assert any("해외 ETF 마스터" in n for n in plan.notes)
    assert _call(plan, "etp_low_fee") is None


@pytest.mark.parametrize("q", ["TIGER 미국S&P500 총보수 얼마야", "KODEX 미국S&P500 상장일 알려줘"])
def test_domestic_us_products_stay_domestic(index, q):
    plan = _route(index, q)
    assert plan.intent == "product_detail"


# ── 3. 개별 종목 시세는 원천에 없는 항목 → 거절 ─────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["삼성전자 주가 알려줘", "SK하이닉스 시가총액 알려줘", "에코프로 종가 얼마야"])
def test_stock_price_questions_are_refused(index, q):
    plan = _route(index, q)
    assert plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


@pytest.mark.parametrize("q, intent", [("삼성전자 비중이 가장 높은 ETF", "constituent_reverse"),
                                       ("KODEX 200 종가 알려줘", "product_detail")])
def test_price_words_with_products_or_holdings_still_answer(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint != "refuse"


# ── 4. 건수: 브랜드 · 운용사 · 수식어(테마) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("q, pat", [("TIGER ETF 총 몇 종목?", "TIGER"), ("KODEX ETF 몇 개야", "KODEX")])
def test_brand_count_is_name_count(index, con, q, pat):
    plan = _route(index, q)
    assert plan.intent == "etp_name_count"
    c = _call(plan, "etp_name_search")
    assert c.params["pattern_raw"] == pat and c.params["status"] == "active" and c.params["limit"] == 2000
    n_sql = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                        "AND (pd_abrv_nm ILIKE ? OR pd_nm ILIKE ?)", [f"%{pat}%", f"%{pat}%"]).fetchone()[0]
    assert len(_rows(con, c)) == n_sql


@pytest.mark.parametrize("q", ["미래에셋이 굴리는 ETF 개수", "미래에셋자산운용이 운용하는 ETF 몇 개?"])
def test_manager_count(index, q):
    plan = _route(index, q)
    assert plan.intent == "company_product_count"
    assert _call(plan, "mgmt_product_count").params["mgmt"] == "미래에셋"


@pytest.mark.parametrize("q, pat", [("레버리지 ETF 몇 개 있어?", "레버리지"), ("반도체 ETF 몇 개", "반도체"),
                                    ("인버스 ETF는 총 몇 개야", "인버스")])
def test_qualifier_count_is_name_count(index, q, pat):
    plan = _route(index, q)
    assert plan.intent == "etp_name_count", plan.intent
    assert _call(plan, "etp_name_search").params["pattern_raw"] == pat


def test_threshold_count_still_generic(index):
    plan = _route(index, "순자산 1조 이상인 ETF 몇 개")
    assert plan.intent == "etp_count" and _call(plan, "etp_count").params.get("min_aum_ge") == 1e12


# ── 5. 낱말: 위험등급 · 순위 ─────────────────────────────────────────────────────────────────

def test_risk_words():
    assert extract_risk_grades("가장 위험한 등급 ETF 목록", POLICY)[:2] == (1, 1)
    assert extract_risk_grades("제일 안전한 ETF 알려줘", POLICY)[:2] == (6, 6)
    assert extract_risk_grades("위험등급 3등급 이하 ETF", POLICY)[:2] == (1, 3)     # 종전 해석 유지


@pytest.mark.parametrize("q", ["가장 위험한 등급 ETF 목록", "가장 안전한 ETF 알려줘"])
def test_risk_word_questions_route_to_risk_filter(index, q):
    plan = _route(index, q)
    assert plan.intent == "etp_filter" and _call(plan, "etp_filter_risk") is not None


def test_order_words_rank_by_aum(index):
    plan = _route(index, "AUM 큰 순으로 국내 ETF 다섯개")
    assert plan.intent == "etp_ranking"
    assert _call(plan, "etp_top_aum").params["limit"] == 5


# ── 6. 지수 추종 · 채권 만기 · 테마 어순 ───────────────────────────────────────────────────────

def test_index_products_filter_active_etf(index):
    plan = _route(index, "S&P500 추종 국내 ETF 뭐 있어")
    assert plan.intent == "index_products"
    calls = [c for c in plan.calls if c.op == "etp_name_search"]
    assert calls and all(c.params.get("status") == "active" and c.params.get("instrument_type") == "ETF" for c in calls)


@pytest.mark.parametrize("q", ["만기 2년 안 남은 국공채 알려줘", "만기가 2년 이내인 국공채 보여줘"])
def test_bond_years_left_with_class(index, con, q):
    plan = _route(index, q)
    c = _call(plan, "bond_maturing_within")
    assert c is not None and c.params.get("bond_class") == "국공채"
    assert c.params["until"] == "2028-09-06"
    rows = _rows(con, c)
    assert rows and all(str(r["MAT_DT"]).replace("-", "") <= "20280906" for r in rows)


def test_theme_alias_word_order(index, con):
    assert "우주항공" in detect_theme_terms("항공우주 테마 ETF 알려줘")
    plan = _route(index, "항공우주 테마 ETF 알려줘")
    c = _call(plan, "etp_name_search")
    assert c.params["pattern_raw"] == "우주항공"
    assert len(_rows(con, c)) >= 3


# ── 7. 펀드 이름 건수 · 분배 지급횟수 · 펀드 설명 ────────────────────────────────────────────────

def test_fund_name_count(index, con):
    plan = _route(index, "TDF 펀드 몇 개 있어?")
    assert plan.intent == "fund_count_filter"
    c = _call(plan, "fund_filter")
    assert c.params.get("name_pattern_raw") == "TDF"
    n_sql = con.execute("SELECT count(*) FROM fund_master WHERE itm_nm ILIKE '%TDF%' OR itm_abrv_nm ILIKE '%TDF%'").fetchone()[0]
    assert len(_rows(con, c)) == n_sql > 0
    assert _route(index, "판매중인 공모펀드 몇 개야").intent == "fund_count_filter"      # 종전 조건 건수 유지
    assert _route(index, "공모펀드 총 몇 개야").intent == "fund_count"                   # 전체 건수 유지


@pytest.mark.parametrize("q", ["분배금 지급 횟수 12회인 ETF", "연 12회 분배하는 ETF 알려줘"])
def test_pay_count_exact(index, con, q):
    plan = _route(index, q)
    c = _call(plan, "etp_by_dividend")
    assert c is not None and c.params.get("min_pay_cnt") == 12 and c.params.get("max_pay_cnt") == 12
    rows = _rows(con, c)
    assert rows and all(int(float(r["pd_dvid_pay_cnt"])) == 12 for r in rows)


@pytest.mark.parametrize("q", ["국민성장 펀드 상품 설명 좀", "국민성장펀드 소개해줘"])
def test_fund_description_words(index, q):
    plan = _route(index, q)
    assert plan.intent == "unstructured_info" and plan.behavior_hint == "partial"


# ── 8. 종목 역질의 우선 · 유일 종목 해석 · 테마×수익률 · 채권형×보수 ──────────────────────────────

@pytest.mark.parametrize("q", ["캠브리콘 담고 있는 ETF 중 중국 반도체 테마인 거", "Cambricon 편입한 중국 반도체 ETF 알려주세요"])
def test_constituent_with_theme_routes_to_holders(index, con, q):
    plan = _route(index, q)
    assert plan.intent == "constituent_reverse", plan.intent
    c = _call(plan, "constituent_holders")
    assert c.params["code"] == "CNE1000041R8" and c.params.get("name_pattern_raw") == "반도체"
    rows = _rows(con, c)
    assert rows and all("반도체" in (r.get("pd_abrv_nm") or r.get("etf_name") or "") for r in rows)


def test_product_fragment_containing_constituent_name_keeps_product_path(index):
    plan = _route(index, "애플 밸류체인 ETF 뭘 담고 있어")
    assert plan.intent == "product_constituents_by_name"                      # M-19 유지


def test_theme_return_rank_and_bond_fee(index, con):
    plan = _route(index, "반도체 ETF 중 1년 수익률 가장 높은 것")
    c = _call(plan, "etp_top_return")
    assert c.params.get("name_pattern_raw") == "반도체"
    rows = _rows(con, c)
    assert rows and all("반도체" in r["pd_abrv_nm"] for r in rows)
    plan = _route(index, "채권형 ETF 중 총보수 가장 낮은 3개")
    c = _call(plan, "etp_low_fee")
    assert c.params.get("name_pattern") == "%채권%"
    rows = _rows(con, c)
    assert rows
    ids = [r["pd_itm_no"] for r in rows]
    n_ok = con.execute("SELECT count(*) FROM kr_etp WHERE pd_itm_no IN (SELECT unnest(?)) "
                       "AND (pd_nm ILIKE '%채권%' OR pd_abrv_nm ILIKE '%채권%')", [ids]).fetchone()[0]
    assert n_ok == len(ids)                                   # 정식명 또는 약칭에 '채권' 표기가 있는 상품만


# ── 9. 구성 비교 — 두 상품 / 상품 vs 지수 추종 (v1 H-17 가족을 결정적으로) ────────────────────────

@pytest.mark.parametrize("q", ["KODEX MSCI KOREA랑 TIGER 200은 구성종목이 어떻게 달라?",
                               "TIGER 200과 KODEX 200 구성 차이 비교해줘"])
def test_two_product_composition_compare(index, con, q):
    plan = _route(index, q)
    assert plan.intent == "constituent_compare" and plan.hints.get("skip_generation") is True
    calls = [c for c in plan.calls if c.op == "constituent_top_weights"]
    assert len(calls) == 2 and len({c.params["etf_id"] for c in calls}) == 2
    for c in calls:
        assert _rows(con, c), c.params
    joined = " ".join(plan.notes)
    assert "구성종목 비중 상위" in joined


@pytest.mark.parametrize("q", ["KODEX MSCI KOREA랑 KOSPI200 추종 ETF는 구성이 어떻게 달라?",
                               "KODEX MSCI KOREA와 KOSPI200 추종 ETF는 구성이 어떻게 다른가요?"])
def test_product_vs_index_composition_compare(index, con, q):
    plan = _route(index, q)
    assert plan.intent == "constituent_compare" and plan.behavior_hint == "partial"
    assert _call(plan, "constituent_top_weights").params["etf_id"] == "KR7156080004"      # KODEX MSCI KOREA
    c = _call(plan, "etp_pattern_top_constituents")
    assert c is not None and c.params["top_etfs"] == 1 and c.params["index_pattern"].startswith("%")
    rows = _rows(con, c)
    assert rows and rows[0]["etf_name"] == "KODEX 200"          # KOSPI 200 표기 ETF 중 순자산 1위(8/22 실측)
    assert any("순자산 1위" in n for n in plan.notes) and any("KODEX MSCI KOREA" in n for n in plan.notes)


def test_single_product_composition_unchanged(index):
    plan = _route(index, "TIGER 200 구성종목 상위 3개 알려줘")
    assert plan.intent == "product_constituents"                                  # M-25 유지


def test_llm_catalog_unchanged():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
