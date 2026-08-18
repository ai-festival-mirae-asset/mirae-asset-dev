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
