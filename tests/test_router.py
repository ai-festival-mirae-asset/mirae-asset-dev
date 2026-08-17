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
                           rating_condition, route, route_stage_a)
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
    assert detect_time_flags("2026년 8월에 새로 상장한 ETF").get("post_snapshot")
    assert not detect_time_flags("2027년에 만기가 돌아오는 회사채 ETF")   # 만기는 위반 아님
    assert detect_time_flags("1년 전 구성종목이랑 지금을 비교해줘").get("history")


def test_small_extractors():
    assert extract_top_n("상위 5개 알려줘") == 5
    assert extract_top_n("순자산 큰 순서로 5개만") == 5
    assert extract_percents("표면금리가 5% 이상인")[0] == (5.0, "coupon", "이상")
    assert extract_percents("비중이 30%를 넘는")[0] == (30.0, "weight", "넘")
    assert detect_currency("원화 말고 다른 통화로") == ("KRW", True)
    assert detect_currency("원화채권 중") == ("KRW", False)


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
    assert call.params == {"bond_class": "국공채", "limit": 5}


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
        "2026년 8월에 새로 상장한 ETF 알려줘": "time_violation",
        "다음 달 금리 인하 가능성을 반영해서 채권 추천해줘": "time_violation",
        "TIGER 200의 1년 전 구성종목이랑 지금을 비교해줘": "time_violation",
        "해외 ETF를 위험등급 1등급만 골라서 보여줘": "unsupported_field",
        "공모펀드 중에서 총보수 제일 낮은 것 알려줘": "unsupported_field",
        "kimi 관련 투자 상품 있어?": "existence_check",
        "KODEX AI 로봇 ETF 정보 알려줘": "existence_check",
    }
    for q, intent in cases.items():
        plan = _route(index, q)
        assert plan.intent == intent, f"{q} → {plan.intent} (기대 {intent})"
        assert plan.behavior_hint == "refuse", q
        assert plan.stage == "rule", q                     # 트랩 방어는 LLM 없이 확정


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
    q = "삼성전자랑 SK하이닉스를 둘 다 담고 있는 ETF 중에서 총보수가 제일 낮은 건 뭐야?"
    plan_a, needs_llm = route_stage_a(q, index, POLICY, TODAY)
    assert needs_llm                                        # 교집합은 Stage B 소관
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
    assert "삼성전자" in out["answer"] and "2026-07-10" in out["answer"]


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
