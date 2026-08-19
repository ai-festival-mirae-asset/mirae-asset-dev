# -*- coding: utf-8 -*-
"""
구성종목 한글 별칭 로더 — "캠브리콘" → CNE1000041R8 (S2 순서 ①, 8/13).

무엇: external_data/dictionaries/constituent_aliases.csv 를 읽어
      정규화 별칭 → [(ISIN, 영문 정식명)] 매핑을 만든다.
왜  : KRX 수집분의 해외 주식 ~34,900행은 영문명·ISIN 키인데 평가 질의는
      한글로 온다("캠브리콘 편입 ETF") — 별칭 없이는 그라운딩이 실패하고,
      더 나쁘게는 존재 검증(직접 매칭만)이 정답 가능 질의를 refuse 한다.
      한 별칭이 복수 상장(A/H주·ADR)에 걸릴 수 있어 값은 목록이다.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import csv
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))            # pipeline/
ROOT = os.path.dirname(HERE)
ALIASES_CSV = os.path.join(ROOT, "external_data", "dictionaries",
                           "constituent_aliases.csv")


def norm_alias(text):
    """별칭 정규화 — 공백 제거 + casefold ("티에스엠씨" == "티에스엠씨 ")."""
    return "".join(str(text).split()).casefold()


def parse_alias_rows(rows):
    """[{alias, canonical_en, isin, ...}] → {정규화 별칭: [(isin, canonical_en), ...]}.

    같은 (별칭, ISIN) 중복은 1회만 유지한다(결정적 — 입력 순서 보존).
    """
    out = {}
    for row in rows:
        alias, isin = row.get("alias"), row.get("isin")
        if not alias or not isin:
            continue
        key = norm_alias(alias)
        pair = (isin.strip(), (row.get("canonical_en") or "").strip())
        bucket = out.setdefault(key, [])
        if pair not in bucket:
            bucket.append(pair)
    return out


def load_aliases(path=ALIASES_CSV):
    """CSV → 별칭 매핑. 파일이 없으면 빈 dict(선택적 자산 — 없어도 동작)."""
    if not os.path.exists(path):
        return {}
    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return parse_alias_rows(list(csv.DictReader(fh)))
