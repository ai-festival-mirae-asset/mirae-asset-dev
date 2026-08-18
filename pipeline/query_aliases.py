# -*- coding: utf-8 -*-
"""검증된 국내 ETF 브랜드 별칭을 상품 검색 표기로 확장한다.

별칭 사전에는 브랜드뿐 아니라 투자 은어·테마 연상어도 들어 있다. 상품 존재
grounding 에 의미 추론형 별칭을 섞으면 없는 상품을 있다고 판단할 수 있으므로,
여기서는 ``별칭사전|국내ETF브랜드`` 행의 검증된 표기만 사용한다.
"""
import csv
import io
import os
import re
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ALIASES_PATH_DEFAULT = os.path.join(
    ROOT, "external_data", "dictionaries", "alias_dictionary.csv")

# 상품명 숫자 표기의 빈번한 한글 변형. 검색 표기만 바꾸며 실제 상품명은
# 데이터에 저장된 정식 명칭을 그대로 출력한다.
_TYPOGRAPHIC_ALIASES = {
    "톱텐": "TOP10",
    "탑텐": "TOP10",
    "TOP 10": "TOP10",
}


def _compact_alias(term):
    """상품명 안에서 안전하게 치환할 수 있는 짧은 브랜드 표기만 허용한다."""
    term = str(term or "").strip()
    if len(re.sub(r"\s+", "", term)) < 2:
        return None
    # '삼성 ETF'·'KB자산운용 ETF'처럼 일반 설명에 가까운 문구는 제외한다.
    if re.search(r"\s", term):
        return None
    return term


@lru_cache(maxsize=4)
def load_product_query_aliases(path=ALIASES_PATH_DEFAULT):
    """{사용자 표기: 현재 데이터의 브랜드 키}.

    사전은 현재 브랜드 행을 구 브랜드 행보다 먼저 둔다. ``setdefault``를 써서
    RISE 행의 ``KBSTAR→RISE`` 같은 리브랜딩 매핑이 뒤의 KBSTAR 행에 의해
    되돌아가지 않게 한다.
    """
    aliases = {}
    if os.path.exists(path):
        with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("분류") != "별칭사전|국내ETF브랜드":
                    continue
                if row.get("검증상태") not in ("검증통과", "정정반영"):
                    continue
                canonical = str(row.get("키") or "").strip()
                if not canonical:
                    continue
                terms = [canonical] + str(row.get("동의어") or "").split(";")
                for raw in terms:
                    alias = _compact_alias(raw)
                    if alias:
                        aliases.setdefault(alias, canonical)
    aliases.update(_TYPOGRAPHIC_ALIASES)
    return aliases


def normalize_product_query(text, path=ALIASES_PATH_DEFAULT):
    """질의의 검증된 브랜드·숫자 표기만 정식 상품명 표기로 바꾼다."""
    out = str(text)
    aliases = load_product_query_aliases(path)
    for alias in sorted(aliases, key=lambda value: (-len(value), value.casefold())):
        canonical = aliases[alias]
        if alias.casefold() == canonical.casefold():
            continue
        # 앞 단어의 일부인 우연 일치는 막되 '타이거차이나'처럼 붙여 쓴 상품명은 허용한다.
        pattern = rf"(?<![가-힣A-Za-z0-9]){re.escape(alias)}"
        out = re.sub(pattern, canonical, out, flags=re.IGNORECASE)
    return out


def product_alias_variants(name, path=ALIASES_PATH_DEFAULT, limit=128):
    """정식 국내 ETF 상품명에 대응하는 한글·영문 검색 색인 표기 집합."""
    aliases = load_product_query_aliases(path)
    by_canonical = {}
    for alias, canonical in aliases.items():
        if alias.casefold() != canonical.casefold():
            by_canonical.setdefault(canonical, []).append(alias)

    variants = {str(name)}
    for canonical, forms in sorted(by_canonical.items(), key=lambda item: -len(item[0])):
        for value in list(variants):
            if not re.search(re.escape(canonical), value, flags=re.IGNORECASE):
                continue
            for alias in forms:
                variants.add(re.sub(re.escape(canonical), alias, value,
                                    count=1, flags=re.IGNORECASE))
                if len(variants) >= limit:
                    return variants
    return variants
