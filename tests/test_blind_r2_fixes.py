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
