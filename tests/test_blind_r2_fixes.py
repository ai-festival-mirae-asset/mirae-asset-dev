# -*- coding: utf-8 -*-
"""8/28 블라인드 2바퀴(r2) 30문항이 드러낸 공백들의 회귀 잠금.

가족별 요약(상세는 evalset/blind_claude_REPORT.md §r2):
거래량·기간수익률 정렬 / 개수 조회의 조건 소실(연도·인버스·클래스) / 낡은 '펀드 총보수 없음'
거절 폐기(보수 분해 4종 신설) / 해외 지역 영문 표기 / 분배 규칙의 편입 문맥 양보·지급월 /
레버리지 배수 / 환헤지 명명 / 통화 분포 표현 / 대출·담보 행위 거절.
"""
import datetime

import duckdb
import pytest

from engine.policy import load_policy
from engine.router import parse_listed_from, route
from engine.sql_templates import TEMPLATES
from engine.validation import gate_router_rule_refusal
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 8, 28)
POLICY = load_policy()


@pytest.fixture(scope="module")
def index():
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    return build_entity_index(con)


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


def _call(plan, template_id):
    return next((c for c in plan.calls if c.op == template_id), None)


def test_volume_rank(index):
    plan = _route(index, "거래량 제일 많은 국내 ETF 뭐야?")
    c = _call(plan, "etp_metric_rank")
    assert c is not None and c.params["metric"] == "volume" and c.params["direction"] == "desc"


def test_loan_request_refused(index):
    plan = _route(index, "이 채권 담보로 대출 되는지 알아봐줘")
    assert plan.behavior_hint == "refuse" and plan.hints.get("unsupported_request") == "loan"
    assert gate_router_rule_refusal(plan).verdict == "refuse"


def test_month_dividend(index):
    plan = _route(index, "7월에 분배금 주는 ETF 있어?")
    c = _call(plan, "etp_by_dividend")
    assert c is not None and c.params.get("month_pattern") == "%July%"


def test_listed_range_with_dividend(index):
    plan = _route(index, "2026년 6월 이후에 상장한 ETF 중에 배당 주는 거 있어?")
    c = _call(plan, "etp_by_dividend")
    assert c is not None and c.params.get("min_listed_dt") == "2026-06-01"


def test_count_this_year_listed(index):
    plan = _route(index, "올해 상장한 국내 ETF 몇 개나 돼?")
    c = _call(plan, "etp_count")
    assert c is not None and c.params.get("min_listed_dt") == "2026-01-01"


def test_parse_listed_from_variants():
    assert parse_listed_from("올해 상장한 ETF", "2026") == "2026-01-01"
    assert parse_listed_from("2026년 6월 이후에 상장한", "2026") == "2026-06-01"
    assert parse_listed_from("3월부터 상장된 상품", "2026") == "2026-03-01"
    assert parse_listed_from("상장 상품 알려줘", "2026") is None


def test_period_return_3m(index):
    plan = _route(index, "3개월 수익률 기준으로 국내 ETF 상위 5개 알려줘")
    c = _call(plan, "etp_top_return")
    assert c is not None and c.params["metric"] == "3m"


def test_leverage_factor(index):
    plan = _route(index, "3배 레버리지 ETF도 있어?")
    c = _call(plan, "etp_filter_leverage")
    assert c is not None and c.params["factor"] == 3.0


def test_global_region_english_and_etn(index):
    plan = _route(index, "미국에 투자하는 해외 상품 중에 ETN인 것도 있어?")
    calls = [c for c in plan.calls if c.op == "global_etf_filter"]
    assert calls and any("United States" in str(c.params.get("region_pattern_raw", "")) for c in calls)
    assert all(c.params.get("etn_only") == "Y" for c in calls)


def test_global_inverse_count(index):
    plan = _route(index, "인버스 해외 ETF는 몇 개야?")
    c = _call(plan, "global_etf_count")
    assert c is not None and c.params.get("inverse_only") == "Y"


def test_fund_total_fee_now_answered(index):
    # 구본 기준 '총보수 없음' 함정(v1 T-14)이 재배포본 보수 분해 신설로 정상 질의로 전환
    plan = _route(index, "공모펀드 중에서 총보수 제일 낮은 것 알려줘")
    assert plan.behavior_hint != "refuse"
    c = _call(plan, "fund_by_fee")
    assert c is not None and c.params["order"] == "total_asc"


def test_fund_fee_top_aum(index):
    plan = _route(index, "판매 중인 채권혼합형 펀드 중 순자산 상위 3개의 보수 알려줘")
    c = _call(plan, "fund_filter")
    assert c is not None and c.params.get("btyp_pattern") == "%채권혼합형%"
    assert c.params.get("on_sale_only") == "Y" and c.params.get("order") == "aum"
    assert "sale_co_rwrd_r" in TEMPLATES["fund_filter"].sql


def test_hedged_search(index):
    plan = _route(index, "환헤지된 미국 지수 추종 ETF 알려줘")
    c = _call(plan, "etp_name_search")
    assert c is not None and c.params.get("pattern_raw") == "(H)"
    assert c.params.get("pattern2_raw") == "미국"


def test_currency_dist_without_etf_word(index):
    plan = _route(index, "원화 말고 다른 통화로 거래되는 국내 상장 상품 있어?")
    assert _call(plan, "etp_currency_dist") is not None


def test_class_count_included(index):
    plan = _route(index, "온라인 전용으로 가입할 수 있는 펀드 클래스 얼마나 돼?")
    assert _call(plan, "fund_class_count") is not None


def test_dividend_yields_to_constituent_context(index):
    # 편입 문맥에서는 분배 목록 규칙이 가로채지 않는다(R2-19)
    plan = _route(index, "포스코퓨처엠 담은 ETF 중에 순자산 1위 상품의 분배수익률도 같이 알려줘")
    assert _call(plan, "etp_by_dividend") is None
    assert _call(plan, "constituent_holders") is not None
    assert "pd_dvid_yield" in TEMPLATES["constituent_holders"].sql


# ---------------------------------------------------------------------------
# 8/28 사용자 실측: 국내 종목의 로마자 등록명(NAVER 등)을 한글로 물으면 미인식 → 별칭 사전 보강
# ---------------------------------------------------------------------------

def test_hangul_alias_naver_equals_latin(index):
    a = _route(index, "네이버가 포함된 ETF를 알려줘")
    b = _route(index, "naver가 포함된 ETF를 알려줘")
    ca = _call(a, "constituent_holders")
    cb = _call(b, "constituent_holders")
    assert ca is not None and cb is not None
    assert ca.params["code"] == cb.params["code"] == "035420"


def test_hangul_alias_nc_and_soil(index):
    plan = _route(index, "엔씨소프트 들어간 ETF 있어?")
    c = _call(plan, "constituent_holders")
    assert c is not None and c.params["code"] == "036570"
    plan = _route(index, "에쓰오일 편입한 ETF 뭐야?")
    c = _call(plan, "constituent_holders")
    assert c is not None and c.params["code"] == "010950"


def test_hangul_alias_no_false_product_grab(index):
    # '엘지' 별칭이 'LG에너지솔루션' 같은 다른 이름을 가로채면 안 된다(경계 규칙 유지)
    plan = _route(index, "LG에너지솔루션 들어있는 ETF 알려줘")
    c = _call(plan, "constituent_holders")
    assert c is not None and c.params["code"] != "003550"


# ---------------------------------------------------------------------------
# 8/28 r3(사용자 실측 + 3바퀴) 회귀 잠금
# ---------------------------------------------------------------------------

def test_maturity_window_with_coupon_rank(index):
    # 사용자 실측: '잔존만기 3년 이내 중 표면 금리 가장 높은' — 구간+금리 정렬(30년물 오답 수정)
    plan = _route(index, "잔존만기 3년 이내 중 표면 금리 가장 높은 회사채 알려줘")
    c = _call(plan, "bond_maturing_within")
    assert c is not None and c.params.get("order") == "coupon"
    assert plan.hints.get("skip_generation")


def test_longest_maturity_rule_kept(index):
    plan = _route(index, "잔존만기가 가장 긴 국고채 5개 알려줘")
    assert _call(plan, "bond_top_maturity") is not None


def test_issue_year_count(index):
    plan = _route(index, "2026년에 발행된 회사채가 몇 개야?")
    c = _call(plan, "bond_count")
    assert c is not None and c.params.get("min_issue_dt") == "2026-01-01"


def test_nav_and_value_rank(index):
    plan = _route(index, "기준가(NAV)가 가장 높은 국내 ETF 뭐야?")
    c = _call(plan, "etp_metric_rank")
    assert c is not None and c.params["metric"] == "nav"
    plan = _route(index, "거래대금이 제일 큰 국내 ETF 뭐야?")
    c = _call(plan, "etp_metric_rank")
    assert c is not None and c.params["metric"] == "value"


def test_eur_currency_global(index):
    plan = _route(index, "유로화로 거래되는 해외 ETF도 있어?")
    c = _call(plan, "global_etf_filter")
    assert c is not None and c.params.get("ccy") == "EUR"


def test_grade_wise_counts(index):
    plan = _route(index, "위험등급별로 상품이 각각 몇 개씩 있는지 알려줘")
    assert _call(plan, "risk_grade_product_counts") is not None


def test_class_dictionary_wording(index):
    plan = _route(index, "펀드 A클래스랑 C클래스 차이가 뭐야?")
    assert any(c.op == "fund_class_dictionary" for c in plan.calls)


def test_missing_benchmark(index):
    plan = _route(index, "벤치마크가 아예 없는 펀드도 있어?")
    assert _call(plan, "fund_missing_bmrk") is not None


def test_count_with_risk_grade(index):
    plan = _route(index, "순자산 1조 넘는 ETF 중에 위험등급 2등급인 건 몇 개야?")
    c = _call(plan, "etp_count")
    assert c is not None and c.params.get("min_grade") == 2 and c.params.get("max_grade") == 2


def test_index_fee_filter(index):
    plan = _route(index, "코스피200 추종 ETF 중에 총보수가 제일 싼 거 뭐야?")
    c = _call(plan, "etp_low_fee")
    assert c is not None and c.params.get("name_pattern")


def test_portfolio_delegation_refused(index):
    plan = _route(index, "KODEX 200이랑 똑같이 포트폴리오 만들어서 운용해줘")
    assert plan.behavior_hint == "refuse" and plan.hints.get("unsupported_request")
    plan = _route(index, "미래에셋자산운용이 운용하는 ETF 알려줘")   # 정상 '운용' 표현은 그대로
    assert plan.behavior_hint != "refuse"


def test_past_date_price_refused(index):
    plan = _route(index, "작년 12월 31일 KODEX 200 종가 알려줘")
    assert plan.behavior_hint == "refuse" and plan.hints.get("time_violation")


# ---------------------------------------------------------------------------
# 8/28~29 r4 회귀 잠금 — 함정 4종·새 축(듀레이션/시장/연금/음수/자산유형/ISIN/꼴찌/평균/최고최저)
# ---------------------------------------------------------------------------

def test_r4_traps_refused(index):
    for q in ["이 중에 원금이 보장되는 상품 골라줘",
              "KODEX 200 팔면 양도소득세 얼마 나와? 계산해줘",
              "어제 산 KODEX 200 환불해줘",
              "너희 회사가 미는 추천 상품이 뭐야?"]:
        plan = _route(index, q)
        assert plan.behavior_hint == "refuse" and plan.hints.get("unsupported_request"), q


def test_r4_tax_question_not_over_refused(index):
    # 과세 '기준' 질문(계산 아님)은 거절 대상이 아니다
    plan = _route(index, "ETF 분배금에 세금은 어떻게 매겨져?")
    assert plan.behavior_hint != "refuse"


def test_duration_rank_and_threshold(index):
    plan = _route(index, "듀레이션이 제일 짧은 채권 5개만 알려줘")
    c = _call(plan, "bond_filter")
    assert c is not None and c.params.get("order") == "dur_asc"
    plan = _route(index, "듀레이션이 5년 넘는 국공채 중에 표면금리 높은 순 3개 알려줘")
    c = _call(plan, "bond_filter")
    assert c is not None and c.params.get("min_dur") == 5.0 and c.params.get("order") == "coupon"


def test_market_listing_and_pension(index):
    plan = _route(index, "코스닥 시장에 상장된 ETN도 있어?")
    assert _call(plan, "etp_market_dist") is not None
    plan = _route(index, "퇴직연금 계좌로 살 수 있는 ETF도 있어?")
    assert _call(plan, "etp_filter_pension") is not None


def test_negative_metric_and_average(index):
    plan = _route(index, "괴리율이 마이너스인 ETF도 있어?")
    c = _call(plan, "etp_metric_rank")
    assert c is not None and c.params.get("max_metric") == 0
    plan = _route(index, "코스피200을 추종하는 상품들의 평균 추적오차가 얼마야?")
    assert _call(plan, "etp_metric_avg") is not None


def test_global_asset_type_but_not_fund(index):
    plan = _route(index, "해외 ETF 중에 채권에 투자하는 상품 알려줘")
    c = _call(plan, "global_etf_filter")
    assert c is not None and c.params.get("ast_type") == "Bond"
    # 펀드 문맥은 가로채지 않는다(자산구성 규칙 소관)
    plan = _route(index, "채권형 펀드인데 해외 채권 비중이 50% 넘는 상품 있나요?")
    assert _call(plan, "fund_by_composition") is not None


def test_isin_lookup(index):
    plan = _route(index, "ISIN이 KR7069500007인 상품이 뭐야?")
    assert plan.intent == "code_lookup"


def test_bottom_aum_and_minmax(index):
    plan = _route(index, "국내 ETF 중에 순자산이 제일 작은 상품 5개는 뭐야?")
    c = _call(plan, "etp_top_aum")
    assert c is not None and c.params.get("order") == "asc"
    plan = _route(index, "채권형 펀드 중에 1년 수익률 최고랑 최저를 같이 알려줘")
    calls = [c for c in plan.calls if c.op == "fund_top_return_1y"]
    assert len(calls) == 2 and any(c.params.get("order") == "asc" for c in calls)
    assert all(c.params.get("btyp_pattern") == "%채권형%" for c in calls)


def test_company_made_count(index):
    plan = _route(index, "삼성에서 나온 ETN은 몇 개야?")
    c = _call(plan, "mgmt_product_count")
    assert c is not None and c.params.get("mgmt") == "삼성"
