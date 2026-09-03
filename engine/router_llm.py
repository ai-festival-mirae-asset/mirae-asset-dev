# -*- coding: utf-8 -*-
"""
Router Stage B — HCX-005 Function Calling 라우팅 (S2 순서 ③, 8/13).

무엇: 규칙(Stage A)이 확정 못한 질문을 HCX-005 FC 1콜로 라우팅 플랜 JSON 화.
      SQL 템플릿 id 는 enum 으로 강제 — LLM 은 SQL 을 쓰지 않고 id+파라미터만
      고른다(환각 SQL 구조적 차단, ROADMAP §4.1).
왜  : 멀티홉·교집합·교차 상품군 질문은 규칙 열거가 비경제적 — 대신 플랜 스키마
      검증 실패 시 1회 수리 콜, 그래도 실패면 None(→ 규칙 폴백). 어떤 경로든
      실행은 결정적 코드다.

규정: 메인 에이전트 구성(인텐트 분석 포함)은 HyperCLOVA X 고정 — clova_client 가
      provider 를 강제한다(타 LLM fallback 없음).
구조 주의: 테스트가 순수 함수(스키마 빌드·플랜 변환)를 import 한다 — import 부작용 금지.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.channels import resolve_raw_params                # noqa: E402
from engine.router import ChannelCall, RoutePlan              # noqa: E402
from engine.sql_templates import LLM_HIDDEN_ENUM_VALUES, LLM_HIDDEN_PARAMS, TEMPLATES, validate_params   # noqa: E402

GRAPH_OPS = ("holding_etfs", "company_products", "product_info", "constituents_of")
ROUTER_TEMPERATURE = 0.1          # 계획은 결정적일수록 좋다(8/19 ⑧-6)
ROUTER_SEED = 20260711

# LIKE 계열 파라미터 — LLM 에게는 *_raw(원문)로 받고 이스케이프는 실행기가 한다
_LIKE_PARAMS = {"pattern": "pattern_raw", "attr_pattern": "attr_pattern_raw",
                "region_pattern": "region_pattern_raw", "prefix": "prefix_raw",
                "exclude_region_pattern": "exclude_region_pattern_raw", "name_pattern": "name_pattern_raw"}

# 8/19 ⑧-2 — 파라미터 의미 검증(첫 성적표: 상품명을 id 자리에(M-08), '만기 3년 이하' 같은 글자를 숫자
# 자리에(H-26) 넣어 0건 → "없다"고 답할 위험). 스키마(이름·enum) 검증 위에 값의 종류를 본다.
_KEY_PARAMS = {"pd_itm_no", "itm_no", "code", "code_a", "code_b", "code_c", "code_d", "etf_id"}
_NUMERIC_PARAMS = {"limit", "top_etfs", "per_etf", "min_weight", "max_fee", "min_grade", "max_grade",
                   "min_risk", "max_risk", "min_coupon", "max_coupon", "max_rating_rank",
                   "min_rating_rank", "grade"}
_DATE_PARAMS = {"as_of_date", "until", "date_from", "date_to"}
_RANGES = {"min_grade": (1, 6), "max_grade": (1, 6), "min_risk": (1, 6), "max_risk": (1, 6),
           "grade": (1, 6), "max_rating_rank": (1, 20), "min_rating_rank": (1, 20),
           "limit": (1, 100), "top_etfs": (1, 10), "per_etf": (1, 50)}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY_LIKE_RE = re.compile(r"^(?:[A-Z]{2}[A-Z0-9]{9}\d|\d{6}|[A-Za-z0-9\-]{6,20})$")


def _grounded_lookup(partial_plan):
    """Stage A grounding → {정규화 이름: [ref]}, {키} — 이름을 키 자리에 넣은 값을 되돌리는 데 쓴다."""
    by_name, keys = {}, set()
    for name, refs in partial_plan.entities:
        by_name.setdefault(str(name), []).extend(refs)
        by_name.setdefault(re.sub(r"\s+", "", str(name)).casefold(), []).extend(refs)
        for r in refs:
            keys.add(str(r.key))
            by_name.setdefault(re.sub(r"\s+", "", str(r.display)).casefold(), []).append(r)
    return by_name, keys


_CONSTITUENT_KEY_RE = re.compile(r"^(\d{6}|[A-Z]{2}[A-Z0-9]{10})$")   # 종목 키: 6자리 코드 또는 ISIN


def _ref_by_key(partial_plan, key):
    """grounded 엔티티 중 키가 일치하는 ref — 키의 종류(상품/종목) 판별용."""
    for _name, refs in partial_plan.entities:
        for r in refs:
            if str(r.key) == key:
                return r
    return None


def coerce_llm_params(template_id, params, partial_plan):
    """LLM 이 낸 파라미터를 실행 가능한 값으로 고친다 — 못 고치면 ValueError(수리 콜 유도).

    - 키 자리(code·pd_itm_no·itm_no·etf_id…): grounded 키면 통과, grounded 이름이면 키로 변환,
      키 모양도 아니고 이름도 아니면 오류("이름을 키 자리에 넣지 말고 grounded 키를 쓰라").
    - 숫자 자리: 숫자·숫자 문자열만(글자 섞인 '3년 이하' 등은 오류), 정해진 범위 밖이면 오류.
    - 날짜 자리: YYYY-MM-DD 만.
    """
    by_name, keys = _grounded_lookup(partial_plan)
    fixed = {}
    for k, v in params.items():
        if v is None:
            continue
        if k in _KEY_PARAMS:
            sv = str(v).strip()
            # constituent_holders.code 는 '편입 종목(주식)' 키다 — ETF/상품 키나 지수명을
            # 넣으면 0건 조회가 된다(8/22 H-17 실측: ETF ISIN·'KOSPI200'). 의미가 어긋나면
            # 오류를 던져 수리 콜에서 올바른 템플릿(constituent_top_weights 등)로 바꾸게 한다.
            if template_id == "constituent_holders" and k == "code":
                ref = _ref_by_key(partial_plan, sv)
                if ref is not None and ref.kind != "constituent":
                    raise ValueError(f"[{template_id}] code 는 편입 종목(주식) 키여야 함 — '{sv}' 는 "
                                     f"{ref.kind} 키. ETF 의 구성종목을 보려면 constituent_top_weights(etf_id=…) 사용")
                norm_sv = re.sub(r"\s+", "", sv).casefold()
                if ref is None and not _CONSTITUENT_KEY_RE.match(sv) \
                        and not (by_name.get(sv) or by_name.get(norm_sv)):
                    raise ValueError(f"[{template_id}] code '{sv}' 는 종목 코드(6자리)/ISIN 이 아님 — "
                                     f"지수명·조건이면 다른 템플릿을 쓰고, 종목이면 grounded 키를 사용")
            if sv in keys:
                fixed[k] = sv
                continue
            norm = re.sub(r"\s+", "", sv).casefold()
            refs = by_name.get(sv) or by_name.get(norm)
            if refs:
                fixed[k] = str(refs[0].key)          # 이름 → grounded 키
                continue
            if _KEY_LIKE_RE.match(sv):
                fixed[k] = sv                        # 키 모양(코드·ISIN) — 실행 결과로 검증됨
                continue
            raise ValueError(f"[{template_id}] {k} 에 이름 '{sv}' 이(가) 들어감 — grounded 엔티티의 키를 써야 함")
        elif k in _NUMERIC_PARAMS:
            try:
                num = float(str(v).replace(",", "").strip())
            except ValueError:
                raise ValueError(f"[{template_id}] {k} 는 숫자여야 함: {v!r}")
            if k in _RANGES and not (_RANGES[k][0] <= num <= _RANGES[k][1]):
                raise ValueError(f"[{template_id}] {k}={num:g} 는 허용 범위 {_RANGES[k]} 밖")
            fixed[k] = int(num) if num.is_integer() else num
        elif k in _DATE_PARAMS:
            sv = str(v).strip()
            if not _DATE_RE.match(sv):
                raise ValueError(f"[{template_id}] {k} 는 YYYY-MM-DD 형식이어야 함: {v!r}")
            fixed[k] = sv
        else:
            fixed[k] = v
    return fixed


def coerce_graph_query(op, query, partial_plan):
    """graph 호출 query — holding_etfs 는 종목 키(코드/ISIN)여야 한다. 이름이면 grounded 키로 바꾼다."""
    by_name, keys = _grounded_lookup(partial_plan)
    sq = str(query).strip()
    if op != "holding_etfs" or sq in keys:
        return sq
    refs = by_name.get(sq) or by_name.get(re.sub(r"\s+", "", sq).casefold())
    for r in refs or []:
        if r.kind == "constituent":
            return str(r.key)
    if _KEY_LIKE_RE.match(sq):
        return sq
    raise ValueError(f"graph holding_etfs: query 는 종목 코드/ISIN 이어야 함(받은 값 {sq!r})")


# ---------------------------------------------------------------------------
# FC 도구 스키마 + 프롬프트 (순수 함수)
# ---------------------------------------------------------------------------

def _template_catalog_text():
    """템플릿 카탈로그를 프롬프트용 텍스트로 — id·설명·파라미터(필수/enum).

    규칙 라우터 전용 허용값(sql_templates.LLM_HIDDEN_ENUM_VALUES)은 목록에서 뺀다 — 이 텍스트는 HCX 프롬프트에
    그대로 실려서 값 하나가 늘어도 경계 문항의 선택이 흔들린다(9/2 H-17 실측). 프롬프트를 검증된 상태로 고정.
    """
    lines = []
    for t in TEMPLATES.values():
        ps = []
        for p in t.params:
            if (t.id, p.name) in LLM_HIDDEN_PARAMS:      # 규칙 라우터 전용 파라미터(9/3) — 목록 불변
                continue
            name = _LIKE_PARAMS.get(p.name, p.name)
            tag = "필수" if p.required else "선택"
            hidden = LLM_HIDDEN_ENUM_VALUES.get((t.id, p.name), ())
            shown = [v for v in p.enum if v not in hidden] if p.enum else []
            enum = f" enum{shown}" if shown else ""
            ps.append(f"{name}({tag}{enum})")
        lines.append(f"- {t.id}: {t.description} 파라미터: {', '.join(ps) or '없음'}")
    return "\n".join(lines)


def build_router_tool():
    """submit_route_plan FC 도구 — template_id 는 enum 강제."""
    return {
        "type": "function",
        "function": {
            "name": "submit_route_plan",
            "description": "질문을 데이터 채널 호출 계획으로 변환해 제출한다. "
                           "실행은 시스템이 하며, 여기서는 계획만 만든다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string",
                               "description": "질문 유형 한 줄(예: 구성종목 교집합+정렬)"},
                    "sql_calls": {
                        "type": "array",
                        "description": "실행할 SQL 템플릿 호출 목록(순서대로 실행)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "template_id": {"type": "string",
                                                "enum": sorted(TEMPLATES)},
                                "params": {"type": "object",
                                           "description": "템플릿 파라미터. *_raw 는 원문 텍스트(와일드카드 금지)"},
                            },
                            "required": ["template_id"],
                        },
                    },
                    "graph_calls": {
                        "type": "array",
                        "description": "지식그래프 질의 — 종목 편입 역질의/운용사 상품/상품 정보/구성 나열",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {"type": "string", "enum": list(GRAPH_OPS)},
                                "query": {"type": "string",
                                          "description": "종목 코드/ISIN 또는 grounded 명칭"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["op", "query"],
                        },
                    },
                    "keyword_queries": {"type": "array", "items": {"type": "string"},
                                        "description": "명칭 사전 조회(직접 일치 우선, 부분 일치는 안내용)"},
                    "vector_query": {"type": "string",
                                     "description": "해외ETF 전략 서술 의미 검색 질의(불필요하면 생략)"},
                    "notes": {"type": "array", "items": {"type": "string"},
                              "description": "답변에 명시할 해석·한계 문구"},
                    "unsupported_constraints": {
                        "type": "array", "items": {"type": "string"},
                        "description": "선택한 조회로는 적용할 수 없어 부분 답변에서 제외할 조건",
                    },
                },
                "required": ["intent"],
            },
        },
    }


def build_router_messages(question, partial_plan):
    """시스템+유저 메시지 — Stage A 의 grounding·추출 힌트를 근거로 제공."""
    grounded = []
    for name, refs in partial_plan.entities[:8]:
        for ref in refs[:2]:
            grounded.append(f"'{name}' → {ref.kind} · 키 {ref.key} · {ref.display}")
    hints = {k: v for k, v in partial_plan.hints.items() if v}
    system = (
        "너는 금융상품 질의 라우터다. 질문을 분석해 submit_route_plan 도구로 데이터 조회 "
        "계획만 제출한다(답변 생성 아님).\n"
        "데이터 범위(8/26 재배포본 — 국내 2026-08-22 · 해외 2026-08-23 기준): 국내채권 20,497종 · "
        "국내 ETF/ETN 1,779 · 해외 ETF/ETN 6,037 · 공모펀드 상품 23,622(클래스 23,676) · "
        "ETF 구성종목 75,867행(7/10 기준) · 해외ETF 전략 서술 임베딩. "
        "국내 ETF 분배(배당)수익률·분배금·지급월과 추적오차·괴리율은 재배포본부터 제공.\n"
        "규칙:\n"
        "1) 아래 grounded 엔티티의 키만 template params/graph query 에 사용한다. "
        "확인 안 된 이름은 keyword_queries 에 넣는다(지어내기 금지).\n"
        "2) 종목을 편입한 ETF 질문 → graph holding_etfs(query=종목 키) + "
        "sql constituent_holders(code=종목 키).\n"
        "3) 수치 필터·정렬·건수는 SQL 템플릿으로만. 템플릿 밖 연산이 필요하면 "
        "질문의 분리 가능한 일부만 가장 가까운 템플릿으로 조회하고 unsupported_constraints에 "
        "적용하지 못한 조건을 정확히 적는다. 핵심 조건을 전혀 조회할 수 없으면 임의의 "
        "템플릿으로 대체하지 않는다.\n"
        "4) 해석이 갈리는 표현(위험 낮음, AA 이상, 최근 등)은 notes 에 채택 해석을 적는다.\n"
        "5) 미래·실시간·기준일 이후 정보는 조회 계획을 만들지 말고 intent 에 "
        "'조회 불가'와 사유를 적는다.\n\n"
        "모범 예시(8/26 — 형식 참고):\n"
        "- '○○자산운용이 운용하는 ETF 중 순자산이 가장 큰 건?' → sql_calls=[{template_id:'etp_by_mgmt', "
        "params:{mgmt:'<grounded 키>', active_only:'Y', limit:1}}] — 이 템플릿은 순자산 내림차순이라 "
        "limit=1 이 곧 1위다. 상품 '이름'이 아니라 grounded '키'를 넣는다.\n"
        "- '다음 주에 상장하는 국내 ETF 뭐야?' → sql_calls 없음, intent='조회 불가: 기준일(2026-08-22) "
        "이후 미래 시점' — 날짜 템플릿에 미래 구간을 만들어 넣지 않는다.\n"
        "- '삼성전자를 담은 ETF 중 순자산 상위 5개' → graph_calls=[{op:'holding_etfs', query:'005930'}], "
        "sql_calls=[{template_id:'constituent_holders', params:{code:'005930', order:'aum', limit:30}}], "
        "notes=['순자산 내림차순 기준'].\n\n"
        f"SQL 템플릿 카탈로그:\n{_template_catalog_text()}"
    )
    user = (
        f"질문: {question}\n\n"
        f"grounded 엔티티(정규화 일치):\n" + ("\n".join(grounded) or "(없음)") + "\n\n"
        f"미등록 토큰(데이터에 없음 — 존재 근거로 쓰지 말 것): "
        f"{', '.join(partial_plan.unknown_terms) or '(없음)'}\n"
        f"추출 힌트: {json.dumps(hints, ensure_ascii=False, default=str)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# 플랜 변환·검증 (순수 함수)
# ---------------------------------------------------------------------------

def args_to_plan(args, partial_plan, stage="llm"):
    """FC arguments → RoutePlan. 검증 실패는 ValueError — 호출부가 수리 1회를 시도."""
    if not isinstance(args, dict):
        raise ValueError(f"arguments 가 객체가 아님: {type(args).__name__}")
    plan = RoutePlan(intent=str(args.get("intent") or "llm_plan"),
                     entities=partial_plan.entities,
                     unknown_terms=partial_plan.unknown_terms,
                     hints=dict(partial_plan.hints), stage=stage,
                     notes=list(partial_plan.notes))
    for note in args.get("notes") or []:
        if isinstance(note, str) and note not in plan.notes:
            plan.notes.append(note)

    unsupported = [str(value).strip() for value in args.get("unsupported_constraints") or []
                   if str(value).strip()]
    if unsupported:
        plan.behavior_hint = "partial"
        plan.hints["unsupported_constraints"] = unsupported
        for value in unsupported:
            plan.notes.append(f"미지원 조건: {value} — 해당 조건을 제외한 범위에서만 조회")

    for call in args.get("sql_calls") or []:
        tid = call.get("template_id")
        params = call.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"[{tid}] params 는 객체여야 함")
        validate_params(tid, resolve_raw_params(params))   # KeyError/ValueError 그대로 전파(이름·enum·필수)
        params = coerce_llm_params(tid, params, partial_plan)   # 값의 종류(키/숫자/날짜) — 8/19
        plan.calls.append(ChannelCall("sql", tid, params))

    for call in args.get("graph_calls") or []:
        op = call.get("op")
        if op not in GRAPH_OPS:
            raise ValueError(f"모르는 그래프 op: {op!r} (가능: {list(GRAPH_OPS)})")
        if not call.get("query"):
            raise ValueError(f"graph {op}: query 누락")
        plan.calls.append(ChannelCall("graph", op,
                                      {"query": coerce_graph_query(op, call["query"], partial_plan),
                                       "limit": int(call.get("limit") or 10)}))

    for kq in args.get("keyword_queries") or []:
        if isinstance(kq, str) and kq.strip():
            plan.calls.append(ChannelCall("keyword", "lookup", {"query": kq.strip(), "limit": 5}))

    vq = args.get("vector_query")
    if isinstance(vq, str) and vq.strip():
        plan.calls.append(ChannelCall("vector", "semantic", {"query": vq.strip(), "k": 8}))

    if not plan.calls:
        raise ValueError("플랜에 채널 호출이 하나도 없음 — 최소 1개 필요")
    return plan


def extract_tool_args(response):
    """CLOVA v3 응답 → submit_route_plan arguments(dict). 형식 이탈은 ValueError."""
    msg = (response.get("result") or {}).get("message") or {}
    calls = msg.get("toolCalls") or []
    if not calls:
        raise ValueError(f"toolCalls 없음 (finishReason={ (response.get('result') or {}).get('finishReason')!r})")
    fn = (calls[0].get("function") or {})
    args = fn.get("arguments")
    if isinstance(args, str):
        args = json.loads(args)
    if not isinstance(args, dict):
        raise ValueError("arguments 파싱 실패")
    return calls[0].get("id"), args


# ---------------------------------------------------------------------------
# 호출부 — clova_client 주입(오프라인 테스트는 mock transport)
# ---------------------------------------------------------------------------

def make_llm_router(client=None):
    """route(llm_router=...) 주입용 콜러블 생성. client 미지정 시 HCX-005 생성.

    반환 콜러블: (question, partial_plan) → RoutePlan | None(실패 — 규칙 폴백).
    스키마 검증 실패 시 오류를 tool 결과로 돌려주는 수리 콜 1회.
    """
    if client is None:
        from agent.clova_client import ClovaChatClient
        client = ClovaChatClient(model="HCX-005")
    tool = build_router_tool()
    force = {"type": "function", "function": {"name": "submit_route_plan"}}
    gen_kwargs = {"temperature": ROUTER_TEMPERATURE, "seed": ROUTER_SEED}   # 계획의 흔들림 억제(8/19)

    def llm_router(question, partial_plan):
        messages = build_router_messages(question, partial_plan)
        try:
            resp = client.chat(messages, tools=[tool], tool_choice=force, **gen_kwargs)
        except Exception:
            return None                                   # API 오류 — 규칙 폴백
        try:
            _call_id, args = extract_tool_args(resp)
            return args_to_plan(args, partial_plan, stage="llm")
        except (ValueError, KeyError) as exc:
            repair_error = f"{type(exc).__name__}: {exc}"
        # 수리 1회: 오류를 tool 결과로 알려주고 재제출 받는다
        assistant = (resp.get("result") or {}).get("message") or {}
        tool_call_id = ((assistant.get("toolCalls") or [{}])[0].get("id")) or "call-0"
        repair_messages = messages + [
            {"role": "assistant", "content": assistant.get("content") or "",
             "toolCalls": assistant.get("toolCalls") or []},
            {"role": "tool", "toolCallId": tool_call_id,
             "content": f"플랜 검증 실패: {repair_error}. 오류를 고쳐 "
                        f"submit_route_plan 을 다시 호출하라."},
        ]
        try:
            resp2 = client.chat(repair_messages, tools=[tool], tool_choice=force, **gen_kwargs)
            _cid, args2 = extract_tool_args(resp2)
            return args_to_plan(args2, partial_plan, stage="llm_repair")
        except Exception:
            return None                                   # 수리 실패 — 규칙 폴백
    return llm_router
