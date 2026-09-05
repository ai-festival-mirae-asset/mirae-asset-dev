"""조건 누락을 다른 표현과 독립 SQL 결과로 확인한다."""
import datetime

import duckdb
import pytest

from engine.answer_service import _draft_answer, _focus_cols
from engine.channels import RuntimeContext, execute_plan, resolve_raw_params
from engine.policy import load_policy
from engine.router import extract_top_n, route, extract_percents
from engine.sql_templates import run_template
from engine.validation import gate_existence
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)
FEE = " + ".join(f"coalesce(try_cast({c} as double),0)" for c in
                 ("sale_co_rwrd_r", "or_co_rwrd_r", "trusc_rwrd_r", "ofwk_trus_rwrd_r"))


@pytest.fixture(scope="module")
def con():
    with duckdb.connect(DB_PATH_DEFAULT, read_only=True) as c:
        yield c


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


def call(index, question, op):
    plan = route(question, index, today=TODAY)
    return plan, next(c for c in plan.calls if c.op == op)


@pytest.mark.parametrize("question", ["3% 이자율인 채권 5개", "3%의 표면금리인 채권 다섯 개", "이자율이 3%인 채권 보여줘"])
def test_number_before_coupon(index, con, question):
    _, c = call(index, question, "bond_filter")
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    assert rows and all(float(r["SRFC_IRT"]) == 3 for r in rows)


def test_adjacent_percent_conditions_stay_separate():
    assert extract_percents("보수 0.3% 이하, 수익률 5% 이상") == [(0.3, "fee", "이하"), (5, "return", "이상")]


@pytest.mark.parametrize("question,n", [
    ("1개월 수익률 상위 3개", 3), ("국내ETF3개월변동성낮은순5개", 5),
    ("6개월 변동성 20% 이상 4개", 4), ("순자산 큰 거 하나만!", 1),
    ("해외 ETF 한 개 부탁해", 1), ("채권 다섯 개 보여줘", 5),
    ("하나은행 채권", None), ("두어 개 보여줘", None), ("3개월 변동성", None),
])
def test_count_words_and_months(question, n):
    assert extract_top_n(question) == n


@pytest.mark.parametrize("question,op", [
    ("국내 ETF 순자산 제일 큰 거 하나만!", "etp_top_aum"),
    ("해외 ETF 총보수 낮은 상품 한 개 부탁해", "global_etf_filter"),
    ("온라인 수수료미징구 펀드 한 개 보여줘", "fund_class_by_fee"),
])
def test_explicit_one_is_displayed_once(index, con, question, op):
    plan, _ = call(index, question, op)
    assert plan.hints["display_rows"] == 1
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    answer = _draft_answer(plan, result, question)
    assert "\n  2. " not in answer


@pytest.mark.parametrize("months", [1, 3, 6])
def test_volatility_threshold_uses_requested_period(con, months):
    params = {"type":"ETF", "metric":f"vol_{months}m", "direction":"asc", "limit":2000,
              "min_metric":3, "max_metric":10}
    rows = run_template(con, "etp_metric_rank", params).rows
    expected = con.execute(f"SELECT pd_itm_no FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND try_cast(du_vlty_{months}m as double)>3 AND try_cast(du_vlty_{months}m as double)<10 ORDER BY try_cast(du_vlty_{months}m as double),pd_itm_no").fetchall()
    assert [r["pd_itm_no"] for r in rows] == [r[0] for r in expected]


@pytest.mark.parametrize("question,months", [("1개월 변동성 낮은 ETF",1), ("3개월변동성이 낮은 ETF",3), ("ETF 변동성 6개월 기준",6)])
def test_volatility_display_period(question, months):
    assert f"du_vlty_{months}m" in _focus_cols(question)
    assert "du_vlty_1y" not in _focus_cols(question)


@pytest.mark.parametrize("question", [
    "3개월 변동성 15% 미만이고 순자산 1000억원 이상인 ETF 3개 낮은 순",
    "ETF 순자산 1000억 이상 중 3개월 변동성 낮은 순으로 3개, 15% 미만",
    "국내 ETF 3개월변동성 15% 미만, 순자산 0.1조원 이상 상위 3개",
])
def test_metric_and_aum_conditions(index, con, question):
    _, c = call(index, question, "etp_metric_rank")
    assert c.params["min_aum_ge"] == 1e11
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    assert rows
    ids = [r["pd_itm_no"] for r in rows]
    assert all(con.execute("SELECT try_cast(pd_net_tamt as double)>=100000000000 FROM kr_etp WHERE pd_itm_no=?",[i]).fetchone()[0] for i in ids)


@pytest.mark.parametrize("question", ["판매 중인 공모펀드 총보수 낮은 순 5개", "공모펀드 중 판매중이고 보수가 저렴한 5개", "총보수 최저 공모펀드 5개, 판매 중인 상품만"])
def test_public_fund_sale_and_fee(index, con, question):
    _, c = call(index, question, "fund_by_fee")
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    expected = con.execute(f"SELECT itm_no FROM fund_master WHERE sale_yn='판매중' AND prvo_pbff_desc='공모' AND ({FEE})>0 ORDER BY round(({FEE}),4),itm_no LIMIT ?",[len(rows)]).fetchall()
    assert rows and [r["itm_no"] for r in rows] == [r[0] for r in expected]


@pytest.mark.parametrize("question", ["공모펀드 총보수 0.5% 이하, 위험등급 5등급", "위험등급 5등급인 공모펀드 중 총보수 0.5% 이하", "총보수 0.5% 이하 공모펀드의 이름과 총보수만 보여줘, 위험등급 5등급"])
def test_fund_risk_and_fee(index, con, question):
    _, c = call(index, question, "fund_by_fee")
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    assert len(rows) == 1
    assert all(r["drv_risk_grade"] == "5" and r["prvo_pbff_desc"] == "공모" for r in rows)


@pytest.mark.parametrize("question", ["온라인 수수료미징구 펀드 위험등급 4등급 총보수 낮은 순 3개", "총보수가 낮은 온라인 펀드 3개, 수수료미징구이고 4등급", "수수료 없는 온라인 펀드 중 4등급을 총보수 낮은 순서로 3개"])
def test_class_fee_sort_keeps_conditions(index, con, question):
    _, c = call(index, question, "fund_by_fee")
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    expected = con.execute(f"SELECT itm_no FROM fund_class WHERE han_clas_sales_channel='온라인' AND han_clas_fee_type='수수료미징구' AND drv_risk_grade='4' AND ({FEE})>0 ORDER BY round(({FEE}),4),itm_no LIMIT ?",[len(rows)]).fetchall()
    assert rows and [r["itm_no"] for r in rows] == [r[0] for r in expected]


@pytest.mark.parametrize("question", ["유럽 채권형 해외 ETF 총보수 0.3% 이하 순자산 1억달러 이상 3개", "해외 ETF 중 유럽 채권형, 순자산 1억 달러 이상에 총보수 0.3% 이하", "총보수 0.3% 이하인 유럽 채권형 해외 ETF 중 순자산 0.0001조달러 이상 3개"])
def test_global_combined_bounds(index, con, question):
    _, c = call(index, question, "global_etf_filter")
    assert c.params["min_aum_ge"] == 1e8 and c.params["max_fee_le"] == 0.3
    assert run_template(con, c.op, resolve_raw_params(c.params)).rows == []


def test_empty_sql_result_still_has_source_evidence(con):
    result = run_template(con, "global_etf_filter", {"ast_type": "Bond", "region_pattern": "%Europe%",
                                                       "min_aum_ge": 1e8, "max_fee_le": 0.3, "limit": 10})
    assert result.rows == []
    assert result.evidences and result.evidences[0].source == "PREF02N001"


@pytest.mark.parametrize("question", ["애플 편입 ETF 총보수 0.5% 이하 순자산 1위", "엔비디아 담은 ETF 중 보수 0.4% 미만 순자산 상위 3개", "네이버 포함 ETF 총보수 0.3% 이하 규모 큰 순 2개"])
def test_holder_filter_keeps_ranking_and_drops_unfiltered_graph(index, question):
    plan, c = call(index, question, "constituent_holders")
    assert c.params["order"] == "aum" and "max_fee" in c.params
    assert not any(c.op == "holding_etfs" for c in plan.calls)


@pytest.mark.parametrize("question", ["1개월 수익률 5% 넘는 ETF 상위 3개 종가도", "3개월 수익률 2% 이상 ETF 상위 2개 가격 함께", "6개월 수익률 1% 이상 ETF 4개의 종가 알려줘"])
def test_return_rank_can_show_price(index, con, question):
    _, c = call(index, question, "etp_top_return")
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    assert rows and all("du_clpr" in r for r in rows)


@pytest.mark.parametrize("question", ["AA급 회사채 잔존만기 2년 이하 표면금리 높은 순 3종목 듀레이션", "잔존만기 1년 이내 회사채 금리 상위 2개 듀레이션도", "채권 잔존만기 3년 이하 중 금리 높은 4개와 듀레이션"])
def test_maturity_filter_can_show_duration(index, con, question):
    _, c = call(index, question, "bond_maturing_within")
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    assert rows and all("DUR" in r for r in rows)


# 9/6 리더 세션 정정([구역 침범] 구성종목 구역): 부분 일치 토큰이 여러 종목에 걸리면 후보 안내(종전대로),
# 한 종목으로만 이어지면('ACCENTURE' → ACCENTURE PLC-CL A 하나) 그 종목의 편입 역질의로 답한다 — PLAN §5 9/6.
@pytest.mark.parametrize("question", ["MOTOR 담은 ETF 보여줘", "MOTOR를 편입한 ETF 찾아줘"])
def test_partial_constituent_name_only_offers_candidates(index, con, question):
    plan, _ = call(index, question, "lookup")
    assert plan.intent == "constituent_name_candidates" and plan.behavior_hint == "partial"
    assert all(c.channel == "keyword" for c in plan.calls)
    answer = _draft_answer(plan, execute_plan(plan, RuntimeContext(con=con,index=index)), question)
    assert "MOTOR" in answer.upper() and "존재 근거 아님" in answer


@pytest.mark.parametrize("question", ["ACCENTURE 들어 있는 ETF 순자산 상위 3개", "ACCENTURE를 편입한 ETF 찾아줘", "ACCENTURE 담은 ETF 보여줘"])
def test_unique_partial_constituent_name_resolves_to_holders(index, con, question):
    plan, c = call(index, question, "constituent_holders")
    assert plan.intent == "constituent_reverse"
    assert c.params["code"] == "IE00B4BNMY34"
    rows = run_template(con, c.op, resolve_raw_params(c.params)).rows
    assert rows and all("ACCENTURE" in str(r.get("COMPST_ISU_NM", r)).upper() for r in rows)
    assert any("부분 일치" in n for n in plan.notes)


@pytest.mark.parametrize("question", ["무지개바다여행 2041 ETF 상품 정보 알려줘", "초록구름정거장 2043 ETN 상세 알려줘", "보랏빛종이우주 2047 펀드 정보 부탁해"])
def test_unknown_numbered_product_has_absence_evidence(index, question):
    gate, _ = gate_existence(question,index,load_policy())
    assert gate.verdict == "refuse" and "기준일 상품 목록에 없음" in gate.reason


@pytest.mark.parametrize("question", ["2024년 이후 상장한 ETF 상품 정보", "국내 ETF 순자산 2042억원 이상 상품 정보", "TIGER 200 ETF 상품 정보"])
def test_numbered_product_check_does_not_reject_filters(index, question):
    gate, _ = gate_existence(question,index,load_policy())
    assert gate.verdict != "refuse"


@pytest.mark.parametrize("question", [
    "다음 달 가장 많이 오를 ETF 하나 콕 찍어 줘",
    "앞으로 상승할 펀드 하나 추천해 주세요",
    "내일 수익률이 제일 좋을 ETF를 골라줘",
])
def test_future_forecast_variants_are_refused(index, question):
    plan = route(question, index, today=TODAY)
    assert plan.intent == "time_violation"
    assert plan.behavior_hint == "refuse"
    assert plan.hints["time_violation"] == "future_forecast"
