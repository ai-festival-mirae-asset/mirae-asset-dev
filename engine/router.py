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
import calendar
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
from pipeline.query_aliases import (load_product_query_aliases,  # noqa: E402
                                    normalize_product_query)
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

# 행위 요청(주문·매매·환매·가입) — 정보 조회 전용 서비스의 범위 밖 (8/22 블라인드 v2 T-15)
_ACTION_REQUEST_RE = re.compile(
    r"매수\s*주문|매도\s*주문|주문(을|도)?\s*(넣|해|걸|내|해\s*줘)|사\s*줘|사줘|팔아\s*줘|팔아줘|"
    r"매수해\s*줘|매도해\s*줘|매수해줘|매도해줘|거래해\s*줘|환매해\s*줘|가입해\s*줘|신청해\s*줘|매수\s*해\s*주|매도\s*해\s*주")

# 데이터에 없는 자산 유형 — 직접 refuse 사유 (T-06)
UNSUPPORTED_ASSETS = ("코인", "가상자산", "암호화폐", "비트코인", "크립토")

# 존재 검증을 우회하려는 그럴듯한 상품명의 브랜드 접두 감지 (T-07/08 —
# mgmt_resolution.BRAND_PREFIX_TO_COMPANY 와 동기)
BRAND_TOKENS = tuple(dict.fromkeys(
    ("KODEX", "TIGER", "KoAct", "SOL", "PLUS", "ARIRANG", "RISE",
     "KBSTAR", "ACE", "KOSEF", "히어로즈", "HANARO", "1Q", "마이티", "WON")
    + tuple(load_product_query_aliases().values())))

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


_INVALID_SIGN_RE = re.compile(r"(?<![A-Za-z])(AAA|AA|A|BBB|BB|B|CCC|CC|C|D)([+\-]{2,})(?![A-Za-z0-9])")  # BBB++, AA--


def extract_ratings(question):
    """유효 등급 토큰 [(표기, rank, 끝위치)] + 도메인 밖 표기 목록 (T-01 방어).

    부호가 둘 이상 붙은 'BBB++'(8/22 v2 T-09)는 등급 체계 밖 — 'BBB+'로 읽지 않는다.
    """
    invalid = [m.group(0) for m in _INVALID_RATING_RE.finditer(question)]
    bad_spans = [(m.start(), m.end()) for m in _INVALID_SIGN_RE.finditer(question)]
    invalid += [question[s:e] for s, e in bad_spans]
    valid = []
    for m in _RATING_RE.finditer(question):
        if any(s <= m.start() < e for s, e in bad_spans):
            continue
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
    if re.search(r"지금 주가|현재가|실시간|현재 주가|시세", question) or \
            re.search(r"(오늘|지금|현재)\s*(의)?\s*(코스피|코스닥|나스닥|다우|S&P|환율|기준금리)", question, re.I) or \
            re.search(r"(코스피|코스닥|나스닥|다우)\s*(지수)?\s*(몇|얼마|어때)", question):
        flags["realtime"] = True                         # "오늘 코스피 지수 몇이야?"(v2 T-05) 포함
    if re.search(r"다음\s*달|내년|앞으로|전망|예측|가능성을 반영", question):
        flags["future"] = True
    ay, am = int(as_of[:4]), int(as_of[5:7])
    for m in _YEAR_RE.finditer(question):
        y, mo = int(m.group(1)), int(m.group(2) or 0)
        if (y, mo or 1) > (ay, am) and re.search(r"상장|출시|나온|생긴|설정된", question):
            flags["post_snapshot"] = f"{y}년{f' {mo}월' if mo else ''}"
    if re.search(r"((\d+\s*(년|개월|달|주|일)|한 해|일 년|반년)\s*전|작년|지난\s*(달|해)|예전|과거)", question) \
            and re.search(r"구성|비교", question):
        flags["history"] = True                          # T-15·v2 T-12: 구성 이력은 단일 스냅샷("6개월 전" 포함)
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
        # 띄어쓰기 없는 질문("레버리지ETF찾아줘"·"신용등급AA이상")에서 한글에 붙은 라틴 부분이
        # 일반어(ETF 등)·등급 기호이면 미등록 개체가 아니다(8/22 v2 P-02/05/08/11 과잉 거절 실측)
        latin = re.sub(r"^[가-힣]+", "", tok)
        if latin.casefold() in LATIN_STOPWORDS or _RATING_RE.fullmatch(latin) or _INVALID_RATING_RE.fullmatch(latin):
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

_RATING_NAME_RE = re.compile(r"(aaa|aa|a|bbb|bb|b|ccc|cc|c|d)")


def ground_with_alias_fallback(index, question):
    """(정규화 질의, grounding) — 원문이 이미 상품명을 정확히 품고 있으면 별칭 치환을 하지 않는다.

    8/22 v2 L-10 실측: 펀드명 "KB스타골드…" 속 'KB스타'를 브랜드 별칭 치환이 'RISE'로 바꿔
    펀드를 못 찾고 "RISE골드… 상품 없음"으로 거절했다. 별칭 치환은 원문에서 상품을 못 찾을 때만.
    """
    raw = ground_entities(index, question)
    if any(r.kind.startswith("product") for _n, refs in raw for r in refs):
        return question, raw
    normalized = normalize_product_query(question)
    if normalized == question:
        return question, raw
    return normalized, ground_entities(index, normalized)


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
        # 신용등급 문맥의 'BBB'·'AA' 는 등급 기호이지 티커(BBB.O 등)가 아니다(8/22 v2 T-09 실측)
        if _RATING_NAME_RE.fullmatch(name) and re.search(r"신용|등급|rating|[+\-]", question, re.I):
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


_GENERIC_PRODUCT_TERMS = {
    "etf", "etn", "etp", "상품", "정보", "구성", "구성종목", "종목",
    "알려", "알려줘", "찾아", "찾아줘", "투자", "투자하", "투자하는",
}


def resolve_product_by_terms(index, question, kinds=("product_kr_etp",)):
    """설명형 상품명에서 식별력 있는 토큰 교집합으로 유일한 상품을 찾는다.

    전체 상품명이 질문에 그대로 나오지 않는 M-30 같은 표현을 위한 보수적
    보완이다. 두 개 이상의 토큰이 같은 상품을 가리키고 최고점이 유일할 때만
    채택해, 단일 일반어로 임의 상품을 고르는 일을 막는다.
    """
    tokens = []
    for raw in re.findall(r"[가-힣A-Za-z0-9+]+", normalize_product_query(question)):
        term = raw
        for suffix in ("으로", "에서", "에게", "에는", "에", "의", "을", "를", "이", "가", "은", "는"):
            if term.endswith(suffix) and len(term) > len(suffix) + 1:
                term = term[:-len(suffix)]
                break
        if len(term) < 2 or term.casefold() in _GENERIC_PRODUCT_TERMS:
            continue
        if term.startswith(("알려", "찾아", "투자")):
            continue
        if term not in tokens:
            tokens.append(term)

    scored = {}
    for term in tokens:
        for _name, ref in index.search(term, limit=100, kinds=kinds):
            item = scored.setdefault((ref.kind, ref.key), {"ref": ref, "terms": set(), "chars": 0})
            if term not in item["terms"]:
                item["terms"].add(term)
                item["chars"] += len(term)
    ranked = sorted(scored.values(), key=lambda x: (-len(x["terms"]), -x["chars"], x["ref"].key))
    if not ranked or len(ranked[0]["terms"]) < 2:
        return None
    if len(ranked) > 1 and (len(ranked[0]["terms"]), ranked[0]["chars"]) == \
            (len(ranked[1]["terms"]), ranked[1]["chars"]):
        return None
    return "+".join(sorted(ranked[0]["terms"])), ranked[0]["ref"]


# 상품명 조각으로 볼 수 없는 일반어 — 후보 문자열에서 제외 (8/19 ⑧)
_PRODUCT_FRAGMENT_STOPWORDS = _GENERIC_PRODUCT_TERMS | {
    "국내", "해외", "상장", "상품", "주식", "채권", "펀드", "옵션", "실제로", "뭘", "무엇",
    "있다던데", "있어", "있으면", "관련", "계열사", "계열", "그룹", "운용하는", "운용",
    "구성", "비중", "상위", "종목", "보여줘", "정리해줘", "정리", "위험등급", "같이",
    "지수", "추종", "몇", "퍼센트", "담고", "담은", "들고", "편입", "보유", "중에", "테마", "특성",
    "궁금해", "궁금", "위험", "수익률", "종류", "정보",
    "중국", "미국", "일본", "인도", "유럽", "베트남", "브라질", "신흥국", "빅테크",
}
_PARTICLE_SUFFIXES = ("으로", "에서", "에게", "에는", "이랑", "랑", "에", "의", "을", "를",
                      "이", "가", "은", "는", "도", "과", "와")


def _strip_particle(term):
    for suffix in _PARTICLE_SUFFIXES:
        if term.endswith(suffix) and len(term) > len(suffix) + 1:
            return term[:-len(suffix)]
    return term


def resolve_product_candidates(index, question, kinds=("product_kr_etp",), max_products=12,
                               min_chars=4):
    """질문 속 연속 어절 조합(1~3어절, 공백 제거)으로 상품명을 부분 일치 검색해
    가장 긴 조합의 후보 상품 목록을 돌려준다 — (일치 문자열, [EntityRef]) 또는 None.

    왜: '애플 밸류체인에 투자하는 ETF' · '위클리 커버드콜 ETF' · '한화그룹주 ETF' 처럼
    상품명 일부만 띄어쓰기를 달리해 부르는 질문(M-19·H-10·H-20)은 전체 명칭 grounding
    이 실패한다. 상품명 조각(4자 이상)이 실제 상품명 안에 연속으로 보이면 그 상품(들)을
    후보로 삼는다. 후보가 max_products 를 넘으면 너무 일반적인 조각으로 보고 버린다.
    조각이 그대로 다른 종류의 개체명(종목·회사 — '삼성전자'·'구글')이면 상품 조각으로
    보지 않는다(구성종목 역질의를 가로채지 않기 위해).
    """
    words = [_strip_particle(w) for w in re.findall(r"[가-힣A-Za-z0-9+&.\-]+", normalize_product_query(question))]
    words = [w for w in words if w and w.casefold() not in _PRODUCT_FRAGMENT_STOPWORDS
             and not w.startswith(("알려", "찾아", "보여", "투자"))]
    def _search(cand):
        # 한 상품이 별칭 변형(브랜드 한/영 표기 등)으로 여러 이름을 가지므로 limit 은 넉넉히 —
        # 400 이면 '레버리지'처럼 흔한 조각에서 뒤쪽 상품(SOL 조선TOP3플러스레버리지)이 잘린다(8/19 실측)
        refs, seen = [], set()
        for _name, ref in index.search(cand, limit=5000, kinds=kinds):
            if ref.key not in seen:
                seen.add(ref.key)
                refs.append(ref)
        return refs

    best = None
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            frag = "".join(words[i:i + n])
            frag_stripped = re.sub(r"(펀드|etf|etn|상품)$", "", frag, flags=re.I) or frag
            for cand in dict.fromkeys((frag, frag_stripped)):
                if len(cand) < min_chars:
                    continue
                other_kinds = [r for r in index.exact(cand) if not r.kind.startswith("product")]
                if other_kinds:
                    continue
                refs = _search(cand)
                if refs and len(refs) <= max_products:
                    if n == 2:
                        # 붙여 쓴 조각('방산레버리지')이 한두 상품에만 우연히 들어 있어도, 두 말을 각각 가진 상품
                        # 전체('방산' ∩ '레버리지' = 3종)가 더 넓고 소수면 그쪽이 질문의 뜻에 가깝다(M-20)
                        a, b = _search(words[i]), _search(words[i + 1])
                        keys_b = {r.key for r in b}
                        both = [r for r in a if r.key in keys_b]
                        if len(both) > len(refs) and len(both) <= max_products \
                                and {r.key for r in refs} <= {r.key for r in both}:
                            cand, refs = words[i] + "+" + words[i + 1], both
                    if best is None or len(cand) > len(best[0]):
                        best = (cand, refs)
        if best:
            return best
    # 떨어져 있는 조각의 교집합 — '방산 테마 레버리지 ETF' → '방산' ∩ '레버리지'(M-20/H-21).
    # 각 조각(2자 이상)이 단독으로는 너무 일반적이어도, 둘 다 이름에 든 상품이 소수면 그 상품이다.
    # '조선업'처럼 업종 접미어가 붙은 말은 접미어를 뗀 형태('조선')도 함께 본다.
    parts = []
    for w in words:
        if len(w) < 2 or any(not r.kind.startswith("product") for r in index.exact(w)):
            continue
        parts.append(w)
        if len(w) >= 3 and w.endswith(("업", "주", "산업")):
            base = re.sub(r"(산업|업|주)$", "", w)
            if len(base) >= 2 and base not in parts:
                parts.append(base)
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            if parts[i] in parts[j] or parts[j] in parts[i]:
                continue                                  # 같은 말의 변형끼리는 교집합이 아니다
            a, b = _search(parts[i]), _search(parts[j])
            if not a or not b:
                continue
            keys_b = {r.key for r in b}
            both = [r for r in a if r.key in keys_b]
            if both and len(both) <= max_products:
                return (parts[i] + "+" + parts[j], both)
    return None


# 해외ETF 투자지역(wu_inv_rgn) 영문 표기 — '미국 말고' 같은 지역 제외 조건에 쓴다(H-18)
REGION_INV_RGN_EN = {"미국": "United States", "중국": "China", "일본": "Japan", "유럽": "Europe",
                     "인도": "India", "신흥국": "Emerging", "브라질": "Brazil", "베트남": "Vietnam"}
_REGION_EXCLUDE_RE = re.compile(r"(미국|중국|일본|유럽|인도|신흥국|브라질|베트남)\s*(말고|외에|외의|외|제외|이\s*아닌|가\s*아닌|빼고|이외)")


def detect_region_exclusion(question):
    """'미국 말고 다른 지역' → '미국' (제외할 지역) / 없으면 None."""
    m = _REGION_EXCLUDE_RE.search(question)
    return m.group(1) if m else None


# 지역 테마의 상품명 표기 변형(한/영) — 상품명 검색용 (theme_ko_en.csv 는 영문 anchor 만 가짐)
REGION_NAME_VARIANTS = {
    "중국": ("중국", "차이나", "China"), "미국": ("미국", "US", "S&P", "나스닥"),
    "일본": ("일본", "재팬", "Japan"), "인도": ("인도", "India"), "유럽": ("유럽", "Europe"),
    "베트남": ("베트남", "Vietnam"), "신흥국": ("신흥국", "이머징"), "브라질": ("브라질", "Brazil"),
}


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

    normalized_q, entities = ground_with_alias_fallback(index, q)
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
    # 같은 이름(별칭)이 복수 상장(구글=알파벳 A/C, 알리바바=홍콩/ADR)을 가리키면 한 개체로 센다 —
    # 키 수(n_consts)로 세면 '구글 담은 ETF'가 교집합/미확정으로 새어 나간다(8/19 M-22·H-27 실측)
    const_groups = [(n, [r for r in refs if r.kind == "constituent"]) for n, refs in entities]
    const_groups = [(n, rs) for n, rs in const_groups if rs]
    n_const_groups = len(const_groups)
    comp_name, comp_ref = _first_of_kind(entities, "company")
    idx_name, idx_ref = _first_of_kind(entities, "index")

    if not product_ref and has_etf_word and re.search(r"구성|정보|상세", q):
        fuzzy_product = resolve_product_by_terms(index, q)
        if fuzzy_product:
            matched_terms, product_ref = fuzzy_product
            product_name = product_ref.display
            plan.entities.append((matched_terms, [product_ref]))
            plan.hints["fuzzy_product_terms"] = matched_terms

    if normalized_q != q:
        plan.hints["normalized_product_query"] = normalized_q

    # 금융상품 위험등급은 숫자가 작을수록 위험이 높다. 생성 모델이 방향을
    # 뒤집는 오류(H-06)를 막기 위해 근거 노트를 강제하고 규칙 요약을 사용한다.
    if "위험등급" in q and not is_bond_domain:
        plan.notes.append("위험등급 체계: 1등급=매우 높은 위험, 6등급=매우 낮은 위험"
                          "(숫자가 작을수록 위험이 높음)")
        plan.hints["skip_generation"] = True

    # ── 0. 행위 요청·데이터 밖 항목·값 도메인·미존재 운용사 (8/22 블라인드 v2 함정 실측) ──
    if _ACTION_REQUEST_RE.search(q):
        plan.notes.append("매수·매도·주문·환매 같은 행위 수행은 제공 범위 밖(정보 조회 전용 서비스)")
        plan.hints["unsupported_request"] = "action"
        return done("action_request", "refuse")
    if re.search(r"배당락|배당\s*기준일|배당\s*지급일|분배금\s*지급|분배락", q):
        plan.notes.append("원천 데이터에 배당락일·배당(분배금) 지급일 항목이 없음")
        plan.hints["unavailable_field"] = "dividend_dates"
        return done("unsupported_field", "refuse")
    m_coupon = re.search(r"표면금리\s*(\d+(?:\.\d+)?)\s*%", q)
    if m_coupon and float(m_coupon.group(1)) > 100:
        plan.notes.append(f"표면금리 {m_coupon.group(1)}% 는 값 도메인(0~100%) 밖 — 데이터 최대 표면금리는 약 34%")
        plan.hints["invalid_value"] = f"표면금리 {m_coupon.group(1)}%"
        return done("invalid_value", "refuse")
    m_co = re.search(r"([가-힣A-Za-z0-9&]+(?:자산운용|투자신탁운용|투자운용))", q)
    if m_co and not comp_ref and not product_ref and not index.exact(m_co.group(1)) \
            and not index.search(m_co.group(1), limit=1):
        plan.notes.append(f"'{m_co.group(1)}' 운용사는 기준일 데이터의 운용사 목록(국내·해외 ETF 원천)에 없음")
        plan.hints["existence_query"] = m_co.group(1)
        return done("existence_check", "refuse")

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
    global_product = bool(product_ref and product_ref.kind == "product_global_etf")
    if (is_global or global_product) and "위험" in q and "등급" in q and "국내" not in q:
        plan.notes.append("해외 ETF 원천(PREF02N001)에는 위험등급 컬럼이 없음 — 국내 ETF 는 조회 가능")
        plan.hints["unavailable_field"] = "global_etf.risk_grade"
        return done("unsupported_field", "refuse")
    if is_fund_domain and "타사" in q and "판매" in q:
        plan.notes.append("공모펀드 원천에는 전체 판매상태와 당사판매여부만 있으며 타사 판매사 식별 항목은 없음")
        plan.hints["unavailable_field"] = "fund.third_party_seller"
        return done("unsupported_field", "refuse")
    if is_fund_domain and "보수" in q and not has_etf_word:
        plan.notes.append("공모펀드 원천(PRFD01N001)에는 총보수 필드가 없음(보유: 수익률·위험등급·설정액 등)")
        plan.hints["unavailable_field"] = "fund.total_fee"
        return done("unsupported_field", "refuse")

    # ── 4.5 펀드 클래스 사전 설명 (L-29) ──────────────────────────────────
    if is_fund_domain and "클래스" in q and re.search(r"A\s*형", q, re.I) and \
            re.search(r"C\s*형", q, re.I):
        plan.calls.append(ChannelCall("keyword", "fund_class_dictionary",
                                      {"classes": ["A", "C"]}))
        plan.notes.append("KOFIA 펀드 클래스 코드 사전의 A·C 정의를 근거로 비교")
        plan.hints["skip_generation"] = True
        return done("fund_class_compare")

    # ── 5. 등급 서열 비교 (L-08) — 사전 근거 답변 ────────────────────────────
    if len(ratings) >= 2 and re.search(r"더 높|더 낮|비교|어느|뭐가 높", q):
        plan.hints["rating_compare"] = [(t, r) for t, r, _e in ratings[:2]]
        return done("rating_compare")

    # ── 5.5 상품군 횡단 위험등급 집계 (H-13) ──────────────────────────────
    if risk and risk[0] != "invalid" and risk[0] == risk[1] and "상품군별" in q:
        plan.calls.append(ChannelCall("sql", "risk_grade_product_counts",
                                      {"grade": risk[0]}))
        plan.notes.extend(risk[2])
        plan.notes.append("국내채권은 원천의 상품 위험등급(drv_risk_grade) 1~6을 전체 마스터 기준으로 포함")
        plan.notes.append("상품군별 상태 범위: 국내 ETF·ETN은 상장 active, 공모펀드·국내채권은 전체 마스터")
        plan.notes.append("해외 ETF 원천에는 위험등급 필드가 없어 집계 불가")
        plan.hints["skip_generation"] = True
        return done("risk_grade_cross_counts", "partial")

    # ── 5.6 회사채 ETF 구성채권-마스터 조인 (H-15) ────────────────────────
    if has_etf_word and "회사채" in q and "신용등급" in q and "분포" in q:
        plan.calls.append(ChannelCall("sql", "bond_etf_rating_dist", {}))
        plan.notes.append("상품명에 '회사채'가 표시된 ETF의 BN 구성종목을 대상으로 집계")
        plan.notes.append("채권 마스터 키 미매칭 또는 등급 결측은 '미확인'으로 유지한 부분 집계")
        plan.hints["skip_generation"] = True
        plan.hints["display_rows"] = 10
        return done("bond_etf_rating_dist", "partial")

    # ── 5.7 자회사 관계 미수집 상태의 보수적 대체 조회 (H-01) ────────────
    if has_etf_word and "자회사" in q:
        base = comp_name or const_name
        candidates = []
        if base:
            base_norm = re.sub(r"\s+", "", base).casefold()
            seen = set()
            for _name, ref in index.search(base, limit=30, kinds=("constituent",)):
                display_norm = re.sub(r"\s+", "", ref.display).casefold()
                # 국내 상장사 후보만 사용한다. 이름에 회사명이 들어간 선물·채권
                # 종목을 자회사 후보로 오인하지 않도록 6자리 주식코드로 제한한다.
                if not re.fullmatch(r"\d{6}", ref.key) or ref.key in seen or display_norm == base_norm:
                    continue
                seen.add(ref.key)
                candidates.append(ref)
        plan.calls.append(ChannelCall("keyword", "lookup", {"query": base or q, "limit": 10}))
        for ref in candidates[:4]:
            plan.calls.append(ChannelCall("graph", "holding_etfs",
                                          {"query": ref.key, "limit": 30}))
        if candidates:
            code_params = {f"code_{chr(ord('a') + i)}": ref.key
                           for i, ref in enumerate(candidates[:4])}
            code_params["limit"] = 30
            plan.calls.append(ChannelCall("sql", "constituent_candidate_holders_by_aum",
                                          code_params))
        plan.hints["order"] = "aum"
        plan.hints["skip_generation"] = True
        plan.notes.append("자회사 법적 관계(subsidiaryOf)는 미수집 — 회사명이 포함된 개별 종목을 후보로만 조회")
        plan.notes.append("구체적 위험요인 자료는 미수집 — 조회된 ETF의 상품 위험등급만 안내")
        return done("subsidiary_holding_candidates", "partial")

    # ── 5.8 복수 구성종목 교집합 + 총보수 정렬 (H-03) — 서로 다른 이름 2개(별칭의 복수 상장은 1개) ──
    constituent_refs = [rs[0] for _n, rs in const_groups]
    if len(constituent_refs) >= 2 and "보수" in q and any(w in q for w in TOP_WORDS):
        first, second = constituent_refs[:2]
        for ref in (first, second):
            plan.calls.append(ChannelCall("graph", "holding_etfs",
                                          {"query": ref.key, "limit": 1000}))
        plan.calls.append(ChannelCall("sql", "constituent_intersection_low_fee",
                                      {"code_a": first.key, "code_b": second.key,
                                       "limit": max(limit, 20)}))
        plan.calls.append(ChannelCall("sql", "coverage_check",
                                      {"field": "kr_etp.cu_charge_rt"}))
        plan.notes.append("두 종목 편입 ETF의 교집합에서 총보수 값 보유 상품만 오름차순 정렬")
        plan.notes.append("총보수 0 표기 상품은 값의 의미가 미확정(미수집 추정 — KODEX 200 도 0 으로 표기)이라 결측과 함께 비교에서 제외")
        plan.hints["skip_generation"] = True
        return done("constituent_intersection_low_fee", "partial")

    # ── 5.9 TDF ETF 존재 + 구성 공시 확인 (H-19) ─────────────────────────
    if has_etf_word and "TDF" in q.upper() and re.search(r"담|구성", q):
        refs, seen = [], set()
        for _name, ref in index.search("TDF", limit=50, kinds=("product_kr_etp",)):
            if ref.key not in seen:
                seen.add(ref.key)
                refs.append(ref)
        plan.calls.append(ChannelCall("keyword", "lookup", {"query": "TDF", "limit": 10}))
        plan.calls.append(ChannelCall("sql", "etp_name_search",
                                      {"pattern_raw": "TDF", "instrument_type": "ETF",
                                       "status": "active", "limit": 30}))
        ace_refs = [r for r in refs if "ACE" in r.display.upper()]
        for ref in ace_refs:
            plan.calls.append(ChannelCall("sql", "constituent_top_weights",
                                          {"etf_id": ref.key, "limit": 10}))
        plan.notes.append("TDF ETF 상품은 확인되지만 ACE TDF 시리즈의 2026-07-10 구성 공시는 빈 값")
        plan.notes.append("상품명에서 자산군을 추정하지 않고, 실제 구성 공시가 있는 범위만 안내")
        plan.hints["skip_generation"] = True
        return done("tdf_products_constituents", "partial")

    # ── 5.95 테마 ETF × 코스닥 비중 (H-22) — 구성종목의 시장 구분(MKT_ID=KSQ) 합계.
    #        상품명 조각 규칙(6.1)보다 앞에 둔다 — '바이오 ∩ 코스닥' 이 한 상품으로 잡히면 안 된다.
    if has_etf_word and "코스닥" in q and re.search(r"비중|비율", q) and non_region_themes:
        theme = non_region_themes[0]
        plan.calls.append(ChannelCall("sql", "constituent_ksq_share",
                                      {"pattern_raw": theme, "limit": max(limit, 10)}))
        plan.hints["skip_generation"] = True
        plan.notes.append(f"'{theme}' 표기 상품(상품명 기준)의 구성종목 중 코스닥(KSQ) 종목 비중 합계·종목 수 — "
                          "KRX 공시 비중이 '-'인 종목은 합계에서 빠짐")
        plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일)")
        return done("theme_ksq_share")

    # ── 5.96 리츠 — ETF 와 개별 상장 리츠(구성종목 RT) 를 나눠 답한다 (H-07) ─────────
    #        'SK 계열사(…리츠 등)' 처럼 그룹 질의 안의 '리츠'는 해당 없음(6.0 소관)
    group_m = re.search(r"([가-힣A-Za-z]{2,10}?)\s*(그룹주|그룹|계열사|계열)", q)
    if re.search(r"리츠(에|를|로|\s*투자|\s*ETF)", q) and re.search(r"개별|나눠|나누어|구분|정리", q) \
            and not group_m:
        plan.calls.append(ChannelCall("sql", "etp_name_search",
                                      {"pattern_raw": "리츠", "instrument_type": "ETF",
                                       "status": "active", "limit": 20}))
        plan.calls.append(ChannelCall("sql", "reit_constituents", {"limit": max(limit, 12)}))
        plan.hints["display_rows"] = 12
        plan.hints["skip_generation"] = True
        plan.notes.append("① 리츠 ETF: 상품명에 '리츠'가 있는 상장중 ETF ② 개별 상장 리츠: ETF 구성종목 공시에 등장하는 "
                          "리츠 종목(SECUGRP_ID=RT — 편입 ETF 수 많은 순). 개별 리츠 자체는 제공 마스터에 없어 "
                          "구성종목 수집분에서 확인된 범위만 제시")
        plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일)")
        return done("reit_breakdown", "partial")

    # ── 6.0 그룹·계열사 질의 (M-14/H-10/H-23) — 'X그룹주' 상품 우선 + 회사명 접두 후보 집계 ──
    if group_m and (has_etf_word or re.search(r"담|편입|투자", q)):
        g = _strip_particle(group_m.group(1))
        plan.calls.append(ChannelCall("sql", "etp_name_search",
                                      {"pattern_raw": g + "그룹", "limit": 10}))
        plan.calls.append(ChannelCall("sql", "etp_pattern_top_constituents",
                                      {"pattern_raw": g + "그룹", "top_etfs": 2,
                                       "per_etf": top_n or 12}))
        plan.calls.append(ChannelCall("sql", "constituent_group_holders",
                                      {"prefix_raw": g, "limit": 25}))
        plan.hints["group_prefix"] = g
        plan.hints["display_rows"] = 25
        plan.hints["skip_generation"] = True
        plan.notes.append(f"'{g} 계열사'는 법적 계열 관계 데이터가 없어 회사명이 '{g}'(으)로 시작하는 "
                          f"국내 상장 종목을 후보로 집계(회사명 접두 기준 — 실제 계열 여부와 다를 수 있음)")
        plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일) · 수집분 ETF 기준 · 비중은 각 ETF 안의 편입 비중(%)")
        return done("group_holdings", "partial")

    # ── 6.1 상품명 우선 grounding — 설명형·부분 상품명 + 구성·보유 질의 (M-19/H-20) ─────
    #        '애플 밸류체인 ETF 뭘 담고 있어'는 애플(종목) 역질의가 아니라 그 상품의 구성 질의다.
    if not product_ref and (has_etf_word or re.search(r"커버드콜|그룹주|액티브", q)) \
            and re.search(r"담|들고|구성|비중|편입|보유|퍼센트|%", q):
        cand = resolve_product_candidates(index, q)
        if cand:
            frag, refs = cand
            if len(refs) <= 3 or "+" in frag:
                # 후보가 소수(또는 조각 교집합)면 상품별로 상세(위험등급 등) + 구성 상위를 직접 조회한다
                for ref in refs[:3]:
                    plan.calls.append(ChannelCall("sql", "etp_detail", {"pd_itm_no": ref.key}))
                    plan.calls.append(ChannelCall("sql", "constituent_top_weights",
                                                  {"etf_id": ref.key, "limit": top_n or 10}))
                plan.notes.append(f"상품명 조각 '{frag}'(띄어쓰기 무시)로 식별한 상품 {min(len(refs), 3)}종의 "
                                  "상세(위험등급 포함)와 구성 상위 종목")
            else:
                params = {"pattern_raw": frag, "top_etfs": 3, "per_etf": top_n or 10}
                if comp_ref:
                    params["mgmt"] = comp_ref.key
                plan.calls.append(ChannelCall("sql", "etp_name_search", {"pattern_raw": frag, "limit": 20}))
                plan.calls.append(ChannelCall("sql", "etp_pattern_top_constituents", params))
                plan.notes.append(f"상품명 조각 '{frag}'(띄어쓰기 무시)로 상품을 식별 — 후보 {len(refs)}종 중 "
                                  "순자산 상위 3종의 구성종목")
            plan.hints["product_fragment"] = frag
            plan.hints["display_rows"] = 12
            plan.hints["skip_generation"] = True
            plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일) · 구성 공시가 빈 상품은 '구성 공시 없음'으로 표시 · KRX 공시가 수량만 있고 비중이 '-'인 종목은 비중 없이 표시")
            return done("product_constituents_by_name", "partial" if len(refs) > 1 else "answer")

    # ── 6. 구성종목 역질의 (M-01~07/16/21/22, H-14, H-27) — 서로 다른 종목 2개(교집합)는 5.8/Stage B ──
    if const_ref and n_const_groups == 1 and (
            any(v in q for v in HOLDING_VERBS) or ("구성" not in q and "비중" in q)):
        keys = [r.key for r in const_groups[0][1]][:3]      # 별칭의 복수 상장(A/C 종류주·ADR)은 합쳐 조회
        weight_th = next((v for v, k, d in percents
                          if k in ("weight", "unknown") and d in ("이상", "초과", "넘")), None)
        by_aum = bool(re.search(r"순자산|규모|AUM", q, re.IGNORECASE))
        for key in keys:
            if weight_th is not None:
                plan.calls.append(ChannelCall("sql", "constituent_weight_above",
                                              {"code": key, "min_weight": weight_th,
                                               "limit": limit}))
            else:
                plan.calls.append(ChannelCall("graph", "holding_etfs",
                                              {"query": key, "limit": limit}))
                holder_params = {"code": key, "limit": max(limit, 30)}
                if by_aum:                                # M-02: 순자산 큰 순은 SQL 이 전체에서 정렬
                    holder_params["order"] = "aum"
                plan.calls.append(ChannelCall("sql", "constituent_holders", holder_params))
        if by_aum:
            plan.hints["order"] = "aum"
        plan.hints["constituent"] = {"name": const_name, "key": const_ref.key, "keys": keys}
        if len(keys) > 1:
            plan.notes.append(f"'{const_name}'은(는) 복수 상장 종목 {len(keys)}건"
                              f"({' / '.join(r.display for r in const_groups[0][1][:3])})을 합쳐 조회")
        plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일) · 수집분 ETF 기준")
        return done("constituent_reverse")

    # ── 7. 상품 1종 상세·구성·페어 비교 (L-09/10/28, M-25, H-30) ─────────────
    if product_ref and product_ref.kind == "product_kr_etp":
        _nn = lambda s: re.sub(r"\s+", "", str(s)).casefold()
        alias_hit = _nn(product_name) not in _nn(product_ref.display)   # 약칭(정식명의 일부)이 아닌 별칭·표기 변형으로 식별(8/22)
        if alias_hit and not plan.hints.get("normalized_product_query"):
            plan.hints["normalized_product_query"] = product_name
        if plan.hints.get("normalized_product_query") or plan.hints.get("fuzzy_product_terms"):
            plan.calls.append(ChannelCall("keyword", "lookup",
                                          {"query": product_ref.display, "limit": 5}))
            if plan.hints.get("normalized_product_query"):
                plan.notes.append(f"상품명 별칭을 정규화해 조회: {normalized_q}")
            else:
                plan.notes.append(f"설명형 상품명을 토큰 교집합으로 식별: {plan.hints['fuzzy_product_terms']}")
        if "구성" in q and not re.search(r"비교|달라|차이", q):
            plan.calls.append(ChannelCall("sql", "constituent_top_weights",
                                          {"etf_id": product_ref.key, "limit": top_n or 10}))
            if plan.hints.get("fuzzy_product_terms"):
                plan.calls.append(ChannelCall("graph", "constituents_of",
                                              {"query": product_ref.display, "limit": top_n or 10}))
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

    # ── 7.5 채권·펀드 1종 속성 (8/22 블라인드 v2 L-06~10: 상품은 잡혔는데 속성 규칙이 없어 폴백) ──
    if product_ref and product_ref.kind == "product_bond" and not re.search(r"비교|vs", q, re.IGNORECASE):
        plan.calls.append(ChannelCall("sql", "bond_detail", {"pd_no": product_ref.key}))
        plan.calls.append(ChannelCall("keyword", "lookup", {"query": product_ref.display, "limit": 3}))
        plan.hints["attribute_focus"] = True
        plan.notes.append("채권 1종 상세(만기일·신용등급·표면금리·발행일·분류)는 원천 PRBD01N001 의 값을 그대로 표기")
        return done("bond_detail")
    if product_ref and product_ref.kind == "product_fund" and not re.search(r"구조|전략|동향", q):
        plan.calls.append(ChannelCall("sql", "fund_detail", {"itm_no": product_ref.key}))
        plan.hints["attribute_focus"] = True
        plan.notes.append("공모펀드 1종 상세(위험등급·순자산·수익률·판매상태·벤치마크)는 마스터(PRFD01N001) 기준")
        return done("fund_detail")

    # ── 7.5 지수 추종 상품 검색 (M-18/23) — 펀드 문맥은 12번 소관 ────────────
    if idx_ref and re.search(r"추종|따라가|연동|지수", q) and not product_ref and not is_fund_domain:
        for pat in _spacing_variants(idx_name)[:3]:
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": pat, "limit": max(limit, 20)}))
        plan.notes.append("지수 명칭 표기 변형(붙임/띄움)을 함께 검색")
        return done("index_products")

    # ── 8. 펀드 비정형(구조·전략 서술) — 미수집 명시 + 마스터 보유 필드는 답한다 (M-10) ──
    if is_fund_domain and re.search(r"구조|전략|동향", q):
        fund_ref = product_ref if (product_ref and product_ref.kind == "product_fund") else None
        n_classes = 1
        if fund_ref is None:                             # '국민성장펀드' 같은 부분 명칭 → 펀드 후보
            cand = resolve_product_candidates(index, q, kinds=("product_fund",), max_products=40)
            if cand:
                frag, refs = cand
                fund_ref, n_classes = refs[0], len(refs)
                plan.entities.append((frag, [fund_ref]))
                plan.hints["product_fragment"] = frag
        lookup_query = fund_ref.display if fund_ref else q
        plan.calls.append(ChannelCall("keyword", "lookup", {"query": lookup_query, "limit": 5}))
        if fund_ref:
            plan.calls.append(ChannelCall("sql", "fund_detail", {"itm_no": fund_ref.key}))
            if n_classes > 1:
                plan.notes.append(f"명칭 일치 상품(판매 클래스 포함) {n_classes}건 중 대표 1건의 마스터 정보를 표시")
        plan.calls.append(ChannelCall("vector", "semantic", {"query": q, "k": 5}))
        plan.notes.append("구조·전략 서술(비정형)은 수집 범위 밖 — 마스터 보유 필드(운용속성·위험등급·수익률·순자산·판매상태·벤치마크)까지만 답변")
        plan.hints["skip_generation"] = True
        return done("unstructured_info", "partial")

    # ── 8.6 운용사 × 테마 × 구성 (H-08: 미래에셋이 운용하는 중국 관련 ETF의 구성 상위) ──
    if comp_ref and theme_hits and re.search(r"구성|담|들고|비중|상위\s*종목", q):
        region = next((t for t in theme_hits if t in REGIONS), None)
        variants = list(REGION_NAME_VARIANTS.get(region, (region,))) if region else []
        variants += [t for t in non_region_themes if t not in variants]
        for pat in variants[:3]:
            plan.calls.append(ChannelCall("sql", "etp_pattern_top_constituents",
                                          {"pattern_raw": pat, "mgmt": comp_ref.key,
                                           "top_etfs": 3, "per_etf": top_n or 5}))
        plan.hints["company"] = comp_ref.key
        plan.hints["display_rows"] = 15
        plan.hints["skip_generation"] = True
        plan.notes.append(f"운용사 '{comp_ref.key}'(오염 정정값 기준) × 상품명에 '{' / '.join(variants[:3])}' 표기가 "
                          "있는 상장중 ETP 중 순자산 상위 3종의 구성 상위 종목")
        plan.notes.append("구성종목 기준일 2026-07-10(직전 거래일) · 비중은 각 ETF 안의 편입 비중(%) — KRX 공시가 수량만 있고 비중이 '-'인 종목은 비중 없이 표시")
        return done("mgmt_theme_constituents")

    # ── 8.5 운용사 역질의 (M-09) ─────────────────────────────────────────────
    if comp_ref and re.search(r"운용|발행", q) and "구성" not in q:
        plan.hints["company"] = comp_ref.key
        itype = ("ETN" if re.search(r"ETN", q) and not re.search(r"ETF", q, re.IGNORECASE)
                 else ("ETF" if has_etf_word else None))
        # 8/22 v2 M-06/07: 건수는 근거 줄을 세지 않고 SQL 이 센다
        if any(w in q for w in COUNT_WORDS):
            plan.calls.append(ChannelCall("sql", "mgmt_product_count", {"mgmt": comp_ref.key}))
            plan.notes.append(f"운용사 '{comp_ref.key}'(명칭은 오염 정정값 mgmt_resolved — 64건 복구 기준)의 "
                              "상품 수를 유형(ETF/ETN)·상장상태별로 집계")
            return done("company_product_count")
        # 8/22 v2 M-08/09·H-05: 순위·테마는 순자산 정렬 목록을 SQL 로(그래프 목록엔 순자산이 없어 AI 가 포기했음)
        theme = next((t for t in (non_region_themes or []) if t in q), None) \
            or next((t for t in (theme_hits or []) if t in q and t not in REGIONS), None)
        if theme or re.search(r"순자산|규모|가장 큰|제일 큰|큰 순|상위|AUM", q, re.IGNORECASE):
            params = {"mgmt": comp_ref.key, "limit": top_n or (10 if theme else 5), "active_only": "Y"}
            if itype:
                params["instrument_type"] = itype
            if theme:
                params["name_pattern_raw"] = theme
                plan.notes.append(f"상품명에 '{theme}' 표기가 있는 상품 기준")
            plan.calls.append(ChannelCall("sql", "etp_by_mgmt", params))
            plan.notes.append("운용사 명칭은 오염 정정값(mgmt_resolved — 64건 복구) 기준 · 순자산총액(pd_net_tamt) 내림차순 · 상장중 기준")
            return done("company_products_ranked")
        plan.calls.append(ChannelCall("graph", "company_products",
                                      {"query": comp_ref.key, "limit": max(limit, 10)}))
        plan.calls.append(ChannelCall("sql", "mgmt_top_share", {"limit": 30}))
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
                                          {"bond_class": bond_class, "as_of_date": today.isoformat(),
                                           "limit": limit}))
            plan.notes.append(f"잔존만기(residual_years·일수)는 요청 시점({today.isoformat()}) 기준으로 계산한 값")
            if bond_class == "국공채" and "국고채" in q:
                plan.notes.append("'국고채'는 제공 대분류상 국공채로 조회")
            return done("bond_ranking")
        residual_within = re.search(r"잔존만기\s*(\d+)\s*년\s*(?:이하|이내)", q)
        if residual_within:                              # H-26: 시간+등급+금리 복합 필터
            years = int(residual_within.group(1))
            try:
                until = today.replace(year=today.year + years)
            except ValueError:                           # 2/29 요청의 비윤년 보정
                until = today.replace(year=today.year + years, day=28)
            params = {"as_of_date": today.isoformat(), "until": until.isoformat(),
                      "currency": currency if not ccy_exclude else None,
                      "bond_class": bond_class,
                      "min_coupon": coupon_min if coupon_band is None else coupon_band,
                      "max_coupon": coupon_band + 1 if coupon_band is not None else None,
                      "limit": max(limit, 20)}
            params.update(cond)
            plan.notes.extend(notes)
            plan.notes.append(f"잔존만기 {years}년 이하={today.isoformat()}~{until.isoformat()} 만기일로 재계산")
            plan.calls.append(ChannelCall("sql", "bond_maturing_within",
                                          {k: v for k, v in params.items() if v is not None}))
            return done("bond_maturing_filter")
        if "잔존만기" in q:                              # 그 밖의 복합 표현은 Stage B 소관
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
        # 8/22 v2 L-14: 만기가 이미 지난 채권까지 섞여 나오던 것 — 기본은 '만기 미경과'로 두고 명시한다
        if not wants_active and not any(w in q for w in COUNT_WORDS) \
                and not re.search(r"만기\s*(가\s*)?(지난|된|도래한|경과)|과거|전체|모든|이미|포함", q):
            wants_active = True                      # 목록 질문만 — 건수 질문(L-05 등)은 전체 통계 그대로
            plan.notes.append("만기가 지나지 않은(기준일 현재 유효한) 채권 기준 — 만기 경과분까지 보려면 '만기 지난 채권 포함'으로 질문")
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
        if "운용사" in q and re.search(r"상위\s*\d+\s*개?\s*(국내\s*)?(ETF|ETN|상품)", q, re.IGNORECASE):   # v2 H-09
            plan.calls.append(ChannelCall("sql", "etp_top_aum",
                                          {"instrument_type": itype, "limit": top_n or 3}))
            plan.notes.append("상품 순위(순자산총액 내림차순·상장중)와 각 상품의 운용사(cu_fund_mgmt_co 원시 표기)를 함께 표시")
            return done("etp_ranking")
        if "운용사" in q and any(w in q for w in TOP_WORDS):        # H-29
            plan.calls.append(ChannelCall("sql", "mgmt_top_share", {"limit": top_n or 10}))
            plan.notes.append("운용사 명칭은 오염 정정값(mgmt_resolved — 64건 복구) 기준 집계")
            return done("mgmt_ranking", "partial")
        if re.search(r"ETF.{0,8}ETN|ETN.{0,8}ETF", q, re.IGNORECASE) and \
                re.search(r"더 많|많아|많은|어느\s*쪽|어느쪽|비교", q):               # v2 H-10: 유형별 건수 비교
            plan.calls.append(ChannelCall("sql", "etp_count", {}))
            plan.notes.append("ETF 와 ETN 의 상품 수를 유형·상장상태별로 비교(전체/상장중 구분)")
            return done("etp_count")
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
            if "공통" in q and re.search(r"담|종목|구성", q):        # H-09: 상위 N 의 공통 구성종목
                plan.calls.append(ChannelCall("sql", "etp_top_return_common_holdings",
                                              {"metric": metric, "top_n": top_n or 10, "limit": 15}))
                plan.hints["display_rows"] = 10
                plan.hints["skip_generation"] = True
                plan.notes.append("공통 종목 = 수익률 상위 ETF 중 2개 이상이 구성종목 공시에 담은 종목(보유 ETF 수 많은 순) · "
                                  "구성종목 기준일 2026-07-10 · 구성 공시가 빈 ETF 는 집계에서 빠짐")
                return done("etp_ranking_common_holdings")
            return done("etp_ranking")
        if "보수" in q and re.search(r"이하|미만|낮|싼|저렴", q):    # L-26
            fee_th = next((v for v, k, _d in percents if k == "fee"), None)
            plan.calls.append(ChannelCall("sql", "etp_low_fee",
                                          {"max_fee": fee_th if fee_th is not None else 100.0,
                                           "limit": max(limit, 20)}))
            plan.calls.append(ChannelCall("sql", "coverage_check", {"field": "kr_etp.cu_charge_rt"}))
            plan.notes.append("총보수는 값 보유 상품 기준(실질결측 87.5%) · 0 표기는 의미 미확정(미수집 추정)이라 순위에서 제외 — 커버리지 명시 필수")
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
        m_within_etf = re.search(r"(\d)\s*년\s*(?:안에|이내)", q)
        if "만기" in q and (m_within_etf or "도래" in q or "만기형" in q):   # H-12: 만기형 채권 ETF 만기 창
            years = int(m_within_etf.group(1)) if m_within_etf else 1
            date_from = today.replace(day=1)
            try:
                date_to = today.replace(year=today.year + years)
            except ValueError:
                date_to = today.replace(year=today.year + years, day=28)
            plan.calls.append(ChannelCall("sql", "etp_target_maturity_within",
                                          {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
                                           "limit": max(limit, 20)}))
            plan.notes.append(f"만기형 채권 ETF 의 만기는 상품명의 존속기한 표기('25-11' = 2025년 11월)로 읽음 — "
                              f"상품명 규칙에 기댄 판독이며 정확한 만기일은 원천에 없음 · 창: {date_from.isoformat()}~{date_to.isoformat()}")
            return done("etp_target_maturity")
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
        asks_our_sale = bool(re.search(
            r"(?:당사|미래에셋\s*증권).{0,12}판매|판매.{0,12}(?:당사|미래에셋\s*증권)", q))
        if any(w in q for w in COUNT_WORDS) and "클래스" not in q:   # L-21
            plan.calls.append(ChannelCall("sql", "fund_counts", {}))
            plan.notes.append("상품(마스터) 수와 판매 클래스 수는 다름 — 구분해 답변")
            return done("fund_count")
        attr = next((w for w in ("주식형", "채권형", "혼합형", "재간접", "MMF") if w in q), None)
        if "수익률" in q and any(w in q for w in TOP_WORDS):         # L-24
            sale_params = {}
            if "판매" in q:
                sale_params["on_sale_only"] = "Y"
            if asks_our_sale:
                sale_params["thco_sale_only"] = "Y"
            plan.calls.append(ChannelCall("sql", "fund_top_return_1y",
                                          {**sale_params, "limit": top_n or 5}))
            plan.calls.append(ChannelCall("sql", "coverage_check",
                                          {"field": "fund_master.fd_yr1_ern_r"}))
            plan.notes.append("1년 수익률 값 보유 상품 기준 — 커버리지 명시")
            plan.hints["coverage_is_caveat_only"] = True
            return done("fund_ranking")
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
            params = {"min_risk": risk[0], "max_risk": risk[1],
                      "limit": max(limit, 20)}
            if "판매" in q:
                params["on_sale_only"] = "Y"
                plan.notes.append("현재 판매상태는 sale_yn='판매중' 기준")
            if asks_our_sale:
                params["thco_sale_only"] = "Y"
                plan.notes.append("당사 판매는 thco_sale_yn='Y'를 추가로 동시 충족하는 상품 기준")
            plan.calls.append(ChannelCall("sql", "fund_filter",
                                          params))
            plan.notes.extend(risk[2])
            return done("fund_filter")
        if attr or "판매" in q or re.search(r"해외|국내", q):   # L-22 · v2 M-13(해외 주식형·순자산 순)
            params = {"limit": max(limit, 20)}
            if attr:
                params["attr_pattern_raw"] = attr
            region = "해외" if "해외" in q else ("국내" if "국내" in q else None)
            if region:
                params["region"] = region
                plan.notes.append(f"투자지역은 원천 ovrs_fd_desc='{region}' 기준")
            if re.search(r"순자산|규모", q) and (any(w in q for w in TOP_WORDS)
                                                or re.search(r"큰\s*순|순으로|내림차순|큰 것|큰 상품", q)):
                params["order"] = "aum"
                params["limit"] = top_n or 5
                plan.notes.append("순자산(fd_nast_suma) 내림차순")
            if "판매" in q:
                params["on_sale_only"] = "Y"
                plan.notes.append("현재 판매상태는 sale_yn='판매중' 기준")
            if asks_our_sale:
                params["thco_sale_only"] = "Y"
                plan.notes.append("당사 판매는 thco_sale_yn='Y'를 추가로 동시 충족하는 상품 기준")
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
    if theme_hits and "최근" in q and "개월" in q and "이력" in q:
        months_match = re.search(r"최근\s*(\d+)\s*개월", q)
        months = int(months_match.group(1)) if months_match else policy["recent_window_months"]
        anchor = datetime.date.fromisoformat(AS_OF_MASTER)
        month_index = anchor.year * 12 + anchor.month - 1 - months
        start_year, start_month0 = divmod(month_index, 12)
        start = datetime.date(start_year, start_month0 + 1,
                              min(anchor.day, calendar.monthrange(start_year, start_month0 + 1)[1]))
        theme_query = " ".join(non_region_themes or theme_hits)
        hist_params = {"query": theme_query, "k": 8}
        if ("해외" in q or is_global) and "국내" not in q:
            hist_params["market"] = "해외상장"       # 시장 명시 질문 — 후보를 그 시장으로 제한(8/22)
        elif "국내" in q and "해외" not in q and not is_global:
            hist_params["market"] = "국내상장"
        plan.calls.append(ChannelCall("vector", "semantic", hist_params))
        for term in non_region_themes[:2]:
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": term, "limit": 10}))
            plan.calls.append(ChannelCall("keyword", "lookup", {"query": term, "limit": 5}))
        plan.notes.append(f"'최근 {months}개월'={start.isoformat()}~{AS_OF_MASTER}로 해석")
        plan.notes.append("테마 연결 이력 데이터는 미수집 — 기준일 상품명·전략 서술 매칭 후보만 제시")
        plan.hints["skip_generation"] = True
        return done("theme_history", "partial")

    if (non_region_themes or "테마" in q or (is_global and theme_hits)) \
            and re.search(r"투자하는|투자하|전략|중심|집중|테마|찾아|알려|골라|있어", q) \
            and not product_ref and (not is_bond_domain or has_etf_word):
        excluded_region = detect_region_exclusion(q)
        if is_global and excluded_region and non_region_themes:      # H-18: '미국 말고' 배당형 해외 ETF
            anchors = [t for t in themes.get(non_region_themes[0], []) if t][:1] or [non_region_themes[0]]
            plan.calls.append(ChannelCall("sql", "global_etf_filter",
                                          {"exclude_region_pattern_raw": REGION_INV_RGN_EN[excluded_region],
                                           "name_pattern_raw": anchors[0], "limit": max(limit, 15)}))
            plan.notes.append(f"'{excluded_region} 말고' = 투자지역(wu_inv_rgn)에 '{REGION_INV_RGN_EN[excluded_region]}'가 "
                              f"없는 해외 ETF 중 상품명·전략 서술에 '{anchors[0]}'가 있는 것(순자산 큰 순) — "
                              "Global(전세계) 표기 상품은 미국을 일부 포함할 수 있음")
        # 시장 명시(해외만/국내만) 질문은 벡터·anchor 후보를 그 시장으로 제한한다(8/22 —
        # 국내 이름 문장이 코퍼스에 들어온 뒤 '배당 해외 ETF'(M-12)에 국내 근거가 섞인 실측).
        vec_market = None
        if ("해외" in q or is_global) and "국내" not in q:
            vec_market = "해외상장"
        elif "국내" in q and "해외" not in q and not is_global:
            vec_market = "국내상장"
        vec_params = {"query": q, "k": 8}
        if vec_market:
            vec_params["market"] = vec_market
        plan.calls.append(ChannelCall("vector", "semantic", dict(vec_params)))
        for t in (non_region_themes or theme_hits)[:2]:
            plan.calls.append(ChannelCall("sql", "etp_name_search",
                                          {"pattern_raw": t, "limit": 10}))
            plan.calls.append(ChannelCall("keyword", "lookup", {"query": t, "limit": 5}))
        plan.notes.append("테마 판정 기준: 상품명(국내)·전략 서술(해외) 매칭 — 의미 검색은 키워드 근거와 결합(RRF)")
        if "국내" in q and "해외" in q:                    # H-04: 국내/해외 비교는 위험등급 비대칭을 밝힌다
            plan.notes.append("국내 상장 ETF 는 위험등급(1=매우 높음~6=매우 낮음)이 있지만 해외 상장 ETF 원천에는 "
                              "위험등급 필드가 없음 — 위험 수준을 같은 잣대로 비교할 수 없어 국내 상품만 위험등급 확인 가능")
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
