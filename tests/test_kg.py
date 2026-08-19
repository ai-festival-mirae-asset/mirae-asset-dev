# -*- coding: utf-8 -*-
"""kg/build_kg.py · kg/kg_store.py 테스트.

무엇: ① 직렬화·검증 순수 함수 ② 추출기 매핑(온톨로지 제약 포함) ③ 합성 CSV E2E —
      S1 DoD 질의("TIGER 200의 운용사는?")가 그래프에서 답해지는지.
왜  : 실데이터 전량 빌드는 느려서 수동 CLI 로 검증하고, 테스트는 합성 데이터로
      전체 경로(추출 → .nt → 스토어 적재 → 질의)를 빠르게 회귀한다.
"""
import io
import os

import pandas as pd
import pytest

from kg import build_kg as bk
from kg.kg_store import FP, RDFS_LABEL, TripleStore, norm_name, parse_line


# ---------------------------------------------------------------------------
# 1. 직렬화·검증 순수 함수
# ---------------------------------------------------------------------------

def test_esc_roundtrip_via_parser():
    tricky = '한글 "따옴표" \\역슬래시\\ 탭\t줄\n바꿈'
    line = f"<http://s> <http://p> {bk.lit(tricky)} ."
    s, p, o, kind = parse_line(line)
    assert (s, p, kind) == ("http://s", "http://p", "literal")
    assert o == tricky


def test_slug_percent_encodes():
    assert bk.slug(" 미래에셋 ") == bk.slug("미래에셋")
    assert "/" not in bk.slug("a/b c")
    assert " " not in bk.slug("TIGER 200")


@pytest.mark.parametrize("raw, expected", [
    ("2025-01-31", "2025-01-31"),
    ("20241217", "2024-12-17"),
    ("99991231", None),          # 영구·미도래 센티널
    ("9999-12-31", None),
    ("dneho", None),
    (None, None),
])
def test_as_date(raw, expected):
    assert bk.as_date(raw) == expected


@pytest.mark.parametrize("raw, ok", [
    ("0", True), ("3.59", True), ("-100.0", True), (".5", True),
    ("1e5", False), ("abc", False), ("", False),
])
def test_as_decimal(raw, ok):
    assert (bk.as_decimal(raw or None) is not None) == ok


def test_valid_risk_grade_range_constraint():
    """온톨로지 fp:riskGrade 범위 제약(1~6)의 코드 구현 — 밖이면 거부 + 집계."""
    violations = {}
    assert bk.valid_risk_grade("3", violations) == "3"
    assert bk.valid_risk_grade("6", violations) == "6"
    assert bk.valid_risk_grade("99", violations) is None
    assert bk.valid_risk_grade("0", violations) is None
    assert bk.valid_risk_grade(None, violations) is None
    assert violations == {"99": 1, "0": 1}


# ---------------------------------------------------------------------------
# 2. 추출기 매핑 — 합성 행 단위
# ---------------------------------------------------------------------------

def emit_rows(extractor, rows, **kw):
    buf = io.StringIO()
    em = bk.TableEmitter(buf)
    stats = bk.new_stats(len(rows))
    for row in rows:
        extractor(em, row, stats, **kw)
    return buf.getvalue(), stats


def load_store(nt_text):
    store = TripleStore()
    for line in nt_text.splitlines():
        parsed = parse_line(line)
        if parsed:
            store.add(*parsed[:3])
    return store


KR_ETF_ROW = {
    "pd_itm_no": "KR7102110004",
    "pd_nm": "미래에셋 TIGER 200 증권상장지수투자신탁(주식)",
    "pd_abrv_nm": "TIGER 200",
    "cu_fund_mgmt_co": "미래에셋",
    "cu_charge_rt": "0.05",
    "cu_base_index": "코스피 200",
    "pd_net_tamt": "11278564148232.0",
    "du_last_aum": "11278564148232.0",
    "drv_risk_grade": "2",
    "drv_instrument_type": "ETF",
    "drv_listing_status": "active",
    "drv_curr_cd": "KRW",
    "wu_inv_ast_type": "주식",
    "wu_inv_rgn": "국내",
    "pd_lstg_dt": "20080102",
    "du_er_1y": "12.3",
    "du_er_ytd": "5.1",
}


def test_kr_etf_row_mapping():
    nt, stats = emit_rows(bk.extract_kr_etf_row, [KR_ETF_ROW])
    store = load_store(nt)
    s = bk.res("kr-etf", "KR7102110004")[1:-1]
    assert store.types(s) == [FP + "DomesticETF"]        # 가장 구체적인 클래스 하나만(etf_kr.ttl)
    assert store.object(s, FP + "riskGrade") == "2"
    assert store.object(s, FP + "expenseRatio") == "0.05"
    assert store.object(s, FP + "listedDate") == "2008-01-02"
    mgmt = store.object(s, FP + "managedBy")
    assert store.label(mgmt) == "미래에셋"
    idx = store.object(s, FP + "tracksIndex")
    assert store.label(idx) == "코스피 200"
    assert stats["product_nodes"] == 1


def test_etf_etn_disjoint_guard():
    """instrument_type 불명 행은 ETF 로 단정하지 않고 상위클래스로만 타이핑한다."""
    row = dict(KR_ETF_ROW, pd_itm_no="X1", drv_instrument_type="이상값")
    nt, stats = emit_rows(bk.extract_kr_etf_row, [row])
    store = load_store(nt)
    s = bk.res("kr-etf", "X1")[1:-1]
    assert store.types(s) == [FP + "ExchangeTradedProduct"]
    assert stats["instrument_type_unresolved"] == 1


def test_etn_aum_not_emitted():
    """ETN 은 du_last_aum 전량 0 실측 — aum 트리플을 만들지 않는다(netAssets 만)."""
    row = dict(KR_ETF_ROW, pd_itm_no="X2", drv_instrument_type="ETN", du_last_aum="0")
    nt, _ = emit_rows(bk.extract_kr_etf_row, [row])
    store = load_store(nt)
    s = bk.res("kr-etf", "X2")[1:-1]
    assert store.types(s) == [FP + "DomesticETN"]
    assert store.object(s, FP + "aum") is None
    assert store.object(s, FP + "netAssets") is not None


def test_global_rows_get_foreign_classes():
    """해외ETF 마스터 행은 fp:ForeignETF / fp:ForeignETN (etf_gl.ttl — 공식 예시 클래스)."""
    rows = [{"pd_itm_no": "G-ETF", "pd_nm": "SPDR S&P 500", "drv_instrument_type": "ETF",
             "du_last_aum": "1.5", "pd_trd_ccy": "USD"},
            {"pd_itm_no": "G-ETN", "pd_nm": "iPath ETN", "drv_instrument_type": "ETN",
             "du_last_aum": "0"}]
    nt, stats = emit_rows(bk.extract_global_etf_row, rows)
    store = load_store(nt)
    etf, etn = bk.res("global-etf", "G-ETF")[1:-1], bk.res("global-etf", "G-ETN")[1:-1]
    assert store.types(etf) == [FP + "ForeignETF"]
    assert store.types(etn) == [FP + "ForeignETN"]
    assert store.object(etf, FP + "productGroup") == "해외ETF"
    assert store.object(etf, FP + "tradingCurrency") == "USD"
    assert store.object(etf, FP + "aum") == "1.5" and store.object(etn, FP + "aum") is None
    assert stats["instrument_type_unresolved"] == 0


def test_kg_namespace_matches_official_prefix():
    """8/19 공식 접두어: fp: <http://mafest.ai/product#> — 생성기·스토어·온톨로지 파일이 같은 값."""
    from kg.kg_store import FP as STORE_FP, MF as LEGACY_ALIAS
    assert bk.FP == STORE_FP == "http://mafest.ai/product#"
    assert LEGACY_ALIAS == STORE_FP                       # 옛 이름 호환 별칭
    text = io.open(os.path.join(bk.ROOT, "ontology", "common.ttl"), encoding="utf-8").read()
    assert "@prefix fp:   <http://mafest.ai/product#>" in text
    assert "@prefix fpr:  <http://mafest.ai/resource/>" in text and bk.FPR == "http://mafest.ai/resource/"


def test_bond_row_perpetual_has_no_maturity_date():
    row = {"PD_NO": "B1", "PD_NM": "영구채 테스트", "PD_PBCM": "발행사A",
           "MAT_DT": "9999-12-31", "drv_is_perpetual": "Y", "drv_risk_grade": "99"}
    nt, stats = emit_rows(bk.extract_bond_row, [row])
    store = load_store(nt)
    s = bk.res("bond", "B1")[1:-1]
    assert store.object(s, FP + "maturityDate") is None
    assert store.object(s, FP + "isPerpetual") == "true"
    assert store.object(s, FP + "riskGrade") is None       # 99 → 범위 제약 거부
    assert stats["risk_grade_dropped"] == {"99": 1}
    issuer = store.object(s, FP + "issuedBy")
    assert store.label(issuer) == "발행사A"


def test_missing_values_make_no_triples():
    """결측은 트리플 미생성 — '확인할 수 없음' 답변의 근거(해외ETF 위험등급 등)."""
    row = {"pd_itm_no": "G1", "pd_nm": "테스트 해외ETF", "drv_instrument_type": "ETF"}
    nt, _ = emit_rows(bk.extract_global_etf_row, [row])
    store = load_store(nt)
    s = bk.res("global-etf", "G1")[1:-1]
    assert store.object(s, FP + "riskGrade") is None
    assert store.object(s, FP + "expenseRatio") is None
    assert store.object(s, FP + "managedBy") is None


# ---------------------------------------------------------------------------
# 3. 합성 CSV E2E — build_table → .nt → 스토어 → DoD 질의
# ---------------------------------------------------------------------------

def test_e2e_dod_query_tiger200(tmp_path):
    """S1 DoD(CQ1): 'TIGER 200의 운용사는?' → 미래에셋. CQ2 역관계도 함께 확인."""
    rows = [
        KR_ETF_ROW,
        dict(KR_ETF_ROW, pd_itm_no="KR0000000001", pd_nm="삼성 KODEX 200 증권상장지수투자신탁(주식)",
             pd_abrv_nm="KODEX 200", cu_fund_mgmt_co="삼성"),
    ]
    df = pd.DataFrame(rows, dtype=str)
    stats = bk.build_table("kr_etf", df, str(tmp_path))
    assert stats["product_nodes"] == 2 and stats["triples"] > 0

    store = TripleStore.from_dir(str(tmp_path))
    hits = store.search_products("TIGER 200")
    assert len(hits) == 1
    s, _ = hits[0]
    mgmt = store.object(s, FP + "managedBy")
    assert store.label(mgmt) == "미래에셋"                    # ← DoD
    # 역관계(CQ2): 미래에셋 노드에서 managedBy 역방향으로 상품이 찾아진다
    assert s in store.subjects(FP + "managedBy", mgmt)


def test_fund_master_grouping(tmp_path):
    """공모펀드는 itm_no 마스터 1노드 + shareClassCount 로 클래스 행 수 보존."""
    base = {"itm_nm": "테스트펀드", "or_co_xtn_itt_cd": "00040010",
            "drv_risk_grade": "4", "fd_ivst_rgn_desc": "국내", "sale_yn": "판매중"}
    rows = [dict(base, itm_no="F1", prfd_attr_cd="A"),
            dict(base, itm_no="F1", prfd_attr_cd="C"),
            dict(base, itm_no="F2", prfd_attr_cd="A", itm_nm="테스트펀드2", sale_yn="판매완료")]
    df = pd.DataFrame(rows, dtype=str)
    stats = bk.build_table("public_fund", df, str(tmp_path))
    assert stats["product_nodes"] == 2

    store = TripleStore.from_dir(str(tmp_path))
    f1 = bk.res("fund", "F1")[1:-1]
    assert store.types(f1) == [FP + "PublicFund"]
    assert store.object(f1, FP + "shareClassCount") == "2"
    mgmt = store.object(f1, FP + "managedBy")
    assert store.object(mgmt, FP + "companyCode") == "00040010"   # 코드 노드(명칭 없음)
    assert store.label(mgmt) is None
    # sale_yn 실제 값('판매중'/'판매완료' — Y/N 아님)이 판매중 여부로 적재된다(8/19 정정)
    assert store.object(f1, FP + "isOnSale") == "true"
    assert store.object(bk.res("fund", "F2")[1:-1], FP + "isOnSale") == "false"


def test_legacy_namespace_graph_is_rejected(tmp_path):
    """8/18 이전 어휘(mf:)로 만든 .nt 를 적재하면 조용히 '상품 없음'이 되지 않고 즉시 멈춘다."""
    old = ('<https://ai-festival-mirae-asset.github.io/resource/kr-etf/X> '
           '<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> '
           '<https://ai-festival-mirae-asset.github.io/ontology/finance#ETF> .\n')
    path = tmp_path / "kr_etf.nt"
    path.write_text(old, encoding="utf-8")
    with pytest.raises(ValueError, match="재생성"):
        TripleStore.from_dir(str(tmp_path))


def test_nt_file_self_contained_and_dedup(tmp_path):
    """같은 운용사가 여러 행에 나와도 회사 노드 트리플은 파일당 1회."""
    rows = [KR_ETF_ROW, dict(KR_ETF_ROW, pd_itm_no="Z2", pd_nm="미래에셋 TIGER 다른상품")]
    df = pd.DataFrame(rows, dtype=str)
    bk.build_table("kr_etf", df, str(tmp_path))
    text = io.open(os.path.join(str(tmp_path), "kr_etf.nt"), encoding="utf-8").read()
    company_type_lines = [l for l in text.splitlines()
                          if "company/" in l.split(" ")[0] and "#type>" in l]
    assert len(company_type_lines) == 1


def test_norm_name():
    assert norm_name("TIGER 200") == norm_name("tiger200") == "tiger200"


# ---------------------------------------------------------------------------
# 4. 구성종목 추출기 (8/13 — KRX 수집분, CQ6)
# ---------------------------------------------------------------------------

CONST_ROW = {
    "etf_isin": "KR7102110004",
    "etf_name": "TIGER 200",
    "COMPST_ISU_CD": "005930",
    "COMPST_ISU_CD2": "KR7005930003",
    "MKT_ID": "STK",
    "SECUGRP_ID": "ST",
    "COMPST_ISU_NM": "삼성전자",
    "COMPST_ISU_CU1_SHRS": "6,984.00",
    "COMPST_RTO": "33.03",
}


def emit_const_rows(rows):
    buf = io.StringIO()
    em = bk.TableEmitter(buf)
    stats = bk.new_constituents_stats(len(rows))
    seen = set()
    for row in rows:
        bk.extract_constituent_row(em, row, stats, seen)
    return buf.getvalue(), stats


def test_constituent_stock_row_membership():
    """주식 구성종목 1행 → ETF 라벨 + 회사 노드(타입·라벨·코드) + membership 관계."""
    buf = io.StringIO()
    em = bk.TableEmitter(buf)
    stats = bk.new_constituents_stats(1)
    bk.extract_constituent_row(em, CONST_ROW, stats, set())
    store = load_store(buf.getvalue())
    etf = bk.res("kr-etf", "KR7102110004")[1:-1]
    company = store.object(etf, FP + "holdsConstituent")
    assert company is not None
    assert FP + "ListedCompany" in store.types(company)
    assert store.label(company) == "삼성전자"
    assert store.object(company, FP + "tickerCode") == "005930"
    assert store.label(etf) == "TIGER 200"
    assert store.subjects(FP + "holdsConstituent", company) == [etf]  # CQ6 역질의
    assert stats["edges"] == 1 and stats["etf_nodes"] == 1


def test_constituent_etf_uri_joins_master():
    """구성종목의 ETF IRI 는 국내ETF 마스터 추출기와 동일해야 조인이 성립한다."""
    nt_master, _ = emit_rows(bk.extract_kr_etf_row, [KR_ETF_ROW])
    nt_const, _ = emit_const_rows([CONST_ROW])
    subj = bk.res("kr-etf", "KR7102110004")
    assert subj in nt_master and subj in nt_const


def test_constituent_non_stock_and_no_code_skipped():
    """비주식(EF 등)·코드 없는 행(현금)은 관계를 만들지 않고 집계만 한다."""
    rows = [dict(CONST_ROW, SECUGRP_ID="EF", COMPST_ISU_CD="152100",
                 COMPST_ISU_NM="ARIRANG 200"),
            dict(CONST_ROW, COMPST_ISU_CD=None, COMPST_ISU_NM="원화현금")]
    nt, stats = emit_const_rows(rows)
    assert stats["edges"] == 0
    assert stats["skipped_non_stock"] == {"EF": 1}
    assert stats["skipped_no_code"] == 1
    assert "holdsConstituent" not in nt


def test_constituent_dedupe_across_etfs():
    """같은 종목을 두 ETF 가 편입 — 회사 노드는 1회, membership 은 2건."""
    rows = [CONST_ROW,
            dict(CONST_ROW, etf_isin="KR7069500007", etf_name="KODEX 200")]
    nt, stats = emit_const_rows(rows)
    store = load_store(nt)
    company = bk.res("company", "krx-005930")[1:-1]
    type_lines = [l for l in nt.splitlines() if "#type>" in l and "company/" in l]
    assert len(type_lines) == 1
    assert len(store.subjects(FP + "holdsConstituent", company)) == 2
    assert stats["edges"] == 2 and stats["etf_nodes"] == 2


def test_constituent_foreign_stock_label_variants():
    """해외 주식은 ISIN 키로 적재되고, 운용사별 이름 표기 변형이 복수 라벨로 합쳐진다."""
    rows = [dict(CONST_ROW, SECUGRP_ID=None, MKT_ID=None,
                 COMPST_ISU_CD="CNE1000041R8", COMPST_ISU_NM="CAMBRICON TECHNOLOGIES-A"),
            dict(CONST_ROW, etf_isin="KR7069500007", etf_name="다른 차이나 ETF",
                 SECUGRP_ID=None, MKT_ID=None,
                 COMPST_ISU_CD="CNE1000041R8", COMPST_ISU_NM="Cambricon Technologies Corp Ltd")]
    nt, stats = emit_const_rows(rows)
    store = load_store(nt)
    company = bk.res("company", "isin-CNE1000041R8")[1:-1]
    assert stats["edges_foreign"] == 2 and stats["edges_domestic"] == 0
    assert FP + "ListedCompany" in store.types(company)
    assert store.object(company, FP + "securityIsin") == "CNE1000041R8"
    labels = store.objects(company, RDFS_LABEL)
    assert len(labels) == 2                      # 두 표기 전부 보존 — 이름 검색 성립 조건
    assert len(store.subjects(FP + "holdsConstituent", company)) == 2


def test_constituent_reit_included_cash_and_futures_excluded():
    """리츠(RT)는 상장 종목으로 포함 — 현금성(KR 접두)·해외선물(비ISIN)은 제외."""
    rows = [dict(CONST_ROW, SECUGRP_ID="RT", COMPST_ISU_CD="395400", COMPST_ISU_NM="SK리츠"),
            dict(CONST_ROW, SECUGRP_ID=None, COMPST_ISU_CD="KRD010010001", COMPST_ISU_NM="원화현금"),
            dict(CONST_ROW, SECUGRP_ID=None, COMPST_ISU_CD="ESU25", COMPST_ISU_NM="S&P500 FUT")]
    nt, stats = emit_const_rows(rows)
    assert stats["edges"] == 1 and stats["edges_domestic"] == 1
    assert stats["skipped_non_stock"] == {"(현금성·기타)": 2}
    store = load_store(nt)
    reit = bk.res("company", "krx-395400")[1:-1]
    assert store.label(reit) == "SK리츠"


def test_constituent_cash_sentinel_isin_excluded():
    """ISIN 형식을 통과하는 현금성 센티널(CASH·USDZZ·JPYZZ — 8/13 실측)은 회사가 아니다."""
    rows = [dict(CONST_ROW, SECUGRP_ID=None, COMPST_ISU_CD="CASH00000001", COMPST_ISU_NM="설정현금액"),
            dict(CONST_ROW, SECUGRP_ID=None, COMPST_ISU_CD="USDZZ0000001", COMPST_ISU_NM="USD현금"),
            dict(CONST_ROW, SECUGRP_ID=None, COMPST_ISU_CD="JPYZZ0000001", COMPST_ISU_NM="[JPY] 예금"),
            dict(CONST_ROW, SECUGRP_ID=None, COMPST_ISU_CD="US67066G1040", COMPST_ISU_NM="NVIDIA CORP")]
    nt, stats = emit_const_rows(rows)
    assert stats["edges"] == 1 and stats["edges_foreign"] == 1      # NVIDIA 만 남는다
    assert sum(stats["skipped_non_stock"].values()) == 3
    assert "설정현금액" not in nt and "NVIDIA" in nt


def test_build_constituents_e2e(tmp_path):
    """CSV → constituents.nt → 스토어 적재 → CQ6 질의까지 관통."""
    rows = [CONST_ROW,
            dict(CONST_ROW, COMPST_ISU_CD="000660", COMPST_ISU_CD2="KR7000660001",
                 COMPST_ISU_NM="SK하이닉스", COMPST_RTO="30.02"),
            dict(CONST_ROW, COMPST_ISU_CD=None, COMPST_ISU_NM="원화현금")]
    df = pd.DataFrame(rows, dtype=str)
    stats = bk.build_constituents(df, str(tmp_path), "constituents_20260710.csv")
    assert stats["edges"] == 2 and stats["companies"] == 2 and stats["skipped_no_code"] == 1
    assert stats["as_of"] == "2026-07-10"
    store = TripleStore().load(os.path.join(str(tmp_path), "constituents.nt"))
    samsung = bk.res("company", "krx-005930")[1:-1]
    etfs = store.subjects(FP + "holdsConstituent", samsung)
    assert [store.label(s) for s in etfs] == ["TIGER 200"]
