# -*- coding: utf-8 -*-
"""구현 순서 ⑤ 테스트 — 답변 생성기(사후 대조)와 5필드 직렬화.

핵심 검사: AI 가 근거에 없는 상품명·숫자를 답변에 끼워 넣으면(지어냄)
사후 대조가 그 줄을 지우는가. 생성이 실패하면 규칙 요약으로 폴백하는가.
"""
import os

import pytest

from engine.answer_service import _draft_answer, answer_question, serialize_answer
from engine.channels import ChannelOutcome, ExecutionResult, RuntimeContext
from engine.generator import build_generation_messages, post_check_answer
from engine.policy import load_policy
from engine.router import RoutePlan
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index
from pipeline.evidence import Evidence

DB_EXISTS = os.path.exists(DB_PATH_DEFAULT)
needs_db = pytest.mark.skipif(not DB_EXISTS, reason="products.duckdb 미생성 — load_duckdb.py 선행")
live_llm = pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1",
                              reason="라이브 LLM 테스트는 RUN_LIVE_LLM=1 로만(비용)")

POLICY = load_policy()
TODAY = __import__("datetime").date(2026, 8, 14)


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
def ctx(con, index):
    return RuntimeContext(con=con, index=index, policy=POLICY)


def _ev(**fields):
    return Evidence(source="PREF01N001", source_id="KR7102110004", channel="sql",
                    as_of="2026-07-11", fields=fields)


def test_zero_sql_result_is_stated_explicitly():
    plan = RoutePlan(intent="constituent_intersection_low_fee")
    result = ExecutionResult([ChannelOutcome("sql", "constituent_intersection_low_fee", rows=[])])
    answer = _draft_answer(plan, result)
    assert "constituent_intersection_low_fee" in answer and "0건" in answer


# ---------------------------------------------------------------------------
# 1. 사후 대조 (순수 — 숫자 검사는 데이터 불필요)
# ---------------------------------------------------------------------------

def test_post_check_removes_fabricated_number():
    evidences = [_ev(pd_abrv_nm="TIGER 200", weight_pct=33.03)]
    text = "1. TIGER 200의 비중은 33.03%입니다.\n2. 수수료는 99.99%로 매우 높습니다."
    clean, removed = post_check_answer(text, evidences, "TIGER 200 비중 알려줘")
    assert "33.03" in clean and "99.99" not in clean
    assert len(removed) == 1 and "99.99" in removed[0][1]


def test_post_check_allows_rounded_numbers():
    evidences = [_ev(weight_pct=33.03)]
    clean, removed = post_check_answer("비중은 약 33%입니다.", evidences, "질문")
    assert clean and not removed                       # 33 은 33.03 의 반올림 — 허용


def test_post_check_all_removed_returns_none():
    evidences = [_ev(pd_abrv_nm="TIGER 200")]
    clean, removed = post_check_answer("존재하지 않는 수치 77.77%가 있습니다.", evidences, "질문")
    assert clean is None and removed


@needs_db
def test_post_check_removes_foreign_product_name(index):
    """근거에 없는 '실존 상품명'(KODEX 200)을 끼워 넣으면 그 줄이 삭제된다."""
    evidences = [_ev(pd_abrv_nm="TIGER 200", pd_net_tamt=100)]
    text = "1. TIGER 200 순자산은 100입니다.\n2. 참고로 KODEX 200도 유명합니다."
    clean, removed = post_check_answer(text, evidences, "TIGER 200 알려줘", index=index)
    assert "TIGER 200" in clean and "KODEX 200" not in clean
    assert any("근거 밖 이름" in r[1] for r in removed)


# ---------------------------------------------------------------------------
# 2. 생성기 주입 흐름 — 모의 생성기로 지어냄·실패 시나리오 재현
# ---------------------------------------------------------------------------

@needs_db
def test_generator_output_is_post_checked(ctx):
    def fake_generator(question, plan, result, verdict):
        return ("순자산총액 1위는 근거의 상품입니다.\n"
                "제 생각에 내년 수익률은 25.55%로 예상됩니다.")   # 근거 밖 숫자 — 지어냄
    out = answer_question("순자산총액 기준으로 국내 ETF 상위 5개 알려줘", ctx,
                          today=TODAY, generator=fake_generator)
    assert "25.55" not in out["answer"]                # 지어낸 줄이 지워졌다
    assert "사후 대조" in out["think_trace"]


@needs_db
def test_generator_failure_falls_back_to_stub(ctx):
    out = answer_question("순자산총액 기준으로 국내 ETF 상위 5개 알려줘", ctx,
                          today=TODAY, generator=lambda *a: None)
    assert "KODEX 200" in out["answer"]                # 규칙 요약 폴백이 동작
    assert "폴백" in out["think_trace"]


@needs_db
def test_generator_not_called_for_refusals(ctx):
    called = []
    def spy_generator(*a):
        called.append(1)
        return "이 문장은 나오면 안 됩니다"
    out = answer_question("kimi 관련 투자 상품 있어?", ctx, today=TODAY,
                          generator=spy_generator)
    assert not called                                  # 거절 경로는 생성기를 부르지 않는다
    assert out["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다")


@needs_db
def test_unstructured_partial_does_not_expose_semantic_candidates(ctx):
    out = answer_question("국민성장펀드의 구조와 투자전략 동향을 찾아서 알려줘",
                          ctx, today=TODAY)
    assert "비정형 자료" in out["answer"]
    assert "behavior=partial" in out["think_trace"]


@needs_db
def test_notes_are_forced_into_generated_answer(ctx):
    def bare_generator(question, plan, result, verdict):
        return "조건에 맞는 채권 목록입니다."            # 노트·기준일 누락 생성
    out = answer_question("현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘",
                          ctx, today=TODAY, generator=bare_generator)
    assert "AA- 미포함" in out["answer"]               # 해석 노트 강제 부착
    assert "기준일" in out["answer"]


# ---------------------------------------------------------------------------
# 3. 프롬프트·직렬화
# ---------------------------------------------------------------------------

def test_generation_messages_contain_rules_and_context():
    from engine.router import RoutePlan
    from engine.channels import ExecutionResult, ChannelOutcome
    from engine.validation import Verdict
    plan = RoutePlan(intent="test", notes=["해석 기준 한 줄"])
    result = ExecutionResult(outcomes=[ChannelOutcome("sql", "t", rows=[],
                                                      evidences=[_ev(a=1)])])
    msgs = build_generation_messages("질문", plan, result, Verdict("answer"))
    assert msgs[0]["role"] == "system" and "근거에 없는" in msgs[0]["content"]
    assert "1등급이 매우 높은 위험" in msgs[0]["content"]
    assert "[근거1" in msgs[1]["content"] and "해석 기준 한 줄" in msgs[1]["content"]


def test_serialize_answer_all_strings_nonempty():
    out = serialize_answer("", "", [], "", "")
    assert set(out) == {"question_id", "question", "retrieved_context",
                        "think_trace", "answer"}
    assert all(isinstance(v, str) and v != "" or k in ("question_id", "question")
               for k, v in out.items())


# ---------------------------------------------------------------------------
# 4. 라이브 (RUN_LIVE_LLM=1 일 때만 — HCX-005 생성 1콜)
# ---------------------------------------------------------------------------

@needs_db
@live_llm
def test_live_generation_l01(ctx):
    from engine.generator import make_hcx_generator
    out = answer_question("현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘",
                          ctx, today=TODAY, generator=make_hcx_generator())
    assert not out["answer"].startswith("요청하신 내용은")
    assert "AA" in out["answer"] and "기준일" in out["answer"]
    assert "HCX-005 생성" in out["think_trace"] or "폴백" in out["think_trace"]


# ---------------------------------------------------------------------------
# 5. ⑧-3 (8/19) — 사후 대조가 맞는 줄까지 지우던 사례 · HCX 라우터 파라미터 의미 검증
# ---------------------------------------------------------------------------

def test_post_check_allows_unit_scaled_amounts_and_rank_words():
    """큰 금액의 조·억 환산('약 28.4조원')과 순위 표기('1위')는 근거 밖 숫자가 아니다(L-04·M-02·H-29 유형)."""
    evidences = [_ev(pd_abrv_nm="KODEX 200", pd_net_tamt="28359162282520.0"),
                 Evidence(source="PREF01N001", source_id="KR7102110004", channel="sql", as_of="2026-07-11",
                          fields={"pd_abrv_nm": "TIGER 200", "pd_net_tamt": "11278564148232.0"})]
    text = ("1위 KODEX 200 — 순자산 약 28.4조원 [근거1]\n"
            "2위 TIGER 200 — 순자산 약 11.3조원 [근거2]\n"
            "3위 지어낸ETF — 순자산 약 9.9조원")
    clean, removed = post_check_answer(text, evidences, "순자산 상위 알려줘")
    assert "28.4조" in clean and "11.3조" in clean
    assert len(removed) == 1 and "9.9조" in removed[0][1]
    # 억 단위·정수 반올림도 같은 규칙 — 28조 3,591억 (283591 억)
    clean2, removed2 = post_check_answer("KODEX 200 순자산은 283,592억원입니다.", evidences, "질문")
    assert clean2 and not removed2
    # 단위가 붙어도 값이 다르면 여전히 지운다
    clean3, removed3 = post_check_answer("KODEX 200 순자산은 30.1조원입니다.", evidences, "질문")
    assert clean3 is None and removed3


@needs_db
def test_post_check_keeps_name_variants_of_evidence_products(con, index):
    """근거에는 약칭(KODEX 200TR)만 있고 답변이 정식 명칭(…Total Return…)을 써도 같은 상품(키 일치)이면 남긴다."""
    key, full_name = con.execute(
        "SELECT pd_itm_no, pd_nm FROM kr_etp WHERE pd_abrv_nm = 'KODEX 200TR' LIMIT 1").fetchone()
    ev = Evidence(source="PREF01N001", source_id=key, channel="sql", as_of="2026-07-11",
                  fields={"pd_abrv_nm": "KODEX 200TR", "pd_itm_no": key})
    # 상품 키가 근거 안에 있으므로 정식 명칭 표기도 허용된다
    text = f"{full_name}이 해당합니다."
    clean, removed = post_check_answer(text, [ev], "질문", index=index)
    assert clean == text and not removed
    # 근거에 전혀 없는 실존 상품명은 여전히 지운다
    text2 = "TIGER 반도체TOP10 도 해당합니다."
    clean2, removed2 = post_check_answer(text2, [ev], "질문", index=index)
    assert clean2 is None and "근거 밖 이름" in removed2[0][1]


def test_generation_prompt_has_three_part_format_and_low_temperature():
    from engine.generator import GENERATION_SEED, GENERATION_TEMPERATURE
    plan = RoutePlan(intent="x", notes=["노트1"])
    result = ExecutionResult([ChannelOutcome("sql", "etp_top_aum", rows=[])])
    msgs = build_generation_messages("질문", plan, result, type("V", (), {"evidences": []})())
    system = msgs[0]["content"]
    assert "3단" in system and "결론" in system and "근거·기준일" in system
    assert "환산 표기" in system and "0건이면" in system      # 금액은 근거의 환산 표기(…억원)를 옮겨 쓴다
    from engine.sql_templates import krw_readable
    assert krw_readable("28359162282520.0") == "28.4조원" and krw_readable(346687108988) == "3,467억원"
    assert krw_readable("12345678") == "1,235만원" and krw_readable("abc") is None
    assert GENERATION_TEMPERATURE <= 0.3 and GENERATION_SEED > 0


def test_llm_router_param_coercion_and_rejection():
    """HCX 라우터 플랜: 이름→키 변환, 숫자 자리의 글자 거부, 범위 밖 거부, 날짜 형식 검사."""
    from engine.router_llm import coerce_graph_query, coerce_llm_params
    from pipeline.entity_index import EntityRef
    partial = RoutePlan(intent="unresolved", entities=[
        ("삼성전자", [EntityRef("constituent", "005930", "삼성전자", "KRX-PDF")]),
        ("tiger200", [EntityRef("product_kr_etp", "KR7102110004", "TIGER 200", "PREF01N001")]),
    ])
    fixed = coerce_llm_params("constituent_holders", {"code": "삼성전자", "limit": "10"}, partial)
    assert fixed == {"code": "005930", "limit": 10}
    fixed2 = coerce_llm_params("etp_detail", {"pd_itm_no": "TIGER 200"}, partial)
    assert fixed2 == {"pd_itm_no": "KR7102110004"}
    with pytest.raises(ValueError, match="grounded"):
        coerce_llm_params("constituent_holders", {"code": "없는 종목 이름", "limit": 5}, partial)
    with pytest.raises(ValueError, match="숫자"):
        coerce_llm_params("bond_maturing_within", {"as_of_date": "2026-08-19", "until": "2029-08-19",
                                                   "min_coupon": "만기 3년 이하", "limit": 20}, partial)
    with pytest.raises(ValueError, match="범위"):
        coerce_llm_params("etp_filter_risk", {"instrument_type": "ETF", "min_grade": 0, "max_grade": 9,
                                              "limit": 20}, partial)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        coerce_llm_params("bond_maturing_within", {"as_of_date": "지금", "until": "2029-08-19", "limit": 5}, partial)
    assert coerce_graph_query("holding_etfs", "삼성전자", partial) == "005930"
    assert coerce_graph_query("holding_etfs", "US0378331005", partial) == "US0378331005"
    assert coerce_graph_query("product_info", "TIGER 200", partial) == "TIGER 200"
    with pytest.raises(ValueError):
        coerce_graph_query("holding_etfs", "모르는 회사", partial)


def test_coerce_constituent_holders_semantic_key_check():
    """8/22 H-17 실측: constituent_holders.code 에 ETF 키·지수명이 들어가면 수리 콜을 유도한다."""
    from engine.router_llm import coerce_llm_params
    from pipeline.entity_index import EntityRef
    partial = RoutePlan(intent="unresolved", entities=[
        ("kodex msci korea", [EntityRef("product_kr_etp", "KR7156080004", "KODEX MSCI KOREA", "PREF01N001")]),
        ("삼성전자", [EntityRef("constituent", "005930", "삼성전자", "KRX-PDF")]),
    ])
    with pytest.raises(ValueError, match="constituent_top_weights"):
        coerce_llm_params("constituent_holders", {"code": "KR7156080004", "limit": 100}, partial)
    with pytest.raises(ValueError, match="종목 코드"):
        coerce_llm_params("constituent_holders", {"code": "KOSPI200", "limit": 100}, partial)
    fixed = coerce_llm_params("constituent_holders", {"code": "005930", "limit": 30}, partial)
    assert fixed["code"] == "005930"


def test_respace_names_restores_official_spacing():
    """8/22 M-18 실측: 생성기가 상품명에 공백을 끼워 넣으면('코스닥 150') 근거 정식 표기로 복원한다."""
    from engine.generator import respace_names
    official = "미래에셋 TIGER 코스닥150인버스증권상장지수투자신탁(주식-파생형)"
    ev = Evidence(source="PREF01N001", source_id="KR7XXXX0000", channel="sql", as_of="2026-07-11",
                  fields={"pd_nm": official})
    text = "1. 미래에셋 TIGER 코스닥150 인버스 증권상장지수투자신탁 (주식-파생형), [근거1]"
    fixed, corrections = respace_names(text, [ev])
    assert official in fixed and corrections == [official]
    # 정식 표기가 이미 있으면 건드리지 않는다
    same, corr2 = respace_names(f"1. {official} [근거1]", [ev])
    assert same == f"1. {official} [근거1]" and not corr2
    # 사후 대조 전체 경로에서도 '표기 정정'으로 기록되고 줄은 살아남는다
    clean, removed = post_check_answer(text, [ev], "코스닥150 인버스 상품 알려줘")
    assert clean is not None and official in clean
    assert any("띄어쓰기" in r for _s, r in removed)


def test_llm_zero_row_plan_gets_non_assertive_note(ctx):
    """HCX 라우터 계획이 0건이면 '없다' 단정 대신 해석 차이 노트가 강제되고, 근거 블록도 비지 않는다(8/22)."""
    plan = RoutePlan(intent="llm_plan", stage="llm")
    plan.calls.append(__import__("engine.router", fromlist=["ChannelCall"]).ChannelCall(
        "sql", "etp_name_search", {"pattern_raw": "존재하지않는이름", "limit": 5}))
    def fake_router(question, partial):
        return plan
    q = "표면금리와 순자산을 곱한 값이 가장 큰 상품은?"     # 규칙이 못 정하는 질문 → Stage B(가짜 라우터)
    out = answer_question(q, ctx, today=TODAY, llm_router=fake_router)
    assert "stage=llm" in out["think_trace"]
    assert "단정하지 않음" in out["answer"]
    # 근거 0개 방지망(H-17 실측): 0건이어도 '무엇을 찾아봤는지'가 근거로 남는다
    assert out["retrieved_context"] != "(근거 없음)"
    assert "조회 기록" in out["retrieved_context"]


def test_wall_clock_guard_cuts_stalled_calls():
    """8/19 실측(HCX 호출 1건 368초 정지 — DNS 조회 멈춤): httpx timeout 밖의 멈춤도 timeout+2초 안에 끊는다."""
    import time
    import httpx
    from agent import net_guard
    from agent.clova_client import ClovaChatClient
    from agent.net_guard import WallClockTimeout, call_with_wall_clock

    def sleepy():
        time.sleep(30)
        return "늦은 응답"
    t0 = time.monotonic()
    with pytest.raises(WallClockTimeout):
        call_with_wall_clock(sleepy, 0.5)
    assert time.monotonic() - t0 < 5                       # 0.5 + 2초 근처에서 끊긴다(30초를 기다리지 않음)

    # 클라이언트 경유: 멈추는 transport 로도 chat() 이 timeout+2초 안에 예외로 끝나고 감사 로그에 남는다
    def stalled_handler(request):
        time.sleep(30)
        return httpx.Response(200, json={"status": {"code": "20000"}, "result": {"message": {"content": "x"}}})
    client = ClovaChatClient("HCX-005", api_key="test-key", transport=httpx.MockTransport(stalled_handler),
                             timeout=0.5, audit_path=os.path.join(os.path.dirname(__file__), "..", "storage",
                                                                    "output", "_test_audit.jsonl"))
    t1 = time.monotonic()
    with pytest.raises(Exception):
        client.chat([{"role": "user", "content": "안녕"}])
    assert time.monotonic() - t1 < 6
    assert net_guard.WALL_MARGIN_SEC == 2.0


def test_autocorrect_single_char_name_typos():
    """생성기의 이름 한 글자 오기('퀀타매트릭스'→'퀸타매트릭스', L-06 실측)는 지우지 않고 근거 표기로 되돌린다."""
    from engine.generator import autocorrect_names
    ev = Evidence(source="PRBD01N001", source_id="KR6317691FC9", channel="sql", as_of="2026-07-11",
                  fields={"PD_NM": "퀀타매트릭스 3CB(신종)(사모/전환/콜/후)", "PD_ABRV_NM": "퀀타매트릭스3CB(신종)"})
    text = "2. 퀸타매트릭스 3CB(신종)(사모/전환/콜/후) — 영구채입니다."
    fixed, corrections = autocorrect_names(text, [ev])
    assert "퀀타매트릭스 3CB(신종)(사모/전환/콜/후)" in fixed and len(corrections) == 1
    # 사후 대조 전체 경로에서도 정정되고, 정정은 '삭제'로 세지 않는다
    clean, removed = post_check_answer(text, [ev], "만기가 없는 영구채도 있어?")
    assert "퀀타매트릭스" in clean and all(r.startswith("표기 정정") for _s, r in removed)
    # 두 글자 이상 다르거나 짧은 이름은 건드리지 않는다(다른 상품으로 바꿔치기 방지)
    ev2 = Evidence(source="PREF01N001", source_id="X", channel="sql", as_of="2026-07-11",
                   fields={"pd_abrv_nm": "TIGER 200"})
    same, corr2 = autocorrect_names("TIGER 300 은 다른 상품", [ev2])
    assert same == "TIGER 300 은 다른 상품" and not corr2
