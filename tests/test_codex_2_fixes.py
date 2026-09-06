"""표현을 바꿔도 원천 조건과 거절 경계가 유지되는지 확인한다."""
import datetime
import json
from pathlib import Path

import duckdb
import pytest

from engine.answer_service import _draft_answer
from engine.channels import RuntimeContext, execute_plan, resolve_raw_params
from engine.router import extract_top_n, route
from engine.sql_templates import run_template
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)


@pytest.fixture(scope="module")
def con():
    with duckdb.connect(DB_PATH_DEFAULT, read_only=True) as c:
        yield c


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


def query(index, con, question, op):
    plan = route(question, index, today=TODAY)
    call = next(c for c in plan.calls if c.op == op)
    rows = run_template(con, op, resolve_raw_params(call.params)).rows
    return plan, rows


@pytest.mark.parametrize("question", ["국내ETF순자산TOP3좀알려줘", "국내 ETF 순자산 top3 보여주세요", "국내ETF순자산Top3 알려줘"])
def test_attached_top_is_ranking(index, con, question):
    plan, rows = query(index, con, question, "etp_top_aum")
    gold = con.execute("SELECT pd_itm_no FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND try_cast(pd_net_tamt AS DOUBLE)>0 ORDER BY try_cast(pd_net_tamt AS DOUBLE) DESC,pd_itm_no LIMIT 3").fetchall()
    assert [r["pd_itm_no"] for r in rows[:3]] == [r[0] for r in gold]
    assert plan.behavior_hint != "refuse"


@pytest.mark.parametrize("word", ["두어 개", "서너 개", "몇 개"])
def test_vague_count_is_not_an_exact_number(word):
    assert extract_top_n(f"채권 {word} 소개해줘") is None


@pytest.mark.parametrize("question", ["만기 짧은 채권 서너 개만 보여줘", "만기가 가까운 채권 몇 개 보여주세요", "만기 빠른 채권을 소개해줘"])
def test_short_maturity_uses_master_active_rows(index, con, question):
    plan, rows = query(index, con, question, "bond_filter")
    gold = con.execute("SELECT replace(MAT_DT,'-','') FROM kr_bond WHERE drv_maturity_status='active' ORDER BY replace(MAT_DT,'-','') LIMIT ?", [len(rows)]).fetchall()
    assert rows and [r["MAT_DT"].replace("-", "") for r in rows] == [r[0] for r in gold]
    assert plan.hints["skip_generation"]


@pytest.mark.parametrize("question", ["분배금을 1년에 12번 주는 국내 ETF가 궁금해요", "연간 12회 분배 ETF 보여줘", "국내ETF연12회분배금 알려줘"])
def test_distribution_count(index, con, question):
    _, rows = query(index, con, question, "etp_by_dividend")
    assert rows and all(float(r["pd_dvid_pay_cnt"]) == 12 for r in rows)


@pytest.mark.parametrize("question", ["4월에 분배하고 연간 분배금 500원 이상인 국내 ETF 좀요", "국내 ETF 4월 지급 분배금 500원 이상 보여줘", "4월 분배금이 500원 이상인 ETF 목록"])
def test_distribution_month_and_amount(index, con, question):
    _, rows = query(index, con, question, "etp_by_dividend")
    assert rows and all("April" in r["pd_dvid_pay_months"] and float(r["pd_divd_amt_ann"])>=500 for r in rows)


@pytest.mark.parametrize("question", ["1월 지급 연간 4회 분배금 200원 이상 ETF 세 개", "국내ETF 1월 분배 연4회 분배금 200원 이상 알려줘", "연간 분배금 200원 이상이고 연간 4회 분배하는 1월 지급 ETF"])
def test_distribution_three_conditions(index, con, question):
    _, rows = query(index, con, question, "etp_by_dividend")
    assert rows and all("January" in r["pd_dvid_pay_months"] and float(r["pd_dvid_pay_cnt"])==4 and float(r["pd_divd_amt_ann"])>=200 for r in rows)


@pytest.mark.parametrize("question", ["해외 ETF 중 유럽 채권형 fee 0.1% 이하만 보여주세요", "해외 ETF 유럽 채권형 FEE 0.1% 이하 알려줘", "유럽 채권형 해외 ETF 보수 0.1% 이하 목록"])
def test_english_fee_does_not_drop_bound(index, con, question):
    plan, rows = query(index, con, question, "global_etf_filter")
    gold = con.execute("SELECT pd_itm_no FROM global_etf WHERE drv_instrument_type='ETF' AND wu_inv_rgn='Europe' AND wu_inv_ast_type='Bond' AND try_cast(cu_charge_rt AS DOUBLE)>0 AND try_cast(cu_charge_rt AS DOUBLE)<=0.1").fetchall()
    assert gold == [] and rows == []
    assert plan.behavior_hint != "refuse"


@pytest.mark.parametrize("question", ["판매중인 공모펀드 몇 개 소개해줘", "판매 중 공모펀드 몇 개 보여주세요", "판매중 공모펀드 몇 개 골라줘",
                                      "판매 중 공모펀드 몇개만 소개해 주세요", "판매중인 공모펀드 중 몇 개를 소개 부탁해요"])
def test_public_fund_examples_not_total_count(index, con, question):
    plan, rows = query(index, con, question, "fund_filter")
    assert plan.intent == "fund_filter"
    allowed = {r[0] for r in con.execute("SELECT itm_no FROM fund_master WHERE sale_yn='판매중' AND prvo_pbff_desc='공모'").fetchall()}
    assert rows and all(r["itm_no"] in allowed for r in rows)


@pytest.mark.parametrize("question", ["국내 주식형 공모펀드 1년 수익률 높은 순 다섯 개", "국내 주식형 공모펀드 수익률 상위 5개", "국내 주식형 공모펀드 1년 수익률 TOP5"])
def test_fund_rank_region_and_public(index, con, question):
    _, rows = query(index, con, question, "fund_top_return_1y")
    gold = con.execute("SELECT itm_no FROM fund_master WHERE ovrs_fd_desc='국내' AND zrin_btyp_nm LIKE '%주식형%' AND prvo_pbff_desc='공모' AND try_cast(fd_yr1_ern_r AS DOUBLE)<>0 ORDER BY try_cast(fd_yr1_ern_r AS DOUBLE) DESC,itm_no LIMIT 5").fetchall()
    assert [r["itm_no"] for r in rows] == [r[0] for r in gold]


@pytest.mark.parametrize("question", ["SK 계열 종목 편입 ETF 중 규모 최대 상품 위험등급", "한화 계열사 담은 ETF 중 순자산 최대 상품 운용사", "LG 계열 종목 포함 ETF 중 규모 최고 상품"])
def test_prefix_then_holders_then_maximum(index, con, question):
    _, rows = query(index, con, question, "constituent_prefix_holders_by_aum")
    assert rows and float(rows[0]["pd_net_tamt"]) == max(float(r["pd_net_tamt"]) for r in rows)


@pytest.mark.parametrize("question", ["네이버 담은 국내 ETF와 공모펀드 1년 수익률 TOP3", "현대차 편입 국내 ETF와 공모펀드 1년 수익률 상위 3개", "삼성전자 보유 ETF와 공모펀드 연 수익률 상위 세 개"])
def test_cross_fund_reference_is_public(index, con, question):
    plan, rows = query(index, con, question, "fund_top_return_1y")
    allowed = {r[0] for r in con.execute("SELECT itm_no FROM fund_master WHERE prvo_pbff_desc='공모'").fetchall()}
    assert rows and all(r["itm_no"] in allowed for r in rows)
    assert plan.behavior_hint == "partial"


@pytest.mark.parametrize("question", ["코스피200 추종 국내 ETF 총보수 0.3% 이하 순자산 1000억원 이상 이름과 총보수만 보여줘", "KOSPI200 추종 ETF 순자산 1000억 이상 보수 0.3% 이하 알려줘", "코스피 200 지수 ETF 중 보수 0.3% 이하이고 순자산 1000억원 이상"])
def test_index_fee_aum_intersection(index, con, question):
    plan, rows = query(index, con, question, "etp_low_fee")
    gold = con.execute("SELECT pd_itm_no FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND (coalesce(cu_base_index,ref_base_index) ILIKE '%KOSPI%200%' OR pd_nm ILIKE '%KOSPI%200%' OR pd_abrv_nm ILIKE '%KOSPI%200%') AND try_cast(cu_charge_rt AS DOUBLE)>0 AND try_cast(cu_charge_rt AS DOUBLE)<=0.3 AND try_cast(pd_net_tamt AS DOUBLE)>=100000000000 ORDER BY try_cast(cu_charge_rt AS DOUBLE),pd_itm_no").fetchall()
    assert rows and [r["pd_itm_no"] for r in rows] == [r[0] for r in gold]
    if "만 보여" in question:
        answer = _draft_answer(plan,execute_plan(plan,RuntimeContext(con=con,index=index)),question)
        item_lines = [l for l in answer.splitlines() if l.startswith("  1.")]
        assert item_lines and "총보수" in item_lines[0] and "위험등급" not in item_lines[0]


@pytest.mark.parametrize("question", ["AA급 회사채 잔존만기 3년 이내 표면금리 3% 초과 퇴직연금 편입 가능한 것 세 개", "퇴직연금 편입 가능한 AA급 회사채 중 잔존만기 3년 이하 표면금리 3% 초과", "잔존만기 3년 이내 AA급 회사채 표면금리 3% 초과 퇴직 연금 가능한 채권"])
def test_maturity_coupon_rating_pension(index, con, question):
    _, rows = query(index, con, question, "bond_maturing_within")
    allowed = {r[0] for r in con.execute("SELECT PD_NO FROM kr_bond WHERE drv_maturity_status='active' AND drv_crd_grd_rank::INT BETWEEN 2 AND 4 AND STD_PD_MCLS_NM='회사채' AND replace(MAT_DT,'-','') BETWEEN '20260906' AND '20290906' AND try_cast(SRFC_IRT AS DOUBLE)>3 AND PD_PEN_TR_YN='Y'").fetchall()}
    assert rows and all(r["PD_NO"] in allowed for r in rows)


@pytest.mark.parametrize("question", ["해외ETF 미국 주식형에서 보수 0.1% 이하 순자산 10억달러 이상 100억달러 이하 규모순 TOP3", "미국 주식형 해외 ETF 보수 0.1% 이하 순자산 10억달러 이상 100억달러 이하 상위 3개", "해외 ETF 미국 주식형 보수 0.1% 이하 순자산 10억달러 이상 100억달러 이하 세 개"])
def test_global_ranges_do_not_become_domestic_product(index, con, question):
    plan, rows = query(index, con, question, "global_etf_filter")
    gold = con.execute("SELECT pd_itm_no FROM global_etf WHERE drv_instrument_type='ETF' AND wu_inv_rgn='United States of America' AND wu_inv_ast_type='Equity' AND try_cast(cu_charge_rt AS DOUBLE)>0 AND try_cast(cu_charge_rt AS DOUBLE)<=0.1 AND try_cast(du_last_aum AS DOUBLE) BETWEEN 1000000000 AND 10000000000 ORDER BY try_cast(du_last_aum AS DOUBLE) DESC,pd_itm_no LIMIT 3").fetchall()
    assert [r["pd_itm_no"] for r in rows[:3]] == [r[0] for r in gold]
    assert all(c.op != "etp_detail" for c in plan.calls)


@pytest.mark.parametrize("question", ["카카오뱅 들어간 ETF 있나요", "포스코퓨처 포함 ETF 알려줘", "셀트리온제약우 담은 ETF 알려줘"])
def test_partial_name_only_suggests(index, question):
    plan = route(question,index,today=TODAY)
    if "셀트리온제약우" in question:
        assert plan.behavior_hint != "refuse"  # 모호한 이름만으로 부재를 단정하지 않는다.
    else:
        assert plan.behavior_hint == "partial"
        assert "부분 일치" in " ".join(plan.notes)
        assert not any(c.op in ("holding_etfs","constituent_holders") for c in plan.calls)


@pytest.mark.parametrize("question", ["최근 6개월 우주항공 테마랑 연결됐던 ETF, 과거 연결 기록도 알려줘", "최근 3개월 반도체 테마의 연결 기록을 보여줘", "최근 6개월 전기차 테마 과거 연결 기록 알려줘"])
def test_theme_history_caveat(index, question):
    plan=route(question,index,today=TODAY)
    assert plan.behavior_hint == "partial"
    assert any("이력" in n and "미수집" in n for n in plan.notes)


@pytest.mark.parametrize("question", ["ETF 양도세 계산해줘", "펀드 양도 세 얼마 나와", "해외 ETF 양도소득세 계산해 주세요"])
def test_tax_calculation_refused_by_route(index,question):
    assert route(question,index,today=TODAY).behavior_hint == "refuse"


@pytest.mark.parametrize("question", ["별빛AI로봇2031 ETF라는 상품 상세 알려줘", "달빛우주성장 2032 ETF 정보 보여줘", "은하초신성2033펀드의 상세 알려주세요"])
def test_absent_numbered_name_refused_by_route(index,question):
    assert route(question,index,today=TODAY).behavior_hint == "refuse"


@pytest.mark.parametrize("question", ["2024년 이후 상장 ETF 정보 알려줘", "TIGER TDF2045 ETF 정보 알려줘", "해외 ETF 보수 0.1% 이하 알려줘"])
def test_year_and_numeric_conditions_are_not_absent_names(index,question):
    assert route(question,index,today=TODAY).behavior_hint != "refuse"


@pytest.mark.parametrize("question", [
    "표면금리가 3%인 채권 한 개만 보여줘",
    "3% 표면금리 퇴직연금 채권 하나만",
    "금리 3%짜리 채권 1개",
])
def test_single_bond_keeps_verified_product_name(index, con, question):
    plan, rows = query(index, con, question, "bond_filter")
    assert plan.hints["skip_generation"]
    assert plan.hints["display_rows"] == 1
    gold = con.execute("SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE drv_maturity_status='active' AND TRY_CAST(SRFC_IRT AS DOUBLE)=3 AND (?=false OR PD_PEN_TR_YN='Y') ORDER BY PD_NO", ["퇴직연금" in question]).fetchall()
    answer = _draft_answer(plan, execute_plan(plan, RuntimeContext(con=con, index=index)), question)
    assert rows and gold
    assert sum(any(name and name in answer for name in pair) for pair in gold) == 1


def test_all_new_traps_are_rule_refusals(index):
    path=Path(__file__).resolve().parents[1]/"evalset/evalset_codex_2.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        item=json.loads(line)
        if item["behavior"]=="refuse":
            plan=route(item["question"],index,today=TODAY)
            assert plan.stage=="rule" and plan.behavior_hint=="refuse",item["id"]
