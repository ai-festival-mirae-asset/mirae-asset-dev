# -*- coding: utf-8 -*-
"""
FC Hello World 검증 — httpx.MockTransport 로 네트워크 없이 전 구간 테스트

무엇을: ① 요청에 모델명·tools 스키마·maxCompletionTokens>=1024 가 실리는지
        ② toolCalls 응답 → 로컬 실행 → tool 메시지 재전송 루프가 도는지
        ③ allowlist 밖 모델 / 비 clovastudio URL / 키 부재가 각각 예외를 내는지
왜   : CLOVA Studio API 키 발급 전(계정 신청 전)에도 Sprint 0 DoD 코드의 구조를
        검증 가능하게 하기 위함. 키가 생기면 `python agent/fc_hello.py` 로 실증한다.

실행 : pytest tests/test_fc_hello.py  (repo 루트에서)
"""
import sys
import json
from pathlib import Path

import httpx
import pytest

# repo 루트를 sys.path 에 추가 — 패키지 설치 없이 agent/ 를 import
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.clova_client import (  # noqa: E402
    ClovaChatClient,
    ClovaComplianceError,
    ClovaConfigError,
    API_KEY_ENV,
)
from agent import fc_hello  # noqa: E402


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _ok_response(message: dict, finish: str) -> httpx.Response:
    """공식 문서(chatcompletionsv3-fc)의 응답 구조를 재현한다."""
    return httpx.Response(200, json={
        "status": {"code": "20000", "message": "OK"},
        "result": {"message": message, "finishReason": finish,
                   "usage": {"promptTokens": 1, "completionTokens": 1,
                             "totalTokens": 2}},
    })


def _make_client(handler, tmp_path: Path) -> ClovaChatClient:
    return ClovaChatClient(
        "HCX-007",
        transport=httpx.MockTransport(handler),
        api_key="TEST-KEY",
        audit_path=tmp_path / "llm_audit.jsonl",
    )


# ---------------------------------------------------------------------------
# ① 요청 payload 검증
# ---------------------------------------------------------------------------

def test_request_carries_model_tools_and_min_tokens(tmp_path):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return _ok_response({"role": "assistant", "content": "안녕하세요"}, "stop")

    client = _make_client(handler, tmp_path)
    client.chat([{"role": "user", "content": "hi"}], tools=fc_hello.TOOLS)

    # 엔드포인트 경로에 모델명
    assert captured["url"] == (
        "https://clovastudio.stream.ntruss.com/v3/chat-completions/HCX-007"
    )
    # 헤더: Bearer 인증 + 요청 추적 ID
    assert captured["headers"]["Authorization"] == "Bearer TEST-KEY"
    assert captured["headers"].get("X-NCP-CLOVASTUDIO-REQUEST-ID")

    body = captured["body"]
    # tools 스키마(JSON Schema)가 문서 규약대로 실림
    assert body["tools"][0]["type"] == "function"
    fn = body["tools"][0]["function"]
    assert fn["name"] == "get_product_count"
    assert fn["parameters"]["properties"]["product_type"]["type"] == "string"
    assert body["toolChoice"] == "auto"
    # HCX-007(추론 모델): maxCompletionTokens >= 1024, thinking 비활성(FC와 동시 사용 불가)
    assert "maxTokens" not in body
    assert body["maxCompletionTokens"] >= 1024
    assert body["thinking"] == {"effort": "none"}


def test_tokens_below_1024_rejected(tmp_path):
    client = _make_client(lambda r: _ok_response({}, "stop"), tmp_path)
    with pytest.raises(ValueError):
        client.build_request([{"role": "user", "content": "hi"}],
                             tools=fc_hello.TOOLS, max_completion_tokens=512)


# ---------------------------------------------------------------------------
# ② FC 왕복 루프: toolCalls → 로컬 실행 → tool 메시지 재전송 → 최종 답변
# ---------------------------------------------------------------------------

def test_full_function_calling_round_trip(tmp_path, capsys):
    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests_seen.append(body)
        if len(requests_seen) == 1:
            return _ok_response(
                {"role": "assistant", "content": "",
                 "toolCalls": [{
                     "id": "call_test_001", "type": "function",
                     "function": {"name": "get_product_count",
                                  "arguments": {"product_type": "채권"}},
                 }]},
                "tool_calls",
            )
        return _ok_response(
            {"role": "assistant", "content": "채권은 42,394건입니다."}, "stop")

    client = _make_client(handler, tmp_path)
    trace = fc_hello.run_fc_loop(client, "채권 몇 개야?")

    # 왕복 2회(도구 호출 → 최종 답변)
    assert trace["rounds"] == 2
    assert trace["final_answer"] == "채권은 42,394건입니다."

    # 로컬 도구가 모델이 준 파라미터로 실행됨 (향후 think_trace 원형)
    assert trace["tool_trace"] == [{
        "tool_call_id": "call_test_001",
        "tool_name": "get_product_count",
        "arguments": {"product_type": "채권"},
        "result": {"product_type": "채권", "count": 42394, "as_of": "2026-07-11"},
    }]

    # 2회차 요청에 assistant(toolCalls) + tool 역할 메시지가 문서 규약대로 실림
    second_msgs = requests_seen[1]["messages"]
    assistant_msg = second_msgs[-2]
    tool_msg = second_msgs[-1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["toolCalls"][0]["id"] == "call_test_001"
    assert tool_msg["role"] == "tool"
    assert tool_msg["toolCallId"] == "call_test_001"
    assert json.loads(tool_msg["content"])["count"] == 42394


def test_dry_run_mode_completes(tmp_path, monkeypatch):
    """--dry-run 과 동일한 경로: mock transport 로 키 없이 전체 루프가 돈다."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    client = ClovaChatClient(
        "HCX-007", transport=fc_hello._mock_transport(),
        api_key="DRY-RUN-PLACEHOLDER", audit_path=tmp_path / "audit.jsonl")
    trace = fc_hello.run_fc_loop(client, "국내ETF 상품이 몇 개야?")
    assert trace["final_answer"]
    assert trace["tool_trace"][0]["tool_name"] == "get_product_count"


# ---------------------------------------------------------------------------
# ③ 규정 준수 강제: allowlist 밖 모델 / 비 clovastudio URL / 키 부재
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_model", [
    "gpt-4o",          # 타 LLM — 실격 조항
    "gemini-2.0",      # 타 LLM
    "HCX-003",         # HCX 이지만 FC 미지원 구버전
    "HCX-DASH-001",    # HCX 이지만 FC 미지원 구버전
])
def test_non_allowlisted_model_rejected(bad_model):
    with pytest.raises(ClovaComplianceError):
        ClovaChatClient(bad_model, api_key="TEST-KEY")


@pytest.mark.parametrize("bad_url", [
    "https://api.openai.com/v1",                    # 구조적 차단 대상
    "https://generativelanguage.googleapis.com",
    "http://clovastudio.stream.ntruss.com",          # https 강제
    "https://clovastudio.stream.ntruss.com.evil.io", # 도메인 위장
])
def test_non_clovastudio_base_url_rejected(bad_url):
    with pytest.raises(ClovaComplianceError):
        ClovaChatClient("HCX-007", base_url=bad_url, api_key="TEST-KEY")


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(ClovaConfigError) as ei:
        ClovaChatClient("HCX-007")
    assert API_KEY_ENV in str(ei.value)   # 안내 메시지에 환경변수명 포함


# ---------------------------------------------------------------------------
# 감사 로그: 모든 호출의 모델·엔드포인트·타임스탬프가 jsonl 로 남는다
# ---------------------------------------------------------------------------

def test_audit_log_written(tmp_path):
    client = _make_client(
        lambda r: _ok_response({"role": "assistant", "content": "ok"}, "stop"),
        tmp_path)
    client.chat([{"role": "user", "content": "hi"}])
    lines = (tmp_path / "llm_audit.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["provider"] == "clova_studio"
    assert entry["model"] == "HCX-007"
    assert entry["endpoint"].startswith("https://clovastudio.stream.ntruss.com/")
    assert entry["ts"] and entry["status_code"] == "20000"
