# -*- coding: utf-8 -*-
"""S2 순서 ③ 테스트 — Router Stage A/B·채널 실행기·M1 응답 조립.

구성: ① 순수 로직(추출기·RRF·플랜 검증 — DB 불필요)
     ② 실 DB 통합(products.duckdb — 없으면 skip)
     ③ KG 통합(kg/output — 없으면 skip. kr_etf+constituents 부분 적재)
     ④ 라이브(LLM/임베딩 — RUN_LIVE_LLM=1 환경변수로만 실행. API 비용 발생)
"""
import csv
import io
import os
import re

import pytest

from engine.channels import (RuntimeContext, execute_plan, resolve_raw_params,
                             rrf_fuse)
from engine.policy import DEFAULTS, load_policy
from engine.router import (RATING_RANK, RoutePlan, detect_currency,
                           detect_time_flags, extract_percents, extract_ratings,
                           extract_risk_grades, extract_top_n, fallback_plan,
                           normalize_product_query, rating_condition, route,
                           route_stage_a)
from engine.router_llm import (args_to_plan, build_router_messages,
                               build_router_tool, extract_tool_args)
from engine.sql_templates import TEMPLATES
from engine.answer_service import answer_question
from pipeline.entity_index import DB_PATH_DEFAULT, EntityIndex, EntityRef, build_entity_index
from pipeline.themes import detect_theme_terms, expand_anchors, load_themes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KG_OUT = os.path.join(ROOT, "kg", "output")

DB_EXISTS = os.path.exists(DB_PATH_DEFAULT)
KG_EXISTS = os.path.exists(os.path.join(KG_OUT, "constituents.nt"))
needs_db = pytest.mark.skipif(not DB_EXISTS, reason="products.duckdb 미생성 — load_duckdb.py 선행")
needs_kg = pytest.mark.skipif(not (DB_EXISTS and KG_EXISTS), reason="kg/output 미생성 — build_kg.py 선행")
live_llm = pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1",
                              reason="라이브 LLM 테스트는 RUN_LIVE_LLM=1 로만(비용)")

POLICY = load_policy()
TODAY = __import__("datetime").date(2026, 8, 14)   # 결정적 시간 앵커


@pytest.fixture(scope="module")
def con():
    import duckdb
    c = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


@pytest.fixture(scope="module")
def kg_store():
    from kg.kg_store import TripleStore
    return TripleStore.from_dir(KG_OUT, tables=["kr_etf", "constituents"])


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


# ---------------------------------------------------------------------------
# 1. 순수 로직
# ---------------------------------------------------------------------------

def test_rating_rank_synced_with_dictionary():
    """코드의 서열 dict 가 credit_rating.csv(원천 사전)와 어긋나면 실패."""
    path = os.path.join(ROOT, "external_data", "dictionaries", "credit_rating.csv")
    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["분류"] != "등급체계:회사채신용등급":
                continue
            m = re.search(r"rank=(\d+)", row["단위_포맷_규칙"])
            if m and row["키"] in RATING_RANK:
                assert RATING_RANK[row["키"]] == int(m.group(1)), row["키"]


def test_extract_ratings_valid_and_invalid():
    valid, invalid = extract_ratings("신용등급이 AAAA인 채권")
    assert invalid == ["AAAA"] and valid == []
    valid, invalid = extract_ratings("AA-랑 A+ 중에")
    assert [(t, r) for t, r, _e in valid] == [("AA-", 4), ("A+", 5)] and not invalid


def test_rating_condition_policy():
    """8/14 사용자 확정: 'AA 이상'=문자 그대로(≤3), 'AA급/등급대 이상'만 AA- 포함(≤4)."""
    cond, notes = rating_condition("신용등급 AA 이상인 종목", POLICY)
    assert cond == {"max_rating_rank": 3} and any("미포함" in n for n in notes)
    cond, notes = rating_condition("AA급 이상 회사채에 투자", POLICY)
    assert cond == {"max_rating_rank": 4} and any("등급대" in n for n in notes)
    cond, _ = rating_condition("AA등급대 이상인 채권", POLICY)
    assert cond == {"max_rating_rank": 4}
    cond, _ = rating_condition("신용등급이 BBB 이하인 회사채", POLICY)
    assert cond == {"min_rating_rank": 9}


def test_extract_risk_grades():
    assert extract_risk_grades("위험등급 2등급인 ETF", POLICY)[:2] == (2, 2)
    assert extract_risk_grades("위험등급 0등급인 ETF", POLICY)[0] == "invalid"
    assert extract_risk_grades("위험등급이 낮은 국내 ETF", POLICY)[:2] == (5, 6)
    assert extract_risk_grades("신용등급이 BBB 이하인 회사채", POLICY) is None


def test_time_flags():
    assert detect_time_flags("삼성전자 지금 주가가 얼마야?").get("realtime")
    assert detect_time_flags("2026년 9월에 새로 상장한 ETF").get("post_snapshot")
    assert not detect_time_flags("2026년 8월에 새로 상장한 ETF")   # 8/22 기준일 이내 — 정상 조회(8/27 재배포)
    assert not detect_time_flags("2027년에 만기가 돌아오는 회사채 ETF")   # 만기는 위반 아님
    assert detect_time_flags("1년 전 구성종목이랑 지금을 비교해줘").get("history")


def test_small_extractors():
    assert extract_top_n("상위 5개 알려줘") == 5
    assert extract_top_n("순자산 큰 순서로 5개만") == 5
    assert extract_percents("표면금리가 5% 이상인")[0] == (5.0, "coupon", "이상")
    assert extract_percents("비중이 30%를 넘는")[0] == (30.0, "weight", "넘")
    assert detect_currency("원화 말고 다른 통화로") == ("KRW", True)
    assert detect_currency("원화채권 중") == ("KRW", False)


def test_product_query_alias_normalization():
    assert normalize_product_query("타이거 차이나테크 톱텐 정보") == \
        "TIGER 차이나테크 TOP10 정보"
    assert normalize_product_query("KB스타 200 구성") == "RISE 200 구성"
    assert normalize_product_query("킨덱스 미국S&P500 정보") == "ACE 미국S&P500 정보"


def test_rrf_fuse_semiconductor_regression():
    """벡터 1위가 로보틱스여도 anchor(키워드) 목록과 결합하면 반도체가 이긴다."""
    vec = ["ROBOTICS", "SEMI-1", "OTHER"]
    lex = ["SEMI-1", "SEMI-2"]
    fused = rrf_fuse([vec, lex])
    assert fused[0][0] == "SEMI-1"
    assert fused[0][1] > dict(fused)["ROBOTICS"]


def test_entity_scan_longest_match():
    idx = EntityIndex()
    idx.add("삼성전자", EntityRef("constituent", "005930", "삼성전자", "KRX-PDF"))
    idx.add("삼성전자우", EntityRef("constituent", "005935", "삼성전자우", "KRX-PDF"))
    hits = idx.scan("삼성전자 우선주를 담은 ETF도 있어?")
    assert [h[0] for h in hits] == ["삼성전자우"]        # 긴 매칭이 짧은 매칭을 흡수
    hits2 = idx.scan("삼성전자가 포함된 ETF")
    assert [h[0] for h in hits2] == ["삼성전자"]


def test_resolve_raw_params_escapes():
    out = resolve_raw_params({"pattern_raw": "100%_리츠", "limit": 5})
    assert out == {"pattern": "%100\\%\\_리츠%", "limit": 5}


def test_themes_dictionary():
    themes = load_themes()
    assert "semiconductor" in themes["반도체"]
    assert detect_theme_terms("반도체 산업에 집중 투자하는 해외 ETF는?", themes)[0] == "반도체"
    anchors = expand_anchors(["반도체"], themes)
    assert anchors[0] == "반도체" and "semiconductor" in anchors
    assert "금" not in detect_theme_terms("지금 판매 중인 펀드", themes)   # 1글자 오탐 방지


def test_router_tool_schema_and_plan_validation():
    tool = build_router_tool()
    enum = tool["function"]["parameters"]["properties"]["sql_calls"]["items"][
        "properties"]["template_id"]["enum"]
    assert set(enum) == set(TEMPLATES)
    partial = RoutePlan(intent="unresolved")
    plan = args_to_plan({"intent": "테스트",
                         "sql_calls": [{"template_id": "etp_top_aum",
                                        "params": {"instrument_type": "ETF", "limit": 5}}],
                         "graph_calls": [{"op": "holding_etfs", "query": "005930"}]},
                        partial)
    assert [c.channel for c in plan.calls] == ["sql", "graph"] and plan.stage == "llm"
    partial_plan = args_to_plan(
        {"intent": "부분 조회",
         "sql_calls": [{"template_id": "etp_top_aum",
                         "params": {"instrument_type": "ETF", "limit": 5}}],
         "unsupported_constraints": ["법적 자회사 관계"]},
        RoutePlan(intent="unresolved"))
    assert partial_plan.behavior_hint == "partial"
    assert partial_plan.hints["unsupported_constraints"] == ["법적 자회사 관계"]
    assert any("미지원 조건" in note for note in partial_plan.notes)
    with pytest.raises(KeyError):
        args_to_plan({"intent": "x", "sql_calls": [{"template_id": "없는것"}]}, partial)
    with pytest.raises(ValueError, match="필수"):
        args_to_plan({"intent": "x", "sql_calls": [{"template_id": "etp_top_aum",
                                                    "params": {"instrument_type": "ETF"}}]}, partial)
    with pytest.raises(ValueError, match="그래프 op"):
        args_to_plan({"intent": "x", "graph_calls": [{"op": "이상한것", "query": "y"}]}, partial)
    with pytest.raises(ValueError, match="하나도 없음"):
        args_to_plan({"intent": "x"}, partial)


def test_extract_tool_args_shapes():
    resp = {"result": {"message": {"toolCalls": [
        {"id": "c1", "function": {"name": "submit_route_plan",
                                  "arguments": {"intent": "ok"}}}]}}}
    cid, args = extract_tool_args(resp)
    assert cid == "c1" and args["intent"] == "ok"
    with pytest.raises(ValueError, match="toolCalls"):
        extract_tool_args({"result": {"message": {"content": "그냥 텍스트"}}})


def test_policy_defaults_match_config():
    assert POLICY["rating_at_or_above_includes_minus"] == DEFAULTS["rating_at_or_above_includes_minus"]
    assert POLICY["low_risk_grades"] == DEFAULTS["low_risk_grades"]


# ---------------------------------------------------------------------------
# 2. 실 DB 통합 — 평가셋 대표 문항 라우팅·실행
# ---------------------------------------------------------------------------

@needs_db
def test_route_L01_bond_filter(index):
    plan = _route(index, "현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘")
    assert plan.stage == "rule" and plan.intent == "bond_filter"
    ops = {c.op: c.params for c in plan.calls}
    assert ops["bond_filter"]["currency"] == "KRW"
    assert ops["bond_filter"]["max_rating_rank"] == 3      # 문자 그대로(AA- 미포함) — 8/14 확정
    assert ops["bond_filter"]["buyable_only"] == "Y"
    assert "bond_count" in ops
    assert any("미포함" in n for n in plan.notes)           # 채택 해석을 답변에 명시


@needs_db
def test_route_L04_bond_top_maturity(index):
    plan = _route(index, "잔존만기가 가장 긴 국고채 5개 알려줘")
    call = plan.calls[0]
    assert call.op == "bond_top_maturity"
    # 8/19: 잔존만기(년·일)를 SQL 이 요청 시점 기준으로 계산해 돌려준다(생성기 계산분이 사후 대조에 걸리던 L-04)
    assert call.params == {"bond_class": "국공채", "as_of_date": TODAY.isoformat(), "limit": 5}


@needs_db
def test_route_L11_top_aum_executes_kodex200(con, index):
    plan = _route(index, "순자산총액 기준으로 국내 ETF 상위 5개 알려줘")
    assert plan.calls[0].op == "etp_top_aum" and plan.calls[0].params["limit"] == 5
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    assert "KODEX 200" in result.outcomes[0].rows[0]["pd_abrv_nm"]


@needs_db
def test_route_L21_fund_counts(index):
    plan = _route(index, "공모펀드는 총 몇 개야?")
    assert plan.calls[0].op == "fund_counts" and plan.stage == "rule"


@needs_db
def test_route_L22_on_sale_fund_uses_real_sale_values(con, index):
    plan = _route(index, "지금 판매 중인 주식형 공모펀드 보여줘")
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    rows = result.outcomes[0].rows
    assert rows and all(str(r["sale_yn"]).replace(" ", "") == "판매중" for r in rows)


@needs_db
def test_route_fund_current_sale_and_our_sale_are_separate(con, index):
    generic = _route(index, "지금 판매 중인 공모펀드 보여줘")
    generic_call = next(c for c in generic.calls if c.op == "fund_filter")
    assert generic_call.params["on_sale_only"] == "Y"
    assert "thco_sale_only" not in generic_call.params

    ours = _route(index, "미래에셋증권에서 지금 판매 중인 공모펀드 보여줘")
    ours_call = next(c for c in ours.calls if c.op == "fund_filter")
    assert ours_call.params["on_sale_only"] == "Y"
    assert ours_call.params["thco_sale_only"] == "Y"
    result = execute_plan(ours, RuntimeContext(con=con, index=index))
    rows = result.outcomes[0].rows
    assert rows and all(r["sale_yn"] == "판매중" and r["thco_sale_yn"] == "Y"
                        for r in rows)

    third_party = _route(index, "타사에서 판매 중인 공모펀드 보여줘")
    assert third_party.intent == "unsupported_field"
    assert third_party.behavior_hint == "refuse"


@needs_db
def test_route_M08_product_alias_resolves_to_internal_id(index):
    plan = _route(index, "타이거 차이나테크 톱텐 정보 알려줘")
    calls = {(c.channel, c.op): c.params for c in plan.calls}
    assert plan.stage == "rule" and plan.intent == "product_detail"
    assert calls[("sql", "etp_detail")]["pd_itm_no"].startswith("KR7")
    assert ("keyword", "lookup") in calls


@needs_db
def test_route_legacy_brand_alias_uses_current_product_index(index):
    plan = _route(index, "KBSTAR 200 구성종목 알려줘")
    call = next(c for c in plan.calls if c.op == "constituent_top_weights")
    assert plan.intent == "product_constituents"
    assert call.params["etf_id"] == "KR7148020001"


@needs_db
def test_route_M10_unstructured_fund_is_partial(index):
    plan = _route(index, "국민성장펀드의 구조와 투자전략 동향을 찾아서 알려줘")
    assert plan.stage == "rule" and plan.intent == "unstructured_info"
    assert plan.behavior_hint == "partial" and plan.hints["skip_generation"]


@needs_db
def test_route_H06_risk_grade_direction_is_forced(index):
    plan = _route(index, "캠브리콘이 들어간 ETF들 위험등급은 어때?")
    assert plan.hints["skip_generation"]
    assert any("1등급=매우 높은 위험" in n for n in plan.notes)


@needs_db
def test_route_H26_residual_maturity_composite_filter(con, index):
    plan = _route(index, "지금 기준으로 잔존만기 3년 이하이고 AA 이상이면서 표면금리 4%대인 채권 찾아줘")
    assert plan.stage == "rule" and plan.intent == "bond_maturing_filter"
    call = plan.calls[0]
    assert call.op == "bond_maturing_within"
    assert call.params["max_rating_rank"] == 3
    assert call.params["min_coupon"] == 4.0 and call.params["max_coupon"] == 5.0
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    assert result.outcomes[0].ok and result.outcomes[0].rows


@needs_db
def test_route_M30_description_resolves_product_constituents(index):
    plan = _route(index, "인도 일등기업에 투자하는 액티브 ETF의 구성종목 알려줘")
    calls = {(c.channel, c.op): c.params for c in plan.calls}
    assert plan.stage == "rule" and plan.intent == "product_constituents"
    assert calls[("sql", "constituent_top_weights")]["etf_id"] == "KR70002C0008"
    assert ("keyword", "lookup") in calls and ("graph", "constituents_of") in calls


@needs_db
def test_route_H01_subsidiary_query_is_partial_and_prefix_aggregated(index):
    """8/26(v2 O-05): 자회사 질의는 후보 4종 나열이 아니라 6.0(그룹)과 같은 접두 집계로 —
    회사명이 base 로 시작하는 종목을 편입한 ETF 를 순자산 큰 순으로 조회한다."""
    plan = _route(index, "에코프로의 자회사를 편입한 ETF 중에 순자산이 큰 상품의 위험요인을 알려줘")
    assert plan.intent == "subsidiary_holding_candidates" and plan.behavior_hint == "partial"
    holder_call = next(c for c in plan.calls if c.op == "constituent_prefix_holders_by_aum")
    assert holder_call.params["prefix_raw"] == "에코프로"
    assert plan.hints["group_prefix"] == "에코프로" and plan.hints["order"] == "aum"
    assert plan.hints["skip_generation"]


@needs_db
def test_route_H02_theme_history_is_partial(index):
    plan = _route(index, "최근 6개월 동안 우주항공 테마와 연결된 이력이 있는 ETF를 정리해줘")
    assert plan.intent == "theme_history" and plan.behavior_hint == "partial"
    assert {c.channel for c in plan.calls} == {"keyword", "sql", "vector"}
    assert any("2026-02-22~2026-08-22" in n for n in plan.notes)


@needs_db
def test_route_H03_constituent_intersection_low_fee(con, index):
    plan = _route(index, "삼성전자랑 SK하이닉스를 둘 다 담고 있는 ETF 중에서 총보수가 제일 낮은 건 뭐야?")
    assert plan.intent == "constituent_intersection_low_fee" and plan.behavior_hint == "partial"
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    fee_rows = next(o.rows for o in result.outcomes if o.op == "constituent_intersection_low_fee")
    # 8/19: 총보수 0 표기는 의미 미확정(미수집 추정 — KODEX 200 도 0)이라 순위에서 제외한다 —
    # 값 보유(>0) 상품이 오름차순으로 나오고, 제외 사실이 노트에 남는다
    assert fee_rows and float(fee_rows[0]["cu_charge_rt"]) > 0.0
    fees = [float(r["cu_charge_rt"]) for r in fee_rows]
    assert fees == sorted(fees)
    assert any("0 표기" in n for n in plan.notes)


@needs_db
def test_route_H13_cross_product_risk_counts(con, index):
    plan = _route(index, "위험등급 1등급(매우 높은 위험) 상품이 상품군별로 몇 개씩 있어?")
    assert plan.intent == "risk_grade_cross_counts" and plan.behavior_hint == "partial"
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    groups = {r["product_group"] for r in result.outcomes[0].rows}
    assert groups == {"국내채권", "국내ETF", "국내ETN", "공모펀드"}
    bond = next(r for r in result.outcomes[0].rows if r["product_group"] == "국내채권")
    assert bond["n"] == 1395    # 8/27 재배포본: 위험등급 코드 11~16 정정 후 1등급 채권 실측


@needs_db
def test_route_H15_bond_etf_rating_distribution(con, index):
    plan = _route(index, "회사채 ETF가 실제로 담고 있는 채권들의 신용등급 분포를 보여줘")
    assert plan.intent == "bond_etf_rating_dist" and plan.behavior_hint == "partial"
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    rows = result.outcomes[0].rows
    assert rows and any(r["credit_rating"] == "미확인" for r in rows)
    assert rows[0]["matched_ratings"] < rows[0]["total_constituents"]


@needs_db
def test_route_L24_fund_ranking_coverage_is_answer_caveat(con, index):
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("1년 수익률이 좋은 공모펀드 5개 알려줘", ctx, today=TODAY)
    assert "behavior=answer" in out["think_trace"]
    assert "커버리지" in out["answer"] or "값 보유" in out["answer"]


@needs_db
def test_route_L29_fund_class_dictionary(con, index):
    plan = _route(index, "펀드 클래스 A형이랑 C형은 뭐가 달라?")
    assert plan.intent == "fund_class_compare" and plan.behavior_hint == "answer"
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    rows = result.outcomes[0].rows
    assert {r["class"] for r in rows} == {"A", "C"}
    assert any("선취판매수수료" in r["meaning"] for r in rows if r["class"] == "A")


@needs_db
def test_route_H19_tdf_empty_constituent_disclosure_is_partial(index):
    plan = _route(index, "TDF(타깃데이트) ETF 상품이 있어? 뭘 담고 있는지도 알려줘")
    assert plan.intent == "tdf_products_constituents" and plan.behavior_hint == "partial"
    assert {c.channel for c in plan.calls} == {"keyword", "sql"}
    assert any(c.op == "constituent_top_weights" for c in plan.calls)
    assert any("빈 값" in n for n in plan.notes)


@needs_db
def test_route_M01_constituent_reverse(index):
    plan = _route(index, "삼성전자가 포함된 ETF 알려줘")
    assert plan.intent == "constituent_reverse"
    ops = {c.op: c for c in plan.calls}
    assert ops["holding_etfs"].params["query"] == "005930"
    assert ops["constituent_holders"].params["code"] == "005930"


@needs_db
def test_route_M16_preferred_stock(index):
    plan = _route(index, "삼성전자 우선주를 담은 ETF도 있어?")
    assert plan.hints["constituent"]["key"] == "005935"     # 보통주(005930)와 구분


@needs_db
def test_route_M25_product_constituents(index):
    plan = _route(index, "TIGER 200 구성종목 중 비중 상위 3개가 뭐야?")
    call = plan.calls[0]
    assert call.op == "constituent_top_weights" and call.params["limit"] == 3
    assert call.params["etf_id"].startswith("KR7")


@needs_db
def test_route_H14_weight_above(con, index):
    plan = _route(index, "삼성전자 비중이 30%를 넘는 ETF가 있어?")
    call = plan.calls[0]
    assert call.op == "constituent_weight_above" and call.params["min_weight"] == 30.0
    result = execute_plan(plan, RuntimeContext(con=con, index=index))
    assert result.outcomes[0].rows and result.outcomes[0].rows[0]["weight_pct"] > 30


@needs_db
def test_route_H29_mgmt_share(index):
    plan = _route(index, "운용사별 국내 ETF 순자산 점유율 상위 3개사 알려줘")
    assert plan.calls[0].op == "mgmt_top_share" and plan.calls[0].params["limit"] == 3
    assert plan.behavior_hint == "partial"


@needs_db
def test_route_traps_refuse_hints(index):
    cases = {
        "신용등급이 AAAA인 채권을 찾아줘": "invalid_value",
        "위험등급 0등급인 초저위험 ETF 알려줘": "invalid_value",
        "총보수가 마이너스인 ETF 있어?": "invalid_value",
        "테슬라 코인에 투자하는 펀드 찾아줘": "unsupported_asset",
        "삼성전자 지금 주가가 얼마야?": "time_violation",
        "2026년 9월에 새로 상장한 ETF 알려줘": "time_violation",
        "다음 달 금리 인하 가능성을 반영해서 채권 추천해줘": "time_violation",
        "TIGER 200의 1년 전 구성종목이랑 지금을 비교해줘": "time_violation",
        "해외 ETF를 위험등급 1등급만 골라서 보여줘": "unsupported_field",
        "kimi 관련 투자 상품 있어?": "existence_check",
        "KODEX AI 로봇 ETF 정보 알려줘": "existence_check",
    }
    for q, intent in cases.items():
        plan = _route(index, q)
        assert plan.intent == intent, f"{q} → {plan.intent} (기대 {intent})"
        assert plan.behavior_hint == "refuse", q
        assert plan.stage == "rule", q                     # 트랩 방어는 LLM 없이 확정
    # 8/28 r2: '펀드 총보수'는 재배포본 보수 분해 4종 신설로 거절이 아니라 합산 partial 로 전환
    plan = _route(index, "공모펀드 중에서 총보수 제일 낮은 것 알려줘")
    assert plan.intent == "fund_fee_rank" and plan.behavior_hint == "partial"


@needs_db
def test_route_L08_rating_compare(index):
    plan = _route(index, "AA-랑 A+ 중에 어느 쪽이 더 높은 등급이야?")
    assert plan.intent == "rating_compare"
    assert plan.hints["rating_compare"] == [("AA-", 4), ("A+", 5)]


@needs_db
def test_route_M13_theme_uses_vector_and_anchor(con, index):
    plan = _route(index, "반도체 산업에 집중 투자하는 해외 ETF는?")
    channels = {c.channel for c in plan.calls}
    assert "vector" in channels
    result = execute_plan(plan, RuntimeContext(con=con, index=index))   # 임베더 없음 → lexical 단독
    vec = next(o for o in result.outcomes if o.channel == "vector")
    assert vec.ok and vec.rows, vec.error
    assert any(re.search(r"semiconductor|반도체", str(r["pd_nm"]), re.I) for r in vec.rows[:5])


@needs_db
def test_unresolved_falls_back_offline(index):
    q = "국내 ETF와 해외 ETF 중 동일 지수를 추종하는 상품을 비교해줘"
    plan_a, needs_llm = route_stage_a(q, index, POLICY, TODAY)
    assert needs_llm                                        # 교차 시장 비교는 Stage B 소관
    plan = _route(index, q)                                 # LLM 미주입 → 폴백
    assert plan.stage == "fallback" and plan.calls


@needs_db
def test_answer_question_L01_five_string_fields(con, index):
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘",
                          ctx, question_id="L-01", today=TODAY)
    assert set(out) == {"question_id", "question", "retrieved_context", "think_trace", "answer"}
    assert all(isinstance(v, str) for v in out.values())
    assert "PRBD01N001" in out["retrieved_context"]
    assert "AA" in out["answer"] and "매수가능" in out["answer"]


@needs_db
def test_answer_question_trap_kimi_refuses(con, index):
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("kimi 관련 투자 상품 있어?", ctx, question_id="T-04", today=TODAY)
    assert out["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다")
    assert "채널: validation" in out["retrieved_context"]   # 거절도 근거(판정·사유)를 남긴다


@needs_db
def test_answer_question_existing_product_not_refused(con, index):
    """존재 질의라도 직접 일치가 확인되면 거절하지 않는다(과잉 거절 방지)."""
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("TIGER 200 정보 알려줘", ctx, today=TODAY)
    assert "확인할 수 없습니다" not in out["answer"]


# ---------------------------------------------------------------------------
# 3. KG 통합 — 그래프 채널 (부분 적재: kr_etf + constituents)
# ---------------------------------------------------------------------------

@needs_kg
def test_graph_channel_samsung_holdings(con, index, kg_store):
    plan = _route(index, "삼성전자가 포함된 ETF 알려줘")
    ctx = RuntimeContext(con=con, index=index, kg_store=kg_store)
    result = execute_plan(plan, ctx)
    graph = next(o for o in result.outcomes if o.channel == "graph")
    assert graph.ok and graph.rows
    assert graph.rows[0]["편입ETF수"] > 200                 # 실측 229종목
    out = answer_question("삼성전자가 포함된 ETF 알려줘", ctx, question_id="M-01", today=TODAY)
    assert "삼성전자" in out["answer"] and "2026-08-21" in out["answer"]


# ---------------------------------------------------------------------------
# 4. 라이브 (RUN_LIVE_LLM=1 일 때만 — HCX-005 FC 1~2콜 비용)
# ---------------------------------------------------------------------------

@needs_db
@live_llm
def test_stage_b_live_intersection_question(index):
    from engine.router_llm import make_llm_router
    q = "삼성전자랑 SK하이닉스를 둘 다 담고 있는 ETF 중에서 총보수가 제일 낮은 건 뭐야?"
    plan = route(q, index, policy=POLICY, today=TODAY, llm_router=make_llm_router())
    assert plan.stage in ("llm", "llm_repair", "fallback")
    assert plan.calls
    for call in plan.calls:                                 # 플랜 전 호출이 실행 가능형이어야 함
        assert call.channel in ("sql", "graph", "keyword", "vector")


# ---------------------------------------------------------------------------
# 5. ⑧ 다듬기(8/19) — 첫 성적표가 드러낸 유형의 규칙 잠금
#    (별칭 복수 상장 · 그룹/계열사 · 상품명 조각 우선 · 운용사×테마 · 펀드 상세 · 코스닥 비중 · 리츠 · 분포 결론)
# ---------------------------------------------------------------------------

@needs_db
def test_alias_with_multiple_listings_is_one_entity(index):
    """'구글'(알파벳 A/C 2종) · '알리바바'(홍콩/ADR) — 키가 2개여도 한 개체로 보고 편입 ETF 를 합쳐 조회(M-22/H-27)."""
    for q, n_keys in (("구글 주식을 담은 국내 상장 ETF 알려줘", 2),
                      ("알리바바 같은 중국 빅테크를 담은 ETF가 있으면 위험등급이랑 같이 알려줘", 2)):
        plan = _route(index, q)
        assert plan.intent == "constituent_reverse", q
        holders = [c for c in plan.calls if c.op == "constituent_holders"]
        assert len(holders) == n_keys and len({c.params["code"] for c in holders}) == n_keys
        assert any("복수 상장" in n for n in plan.notes)


@needs_db
def test_constituent_reverse_orders_by_aum_in_sql(index):
    """'순자산이 큰 순서로' 는 SQL 이 전체 편입 ETF 에서 정렬한다(비중 상위 30 안에서만 재정렬하면 M-02 오답)."""
    plan = _route(index, "SK하이닉스를 담은 ETF 중에 순자산이 큰 순서로 5개만 알려줘")
    holders = [c for c in plan.calls if c.op == "constituent_holders"]
    assert holders and all(c.params.get("order") == "aum" for c in holders)
    assert plan.hints.get("order") == "aum"


@needs_db
def test_group_affiliate_questions(con, index):
    """'X그룹 계열사' — X그룹주 상품 우선 + 회사명 접두 후보 집계 (M-14/H-10/H-23)."""
    for q, prefix in (("한화그룹 계열사에 투자하는 ETF 있어?", "한화"),
                      ("한화그룹주 ETF는 계열사별로 몇 퍼센트씩 담고 있어?", "한화"),
                      ("SK 계열사(하이닉스·스퀘어·리츠 등)를 담은 ETF를 계열사별로 정리해줘", "SK")):
        plan = _route(index, q)
        assert plan.intent == "group_holdings" and plan.behavior_hint == "partial", q
        ops = {c.op for c in plan.calls}
        assert {"etp_name_search", "etp_pattern_top_constituents", "constituent_group_holders"} <= ops
        grp = next(c for c in plan.calls if c.op == "constituent_group_holders")
        assert grp.params["prefix_raw"] == prefix
        assert any("접두 기준" in n for n in plan.notes)      # 법적 계열 관계 아님을 명시
    result = execute_plan(_route(index, "SK 계열사를 담은 ETF를 계열사별로 정리해줘"),
                          RuntimeContext(con=con, index=index))
    rows = next(o.rows for o in result.outcomes if o.op == "constituent_group_holders")
    names = [r["COMPST_ISU_NM"] for r in rows]
    assert names[0] == "SK하이닉스" and "SK스퀘어" in names and "SK리츠" in names
    assert all(n.upper().startswith("SK") for n in names)


@needs_db
def test_product_fragment_first_grounding(con, index):
    """'애플 밸류체인 ETF 뭘 담고 있어' 는 애플(종목) 역질의가 아니라 그 상품의 구성 질의(M-19) —
    구성 공시가 빈 상품은 '구성 공시 없음'을 명시. '위클리 커버드콜'은 후보 여러 개(H-20)."""
    plan = _route(index, "애플 밸류체인에 투자하는 ETF가 있다던데, 뭘 담고 있어?")
    assert plan.intent == "product_constituents_by_name" and plan.behavior_hint == "answer"
    assert plan.hints["product_fragment"] == "애플밸류체인"
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("애플 밸류체인에 투자하는 ETF가 있다던데, 뭘 담고 있어?", ctx, today=TODAY)
    assert "애플밸류체인" in out["answer"] and "구성 공시 없음" in out["answer"]
    plan2 = _route(index, "위클리 커버드콜 ETF는 옵션을 실제로 뭘 들고 있어?")
    assert plan2.intent == "product_constituents_by_name" and plan2.behavior_hint == "partial"
    assert plan2.hints["product_fragment"] == "위클리커버드콜"
    # 종목명이 그대로 있는 질문은 가로채지 않는다(구성종목 역질의 유지)
    assert _route(index, "삼성전자를 담은 ETF 알려줘").intent == "constituent_reverse"


@needs_db
def test_mgmt_theme_constituents(con, index):
    """운용사 × 지역 테마 × 구성 (H-08): 상품명 표기 변형(중국/차이나/China)으로 순자산 상위 ETF 의 구성 상위."""
    q = "미래에셋이 운용하는 중국 관련 ETF의 구성 상위 종목 보여줘"
    plan = _route(index, q)
    assert plan.intent == "mgmt_theme_constituents"
    pats = [c.params["pattern_raw"] for c in plan.calls if c.op == "etp_pattern_top_constituents"]
    assert pats[:2] == ["중국", "차이나"] and all(
        c.params.get("mgmt") == "미래에셋" for c in plan.calls if c.op == "etp_pattern_top_constituents")
    out = answer_question(q, RuntimeContext(con=con, index=index), today=TODAY)
    assert "TIGER 차이나" in out["answer"] or "TIGER 중국" in out["answer"]


@needs_db
def test_fund_unstructured_still_answers_master_fields(con, index):
    """M-10: 구조·전략 서술은 없어도 부분 명칭(국민성장펀드)으로 펀드를 찾아 마스터 필드는 답한다."""
    q = "국민성장펀드의 구조와 투자전략 동향을 찾아서 알려줘"
    plan = _route(index, q)
    assert plan.intent == "unstructured_info" and any(c.op == "fund_detail" for c in plan.calls)
    out = answer_question(q, RuntimeContext(con=con, index=index), today=TODAY)
    assert "국민성장" in out["answer"] and "비정형 자료" in out["answer"]
    assert "위험등급" in out["answer"] or "drv_risk_grade" in out["answer"]


@needs_db
def test_theme_ksq_share_and_reit_breakdown(con, index):
    """H-22 코스닥 비중 · H-07 리츠 ETF vs 개별 상장 리츠."""
    plan = _route(index, "바이오 ETF 중에 코스닥 종목 비중이 높은 상품은?")
    assert plan.intent == "theme_ksq_share"
    rows = next(o.rows for o in execute_plan(plan, RuntimeContext(con=con, index=index)).outcomes
                if o.op == "constituent_ksq_share")
    assert rows and rows[0]["ksq_weight_pct"] >= rows[-1]["ksq_weight_pct"] and rows[0]["ksq_weight_pct"] > 90
    plan2 = _route(index, "리츠에 투자하는 방법을 ETF랑 개별 상장 리츠로 나눠 정리해줘")
    assert plan2.intent == "reit_breakdown"
    out = answer_question("리츠에 투자하는 방법을 ETF랑 개별 상장 리츠로 나눠 정리해줘",
                          RuntimeContext(con=con, index=index), today=TODAY)
    assert "리츠부동산인프라" in out["answer"] and ("SK리츠" in out["answer"] or "신한알파리츠" in out["answer"])


@needs_db
def test_distribution_conclusion_and_missing_index_note(con, index):
    """L-20/L-30 분포 답변은 '전부 X — 다른 것 없음' 결론을, L-28 은 기초지수 결측을 명시한다."""
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("달러 말고 다른 통화로 거래되는 해외 ETF도 있어?", ctx, today=TODAY)
    assert "전부 USD" in out["answer"] and "다른 거래통화 없음" in out["answer"]
    out2 = answer_question("TIGER 200이 추종하는 지수가 뭐야?", ctx, today=TODAY)
    assert "기초지수(Refinitiv 참조 ref_base_index): KOSPI 200 CR" in out2["answer"]   # 8/27 재배포본: 참조 지수 신설
    from engine.answer_service import dist_sentence
    assert dist_sentence("etp_currency_dist", [{"drv_curr_cd": "KRW", "n": 1500}, {"drv_curr_cd": "USD", "n": 5}]) \
        == "거래통화 분포: KRW 1,500건(99.7%), USD 5건(0.3%) — 총 1,505건, 이 밖의 거래통화 없음"
    assert dist_sentence("bond_class_dist", []) is None


def test_new_templates_registered_with_like_conventions():
    """8/19 신규 템플릿 5종 — 등록·파라미터 규약(pattern/prefix 는 *_raw 로 플랜에 실림)."""
    for tid in ("etp_pattern_top_constituents", "constituent_group_holders", "fund_detail",
                "constituent_ksq_share", "reit_constituents"):
        assert tid in TEMPLATES, tid
    assert resolve_raw_params({"prefix_raw": "SK_", "limit": 5}) == {"prefix": r"SK\_%", "limit": 5}
    assert resolve_raw_params({"pattern_raw": "50%", "top_etfs": 3}) == {"pattern": r"%50\%%", "top_etfs": 3}
    order_param = next(p for p in TEMPLATES["constituent_holders"].params if p.name == "order")
    assert order_param.enum == ("aum", "weight", "fee", "mkt_cap")   # fee 는 8/26 v3 C-03(총보수 오름차순) · mkt_cap 은 9/2(시가총액=종가×상장주식수)
    assert any(p.name == "mgmt" for p in TEMPLATES["constituent_holders"].params)   # 8/26 v2 H-08


@needs_db
def test_fragment_intersection_and_per_product_details(con, index):
    """'방산 테마 레버리지 ETF' 는 '방산' ∩ '레버리지' 상품 3종(ETF·ETN)의 상세+구성 — 조선업은 '업' 을 떼고 본다(M-20/H-21)."""
    plan = _route(index, "방산 테마 레버리지 ETF의 구성이 궁금해")
    assert plan.intent == "product_constituents_by_name" and plan.hints["product_fragment"] == "방산+레버리지"
    ops = [c.op for c in plan.calls]
    assert ops.count("etp_detail") == 3 and ops.count("constituent_top_weights") == 3
    plan2 = _route(index, "조선업 테마 레버리지 ETF의 구성과 위험 특성을 알려줘")
    assert plan2.intent == "product_constituents_by_name" and plan2.hints["product_fragment"] == "조선+레버리지"
    out = answer_question("조선업 테마 레버리지 ETF의 구성과 위험 특성을 알려줘",
                          RuntimeContext(con=con, index=index), today=TODAY)
    assert "SOL 조선TOP3플러스레버리지" in out["answer"] and "파생" in out["answer"]
    # 비교·선택형 질문(코스닥 비중)은 상품명 조각 규칙이 가로채지 않는다
    assert _route(index, "바이오 ETF 중에 코스닥 종목 비중이 높은 상품은?").intent == "theme_ksq_share"


@needs_db
def test_common_holdings_and_target_maturity_templates(con, index):
    """H-09 수익률 상위 N 의 공통 종목(현금성 제외) · H-12 만기형 채권 ETF 의 만기 창."""
    q = "올해 수익률 상위 10개 국내 ETF가 공통으로 담고 있는 종목이 있어?"
    plan = _route(index, q)
    assert plan.intent == "etp_ranking_common_holdings"
    rows = next(o.rows for o in execute_plan(plan, RuntimeContext(con=con, index=index)).outcomes
                if o.op == "etp_top_return_common_holdings")
    assert rows and all(r["n_etfs_holding"] >= 2 for r in rows)
    assert not any("현금" in r["COMPST_ISU_NM"] for r in rows)
    plan2 = _route(index, "지금부터 1년 안에 만기가 도래하는 만기형 채권 ETF 있어?")
    assert plan2.intent == "etp_target_maturity"
    call = plan2.calls[0]
    assert call.op == "etp_target_maturity_within" and call.params["date_from"] == "2026-08-01" \
        and call.params["date_to"] == "2027-08-14"
    rows2 = next(o.rows for o in execute_plan(plan2, RuntimeContext(con=con, index=index)).outcomes
                 if o.op == "etp_target_maturity_within")
    assert rows2 and all("2026-08" <= r["maturity_yyyymm"] <= "2027-08" for r in rows2)


@needs_db
def test_count_and_residual_maturity_are_data_notes(con, index):
    """건수 결과는 문장으로(L-05 — 생성기가 'n=306' 을 정보 없음으로 오독), 잔존만기는 SQL 계산값(L-04)."""
    ctx = RuntimeContext(con=con, index=index)
    out = answer_question("신용등급이 BBB 이하인 회사채는 몇 개나 돼?", ctx, today=TODAY)
    assert "조건 일치 건수 — 건수 178건" in out["answer"]   # 8/27 재배포본 실측
    out2 = answer_question("잔존만기가 가장 긴 국고채 5개 알려줘", ctx, today=TODAY)
    assert "잔존만기(년)" in out2["answer"] and "2074" in out2["answer"]   # 9/3 표기: 한글 라벨
    from engine.answer_service import count_sentence
    assert count_sentence("fund_counts", [{"products": 11138, "share_classes": 95618}]) \
        == "조건 일치 건수 — 상품(마스터) 수 11,138건 · 판매 클래스 수 95,618건"
    assert count_sentence("etp_count", [{"drv_instrument_type": "ETF", "drv_listing_status": "active", "n": 1139}]) \
        == "조건 일치 건수 — ETF active: 건수 1,139건"


def test_theme_vector_market_filter(index):
    """시장 명시 테마 질문은 벡터 호출에 market 필터를 싣는다(8/22 M-12 실측 — 국내 근거 혼입 방지)."""
    plan = _route(index, "배당 수익 중심 전략을 쓰는 해외 ETF 알려줘")
    vec = [c for c in plan.calls if c.channel == "vector"]
    assert vec and vec[0].params.get("market") == "해외상장"
    # 국내 명시 — 그동안 없던 벡터 호출이 국내 필터로 생긴다(2순위 확장의 목적)
    plan2 = _route(index, "국내 2차전지 테마 ETF 알려줘")
    vec2 = [c for c in plan2.calls if c.channel == "vector"]
    assert vec2 and vec2[0].params.get("market") == "국내상장"
    # 국내·해외 비교(H-04 유형)는 필터 없음 — 두 시장 다 후보
    plan3 = _route(index, "반도체에 투자하고 싶은데 국내 상장 ETF랑 해외 상장 ETF 옵션을 비교해줘")
    vec3 = [c for c in plan3.calls if c.channel == "vector"]
    assert vec3 and "market" not in vec3[0].params
