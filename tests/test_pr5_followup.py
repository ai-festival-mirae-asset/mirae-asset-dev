# -*- coding: utf-8 -*-
"""Codex 개선 바퀴(PR #5) 병합 후속 2건의 회귀 잠금 (9/6).

PR 이 넣은 '조회 결과 0건' 표식 근거(run_template 가 행이 없을 때 남기는 Evidence)가 생성기 입력을 바꿔
HCX 문장이 흔들렸다 — 리더 관문(HCX 포함 재채점) 실측:
  · v1 L-25 "KOSPI200을 벤치마크로 삼는 펀드": 표기 변형 4회 조회 중 0건인 2개의 표식이 근거1로 앞서자
    HCX 가 상품명을 띄어 쓰고 줄여 검사표와 어긋남(3회 연속 실패, main 은 통과).
  · v1 L-06 "만기가 없는 영구채도 있어?": 0건 표식의 문구가 바뀌자 HCX 가 번호 목록 형태로 거절문을 써서
    자유 거절 안전망(목록이면 거절 아님으로 봄)이 비켜 가 과잉 거절(3회 중 2회).
수정: ① 다른 조회가 실제 행을 냈으면 '(0건)' 표식을 근거에서 뺀다(전부 0건이면 남김 — 출처 표시 유지)
     ② 규칙 경로 + 미등록 이름 없음 + 실행한 조회 전부 0건이면 HCX 생성을 생략하고 규칙 요약을 쓴다.
"""
import datetime

import duckdb
import pytest

from engine.answer_service import _COL_DISPLAY, _fmt_row, answer_question, serialize_answer
from engine.channels import RuntimeContext
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)


@pytest.fixture(scope="module")
def con():
    return duckdb.connect(DB_PATH_DEFAULT, read_only=True)


@pytest.fixture(scope="module")
def ctx(con):
    return RuntimeContext(con=con, index=build_entity_index(con))


def _ask(ctx, q, generator=None):
    out = answer_question(q, ctx, today=TODAY, generator=generator)
    return out if isinstance(out, dict) else serialize_answer(out)


def _fake_numbered_refusal(question, plan, result, verdict):
    # HCX 가 실제로 낸 형태(9/6 L-06 실측): 번호 목록 모양의 자기 말 거절
    return "답변합니다.\n\n1. 현재 제공된 데이터에서는 확인할 수 없습니다.\n2. 조건에 맞는 항목이 없습니다."


def test_zero_row_markers_are_dropped_when_another_call_returned_rows(ctx):
    ser = _ask(ctx, "KOSPI200을 벤치마크로 삼는 펀드 알려줘")
    rc = ser["retrieved_context"]
    assert "IBK" in rc and "PRFD01N001" in rc                 # 실제 행 근거는 그대로
    assert "(0건)" not in rc                                   # 0건 표식은 생성기·근거에서 제외
    assert not rc.startswith("[근거1 | 출처: PRFD01N001 | 키: (0건)")


def test_all_zero_rows_keep_marker_for_source(ctx):
    ser = _ask(ctx, "만기가 없는 영구채도 있어?")
    rc = ser["retrieved_context"]
    assert "(0건)" in rc and "PRBD01N001" in rc               # 어느 원천을 찾아봤는지는 남긴다
    assert "결과 0건" in ser["answer"]


def test_all_zero_rows_skip_generation_and_stay_deterministic(ctx):
    ser = _ask(ctx, "만기가 없는 영구채도 있어?", generator=_fake_numbered_refusal)
    assert "결과 0건" in ser["answer"]
    assert not ser["answer"].startswith("답변합니다")
    assert "HCX-005 생성" not in ser["think_trace"]          # 생성기를 부르지 않았다


@pytest.mark.parametrize("q", ["영구채 목록 보여줘", "영구채 있어?"])
def test_zero_rows_variants_are_fact_answers_not_refusals(ctx, q):
    # 영구채는 재배포본에서 소멸(0건) — 규칙 경로의 0건은 '없음' 사실 답변이어야 한다(거절도, 자기 말 거절도 아님)
    ser = _ask(ctx, q, generator=_fake_numbered_refusal)
    assert "결과 0건" in ser["answer"], ser["answer"][:200]
    assert not ser["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다")
    assert not ser["answer"].startswith("답변합니다")


def test_generation_still_used_when_rows_exist(ctx):
    calls = []

    def gen(question, plan, result, verdict):
        calls.append(question)
        return "1. KODEX 200\n2. TIGER 미국S&P500"

    ser = _ask(ctx, "순자산총액 기준으로 국내 ETF 상위 5개 알려줘", generator=gen)
    assert calls, "행이 있는 질문은 종전대로 생성기를 쓴다"
    assert "KODEX 200" in ser["answer"]


def test_public_private_column_has_korean_label():
    assert _COL_DISPLAY["prvo_pbff_desc"][0] == "공모/사모"
    text = _fmt_row({"itm_abrv_nm": "신영신종MMF4-12", "prvo_pbff_desc": "공모"})
    assert "공모/사모 공모" in text and "prvo_pbff_desc" not in text


# ---------------------------------------------------------------------------
# 3. 함정(행위 요청) — Codex 바퀴 CA-27 "환불 처리해줘요"가 규칙을 비껴 HCX 자유 거절에 기대다 한 번 함정 오답
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "내가 산 펀드 지금 환불 처리해줘요.",
    "어제 산 KODEX 200 환불해줘",
    "이 ETF 환불 좀 부탁드립니다",
    "펀드 환불 가능한가요?",
])
def test_refund_requests_are_rule_refusals(ctx, q):
    from engine.policy import load_policy
    from engine.router import route
    plan = route(q, ctx.index, policy=load_policy(), today=TODAY)
    assert plan.intent == "action_request" and plan.behavior_hint == "refuse"
    ser = _ask(ctx, q, generator=lambda *a: "죄송합니다. 환불 처리 여부를 확인할 수 없습니다.\n\n근거·기준일: 결과 0건")
    assert ser["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다")


def test_zero_result_echo_is_not_a_list():
    from engine.answer_service import _looks_like_free_refusal
    echo = "죄송합니다. 현재 제공된 정보로는 확인할 수 없습니다.\n\n근거·기준일: [검증 통과 근거] — 결과 0건"
    assert _looks_like_free_refusal(echo) is True            # '결과 0건'은 목록이 아니다 → 통일 대상
    assert _looks_like_free_refusal("죄송합니다. 확인할 수 없습니다.\n결과 3건\n  1. A\n  2. B") is False
    assert _looks_like_free_refusal("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다.\n- 사유: x") is False


def test_fallback_free_refusal_with_zero_echo_is_unified(ctx):
    # 규칙을 비껴 폴백으로 간 행위 요청이라도, HCX 가 '결과 0건'을 옮겨 쓴 자유 거절문은 정해진 거절문으로 통일된다
    def gen(question, plan, result, verdict):
        return "죄송합니다. 현재 제공된 정보로는 요청하신 내용을 확인할 수 없습니다.\n\n근거·기준일: [검증 통과 근거] — 결과 0건"
    ser = _ask(ctx, "내 계좌 잔고 좀 보여주삼", generator=gen)
    assert ser["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다") or "결과 0건" in ser["answer"]
    assert not ser["answer"].startswith("죄송합니다")
