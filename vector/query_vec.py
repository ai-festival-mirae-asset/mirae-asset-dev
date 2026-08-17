# -*- coding: utf-8 -*-
"""
벡터 채널 질의 데모 CLI — 의미 검색으로 해외ETF 전략 서술을 찾는다 (중-1·상-1 유형 기반).

실행:
  python vector/query_vec.py "China semiconductor AI"      # 쿼리 임베딩(실 API 1회) → top-k
  python vector/query_vec.py "우주항공 방위산업" --k 8

주의: 쿼리도 같은 모델(Embedding v2)로 임베딩해야 코퍼스와 공간이 일치한다.
근거 표시(출처 테이블·상품번호·기준일)는 필수 규칙(ROADMAP §3) — 데모부터 포함.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vector.vector_store import VectorStore  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="벡터 채널 질의 데모")
    ap.add_argument("query", help="검색 질의(자연어)")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args(argv)

    store = VectorStore.load()
    print(f"(인덱스: {len(store.ids):,}×{store.meta['dim']} · 모델 {store.meta['model']})")

    from agent.clova_embedding import ClovaEmbeddingClient
    vec, _tokens = ClovaEmbeddingClient().embed(args.query)

    hits = store.search(vec, args.k)
    if not hits:
        print("검색 결과 없음")
        return
    print(f"'{args.query}' 상위 {len(hits)}건:\n")
    for sha, score, products in hits:
        head = products[0] if products else {"pd_itm_no": "?", "pd_nm": "(매핑 없음)"}
        extra = f" 외 {len(products) - 1}종" if len(products) > 1 else ""
        print(f"· [{score:.3f}] {head['pd_nm']}{extra}")
        print(f"    근거: PREF02N001 · 상품번호 {head['pd_itm_no']} · cu_strtegy 서술 · 기준일 {store.meta['as_of']}")


if __name__ == "__main__":
    main()
