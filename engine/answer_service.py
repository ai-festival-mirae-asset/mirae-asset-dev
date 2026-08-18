# -*- coding: utf-8 -*-
"""
답변 조립기 — 질문 1건의 전 과정: 라우팅 → 채널 조회 → 검증 → 생성 → 5필드 JSON.

무엇: API 서버(순서 ⑥)가 그대로 호출할 진입점 answer_question(). 흐름:
      ① 라우터(순서 ③)가 조회 계획을 세우고 ② 4채널을 실행하고
      ③ 5중 검문(순서 ④)이 답변 태도(answer/partial/refuse)를 확정하고
      ④ 생성기(순서 ⑤)가 있으면 HCX-005 로 문장을 다듬고(사후 대조 포함)
      ⑤ 공식 규격(5필드 전부 문자열)으로 직렬화한다.
왜  : 거절은 템플릿 문구만(함정 경로에서 AI 발화 0), 생성 실패는 규칙 요약으로
      폴백 — 어떤 경우에도 유효한 5필드 응답이 나온다.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.channels import execute_plan                      # noqa: E402
from engine.generator import post_check_answer                # noqa: E402
from engine.router import RATING_RANK, route                  # noqa: E402
from engine.validation import validate_answerability          # noqa: E402
from pipeline.evidence import (AS_OF_CONSTITUENTS, AS_OF_MASTER,  # noqa: E402
                               Evidence, to_context_string)

# 행 요약에서 이름으로 쓸 컬럼 우선순위 (테이블별 상이 — 먼저 발견되는 것 사용)
_NAME_COLS = ("pd_abrv_nm", "pd_nm", "PD_NM", "itm_nm", "etf_name", "COMPST_ISU_NM",
              "mgmt_co", "종목", "회사", "상품", "상품명", "매칭")
_SKIP_COLS = {"pd_itm_no", "PD_NO", "itm_no", "etf_isin", "COMPST_ISU_CD", "코드", "키"}


def _fmt_value(v):
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _fmt_row(row, max_fields=4):
    """행 1개 → '이름 (필드=값 · …)' — 테이블 무관 요약."""
    name = next((str(row[c]) for c in _NAME_COLS if row.get(c)), None)
    parts = []
    for k, v in row.items():
        if k in _SKIP_COLS or v is None or (name is not None and str(v) == name):
            continue
        if isinstance(v, list):
            v = " / ".join(str(x) for x in v[:5]) + (" 외" if len(v) > 5 else "")
        parts.append(f"{k}={_fmt_value(v)}")
        if len(parts) >= max_fields:
            break
    body = " · ".join(parts)
    return f"{name} ({body})" if name and body else (name or body or str(row))


def _sort_rows_by_aum(rows):
    def aum(row):
        v = row.get("pd_net_tamt")
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return -1.0
    return sorted(rows, key=aum, reverse=True)


def _draft_refusal(plan, result, verdict):
    """사유 기반 템플릿 거절문 — HCX 자유 생성 없음(확정 설계).

    사유는 검증(5중 검문)의 판정을 우선 쓰고, 라우터가 남긴 해석 노트를 보탠다.
    유사 이름 안내는 '부분 일치일 뿐 존재 근거가 아님'을 문면에 명시한다.
    """
    reasons = list(verdict.reasons)
    for n in plan.notes:
        if n not in reasons and "부분 일치" not in n:
            reasons.append(n)
    if not reasons:
        reasons = ["요청 내용을 보유 데이터에서 확인할 수 없습니다"]
    lines = ["요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다."]
    lines += [f"- 사유: {r}" for r in reasons]

    suggestions = list(verdict.suggestions)
    # 키워드 채널의 부분 일치도 안내에 합류하되, '상품' 종류이면서 질의어가 원문 표기
    # 그대로 이름 안에 보이는 것만 — 'kimi' ⊂ 'Denmark IMI'(공백 제거 우연 겹침)처럼
    # 검문소가 이미 "의미 없는 겹침"으로 판정한 이름을 안내로 되살리지 않는다(8/18 실측).
    kw_queries = [str(c.params.get("query", "")) for c in plan.calls if c.channel == "keyword"]
    for o in result.outcomes:
        if o.channel == "keyword":
            for r in o.rows:
                name = str(r["매칭"])
                is_product = str(r.get("종류", "")).startswith("product")
                visible = any(q and q.lower() in name.lower() for q in kw_queries)
                if (not r.get("직접일치") and is_product and visible
                        and name not in suggestions):
                    suggestions.append(name)
    if suggestions:
        lines.append("- 혹시 다음 상품을 찾으셨나요(명칭 부분 일치 안내이며, "
                     "질의하신 대상의 존재 근거는 아닙니다): " + " / ".join(suggestions[:3]))
    lines.append(f"(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})")
    return "\n".join(lines)


def _draft_rating_compare(plan):
    pairs = plan.hints.get("rating_compare") or []
    if len(pairs) < 2:
        return None
    (t1, r1), (t2, r2) = pairs[0], pairs[1]
    hi, lo = ((t1, r1), (t2, r2)) if r1 < r2 else ((t2, r2), (t1, r1))
    return (f"{hi[0]} 등급이 {lo[0]} 등급보다 높습니다. 신용등급 서열(AAA=1 최상 ~ D=20)에서 "
            f"{hi[0]}는 서열 {hi[1]}, {lo[0]}는 서열 {lo[1]}입니다. "
            f"(근거: 신용등급 서열 사전 — 신용평가 3사 공식 등급체계)")


def _draft_answer(plan, result):
    """규칙 기반 요약 답변 — 생성기가 없거나 실패했을 때의 폴백(항상 동작)."""
    lines = []
    for o in result.outcomes:
        if not o.ok or not o.rows:
            continue
        rows = o.rows
        if o.channel == "sql":
            if plan.hints.get("order") == "aum" and rows and "pd_net_tamt" in rows[0]:
                rows = _sort_rows_by_aum(rows)
            head = f"[{o.op}] 결과 {len(rows):,}건"
            body = [f"  {i}. {_fmt_row(r)}" for i, r in enumerate(rows[:5], 1)]
            lines.append("\n".join([head] + body))
        elif o.channel == "graph":
            for r in rows[:3]:
                if "편입ETF수" in r:
                    etfs = r.get("ETF") or []
                    lines.append(f"'{r['종목']}'({r['코드']})을(를) 편입한 ETF {r['편입ETF수']:,}종 — "
                                 f"대표: {' / '.join(etfs[:5])}")
                elif "상품수" in r:
                    lines.append(f"{r['회사']}이(가) {r['관계']}하는 상품 {r['상품수']:,}종 — "
                                 f"대표: {' / '.join((r.get('상품') or [])[:5])}")
                else:
                    lines.append(_fmt_row(r))
        elif o.channel == "vector":
            names = [str(r.get("pd_nm")) for r in rows[:5]]
            lines.append(f"[의미·키워드 결합 검색] 상위: " + " / ".join(names) + f" ({o.note})")
        elif o.channel == "keyword":
            exact = [r["매칭"] for r in rows if r.get("직접일치")]
            partial = [r["매칭"] for r in rows if not r.get("직접일치")]
            if exact:
                lines.append("명칭 직접 일치: " + " / ".join(exact[:5]))
            if partial:
                lines.append("유사 명칭 안내(부분 일치 — 존재 근거 아님): " + " / ".join(partial[:5]))
    if not lines:
        lines.append("조건에 일치하는 결과를 보유 데이터에서 확인하지 못했습니다.")
    if plan.notes:
        lines.append("")
        lines += [f"※ {n}" for n in plan.notes]
    lines.append(f"(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})")
    return "\n".join(lines)


def _ensure_notes(text, plan):
    """생성 답변에 해석·한계 노트와 기준일이 빠졌으면 강제로 붙인다(채점 필수 요소)."""
    for n in plan.notes:
        if n not in text:
            text += f"\n※ {n}"
    if "기준일" not in text:
        text += f"\n(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})"
    return text


def _think_trace(plan, result, verdict, gen_note=""):
    lines = [f"stage={plan.stage} intent={plan.intent} behavior={verdict.behavior}"
             f"(라우터 힌트 {plan.behavior_hint})"]
    if plan.entities:
        ents = "; ".join(f"{n}→{refs[0].kind}:{refs[0].key}" for n, refs in plan.entities[:6])
        lines.append(f"grounded: {ents}")
    if plan.unknown_terms:
        lines.append(f"미등록 토큰: {', '.join(plan.unknown_terms)}")
    for call in plan.calls:
        lines.append(f"call {call.channel}.{call.op} {call.params}")
    for ch, op, err in result.errors:
        lines.append(f"오류 {ch}.{op}: {err}")
    for g in verdict.gates:
        detail = f" — {g.reason}" if g.reason else ""
        lines.append(f"검문[{g.gate}] {g.verdict}{detail}")
    if gen_note:
        lines.append(f"생성: {gen_note}")
    for n in plan.notes:
        lines.append(f"note: {n}")
    return "\n".join(lines)


def serialize_answer(question_id, question, evidences, think_trace, answer):
    """공식 응답 규격 — 5필드 전부 문자열, 빈 값 없이. API 서버(⑥)가 재사용."""
    return {
        "question_id": str(question_id or ""),
        "question": str(question or ""),
        "retrieved_context": to_context_string(evidences) or "(근거 없음)",
        "think_trace": str(think_trace or "(기록 없음)"),
        "answer": str(answer or "답변 생성에 실패했습니다. 다시 시도해 주세요."),
    }


def answer_question(question, ctx, question_id="", today=None,
                    llm_router=None, generator=None, deadline=None):
    """질문 1건 → 5필드(string) dict — E2E 진입점.

    llm_router: 복잡한 질문의 조회 계획을 HCX 로 세우는 콜러블(없으면 규칙+폴백만).
    generator : 최종 문장을 HCX 로 다듬는 콜러블(없으면 규칙 요약) — 실패 시 자동 폴백.
    deadline  : 시간 예산(engine.deadline.Deadline) — 초과 시 생성 단계를 생략(강등).
    검증은 라우터 판정을 신뢰하지 않고 질문 원문에서 독립 재검사한다(이중 방어).
    """
    plan = route(question, ctx.index, policy=ctx.policy, today=today, llm_router=llm_router)
    result = execute_plan(plan, ctx)
    verdict = validate_answerability(question, plan, result, ctx.index, ctx.policy)

    evidences = list(result.evidences) + list(verdict.evidences)
    gen_note = ""
    if generator is not None and deadline is not None and deadline.over(deadline.generation_cutoff):
        generator = None
        gen_note = f"시간 예산 초과({deadline.elapsed():.1f}s) — HCX 생성 생략, 규칙 요약으로 강등"

    if verdict.behavior == "refuse":
        answer = _draft_refusal(plan, result, verdict)
    elif plan.intent == "rating_compare":            # 사전 근거 답변 — 생성 불필요(결정적)
        answer = _draft_rating_compare(plan) or _draft_answer(plan, result)
        evidences.append(Evidence(source="credit_rating.csv", source_id="서열사전",
                                  channel="keyword", as_of=AS_OF_MASTER,
                                  fields={k: RATING_RANK[k] for k, _r in
                                          (plan.hints.get("rating_compare") or [])[:2]}))
    else:
        if verdict.behavior == "partial":            # 한계 문구를 노트에 합류(생성 전에)
            for r in verdict.reasons:
                if r not in plan.notes:
                    plan.notes.append(r)
        answer = None
        if generator is not None:
            raw = generator(question, plan, result, verdict)
            if raw:
                checked, removed = post_check_answer(
                    raw, evidences, question, index=ctx.index,
                    extra_allowed=" ".join(plan.notes))
                if checked is not None:
                    answer = _ensure_notes(checked, plan)
                    gen_note = ("HCX-005 생성 · 사후 대조 통과" if not removed else
                                f"HCX-005 생성 · 사후 대조로 {len(removed)}줄 제거")
                else:
                    gen_note = "생성 답변 전체가 근거 대조 실패 — 규칙 요약으로 강등"
            else:
                gen_note = "생성 호출 실패 — 규칙 요약으로 폴백"
        if answer is None:
            answer = _draft_answer(plan, result)

    return serialize_answer(question_id, question, evidences,
                            _think_trace(plan, result, verdict, gen_note), answer)
