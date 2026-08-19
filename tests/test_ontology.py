# -*- coding: utf-8 -*-
"""구현 순서 ⑦-2 테스트 — 온톨로지 5파일(공식 형식) + SHACL 데이터 규칙.

무엇: ① 5파일이 Turtle 로 정상 파싱되고 공식 예시 선언(fp:Product, fp:ForeignETF ⊑ fp:ETF,
      접두어 fp:)을 담는지 ② 그래프 생성 코드(kg/build_kg.py)·검색 코드가 쓰는 항이 전부
      온톨로지에 선언돼 있는지("코드 어휘 ⊆ 온톨로지") ③ SHACL 규칙(shapes.ttl)이 위반
      표본을 실제로 잡아내는지(빈 검사가 아님을 증명) ④ 생성기 출력이 규칙을 지키는지.
왜  : 온톨로지는 소스코드 필수 제출물(파일명까지 지정)이라 깨지면 안 되고, 코드와 어휘가
      어긋나면 그래프 채널이 조용히 '상품 없음'으로 오답한다.
"""
import io
import os
import re

import pandas as pd
import pytest

rdflib = pytest.importorskip("rdflib")
pyshacl = pytest.importorskip("pyshacl")
from rdflib.namespace import OWL, RDF, RDFS  # noqa: E402

from kg import build_kg as bk                        # noqa: E402
from kg import validate_shacl as vs                  # noqa: E402
from kg.kg_store import FP as FP_STR, PRODUCT_CLASSES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONT_DIR = os.path.join(ROOT, "ontology")
FIVE = ("common.ttl", "bond_kr.ttl", "etf_kr.ttl", "etf_gl.ttl", "fund_pub.ttl")
FP = rdflib.Namespace(FP_STR)
FPR = rdflib.Namespace(bk.FPR)
XSD = rdflib.namespace.XSD


@pytest.fixture(scope="module")
def graphs():
    out = {}
    for f in FIVE + ("shapes.ttl",):
        g = rdflib.Graph()
        g.parse(os.path.join(ONT_DIR, f), format="turtle")
        out[f] = g
    return out


@pytest.fixture(scope="module")
def union(graphs):
    g = rdflib.Graph()
    for f in FIVE:
        g += graphs[f]
    return g


# ---------------------------------------------------------------------------
# 1. 5파일 형식·공식 예시 선언
# ---------------------------------------------------------------------------

def test_official_five_files_exist_and_parse(graphs):
    """공식 자료(과제 설명 p.9)가 지정한 파일명 5개 — 전부 존재하고 Turtle 로 파싱된다."""
    for f in FIVE:
        assert os.path.exists(os.path.join(ONT_DIR, f)), f
        assert len(graphs[f]) > 20, f
    assert not os.path.exists(os.path.join(ONT_DIR, "finance.ttl"))   # 옛 단일 파일은 제거됨


def test_official_prefix_and_top_class(graphs, union):
    """접두어 fp: <http://mafest.ai/product#> · 공통 상위 클래스 fp:Product(common.ttl)."""
    common = graphs["common.ttl"]
    assert dict(common.namespaces())["fp"] == rdflib.URIRef("http://mafest.ai/product#")
    assert (FP.Product, RDF.type, OWL.Class) in common
    # 상품군 뿌리 3개가 전부 fp:Product 아래
    for cls in (FP.Bond, FP.ExchangeTradedProduct, FP.PublicFund):
        assert (cls, RDFS.subClassOf, FP.Product) in union, cls


def test_official_example_declaration_foreign_etf(graphs):
    """공식 예시 그대로: fp:ForeignETF rdfs:subClassOf fp:ETF ; rdfs:label "해외ETF"@ko (etf_gl.ttl)."""
    g = graphs["etf_gl.ttl"]
    assert (FP.ForeignETF, RDFS.subClassOf, FP.ETF) in g
    assert rdflib.Literal("해외ETF", lang="ko") in set(g.objects(FP.ForeignETF, RDFS.label))
    # 짝이 되는 국내 클래스는 etf_kr.ttl 에
    assert (FP.DomesticETF, RDFS.subClassOf, FP.ETF) in graphs["etf_kr.ttl"]
    # 공식 예시의 fp:expenseRatio 는 소수(xsd:decimal) 데이터 속성 — 도메인은 ETP(ETN 포함)로 넓힘
    assert (FP.expenseRatio, RDFS.range, XSD.decimal) in graphs["common.ttl"]


def test_domain_files_import_common(graphs):
    for f in FIVE[1:] + ("shapes.ttl",):
        onts = list(graphs[f].subjects(RDF.type, OWL.Ontology))
        assert len(onts) == 1, f
        assert (onts[0], OWL.imports, rdflib.URIRef("http://mafest.ai/product/common")) in graphs[f], f


def test_class_hierarchy_and_disjointness(union):
    """ETF/ETN 배타, 상품군 3갈래 배타, 국내/해외 하위 클래스가 제자리에."""
    assert (FP.ETF, OWL.disjointWith, FP.ETN) in union
    assert (FP.Bond, OWL.disjointWith, FP.ExchangeTradedProduct) in union
    assert (FP.PublicFund, OWL.disjointWith, FP.Bond) in union
    for leaf, parent in ((FP.DomesticETF, FP.ETF), (FP.ForeignETF, FP.ETF),
                         (FP.DomesticETN, FP.ETN), (FP.ForeignETN, FP.ETN)):
        assert (leaf, RDFS.subClassOf, parent) in union
    # 해외ETP 는 위험등급을 갖지 않는다 — owl:maxCardinality 0 제한(etf_gl.ttl)
    restr = [o for o in union.objects(FP.ForeignETF, RDFS.subClassOf)
             if (o, OWL.onProperty, FP.riskGrade) in union]
    assert restr and union.value(restr[0], OWL.maxCardinality).toPython() == 0


def test_every_term_used_in_ontology_is_declared(union):
    """도메인·범위·상하위·역관계에 등장하는 fp: 항은 전부 클래스/속성으로 선언돼 있다."""
    declared = (set(union.subjects(RDF.type, OWL.Class))
                | set(union.subjects(RDF.type, OWL.ObjectProperty))
                | set(union.subjects(RDF.type, OWL.DatatypeProperty)))
    used = set()
    for p in (RDFS.domain, RDFS.range, RDFS.subClassOf, OWL.disjointWith, OWL.inverseOf, OWL.onProperty):
        for s, o in union.subject_objects(p):
            for t in (s, o):
                if isinstance(t, rdflib.URIRef) and str(t).startswith(FP_STR):
                    used.add(t)
    assert not (used - declared), sorted(str(t) for t in used - declared)


# ---------------------------------------------------------------------------
# 2. 코드 어휘 ⊆ 온톨로지 선언
# ---------------------------------------------------------------------------

def _declared_locals(union):
    names = set()
    for typ in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty):
        for s in union.subjects(RDF.type, typ):
            if isinstance(s, rdflib.URIRef) and str(s).startswith(FP_STR):
                names.add(str(s)[len(FP_STR):])
    return names


def _code_locals():
    """build_kg.py 의 fp_term("X")·ETP_CLASS·aux_node 클래스, 검색 코드의 FP + "X" 사용처."""
    names = set()
    src = io.open(os.path.join(ROOT, "kg", "build_kg.py"), encoding="utf-8").read()
    names |= set(re.findall(r'fp_term\("(\w+)"\)', src))
    names |= set(bk.ETP_CLASS.values()) | {"ExchangeTradedProduct"}
    names |= set(re.findall(r'aux_node\("[^"]+",\s*[^,]+,\s*"(\w+)"', src))
    for rel in ("kg/kg_store.py", "kg/query_kg.py", "engine/channels.py"):
        text = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
        names |= set(re.findall(r'FP \+ "(\w+)"', text))
    names |= {c[len(FP_STR):] for c in PRODUCT_CLASSES}
    return names


def test_code_vocabulary_is_subset_of_ontology(union):
    declared = _declared_locals(union)
    used = _code_locals()
    assert len(used) >= 40                                   # 실제로 많이 잡혔는지(빈 검사 방지)
    assert used <= declared, sorted(used - declared)


def test_instance_classes_are_leaf_classes(union):
    """인스턴스에 붙는 클래스(PRODUCT_CLASSES)는 하위 클래스가 없는 잎이거나 판정 불가용 ETP."""
    for c in PRODUCT_CLASSES:
        node = rdflib.URIRef(c)
        children = [s for s in union.subjects(RDFS.subClassOf, node) if isinstance(s, rdflib.URIRef)]
        if node == FP.ExchangeTradedProduct:
            assert children                                  # ETF·ETN 이 아래에 있다
        else:
            assert not children, (c, children)


# ---------------------------------------------------------------------------
# 3. SHACL — 위반 표본을 실제로 잡는가 / 생성기 출력은 규칙을 지키는가
# ---------------------------------------------------------------------------

def _g(nt_text):
    g = rdflib.Graph()
    g.parse(data=nt_text, format="nt")
    return g


def _nt(s, p, o):
    return f"{s} {p} {o} .\n"


def test_shacl_catches_known_violations():
    """규칙 위반 표본 5종 — 각각의 메시지가 결과에 나타난다(빈 검사가 아님)."""
    T, L, D = bk.RDF_TYPE, bk.RDFS_LABEL, bk.lit_typed
    def base(s, cls, group, pid):
        return (_nt(s, T, bk.fp_term(cls)) + _nt(s, bk.fp_term("productId"), bk.lit(pid))
                + _nt(s, bk.fp_term("sourceTable"), bk.lit("T")) + _nt(s, bk.fp_term("productGroup"), bk.lit(group)))
    a, b, c, d, e = (bk.res("kr-etf", "A"), bk.res("global-etf", "B"), bk.res("bond", "C"),
                     bk.res("kr-etf", "D"), bk.res("fund", "E"))
    text = (
        base(a, "DomesticETF", "국내ETF", "A") + _nt(a, bk.fp_term("riskGrade"), D("99", "integer"))     # 범위 밖
        + base(b, "ForeignETF", "해외ETF", "B") + _nt(b, bk.fp_term("riskGrade"), D("3", "integer"))    # 해외에 위험등급
        + base(c, "Bond", "국내채권", "C") + _nt(c, bk.fp_term("creditRating"), bk.lit("AAAA"))          # 등급 체계 밖
        + base(d, "DomesticETF", "국내ETF", "D") + _nt(d, T, bk.fp_term("DomesticETN"))                 # ETF ∧ ETN
        + base(e, "PublicFund", "공모펀드", "E") + _nt(e, bk.fp_term("expenseRatio"), D("0.5", "decimal"))  # 펀드 총보수
    )
    conforms, results, _ = vs.validate_graph(_g(text))
    assert not conforms
    msgs = " | ".join(m for m, _, _ in vs.summarize(results))
    for expected in ("위험등급은 1~6", "해외ETF/ETN 은 위험등급이 없다", "AAA~D", "ETF 이면서 ETN", "공모펀드 마스터에는 총보수가 없다"):
        assert expected in msgs, expected


def test_shacl_targets_via_subclass_closure():
    """상위 클래스 대상 shape(fp:Product·fp:ETF)가 잎 클래스 인스턴스(fp:DomesticETF)에 적용된다."""
    s = bk.res("kr-etf", "Z")
    text = (_nt(s, bk.RDF_TYPE, bk.fp_term("DomesticETF"))                 # productId·sourceTable 없음
            + _nt(s, bk.fp_term("expenseRatio"), bk.lit_typed("-1", "decimal")))   # 음수 총보수
    conforms, results, _ = vs.validate_graph(_g(text))
    assert not conforms
    msgs = " | ".join(m for m, _, _ in vs.summarize(results))
    assert "상품번호" in msgs and "총보수는 0 이상" in msgs


def test_generator_output_conforms(tmp_path):
    """build_kg 의 합성 출력(국내ETF·해외ETF·채권·펀드·구성종목)이 SHACL 규칙을 전부 지킨다."""
    kr = [{"pd_itm_no": "KR7102110004", "pd_nm": "TIGER 200", "pd_abrv_nm": "TIGER 200",
           "cu_fund_mgmt_co": "미래에셋", "cu_charge_rt": "0.05", "cu_base_index": "코스피 200",
           "pd_net_tamt": "100", "du_last_aum": "100", "drv_risk_grade": "2", "drv_instrument_type": "ETF",
           "drv_listing_status": "active", "drv_curr_cd": "KRW", "pd_lstg_dt": "20080102", "du_er_1y": "12.3"},
          {"pd_itm_no": "KR7500001111", "pd_nm": "삼성 레버리지 ETN", "cu_fund_mgmt_co": "삼성증권",
           "pd_net_tamt": "5", "du_last_aum": "0", "drv_risk_grade": "1", "drv_instrument_type": "ETN",
           "drv_listing_status": "delisted", "pd_lste_dt": "20250101"}]
    gl = [{"pd_itm_no": "US1", "pd_nm": "SPDR S&P 500", "cu_fund_mgmt_co": "SSGA", "cu_charge_rt": "0.09",
           "du_last_aum": "1", "drv_instrument_type": "ETF", "pd_trd_ccy": "USD", "drv_is_inverse": "N"},
          {"pd_itm_no": "US2", "pd_nm": "Some ETN", "drv_instrument_type": "ETN"}]
    bond = [{"PD_NO": "KR1", "PD_NM": "국고채", "PD_PBCM": "대한민국", "STD_PD_MCLS_NM": "국공채",
             "MAT_DT": "20300101", "ISU_DT": "20200101", "SRFC_IRT": "3.5", "drv_crd_grd_norm": "AAA",
             "drv_crd_grd_rank": "1", "drv_risk_grade": "6", "drv_maturity_status": "active"},
            {"PD_NO": "KR2", "PD_NM": "영구채", "PD_PBCM": "발행사", "MAT_DT": "99991231",
             "drv_is_perpetual": "Y", "drv_risk_grade": "1"}]
    fund = [{"itm_no": "F1", "prfd_attr_cd": "A", "itm_nm": "펀드", "or_co_xtn_itt_cd": "00040010",
             "drv_risk_grade": "4", "sale_yn": "판매중", "bmrk_nm": "KOSPI"},
            {"itm_no": "F1", "prfd_attr_cd": "C", "itm_nm": "펀드", "or_co_xtn_itt_cd": "00040010",
             "drv_risk_grade": "4", "sale_yn": "판매중", "bmrk_nm": "KOSPI"}]
    const = [{"etf_isin": "KR7102110004", "etf_name": "TIGER 200", "COMPST_ISU_CD": "005930",
              "SECUGRP_ID": "ST", "COMPST_ISU_NM": "삼성전자"},
             {"etf_isin": "KR7102110004", "etf_name": "TIGER 200", "COMPST_ISU_CD": "US67066G1040",
              "SECUGRP_ID": None, "COMPST_ISU_NM": "NVIDIA CORP"}]
    for slug_name, rows in (("kr_etf", kr), ("global_etf", gl), ("kr_bond", bond), ("public_fund", fund)):
        bk.build_table(slug_name, pd.DataFrame(rows, dtype=str), str(tmp_path))
    bk.build_constituents(pd.DataFrame(const, dtype=str), str(tmp_path), "c.csv")
    paths = [os.path.join(str(tmp_path), f) for f in sorted(os.listdir(str(tmp_path))) if f.endswith(".nt")]
    conforms, summary, n = vs.validate_files(paths, limit=0)
    assert n > 40
    assert conforms, summary


def test_read_head_keeps_last_subject_whole(tmp_path):
    """표본 읽기는 마지막 노드의 트리플을 자르지 않는다(가짜 '상품번호 없음' 방지)."""
    s1, s2 = bk.res("bond", "1"), bk.res("bond", "2")
    text = ("".join(_nt(s1, bk.fp_term("p"), bk.lit(str(i))) for i in range(3))
            + "".join(_nt(s2, bk.fp_term("p"), bk.lit(str(i))) for i in range(4)))
    p = tmp_path / "x.nt"
    p.write_text(text, encoding="utf-8")
    head = vs.read_head(str(p), 4)          # 4줄 요청 → 두 번째 주어의 나머지 3줄까지 포함
    assert head.count("\n") == 7
    assert vs.read_head(str(p), 3).count("\n") == 3
    assert vs.read_head(str(p), 0) == text
