# -*- coding: utf-8 -*-
"""
CLOVA Studio Embedding v2 클라이언트 — 벡터 채널(의미 검색)의 임베딩 공급자.

무엇을: 텍스트 1건 → 1024차원 벡터. 해외ETF `cu_strtegy` 서술 인덱싱과
        질의 시점 쿼리 임베딩에 공통으로 쓴다(같은 모델이어야 공간이 일치).
왜   : 8/6 설명회·팀 결정 — 임베딩은 CLOVA Embedding v2(보수적 선택, 1024차원).
        규정상 외부 LLM 금지이므로 임베딩도 CLOVA Studio 도구만 쓴다
        (엔드포인트 도메인 검증 + 감사 로그는 clova_client 와 동일 원칙).

엔드포인트: 문서 기준 /v1/api-tools/embedding/v2 이나 계정·앱 유형에 따라
        /testapp/... /serviceapp/... 변형이 있어(8/13 chat 실측과 동일 패턴)
        첫 호출에서 후보를 순회해 유효한 경로를 자동 채택한다.

API 키: 환경변수 CLOVASTUDIO_API_KEY (clova_client 와 동일).
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

DEFAULT_BASE_URL = "https://clovastudio.stream.ntruss.com"
API_KEY_ENV = "CLOVASTUDIO_API_KEY"

# 첫 호출에서 순회하는 경로 후보 — 유효 응답(20000)을 주는 경로를 고정한다.
ENDPOINT_CANDIDATES = (
    "/v1/api-tools/embedding/v2",
    "/testapp/v1/api-tools/embedding/v2",
    "/serviceapp/v1/api-tools/embedding/v2",
)

EMBEDDING_DIM = 1024   # Embedding v2 고정 차원 — 응답 검증에 사용

HERE = Path(__file__).resolve().parent
DEFAULT_AUDIT_PATH = HERE / "logs" / "llm_audit.jsonl"   # chat 과 같은 감사 로그 파일


class ClovaEmbeddingError(RuntimeError):
    """Embedding v2 오류 응답 또는 후보 경로 전체 실패."""


class ClovaEmbeddingClient:
    """Embedding v2 전용 클라이언트 — clova_client 와 같은 도메인 검증·감사 로그 원칙.

    transport 주입 시 mock 모드(테스트)이며, 실제 네트워크 호출은 transport=None 뿐이다.
    """

    def __init__(self, base_url=DEFAULT_BASE_URL, api_key=None,
                 transport=None, audit_path=DEFAULT_AUDIT_PATH, timeout=30.0):
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == "clovastudio.stream.ntruss.com"
            or (host.startswith("clovastudio.") and host.endswith(".ntruss.com"))
        ):
            raise ClovaEmbeddingError(
                f"허용되지 않은 엔드포인트: {base_url!r} — CLOVA Studio 도메인만 허용")
        self._api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self._api_key:
            raise ClovaEmbeddingError(
                f"API 키가 없습니다. 환경변수 {API_KEY_ENV} 를 설정하세요.")
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout
        self._audit_path = Path(audit_path)
        self._endpoint_path = None       # 첫 성공 시 고정

    # -- 호출 ---------------------------------------------------------------

    def embed(self, text, request_id=None):
        """텍스트 1건 → (vector[1024], input_tokens). 오류는 예외로 던진다."""
        candidates = [self._endpoint_path] if self._endpoint_path else list(ENDPOINT_CANDIDATES)
        last_err = None
        for path in candidates:
            try:
                vec, tokens = self._call(path, text, request_id)
            except ClovaEmbeddingError as e:
                last_err = e
                continue
            self._endpoint_path = path   # 유효 경로 고정 — 이후 호출은 1회만
            return vec, tokens
        msg = str(last_err)
        if "40100" in msg or "No Service App" in msg:
            # 8/13 실측: 전 경로 40100 — 계정의 서비스 앱이 HCX-005 채팅 전용이라
            # 임베딩 도구용 서비스 앱을 콘솔에서 추가 신청해야 한다(사용자 액션).
            raise ClovaEmbeddingError(
                "임베딩용 서비스 앱이 없다(40100) — NCP 콘솔 > CLOVA Studio 에서 "
                "임베딩 v2 서비스 앱을 추가 신청할 것 (HCX-005 신청과 동일 절차). "
                f"원본 오류: {msg}")
        raise ClovaEmbeddingError(f"Embedding v2 호출 실패(전 후보): {msg}")

    def _call(self, path, text, request_id=None):
        url = self.base_url + path
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id or str(uuid.uuid4()),
        }
        audit = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "provider": "clova_studio", "model": "embedding-v2", "endpoint": url,
            "request_id": headers["X-NCP-CLOVASTUDIO-REQUEST-ID"],
            "mode": "live" if self._transport is None else "mock",
        }
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as http:
                resp = http.post(url, headers=headers, json={"text": text})
            audit["http_status"] = resp.status_code
            if resp.status_code != 200:      # 404(경로 변형)·401(앱 범위) 등 — 다음 후보 시도
                body = resp.text[:200]
                self._write_audit(audit)
                raise ClovaEmbeddingError(f"HTTP {resp.status_code} @{path}: {body}")
            data = resp.json()
        except ClovaEmbeddingError:
            raise
        except Exception as exc:             # 네트워크 오류 등 — 호출자에서 재시도 판단
            audit["error"] = f"{type(exc).__name__}: {exc}"
            self._write_audit(audit)
            raise ClovaEmbeddingError(f"전송 실패 @{path}: {type(exc).__name__}: {exc}")
        status = data.get("status", {})
        audit["status_code"] = status.get("code")
        self._write_audit(audit)
        if status.get("code") != "20000":
            raise ClovaEmbeddingError(f"오류 응답 @{path}: {status}")
        result = data.get("result", {})
        vec = result.get("embedding")
        if not isinstance(vec, list) or len(vec) != EMBEDDING_DIM:
            raise ClovaEmbeddingError(
                f"벡터 차원 이상: {len(vec) if isinstance(vec, list) else type(vec)}")
        return vec, result.get("inputTokens")

    # -- 감사 로그 ----------------------------------------------------------

    def _write_audit(self, entry):
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
