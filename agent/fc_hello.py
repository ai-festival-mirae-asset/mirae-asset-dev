# -*- coding: utf-8 -*-
"""
HCX-007 Function Calling "Hello World" — Sprint 0 DoD

무엇을: 더미 도구 1개(get_product_count)를 JSON Schema 로 정의해 Function Calling
        왕복(모델의 도구 호출 → 로컬 실행 → tool 메시지 반환 → 최종 답변)을 수행한다.
왜   : Sprint 0 완료 기준 "HCX-007이 우리가 정의한 더미 함수를 호출해 응답을 돌려줬다"
        (ROADMAP.md Sprint 0 DoD). S2 에이전트 루프(파라미터화 도구 호출)의 최소 원형.

실행
  python agent/fc_hello.py --dry-run   # 키 불필요. 요청 payload 구성·검증 + mock 응답으로
                                       # 전체 FC 루프를 시연(오가는 내용을 stdout 출력)
  python agent/fc_hello.py             # 실제 호출. 환경변수 CLOVASTUDIO_API_KEY 필요.
                                       # (키 부재 시 안내와 함께 즉시 실패 — fallback 없음)
  python agent/fc_hello.py --model HCX-005   # 모델 선택. 계정의 서비스 앱이 HCX-005 대상이라
                                             # HCX-007 은 40100(No Service App) — 별도 신청 필요(8/13 실측)

주의: dry-run 과 실제 호출은 완전히 같은 코드 경로(payload 구성 → httpx 전송 → 응답
      파싱 → 루프)를 지난다. dry-run 은 transport 만 MockTransport 로 바꾼 것이므로,
      키 발급 후 실제 호출에서 달라지는 것은 응답 내용뿐이다.
"""
import json
import sys
import argparse
from pathlib import Path

import httpx

# repo 루트를 sys.path 에 추가 — `python agent/fc_hello.py` 직접 실행 지원
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.clova_client import ClovaChatClient, MIN_COMPLETION_TOKENS, ALLOWED_MODELS  # noqa: E402


def _force_utf8_stdout() -> None:
    """Windows 콘솔(cp949)에서도 한글 출력이 깨지지 않게. main() 에서만 호출한다
    (모듈 import 시점에 stdout 을 바꾸면 pytest 의 capture 가 깨진다)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

MODEL = "HCX-007"

# ---------------------------------------------------------------------------
# 더미 도구 정의
# ---------------------------------------------------------------------------

# 스냅샷(2026-07-11) 기준 참고치를 하드코딩한 더미 값이다.
# (국내ETF 1,201 / 해외ETF 5,587 은 8/5 교차검증의 정제 기준 ETF 건수,
#  채권 42,394 / 공모펀드 11,138 은 원본·정제 건수)
# 실제 도구는 S2 에서 DB 조회로 대체된다 — 여기서는 FC 왕복 자체가 목적.
_PRODUCT_COUNTS = {
    "국내ETF": 1201,
    "해외ETF": 5587,
    "채권": 42394,
    "공모펀드": 11138,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_product_count",
            "description": (
                "상품군별 등록 상품 수를 반환한다. "
                "데이터 스냅샷 기준일은 2026-07-11 이다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {
                        "type": "string",
                        "description": "상품군 이름",
                        "enum": sorted(_PRODUCT_COUNTS.keys()),
                    }
                },
                "required": ["product_type"],
            },
        },
    }
]


def get_product_count(product_type: str) -> dict:
    """더미 도구 본체 — 하드코딩 값 반환. 미지의 상품군은 오류 dict 로 답한다."""
    if product_type not in _PRODUCT_COUNTS:
        return {"error": f"알 수 없는 상품군: {product_type}",
                "supported": sorted(_PRODUCT_COUNTS.keys())}
    return {"product_type": product_type,
            "count": _PRODUCT_COUNTS[product_type],
            "as_of": "2026-07-11"}


LOCAL_TOOLS = {"get_product_count": get_product_count}

# ---------------------------------------------------------------------------
# FC 왕복 루프
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "너는 금융상품 데이터 QA 어시스턴트다. "
    "상품 수 질문에는 반드시 get_product_count 도구를 사용해 답하라."
)


def _print(title: str, obj) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(obj, ensure_ascii=False, indent=2) if not isinstance(obj, str) else obj)


def run_fc_loop(client: ClovaChatClient, question: str, max_rounds: int = 3) -> dict:
    """Function Calling 왕복을 수행하고 구조화 결과를 반환한다.

    반환 dict 의 tool_trace(도구명/파라미터/결과/호출ID 목록)는 향후 에이전트의
    think_trace(근거 추적 로그)의 원형이다 — S2 에서 retrieved_context 와 함께
    구조화 로깅으로 확장된다.
    """
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    trace: dict = {"question": question, "model": client.model,
                   "tool_trace": [], "rounds": 0, "final_answer": None}

    for _ in range(max_rounds):
        trace["rounds"] += 1
        _print(f"요청 payload (round {trace['rounds']})",
               client.build_request(messages, tools=TOOLS)[2])
        data = client.chat(messages, tools=TOOLS,
                           max_completion_tokens=MIN_COMPLETION_TOKENS)
        result = data["result"]
        message = result["message"]
        tool_calls = message.get("toolCalls") or []
        _print(f"응답 (finishReason={result.get('finishReason')})", message)

        if not tool_calls:                      # 최종 답변 도착 → 종료
            trace["final_answer"] = message.get("content", "")
            return trace

        # 모델이 도구 호출을 요청 → 로컬 실행 후 tool 메시지로 반환
        # 응답 메시지(assistant + toolCalls)를 대화에 그대로 되돌려 넣는다(문서 규약).
        messages.append({"role": "assistant",
                         "content": message.get("content") or "",
                         "toolCalls": tool_calls})
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            if isinstance(args, str):           # OpenAI 호환 응답은 문자열일 수 있음(방어)
                args = json.loads(args) if args else {}
            local_fn = LOCAL_TOOLS.get(fn_name)
            result_obj = (local_fn(**args) if local_fn
                          else {"error": f"미구현 도구: {fn_name}"})
            _print(f"로컬 도구 실행: {fn_name}({args})", result_obj)
            trace["tool_trace"].append({
                "tool_call_id": tc.get("id"),
                "tool_name": fn_name,
                "arguments": args,
                "result": result_obj,
            })
            messages.append({
                "role": "tool",
                "toolCallId": tc.get("id"),
                "content": json.dumps(result_obj, ensure_ascii=False),
            })

    raise RuntimeError(f"{max_rounds}회 왕복에도 최종 답변이 오지 않음")


# ---------------------------------------------------------------------------
# --dry-run 용 mock transport (키 없이 전체 루프 시연)
# ---------------------------------------------------------------------------

def _mock_transport() -> httpx.MockTransport:
    """1회차: toolCalls 응답 → 2회차: 최종 답변. 공식 문서의 응답 구조를 그대로 재현."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            message = {
                "role": "assistant",
                "content": "",
                "toolCalls": [{
                    "id": "call_mock_001",
                    "type": "function",
                    "function": {"name": "get_product_count",
                                 "arguments": {"product_type": "국내ETF"}},
                }],
            }
            finish = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": "2026-07-11 기준 국내ETF 상품 수는 1,201개입니다.",
            }
            finish = "stop"
        return httpx.Response(200, json={
            "status": {"code": "20000", "message": "OK"},
            "result": {
                "message": message,
                "finishReason": finish,
                "created": 1754400000,
                "usage": {"promptTokens": 100, "completionTokens": 50,
                          "totalTokens": 150},
            },
        })

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description="HCX Function Calling Hello World")
    parser.add_argument("--dry-run", action="store_true",
                        help="API 키 없이 mock 응답으로 전체 FC 루프 시연")
    parser.add_argument("--question", default="국내ETF 상품이 몇 개야?",
                        help="사용자 질문 (기본: 국내ETF 상품 수)")
    parser.add_argument("--model", default=MODEL, choices=sorted(ALLOWED_MODELS),
                        help=f"호출 모델 (기본 {MODEL}. HCX-007 은 서비스 앱 별도 신청 전까지 40100)")
    args = parser.parse_args()

    if args.dry_run:
        print("[dry-run] API 키 없이 MockTransport 로 FC 왕복을 시연합니다.")
        print("[dry-run] 키 발급 후에는 --dry-run 없이 실행하면 같은 코드로 실제 호출됩니다.")
        client = ClovaChatClient(args.model, transport=_mock_transport(),
                                 api_key="DRY-RUN-PLACEHOLDER")
    else:
        # 키 부재 시 여기서 ClovaConfigError 로 즉시 실패(안내 메시지 포함, fallback 금지)
        client = ClovaChatClient(args.model)

    trace = run_fc_loop(client, args.question)
    _print("구조화 결과 (향후 think_trace 원형)", trace)
    print("\nFC 왕복 성공: 도구 호출 → 로컬 실행 → tool 메시지 반환 → 최종 답변")


if __name__ == "__main__":
    main()
