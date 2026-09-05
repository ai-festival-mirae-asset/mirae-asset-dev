# -*- coding: utf-8 -*-
"""
테마 한→영 사전 로더 — Router(테마 감지)와 벡터 채널(lexical anchor)이 공유 (S2 순서 ③).

무엇: external_data/dictionaries/theme_ko_en.csv → {한글 테마: [영문 anchor 들]}.
왜  : 벡터 단독 실측 한계("반도체 집중"→로보틱스 1위) 보완 — 테마 명사를 영문으로
      확장한 lexical anchor 를 해외ETF 전략 서술에 요구하고 RRF 로 결합한다(S2_PLAN §2).
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import csv
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))            # pipeline/
ROOT = os.path.dirname(HERE)
THEMES_PATH_DEFAULT = os.path.join(ROOT, "external_data", "dictionaries", "theme_ko_en.csv")

# 지역 성격 테마 — "미국 소형주"처럼 지역+주제가 함께 오면 주제가 라우팅을 주도한다
REGIONS = frozenset({"미국", "중국", "일본", "인도", "유럽", "신흥국", "베트남", "브라질"})

# 어순·표기 변형 → 사전의 대표 표기 (9/6 표현 변형 점검: '항공우주 테마 ETF'가 상품명 검색 0건 — 사전 키는 '우주항공')
THEME_ALIASES = {"항공우주": "우주항공", "이차전지": "2차전지", "이차 전지": "2차전지", "2차 전지": "2차전지",
                 "바이오테크": "바이오"}


def load_themes(path=THEMES_PATH_DEFAULT):
    """{한글 테마: [영문 anchor, ...]} — 파일 없으면 빈 dict(기능 자동 비활성)."""
    if not os.path.exists(path):
        return {}
    out = {}
    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            ko = (row.get("ko") or "").strip()
            terms = [t.strip() for t in (row.get("en_terms") or "").split(";") if t.strip()]
            if ko:
                out[ko] = terms
    return out


def detect_theme_terms(question, themes=None):
    """질문에 등장하는 테마 키 목록(등장 순). 1글자 키('금')는 조사·투자 문맥일 때만."""
    themes = themes if themes is not None else load_themes()
    hits = []
    for ko in themes:
        if len(ko) == 1:
            if not re.search(re.escape(ko) + r"(?:에|을|이나|으로|\s*투자|\s*관련)", question):
                continue
        elif ko not in question:
            continue
        hits.append((question.find(ko), ko))
    for alias, canon in THEME_ALIASES.items():           # 9/6: 어순·표기 변형은 대표 표기로(사전에 있는 것만)
        if alias in question and canon in themes and all(k != canon for _p, k in hits):
            hits.append((question.find(alias), canon))
    hits.sort()
    return [ko for _pos, ko in hits]


def expand_anchors(terms, themes=None):
    """테마 키들 → [한글 원어 + 영문 anchor] 중복 제거 목록 (lexical anchor 용)."""
    themes = themes if themes is not None else load_themes()
    out = []
    for ko in terms:
        for term in [ko] + themes.get(ko, []):
            if term not in out:
                out.append(term)
    return out
