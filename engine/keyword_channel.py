# -*- coding: utf-8 -*-
"""
키워드 채널 — 명칭·별칭 기반 상품/개체 검색 (S2 순서 ②, 8/13).

무엇: 통합 엔티티 인덱스(pipeline/entity_index)를 그대로 쓰는 얇은 채널.
      exact(직접 매칭 — 존재 검증과 동일 의미론)와 search(부분 일치 — 후보 안내)를
      Evidence 로 감싼다.
왜  : 벡터 단독의 실측 한계("반도체 집중"→로보틱스 1위) 보완의 축 — Router 가
      벡터 결과와 키워드 히트를 결합(RRF)할 때 이 채널의 결과를 쓴다.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.evidence import AS_OF_CONSTITUENTS, AS_OF_MASTER, Evidence  # noqa: E402

# 개체 종류별 기준일 — 구성종목만 7/10(직전 거래일), 나머지는 마스터 7/11
_AS_OF_BY_KIND = {"constituent": AS_OF_CONSTITUENTS}


def _to_evidence(ref, matched_name, exact):
    return Evidence(
        source=ref.source, source_id=ref.key, channel="keyword",
        as_of=_AS_OF_BY_KIND.get(ref.kind, AS_OF_MASTER),
        fields={"매칭": ref.display, "종류": ref.kind},
        note="직접 매칭" if exact else f"부분 일치({matched_name})",
    )


def keyword_lookup(index, query, limit=10, kinds=None):
    """exact 우선, 없으면 search 폴백 — (refs, evidences, exact 여부).

    exact 결과는 존재 검증과 같은 의미론(정규화 완전 일치)이다. 부분 일치 결과는
    "유사 후보 안내"용 — 존재의 근거로 쓰면 안 된다(트랩 방어 정책).
    """
    exact_refs = [r for r in index.exact(query) if kinds is None or r.kind in kinds]
    if exact_refs:
        refs = exact_refs[:limit]
        return refs, [_to_evidence(r, query, True) for r in refs], True
    hits = index.search(query, limit=limit, kinds=kinds)
    refs = [ref for _name, ref in hits]
    return refs, [_to_evidence(ref, name, False) for name, ref in hits], False
