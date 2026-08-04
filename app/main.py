"""GET /answer 뼈대.

지금은 질문 해석(intent parsing)과 답변 문장 생성을 규칙 기반으로 스텁 처리한다.
HyperCLOVA X 연동 시 parse_question()과 render_answer()만 교체하면 되도록 분리해뒀다.
실제 조건 필터링·정렬은 항상 scripts/query_engine.py가 데이터로 직접 계산한다.
"""

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from query_engine import query  # noqa: E402

app = FastAPI(title="금융상품 Agent (스텁)")


def parse_question(question: str) -> dict[str, Any] | None:
    """질문 -> 구조화된 질의 조건. TODO: HyperCLOVA X로 교체.

    지금은 과제 소개 예시 질의 패턴 하나만 인식하는 규칙 기반 스텁이다.
    """
    q = question.replace(" ", "")
    if "미국" in q and "etf" in q.lower() and "주식" in q and "보수" in q:
        return {
            "table": "overseas_etf",
            "filters": {"wu_inv_ast_type": "Equity"},
            "sort": [("cu_charge_rt", "asc"), ("du_last_aum", "desc")],
            "limit": 3,
            "columns": ["pd_itm_no", "pd_nm", "cu_charge_rt", "du_last_aum"],
        }
    return None


def render_answer(question: str, plan: dict[str, Any], rows: list[dict], warnings: list[str]) -> str:
    """조회 결과 -> 사람이 읽을 답변 문장. TODO: HyperCLOVA X로 교체."""
    if not rows:
        return "조건에 맞는 상품을 찾지 못했습니다. 조건을 다시 확인해 주세요."

    lines = [f"조건에 부합하는 상품 {len(rows)}건입니다 (기준일 2026-07-11):"]
    for r in rows:
        lines.append(
            f"- {r['pd_nm']} ({r['pd_itm_no']}): 총보수 {r['cu_charge_rt']}%, "
            f"운용규모 {r['du_last_aum']:,.0f}"
        )
    if warnings:
        lines.append("주의: " + " ".join(warnings))
    return "\n".join(lines)


@app.get("/answer")
def answer(question_id: str, question: str) -> dict[str, Any]:
    plan = parse_question(question)

    if plan is None:
        return {
            "question_id": question_id,
            "question": question,
            "retrieved_context": None,
            "think_trace": [{"tool": "parse_question", "status": "unsupported_pattern"}],
            "answer": "현재 데모 버전은 정해진 예시 질의 패턴만 처리합니다. 다른 조건은 아직 지원하지 않습니다.",
        }

    result_df, warnings = query(
        table=plan["table"], filters=plan["filters"], sort=plan["sort"], limit=plan["limit"]
    )
    rows = result_df[plan["columns"]].to_dict(orient="records")

    think_trace = [
        {"tool": "parse_question", "status": "success", "matched_pattern": "overseas_equity_etf_low_fee_high_aum"},
        {"tool": "query_engine.query", "status": "success", "table": plan["table"], "row_count": len(rows)},
    ]

    retrieved_context = {
        "source_tables": ["PREF02N001"],
        "columns": plan["columns"],
        "as_of_date": "2026-07-11",
        "filters": plan["filters"],
        "sort": plan["sort"],
        "limit": plan["limit"],
    }

    return {
        "question_id": question_id,
        "question": question,
        "retrieved_context": retrieved_context,
        "think_trace": think_trace,
        "answer": render_answer(question, plan, rows, warnings),
    }
