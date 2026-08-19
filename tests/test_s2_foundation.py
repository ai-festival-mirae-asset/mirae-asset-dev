# -*- coding: utf-8 -*-
"""S2 순서 ① 기반 모듈 테스트 — 운용사 오염 복구·구성종목 별칭·Evidence 계약.

왜: 존재 검증·엔티티 인덱스(순서 ②)가 이 세 모듈 위에 서므로 먼저 고정한다.
"""
import pytest

from pipeline.constituent_aliases import load_aliases, norm_alias, parse_alias_rows
from pipeline.evidence import Evidence, to_context_string
from pipeline.mgmt_resolution import build_correction_report, resolve_mgmt_co


# ---------------------------------------------------------------------------
# 1. 운용사 오염 복구 (실측 54+10행 유형)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected, method", [
    # ① 상품명 전체 오염(공백 없음) — 접두로 복구
    ("미래에셋TIGER200IT증권상장지수투자신탁(주식)", "미래에셋", "recovered"),
    ("삼성KODEX레버리지증권상장지수투자신탁[주식-파생형]", "삼성", "recovered"),
    ("신한SOL유럽탄소배출권선물인버스ICE특별자산상장지수투자신탁[탄소배출권-파생형](H)", "신한", "recovered"),
    ("DB마이티K100증권상장지수투자신탁(주식)", "DB", "recovered"),
    # ② 브랜드 결합형
    ("한화PLUS", "한화", "brand_split"),
    ("미래에셋TIGER", "미래에셋", "brand_split"),
    ("삼성KODEX", "삼성", "brand_split"),
    # ③ 정상값은 그대로 (자산운용 축약·ETN 발행 증권사 법인명 — 서로 다른 법인 병합 금지)
    ("미래에셋", "미래에셋", "as_is"),
    ("삼성액티브", "삼성액티브", "as_is"),
    ("미래에셋증권 주식회사", "미래에셋증권 주식회사", "as_is"),
    ("KB증권(주)", "KB증권(주)", "as_is"),
    (None, None, "as_is"),
])
def test_resolve_mgmt_co(raw, expected, method):
    assert resolve_mgmt_co(raw) == (expected, method)


def test_resolve_unknown_contamination_flagged():
    """복구 규칙 밖 오염은 원시값 유지 + unresolved 플래그(조용한 오귀속 금지)."""
    v = "듣도못한운용사XYZ증권상장지수투자신탁(주식)"
    resolved, method = resolve_mgmt_co(v)
    assert (resolved, method) == (v, "unresolved")
    counts, unresolved = build_correction_report(["미래에셋", v])
    assert counts["as_is"] == 1 and counts["unresolved"] == 1 and unresolved == [v]


# ---------------------------------------------------------------------------
# 2. 구성종목 한글 별칭
# ---------------------------------------------------------------------------

def test_parse_alias_rows_multi_listing_and_dedupe():
    rows = [{"alias": "알리바바", "canonical_en": "ALIBABA-HK", "isin": "KYG017191142"},
            {"alias": "알리바바", "canonical_en": "ALIBABA-ADR", "isin": "US01609W1027"},
            {"alias": "알리바바", "canonical_en": "ALIBABA-HK", "isin": "KYG017191142"},  # 중복
            {"alias": "", "canonical_en": "x", "isin": "y"}]                              # 무시
    m = parse_alias_rows(rows)
    assert list(m) == [norm_alias("알리바바")]
    assert m[norm_alias("알리바바")] == [("KYG017191142", "ALIBABA-HK"),
                                       ("US01609W1027", "ALIBABA-ADR")]


def test_load_aliases_real_file_has_cambricon():
    """실제 사전 파일 — 캠브리콘(중-2 유형 공식 예시 종목)이 반드시 있어야 한다."""
    m = load_aliases()
    assert norm_alias("캠브리콘") in m
    assert ("CNE1000041R8", "CAMBRICON TECHNOLOGIES") in m[norm_alias("캠브리콘")]
    assert norm_alias("엔비디아") in m


def test_load_aliases_missing_file_is_empty():
    assert load_aliases("없는/경로.csv") == {}


# ---------------------------------------------------------------------------
# 3. Evidence 계약
# ---------------------------------------------------------------------------

def test_evidence_serialization_includes_source_and_asof():
    evs = [Evidence(source="PREF01N001", source_id="KR7102110004", channel="graph",
                    as_of="2026-07-11", fields={"운용사": "미래에셋"}),
           Evidence(source="KRX-PDF", source_id="KR7102110004", channel="sql",
                    as_of="2026-07-10", fields={"삼성전자 비중(%)": "33.03"},
                    note="구성종목은 직전 거래일 기준")]
    text = to_context_string(evs)
    assert "PREF01N001" in text and "2026-07-11" in text
    assert "KRX-PDF" in text and "2026-07-10" in text        # 소스별 기준일 구분 보존
    assert "삼성전자 비중(%)=33.03" in text
    assert text.count("[근거") == 2


def test_evidence_rejects_unknown_channel():
    with pytest.raises(ValueError):
        Evidence(source="s", source_id="i", channel="webscrape", as_of="2026-07-11")
