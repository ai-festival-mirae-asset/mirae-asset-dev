# -*- coding: utf-8 -*-
"""8/28 블라인드 출제(claude) 20문항이 드러낸 5가지 시스템 공백의 회귀 잠금.

1. B-04/12/16: 재배포 신설 수치(괴리율·추적오차·변동성) 정렬 질의 → etp_metric_rank
2. B-01: 퇴직연금 조건이 채권 카운트·목록에서 빠지던 것 → pension_only
3. B-11: 클래스 수수료 유형·판매채널 필터 부재 → fund_class_by_fee
4. B-14: '…담은 ETF 중 수익률 1등' 이 비중순 목록으로 새던 것 → constituent_holders_top_return
5. B-15: 펀드 목록에 분배율 열이 없어 '자료 없음' 오단정 → fund_filter 에 fd_last_dstb_r

각 수정은 같은 뜻 다른 표현 2~3개로 시험한다(TEAM_IMPROVEMENT_GUIDE §5 수정 원칙 2).
"""
import datetime

import duckdb
import pytest

from engine.policy import load_policy
from engine.router import route
from engine.sql_templates import TEMPLATES, validate_params
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


# ---------------------------------------------------------------------------
# 1. 수치 항목 순위 — etp_metric_rank
# ---------------------------------------------------------------------------

def test_tracking_error_lowest_etf(index):
    plan = _route(index, "추적오차가 가장 낮은 국내 ETF 5개 알려줘")
    c = _call(plan, "etp_metric_rank")
    assert c is not None
    assert c.params["metric"] == "tracking" and c.params["direction"] == "asc"
    assert c.params.get("type") == "ETF"


def test_volatility_highest_etn(index):
    plan = _route(index, "변동성 제일 높은 ETN 뭐야?")
    c = _call(plan, "etp_metric_rank")
    assert c is not None
    assert c.params["metric"] == "vol_1y" and c.params["direction"] == "desc"
    assert c.params.get("type") == "ETN"


def test_divergence_desc_all_etp(index):
    plan = _route(index, "괴리율 큰 순서로 상장 상품 보여줘")
    c = _call(plan, "etp_metric_rank")
    assert c is not None
    assert c.params["metric"] == "diff" and c.params["direction"] == "desc"
    assert "type" not in c.params                       # 유형 미지정 — 전체 ETP + 혼재 노트
    assert any("괴리율" in n and "부호" in n for n in plan.notes)


def test_volatility_period_3m(index):
    plan = _route(index, "3개월 변동성 낮은 순으로 ETF 뽑아줘")
    c = _call(plan, "etp_metric_rank")
    assert c is not None
    assert c.params["metric"] == "vol_3m" and c.params["direction"] == "asc"


def test_single_product_tracking_error_still_detail(index):
    # 상품 1종의 추적오차 '값' 질문은 순위 규칙이 가로채면 안 된다(상세 조회 유지)
    plan = _route(index, "ACE 미국나스닥100 추적오차 어느정도임?")
    assert _call(plan, "etp_metric_rank") is None


# ---------------------------------------------------------------------------
# 2. 퇴직연금 조건 — bond_filter/bond_count
# ---------------------------------------------------------------------------

def test_pension_bond_count(index):
    plan = _route(index, "퇴직연금에 넣을 수 있는 채권 중에 신용등급 AAA인 거 몇 개나 돼?")
    c = _call(plan, "bond_count")
    assert c is not None and c.params.get("pension_only") == "Y"


def test_pension_bond_list(index):
    plan = _route(index, "퇴직연금 편입 가능한 국공채 알려줘")
    c = _call(plan, "bond_filter")
    assert c is not None and c.params.get("pension_only") == "Y"
    assert c.params.get("bond_class") == "국공채"


def test_bond_templates_accept_pension_param():
    validate_params("bond_filter", {"pension_only": "Y", "limit": 5})
    validate_params("bond_count", {"pension_only": "Y"})
    assert "PD_PEN_TR_YN" in TEMPLATES["bond_filter"].sql
    assert "PD_PEN_TR_YN" in TEMPLATES["bond_count"].sql


# ---------------------------------------------------------------------------
# 3. 클래스 수수료·채널 — fund_class_by_fee
# ---------------------------------------------------------------------------

def test_fee_free_index_fund_class(index):
    plan = _route(index, "판매수수료 없는 클래스로 가입할 수 있는 인덱스펀드 알려줘")
    c = _call(plan, "fund_class_by_fee")
    assert c is not None
    assert c.params["fee_type"] == "수수료미징구"
    assert c.params.get("strategy_pattern") == "%인덱스%"
    assert c.params.get("on_sale_only") == "Y"          # '가입' 관점


def test_fee_free_variants(index):
    plan = _route(index, "수수료 안 떼는 펀드 클래스 있어?")
    c = _call(plan, "fund_class_by_fee")
    assert c is not None and c.params["fee_type"] == "수수료미징구"


def test_online_channel_class(index):
    plan = _route(index, "온라인으로 가입할 수 있는 인덱스 펀드 클래스 알려줘")
    c = _call(plan, "fund_class_by_fee")
    assert c is not None and c.params.get("channel_pattern") == "%온라인%"


# ---------------------------------------------------------------------------
# 4. 편입 ETF 수익률 순위 — constituent_holders_top_return
# ---------------------------------------------------------------------------

def test_holder_top_return_first_place(index):
    plan = _route(index, "LG에너지솔루션 담고있는 ETF들중 1년수익률 1등이 뭐고 그 상품 위험등급도 같이 알려줘")
    assert _call(plan, "constituent_holders_top_return") is not None
    assert plan.hints.get("order") == "return"


def test_holder_top_return_wording_variant(index):
    plan = _route(index, "포스코퓨처엠 편입한 ETF 중에 수익률 가장 높은 상품 알려줘")
    assert _call(plan, "constituent_holders_top_return") is not None


def test_holder_default_still_weight_order(index):
    # 수익률 순위 표현이 없으면 기존(비중 중심) 목록 경로 유지
    plan = _route(index, "LG에너지솔루션 들어있는 ETF 알려줘")
    assert _call(plan, "constituent_holders_top_return") is None


def test_top_return_template_has_risk_grade():
    assert "drv_risk_grade" in TEMPLATES["constituent_holders_top_return"].sql


# ---------------------------------------------------------------------------
# 5. 펀드 목록의 분배율 열
# ---------------------------------------------------------------------------

def test_fund_filter_selects_distribution_rate():
    assert "fd_last_dstb_r" in TEMPLATES["fund_filter"].sql


# ---------------------------------------------------------------------------
# 6. 자산구성 비율 문턱값 — fund_by_composition (B-09: HCX 추측 오답 차단)
# ---------------------------------------------------------------------------

def test_composition_ovrs_bond_over(index):
    plan = _route(index, "채권형 펀드인데 해외 채권 비중이 50% 넘는 상품 있나요?")
    c = _call(plan, "fund_by_composition")
    assert c is not None
    assert c.params["field"] == "ovrs_bd" and c.params["min_rt"] == 50.0
    assert c.params.get("strict") == "Y"                 # '넘는' = 초과
    assert c.params.get("btyp_pattern") == "%채권%"
    assert plan.hints.get("skip_generation")             # 결정적 답변 — 생성 추측 여지 제거


def test_composition_ovrs_stock_atleast(index):
    plan = _route(index, "해외 주식 비율 70% 이상인 펀드 알려줘")
    c = _call(plan, "fund_by_composition")
    assert c is not None
    assert c.params["field"] == "ovrs_stk" and c.params["min_rt"] == 70.0
    assert "strict" not in c.params                      # '이상' = 포함


def test_composition_dmst_stock_variant(index):
    plan = _route(index, "국내 주식 구성비 80% 초과하는 주식형 펀드 있어?")
    c = _call(plan, "fund_by_composition")
    assert c is not None
    assert c.params["field"] == "dmst_stk" and c.params.get("strict") == "Y"


# ---------------------------------------------------------------------------
# 7. 8/28 회귀 2건 잠금 — 표현 운 제거 (V3-H-01 · V3-T-09)
# ---------------------------------------------------------------------------

def test_plain_intersection_is_rule_based(index):
    # 순위 낱말이 없어도 'A랑 B 둘 다 담은'은 규칙(결정적)으로 — LLM 분류 흔들림에 안 맡긴다
    plan = _route(index, "NAVER랑 카카오 둘 다 담고 있는 ETF 알려줘")
    assert plan.stage == "rule"
    assert _call(plan, "constituent_intersection_top_aum") is not None


def test_plain_intersection_variant(index):
    plan = _route(index, "삼성전자하고 SK하이닉스 모두 편입한 ETF 있어?")
    assert _call(plan, "constituent_intersection_top_aum") is not None


def test_time_violation_hint_promotes_refusal(index):
    from engine.validation import gate_router_rule_refusal
    plan = _route(index, "다음 주에 상장하는 국내 ETF 뭐야?")
    assert plan.behavior_hint == "refuse" and plan.hints.get("time_violation")
    assert gate_router_rule_refusal(plan).verdict == "refuse"


def test_free_refusal_regex_covers_new_wordings():
    from engine.answer_service import _looks_like_free_refusal
    assert _looks_like_free_refusal("다음 주에 상장하는 ETF에 대해 확인해 드릴 수 없습니다.")
    assert _looks_like_free_refusal("질문하신 내용에 대한 답변을 드리기 어렵습니다.")
