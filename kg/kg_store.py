# -*- coding: utf-8 -*-
"""
KG 경량 트리플 스토어 — build_kg.py 산출 N-Triples 를 읽어 인덱스 질의를 제공한다.

무엇: (s,p,o) 3중 인덱스(spo/pos)와 상품명 검색을 갖춘 최소 인메모리 스토어.
왜  : 그래프 저장소 제품(rdflib/Neo4j/AGE)은 8/8 총검토에서 선정한다(ROADMAP §8.1).
      그 결정과 무관하게 "그래프에서 답할 수 있다"(S1 DoD — CQ1 'TIGER 200의 운용사는?')
      를 지금 검증하기 위한 저장소 중립 참조 구현이다. 표준 .nt 파일이 원본이므로
      제품 선정 후에는 같은 파일을 그 저장소에 적재하면 된다.

파서 범위: build_kg.py 가 쓰는 N-Triples 부분집합만 지원한다 —
      <iri> <iri> (<iri> | "리터럴" | "리터럴"^^<datatype>) .
      일반 N-Triples 전체(빈 노드·언어 태그 등)의 파서가 아니다.
어휘: ontology/common.ttl + 도메인 파일 4개(bond_kr·etf_kr·etf_gl·fund_pub) — 접두어 fp:.
"""
import io
import os
import re

# --- 네임스페이스 (ontology/common.ttl 과 일치해야 한다 — 8/19 공식 접두어 fp: 채택) ---
FP = "http://mafest.ai/product#"          # 스키마(클래스·속성) — 공식 예시 접두어 fp:
FPR = "http://mafest.ai/resource/"        # 인스턴스(상품·회사·지수 노드)
MF, MFR = FP, FPR                          # 옛 이름(8/11~8/18 코드) 호환용 별칭 — 새 코드는 FP/FPR 사용
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

# 8/18 이전 그래프 파일의 스키마 네임스페이스 — 이 문자열이 보이면 재생성이 필요하다
LEGACY_NS_MARKER = "ai-festival-mirae-asset.github.io/"

# 인스턴스가 실제로 타이핑되는 상품 클래스 IRI(가장 구체적인 클래스만 — build_kg.py 와 일치).
# 상위 클래스(fp:ETF·fp:ETN·fp:Product)는 인스턴스에 직접 붙지 않는다(온톨로지 5파일의
# rdfs:subClassOf 로 추론되는 몫). ExchangeTradedProduct 는 ETF/ETN 판정 불가 행 전용.
PRODUCT_CLASSES = {FP + c for c in
                   ("Bond", "DomesticETF", "DomesticETN", "ForeignETF", "ForeignETN",
                    "ExchangeTradedProduct", "PublicFund")}

_UNESC = {"\\\\": "\\", '\\"': '"', "\\n": "\n", "\\r": "\r", "\\t": "\t"}
_UNESC_RE = re.compile(r"\\[\\\"nrt]")


def _unescape(text):
    return _UNESC_RE.sub(lambda m: _UNESC[m.group(0)], text)


def parse_object(term):
    """오브젝트 항 → (값, 종류) — 종류: 'iri' | 'literal'. 타입 태그는 값에서 제거한다."""
    if term.startswith("<") and term.endswith(">"):
        return term[1:-1], "iri"
    # "..."^^<dt> 또는 "..."
    m = re.match(r'^"(.*)"(?:\^\^<[^>]+>)?$', term, re.S)
    if not m:
        raise ValueError(f"지원하지 않는 오브젝트 항: {term!r}")
    return _unescape(m.group(1)), "literal"


def parse_line(line):
    """N-Triples 한 줄 → (s, p, o값, o종류) 또는 None(빈 줄)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if not line.endswith("."):
        raise ValueError(f"트리플 종결자 없음: {line!r}")
    body = line[:-1].rstrip()
    m = re.match(r"^<([^>]*)>\s+<([^>]*)>\s+(.+)$", body, re.S)
    if not m:
        raise ValueError(f"트리플 파싱 실패: {line!r}")
    s, p, o_term = m.group(1), m.group(2), m.group(3)
    o_val, o_kind = parse_object(o_term)
    return s, p, o_val, o_kind


def norm_name(text):
    """상품명 검색용 정규화 — 공백 제거 + casefold ("TIGER 200" == "tiger200")."""
    return re.sub(r"\s+", "", text).casefold()


class TripleStore:
    """spo/pos 인덱스 + 상품 라벨 검색. 중복 트리플은 집합 의미로 1회만 유지."""

    def __init__(self):
        self._spo = {}      # s -> p -> [o값,...] (입력 순서 보존, 중복 제거)
        self._pos = {}      # p -> o값 -> [s,...]
        self._seen = set()
        self.triples = 0

    # -- 적재 ---------------------------------------------------------------
    def add(self, s, p, o_val):
        key = (s, p, o_val)
        if key in self._seen:
            return
        self._seen.add(key)
        self._spo.setdefault(s, {}).setdefault(p, []).append(o_val)
        self._pos.setdefault(p, {}).setdefault(o_val, []).append(s)
        self.triples += 1

    def load(self, *paths):
        for path in paths:
            with io.open(path, "r", encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    if i == 0 and LEGACY_NS_MARKER in line:
                        # 첫 트리플의 IRI 로 어휘 세대를 판별한다(build_kg 는 노드의 첫 줄에
                        # 항상 인스턴스 IRI 를 쓴다). 옛 어휘 파일을 조용히 적재하면 그래프
                        # 채널이 "상품 없음"으로 오답할 수 있어 즉시 멈춘다.
                        raise ValueError(
                            f"{path}: 옛 어휘(8/18 이전 mf: 네임스페이스)로 만든 그래프 파일 — "
                            "python kg/build_kg.py 로 재생성 필요(온톨로지 fp: 접두어, 8/19)")
                    parsed = parse_line(line)
                    if parsed:
                        s, p, o_val, _kind = parsed
                        self.add(s, p, o_val)
        return self

    @classmethod
    def from_dir(cls, out_dir, tables=None):
        """kg/output 폴더의 .nt 파일들을 적재. tables=슬러그 목록으로 부분 적재 가능."""
        store = cls()
        names = sorted(f for f in os.listdir(out_dir) if f.endswith(".nt"))
        if tables:
            wanted = {t + ".nt" for t in tables}
            names = [f for f in names if f in wanted]
        if not names:
            raise FileNotFoundError(f"{out_dir} 에 적재할 .nt 파일이 없다 — 먼저 python kg/build_kg.py 실행")
        return store.load(*(os.path.join(out_dir, f) for f in names))

    # -- 질의 ---------------------------------------------------------------
    def objects(self, s, p):
        return self._spo.get(s, {}).get(p, [])

    def object(self, s, p):
        vals = self.objects(s, p)
        return vals[0] if vals else None

    def subjects(self, p, o_val):
        return self._pos.get(p, {}).get(o_val, [])

    def properties(self, s):
        return self._spo.get(s, {})

    def types(self, s):
        return self.objects(s, RDF_TYPE)

    def label(self, s):
        return self.object(s, RDFS_LABEL)

    # -- 상품 검색 ----------------------------------------------------------
    def product_subjects(self):
        for cls in sorted(PRODUCT_CLASSES):
            for s in self._pos.get(RDF_TYPE, {}).get(cls, []):
                yield s

    def search_products(self, query, limit=10):
        """이름(라벨·약칭) 부분일치 검색 — 공백·대소문자 무시. (subject, label) 목록."""
        q = norm_name(query)
        hits = []
        for s in self.product_subjects():
            names = self.objects(s, RDFS_LABEL) + self.objects(s, FP + "shortName")
            for n in names:
                if q in norm_name(n):
                    hits.append((s, self.label(s) or n))
                    break
            if len(hits) >= limit:
                break
        return hits
