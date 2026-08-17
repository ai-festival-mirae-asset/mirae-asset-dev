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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.channels import resolve_raw_params                # noqa: E402
from engine.router import ChannelCall, RoutePlan              # noqa: E402
from engine.sql_templates import TEMPLATES, validate_params   # noqa: E402

GRAPH_OPS = ("holding_etfs", "company_products", "product_info", "constituents_of")

# LIKE 계열 파라미터 — LLM 에게는 *_raw(원문)로 받고 이스케이프는 실행기가 한다
_LIKE_PARAMS = {"pattern": "pattern_raw", "attr_pattern": "attr_pattern_raw",
                "region_pattern": "region_pattern_raw"}


# ---------------------------------------------------------------------------
# FC 도구 스키마 + 프롬프트 (순수 함수)
# ---------------------------------------------------------------------------

def _template_catalog_text():
    """템플릿 카탈로그를 프롬프트용 텍스트로 — id·설명·파라미터(필수/enum)."""
    lines = []
    for t in TEMPLATES.values():
        ps = []
        for p in t.params:
            name = _LIKE_PARAMS.get(p.name, p.name)
            tag = "필수" if p.required else "선택"
            enum = f" enum{list(p.enum)}" if p.enum else ""
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
        "데이터 범위(2026-07-11 스냅샷): 국내채권 42,394 · 국내 ETF/ETN 1,733 · "
        "해외 ETF/ETN 5,646 · 공모펀드 11,138(클래스 95,618) · ETF 구성종목 75,081행(7/10 기준) · "
        "해외ETF 전략 서술 임베딩.\n"
        "규칙:\n"
        "1) 아래 grounded 엔티티의 키만 template params/graph query 에 사용한다. "
        "확인 안 된 이름은 keyword_queries 에 넣는다(지어내기 금지).\n"
        "2) 종목을 편입한 ETF 질문 → graph holding_etfs(query=종목 키) + "
        "sql constituent_holders(code=종목 키).\n"
        "3) 수치 필터·정렬·건수는 SQL 템플릿으로만. 템플릿 밖 연산이 필요하면 "
        "가장 가까운 템플릿 결과 + notes 로 한계를 명시한다.\n"
        "4) 해석이 갈리는 표현(위험 낮음, AA 이상, 최근 등)은 notes 에 채택 해석을 적는다.\n"
        "5) 미래·실시간·기준일 이후 정보는 조회 계획을 만들지 말고 intent 에 "
        "'조회 불가'와 사유를 적는다.\n\n"
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

    for call in args.get("sql_calls") or []:
        tid = call.get("template_id")
        params = call.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"[{tid}] params 는 객체여야 함")
        validate_params(tid, resolve_raw_params(params))   # KeyError/ValueError 그대로 전파
        plan.calls.append(ChannelCall("sql", tid, params))

    for call in args.get("graph_calls") or []:
        op = call.get("op")
        if op not in GRAPH_OPS:
            raise ValueError(f"모르는 그래프 op: {op!r} (가능: {list(GRAPH_OPS)})")
        if not call.get("query"):
            raise ValueError(f"graph {op}: query 누락")
        plan.calls.append(ChannelCall("graph", op,
                                      {"query": call["query"],
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

    def llm_router(question, partial_plan):
        messages = build_router_messages(question, partial_plan)
        try:
            resp = client.chat(messages, tools=[tool], tool_choice=force)
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
            resp2 = client.chat(repair_messages, tools=[tool], tool_choice=force)
            _cid, args2 = extract_tool_args(resp2)
            return args_to_plan(args2, partial_plan, stage="llm_repair")
        except Exception:
            return None                                   # 수리 실패 — 규칙 폴백
    return llm_router
