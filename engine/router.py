# -*- coding: utf-8 -*-
"""
Router Stage A — 규칙 기반 라우팅(LLM 0콜) + 오케스트레이션 (S2 순서 ③, 8/13).

무엇: 질문 → RoutePlan(어느 채널에 어떤 템플릿·파라미터로 물을지). 엔티티
      grounding(통합 인덱스 scan)·수치/등급/시간 조건 추출·유형 규칙으로
      단일 플랜이 확정되면 LLM 을 생략한다(fast path — 하 난이도 목표).
왜  : LLM 콜 예산(기본 2콜, 하 0~1콜)과 15초 응답 목표. 규칙이 못 정하면
      Stage B(HCX-005 FC, router_llm.py)로 넘기고, 그것도 실패하면 키워드
      폴백 플랜 — 어떤 질문에도 플랜은 반드시 나온다.

평가셋 대응: 규칙마다 대상 문항(L/M/H/T-nn)을 주석으로 명시 — evalset_v1.jsonl
      에서 역산했다. 여기서 안 잡히는 유형(멀티홉·교집합·교차 상품군)은 Stage B 소관.
LIKE 파라미터 규약: 플랜에는 원문(*_raw)을 싣고 이스케이프(like_param)는 채널
      실행기가 일괄 적용한다 — LLM 플랜(Stage B)이 이스케이프를 놓치는 사고 방지.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import datetime
import os
import re
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.policy import load_policy                         # noqa: E402
from pipeline.entity_index import token_matches               # noqa: E402
from pipeline.evidence import AS_OF_MASTER                    # noqa: E402
from pipeline.themes import REGIONS, detect_theme_terms, load_themes  # noqa: E402

# ---------------------------------------------------------------------------
# 사전 — 신용등급 서열 (external_data/dictionaries/credit_rating.csv 와 동기.
# 테스트가 CSV 의 rank=N 표기와 이 dict 를 대조해 사전-코드 불일치를 잡는다.)
# ---------------------------------------------------------------------------
RATING_RANK = {
    "AAA": 1, "AA+": 2, "AA": 3, "AA-": 4, "A+": 5, "A": 6, "A-": 7,
    "BBB+": 8, "BBB": 9, "BBB-": 10, "BB+": 11, "BB": 12, "BB-": 13,
    "B+": 14, "B": 15, "B-": 16, "CCC": 17, "CC": 18, "C": 19, "D": 20,
}

# 채권 대분류 표현 → STD_PD_MCLS_NM 값 (L-04/05/27 — 국고채는 대분류상 국공채)
BOND_CLASS_MAP = [("국고채", "국공채"), ("국공채", "국공채"), ("국채", "국공채"),
                  ("회사채", "회사채"), ("특수채", "특수채"),
                  ("개인투자용국채", "개인투자용국채")]

CURRENCY_MAP = [("원화", "KRW"), ("달러", "USD"), ("엔화", "JPY"), ("엔", "JPY"),
                ("유로", "EUR"), ("위안", "CNY")]

# 데이터에 없는 자산 유형 — 직접 refuse 사유 (T-06)
UNSUPPORTED_ASSETS = ("코인", "가상자산", "암호화폐", "비트코인", "크립토")

# 존재 검증을 우회하려는 그럴듯한 상품명의 브랜드 접두 감지 (T-07/08 —
# mgmt_resolution.BRAND_PREFIX_TO_COMPANY 와 동기)
BRAND_TOKENS = ("KODEX", "TIGER", "KoAct", "SOL", "PLUS", "ARIRANG", "RISE",
                "KBSTAR", "ACE", "KOSEF", "히어로즈", "HANARO", "1Q", "마이티", "WON")

# 라틴 토큰 중 미등록 개체로 세지 않는 일반어
LATIN_STOPWORDS = {"etf", "etn", "etp", "top", "vs", "ytd", "aum", "ai", "tdf",
                   "msci", "korea", "kospi", "kosdaq", "reit", "esg", "mmf", "csi"}

HOLDING_VERBS = ("편입", "담은", "담고", "담아", "포함", "들어간", "들어있", "들어 있")
COUNT_WORDS = ("몇 개", "몇개", "몇 종", "몇이", "개수", "총 몇", "얼마나 되", "얼마나 돼")
TOP_WORDS = ("상위", "가장", "제일", "톱", "탑", "top", "1위", "순서로", "순위", "좋은")


# ---------------------------------------------------------------------------
# 플랜 자료구조 — Stage B(LLM)·채널 실행기와 공유하는 계약
# ---------------------------------------------------------------------------

@dataclass
class ChannelCall:
    """채널 호출 1건. op — sql: 템플릿 id · graph: holding_etfs/constituents_of/
    company_products/product_info · keyword: lookup · vector: semantic."""
    channel: str
    op: str
    params: dict = field(default_factory=dict)


@dataclass
class RoutePlan:
    intent: str                      # 규칙/LLM 이 판정한 질문 유형
    calls: list = field(default_factory=list)          # [ChannelCall]
    entities: list = field(default_factory=list)       # [(매칭명, [EntityRef])] — grounded
    unknown_terms: list = field(default_factory=list)  # 개체처럼 보이나 미등록 (Validation ② 입력)
    hints: dict = field(default_factory=dict)          # 추출 조건·정책 적용 기록
    stage: str = "rule"              # rule | llm | llm_repair | fallback
    behavior_hint: str = "answer"    # answer | partial | refuse — 최종 판정은 Validation(④)
    notes: list = field(default_factory=list)          # 답변에 명시할 해석·한계 문구


# ---------------------------------------------------------------------------
# 추출기 (순수 함수 — 단위 테스트 대상)
# ---------------------------------------------------------------------------

_RATING_RE = re.compile(r"(?<![A-Za-z])(AAA|AA|A|BBB|BB|B|CCC|CC|C|D)([+\-0])?(?![A-Za-z0-9])")
_INVALID_RATING_RE = re.compile(r"(?<![A-Za-z])([A-D])\1{3,}(?![A-Za-z])")  # AAAA, BBBB…


def extract_ratings(question):
    """유효 등급 토큰 [(표기, rank, 끝위치)] + 도메인 밖 표기 목록 (T-01 방어)."""
    invalid = [m.group(0) for m in _INVALID_RATING_RE.finditer(question)]
    valid = []
    for m in _RATING_RE.finditer(question):
        token = m.group(1) + (m.group(2) or "")
        norm = token[:-1] if token.endswith("0") else token   # AA0 → AA (플랫)
        if norm in RATING_RANK:
            valid.append((norm, RATING_RANK[norm], m.end()))
    return valid, invalid


def rating_condition(question, policy):
    """'AA 이상/AA급 이상/BBB 이하' → {max_rating_rank / min_rating_rank} + 해석 문구.

    8/14 사용자 확정: 'AA 이상'은 **문자 그대로**(AAA·AA+·AA, rank<=3) — AA- 는
    'AA급'/'AA등급대'라는 band 표현이 있을 때만 포함(rank<=4). 정책 플래그
    (rating_at_or_above_includes_minus)를 true 로 바꾸면 옛 등급대 해석으로 복귀.
    채택 해석은 노트로 반환해 답변에 항상 명시한다.
    """
    valid, _ = extract_ratings(question)
    if not valid:
        return {}, []
    token, rank, end = valid[0]
    tail = question[end:end + 6]
    cond, notes = {}, []
    is_flat = not token.endswith(("+", "-"))
    band_low = RATING_RANK.get(token + "-", rank) if is_flat else rank
    is_band = is_flat and (tail.startswith("급") or tail.startswith("등급대"))
    if "이상" in tail:
        if is_band or (policy["rating_at_or_above_includes_minus"] and is_flat):
            cond["max_rating_rank"] = band_low
            notes.append(f"'{token}급(등급대) 이상'={token}+·{token}·{token}- 포함(서열 {band_low} 이하)으로 해석")
        else:
            cond["max_rating_rank"] = rank
            if is_flat and band_low != rank:
                notes.append(f"'{token} 이상'=문자 그대로 서열 {rank} 이하로 해석({token}- 미포함 — "
                             f"'{token}급/등급대'와 구분. {token}- 포함 시 서열 {band_low} 이하)")
            else:
                notes.append(f"'{token} 이상'=서열 {rank} 이하로 해석")
    elif "이하" in tail:
        cond["min_rating_rank"] = rank
        notes.append(f"'{token} 이하'=서열 {rank} 이상(등급 낮은 쪽)으로 해석")
    else:
        cond["max_rating_rank"] = band_low if is_band else rank
    return cond, notes


_RISK_RE = re.compile(r"(\d)\s*등급")


def extract_risk_grades(question, policy):
    """위험등급 조건 → (min, max, notes) / 도메인 밖이면 ('invalid', 표기) / 없으면 None."""
    if "위험" not in question and "등급" not in question:
        return None
    m = _RISK_RE.search(question)
    if m:
        if "신용등급" in question[max(0, m.start() - 8):m.start()]:
            return None                                   # 채권 신용등급 문맥은 별도
        g = int(m.group(1))
        if not 1 <= g <= 6:
            return ("invalid", f"{g}등급")                # T-02: 0등급 등
        tail = question[m.end():m.end() + 4]
        if "이하" in tail:
            return (1, g, [f"'{g}등급 이하'=등급 숫자 {g} 이하(1~{g})로 해석"])
        if "이상" in tail:
            return (g, 6, [f"'{g}등급 이상'=등급 숫자 {g} 이상({g}~6)으로 해석"])
        return (g, g, [])
    if "위험" in question:
        low, high = policy["low_risk_grades"], policy["high_risk_grades"]
        if re.search(r"낮은 위험|저위험|위험.{0,6}낮", question):
            return (min(low), max(low),
                    [f"'낮은 위험'={min(low)}~{max(low)}등급(6=매우 낮음)으로 해석 — 위험등급 1~6 체계"])
        if re.search(r"높은 위험|고위험|위험.{0,6}높", question):
            return (min(high), max(high),
                    [f"'높은 위험'={min(high)}~{max(high)}등급(1=매우 높음)으로 해석"])
    return None


_TOPN_RE = re.compile(r"(?:상위|톱|탑|top)\s*(\d+)|(\d+)\s*(?:개|위|종목)(?:만)?", re.IGNORECASE)


def extract_top_n(question):
    for m in _TOPN_RE.finditer(question):
        n = m.group(1) or m.group(2)
        if n and 0 < int(n) <= 100:
            return int(n)
    return None


_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:를|을|가)?\s*(이상|초과|넘|이하|미만|대)?")


def extract_percents(question):
    """[(값, 종류, 방향)] — 종류는 앞 문맥(금리/보수/비중/수익률)으로 판정."""
    out = []
    for m in _PCT_RE.finditer(question):
        value, direction = float(m.group(1)), m.group(2) or ""
        ctx = question[max(0, m.start() - 14):m.start()]
        kind = ("coupon" if ("금리" in ctx or "이자" in ctx) else
                "fee" if "보수" in ctx else
                "weight" if "비중" in ctx else
                "return" if "수익" in ctx else "unknown")
        out.append((value, kind, direction))
    return out


_YEAR_RE = re.compile(r"(\d{4})\s*년(?:\s*(\d{1,2})\s*월)?")


def detect_time_flags(question, as_of=AS_OF_MASTER):
    """시간 경계 신호 — 실시간/미래/스냅샷 이후 사건/이력 요구 (T-10~12/15 방어).

    스냅샷 이후 연도라도 '만기'처럼 미래가 당연한 속성이면 위반이 아니다(M-17) —
    상장·출시 등 데이터 발생 사건과 결합할 때만 post_snapshot 을 세운다.
    """
    flags = {}
    if re.search(r"지금 주가|현재가|실시간|현재 주가|시세", question):
        flags["realtime"] = True
    if re.search(r"다음\s*달|내년|앞으로|전망|예측|가능성을 반영", question):
        flags["future"] = True
    ay, am = int(as_of[:4]), int(as_of[5:7])
    for m in _YEAR_RE.finditer(question):
        y, mo = int(m.group(1)), int(m.group(2) or 0)
        if (y, mo or 1) > (ay, am) and re.search(r"상장|출시|나온|생긴|설정된", question):
            flags["post_snapshot"] = f"{y}년{f' {mo}월' if mo else ''}"
    if re.search(r"(\d+\s*년|한 해|일 년|작년)\s*전", question) and re.search(r"구성|비교", question):
        flags["history"] = True                          # T-15: 구성 이력은 단일 스냅샷
    return flags


def detect_currency(question):
    """(통화코드, 제외 여부) — '원화 말고/아닌/외' 는 제외 필터."""
    for word, code in CURRENCY_MAP:
        i = question.find(word)
        if i < 0:
            continue
        tail = question[i + len(word):i + len(word) + 6]
        exclude = any(t in tail for t in ("말고", "아닌", "외", "제외"))
        return code, exclude
    return None, False


def find_unknown_latin_terms(question, matched_names):
    """등록 개체와 무관한 라틴 토큰 — 미존재 개체 후보 (T-04/05 방어).

    한글이 바로 붙은 라틴 토큰('챗GPT')은 한 단어로 붙여서 잡는다 — 'GPT'만
    떼면 실제 티커(GPT ETF)와 우연 일치해 함정을 놓친다(8/14 실측).
    matched_names: index.scan 이 찾은 정규화 명칭들 — 그 안에 포함되는 토큰은 제외.
    """
    out = []
    for tok in re.findall(r"[가-힣]*[A-Za-z][A-Za-z0-9&.\-]{1,}", question):
        low = tok.casefold()
        if low in LATIN_STOPWORDS or tok in BRAND_TOKENS:
            continue
        if _RATING_RE.fullmatch(tok) or _INVALID_RATING_RE.fullmatch(tok):
            continue
        if any(low in name for name in matched_names):
            continue
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# 엔티티 grounding — index.scan 결과의 노이즈 필터
# ---------------------------------------------------------------------------

def ground_entities(index, question):
    """scan 결과에서 짧은 일반어 오탐을 걸러낸 [(name, [refs])].

    company 2~3자(우리·하나·삼성…)는 운용/발행 문맥이 있을 때만, index 짧은
    명칭은 지수 문맥이 있을 때만 인정 — Validation(④)이 질의 원문 기준으로
    독립 재수행하므로 여기서는 라우팅 정밀도만 책임진다.
    """
    has_mgmt_ctx = bool(re.search(r"운용|발행|자산운용", question))
    has_index_ctx = bool(re.search(r"지수|벤치마크|추종|따라가", question))
    out = []
    for name, refs in index.scan(question):
        # 짧은 라틴 약칭(티커 'XA'·'GPT' 등)은 공백 제거 정규화의 우연 매칭이 잦다 —
        # 원문에 독립 토큰으로 존재할 때만 인정. 한글이 바로 앞에 붙은 경우
        # ('챗GPT'의 GPT)도 독립 토큰이 아니다(8/14 실측 — 티커 GPT 오인 방어).
        if len(name) <= 4 and re.fullmatch(r"[0-9a-z.&\-]+", name) and not \
                re.search(rf"(?<![0-9A-Za-z가-힣]){re.escape(name)}(?![0-9A-Za-z])", question, re.I):
            continue
        kept = []
        for ref in refs:
            if ref.kind == "company" and len(name) < 4 and not has_mgmt_ctx:
                continue
            if ref.kind == "index" and len(name) < 4 and not has_index_ctx:
                continue
            kept.append(ref)
        if kept:
            out.append((name, kept))
    return out


def _first_of_kind(entities, *kinds):
    for name, refs in entities:
        for ref in refs:
            if ref.kind in kinds:
                return name, ref
    return None, None


def _spacing_variants(name):
    """지수·벤치마크 표기 변형 — 'kospi200' ↔ 'KOSPI 200' (ILIKE 는 대소문자 무시)."""
    tight = name.replace(" ", "")
    spaced = re.sub(r"(?<=[A-Za-z가-힣])(?=\d)", " ", tight)
    return sorted({name, tight, spaced})


# ---------------------------------------------------------------------------
# Stage A 본체
# ---------------------------------------------------------------------------

def route_stage_a(question, index, policy=None, today=None):
    """규칙 라우팅 — (plan, needs_llm). 확정이면 stage='rule' 완성 플랜,
    미확정이면 힌트만 담긴 미완 플랜과 needs_llm=True."""
    policy = policy or load_policy()
    today = today or datetime.date.today()
    q = question.strip()
    themes = load_themes()

    entities = ground_entities(index, q)
    matched_names = [name for name, _refs in entities]
    unknown = find_unknown_latin_terms(q, matched_names)
    ratings, invalid_ratings = extract_ratings(q)
    risk = extract_risk_grades(q, policy)
    top_n = extract_top_n(q)
    limit = top_n or policy["default_limit"]
    percents = extract_percents(q)
    time_flags = detect_time_flags(q)
    currency, ccy_exclude = detect_currency(q)
    theme_hits = detect_theme_terms(q, themes)
    non_region_themes = [t for t in theme_hits if t not in REGIONS]

    plan = RoutePlan(intent="", entities=entities, unknown_terms=unknown,
                     hints={"top_n": top_n, "percents": percents, "ratings": ratings,
                            "time_flags": time_flags, "themes": theme_hits})

    def done(intent, behavior="answer"):
        plan.intent = intent
        plan.behavior_hint = behavior
        return plan, False

    has_etf_word = bool(re.search(r"ETF|ETN|ETP|이티에프", q, re.IGNORECASE))
    is_global = "해외" in q
    is_bond_domain = any(w in q for w, _v in BOND_CLASS_MAP) or "채권" in q or "영구채" in q
    is_fund_domain = "펀드" in q
    product_name, product_ref = _first_of_kind(
        entities, "product_kr_etp", "product_global_etf", "product_bond", "product_fund")
    const_name, const_ref = _first_of_kind(entities, "constituent")
    n_consts = len({r.key for _n, refs in entities for r in refs if r.kind == "constituent"})
    comp_name, comp_ref = _first_of_kind(entities, "company")
    idx_name, idx_ref = _first_of_kind(entities, "index")

    # ── 1. 값 도메인 밖 (T-01/02/03) — 근거 없이도 확정 refuse 힌트 ──────────
    if invalid_ratings:
        # 검문소(validation.gate_value_domain)와 같은 문구 — 거절문에서 사유가 중복 표시되지 않게
        plan.notes.append(f"'{invalid_ratings[0]}'는 신용등급 체계(AAA~D)에 존재하지 않는 표기")
        plan.hints["invalid_value"] = invalid_ratings[0]
        return done("invalid_value", "refuse")
    if risk and risk[0] == "invalid":
        plan.notes.append(f"위험등급은 1~6등급 체계 — '{risk[1]}'은 존재하지 않음(0은 미분류 센티널)")
        plan.hints["invalid_value"] = risk[1]
        return done("invalid_value", "refuse")
    if "보수" in q and re.search(r"마이너스|음수", q):
        plan.notes.append("보수율은 음수가 될 수 없는 값 도메인")
        plan.hints["invalid_value"] = "마이너스 보수"
        return done("invalid_value", "refuse")

    # ── 2. 데이터에 없는 자산 (T-06) ─────────────────────────────────────────
    for asset in UNSUPPORTED_ASSETS:
        if asset in q:
            plan.notes.append(f"'{asset}' 유형 자산은 제공 데이터(채권·ETF·ETN·공모펀드)에 없음")
            plan.hints["unsupported_asset"] = asset
            return done("unsupported_asset", "refuse")

    # ── 3. 시간 경계 위반 (T-10/11/12/15) ───────────────────────────────────
    if time_flags.get("realtime"):
        plan.notes.append(f"실시간 시세는 제공 범위 밖 — 데이터는 {AS_OF_MASTER} 스냅샷")
        return done("time_violation", "refuse")
    if time_flags.get("history"):
        plan.notes.append("구성종목은 2026-07-10 단일 스냅샷만 보유 — 과거 시점과의 비교 불가")
        return done("time_violation", "refuse")
    if time_flags.get("post_snapshot"):
        plan.notes.append(f"기준일({AS_OF_MASTER}) 이후({time_flags['post_snapshot']}) 정보는 보유하지 않음")
        return done("time_violation", "refuse")
    if time_flags.get("future") and re.search(r"추천|골라|알려", q):
        plan.notes.append("미래 전망·시장 예측 반영은 제공 불가(단정 추천 금지) — 조건 기반 사실 조회로 전환 가능")
        return done("time_violation", "refuse")

    # ── 4. 원천에 없는 필드 (M-29/T-13/T-14) ────────────────────────────────
    if is_global and "위험" in q and "등급" in q and "국내" not in q:
        plan.notes.append("해외 ETF 원천(PREF02N001)에는 위험등급 컬럼이 없음 — 국내 ETF 는 조회 가능")
        plan.hints["unavailable_field"] = "global_etf.risk_grade"
        return done("unsupported_field", "refuse")
    if is_fund_domain and "보수" in q and not has_etf_word:
        plan.notes.append("공모펀드 원천(PRFD01N001)에는 총보수 필드가 없음(보유: 수익률·위험등급·설정액 등)")
        plan.hints["unavailable_field"] = "fund.total_fee"
        return done("unsupported_field", "refuse")

    # ── 5. 등급 서열 비교 (L-08) — 사전 근거 답변 ────────────────────────────
    if len(ratings) >= 2 and re.search(r"더 높|더 낮|비교|어느|뭐가 높", q):
        plan.hints["rating_compare"] = [(t, r) for t, r, _e in ratings[:2]]
        return done("rating_compare")

    # ── 6. 구성종목 역질의 (M-01~07/16/21/22, H-14) — 교집합(복수 종목)은 Stage B ──
    if const_ref and n_consts == 1 and (
            any(v in q for v in HOLDING_VERBS) or ("구성" not in q and "비중" in q)):
        weight_th = next((v for v, k, d in percents
                          if k in ("weight", "unknown") and d in ("이상", "초과", "넘")), None)
        if weight_th is not None:
            plan.calls.append(ChannelCall("sql", "constituent_weight_above",
                                          {"code": const_ref.key, "min_weight": weight_th,
                                           "limit": limit}))
        else:
            plan.calls.append(ChannelCall("graph", "holding_etfs",
                                          {"query": const_ref.key, "limit": limit}))
            plan.calls.append(ChannelCall("sql", "constituent_holders",
                                          {"code": const_ref.key, "limit": max(limit, 30)}))
        if "순자산" in q or "규모" in q:
            plan.hints["order"] = "aum"
        plan.hints["constituent"] = {"name": const_name, "key": const_ref.key}
        plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일) · 수집분 ETF 기준")
        return done("constituent_reverse")

    # ── 7. 상품 1종 상세·구성·페어 비교 (L-09/10/28, M-25, H-30) ─────────────
    if product_ref and product_ref.kind == "product_kr_etp":
        if "구성" in q and not re.search(r"비교|달라|차이", q):
            plan.calls.append(ChannelCall("sql", "constituent_top_weights",
                                          {"etf_id": product_ref.key, "limit": top_n or 10}))
            plan.notes.append("구성종목 기준일 2026-07-10")
            return done("product_constituents")
        if "구성" not in q:
            second_ref = None
            for name, refs in entities:
                for ref in refs:
                    if ref.kind == "product_kr_etp" and ref.key != product_ref.key:
                        second_ref = ref
            plan.calls.append(ChannelCall("sql", "etp_detail", {"pd_itm_no": product_ref.key}))
            if second_ref:                               # H-30: 페어 비교
                plan.calls.append(ChannelCall("sql", "etp_detail", {"pd_itm_no": second_ref.key}))
                if "보수" in q:
                    plan.calls.append(ChannelCall("sql", "coverage_check",
                                                  {"field": "kr_etp.cu_charge_rt"}))
                    plan.notes.append("총보수는 값 보유 상품이 일부(실질결측 87.5%) — 결측 시 비교 불가를 명시")
                    return done("pair_compare", "partial")
                return done("pair_compare")
            plan.calls.append(ChannelCall("graph", "product_info", {"query": product_name}))
            return done("product_detail")

    # ── 7.5 지수 추종 상품 검색 (M-18/23) — 펀드 문맥은 12번 소관 ────────────
    if idx_ref and re.search(r"추종|따라가|연동|지수", q) and not product_ref and not is_fund_domain:
        for pat in _spacing_variants(idx_name)[:3]:
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": pat, "limit": max(limit, 20)}))
        plan.notes.append("지수 명칭 표기 변형(붙임/띄움)을 함께 검색")
        return done("index_products")

    # ── 8. 펀드 비정형(구조·전략 서술) — 미수집 명시 (M-10) ──────────────────
    if product_ref and product_ref.kind == "product_fund" and re.search(r"구조|전략|동향", q):
        plan.calls.append(ChannelCall("keyword", "lookup", {"query": product_name}))
        plan.notes.append("구조·전략 서술(비정형)은 수집 범위 밖 — 마스터 보유 필드까지만 답변")
        return done("unstructured_info", "partial")

    # ── 8.5 운용사 역질의 (M-09) — 구성 결합(H-08)은 Stage B ────────────────
    if comp_ref and re.search(r"운용|발행", q) and "구성" not in q:
        plan.calls.append(ChannelCall("graph", "company_products",
                                      {"query": comp_ref.key, "limit": max(limit, 10)}))
        plan.calls.append(ChannelCall("sql", "mgmt_top_share", {"limit": 30}))
        plan.hints["company"] = comp_ref.key
        plan.notes.append("운용사 명칭은 오염 정정값(mgmt_resolved — 64건 복구) 기준")
        return done("company_products")

    # ── 9. 채권 (L-01~07/27) — ETF 단어가 있으면 ETP 소관, 잔존만기 복합은 Stage B ──
    if is_bond_domain and not has_etf_word and not is_fund_domain:
        cond, notes = rating_condition(q, policy)
        bond_class = next((v for w, v in BOND_CLASS_MAP if w in q), None)
        buyable = "Y" if re.search(r"판매 가능|매수 가능|매수할 수 있|살 수 있", q) else None
        coupon_min = next((v for v, k, d in percents if k == "coupon" and d in ("이상", "초과", "넘")), None)
        coupon_band = next((v for v, k, d in percents if k == "coupon" and d == "대"), None)

        if "영구채" in q:                                # L-06
            plan.calls.append(ChannelCall("sql", "bond_perpetual_list", {}))
            plan.notes.append("영구채는 만기일 없음(센티널 99991231 은 전처리에서 플래그화)")
            return done("bond_list")
        if re.search(r"각각 몇|별로|종류별", q):          # L-27
            plan.calls.append(ChannelCall("sql", "bond_class_dist", {}))
            return done("bond_dist")
        if currency and ccy_exclude:                     # L-07
            plan.calls.append(ChannelCall("sql", "bond_currency_dist", {}))
            plan.notes.append("통화 미지정 센티널 '000' 제외 분포")
            return done("bond_dist")
        if "잔존만기" in q and any(w in q for w in TOP_WORDS) and "이하" not in q:   # L-04
            plan.notes.extend(notes)
            plan.calls.append(ChannelCall("sql", "bond_top_maturity",
                                          {"bond_class": bond_class, "limit": limit}))
            plan.notes.append(f"잔존만기는 요청 시점({today.isoformat()}) 기준 재계산 값으로 병기")
            if bond_class == "국공채" and "국고채" in q:
                plan.notes.append("'국고채'는 제공 대분류상 국공채로 조회")
            return done("bond_ranking")
        if "잔존만기" in q:                              # H-26 등 복합 — Stage B 소관
            plan.intent = "unresolved"
            return plan, True
        m_within = re.search(r"(\d)\s*년\s*(?:안에|이내)", q)
        if m_within or "만기가 도래" in q or "만기 도래" in q:
            years = int(m_within.group(1)) if m_within else 1
            until = today.replace(year=today.year + years)
            plan.notes.extend(notes)
            plan.calls.append(ChannelCall("sql", "bond_maturing_within",
                                          {"as_of_date": today.isoformat(),
                                           "until": until.isoformat(), "limit": limit}))
            plan.notes.append(f"만기 도래 판정: {today.isoformat()} ~ {until.isoformat()} (요청 시점 기준)")
            return done("bond_ranking")

        wants_active = bool(re.search(r"만기가 안 지|만기 안 지|만기가 지나지", q)) or bool(buyable)
        params = {"currency": currency if not ccy_exclude else None,
                  "bond_class": bond_class, "buyable_only": buyable,
                  "maturity_status": "active" if wants_active else None,
                  "min_coupon": coupon_min if coupon_band is None else coupon_band,
                  "max_coupon": coupon_band + 1 if coupon_band is not None else None}
        params.update(cond)
        params = {k: v for k, v in params.items() if v is not None}
        if buyable:
            plan.notes.append(f"매수가능 판정 기준: {policy['buyable_rule']} 플래그(§8.4 채택 규칙 명시)"
                              " + 만기 경과 채권 제외(플래그가 행별 갱신일 기준이라 만기 상태로 이중 확인)")
        if any(w in q for w in COUNT_WORDS):             # L-02/05
            count_keys = ("currency", "max_rating_rank", "min_rating_rank",
                          "maturity_status", "buyable_only", "bond_class")
            plan.notes.extend(notes)
            plan.calls.append(ChannelCall("sql", "bond_count",
                                          {k: v for k, v in params.items() if k in count_keys}))
            return done("bond_count")
        if params:                                       # L-01/03
            plan.notes.extend(notes)
            filter_params = dict(params)
            filter_params.pop("min_rating_rank", None)   # bond_filter 는 상한만 받는다
            filter_params["limit"] = max(limit, 20)
            plan.calls.append(ChannelCall("sql", "bond_filter", filter_params))
            count_keys = ("currency", "max_rating_rank", "min_rating_rank",
                          "maturity_status", "buyable_only", "bond_class")
            plan.calls.append(ChannelCall("sql", "bond_count",
                                          {k: v for k, v in params.items() if k in count_keys}))
            return done("bond_filter")

    # ── 10. 해외 ETF (L-17~20) ──────────────────────────────────────────────
    if is_global:
        if any(w in q for w in COUNT_WORDS):             # L-17
            plan.calls.append(ChannelCall("sql", "global_etf_count", {}))
            plan.notes.append("ETF/ETN 혼재 원천 — 유형 구분 건수로 답변")
            return done("global_count")
        if "인버스" in q and not re.search(r"레버리지|곱버스", q):   # L-18
            plan.calls.append(ChannelCall("sql", "global_etf_filter",
                                          {"inverse_only": "Y", "limit": max(limit, 20)}))
            return done("global_filter")
        if currency == "USD" and ccy_exclude:            # L-20
            plan.calls.append(ChannelCall("sql", "global_ccy_dist", {}))
            return done("global_dist")
        region = next((t for t in theme_hits if t in REGIONS), None)
        if region and not non_region_themes and re.search(r"투자|상품|ETF", q):   # L-19
            for pat in ([region] + themes.get(region, []))[:2]:
                plan.calls.append(ChannelCall("sql", "global_etf_filter",
                                              {"region_pattern_raw": pat, "limit": max(limit, 20)}))
            plan.notes.append(f"투자지역(wu_inv_rgn) '{region}' 표기 변형(한/영)을 함께 검색")
            return done("global_filter")

    # ── 11. 국내 ETP (L-10~16/26/30, M-15/17, H-29) — 구성종목 결합 질의(H-03
    #        교집합 등)는 편입 동사+종목이 있으면 여기서 가로채지 않는다(Stage B 소관)
    holding_ctx = n_consts > 0 and any(v in q for v in HOLDING_VERBS)
    if not holding_ctx and (
            has_etf_word or (not is_global and not is_bond_domain and not is_fund_domain
                             and re.search(r"레버리지|인버스|커버드콜", q))):
        itype = "ETN" if re.search(r"ETN", q) and not re.search(r"ETF", q, re.IGNORECASE) else "ETF"
        if currency and ccy_exclude and ("국내" in q or "ETP" in q.upper()):   # L-30
            plan.calls.append(ChannelCall("sql", "etp_currency_dist", {}))
            return done("etp_dist")
        if "운용사" in q and any(w in q for w in TOP_WORDS):        # H-29
            plan.calls.append(ChannelCall("sql", "mgmt_top_share", {"limit": top_n or 10}))
            plan.notes.append("운용사 명칭은 오염 정정값(mgmt_resolved — 64건 복구) 기준 집계")
            return done("mgmt_ranking", "partial")
        if any(w in q for w in COUNT_WORDS) and not is_global and not comp_ref:   # L-13
            plan.calls.append(ChannelCall("sql", "etp_count", {}))
            plan.notes.append("전체/상장중(active) 건수를 구분해 답변")
            return done("etp_count")
        if re.search(r"순자산|AUM|규모", q, re.IGNORECASE) and any(w in q for w in TOP_WORDS):  # L-11
            plan.calls.append(ChannelCall("sql", "etp_top_aum",
                                          {"instrument_type": itype, "limit": top_n or 5}))
            plan.notes.append("상장중(active) 기준 · ETF/ETN 구분 적용")
            return done("etp_ranking")
        if "수익률" in q and any(w in q for w in TOP_WORDS) and not is_fund_domain:  # L-14/M-15
            metric = "1y" if re.search(r"1\s*년|일 년|최근 1년", q) else "ytd"
            params = {"metric": metric, "limit": top_n or 5}
            if risk and risk[0] != "invalid":
                params["min_risk"], params["max_risk"] = risk[0], risk[1]
                plan.notes.extend(risk[2])
            plan.calls.append(ChannelCall("sql", "etp_top_return", params))
            if metric == "ytd":
                plan.notes.append("YTD = 2026-01-01 ~ 2026-07-11 (기준일까지)")
            return done("etp_ranking")
        if "보수" in q and re.search(r"이하|미만|낮|싼|저렴", q):    # L-26
            fee_th = next((v for v, k, _d in percents if k == "fee"), None)
            plan.calls.append(ChannelCall("sql", "etp_low_fee",
                                          {"max_fee": fee_th if fee_th is not None else 100.0,
                                           "limit": max(limit, 20)}))
            plan.calls.append(ChannelCall("sql", "coverage_check", {"field": "kr_etp.cu_charge_rt"}))
            plan.notes.append("총보수는 값 보유 상품 기준(실질결측 87.5%) · 값 0의 의미 미확정 — 커버리지 명시 필수")
            return done("etp_fee_filter", "partial")
        if risk and risk[0] != "invalid" and "수익률" not in q:      # L-12
            plan.calls.append(ChannelCall("sql", "etp_filter_risk",
                                          {"instrument_type": itype, "min_grade": risk[0],
                                           "max_grade": risk[1], "limit": max(limit, 20)}))
            plan.notes.extend(risk[2])
            return done("etp_filter")
        m_year = _YEAR_RE.search(q)
        if re.search(r"새로 상장|신규 상장|들어서.{0,8}상장|최근.{0,8}상장", q):   # L-16/H-28
            if m_year and int(m_year.group(1)) == int(AS_OF_MASTER[:4]):
                date_from = f"{m_year.group(1)}-01-01"
            else:
                months = policy["recent_window_months"]
                anchor = datetime.date.fromisoformat(AS_OF_MASTER)
                date_from = (anchor - datetime.timedelta(days=months * 30)).isoformat()
                plan.notes.append(f"'최근' = 기준일 직전 {months}개월({date_from}~)로 해석")
            plan.calls.append(ChannelCall("sql", "etp_listed_between",
                                          {"date_from": date_from, "date_to": AS_OF_MASTER,
                                           "limit": max(limit, 20)}))
            plan.notes.append(f"기준일({AS_OF_MASTER}) 이후 상장분은 데이터 범위 밖")
            return done("etp_listed")
        if m_year and "만기" in q:                       # M-17: 만기형 존속기한 명명
            yy = m_year.group(1)[2:]
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": f"{yy}-", "limit": max(limit, 20)}))
            plan.notes.append(f"만기형 ETF 는 상품명 존속기한 표기({yy}-06·{yy}-12 등)로 식별")
            return done("etp_name_search")
        for kw in ("곱버스", "레버리지", "인버스", "커버드콜", "TDF", "나스닥100", "코스닥150"):
            if kw in q:                                  # L-15, H-11/16/24
                terms = ["인버스", "2X"] if kw == "곱버스" else [kw]
                for t in terms:
                    plan.calls.append(ChannelCall("sql", "etp_name_search",
                                                  {"pattern_raw": t, "limit": max(limit, 20)}))
                if kw == "곱버스":
                    plan.notes.append("'곱버스'=레버리지 인버스(-2X) — 상품명 인버스+2X 조합으로 검색")
                return done("etp_name_search")

    # ── 12. 공모펀드 (L-21~25) ──────────────────────────────────────────────
    if is_fund_domain:
        if any(w in q for w in COUNT_WORDS) and "클래스" not in q:   # L-21
            plan.calls.append(ChannelCall("sql", "fund_counts", {}))
            plan.notes.append("상품(마스터) 수와 판매 클래스 수는 다름 — 구분해 답변")
            return done("fund_count")
        attr = next((w for w in ("주식형", "채권형", "혼합형", "재간접", "MMF") if w in q), None)
        if "수익률" in q and any(w in q for w in TOP_WORDS):         # L-24
            plan.calls.append(ChannelCall("sql", "fund_top_return_1y",
                                          {"on_sale_only": "Y" if "판매" in q else None,
                                           "limit": top_n or 5}))
            plan.calls.append(ChannelCall("sql", "coverage_check",
                                          {"field": "fund_master.fd_yr1_ern_r"}))
            plan.notes.append("1년 수익률 값 보유 상품 기준 — 커버리지 명시")
            return done("fund_ranking", "partial")
        if re.search(r"벤치마크|추종|따라가|삼는", q):               # L-25
            target = idx_name or next((w for w in ("KOSPI200", "코스피200", "코스피 200") if w in q), None)
            if target:
                patterns = set(_spacing_variants(target))
                if "코스피" in target or "kospi" in target.casefold():
                    patterns.update({"KOSPI200", "KOSPI 200", "코스피200", "코스피 200"})
                for p in sorted(patterns)[:4]:
                    plan.calls.append(ChannelCall("sql", "fund_by_benchmark",
                                                  {"pattern_raw": p, "limit": max(limit, 20)}))
                plan.notes.append("벤치마크 표기 변형(한/영·붙임/띄움)을 함께 검색")
                return done("fund_by_benchmark")
        if risk and risk[0] != "invalid":                 # L-23
            plan.calls.append(ChannelCall("sql", "fund_filter",
                                          {"min_risk": risk[0], "max_risk": risk[1],
                                           "limit": max(limit, 20)}))
            plan.notes.extend(risk[2])
            return done("fund_filter")
        if attr or "판매 중" in q or "판매중" in q:        # L-22
            params = {"limit": max(limit, 20)}
            if attr:
                params["attr_pattern_raw"] = attr
            if "판매" in q:
                params["on_sale_only"] = "Y"
            plan.calls.append(ChannelCall("sql", "fund_filter", params))
            return done("fund_filter")

    # ── 13. 미등록 개체·상품 존재 질의 (T-04~09) — 비대칭 원칙:
    #        이름 일부라도 실제 상품명 안에 등장하면(CSI300 등) 거절이 아니라
    #        이름 검색으로 답하고, 부분 일치조차 0건일 때만 거절 후보로 본다.
    brand_hit = next((b for b in BRAND_TOKENS if b in q), None)
    asks_specific = bool(re.search(r"정보|알려|수익률|보수|어때|있어|찾아", q))
    unfindable = [t for t in unknown if not token_matches(index, t, limit=1)]
    findable = [t for t in unknown if t not in unfindable]
    if (unfindable or (brand_hit and not product_ref)) and asks_specific and not plan.calls:
        target = unfindable[0] if unfindable else \
            re.sub(r"정보|알려줘|알려|수익률|어때|찾아줘|있어|\?|관련|투자|상품", " ", q).strip()
        plan.calls.append(ChannelCall("keyword", "lookup",
                                      {"query": target,
                                       "limit": policy["trap_similar_suggest_limit"]}))
        plan.hints["existence_query"] = target
        plan.notes.append("기준일 데이터에서 직접 매칭 확인 필요 — 부분 일치는 유사 상품 안내까지만(존재 근거 아님)")
        return done("existence_check", "refuse")
    if findable and asks_specific and not plan.calls:          # M-23: CSI300 등 명칭 토큰 검색
        for t in findable[:2]:
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": t, "limit": max(limit, 20)}))
            plan.calls.append(ChannelCall("keyword", "lookup", {"query": t, "limit": 5}))
        return done("name_token_search")

    # ── 14. 테마 검색 (M-11~13/24/26/28, H-04) — 벡터+키워드+상품명 결합 ─────
    if (non_region_themes or "테마" in q or (is_global and theme_hits)) \
            and re.search(r"투자하는|투자하|전략|중심|집중|테마|찾아|알려|골라|있어", q) \
            and not product_ref and (not is_bond_domain or has_etf_word):
        if is_global or "해외" in q or not non_region_themes:
            plan.calls.append(ChannelCall("vector", "semantic", {"query": q, "k": 8}))
        elif "국내" not in q:
            plan.calls.append(ChannelCall("vector", "semantic", {"query": q, "k": 8}))
        for t in (non_region_themes or theme_hits)[:2]:
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": t, "limit": 10}))
            plan.calls.append(ChannelCall("keyword", "lookup", {"query": t, "limit": 5}))
        plan.notes.append("테마 판정 기준: 상품명(국내)·전략 서술(해외) 매칭 — 의미 검색은 키워드 근거와 결합(RRF)")
        return done("theme_search")

    # ── 확정 실패 — Stage B(LLM) 로 ────────────────────────────────────────
    plan.intent = "unresolved"
    return plan, True


# ---------------------------------------------------------------------------
# 폴백 플랜 — LLM 까지 실패해도 플랜은 반드시 나온다 (S2_PLAN: 규칙 기반 기본 플랜)
# ---------------------------------------------------------------------------

def fallback_plan(question, partial_plan, policy=None):
    policy = policy or load_policy()
    plan = partial_plan
    plan.stage = "fallback"
    if plan.intent in ("", "unresolved"):
        plan.intent = "keyword_fallback"
    if not plan.calls:
        for name, _refs in plan.entities[:3]:
            plan.calls.append(ChannelCall("keyword", "lookup", {"query": name, "limit": 5}))
        if not plan.entities:
            plan.calls.append(ChannelCall("keyword", "lookup",
                                          {"query": question, "limit": policy["default_limit"]}))
        if "해외" in question or "테마" in question:
            plan.calls.append(ChannelCall("vector", "semantic", {"query": question, "k": 8}))
        plan.notes.append("규칙·LLM 라우팅 미확정 — 키워드 기반 안내 플랜으로 폴백")
    return plan


def route(question, index, policy=None, today=None, llm_router=None):
    """오케스트레이션: Stage A → (미확정 시) Stage B(llm_router 주입) → 폴백.

    llm_router: callable(question, partial_plan) -> RoutePlan | None.
    주입식이라 오프라인(테스트)에서는 규칙+폴백만으로 결정적으로 동작한다.
    """
    policy = policy or load_policy()
    plan, needs_llm = route_stage_a(question, index, policy, today)
    if not needs_llm:
        return plan
    if llm_router is not None:
        llm_plan = llm_router(question, plan)
        if llm_plan is not None:
            return llm_plan
    return fallback_plan(question, plan, policy)
