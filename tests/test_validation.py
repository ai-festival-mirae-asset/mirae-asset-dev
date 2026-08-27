# -*- coding: utf-8 -*-
"""구현 순서 ④ 테스트 — 답변 검증(5중 검문소) + 함정 문항 상시 자동 방어.

핵심: 모의고사 파일(evalset_v1.jsonl)에서 "거절이 정답"인 문항 16개(함정 15 + M-29)를
읽어 실제 응답 경로 전체에 통과시킨다. 이 테스트가 스위트에 있는 한, 어떤 코드
수정이 함정 방어를 깨뜨리면 즉시 실패로 드러난다(회귀 테스트).
반대 방향(과잉 거절 — 정상 질문을 거절해버리는 사고)도 표본으로 잠근다.
"""
import io
import json
import os

import pytest

from engine.answer_service import answer_question
from engine.channels import RuntimeContext, execute_plan
from engine.policy import load_policy
from engine.router import RoutePlan, route
from engine.validation import (gate_field_availability, gate_time_boundary,
                               gate_value_domain, validate_answerability)
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALSET = os.path.join(ROOT, "evalset", "evalset_v1.jsonl")
DB_EXISTS = os.path.exists(DB_PATH_DEFAULT)
needs_db = pytest.mark.skipif(not DB_EXISTS, reason="products.duckdb 미생성 — load_duckdb.py 선행")

POLICY = load_policy()
TODAY = __import__("datetime").date(2026, 8, 14)
REFUSE_HEAD = "요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"


def load_refuse_questions():
    """모의고사 파일에서 '거절이 정답'인 문항을 읽는다 — 파일과 테스트가 자동 동기."""
    rows = []
    with io.open(EVALSET, "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["behavior"] == "refuse":
                rows.append((row["id"], row["question"]))
    return rows


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


# ---------------------------------------------------------------------------
# 1. 검문소 단위 검사 (데이터 불필요 — 순수 로직)
# ---------------------------------------------------------------------------

def test_gate_value_domain():
    assert gate_value_domain("신용등급이 AAAA인 채권을 찾아줘", POLICY).verdict == "refuse"
    assert gate_value_domain("위험등급 0등급인 ETF", POLICY).verdict == "refuse"
    assert gate_value_domain("총보수가 마이너스인 ETF 있어?", POLICY).verdict == "refuse"
    assert gate_value_domain("테슬라 코인에 투자하는 펀드", POLICY).verdict == "refuse"
    assert gate_value_domain("신용등급 AA 이상인 채권", POLICY).verdict == "pass"


def test_gate_time_boundary():
    assert gate_time_boundary("삼성전자 지금 주가가 얼마야?").verdict == "refuse"
    assert gate_time_boundary("2026년 9월에 새로 상장한 ETF 알려줘").verdict == "refuse"
    assert gate_time_boundary("2026년 8월에 새로 상장한 ETF 알려줘").verdict == "pass"   # 8/22 기준일 이내(8/27 재배포)
    assert gate_time_boundary("TIGER 200의 1년 전 구성종목이랑 지금을 비교해줘").verdict == "refuse"
    assert gate_time_boundary("2027년에 만기가 돌아오는 회사채 ETF 있어?").verdict == "pass"


def test_gate_field_availability():
    assert gate_field_availability("해외 ETF를 위험등급 1등급만 골라서 보여줘").verdict == "refuse"
    assert gate_field_availability("공모펀드 중에서 총보수 제일 낮은 것 알려줘").verdict == "refuse"
    assert gate_field_availability("타사에서 판매 중인 공모펀드 알려줘").verdict == "refuse"
    assert gate_field_availability("위험등급 낮은 국내 ETF 보여줘").verdict == "pass"
    assert gate_field_availability("회사채 ETF의 총보수 알려줘").verdict == "pass"   # ETF 보수는 있음


# ---------------------------------------------------------------------------
# 2. 함정 16문항 상시 자동 방어 (모의고사 파일에서 직접 읽음 — 회귀 고정)
# ---------------------------------------------------------------------------

@needs_db
@pytest.mark.parametrize("qid,question",
                         load_refuse_questions() or [("없음", "빈 파일")],
                         ids=lambda v: v if isinstance(v, str) and "-" in v else None)
def test_refuse_questions_are_refused(ctx, qid, question):
    """거절이 정답인 문항 전부: 정해진 거절문으로 답하고, 검증 근거를 남겨야 한다."""
    out = answer_question(question, ctx, question_id=qid, today=TODAY)
    assert out["answer"].startswith(REFUSE_HEAD), f"{qid}: 거절해야 하는데 답변함 → {out['answer'][:80]}"
    assert "채널: validation" in out["retrieved_context"], f"{qid}: 검증 근거 누락"
    assert all(isinstance(v, str) for v in out.values())


@needs_db
def test_trap_count_matches_expectation():
    """모의고사의 거절 문항 수가 바뀌면(문항 추가·수정) 여기서 알아챈다."""
    assert len(load_refuse_questions()) == 16          # 함정 15 + M-29


# ---------------------------------------------------------------------------
# 3. 과잉 거절 방지 — 정상 질문은 거절하면 안 된다 (반대 방향 회귀)
# ---------------------------------------------------------------------------

NORMAL_SAMPLES = [
    ("L-01", "현재 판매 가능한 원화채권 중 신용등급 AA 이상인 종목을 알려줘"),
    ("L-09", "TIGER 200의 운용사가 어디야?"),
    ("L-15", "레버리지 ETF 찾아줘"),
    ("M-13", "반도체 산업에 집중 투자하는 해외 ETF는?"),
    ("M-17", "2027년에 만기가 돌아오는 회사채 ETF 있어?"),
    ("M-23", "중국 본토 CSI300 지수를 따라가는 상품 있어?"),
    ("M-26", "휴머노이드 로봇 산업에 투자하는 ETF 있어?"),
]


@needs_db
@pytest.mark.parametrize("qid,question", NORMAL_SAMPLES, ids=[q[0] for q in NORMAL_SAMPLES])
def test_normal_questions_not_refused(ctx, qid, question):
    out = answer_question(question, ctx, question_id=qid, today=TODAY)
    assert not out["answer"].startswith(REFUSE_HEAD), f"{qid}: 정상 질문을 과잉 거절 → {out['answer'][:120]}"


@needs_db
def test_variant_names_suggest_but_dont_pretend(ctx):
    """T-09형: '국민성장펀드 2호'는 없다 — 거절하되 유사명 안내(존재 근거 아님)까지만."""
    out = answer_question("국민성장펀드 2호 수익률 알려줘", ctx, today=TODAY)
    assert out["answer"].startswith(REFUSE_HEAD)
    assert "2호" in out["answer"] and "부분 일치" in out["answer"]
    assert "국민성장" in out["answer"]                  # 유사명 안내가 실제로 붙는다


@needs_db
def test_trap_terms_have_zero_meaningful_matches(index):
    """기록 고정: 함정 단어들은 '의미 있는 부분 일치'가 0건(거절의 적극적 증거).

    실측(8/14): 'kimi'는 해외 지수명 'MSCI Denmark IMI'의 공백 제거 문자열
    (denmar-kimi)에 우연히 들어간다 — 원문 표기 대조(token_matches)가 이런
    우연 겹침을 걸러내야 함정 방어가 성립한다. 'GPT'는 실제 티커(GPT ETF)와
    일치하므로 단독으로는 함정 단어가 아니며, '챗GPT'가 한 단어로 잡혀야 한다.
    """
    from pipeline.entity_index import token_matches
    for term in ("kimi", "챗GPT", "타임머신"):
        assert token_matches(index, term, limit=1) == [], term
    # 우연 겹침의 실존 증거 — 정규화 검색은 kimi 를 찾지만(우연), 의미 필터는 걸러낸다
    assert index.search("kimi", limit=1) != []


@needs_db
def test_refusal_does_not_resurface_accidental_match_as_suggestion(ctx):
    """8/18 실측: 거절문의 '혹시 다음 상품…' 안내에 우연 겹침(MSCI Denmark IMI)이
    되살아나던 문제 — 안내는 질의어가 원문 표기 그대로 보이는 '상품'만 허용한다."""
    out = answer_question("Kimi 관련 투자상품 있어?", ctx, today=TODAY)
    assert out["answer"].startswith(REFUSE_HEAD)
    assert "Denmark" not in out["answer"] and "혹시" not in out["answer"]
    # 값 도메인 함정: 라우터 노트와 검문소 사유가 같은 문구라 사유가 한 줄만 나온다
    out2 = answer_question("신용등급 AAAA인 채권 찾아줘", ctx, today=TODAY)
    assert out2["answer"].startswith(REFUSE_HEAD)
    assert out2["answer"].count("- 사유:") == 1


# ---------------------------------------------------------------------------
# 4. 충분성 검사(partial) — 커버리지 낮은 질문은 한계 문구가 강제된다
# ---------------------------------------------------------------------------

@needs_db
def test_low_coverage_forces_partial_note(ctx, index):
    q = "총보수가 0.1% 이하인 국내 ETF 알려줘"          # 보수 값 보유는 전체의 12.5%뿐
    plan = route(q, index, policy=POLICY, today=TODAY)
    result = execute_plan(plan, ctx)
    verdict = validate_answerability(q, plan, result, index, POLICY)
    assert verdict.behavior == "partial"
    assert any("값 보유" in r for r in verdict.reasons)
    out = answer_question(q, ctx, today=TODAY)
    assert not out["answer"].startswith(REFUSE_HEAD)   # 거절 아님 — 답하되
    assert "값 보유" in out["answer"] or "커버리지" in out["answer"]   # 한계 명시


@needs_db
def test_verdict_gates_recorded_in_trace(ctx):
    out = answer_question("TIGER 200의 운용사가 어디야?", ctx, today=TODAY)
    assert "검문[value] pass" in out["think_trace"]
    assert "검문[existence] pass" in out["think_trace"]
    assert "behavior=answer" in out["think_trace"]
