# -*- coding: utf-8 -*-
"""
전처리 CSV 4종(+구성종목 수집분) → 지식그래프(KG) 트리플 추출 파이프라인

입력 : preprocessing/processed/<테이블ID>_<상품군>_processed.csv (preprocess.py 산출물)
       external_data/constituents/constituents_20260821.csv (KRX 수집분 — 있으면 자동 포함)
출력 : kg/output/kr_bond.nt · kr_etf.nt · global_etf.nt · public_fund.nt · constituents.nt
       kg/output/build_report.json (테이블별 노드·트리플 수 + 무결성 위반 집계)

원칙
  1. 어휘는 온톨로지 5파일(ontology/common.ttl + bond_kr·etf_kr·etf_gl·fund_pub.ttl,
     접두어 fp:) 정의만 사용한다 — tests/test_ontology.py 가 "코드가 쓰는 항 ⊆ 온톨로지
     선언"을 검사한다. 인스턴스는 가장 구체적인 클래스로만 타이핑한다(국내ETF 행 →
     fp:DomesticETF, 해외 ETN 행 → fp:ForeignETN …). 상위 클래스(fp:ETF·fp:Product)는
     온톨로지의 rdfs:subClassOf 로 추론되는 몫이라 인스턴스에 직접 붙이지 않는다.
  2. 온톨로지 제약을 적재 전에 코드로 검증한다(SHACL 선언은 ontology/shapes.ttl —
     같은 규칙을 kg/validate_shacl.py 로 독립 재검사할 수 있다):
       - riskGrade ∉ {1..6}  → 해당 트리플 미생성 + 리포트 (범위 무결성 — "99등급" 차단)
       - instrument_type ∉ {ETF, ETN} → 상위클래스(ExchangeTradedProduct)로만 타이핑 + 리포트
         (ETF/ETN disjoint 보호 — 불명 행을 ETF 로 단정하지 않는다)
       - 결측은 트리플을 만들지 않는다 — 없는 값은 없는 것("확인할 수 없음" 답변의 근거).
  3. 저장소 중립: 표준 N-Triples 만 출력한다. 후보 저장소(rdflib/Neo4j/AGE — 8/8 총검토)
     어디든 그대로 적재 가능하며, 질의 데모는 kg/kg_store.py 경량 인덱스를 쓴다.
  4. 결정적(멱등): 같은 입력이면 같은 출력. 타임스탬프 등 휘발 값을 출력에 넣지 않고,
     수치는 원문 문자열(lexical form)을 보존한다.
  5. 각 .nt 파일은 자기완결적이다 — 참조하는 회사·지수 노드의 타입·라벨 트리플을
     같은 파일 안에 포함한다(단독 적재 가능). 파일 간 중복 트리플은 RDF 집합 의미상 무해.

공모펀드 단순화: 동일 itm_no 그룹에서 달라지는 컬럼은 prfd_attr_cd 뿐임이 검증되어
  있으므로(8/5 dev-kyung, memory.md) 마스터(itm_no) 단위 1노드로 적재하고
  fp:shareClassCount 로 클래스 행 수를 보존한다.

구조 주의: 테스트(tests/test_kg.py)가 순수 함수를 import 한다 — import 시점 부작용 금지.

실행 : python kg/build_kg.py [--tables kr_etf,global_etf] [--limit N] [--out DIR]
근거 : ontology/*.ttl · ROADMAP.md §4(아키텍처)·§7.2 · kg/KG_METHOD.md
"""
import argparse
import io
import csv
import json
import os
import re
import sys
from urllib.parse import quote

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))            # kg/
ROOT = os.path.dirname(HERE)                                 # repo 루트
PROCESSED = os.path.join(ROOT, "preprocessing", "processed")
OUT_DEFAULT = os.path.join(HERE, "output")

AS_OF = "2026-08-22"  # 데이터 스냅샷 기준일(국내 영업일) — preprocess.py 와 동일 (8/26 재배포본)

# --- 네임스페이스 (ontology/common.ttl 과 일치해야 한다 — kg_store.py 와 같은 값) ----
FP = "http://mafest.ai/product#"          # 스키마 — 공식 예시 접두어 fp: (8/19 채택)
FPR = "http://mafest.ai/resource/"        # 인스턴스
ONTOLOGY_FILES = ("ontology/common.ttl", "ontology/bond_kr.ttl", "ontology/etf_kr.ttl",
                  "ontology/etf_gl.ttl", "ontology/fund_pub.ttl")
RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
RDFS_LABEL = "<http://www.w3.org/2000/01/rdf-schema#label>"
SKOS_ALT = "<http://www.w3.org/2004/02/skos/core#altLabel>"   # 별칭 이름표 (8/22, KG_NEXT 1순위)
XSD = "http://www.w3.org/2001/XMLSchema#"

ALIAS_DICT_CSV = os.path.join(ROOT, "external_data", "dictionaries", "alias_dictionary.csv")
CONSTITUENT_ALIAS_CSV = os.path.join(ROOT, "external_data", "dictionaries", "constituent_aliases.csv")

# 처리 대상: (슬러그, CSV 파일명, 테이블ID, 상품군 표기)
TABLES = {
    "kr_bond":     ("PRBD01N001_kr_bond_processed.csv",     "PRBD01N001", "국내채권"),
    "kr_etf":      ("PREF01N001_kr_etf_processed.csv",      "PREF01N001", "국내ETF"),
    "global_etf":  ("PREF02N001_global_etf_processed.csv",  "PREF02N001", "해외ETF"),
    "public_fund": ("PRFD01N001_public_fund_processed.csv", "PRFD01N001", "공모펀드"),
}

# 구성종목(KRX 수집분) — 마스터 4종과 소스·기준일이 달라 TABLES 밖에서 별도 처리한다.
# 기준일 주의: 마스터는 7/11 스냅샷, 구성종목은 7/10(직전 거래일) 조회분이다.
CONSTITUENTS_SLUG = "constituents"
CONSTITUENTS_CSV_DEFAULT = os.path.join(
    ROOT, "external_data", "constituents", "constituents_20260821.csv")
CONSTITUENTS_AS_OF = "2026-08-21"
CONSTITUENTS_SOURCE = "KRX 정보데이터시스템 ETF PDF(구성종목)"

# membership 대상 국내 증권군(SECUGRP_ID) — 통상 "종목"으로 질의되는 상장 증권.
# ST=주식 · DR=예탁증권(코오롱티슈진) · RT=리츠 · IF=인프라(맥쿼리인프라) · MF=상장펀드.
# 8/13 실측 분포: ST 20,905 / RT 137 / IF 19 / DR 2 / MF 1. 제외 = BN(채권)·EF/EN(ETF·ETN
# 편입분 — 회사가 아니라 상품)·FU/OP(파생)·현금성(KRD/KRZ 코드의 CP·CD·예금·스왑).
DOMESTIC_STOCKLIKE_SECUGRP = frozenset({"ST", "DR", "RT", "IF", "MF"})
# 해외 상장 주식: SECUGRP_ID 없음 + ISIN 형식 + 비KR 접두 (8/13 실측 ~25,400행 —
# 미국·중국·일본 등. 중-2 유형 "캠브리콘 편입 ETF"의 답이 여기서 나온다).
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# 현금성 센티널 방어(8/13 발견): CASH00000001(설정현금액)·USDZZ/JPYZZ0000001(외화
# 현금·예금)이 ISIN 형식을 통과해 가짜 회사 노드가 되는 것을 이름·코드로 차단한다.
_CASH_NAME_RE = re.compile(r"현금|예금|설정현금액")


def _is_cash_sentinel(code, name):
    """현금성 편입 행 여부 — 회사 노드로 적재하면 안 되는 유사 ISIN 코드."""
    return bool(code.startswith("CASH") or _CASH_NAME_RE.search(name or ""))

_DECIMAL_RE = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_COMPACT_RE = re.compile(r"^\d{8}$")

# 해외ETF 기초지수 센티널 — 전처리에서 NULL 처리되지만 방어적으로 재차 차단
INDEX_SENTINEL_SUBSTRINGS = ("Index is not provided", "Index is not available")


# ---------------------------------------------------------------------------
# N-Triples 직렬화 순수 함수
# ---------------------------------------------------------------------------

def esc(text):
    """N-Triples 리터럴 이스케이프. 한글 등 비ASCII는 UTF-8 그대로 둔다(N-Triples 1.1)."""
    return (text.replace("\\", "\\\\").replace('"', '\\"')
                .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def lit(text):
    """평문 리터럴 항."""
    return '"' + esc(text) + '"'


def lit_typed(lexical, xsd_local):
    """타입 리터럴 항 (xsd:decimal 등). lexical 은 이미 검증된 문자열."""
    return '"' + esc(lexical) + '"^^<' + XSD + xsd_local + ">"


def slug(text):
    """원시 문자열 → URI 경로 조각 (퍼센트 인코딩, 앞뒤 공백 제거)."""
    return quote(text.strip(), safe="")


def fp_term(local):
    """스키마 항 (클래스·프로퍼티) IRI — fp:{local}. local 은 온톨로지 5파일에 선언된 이름."""
    return "<" + FP + local + ">"


def res(kind, key):
    """인스턴스 IRI — fpr:{kind}/{key}. key 는 원시 문자열(내부에서 슬러그화)."""
    return "<" + FPR + kind + "/" + slug(key) + ">"


def sv(row, col):
    """행 dict 에서 문자열 값을 꺼낸다. 결측(NaN/None/공백)은 None."""
    v = row.get(col)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def as_decimal(s):
    """xsd:decimal lexical 검증 — 통과 시 원문 보존, 실패 시 None (지수 표기 등 거부)."""
    return s if (s is not None and _DECIMAL_RE.match(s)) else None


def as_int(s):
    return s if (s is not None and _INT_RE.match(s)) else None


def as_date(s):
    """ISO(YYYY-MM-DD) 또는 YYYYMMDD → ISO. 9999 시작(영구·미도래 센티널)은 None."""
    if s is None:
        return None
    if _DATE_ISO_RE.match(s):
        return None if s.startswith("9999") else s
    if _DATE_COMPACT_RE.match(s):
        return None if s.startswith("9999") else f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def as_bool(s):
    """Y/N·True/False·1/0 → xsd:boolean lexical. 그 외 None."""
    if s is None:
        return None
    u = s.strip().upper()
    if u in ("Y", "TRUE", "1"):
        return "true"
    if u in ("N", "FALSE", "0"):
        return "false"
    return None


def as_sale_flag(s):
    """공모펀드 sale_yn → xsd:boolean lexical.

    실제 값은 Y/N 이 아니라 '판매중'/'판매완료'다(8/18 채점기가 발견 — 그 전에는 이 속성이
    조용히 한 건도 생성되지 않았다). Y/N 표기도 함께 받는다.
    """
    if s is None:
        return None
    u = s.strip()
    if u == "판매중":
        return "true"
    if u == "판매완료":
        return "false"
    return as_bool(u)


def valid_risk_grade(s, violations):
    """위험등급 범위 제약(1~6, 데이터 실측 우선 — ROADMAP §8.4).

    범위 밖 값은 None 을 돌려주고 violations 카운터에 기록한다 — 온톨로지
    fp:riskGrade 제약(common.ttl · shapes.ttl fp:ProductShape)의 코드 구현
    ("99등급"이라고 답하느니 없다고 답한다).
    """
    v = as_int(s)
    if v is None:
        return None
    if 1 <= int(v) <= 6:
        return v
    violations[s] = violations.get(s, 0) + 1
    return None


# ---------------------------------------------------------------------------
# 트리플 방출기
# ---------------------------------------------------------------------------
# 별칭(altLabel) 재료 — 사전 파일 → 노드별 별칭 목록 (8/22, kg/KG_NEXT.md 1순위)
# 원칙: 노드를 병합하지 않는다("삼성"≠"삼성액티브" — 별개 법인). 이름표만 추가한다.
# ---------------------------------------------------------------------------

def _read_alias_rows(path=ALIAS_DICT_CSV):
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _norm_alias_text(s):
    return re.sub(r"\s+", "", str(s or "")).casefold()


def company_alias_map(mgmt_values, market, alias_rows=None):
    """운용사 원시 표기 → 별칭 목록.

    국내: 별칭 사전 '국내ETF브랜드'의 한글명("{운용사명} {브랜드}")에서 정식 운용사명을
    뽑아, 그 이름의 접두어인 원시 표기 중 **가장 긴 것 하나**에만 붙인다
    (정식명 '삼성액티브자산운용'은 '삼성'이 아니라 '삼성액티브'에 붙는다 — 오귀속 방지).
    해외: '해외운용사' 항목의 키가 원시 표기의 단어로 나타나면 한글 별칭들을 붙인다
    (짧은 키는 단어 단위로만 대조 — 'ARK' 가 다른 단어 속 문자열로 잡히지 않게).
    """
    rows = alias_rows if alias_rows is not None else _read_alias_rows()
    raws = sorted({str(m).strip() for m in mgmt_values if m and str(m).strip()},
                  key=len, reverse=True)
    out = {}
    if market == "domestic":
        for r in rows:
            if not (r.get("분류") or "").endswith("국내ETF브랜드"):
                continue
            key, han = (r.get("키") or "").strip(), (r.get("한글명") or "").strip()
            if han.endswith(" " + key) and key:
                formal = han[: -len(key) - 1].strip()
            elif " " not in han and "운용" in han:
                formal = han                        # "에셋플러스자산운용"처럼 브랜드=사명
            else:
                continue                            # 구 브랜드 설명행 등 — 형식 밖은 제외
            for raw in raws:                        # 긴 표기부터 — 첫 접두 일치가 가장 긴 것
                if formal != raw and formal.startswith(raw):
                    out.setdefault(raw, []).append(formal)
                    break
    else:
        for r in rows:
            if not (r.get("분류") or "").endswith("해외운용사"):
                continue
            key = (r.get("키") or "").strip()
            if not key:
                continue
            alts = [(r.get("한글명") or "").strip()]
            alts += [a.strip() for a in (r.get("동의어") or "").split(";")]
            kl = key.casefold()
            for raw in raws:
                rl = raw.casefold()
                tokens = re.split(r"[^0-9a-z가-힣&]+", rl)
                if kl in tokens or rl.startswith(kl) or (len(kl) >= 6 and kl in rl):
                    out.setdefault(raw, []).extend([key] + alts)
    return {k: [a for a in dict.fromkeys(v) if a and a != k] for k, v in out.items()}


def index_alias_map(index_values, alias_rows=None):
    """지수 원시 표기 → 통칭 별칭 목록 — 정규화 동등('KOSPI200'=='KOSPI 200')일 때만 붙인다.

    부분일치를 쓰지 않는 이유: '나스닥' 통칭 항목은 나스닥100 지수를 가리키므로,
    '나스닥 종합' 같은 다른 지수에 잘못 붙으면 안 된다.
    """
    rows = [r for r in (alias_rows if alias_rows is not None else _read_alias_rows())
            if (r.get("분류") or "").endswith("지수통칭")]
    out = {}
    for idx in {str(v).strip() for v in index_values if v and str(v).strip()}:
        nn = _norm_alias_text(idx)
        for r in rows:
            syns = [a.strip() for a in (r.get("동의어") or "").split(";") if a.strip()]
            keys = {_norm_alias_text(r.get("키"))} | {_norm_alias_text(a) for a in syns}
            if nn in keys:
                alts = [(r.get("키") or "").strip()] + syns
                out[idx] = [a for a in dict.fromkeys(alts) if a and _norm_alias_text(a) != nn]
    return out


def constituent_alias_map(path=CONSTITUENT_ALIAS_CSV):
    """구성종목 별칭 사전 → {ISIN: [한글 별칭…]} — 해외 종목 한글명("캠브리콘")용."""
    out = {}
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            isin, alias = (r.get("isin") or "").strip(), (r.get("alias") or "").strip()
            if isin and alias:
                out.setdefault(isin, []).append(alias)
    return out


class TableEmitter:
    """테이블 1개 분량의 트리플을 모아 정렬 없이 순서대로 쓴다(입력 행 순서 = 출력 순서).

    파일 자기완결성을 위해 회사·지수 노드 트리플을 같은 파일에 중복 없이 포함한다.
    """

    def __init__(self, fh):
        self.fh = fh
        self.count = 0
        self._seen_aux = set()      # (kind, key) — 이 파일에 이미 쓴 회사·지수 노드
        self._seen_labels = set()   # (kind, key, label) — 노드별 이미 쓴 라벨(이름 변형 지원)

    def t(self, s, p, o):
        if o is None:
            return
        self.fh.write(f"{s} {p} {o} .\n")
        self.count += 1

    def aux_node(self, kind, key, cls_local, label=None, code=None, ticker=None, isin=None):
        """회사·지수 등 참조 노드 — 파일당 1회만 타입·라벨을 쓴다. IRI 를 돌려준다."""
        iri = res(kind, key)
        k = (kind, key)
        if k not in self._seen_aux:
            self._seen_aux.add(k)
            self.t(iri, RDF_TYPE, fp_term(cls_local))
            if label is not None:
                self._seen_labels.add((kind, key, label))
                self.t(iri, RDFS_LABEL, lit(label))
            if code is not None:
                self.t(iri, fp_term("companyCode"), lit(code))
            if ticker is not None:
                self.t(iri, fp_term("tickerCode"), lit(ticker))
            if isin is not None:
                self.t(iri, fp_term("securityIsin"), lit(isin))
        return iri

    def aux_label(self, kind, key, label):
        """기존 참조 노드에 이름 변형을 추가 라벨로 쓴다(중복 없이) — 해외 종목은
        ETF 운용사마다 표기가 달라(예: 'CAMBRICON TECHNOLOGIES-A' vs 'Cambricon
        Technologies Corp Ltd') 변형을 전부 라벨로 보존해야 이름 검색이 성립한다."""
        k = (kind, key, label)
        if k not in self._seen_labels:
            self._seen_labels.add(k)
            self.t(res(kind, key), RDFS_LABEL, lit(label))

    def aux_alt_label(self, kind, key, alt):
        """별칭 이름표(skos:altLabel) — 정식 라벨(rdfs:label)과 겹치면 쓰지 않는다.

        8/22(KG_NEXT 1순위): 노드 병합 대신 별칭을 선언해 두고 검색이 정식명+별칭을
        한 색인으로 본다. 같은 별칭이 여러 노드에 걸리면 검색 계층이 합집합으로 답한다.
        """
        if not alt or (kind, key, alt) in self._seen_labels:
            return
        k = (kind, key, "alt␟" + alt)          # rdfs 라벨 키와 구분되는 별칭 키
        if k not in self._seen_labels:
            self._seen_labels.add(k)
            self.t(res(kind, key), SKOS_ALT, lit(alt))
            self.alt_count = getattr(self, "alt_count", 0) + 1


def emit_common(em, s_iri, row, table_id, group_label, name_col, short_col, id_col):
    """4종 공통 속성 — 이름·키·출처(근거 표시 지원)."""
    em.t(s_iri, RDFS_LABEL, lit(sv(row, name_col)) if sv(row, name_col) else None)
    if short_col and sv(row, short_col):
        em.t(s_iri, fp_term("shortName"), lit(sv(row, short_col)))
    em.t(s_iri, fp_term("productId"), lit(sv(row, id_col)))
    em.t(s_iri, fp_term("sourceTable"), lit(table_id))
    em.t(s_iri, fp_term("productGroup"), lit(group_label))


# ---------------------------------------------------------------------------
# 테이블별 추출기 — row(dict) 를 받아 트리플을 방출한다
# ---------------------------------------------------------------------------

def extract_bond_row(em, row, stats):
    pid = sv(row, "PD_NO")
    if pid is None:
        return
    s = res("bond", pid)
    em.t(s, RDF_TYPE, fp_term("Bond"))
    emit_common(em, s, row, "PRBD01N001", "국내채권", "PD_NM", "PD_ABRV_NM", "PD_NO")

    issuer = sv(row, "PD_PBCM")
    if issuer:
        em.t(s, fp_term("issuedBy"), em.aux_node("company", issuer, "Issuer", label=issuer))

    em.t(s, fp_term("bondClass"), lit(sv(row, "STD_PD_MCLS_NM")) if sv(row, "STD_PD_MCLS_NM") else None)
    em.t(s, fp_term("bondKind"), lit(sv(row, "BD_KND")) if sv(row, "BD_KND") else None)
    curr = sv(row, "CURR_CD")
    if curr and curr != "000":  # '000' = 통화 미지정 센티널 (column_dictionary)
        em.t(s, fp_term("currency"), lit(curr))

    d = as_date(sv(row, "ISU_DT"))
    em.t(s, fp_term("issueDate"), lit_typed(d, "date") if d else None)
    perpetual = as_bool(sv(row, "drv_is_perpetual"))
    if perpetual == "true":
        em.t(s, fp_term("isPerpetual"), lit_typed("true", "boolean"))
    else:
        d = as_date(sv(row, "MAT_DT"))
        em.t(s, fp_term("maturityDate"), lit_typed(d, "date") if d else None)
    em.t(s, fp_term("maturityStatus"), lit(sv(row, "drv_maturity_status")) if sv(row, "drv_maturity_status") else None)
    b = as_bool(sv(row, "drv_is_buyable"))
    em.t(s, fp_term("isBuyable"), lit_typed(b, "boolean") if b else None)

    c = as_decimal(sv(row, "SRFC_IRT"))
    em.t(s, fp_term("couponRate"), lit_typed(c, "decimal") if c else None)
    em.t(s, fp_term("creditRating"), lit(sv(row, "drv_crd_grd_norm")) if sv(row, "drv_crd_grd_norm") else None)
    r = as_int(sv(row, "drv_crd_grd_rank"))
    em.t(s, fp_term("creditRatingRank"), lit_typed(r, "integer") if r else None)
    g = valid_risk_grade(sv(row, "drv_risk_grade"), stats["risk_grade_dropped"])
    em.t(s, fp_term("riskGrade"), lit_typed(g, "integer") if g else None)
    stats["product_nodes"] += 1


# drv_instrument_type × 상장 시장 → 가장 구체적인 온톨로지 클래스 (etf_kr.ttl · etf_gl.ttl)
ETP_CLASS = {
    ("domestic", "ETF"): "DomesticETF", ("domestic", "ETN"): "DomesticETN",
    ("foreign", "ETF"): "ForeignETF",   ("foreign", "ETN"): "ForeignETN",
}


def _etp_class(row, stats, market="domestic"):
    """drv_instrument_type → 온톨로지 클래스(국내/해외 × ETF/ETN).

    ETF/ETN 이외(불명)는 상위클래스 ExchangeTradedProduct 로만 타이핑한다 — ETF 로
    단정하지 않는다(disjoint 보호). 상품군은 fp:productGroup 리터럴로 따로 남는다.
    """
    t = sv(row, "drv_instrument_type")
    cls = ETP_CLASS.get((market, t))
    if cls:
        return cls
    stats["instrument_type_unresolved"] += 1
    return "ExchangeTradedProduct"


def _emit_etp_shared(em, s, row, cls, stats):
    """국내·해외 ETP 공통 속성 (common.ttl 의 ETP 도메인 속성)."""
    mgmt = sv(row, "cu_fund_mgmt_co")
    if mgmt:
        em.t(s, fp_term("managedBy"), em.aux_node("company", mgmt, "ManagementCompany", label=mgmt))
        for alt in getattr(em, "company_aliases", {}).get(mgmt, ()):
            em.aux_alt_label("company", mgmt, alt)
    idx = sv(row, "cu_base_index")
    if idx and not any(t in idx for t in INDEX_SENTINEL_SUBSTRINGS):
        em.t(s, fp_term("tracksIndex"), em.aux_node("index", idx, "Index", label=idx))
        for alt in getattr(em, "index_aliases", {}).get(idx, ()):
            em.aux_alt_label("index", idx, alt)
    e = as_decimal(sv(row, "cu_charge_rt"))
    em.t(s, fp_term("expenseRatio"), lit_typed(e, "decimal") if e else None)
    if not cls.endswith("ETN"):  # ETN 의 du_last_aum 은 전량 0 실측 — 규모는 netAssets 로
        a = as_decimal(sv(row, "du_last_aum"))
        em.t(s, fp_term("aum"), lit_typed(a, "decimal") if a else None)
    em.t(s, fp_term("assetClass"), lit(sv(row, "wu_inv_ast_type")) if sv(row, "wu_inv_ast_type") else None)
    em.t(s, fp_term("region"), lit(sv(row, "wu_inv_rgn")) if sv(row, "wu_inv_rgn") else None)
    d = as_date(sv(row, "pd_lstg_dt"))
    em.t(s, fp_term("listedDate"), lit_typed(d, "date") if d else None)


def extract_kr_etf_row(em, row, stats):
    pid = sv(row, "pd_itm_no")
    if pid is None:
        return
    s = res("kr-etf", pid)
    cls = _etp_class(row, stats, market="domestic")    # DomesticETF / DomesticETN (etf_kr.ttl)
    em.t(s, RDF_TYPE, fp_term(cls))
    emit_common(em, s, row, "PREF01N001", "국내ETF", "pd_nm", "pd_abrv_nm", "pd_itm_no")
    _emit_etp_shared(em, s, row, cls, stats)

    n = as_decimal(sv(row, "pd_net_tamt"))
    em.t(s, fp_term("netAssets"), lit_typed(n, "decimal") if n else None)
    em.t(s, fp_term("currency"), lit(sv(row, "drv_curr_cd")) if sv(row, "drv_curr_cd") else None)
    g = valid_risk_grade(sv(row, "drv_risk_grade"), stats["risk_grade_dropped"])
    em.t(s, fp_term("riskGrade"), lit_typed(g, "integer") if g else None)
    status = sv(row, "drv_listing_status")
    em.t(s, fp_term("listingStatus"), lit(status) if status else None)
    if status == "delisted":
        d = as_date(sv(row, "pd_lste_dt"))
        em.t(s, fp_term("delistedDate"), lit_typed(d, "date") if d else None)
    r1 = as_decimal(sv(row, "du_er_1y"))
    em.t(s, fp_term("return1y"), lit_typed(r1, "decimal") if r1 else None)
    ry = as_decimal(sv(row, "du_er_ytd"))
    em.t(s, fp_term("returnYtd"), lit_typed(ry, "decimal") if ry else None)
    stats["product_nodes"] += 1


def extract_global_etf_row(em, row, stats):
    pid = sv(row, "pd_itm_no")
    if pid is None:
        return
    s = res("global-etf", pid)
    cls = _etp_class(row, stats, market="foreign")     # ForeignETF / ForeignETN (etf_gl.ttl)
    em.t(s, RDF_TYPE, fp_term(cls))
    emit_common(em, s, row, "PREF02N001", "해외ETF", "pd_nm", "pd_abrv_nm", "pd_itm_no")
    _emit_etp_shared(em, s, row, cls, stats)

    isin = sv(row, "pd_isin_cd")
    em.t(s, fp_term("isin"), lit(isin) if isin else None)  # 키 아님 — 중복·공백 실측 (§5)
    em.t(s, fp_term("tradingCurrency"), lit(sv(row, "pd_trd_ccy")) if sv(row, "pd_trd_ccy") else None)
    inv = as_bool(sv(row, "drv_is_inverse"))
    if inv == "true":
        em.t(s, fp_term("isInverse"), lit_typed("true", "boolean"))
    inc = as_bool(sv(row, "drv_incomplete_core"))
    if inc == "true":
        em.t(s, fp_term("isIncompleteRecord"), lit_typed("true", "boolean"))
    # 해외ETF 는 위험등급 원천 컬럼이 없다 — riskGrade 트리플 없음 = "확인할 수 없음" 근거
    stats["product_nodes"] += 1


def extract_fund_master_row(em, row, stats, share_class_count):
    pid = sv(row, "itm_no")
    if pid is None:
        return
    s = res("fund", pid)
    em.t(s, RDF_TYPE, fp_term("PublicFund"))
    emit_common(em, s, row, "PRFD01N001", "공모펀드", "itm_nm", "itm_abrv_nm", "itm_no")

    code = sv(row, "or_co_xtn_itt_cd")
    if code:
        # 운용사 명칭이 원천에 없다 — 코드 노드로 적재, 명칭 해석은 entity resolution 후속
        em.t(s, fp_term("managedBy"), em.aux_node("company", "code-" + code, "ManagementCompany", code=code))
        em.t(s, fp_term("managementCompanyCode"), lit(code))
    bmk = sv(row, "bmrk_nm")
    if bmk:
        em.t(s, fp_term("hasBenchmark"), em.aux_node("index", bmk, "Index", label=bmk))

    em.t(s, fp_term("fundAttribute"), lit(sv(row, "or_attr_desc")) if sv(row, "or_attr_desc") else None)
    g = valid_risk_grade(sv(row, "drv_risk_grade"), stats["risk_grade_dropped"])
    em.t(s, fp_term("riskGrade"), lit_typed(g, "integer") if g else None)
    em.t(s, fp_term("region"), lit(sv(row, "fd_ivst_rgn_desc")) if sv(row, "fd_ivst_rgn_desc") else None)
    em.t(s, fp_term("currency"), lit(sv(row, "curr_cd")) if sv(row, "curr_cd") else None)
    n = as_decimal(sv(row, "fd_nast_suma"))
    em.t(s, fp_term("netAssets"), lit_typed(n, "decimal") if n else None)
    r1 = as_decimal(sv(row, "fd_yr1_ern_r"))
    em.t(s, fp_term("return1y"), lit_typed(r1, "decimal") if r1 else None)
    b = as_sale_flag(sv(row, "sale_yn"))
    em.t(s, fp_term("isOnSale"), lit_typed(b, "boolean") if b else None)
    em.t(s, fp_term("shareClassCount"), lit_typed(str(share_class_count), "integer"))
    stats["product_nodes"] += 1


def extract_constituent_row(em, row, stats, seen_etf):
    """구성종목 수집 CSV 1행 → membership 트리플 (etf_kr.ttl §3, CQ6).

    fp:ListedCompany 적재 대상 (8/13 실측 기반 — KG_METHOD.md 구성종목 절):
      ① 국내 상장 증권(SECUGRP_ID ∈ ST·DR·RT·IF·MF) — 키 krx-{6자리 코드}
      ② 해외 상장 주식(SECUGRP_ID 없음 + 비KR ISIN) — 키 isin-{ISIN}. 운용사별
         이름 표기가 달라 변형을 전부 rdfs:label 로 보존한다(이름 검색 성립 조건).
    제외(수집 CSV 에만 보존): 현금성(원화현금·CP·CD·스왑)·채권(BN)·파생(FU·OP)·
    ETF/ETN 편입분(EF·EN — 회사가 아니라 상품). 비중·수량 등 수치도 CSV 소관 —
    그래프는 관계(membership)만 가진다(채널 역할 분담).
    """
    etf_isin = sv(row, "etf_isin")
    if etf_isin is None:
        return
    code = sv(row, "COMPST_ISU_CD")
    name = sv(row, "COMPST_ISU_NM")
    if code is None or name is None:           # 코드·이름 없는 행 — 식별 불가
        stats["skipped_no_code"] += 1
        return
    secugrp = sv(row, "SECUGRP_ID")
    if secugrp in DOMESTIC_STOCKLIKE_SECUGRP:
        key, kwargs = "krx-" + code, {"ticker": code}
        stats["edges_domestic"] += 1
    elif (secugrp is None and _ISIN_RE.match(code) and not code.startswith("KR")
          and not _is_cash_sentinel(code, name)):
        key, kwargs = "isin-" + code, {"isin": code}
        stats["edges_foreign"] += 1
    else:                                      # 비주식·현금성·파생·상품 편입분
        skip_key = secugrp or "(현금성·기타)"
        stats["skipped_non_stock"][skip_key] = stats["skipped_non_stock"].get(skip_key, 0) + 1
        return
    s = res("kr-etf", etf_isin)                # 국내ETF 마스터(kr_etf.nt)와 같은 IRI — 조인 지점
    if etf_isin not in seen_etf:
        seen_etf.add(etf_isin)
        etf_name = sv(row, "etf_name")
        if etf_name:                           # 파일 자기완결성 — ETF 라벨을 1회 포함
            em.t(s, RDFS_LABEL, lit(etf_name))
        stats["etf_nodes"] += 1
    company = em.aux_node("company", key, "ListedCompany", label=name, **kwargs)
    em.aux_label("company", key, name)         # 이름 변형 추가 라벨(중복이면 no-op)
    for alt in getattr(em, "constituent_aliases", {}).get(code, ()):  # 해외 종목 한글명(8/22)
        em.aux_alt_label("company", key, alt)
    em.t(s, fp_term("holdsConstituent"), company)
    stats["edges"] += 1


# ---------------------------------------------------------------------------
# 테이블 빌드
# ---------------------------------------------------------------------------

def new_stats(rows_in):
    return {"rows_in": rows_in, "product_nodes": 0, "triples": 0,
            "aux_nodes": 0, "risk_grade_dropped": {}, "instrument_type_unresolved": 0}


def new_constituents_stats(rows_in):
    return {"rows_in": rows_in, "etf_nodes": 0, "edges": 0,
            "edges_domestic": 0, "edges_foreign": 0, "companies": 0,
            "skipped_no_code": 0, "skipped_non_stock": {}, "triples": 0}


def build_constituents(df, out_dir, source_csv):
    """구성종목 수집 CSV → {out_dir}/constituents.nt + 통계 dict."""
    stats = new_constituents_stats(len(df))
    path = os.path.join(out_dir, CONSTITUENTS_SLUG + ".nt")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        em = TableEmitter(fh)
        em.constituent_aliases = constituent_alias_map()   # 해외 종목 한글명 이름표(8/22)
        seen_etf = set()
        for row in df.to_dict("records"):
            extract_constituent_row(em, row, stats, seen_etf)
        stats["triples"] = em.count
        stats["companies"] = len(em._seen_aux)
        stats["alt_labels"] = getattr(em, "alt_count", 0)
    stats["as_of"] = CONSTITUENTS_AS_OF
    stats["source"] = CONSTITUENTS_SOURCE
    stats["source_csv"] = os.path.basename(source_csv)
    return stats


def build_table(slug_name, df, out_dir):
    """전처리 DataFrame 1개 → {out_dir}/{slug}.nt + 통계 dict."""
    stats = new_stats(len(df))
    path = os.path.join(out_dir, slug_name + ".nt")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        em = TableEmitter(fh)
        if slug_name == "public_fund":
            # 마스터(상품) 단위 적재 — 8/27 재배포본은 1행=1클래스(itm_no 고유)이고
            # 상품 묶음 키는 fss_itm_no(결측이면 행 자체가 상품). 대표 클래스는
            # 순자산(fd_nast_suma) 최대, 동률이면 itm_no 사전순 — fund_master(DuckDB)와
            # 같은 규칙이라 두 채널의 상품 수·대표가 일치한다.
            grp = df["fss_itm_no"].fillna(df["itm_no"]) if "fss_itm_no" in df.columns else df["itm_no"]
            nast = pd.to_numeric(df.get("fd_nast_suma"), errors="coerce")
            ordered = df.assign(_grp=grp, _nast=nast).sort_values(
                ["_grp", "_nast", "itm_no"], ascending=[True, False, True],
                na_position="last", kind="stable")
            sizes = ordered.groupby("_grp", sort=False).size().to_dict()
            master = ordered.drop_duplicates(subset=["_grp"], keep="first")
            for row in master.to_dict("records"):
                extract_fund_master_row(em, row, stats, sizes.get(row.get("_grp"), 1))
        else:
            extractor = {"kr_bond": extract_bond_row,
                         "kr_etf": extract_kr_etf_row,
                         "global_etf": extract_global_etf_row}[slug_name]
            if slug_name in ("kr_etf", "global_etf"):    # 별칭 이름표 재료(8/22, KG_NEXT 1순위)
                market = "domestic" if slug_name == "kr_etf" else "foreign"
                em.company_aliases = company_alias_map(df.get("cu_fund_mgmt_co", pd.Series(dtype=str)).dropna(), market)
                em.index_aliases = index_alias_map(df.get("cu_base_index", pd.Series(dtype=str)).dropna())
            for row in df.to_dict("records"):
                extractor(em, row, stats)
        stats["triples"] = em.count
        stats["aux_nodes"] = len(em._seen_aux)
        stats["alt_labels"] = getattr(em, "alt_count", 0)
    return stats


def load_table(slug_name, limit=None):
    fname, _tid, _grp = TABLES[slug_name]
    df = pd.read_csv(os.path.join(PROCESSED, fname), dtype=str)
    if limit:
        df = df.head(limit)
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description="전처리 CSV 4종(+구성종목) → KG N-Triples")
    ap.add_argument("--tables", default=",".join(list(TABLES) + [CONSTITUENTS_SLUG]),
                    help="쉼표 구분 대상 테이블 (기본: 마스터 4종 + constituents)")
    ap.add_argument("--limit", type=int, default=None, help="테이블당 처리 행수 제한 (스모크용)")
    ap.add_argument("--out", default=OUT_DEFAULT, help="출력 폴더 (기본: kg/output)")
    ap.add_argument("--constituents-csv", default=CONSTITUENTS_CSV_DEFAULT,
                    help="구성종목 통합 CSV 경로 (수집기 merge 산출물)")
    args = ap.parse_args(argv)

    targets = [t.strip() for t in args.tables.split(",") if t.strip()]
    known = list(TABLES) + [CONSTITUENTS_SLUG]
    unknown = [t for t in targets if t not in known]
    if unknown:
        sys.exit(f"알 수 없는 테이블: {unknown} (가능: {known})")
    if not os.path.isdir(PROCESSED) and any(t in TABLES for t in targets):
        sys.exit(f"전처리 산출물이 없다: {PROCESSED} — 먼저 python preprocessing/preprocess.py 실행")

    os.makedirs(args.out, exist_ok=True)
    report = {"as_of": AS_OF, "ontology": list(ONTOLOGY_FILES), "schema_namespace": FP,
              "note": "결측은 트리플 미생성. risk_grade_dropped = 범위(1~6) 밖 값 적재 거부 집계. "
                      "constituents 는 KRX 수집분(기준일 2026-08-21)이며 부분 수집일 수 있다(etf_nodes 로 확인).",
              "tables": {}}
    for t in targets:
        if t == CONSTITUENTS_SLUG:
            if not os.path.exists(args.constituents_csv):
                print(f"[{t}] 수집 CSV 없음({args.constituents_csv}) — 건너뜀 (수집 후 재실행)")
                continue
            df = pd.read_csv(args.constituents_csv, dtype=str)
            if args.limit:
                df = df.head(args.limit)
            stats = build_constituents(df, args.out, args.constituents_csv)
            report["tables"][t] = stats
            print(f"[{t}] rows={stats['rows_in']:,} → etf_nodes={stats['etf_nodes']:,} "
                  f"companies={stats['companies']:,} edges={stats['edges']:,} "
                  f"(국내 {stats['edges_domestic']:,} · 해외 {stats['edges_foreign']:,}) "
                  f"triples={stats['triples']:,} skipped_no_code={stats['skipped_no_code']:,} "
                  f"skipped_non_stock={sum(stats['skipped_non_stock'].values()):,}")
            continue
        df = load_table(t, args.limit)
        stats = build_table(t, df, args.out)
        report["tables"][t] = stats
        print(f"[{t}] rows={stats['rows_in']:,} → product_nodes={stats['product_nodes']:,} "
              f"aux_nodes={stats['aux_nodes']:,} triples={stats['triples']:,} "
              f"risk_dropped={sum(stats['risk_grade_dropped'].values())} "
              f"type_unresolved={stats['instrument_type_unresolved']}")

    report["totals"] = {
        "product_nodes": sum(s.get("product_nodes", 0) for s in report["tables"].values()),
        "triples": sum(s["triples"] for s in report["tables"].values()),
    }
    rpath = os.path.join(args.out, "build_report.json")
    with io.open(rpath, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"완료 → {args.out} (리포트: {rpath})")


if __name__ == "__main__":
    main()
