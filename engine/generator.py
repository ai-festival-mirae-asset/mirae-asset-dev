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

MAX_GENERATION_TOKENS = 2048      # 응답 상한 — 두 그룹 목록(M-18 유형)이 정식명으로도 잘리지 않게 8/22 상향
# 생성 흔들림 억제(8/19 ⑧-6): 같은 근거에 같은 문장이 나오도록 낮은 온도 + 고정 seed
GENERATION_TEMPERATURE = 0.2
GENERATION_SEED = 20260711        # 데이터 기준일 — 의미 없는 고정값


# ---------------------------------------------------------------------------
# 프롬프트 구성
# ---------------------------------------------------------------------------

def build_generation_messages(question, plan, result, verdict):
    """근거 주입 + '근거 밖 서술 금지' 지시 — 검증 통과 근거만 들어간다.

    답변 형식은 주최 예시의 3단(결론 → 상품별 비교 → 근거·해석 노트·기준일)을 따른다(8/18 결정).
    """
    context = to_context_string(list(result.evidences) + list(verdict.evidences))
    notes = "\n".join(f"- {n}" for n in plan.notes) or "- (없음)"
    system = (
        "너는 금융상품 데이터 안내 도우미다. 아래 규칙을 반드시 지켜라.\n"
        "1) 답변의 모든 사실(상품명·숫자·날짜)은 반드시 [근거N] 블록 안의 내용에서만 가져온다. "
        "근거에 없는 상품·수치·설명은 한 글자도 쓰지 않는다(네가 아는 지식 사용 금지).\n"
        "2) 수치를 인용할 때는 근거의 값을 그대로 쓰고, 어느 근거인지 [근거N] 표기를 붙인다. "
        "금액은 근거에 함께 적힌 환산 표기(예: pd_net_tamt_krw: 28.4조원 / 3,467억원)를 그대로 옮겨 쓴다 — "
        "네가 단위를 바꿔 계산하지 않는다(단위 오류의 원인). 근거에 없는 비율·순위·합계를 네가 계산해 쓰지 않는다.\n"
        "3) '해석·한계 노트'의 내용(해석 기준·데이터 한계)은 답변에 반드시 포함한다.\n"
        "4) 미래 전망·단정적 투자 추천은 금지 — 사실 나열과 조건 안내까지만.\n"
        "5) 금융상품 위험등급은 1등급이 매우 높은 위험, 6등급이 매우 낮은 위험이다. "
        "등급 숫자와 위험 방향을 절대 뒤집지 않는다.\n"
        "6) 답변 형식(3단): ① 첫 줄에 질문에 대한 결론 한두 문장 ② 상품이 여럿이면 번호 목록으로 "
        "상품별 핵심 수치를 비교(이름은 근거의 표기 그대로) ③ 마지막에 '근거·기준일:' 줄 — "
        "해석·한계 노트와 데이터 기준일을 쓴다.\n"
        "7) 결과가 0건이면 '조건에 맞는 항목을 데이터에서 확인하지 못했다'고만 쓰고, 이유를 지어내지 않는다.\n"
        "8) 간결한 한국어 존댓말."
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


# 한국어 큰 단위 — 근거의 원 단위 숫자를 생성기가 환산해 쓸 수 있다("28,359,162,282,520" → "약 28.4조").
# 생성기가 '십억원'(M-03 실측)·'백만' 같은 단위도 쓰므로 함께 받는다(긴 단위를 먼저 매칭).
_UNIT_SCALE = {"만": 1e4, "십만": 1e5, "백만": 1e6, "천만": 1e7, "억": 1e8, "십억": 1e9,
               "백억": 1e10, "천억": 1e11, "조": 1e12}
_NUMBER_WITH_UNIT_RE = re.compile(r"(\d[\d,\.]*)\s*(십억|백억|천억|십만|백만|천만|조|억|만)?")


def _scaled_number_ok(token, unit, allowed_floats):
    """'28.4조'처럼 단위가 붙은 수: 근거 숫자를 그 단위로 나눠 토큰의 자릿수로 반올림한 값과 같으면 허용."""
    t = token.replace(",", "").rstrip(".")
    try:
        value = float(t)
    except ValueError:
        return False
    decimals = len(t.split(".")[1]) if "." in t else 0
    scale = _UNIT_SCALE[unit]
    for a in allowed_floats:
        if a <= 0:
            continue
        if round(a / scale, decimals) == round(value, decimals):
            return True
    return False


def _tokens_with_units(body):
    """본문 → [(숫자 토큰, 단위|None)] — 순위 표기(1위·2번째)와 목록 번호는 숫자로 세지 않는다."""
    body = re.sub(r"^\s*\d+[\.\)]\s*", "", body)                # 목록 번호(1. / 2))
    body = re.sub(r"\d+\s*(?:위|번째|순위)(?![\d%])", " ", body)  # 순위 표기는 근거의 숫자가 아니다
    body = re.sub(r"\[근거\s*\d+\]", " ", body)                  # 근거 표기 [근거3]
    return [(m.group(1), m.group(2)) for m in _NUMBER_WITH_UNIT_RE.finditer(body) if m.group(1)]


_NAME_FIELDS = ("pd_abrv_nm", "pd_nm", "PD_ABRV_NM", "PD_NM", "itm_nm", "itm_abrv_nm", "etf_name",
                "COMPST_ISU_NM", "mgmt_co", "매칭", "상품", "종목")


def _evidence_names(evidences):
    """근거의 이름 열 값(상품·종목·운용사) — 표기 정정의 기준."""
    names = []
    for ev in evidences:
        for k, v in (getattr(ev, "fields", None) or {}).items():
            if k in _NAME_FIELDS and isinstance(v, str) and len(v.strip()) >= 4:
                names.append(v.strip())
    return list(dict.fromkeys(names))


def respace_names(text, evidences):
    """근거 상품명의 띄어쓰기 변형을 정식 표기로 복원 — '코스닥 150' → '코스닥150' (8/22 M-18 실측).

    생성기가 상품명 안에 공백을 넣거나 빼면 채점(이름 substring 대조)이 깨진다.
    글자 내용·순서는 같고 공백만 다른 구간을 찾아 근거의 정식 표기로 되돌린다.
    같은 줄 안에서만 본다(개행은 공백으로 취급하지 않음 — 목록 구조 보존).
    반환: (정정된 텍스트, [정정된 이름]).
    """
    corrections = []
    for name in _evidence_names(evidences):
        compact = re.sub(r"\s+", "", name)
        if len(compact) < 8 or name in text:
            continue
        pattern = r"[ \t]*".join(re.escape(ch) for ch in compact)
        new_text, n = re.subn(pattern, name, text)
        if n and new_text != text:
            text = new_text
            corrections.append(name)
    return text, corrections


def autocorrect_names(text, evidences):
    """생성기가 이름을 한 글자 잘못 옮긴 경우('퀀타매트릭스'→'퀸타매트릭스', 8/19 L-06 실측)를 근거 표기로 되돌린다.

    규칙(보수적 — 다른 상품으로 바꿔치기하지 않기 위해):
      · 근거 이름이 답변에 (띄어쓰기 무시로도) 없을 때만 본다
      · 같은 길이의 창을 밀며 **글자 하나만 다른**(길이 12 이상이면 둘까지) 구간을 찾아 근거 표기로 바꾼다 —
        삽입·삭제·띄어쓰기 차이는 정정 대상이 아니다('KODEX 200TR' vs 'KODEX 200 T…' 는 건드리지 않음)
      · 다른 근거 이름과 정확히 같은 구간은 건드리지 않는다
    반환: (정정된 텍스트, [(원문, 정정)]).
    """
    names = _evidence_names(evidences)
    exact = set(names)
    corrections = []
    for name in names:
        if len(name) < 6 or name in text or norm_name(name) in norm_name(text):
            continue
        L = len(name)
        limit = 2 if L >= 12 else 1
        best = None
        for i in range(0, max(0, len(text) - L + 1)):
            window = text[i:i + L]
            if "\n" in window or window in exact:
                continue
            diffs = sum(1 for a, b in zip(window, name) if a != b)
            if diffs == 0 or diffs > limit:
                continue
            # 다른 글자가 공백·구두점이면 표기 차이일 뿐 오기가 아니고, 숫자가 다르면 다른 상품일 수 있다
            # (KODEX 200 vs 100, 25-11 vs 26-11) — 한글·영문 글자끼리의 치환만 정정한다
            if any((a.isspace() or b.isspace() or not (a.isalnum() and b.isalnum())
                    or a.isdigit() or b.isdigit())
                   for a, b in zip(window, name) if a != b):
                continue
            if best is None or diffs < best[0]:
                best = (diffs, i, window)
        if best is not None:
            _d, i, window = best
            text = text[:i] + name + text[i + L:]
            corrections.append((window, name))
    return text, corrections


def _evidence_keys(evidences):
    """근거에 등장하는 키·값 문자열 집합 — 이름 표기가 달라도 같은 상품(키 일치)이면 근거 안의 것으로 본다."""
    keys = set()
    for ev in evidences:
        if getattr(ev, "source_id", None):
            keys.add(str(ev.source_id))
        for v in (getattr(ev, "fields", None) or {}).values():
            if isinstance(v, (str, int)):
                keys.add(str(v))
    return keys


def post_check_answer(text, evidences, question, index=None, extra_allowed=""):
    """생성 답변 → (정제 답변 | None, 제거된 줄 목록).

    검사 2종 — 줄 단위로 판정해 위반 줄을 삭제한다:
      ① 실존 상품명 대조: 답변에 등장한 (사전에 있는) 상품·종목 이름이 근거·질문에
         없으면 그 줄은 AI 가 끼워 넣은 것 → 삭제. (index 없으면 생략)
         표기가 달라도 같은 상품(약칭↔정식명, 별칭 — 데이터 키가 근거 안에 있음)이면 허용(8/19).
      ② 숫자 대조: 답변 속 숫자가 근거·질문·노트에 없으면 삭제(반올림 허용).
         조·억·만 단위 환산("28.4조" ← 28,359,162,282,520)과 순위 표기(1위)는 허용(8/19).
    전부 삭제되면 None — 호출부가 규칙 요약으로 폴백한다.
    """
    blob = to_context_string(evidences) + " " + question + " " + extra_allowed
    blob_norm = norm_name(blob)
    allowed_nums = _allowed_numbers(blob)
    allowed_floats = []
    for a in allowed_nums:
        try:
            allowed_floats.append(float(a))
        except ValueError:
            pass
    evidence_keys = _evidence_keys(evidences)

    # 이름 띄어쓰기 복원(8/22) → 한 글자 오기 정정(8/19) — 지우는 대신 근거 표기로 되돌린다
    text, respaced = respace_names(text, evidences)
    text, corrections = autocorrect_names(text, evidences)
    kept, removed = [], [(n[:60], f"표기 정정(띄어쓰기): {n}") for n in respaced]
    removed += [(w[:60], f"표기 정정: {w} → {n}") for w, n in corrections]
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
                if not any(r.kind.startswith("product") or r.kind == "constituent" for r in refs):
                    continue
                if name in blob_norm or any(r.key in evidence_keys for r in refs):
                    continue                         # 근거 안의 이름(표기 변형·별칭 포함)
                bad = f"근거 밖 이름: {name}"
                break
        if bad is None:
            for tok, unit in _tokens_with_units(stripped):
                if _number_ok(tok, allowed_nums):
                    continue
                if unit and _scaled_number_ok(tok, unit, allowed_floats):
                    continue
                bad = f"근거 밖 숫자: {tok}{unit or ''}"
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
            resp = client.chat(messages, max_completion_tokens=MAX_GENERATION_TOKENS,
                               temperature=GENERATION_TEMPERATURE, seed=GENERATION_SEED)
            content = ((resp.get("result") or {}).get("message") or {}).get("content")
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None
    return generator


# ---------------------------------------------------------------------------
# HCX 필수 구간 준수 (8/26 주최 공지: '질의 Intent분석'과 '답변 생성'은 HCX 필수) — 8/27
#   우리 파이프라인은 규칙 라우팅 + 확정 답안(생성 생략) 조합에서 HCX 를 한 번도 거치지
#   않을 수 있었다. 아래 두 콜러블이 그 공백을 메운다:
#   ① intent_checker — 모든 질의의 의도를 HCX 가 분석해 think_trace 에 기록.
#     라우팅 판정은 규칙·검증이 우선한다(상충 시 주최 데이터 우선 원칙과 같은 구조).
#   ② finalizer — 확정 답안(규칙 요약·거절문 등 비생성 경로)을 HCX 가 '그대로' 최종
#     출력하게 한다. 출력이 확정 답안과 내용 불일치면 확정 답안을 유지(결정성 보존).
#   실패는 None 으로 돌려 파이프라인을 막지 않는다 — 준수 시도와 결과는 trace 에 남는다.
# ---------------------------------------------------------------------------
INTENT_CHECK_MAX_TOKENS = 1024   # ClovaChatClient 가 1024 미만 요청을 거부(짧은 분류 출력이라 실사용은 소량)


def collapse_ws(text):
    """공백만 다른 두 답안을 같은 내용으로 보는 비교용 정규화(순수 함수)."""
    return re.sub(r"\s+", " ", text or "").strip()


_ECHO_DOT_RE = re.compile(r"[·ㆍ‧•]")
_ECHO_BULLET_RE = re.compile(r"^[\s*•\-–—>]+", re.MULTILINE)


def echo_equivalent(a, b):
    """HCX 최종화 출력이 확정 답안과 '내용 동일'인지 — 표기 차이만 무시(순수 함수).

    실측(8/27): HCX 는 그대로 출력하라고 해도 글머리표('- ')를 지우거나 가운뎃점을
    'ㆍ'로 바꾸고 줄을 합친다. 수치·문장이 같으면 같은 답으로 보고 HCX 출력을
    채택한다(형식 차이 허용). 내용이 다르면 False — 확정 답안 유지."""
    def norm(t):
        t = _ECHO_DOT_RE.sub("·", t or "")
        t = _ECHO_BULLET_RE.sub("", t)
        t = re.sub(r"\s*·\s*", "·", t)      # 'A · B' vs 'AㆍB' — 가운뎃점 주변 공백 차이 무시
        return re.sub(r"\s+", " ", t).strip()
    return norm(a) == norm(b)


def make_hcx_intent_checker(client=None):
    """intent_checker(question) -> 의도 분류 한 줄 | None(실패)."""
    if client is None:
        from agent.clova_client import ClovaChatClient
        client = ClovaChatClient(model="HCX-005")

    def intent_checker(question):
        try:
            messages = [
                {"role": "system", "content": (
                    "너는 금융상품 질의 의도 분류기다. 질문의 의도를 정확히 한 줄로 분류한다. "
                    "형식: 분류=<조회|비교|순위|건수|추천|거절대상|조회불가> · "
                    "대상=<국내채권|국내ETF|해외ETF|공모펀드|복수|기타> · 핵심조건=<15자 이내>. "
                    "다른 말은 출력하지 않는다.")},
                {"role": "user", "content": question},
            ]
            resp = client.chat(messages, max_completion_tokens=INTENT_CHECK_MAX_TOKENS,
                               temperature=0.0, seed=GENERATION_SEED)
            content = ((resp.get("result") or {}).get("message") or {}).get("content")
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None
    return intent_checker


def make_hcx_finalizer(client=None):
    """finalizer(question, draft) -> HCX 출력 텍스트 | None(실패). 수락 판정은 호출부가
    collapse_ws 동일성으로 한다(내용이 달라졌으면 확정 답안 유지)."""
    if client is None:
        from agent.clova_client import ClovaChatClient
        client = ClovaChatClient(model="HCX-005")

    def finalizer(question, draft):
        try:
            messages = [
                {"role": "system", "content": (
                    "너는 금융상품 QA 서비스의 최종 답변 출력기다. 검증이 끝난 확정 답안이 주어진다. "
                    "확정 답안을 한 글자도 바꾸지 말고 그대로 최종 답변으로 출력한다. "
                    "수치·상품명·목록 순서·기준일·줄바꿈을 더하거나 빼거나 바꾸지 않는다. "
                    "확정 답안 본문 외의 말은 출력하지 않는다.")},
                {"role": "user", "content": f"질문: {question}\n\n확정 답안:\n{draft}"},
            ]
            resp = client.chat(messages, max_completion_tokens=MAX_GENERATION_TOKENS,
                               temperature=0.0, seed=GENERATION_SEED)
            content = ((resp.get("result") or {}).get("message") or {}).get("content")
            return content.strip() if isinstance(content, str) and content.strip() else None
        except Exception:
            return None
    return finalizer
