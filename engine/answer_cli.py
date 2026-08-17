# -*- coding: utf-8 -*-
"""
E2E 데모 CLI — 질문 1건을 전 과정(라우팅→조회→검증→생성→5필드)에 통과시킨다.

실행:
  python engine/answer_cli.py "순자산총액 기준으로 국내 ETF 상위 5개 알려줘"
  python engine/answer_cli.py "삼성전자가 포함된 ETF 알려줘" --kg kr_etf,constituents
  python engine/answer_cli.py "반도체 산업에 집중 투자하는 해외 ETF는?" --embed
  python engine/answer_cli.py "AA급 이상 회사채 투자 방법 알려줘" --llm --gen

옵션: --kg <슬러그,..> 그래프 부분 적재 · --embed 쿼리 임베딩(실 API 1콜) ·
      --llm 복잡 질문의 조회 계획을 HCX 로(실 API 1~2콜) · --gen 최종 문장을
      HCX 생성+사후 대조(실 API 1콜) · --json 5필드 원문 출력.
왜 CLI 인가: ⑥ API 서버 전까지 개발 중 관통 확인·디버깅 도구로 쓴다.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.answer_service import answer_question          # noqa: E402
from engine.channels import RuntimeContext                 # noqa: E402
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="E2E 데모")
    ap.add_argument("question")
    ap.add_argument("--id", default="", help="question_id 에코")
    ap.add_argument("--kg", help="그래프 부분 적재 슬러그 (예: kr_etf,constituents)")
    ap.add_argument("--embed", action="store_true", help="쿼리 임베딩 사용(실 API)")
    ap.add_argument("--llm", action="store_true", help="조회 계획을 HCX 로(실 API)")
    ap.add_argument("--gen", action="store_true", help="최종 문장 HCX 생성+사후 대조(실 API)")
    ap.add_argument("--json", action="store_true", help="5필드 JSON 원문 출력")
    args = ap.parse_args(argv)

    import duckdb
    t0 = time.perf_counter()
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    index = build_entity_index(con)
    ctx = RuntimeContext(con=con, index=index)

    if args.kg:
        from kg.kg_store import TripleStore
        ctx.kg_store = TripleStore.from_dir(os.path.join(ROOT, "kg", "output"),
                                            [t.strip() for t in args.kg.split(",")])
    if args.embed:
        from agent.clova_embedding import ClovaEmbeddingClient
        from vector.vector_store import VectorStore
        ctx.vstore = VectorStore.load()
        ctx.embedder = ClovaEmbeddingClient().embed
    llm_router, generator = None, None
    if args.llm:
        from engine.router_llm import make_llm_router
        llm_router = make_llm_router()
    if args.gen:
        from engine.generator import make_hcx_generator
        generator = make_hcx_generator()
    t_load = time.perf_counter() - t0

    t1 = time.perf_counter()
    out = answer_question(args.question, ctx, question_id=args.id,
                          llm_router=llm_router, generator=generator)
    t_answer = time.perf_counter() - t1

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[적재 {t_load:.1f}s · 응답 {t_answer:.2f}s]\n")
        print("── think_trace " + "─" * 40)
        print(out["think_trace"])
        print("\n── answer " + "─" * 45)
        print(out["answer"])
        print("\n── retrieved_context (앞 6건) " + "─" * 24)
        print("\n".join(out["retrieved_context"].splitlines()[:6]))
    con.close()


if __name__ == "__main__":
    main()
