# -*- coding: utf-8 -*-
"""
답변 검증(Answer Validation) — 답이 나가기 직전의 5중 검문소 (구현 순서 ④, 8/14).

무엇: 질문 **원문**을 기준으로 5가지를 다시 검사해 최종 답변 태도(behavior)를
      확정한다 — answer(정상 답변) / partial(답하되 한계 명시) / refuse(확인 불가 안내).
왜  : 함정 문항에 답하면 감점이다. 라우터(순서 ③)가 이미 걸렀더라도 여기서
      **독립적으로 다시 검사**한다 — 한 곳이 놓쳐도 다른 곳이 잡는 이중 방어.
      AI(LLM)는 전혀 관여하지 않는 순수 코드라, 함정 경로에서 지어낼 여지가 없다.

5중 검문소:
  1) 값 검사      — 질문 속 값이 실제 존재하는 범위인가 (AAAA 등급·0등급·마이너스 보수 → 거절)
  2) 존재 검사    — 질문이 전제하는 상품·종목이 데이터에 진짜 있나 (kimi·미존재 상품명 → 거절)
  3) 시점 검사    — 실시간·미래·기준일(7/11) 이후·과거 이력 요구 → 거절
  4) 항목 검사    — 요청한 정보 항목이 원천 데이터에 있기는 한가 (해외 위험등급·펀드 총보수 → 거절)
  5) 충분성 검사  — 값이 채워진 비율이 낮으면 답하되 한계 문구 강제 (partial)

거절의 원칙(비대칭): **"없다는 적극적 증거"가 있을 때만 거절한다.**
  이름 일부만 겹치는 후보(부분 일치)가 있으면 거절하지 않고 후보를 안내한다 —
  정상 문항을 과잉 거절로 잃지 않기 위함. 부분 일치는 안내까지만 쓰고
  존재의 근거로는 쓰지 않는다(함정 방어 정책).
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import os
import re
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.policy import load_policy                         # noqa: E402
from engine.router import (BRAND_TOKENS, UNSUPPORTED_ASSETS,  # noqa: E402
                           detect_time_flags, extract_ratings,
                           extract_risk_grades, find_unknown_latin_terms,
                           ground_entities)
from pipeline.entity_index import norm_name, token_matches    # noqa: E402
from pipeline.evidence import AS_OF_MASTER, Evidence          # noqa: E402
from pipeline.query_aliases import normalize_product_query    # noqa: E402


@dataclass
class GateResult:
    """검문소 1곳의 판정 — think_trace 에 그대로 기록된다."""
    gate: str                 # value(값)·existence(존재)·time(시점)·field(항목)·coverage(충분성)
    verdict: str              # pass | refuse | partial
    reason: str = ""


@dataclass
class Verdict:
    """5중 검문 종합 결과 — 답변 조립기가 이 판정대로 답변 태도를 정한다."""
    behavior: str                                   # answer | partial | refuse
    reasons: list = field(default_factory=list)     # 사람이 읽을 사유(거절문·한계 문구 재료)
    gates: list = field(default_factory=list)       # [GateResult]
    suggestions: list = field(default_factory=list) # 유사 이름 안내(존재 근거 아님)
    evidences: list = field(default_factory=list)   # validation 채널 근거


# ---------------------------------------------------------------------------
# 검문소 1 — 값 검사: 질문 속 값이 도메인(존재 가능한 범위) 안인가
# ---------------------------------------------------------------------------

def gate_value_domain(question, policy):
    _valid, invalid = extract_ratings(question)
    if invalid:
        return GateResult("value", "refuse",
                          f"'{invalid[0]}'는 신용등급 체계(AAA~D)에 존재하지 않는 표기")
    risk = extract_risk_grades(question, policy)
    if risk and risk[0] == "invalid":
        return GateResult("value", "refuse",
                          f"위험등급은 1~6등급 체계 — '{risk[1]}'은 존재하지 않음(0은 미분류 표시값)")
    if "보수" in question and re.search(r"마이너스|음수", question):
        return GateResult("value", "refuse", "보수율은 음수가 될 수 없는 값")
    for asset in UNSUPPORTED_ASSETS:
        if asset in question:
            return GateResult("value", "refuse",
                              f"'{asset}' 유형 자산은 제공 데이터(채권·ETF·ETN·공모펀드)에 없음")
    return GateResult("value", "pass")


# ---------------------------------------------------------------------------
# 검문소 2 — 존재 검사: 질문이 전제하는 상품·종목이 데이터에 진짜 있나
# ---------------------------------------------------------------------------

_ASKS_SPECIFIC_RE = re.compile(r"정보|알려|수익률|보수|어때|있어|찾아|추천|구조|얼마|몇|언제|뭐야|어디|순자산|비교")   # 8/22 v2: "순자산 얼마야?"도 특정 상품 질문
_VARIANT_RE = re.compile(r"([가-힣A-Za-z0-9]*)\s*(제?\s?\d+\s?호)")
_NAME_SUFFIX_RE = re.compile(r"(펀드|투자신탁|증권|상품)$")


def _mask_grounded(question, grounded):
    """정규화 질문에서 '데이터와 정확 일치한 이름' 구간을 ■로 가린 문자열.

    가려지지 않고 남은 부분에서 발견되는 상품명 패턴만 '미확인 개체' 후보가 된다.
    """
    masked = norm_name(question)
    for name, _refs in grounded:
        pos = masked.find(name)
        if pos >= 0:
            masked = masked[:pos] + "■" * len(name) + masked[pos + len(name):]
    return masked


def _suggest(index, text, limit):
    """부분 일치 후보 이름 목록(안내용). 상품 종류만, 중복 제거."""
    core = _NAME_SUFFIX_RE.sub("", norm_name(text))
    out = []
    if len(core) >= 2:
        for _name, ref in index.search(core, limit=limit * 3):
            if ref.kind.startswith("product") and ref.display not in out:
                out.append(ref.display)
            if len(out) >= limit:
                break
    return out


def gate_existence(question, index, policy):
    """거절 조건(적극적 부재 증거) 3종 — 그 외 애매한 경우는 pass(후보 안내는 답변 쪽).

    ① 미등록 라틴 토큰: 정확 일치도, 이름 속 부분 일치도 전혀 없는 단어(kimi 등)
    ② 브랜드+미존재 상품명: KODEX 등 브랜드로 시작하는 상품명인데 그 이름이 없음
    ③ 'N호' 변형: 기준일 데이터에 해당 호수로 식별되는 상품이 없음
    """
    from engine.router import ground_with_alias_fallback      # 원문이 상품명을 품으면 별칭 치환 안 함(8/22)
    normalized_question, grounded = ground_with_alias_fallback(index, question)
    matched_names = [name for name, _refs in grounded]
    asks = bool(_ASKS_SPECIFIC_RE.search(question))
    limit = policy["trap_similar_suggest_limit"]

    # ① 미등록 라틴 토큰 — 의미 있는 부분 일치(원문 표기 기준)조차 0건일 때만 거절.
    #    공백 제거 정규화의 우연 겹침('kimi' ⊂ 'Denmark IMI')은 token_matches 가 걸러낸다.
    unknown = find_unknown_latin_terms(normalized_question, matched_names)
    for tok in unknown:
        if asks and not token_matches(index, tok, limit=1):
            return (GateResult("existence", "refuse",
                               f"'{tok}'로 식별되는 상품·종목이 기준일 데이터에 없음"
                               "(이름 일부가 겹치는 후보도 0건 — 간접 연상으로 답하지 않음)"),
                    [])

    # ② 브랜드 접두 상품명 — 정확 일치·부분 일치 모두 없으면 그 상품은 없다
    brand = next((b for b in BRAND_TOKENS if b in normalized_question), None)
    has_product = any(r.kind.startswith("product") for _n, refs in grounded for r in refs)
    if brand and asks and not has_product:
        phrase = re.sub(r"정보|알려줘|알려|수익률|어때|찾아줘|있어|\?", " ",
                        normalized_question).strip()
        if not index.exact(phrase) and not index.search(phrase, limit=1):
            suggestions = _suggest(index, phrase, limit)
            if not suggestions:                       # 브랜드 뒤 토막말로 재시도(안내용)
                tail = phrase.replace(brand, " ").split()
                if tail:
                    suggestions = _suggest(index, max(tail, key=len), limit)
            return (GateResult("existence", "refuse",
                               f"'{phrase}' 명칭의 상품이 기준일 상품 목록에 없음"), suggestions)

    # ③ 'N호' 변형 — 정확 일치한 이름에 포함된 경우는 제외하고 검사
    masked = _mask_grounded(normalized_question, grounded)
    if re.search(r"\d+\s?호", masked):
        m = _VARIANT_RE.search(question)
        if m and asks:
            base = m.group(1)
            full = norm_name(base + m.group(2))
            if not index.exact(full):
                suggestions = _suggest(index, base, limit) if base else []
                return (GateResult("existence", "refuse",
                                   f"'{m.group(2).strip()}'로 식별되는 상품이 기준일 데이터에 없음"
                                   + (f" — '{base}' 유사명은 별도 확인 필요" if suggestions else "")),
                        suggestions)

    return GateResult("existence", "pass"), []


# ---------------------------------------------------------------------------
# 검문소 3 — 시점 검사 / 검문소 4 — 항목 검사
# ---------------------------------------------------------------------------

def gate_time_boundary(question):
    flags = detect_time_flags(question)
    if flags.get("realtime"):
        return GateResult("time", "refuse",
                          f"실시간 시세는 제공 범위 밖 — 데이터는 {AS_OF_MASTER} 시점 스냅샷")
    if flags.get("history"):
        return GateResult("time", "refuse",
                          "구성종목은 2026-07-10 하루치만 보유 — 과거 시점과의 비교 불가")
    if flags.get("post_snapshot"):
        return GateResult("time", "refuse",
                          f"기준일({AS_OF_MASTER}) 이후({flags['post_snapshot']}) 정보는 보유하지 않음")
    if flags.get("future") and re.search(r"추천|골라|알려", question):
        return GateResult("time", "refuse",
                          "미래 전망·시장 예측은 제공 불가(단정 추천 금지) — 조건 기반 사실 조회로 전환 가능")
    return GateResult("time", "pass")


# 항목 부재 규칙 — (조건 함수, 사유). 원천에 컬럼 자체가 없는 요청은 거절이 정답.
_FIELD_RULES = (
    (lambda q: "해외" in q and "위험" in q and "등급" in q and "국내" not in q,
     "해외 ETF 원천 데이터에는 위험등급 항목이 없음 — 국내 ETF 는 조회 가능"),
    (lambda q: "펀드" in q and "타사" in q and "판매" in q,
     "공모펀드 원천에는 전체 판매상태와 당사판매여부만 있으며 타사 판매사 식별 항목은 없음"),
    (lambda q: "펀드" in q and "보수" in q and not re.search(r"ETF|ETN|ETP", q, re.I),
     "공모펀드 원천 데이터에는 총보수 항목이 없음(보유: 수익률·위험등급·설정액 등)"),
)


def gate_field_availability(question):
    for cond, reason in _FIELD_RULES:
        if cond(question):
            return GateResult("field", "refuse", reason)
    return GateResult("field", "pass")


# ---------------------------------------------------------------------------
# 검문소 5 — 충분성 검사: 커버리지가 낮으면 답하되 한계 명시(partial)
# ---------------------------------------------------------------------------

def gate_coverage(plan, result, policy):
    threshold = policy["coverage_partial_threshold_pct"]
    for outcome in result.outcomes:
        if outcome.channel == "sql" and outcome.op == "coverage_check":
            for row in outcome.rows:
                if row.get("coverage_pct", 100.0) < threshold:
                    if plan.hints.get("coverage_is_caveat_only"):
                        continue
                    return GateResult(
                        "coverage", "partial",
                        f"{row['field']} 값 보유 {row['non_null']:,}/{row['total']:,}건"
                        f"({row['coverage_pct']}%) — 값 보유 상품 기준의 부분 답변임을 명시")
    if plan.behavior_hint == "partial":
        return GateResult("coverage", "partial",
                          "데이터 한계로 부분 답변 — 답변에 한계 문구 포함")
    return GateResult("coverage", "pass")


# ---------------------------------------------------------------------------
# 종합 판정
# ---------------------------------------------------------------------------

def validate_answerability(question, plan, result, index, policy=None):
    """5중 검문 실행 → Verdict. 라우터 판정을 신뢰하지 않고 질문 원문에서 재검사한다."""
    policy = policy or load_policy()
    gates, suggestions = [], []

    gates.append(gate_value_domain(question, policy))
    g_exist, sug = gate_existence(question, index, policy)
    gates.append(g_exist)
    suggestions.extend(sug)
    gates.append(gate_time_boundary(question))
    gates.append(gate_field_availability(question))
    gates.append(gate_coverage(plan, result, policy))

    refusals = [g for g in gates if g.verdict == "refuse"]
    partials = [g for g in gates if g.verdict == "partial"]

    if refusals:
        reasons = [g.reason for g in refusals]
        ev = Evidence(source="validation", source_id=refusals[0].gate,
                      channel="validation", as_of=AS_OF_MASTER,
                      fields={"판정": "확인 불가", "사유": " / ".join(reasons)[:300]})
        return Verdict("refuse", reasons, gates, suggestions, [ev])
    if partials:
        reasons = [g.reason for g in partials]
        ev = Evidence(source="validation", source_id="coverage",
                      channel="validation", as_of=AS_OF_MASTER,
                      fields={"판정": "부분 답변", "사유": " / ".join(reasons)[:300]})
        return Verdict("partial", reasons, gates, [], [ev])
    return Verdict("answer", [], gates, [], [])
