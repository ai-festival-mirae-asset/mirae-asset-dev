# -*- coding: utf-8 -*-
"""
답변 생성기 — HCX-005 가 근거를 문장으로 다듬고, 사후 대조로 지어냄을 지운다 (순서 ⑤, 8/14).

무엇: 검증(순서 ④)을 통과한 근거들만 HCX-005 에게 주고 최종 답변 문장을 쓰게 한다.
      생성이 끝나면 **사후 대조(post-check)**: 답변에 등장한 상품명·숫자를 근거와
      대조해서, 근거에 없는 것이 섞인 줄을 삭제한다.
왜  : AI 가 학습으로 아는 지식을 답에 끼워 넣는 것이 대회의 대표 감점 경로다.
      ① 프롬프트로 금지하고 ② 사후 대조로 지우는 이중 방어. 생성이 실패하면
      규칙 기반 요약(스텁)으로 폴백 — 응답이 끊기는 일은 없다.
경계: 거절(refuse) 답변은 이 모듈을 거치지 않는다 — 정해진 템플릿 문구만 사용
      (함정 경로에서 AI 가 말할 기회 자체를 없애는 확정 설계).
구조 주의: 테스트가 순수 함수(post_check_answer)를 import 한다 — import 부작용 금지.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.entity_index import norm_name                   # noqa: E402
from pipeline.evidence import to_context_string               # noqa: E402

MAX_GENERATION_TOKENS = 1024      # 응답 상한 — 길이 제한은 없지만 과도하면 초과분 미평가


# ---------------------------------------------------------------------------
# 프롬프트 구성
# ---------------------------------------------------------------------------

def build_generation_messages(question, plan, result, verdict):
    """근거 주입 + '근거 밖 서술 금지' 지시 — 검증 통과 근거만 들어간다."""
    context = to_context_string(list(result.evidences) + list(verdict.evidences))
    notes = "\n".join(f"- {n}" for n in plan.notes) or "- (없음)"
    system = (
        "너는 금융상품 데이터 안내 도우미다. 아래 규칙을 반드시 지켜라.\n"
        "1) 답변의 모든 사실(상품명·숫자·날짜)은 반드시 [근거N] 블록 안의 내용에서만 가져온다. "
        "근거에 없는 상품·수치·설명은 한 글자도 쓰지 않는다(네가 아는 지식 사용 금지).\n"
        "2) 수치를 인용할 때는 근거의 값을 그대로 쓰고, 어느 근거인지 [근거N] 표기를 붙인다.\n"
        "3) '해석·한계 노트'의 내용(해석 기준·데이터 한계)은 답변에 반드시 포함한다.\n"
        "4) 미래 전망·단정적 투자 추천은 금지 — 사실 나열과 조건 안내까지만.\n"
        "5) 금융상품 위험등급은 1등급이 매우 높은 위험, 6등급이 매우 낮은 위험이다. "
        "등급 숫자와 위험 방향을 절대 뒤집지 않는다.\n"
        "6) 간결한 한국어 존댓말. 목록이 필요하면 번호 목록. 마지막 줄에 데이터 기준일을 쓴다."
    )
    user = (f"질문: {question}\n\n"
            f"[검증 통과 근거]\n{context or '(근거 없음 — 결과 0건임을 안내)'}\n\n"
            f"[해석·한계 노트]\n{notes}\n\n"
            "위 근거만으로 질문에 답하라.")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 사후 대조 (순수 함수) — 근거 밖 상품명·숫자가 든 줄을 지운다
# ---------------------------------------------------------------------------

def _allowed_numbers(blob):
    """근거 문자열에서 숫자 토큰(콤마 제거)을 모두 수집."""
    return {t.replace(",", "").rstrip(".") for t in re.findall(r"\d[\d,\.]*", blob)}


def _number_ok(token, allowed):
    """답변 속 숫자가 근거 숫자와 같거나 그 '소수부 반올림'일 때만 허용.

    자릿수가 다른 앞자리 우연 일치(25.55 vs 2,555억…)는 지어낸 수로 본다 —
    프리픽스 허용은 소수점 경계가 맞을 때(33 ← 33.03)로 제한한다.
    """
    t = token.replace(",", "").rstrip(".")
    if not t:
        return True
    for a in allowed:
        if a == t:
            return True
        if a.startswith(t):
            nxt = a[len(t):len(t) + 1]
            if "." in t or nxt == ".":                # 33←33.03 / 33.0←33.03 허용
                return True
        if t.startswith(a):
            nxt = t[len(a):len(a) + 1]
            if nxt == "" or nxt == ".":               # 33.0 vs 33 허용
                return True
    return False


def post_check_answer(text, evidences, question, index=None, extra_allowed=""):
    """생성 답변 → (정제 답변 | None, 제거된 줄 목록).

    검사 2종 — 줄 단위로 판정해 위반 줄을 삭제한다:
      ① 실존 상품명 대조: 답변에 등장한 (사전에 있는) 상품·종목 이름이 근거·질문에
         없으면 그 줄은 AI 가 끼워 넣은 것 → 삭제. (index 없으면 생략)
      ② 숫자 대조: 답변 속 숫자가 근거·질문·노트에 없으면 삭제(반올림 허용).
    전부 삭제되면 None — 호출부가 규칙 요약으로 폴백한다.
    """
    blob = to_context_string(evidences) + " " + question + " " + extra_allowed
    blob_norm = norm_name(blob)
    allowed_nums = _allowed_numbers(blob)

    kept, removed = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        bad = None
        if index is not None:
            for name, refs in index.scan(stripped):
                if len(name) < 4:                    # 아주 짧은 이름은 우연 일치가 많다
                    continue
                if any(r.kind.startswith("product") or r.kind == "constituent"
                       for r in refs) and name not in blob_norm:
                    bad = f"근거 밖 이름: {name}"
                    break
        if bad is None:
            body = re.sub(r"^\s*\d+[\.\)]\s*", "", stripped)   # 목록 번호(1. / 2))는 숫자가 아님
            for tok in re.findall(r"\d[\d,\.]*", body):
                if not _number_ok(tok, allowed_nums):
                    bad = f"근거 밖 숫자: {tok}"
                    break
        if bad is None:
            kept.append(line)
        else:
            removed.append((stripped[:60], bad))

    clean = "\n".join(kept).strip()
    return (clean if clean else None), removed


# ---------------------------------------------------------------------------
# HCX-005 생성기 — answer_service 에 주입하는 콜러블을 만든다
# ---------------------------------------------------------------------------

def make_hcx_generator(client=None):
    """generator(question, plan, result, verdict) -> 생성 답변 텍스트 | None(실패).

    실패(API 오류·빈 응답)는 None 으로 돌려 규칙 요약 폴백을 살린다 —
    다른 LLM 으로의 대체는 규정상 존재하지 않는다.
    """
    if client is None:
        from agent.clova_client import ClovaChatClient
        client = ClovaChatClient(model="HCX-005")

    def generator(question, plan, result, verdict):
        try:
            messages = build_generation_messages(question, plan, result, verdict)
            resp = client.chat(messages, max_completion_tokens=MAX_GENERATION_TOKENS)
            content = ((resp.get("result") or {}).get("message") or {}).get("content")
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None
    return generator
