# -*- coding: utf-8 -*-
"""S2 순서 ② 테스트 — 엔티티 인덱스·키워드 채널·SQL 템플릿.

구성: ① 순수 로직(파라미터 검증·이스케이프·인덱스 의미론) ② 실 DB 통합
     (storage/output/products.duckdb 필요 — 없으면 skip: 먼저 python storage/load_duckdb.py).
"""
import os

import pytest

from engine.keyword_channel import keyword_lookup
from engine.sql_templates import TEMPLATES, like_param, run_template
from pipeline.entity_index import (DB_PATH_DEFAULT, EntityIndex, EntityRef,
                                   build_entity_index)

DB_EXISTS = os.path.exists(DB_PATH_DEFAULT)
needs_db = pytest.mark.skipif(not DB_EXISTS, reason="products.duckdb 미생성 — load_duckdb.py 선행")


@pytest.fixture(scope="module")
def con():
    import duckdb
    c = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


# ---------------------------------------------------------------------------
# 1. 순수 로직
# ---------------------------------------------------------------------------

def test_like_param_escapes_wildcards():
    assert like_param("100%_리츠") == "%100\\%\\_리츠%"


def test_entity_index_exact_vs_search_semantics():
    """존재 검증(exact)은 완전 일치만 — 부분 일치는 search 에서만 나온다."""
    idx = EntityIndex()
    ref = EntityRef("product_kr_etp", "KR7102110004", "TIGER 200", "PREF01N001")
    idx.add("TIGER 200", ref)
    assert idx.exact("tiger200") == [ref]          # 정규화(공백·대소문자) 일치
    assert idx.exact("TIGER") == []                # 부분 일치는 존재로 인정 안 함
    assert idx.search("TIGER") == [("tiger200", ref)]


def test_template_param_validation():
    with pytest.raises(KeyError):
        run_template(None, "없는_템플릿")
    with pytest.raises(ValueError, match="필수"):
        run_template(None, "etp_top_aum", {"instrument_type": "ETF"})   # limit 누락
    with pytest.raises(ValueError, match="중 하나"):
        run_template(None, "etp_top_aum", {"instrument_type": "주식", "limit": 5})
    with pytest.raises(ValueError, match="모르는"):
        run_template(None, "etp_top_aum",
                     {"instrument_type": "ETF", "limit": 5, "oops": 1})


def test_all_templates_have_description_and_known_source():
    for t in TEMPLATES.values():
        assert t.description and len(t.description) > 10
        if t.id != "coverage_check":
            assert t.sql and "$" in t.sql or not t.params


# ---------------------------------------------------------------------------
# 2. 실 DB 통합 — 평가셋 gold 스펙 실측
# ---------------------------------------------------------------------------

@needs_db
def test_bond_filter_aa_krw(con):
    """L-01: 원화 + AA 이상(문자 그대로 서열≤3 — 8/14 확정) — 결과 존재 + 등급 도메인 준수."""
    r = run_template(con, "bond_filter",
                     {"currency": "KRW", "max_rating_rank": 3, "limit": 50})
    assert len(r.rows) > 0
    assert all(int(row["drv_crd_grd_rank"]) <= 3 for row in r.rows)
    assert r.evidences and r.evidences[0].source == "PRBD01N001"


@needs_db
def test_etp_top_aum_has_kodex200_first(con):
    """L-11: AUM 상위 — KODEX 200 1위(주최 채점 예시)."""
    r = run_template(con, "etp_top_aum", {"instrument_type": "ETF", "limit": 5})
    assert "KODEX 200" in r.rows[0]["pd_abrv_nm"]


@needs_db
def test_fund_counts_master_vs_class(con):
    """L-21: 상품 11,138 vs 클래스 95,618 함정."""
    r = run_template(con, "fund_counts")
    assert r.rows[0]["products"] == 11138 and r.rows[0]["share_classes"] == 95618


@needs_db
def test_fund_sale_status_and_our_sale_flag_are_distinct(con):
    current = run_template(con, "fund_filter", {"on_sale_only": "Y", "limit": 20000})
    ours = run_template(con, "fund_filter",
                        {"on_sale_only": "Y", "thco_sale_only": "Y", "limit": 20000})
    assert len(current.rows) == 8445
    assert len(ours.rows) == 8434
    assert all(row["sale_yn"] == "판매중" for row in current.rows)
    assert all(row["sale_yn"] == "판매중" and row["thco_sale_yn"] == "Y"
               for row in ours.rows)


@needs_db
def test_constituent_holders_samsung(con):
    """M-01: 삼성전자(005930) 편입 ETF — 다수 존재 + 비중 내림차순 + 기준일 7/10."""
    r = run_template(con, "constituent_holders", {"code": "005930", "limit": 300})
    assert len(r.rows) > 200
    w = [row["weight_pct"] for row in r.rows if row["weight_pct"] is not None]
    assert w == sorted(w, reverse=True)
    assert r.evidences[0].as_of == "2026-07-10"


@needs_db
def test_constituent_weight_above_30(con):
    """H-14: 삼성전자 비중 30%+ ETF 존재(실측 TIGER 200 33.03)."""
    r = run_template(con, "constituent_weight_above",
                     {"code": "005930", "min_weight": 30, "limit": 10})
    assert len(r.rows) >= 1 and all(row["weight_pct"] > 30 for row in r.rows)


@needs_db
def test_mgmt_top_share_uses_resolved(con):
    """H-29: 운용사 점유 집계 — 복구값 기준(상품명 오염값이 그룹에 없어야 함)."""
    r = run_template(con, "mgmt_top_share", {"limit": 10})
    names = [row["mgmt_co"] for row in r.rows]
    assert all("투자신탁" not in n for n in names)
    assert len(names) == 10


@needs_db
def test_coverage_check_fee_matches_known_gap(con):
    """L-26 분모: 총보수 커버리지가 낮음(실질결측 87.5% 실측과 정합 — 20% 미만)."""
    r = run_template(con, "coverage_check", {"field": "kr_etp.cu_charge_rt"})
    row = r.rows[0]
    assert row["total"] > 1000 and row["coverage_pct"] < 20


@needs_db
def test_entity_index_grounding(index):
    """인덱스: 상품 약칭·한글 별칭·구성종목 exact — 현금 센티널은 없어야 함."""
    kinds = {r.kind for r in index.exact("TIGER 200")}
    assert "product_kr_etp" in kinds
    assert any(r.key == "KR70047A0007" for r in index.exact("타이거 차이나테크톱텐"))
    assert any(r.key == "KR7148020001" for r in index.exact("KBSTAR 200"))
    cam = index.exact("캠브리콘")
    assert any(r.kind == "constituent" and r.key == "CNE1000041R8" for r in cam)
    assert index.exact("설정현금액") == []          # 현금성은 개체가 아니다(트랩 방어)
    assert index.exact("존재하지않는상품XYZ") == []


@needs_db
def test_keyword_lookup_exact_then_fallback(index):
    refs, evs, exact = keyword_lookup(index, "TIGER 200")
    assert exact and refs and evs[0].channel == "keyword"
    refs2, evs2, exact2 = keyword_lookup(index, "차이나테크", limit=5)
    assert not exact2 and refs2                     # 부분 일치 폴백(안내용)
    assert all("부분 일치" in e.note for e in evs2)
