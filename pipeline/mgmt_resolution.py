# -*- coding: utf-8 -*-
"""
국내 ETP 운용사 컬럼(cu_fund_mgmt_co) 오염 복구 — S2 순서 ① (8/13).

무엇: 원천 오염 두 종류를 결정적 규칙으로 복구한다.
  ① 상품명 전체 오염(54행 실측): "미래에셋TIGER200IT증권상장지수투자신탁(주식)" 처럼
     운용사 자리에 공백 없는 상품명 전체가 들어간 행 → 운용사+브랜드 접두로 복구.
  ② 브랜드 결합형(10행 실측): "한화PLUS"·"미래에셋TIGER"·"삼성KODEX" 처럼
     운용사와 브랜드가 붙은 값 → 접두 운용사만 남긴다.
왜  : 이대로면 "운용사별 상품"(CQ2)·운용사 집계가 오염된다(KG_METHOD 6장,
     평가셋 M-09·H-29가 이 함정을 직접 문항화). 원천 CSV·KG 원시값은 보존하고
     (원시 보존 원칙), 검색·집계 계층이 이 모듈의 복구값을 쓴다.

주의: "삼성"(자산운용)·"삼성액티브"·"삼성증권(주)"는 서로 다른 법인이다 —
     이 모듈은 오염 복구만 하고, 법인 통합(entity resolution)은 하지 않는다.

사용: resolve_mgmt_co(raw) -> (복구값, method)
     method: 'as_is'(정상) | 'brand_split' | 'recovered'(상품명 오염 복구) | 'unresolved'
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""

# (운용사+브랜드 접두) → 운용사. 8/13 오염 64행 실측에서 관찰된 조합 + 동일 명명
# 규칙의 안전한 확장(관찰 안 된 조합도 상품명 접두 규칙이 동일해 오탐 위험 낮음).
BRAND_PREFIX_TO_COMPANY = {
    "미래에셋TIGER": "미래에셋",
    "삼성KODEX": "삼성",
    "삼성액티브KoAct": "삼성액티브",
    "신한SOL": "신한",
    "한화PLUS": "한화",
    "한화ARIRANG": "한화",       # PLUS 리브랜딩 전 구명
    "KBRISE": "KB",
    "KBSTAR": "KB",              # RISE 리브랜딩 전 구명
    "한국투자ACE": "한국투자",
    "키움KOSEF": "키움",
    "키움히어로즈": "키움",
    "NH-AmundiHANARO": "NH-Amundi",
    "하나1Q": "하나",
    "DB마이티": "DB",
    "우리WON": "우리",
}

# 오염 판정 마커 — 상품명 전체가 들어온 행에만 나타난다(정상 운용사·증권사명에는 없음)
CONTAMINATION_MARKERS = ("투자신탁", "상장지수")

# 접두 매칭은 긴 것부터 (예: "삼성액티브KoAct" 가 "삼성KODEX" 보다 먼저 검사될 필요는
# 없지만, "삼성액티브" vs "삼성" 같은 포함 관계를 안전하게 처리하기 위한 일반 규칙)
_PREFIXES_BY_LENGTH = sorted(BRAND_PREFIX_TO_COMPANY, key=len, reverse=True)


def resolve_mgmt_co(raw):
    """오염된 운용사 값 1개 → (복구값, method). None/공백은 (None, 'as_is')."""
    if raw is None:
        return None, "as_is"
    v = str(raw).strip()
    if not v:
        return None, "as_is"
    if v in BRAND_PREFIX_TO_COMPANY:                       # ② 브랜드 결합형
        return BRAND_PREFIX_TO_COMPANY[v], "brand_split"
    if any(m in v for m in CONTAMINATION_MARKERS):         # ① 상품명 전체 오염
        for p in _PREFIXES_BY_LENGTH:
            if v.startswith(p):
                return BRAND_PREFIX_TO_COMPANY[p], "recovered"
        return v, "unresolved"                             # 복구 실패 — 원시값 유지 + 플래그
    return v, "as_is"                                      # 정상(운용사 축약·증권사 법인명)


def build_correction_report(values):
    """값 목록 → method 별 집계 + unresolved 목록 (검증·리포트용)."""
    counts = {"as_is": 0, "brand_split": 0, "recovered": 0, "unresolved": 0}
    unresolved = []
    for v in values:
        resolved, method = resolve_mgmt_co(v)
        counts[method] += 1
        if method == "unresolved":
            unresolved.append(v)
    return counts, unresolved
