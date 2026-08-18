# -*- coding: utf-8 -*-
"""
CLOVA Studio Chat Completions v3 클라이언트 — HCX 단일 provider 강제

무엇을: HyperCLOVA X(Function Calling 지원 모델 3종)만 호출할 수 있는 HTTP 클라이언트.
왜   : 과제 규정상 "HyperCLOVA X 외 다른 LLM 모델을 사용할 경우 평가대상에서 제외"(실격).
       규정 준수를 문서가 아니라 코드로 강제한다(ROADMAP.md 8/5 교차검증 C표 —
       "HCX 단일 provider 강제": provider allowlist, 설정 누락 시 시작 실패(fallback 금지),
       모델 식별자 감사 로그로 "타 LLM 0건"을 증명).

강제 장치 3종
  1. 모델 allowlist  : {HCX-007, HCX-005, HCX-DASH-002} 외 모델명은 생성 시점에 즉시 예외.
                       (HCX-003, HCX-DASH-001은 FC 미지원 구버전이라 allowlist에서도 제외)
  2. 엔드포인트 검증 : base URL 이 clovastudio.*.ntruss.com 이 아니면 예외 —
                       api.openai.com 등 타 provider 호출을 구조적으로 차단.
  3. 감사 로그       : 모든 호출의 모델 식별자·엔드포인트·타임스탬프를
                       agent/logs/llm_audit.jsonl 에 append (logs/는 .gitignore로 커밋 제외).

API 키: 환경변수 CLOVASTUDIO_API_KEY. 실제 호출 모드에서 부재 시 명확한 안내와 함께
        시작 실패한다. 다른 LLM 으로의 fallback 은 금지(실격 조항)이므로 존재하지 않는다.

API 스펙 출처: https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-fc (2026-08-06 확인)
  - POST https://clovastudio.stream.ntruss.com/v3/chat-completions/{modelName}
  - 헤더: Authorization: Bearer <키>, Content-Type: application/json,
          X-NCP-CLOVASTUDIO-REQUEST-ID(선택)
  - body: messages[], tools[{type:"function", function:{name, description, parameters}}],
          toolChoice("auto"|"none"|객체)
  - 토큰: 표준 모델은 maxTokens, 추론 모델(HCX-007)은 maxCompletionTokens — FC 사용 시 1024 이상 필수
  - 주의: HCX-007 의 추론(thinking) 모드는 "Function calling과 동시 사용 불가"
          → FC 요청에서는 thinking.effort="none" 을 명시해야 한다.
  - 응답: result.message.toolCalls[{id, type, function:{name, arguments(객체)}}],
          result.finishReason == "tool_calls"
"""
import os
import json
import uuid
import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

# --- .env 파일 지원 -------------------------------------------------------
# 저장소 최상위의 .env 를 읽어 환경변수로 올린다(파일이 없으면 아무 일도 안 함).
# 운영체제 환경변수가 이미 있으면 그쪽이 우선이라 기존 설정은 깨지지 않는다.
import sys as _sys
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
from config.env_loader import load_env  # noqa: E402
load_env()
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# Function Calling 지원 HCX 모델만 허용 (ROADMAP.md "HyperCLOVA X 확인 사항" 표)
ALLOWED_MODELS = frozenset({"HCX-007", "HCX-005", "HCX-DASH-002"})

# 추론(reasoning) 모델: maxTokens 대신 maxCompletionTokens 를 사용해야 함
REASONING_MODELS = frozenset({"HCX-007"})

DEFAULT_BASE_URL = "https://clovastudio.stream.ntruss.com"
API_KEY_ENV = "CLOVASTUDIO_API_KEY"

# FC 제약: maxTokens/maxCompletionTokens 는 1024 이상이어야 함 (공식 문서)
MIN_COMPLETION_TOKENS = 1024

HERE = Path(__file__).resolve().parent            # agent/
DEFAULT_AUDIT_PATH = HERE / "logs" / "llm_audit.jsonl"


# ---------------------------------------------------------------------------
# 예외 — 규정 준수 위반은 전용 예외로 구분해 테스트/로그에서 식별 가능하게 한다
# ---------------------------------------------------------------------------

class ClovaComplianceError(RuntimeError):
    """HCX 단일 provider 규정 위반(비허용 모델, 비 clovastudio 엔드포인트)."""


class ClovaConfigError(RuntimeError):
    """설정 오류(API 키 부재 등). fallback 없이 즉시 시작 실패시킨다."""


class ClovaAPIError(RuntimeError):
    """CLOVA Studio API 가 오류 상태 코드를 반환."""


# ---------------------------------------------------------------------------
# 클라이언트
# ---------------------------------------------------------------------------

class ClovaChatClient:
    """Chat Completions v3(Function Calling) 전용 클라이언트.

    Parameters
    ----------
    model      : 모델명. ALLOWED_MODELS 밖이면 ClovaComplianceError (즉시, 호출 전에).
    base_url   : 기본은 공식 엔드포인트. clovastudio 도메인이 아니면 ClovaComplianceError.
    api_key    : 명시하지 않으면 환경변수 CLOVASTUDIO_API_KEY. 둘 다 없으면 ClovaConfigError.
    transport  : httpx transport 주입(테스트의 MockTransport / --dry-run 용).
                 실제 네트워크로 나가는 live 호출은 transport=None 일 때뿐이다.
    audit_path : 감사 로그(jsonl) 경로. 테스트에서 tmp 경로로 대체 가능.
    """

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        audit_path: Path | str = DEFAULT_AUDIT_PATH,
        timeout: float = 60.0,
    ):
        # [강제 1] 모델 allowlist — 타 LLM(GPT 계열 등)과 FC 미지원 구버전 HCX 차단
        if model not in ALLOWED_MODELS:
            raise ClovaComplianceError(
                f"허용되지 않은 모델: {model!r}. "
                f"HyperCLOVA X FC 지원 모델만 사용 가능합니다: {sorted(ALLOWED_MODELS)} "
                "(타 LLM 사용 시 평가대상 제외 — 실격 조항)"
            )

        # [강제 2] 엔드포인트 검증 — api.openai.com 등 구조적 차단
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == "clovastudio.stream.ntruss.com"
            or (host.startswith("clovastudio.") and host.endswith(".ntruss.com"))
        ):
            raise ClovaComplianceError(
                f"허용되지 않은 엔드포인트: {base_url!r}. "
                "CLOVA Studio(clovastudio.*.ntruss.com, https) 외 호출은 금지됩니다 "
                "(HCX 단일 provider 강제)"
            )

        # [강제 3] API 키 — 부재 시 시작 실패. 타 LLM fallback 은 존재하지 않는다.
        self._api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self._api_key:
            raise ClovaConfigError(
                f"CLOVA Studio API 키가 없습니다. 환경변수 {API_KEY_ENV} 를 설정하세요.\n"
                "  발급: 네이버클라우드 콘솔 > CLOVA Studio > API 키 (테스트/서비스 앱)\n"
                "  키 없이 구조만 검증하려면: python agent/fc_hello.py --dry-run\n"
                "  (규정상 다른 LLM 으로의 fallback 은 불가 — 실격 조항)"
            )

        self.model = model
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout
        self._audit_path = Path(audit_path)

    # -- 요청 구성 ----------------------------------------------------------

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v3/chat-completions/{self.model}"

    def build_request(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict = "auto",
        max_completion_tokens: int = MIN_COMPLETION_TOKENS,
        request_id: str | None = None,
    ) -> tuple[str, dict, dict]:
        """(url, headers, body) 를 만들되 전송은 하지 않는다 — --dry-run 이 이 출력물을 검증.

        토큰 파라미터: HCX-007 은 추론 모델이라 maxCompletionTokens, 그 외는 maxTokens.
        FC 제약으로 둘 다 1024 이상이어야 한다(문서 명시).
        """
        if max_completion_tokens < MIN_COMPLETION_TOKENS:
            raise ValueError(
                f"Function Calling 은 max(Completion)Tokens >= {MIN_COMPLETION_TOKENS} 필요 "
                f"(요청값 {max_completion_tokens})"
            )

        body: dict = {"messages": messages}
        if tools:
            body["tools"] = tools
            body["toolChoice"] = tool_choice

        if self.model in REASONING_MODELS:
            body["maxCompletionTokens"] = max_completion_tokens
            # 공식 문서: 추론(thinking)은 "Function calling과 동시 사용 불가"
            # → FC 요청에서는 반드시 effort="none" 으로 비활성화한다.
            if tools:
                body["thinking"] = {"effort": "none"}
        else:
            body["maxTokens"] = max_completion_tokens

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id or str(uuid.uuid4()),
        }
        return self.endpoint, headers, body

    # -- 호출 ---------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | dict = "auto",
        max_completion_tokens: int = MIN_COMPLETION_TOKENS,
        request_id: str | None = None,
    ) -> dict:
        """1회 chat-completions 호출. 성공 시 응답 JSON 전체(dict)를 반환.

        모든 호출은 성공/실패와 무관하게 감사 로그에 기록된다(강제 3).
        """
        url, headers, body = self.build_request(
            messages, tools, tool_choice, max_completion_tokens, request_id
        )
        mode = "live" if self._transport is None else "mock"
        audit = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider": "clova_studio",          # 이 값이 항상 단일임이 "타 LLM 0건"의 증거
            "model": self.model,
            "endpoint": url,
            "request_id": headers["X-NCP-CLOVASTUDIO-REQUEST-ID"],
            "mode": mode,
        }
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as http:
                resp = http.post(url, headers=headers, json=body)
            audit["http_status"] = resp.status_code
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:                  # 실패도 감사 로그에 남긴다
            audit["error"] = f"{type(exc).__name__}: {exc}"
            self._write_audit(audit)
            raise
        # CLOVA Studio 응답 규약: status.code == "20000" 이 성공
        status = data.get("status", {})
        audit["status_code"] = status.get("code")
        self._write_audit(audit)
        if status.get("code") != "20000":
            raise ClovaAPIError(f"CLOVA Studio 오류 응답: {status}")
        return data

    # -- 감사 로그 ----------------------------------------------------------

    def _write_audit(self, entry: dict) -> None:
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
