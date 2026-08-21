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
import re
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
    """행 1개 → '이름 (필드=값 · …)' — 테이블 무관 요약. 비중·수익률 열(*_pct)은 % 를 붙인다."""
    name = next((str(row[c]) for c in _NAME_COLS if row.get(c)), None)
    parts = []
    for k, v in row.items():
        if k in _SKIP_COLS or v is None or (name is not None and str(v) == name):
            continue
        if (str(k) + "_krw") in row:                      # 원 단위 원값 대신 환산 표기(…억원)만 보여 준다
            continue
        if isinstance(v, list):
            v = " / ".join(str(x) for x in v[:5]) + (" 외" if len(v) > 5 else "")
        unit = "%" if str(k).endswith("_pct") and isinstance(v, (int, float)) else ""
        parts.append(f"{k}={_fmt_value(v)}{unit}")
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


# 구성종목 조회 템플릿 — 0건이면 "조건 불일치"가 아니라 "구성 공시 없음(빈 응답·미수집)"이 맞는 표현
_CONSTITUENT_OPS = {"etp_pattern_top_constituents", "constituent_top_weights"}


def _draft_answer(plan, result):
    """규칙 기반 요약 답변 — 생성기가 없거나 실패했을 때의 폴백(항상 동작)."""
    if plan.intent == "unstructured_info":
        lines = ["요청하신 상품의 구조·투자전략·동향을 설명할 비정형 자료는 "
                 "현재 보유 데이터에서 확인할 수 없습니다."]
        # 마스터에 있는 사실(운용속성·위험등급·수익률·순자산·판매상태·벤치마크)은 답한다 (M-10)
        for o in result.outcomes:
            if o.ok and o.channel == "sql" and o.op == "fund_detail" and o.rows:
                lines.append("확인된 상품의 마스터 정보:")
                lines += [f"  - {_fmt_row(r, max_fields=8)}" for r in o.rows[:3]]
        if plan.notes:
            lines.append("")
            lines += [f"※ {n}" for n in plan.notes]
        lines.append(f"(데이터 기준일: 마스터 {AS_OF_MASTER} · 구성종목 {AS_OF_CONSTITUENTS})")
        return "\n".join(lines)

    lines = []
    for o in result.outcomes:
        if not o.ok:
            continue
        if not o.rows:
            if o.channel == "sql" and o.op in _CONSTITUENT_OPS:
                lines.append(f"[{o.op}] 구성 공시 없음 — 해당 상품의 {AS_OF_CONSTITUENTS} KRX 구성종목 공시가 "
                             "비어 있거나 미수집이라 구성종목을 확인할 수 없습니다")
            elif o.channel == "sql":
                lines.append(f"[{o.op}] 조건 일치 결과 0건")
            continue
        rows = o.rows
        if o.channel == "sql":
            if plan.hints.get("order") == "aum" and rows and "pd_net_tamt" in rows[0]:
                rows = _sort_rows_by_aum(rows)
            head = f"[{o.op}] 결과 {len(rows):,}건"
            display_rows = int(plan.hints.get("display_rows", 5))
            body = [f"  {i}. {_fmt_row(r)}" for i, r in enumerate(rows[:display_rows], 1)]
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
            if o.op == "fund_class_dictionary":
                lines += [f"{r['class']}형({r['name']}): {r['meaning']}" for r in rows]
                continue
            exact = list(dict.fromkeys(r["매칭"] for r in rows if r.get("직접일치")))
            partial = list(dict.fromkeys(r["매칭"] for r in rows if not r.get("직접일치")))
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


# 분포형 템플릿 — (첫 열 이름, 우리말 라벨). 한 갈래뿐이면 "전부 X — 다른 것 없음"을 명시한다 (L-20/L-30)
_DIST_OPS = {"global_ccy_dist": ("pd_trd_ccy", "거래통화"), "etp_currency_dist": ("drv_curr_cd", "거래통화"),
             "bond_currency_dist": ("CURR_CD", "통화"), "bond_class_dist": ("STD_PD_MCLS_NM", "대분류")}


def dist_sentence(op, rows):
    """분포 결과 → 결론 문장. 순수 함수(테스트 대상)."""
    col, label = _DIST_OPS[op]
    buckets = [(str(r.get(col)), int(r.get("n") or 0)) for r in rows if r.get(col) is not None]
    total = sum(n for _v, n in buckets)
    if not buckets or total == 0:
        return None
    if len(buckets) == 1:
        return f"{label}: 전부 {buckets[0][0]}({total:,}건) — 다른 {label} 없음"
    parts = ", ".join(f"{v} {n:,}건({n / total * 100:.1f}%)" for v, n in buckets[:5])
    more = f" 외 {len(buckets) - 5}종" if len(buckets) > 5 else ""
    return f"{label} 분포: {parts}{more} — 총 {total:,}건, 이 밖의 {label} 없음"


# 건수 템플릿 — 숫자 한 개짜리 결과는 생성기가 "정보 없음"으로 오독하기 쉬워(L-05 실측) 문장으로 승격한다
_COUNT_OPS = {"bond_count", "etp_count", "global_etf_count", "fund_counts"}
_COUNT_LABELS = {"n": "건수", "products": "상품(마스터) 수", "share_classes": "판매 클래스 수"}
# 구성종목에 선물·옵션이 보이면 파생 위험을 데이터 사실로 명시한다 (H-21·M-20)
_DERIV_SECUGRP = {"FU": "선물", "OP": "옵션"}


def count_sentence(op, rows):
    """건수 결과 → '조건 일치 건수: N건' 문장(열이 여럿이면 라벨별로). 순수 함수."""
    if not rows:
        return None
    parts = []
    for row in rows[:6]:
        label_cols = [k for k, v in row.items() if isinstance(v, str)]
        prefix = " ".join(str(row[k]) for k in label_cols[:2])
        nums = [f"{_COUNT_LABELS.get(k, k)} {int(v):,}건" for k, v in row.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            parts.append((prefix + ": " if prefix else "") + " · ".join(nums))
    if not parts:
        return None
    return "조건 일치 건수 — " + " / ".join(parts)


def data_notes(question, plan, result):
    """조회 결과에서 도출한 사실 노트 목록 — 분포 결론·건수·요청 필드 결측·파생 구성 명시."""
    notes = []
    for o in result.outcomes:
        if not o.ok or o.channel != "sql":
            continue
        if o.op in _DIST_OPS:
            s = dist_sentence(o.op, o.rows)
            if s:
                notes.append(s)
        if o.op in _COUNT_OPS:
            s = count_sentence(o.op, o.rows)
            if s:
                notes.append(s)
        if o.op in _CONSTITUENT_OPS and o.rows:
            kinds = {_DERIV_SECUGRP[r.get("SECUGRP_ID")] for r in o.rows if r.get("SECUGRP_ID") in _DERIV_SECUGRP}
            if kinds:
                notes.append(f"구성종목에 {'·'.join(sorted(kinds))}(파생상품)이 포함됨 — 레버리지·인버스형은 지수 선물로 "
                             "배수를 만들므로 기초지수 변동의 배수로 손익이 움직이는 파생 위험이 있음(위험등급은 상품 행 참조)")
        if o.op == "etp_detail" and o.rows and re.search(r"지수|추종|벤치마크|따라가", question):
            idx = o.rows[0].get("cu_base_index")
            if idx:
                notes.append(f"기초지수(cu_base_index): {idx}")
            else:
                notes.append("기초지수 값이 이 상품 행에 없음(cu_base_index 결측) — 국내ETF 마스터의 기초지수 컬럼은 "
                             "전체의 0.18%만 채워져 있어 추종 지수는 제공 데이터로 확인할 수 없음")
    return notes


def _ensure_notes(text, plan):
    """생성 답변에 해석·한계 노트와 기준일이 빠졌으면 강제로 붙인다(채점 필수 요소)."""
    for n in plan.notes:
        if n not in text:
            text += f"\n※ {n}"
    # '기준일'이라는 말과 실제 날짜가 둘 다 있어야 한다 — 생성기가 "데이터 기준일: 현재"(날짜 없음,
    # M-08·M-30)라거나 "2026-07-11"만 덜렁 쓰는(라벨 없음, L-21) 경우 모두 정식 기준일 줄을 붙인다.
    has_label = "기준일" in text
    has_date = AS_OF_MASTER in text or AS_OF_CONSTITUENTS in text
    if not (has_label and has_date):
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

    # 커버리지 수치는 생성 모델이 요약 과정에서 빼먹기 쉽다. 실제 조회된
    # 분자·분모를 노트로 승격해 생성 답변과 규칙 답변 모두에 강제로 남긴다.
    for outcome in result.outcomes:
        if outcome.channel == "sql" and outcome.op == "coverage_check":
            for row in outcome.rows:
                coverage_note = (f"{row['field']} 값 보유 {row['non_null']:,}/{row['total']:,}건"
                                 f"({row['coverage_pct']}%) 기준")
                if coverage_note not in plan.notes:
                    plan.notes.append(coverage_note)
    # 조회 결과에서만 알 수 있는 사실도 노트로 승격한다(8/19 ⑧): 분포 답변의 "전부/없음" 결론,
    # 상세 답변에서 물어본 필드(기초지수)가 결측인 사실 — 생성기가 흐리게 쓰거나 빼먹기 쉬운 것들.
    for note in data_notes(question, plan, result):
        if note not in plan.notes:
            plan.notes.append(note)
    # HCX 라우터(Stage B)가 세운 계획이 0건이면 "없다"가 아니라 "이 조건 해석으로는 못 찾음"이다 —
    # 계획의 파라미터가 질문과 어긋났을 수 있어(M-08·H-26 유형) 단정을 막는 노트를 강제한다(8/19).
    if plan.stage in ("llm", "llm_repair") and verdict.behavior != "refuse" \
            and not any(o.ok and o.rows for o in result.outcomes):
        zero_note = ("조회 계획(HCX 라우터)이 세운 조건으로는 일치하는 항목을 찾지 못함 — 질문 조건의 해석이 "
                     "다를 수 있어 '해당 상품이 없다'고 단정하지 않음(조건을 바꿔 다시 물으면 확인 가능)")
        if zero_note not in plan.notes:
            plan.notes.append(zero_note)

    evidences = list(result.evidences) + list(verdict.evidences)
    # 근거 0개 방지망(8/22 H-17 실측): HCX 계획이 0건으로 끝나면 근거 블록 없이 답이 나가
    # 채점 근거 축을 잃는다. 어떤 경로든 근거가 비면 "무엇을 어떤 조회로 찾아봤는지"를
    # validation 근거로 남긴다 — 0건·실패도 확인 과정의 근거다(거절 경로와 같은 원칙).
    if not evidences:
        for outcome in result.outcomes[:5]:
            n_rows = len(getattr(outcome, "rows", None) or [])
            evidences.append(Evidence(
                source="조회 기록", source_id=f"{outcome.channel}.{outcome.op}",
                channel="validation", as_of=AS_OF_MASTER,
                fields={"실행": f"{outcome.channel}.{outcome.op}",
                        "결과": (f"{n_rows}건" if outcome.ok else f"실패({(outcome.error or '')[:80]})")}))
        if not evidences:
            evidences.append(Evidence(source="조회 기록", source_id="실행 없음",
                                      channel="validation", as_of=AS_OF_MASTER,
                                      fields={"실행": "조회 없음", "결과": "검증 판정만으로 답변"}))
    gen_note = ""
    if generator is not None and deadline is not None and deadline.over(deadline.generation_cutoff):
        generator = None
        gen_note = f"시간 예산 초과({deadline.elapsed():.1f}s) — HCX 생성 생략, 규칙 요약으로 강등"

    if verdict.behavior == "refuse":
        answer = _draft_refusal(plan, result, verdict)
    elif plan.intent == "rating_compare":            # 사전 근거 답변 — 생성 불필요(결정적)
        # 다른 경로와 같이 해석 노트·기준일을 붙인다(8/18 채점기가 '답변에 기준일 없음'을 잡아냄)
        answer = _ensure_notes(_draft_rating_compare(plan) or _draft_answer(plan, result), plan)
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
        if generator is not None and not plan.hints.get("skip_generation"):
            raw = generator(question, plan, result, verdict)
            if raw:
                checked, removed = post_check_answer(
                    raw, evidences, question, index=ctx.index,
                    extra_allowed=" ".join(plan.notes))
                if checked is not None:
                    answer = _ensure_notes(checked, plan)
                    fixes = [r for _s, r in removed if r.startswith("표기 정정")]
                    dropped = [(s, r) for s, r in removed if not r.startswith("표기 정정")]
                    parts = ["HCX-005 생성"]
                    if fixes:                            # 이름 오기를 근거 표기로 되돌린 기록(8/19)
                        parts.append(f"이름 {len(fixes)}건 정정({'; '.join(fixes[:2])})")
                    if not dropped:
                        parts.append("사후 대조 통과")
                    else:                                # 무엇을 왜 지웠는지 남긴다(과잉 삭제 진단용, 8/19)
                        why = "; ".join(f"'{s[:28]}…'({r})" for s, r in dropped[:3])
                        parts.append(f"사후 대조로 {len(dropped)}줄 제거: {why}")
                    gen_note = " · ".join(parts)
                else:
                    gen_note = "생성 답변 전체가 근거 대조 실패 — 규칙 요약으로 강등"
            else:
                gen_note = "생성 호출 실패 — 규칙 요약으로 폴백"
        if answer is None:
            answer = _draft_answer(plan, result)

    return serialize_answer(question_id, question, evidences,
                            _think_trace(plan, result, verdict, gen_note), answer)
