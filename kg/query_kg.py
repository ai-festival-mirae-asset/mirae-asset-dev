# -*- coding: utf-8 -*-
"""
KG 질의 데모 CLI — S1 DoD 검증용 ("TIGER 200의 운용사는?" 을 그래프에서 답한다).

실행:
  python kg/query_kg.py "TIGER 200"                 # 전체 그래프에서 상품 검색
  python kg/query_kg.py "TIGER 200" --tables kr_etf # 국내ETF 그래프만 적재(빠름)
  python kg/query_kg.py --company "미래에셋"          # 역관계: 이 운용사가 운용하는 상품 (CQ2)
  python kg/query_kg.py --holds "삼성전자"            # 이 종목을 편입한 ETF (CQ6, 8/13)
  python kg/query_kg.py --holds 005930 --tables constituents   # 6자리 코드로도 가능

출력: 상품별 요약(유형·운용사·위험등급·총보수·기초지수)과 근거(출처 테이블·상품번호·기준일).
      근거 표시는 필수 규칙(ROADMAP §3)이므로 데모 단계부터 붙인다.
선행: python kg/build_kg.py (kg/output/*.nt 생성)
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # repo 루트 — `python kg/query_kg.py` 직접 실행 지원

from kg.kg_store import FP, RDFS_LABEL, TripleStore, norm_name  # noqa: E402

AS_OF = "2026-07-11"
CONSTITUENTS_AS_OF = "2026-07-10"  # 구성종목(KRX)은 기준일 직전 거래일 조회분

# 인스턴스에 붙는 클래스(가장 구체적인 것) → 표시명. 온톨로지 5파일(fp:) 기준.
CLASS_KO = {
    FP + "Bond": "채권",
    FP + "DomesticETF": "국내ETF", FP + "DomesticETN": "국내ETN",
    FP + "ForeignETF": "해외ETF", FP + "ForeignETN": "해외ETN",
    FP + "ExchangeTradedProduct": "ETP(유형 불명)", FP + "PublicFund": "공모펀드",
}


def describe_product(store, s):
    """상품 subject 1개 → 사람이 읽는 요약 줄들."""
    lines = [f"· {store.label(s) or s}"]
    types = [CLASS_KO.get(t, t) for t in store.types(s)]
    if types:
        lines.append(f"    유형       : {', '.join(types)}")

    mgmt_iri = store.object(s, FP + "managedBy")
    if mgmt_iri:
        mgmt = store.label(mgmt_iri) or store.object(mgmt_iri, FP + "companyCode")
        suffix = "" if store.label(mgmt_iri) else " (기관코드 — 명칭 해석은 후속)"
        lines.append(f"    운용사     : {mgmt}{suffix}")
    issuer_iri = store.object(s, FP + "issuedBy")
    if issuer_iri:
        lines.append(f"    발행기관   : {store.label(issuer_iri)}")

    simple = [("위험등급", "riskGrade"), ("총보수(%)", "expenseRatio"),
              ("1년 수익률(%)", "return1y"), ("신용등급", "creditRating"),
              ("상장상태", "listingStatus"), ("만기상태", "maturityStatus"),
              ("투자지역", "region"), ("투자자산군", "assetClass")]
    for ko, local in simple:
        v = store.object(s, FP + local)
        if v is not None:
            lines.append(f"    {ko}: {v}")
    idx_iri = store.object(s, FP + "tracksIndex") or store.object(s, FP + "hasBenchmark")
    if idx_iri:
        lines.append(f"    기초지수/벤치마크: {store.label(idx_iri)}")

    table = store.object(s, FP + "sourceTable")
    pid = store.object(s, FP + "productId")
    lines.append(f"    근거       : 테이블 {table} · 상품번호 {pid} · 데이터 기준일 {AS_OF}")
    return lines


def find_company_products(store, query, limit):
    """운용사명 부분일치 → fp:managedBy 역방향으로 상품 나열 (CQ2 — 역관계 질의)."""
    q = norm_name(query)
    out = []
    seen = set()
    for p in (FP + "managedBy", FP + "issuedBy"):
        for company_iri, products in store._pos.get(p, {}).items():
            name = store.label(company_iri)
            if name and q in norm_name(name) and company_iri not in seen:
                seen.add(company_iri)
                out.append((name, p, products[:limit]))
    return out


def find_holding_etfs(store, query, limit):
    """종목명(부분일치)·한글 별칭(사전)·코드/ISIN(정확일치) → 편입 ETF 목록 (CQ6).

    해외 종목은 운용사별 이름 표기가 달라 rdfs:label 이 여러 개다 — 전부 대조한다.
    한글 별칭("캠브리콘")은 constituent_aliases.csv 로 ISIN 에 매핑한다(8/13).
    """
    from pipeline.constituent_aliases import load_aliases, norm_alias
    q = norm_name(query)
    exact = query.strip()
    alias_isins = {isin for isin, _name in load_aliases().get(norm_alias(query), [])}
    holds = store._pos.get(FP + "holdsConstituent", {})
    out = []
    for company_iri, etfs in holds.items():
        names = store.objects(company_iri, RDFS_LABEL)
        ticker = store.object(company_iri, FP + "tickerCode") or ""
        isin = store.object(company_iri, FP + "securityIsin") or ""
        if (any(q in norm_name(n) for n in names)
                or (ticker and exact == ticker) or (isin and exact.upper() == isin)
                or (isin and isin in alias_isins)):
            out.append((names[0] if names else (ticker or isin), ticker or isin, etfs))
    out.sort(key=lambda x: (-len(x[2]), x[1]))     # 편입 ETF 많은 종목 우선, 코드로 안정 정렬
    coverage = {s for subjects in holds.values() for s in subjects}
    return out[:limit], len(coverage)


def main(argv=None):
    ap = argparse.ArgumentParser(description="KG 질의 데모 (S1 DoD)")
    ap.add_argument("query", nargs="?", help="상품명 검색어 (예: 'TIGER 200')")
    ap.add_argument("--company", help="운용사·발행기관명으로 역질의 (CQ2)")
    ap.add_argument("--holds", help="구성종목명 또는 6자리 코드 — 편입한 ETF 역질의 (CQ6)")
    ap.add_argument("--tables", help="부분 적재 슬러그 목록 (예: kr_etf,constituents)")
    ap.add_argument("--out", default=os.path.join(HERE, "output"), help="kg 출력 폴더")
    ap.add_argument("--limit", type=int, default=5, help="표시 개수 (기본 5)")
    args = ap.parse_args(argv)
    if not args.query and not args.company and not args.holds:
        ap.error("검색어 또는 --company / --holds 가 필요하다")

    tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
    store = TripleStore.from_dir(args.out, tables)
    print(f"(그래프 적재: {store.triples:,} 트리플)")

    if args.holds:
        rows, coverage = find_holding_etfs(store, args.holds, args.limit)
        if not rows:
            print(f"'{args.holds}' 를 편입한 ETF 를 그래프에서 확인할 수 없음 "
                  f"(구성종목 조회 기준일 {CONSTITUENTS_AS_OF} · 수집분 ETF {coverage:,}종목 기준)")
            return
        for name, ticker, etfs in rows:
            print(f"\n[{name}({ticker})] 을(를) 편입한 ETF {len(etfs):,}종목 (표시 {min(len(etfs), args.limit)}건):")
            for s in etfs[:args.limit]:
                print(f"  - {store.label(s) or s}")
            print(f"  근거: {CONSTITUENTS_AS_OF} KRX 구성종목(PDF) · 수집분 ETF {coverage:,}종목 기준"
                  " — 부분 수집이면 미수집 ETF 는 판단 불가")
        return

    if args.company:
        rows = find_company_products(store, args.company, args.limit)
        if not rows:
            print(f"'{args.company}' 에 해당하는 운용사·발행기관을 그래프에서 확인할 수 없음")
            return
        for name, p, products in rows:
            rel = "운용" if p.endswith("managedBy") else "발행"
            print(f"\n[{name}] 이(가) {rel}하는 상품 (상위 {len(products)}건):")
            for s in products:
                print(f"  - {store.label(s)}")
        return

    hits = store.search_products(args.query, args.limit)
    if not hits:
        print(f"'{args.query}' 에 해당하는 상품을 그래프에서 확인할 수 없음 (데이터 기준일 {AS_OF})")
        return
    print(f"'{args.query}' 검색 결과 {len(hits)}건:\n")
    for s, _label in hits:
        print("\n".join(describe_product(store, s)))
        print()


if __name__ == "__main__":
    main()
