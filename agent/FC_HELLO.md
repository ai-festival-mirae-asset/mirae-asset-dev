# HCX-007 Function Calling "Hello World" — Sprint 0 DoD

작성일 2026-08-06 · 브랜치 papuagigi · **8/7 재검증**: 공식 문서 재확인(스펙 일치) + pytest 14개·`--dry-run` 재통과, 8/6 설명회 확정 사항(HCX 제약 완화·크레딧) 반영

## 1. 무엇을 했고 왜 했는가

**무엇을**: CLOVA Studio HCX-007 에 더미 도구 1개(`get_product_count`)를 JSON Schema 로
정의해 Function Calling 왕복 — 모델의 도구 호출 → 로컬 실행 → `tool` 역할 메시지 반환 →
최종 답변 — 이 되는 최소 코드를 만들었다.

**왜**: ROADMAP.md Sprint 0 완료 기준(DoD) "HCX-007이 우리가 정의한 더미 함수를 호출해
응답을 돌려줬다"의 구현이다. 이 왕복 루프가 S2 에이전트(파라미터화 도구 호출)의 최소
원형이고, `tool_trace`(도구명/파라미터/결과 구조화 수집)는 향후 `think_trace` 의 원형이다.

**제약**: 작성 시점(8/6)에 CLOVA Studio API 키가 없다(네이버클라우드 계정 신청 전).
따라서 키 없이도 검증 가능한 구조로 만들었다 — mock 테스트(pytest) + `--dry-run`.
dry-run 과 실제 호출은 **완전히 같은 코드 경로**(payload 구성 → httpx 전송 → 응답 파싱 →
루프)를 지나고, transport 만 MockTransport 로 바뀐다. 키가 생기면 명령 한 줄로 실증한다.

## 2. 왜 provider 강제인가 (실격 조항)

과제소개서: *"LLM은 HyperCLOVA X만 사용 가능합니다. HyperCLOVA X 외 다른 LLM 모델을
사용할 경우, 평가대상에서 제외됩니다."*

**[8/7 갱신]** 8/6 설명회에서 취지가 공식 완화됐다(ROADMAP.md "타 LLM 사용 가능 여부
판정" 표): **최종 답변 생성·메인 에이전트 구성은 HCX 고정(필수)**, 데이터 전처리·비정형
분석·KG 구축 등 오프라인 단계는 자유. 이 클라이언트가 담당하는 구간이 바로 그 "메인
에이전트"(평가 API가 실행하는 온라인 경로: 질의 이해 → 도구 호출 → 답변 생성)이므로
**provider 강제는 완화 이후에도 그대로 유효하다.** 제출물이 소스코드 전체라 타 provider
호출은 그대로 노출되고, 감사 로그는 "온라인 경로 타 LLM 0건"의 증빙이 된다.

그래서 규정 준수를 문서가 아니라 **코드로 강제**한다(8/5 브랜치 교차검증 C표
"HCX 단일 provider 강제" 채택 항목). `agent/clova_client.py` 의 장치 3종:

| 장치 | 내용 | 위반 시 |
|---|---|---|
| 모델 allowlist | `{HCX-007, HCX-005, HCX-DASH-002}` — FC 지원 HCX만. GPT·Gemini 는 물론 FC 미지원 구버전(HCX-003, HCX-DASH-001)도 차단 | `ClovaComplianceError` (생성 시점 즉시) |
| 엔드포인트 검증 | base URL 이 `https://clovastudio.*.ntruss.com` 이 아니면 차단 — `api.openai.com` 등 구조적 차단, 도메인 위장(`...ntruss.com.evil.io`)·http 다운그레이드도 거부 | `ClovaComplianceError` |
| API 키 fail-fast | `CLOVASTUDIO_API_KEY` 부재 시 발급 절차 안내와 함께 즉시 시작 실패. **타 LLM fallback 경로는 존재하지 않는다** | `ClovaConfigError` |

추가로 모든 호출의 `provider/model/endpoint/timestamp/request_id` 를
`agent/logs/llm_audit.jsonl` 에 append 한다 — 평가·제출 시 **"타 LLM 호출 0건"을 로그로
증명**할 수 있다(정성평가 리스크 관리 어필 요소). `agent/logs/` 는 자체 `.gitignore`(`*`)로
커밋에서 제외된다.

## 3. 실행법

```bash
# 1) 키 없이 — 구조 검증 + 전체 루프 시연 (지금 가능)
python agent/fc_hello.py --dry-run
python -m pytest tests/test_fc_hello.py        # 14개 테스트

# 2) 키 발급 후 — 실제 호출 실증 (명령 한 줄)
#    네이버클라우드 콘솔 > CLOVA Studio > API 키 발급(테스트 앱) 후:
#    (PowerShell) $env:CLOVASTUDIO_API_KEY = "<발급받은 키>"
#    (bash)       export CLOVASTUDIO_API_KEY="<발급받은 키>"
python agent/fc_hello.py
# 성공 기준: "FC 왕복 성공" 출력 + tool_trace 에 get_product_count 실행 기록
# + agent/logs/llm_audit.jsonl 에 mode:"live" 항목
```

## 4. 확인된 API 스펙과 출처

출처: 공식 문서 <https://api.ncloud-docs.com/docs/clovastudio-chatcompletionsv3-fc>
(2026-08-06 WebFetch 확인 → **2026-08-07 재확인**: 엔드포인트·헤더·`tools`/`toolChoice`
표기·1024 토큰 하한·`toolCalls`(arguments=객체)·`toolCallId`·thinking 동시 사용 불가·
`status.code "20000"` 전부 구현과 일치. ROADMAP.md "HyperCLOVA X 확인 사항" 표와도 일치)

| 항목 | 확인 내용 |
|---|---|
| 엔드포인트 | `POST https://clovastudio.stream.ntruss.com/v3/chat-completions/{modelName}` |
| 헤더 | `Authorization: Bearer <API키>` · `Content-Type: application/json` · `X-NCP-CLOVASTUDIO-REQUEST-ID`(추적용, 예제에 항상 등장 — 매 요청 uuid 로 전송) |
| tools | `[{type:"function", function:{name, description, parameters(JSON Schema)}}]` |
| toolChoice | `"auto"` \| `"none"` \| 특정 함수 지정 객체 |
| 토큰 파라미터 | 표준 모델 `maxTokens`, **추론 모델(HCX-007)은 `maxCompletionTokens`** — FC 사용 시 둘 다 **1024 이상 필수** (코드에서 미달 시 `ValueError`) |
| **thinking 주의** | HCX-007 의 추론 모드(`thinking.effort`)는 **"Function calling과 동시 사용 불가"**(문서 원문) → FC 요청에는 `thinking: {"effort": "none"}` 을 명시해 비활성화 (`clova_client.py` 가 자동 처리) |
| toolCalls 응답 | `result.message.toolCalls[{id, type:"function", function:{name, arguments}}]` — `arguments` 는 **객체**(OpenAI 호환 API 는 문자열일 수 있어 코드에서 방어 파싱) · `result.finishReason == "tool_calls"` |
| 도구 결과 반환 | assistant 메시지(toolCalls 포함)를 대화에 되돌려 넣고, 이어서 `{role:"tool", toolCallId:<응답의 id>, content:<실행 결과 문자열>}` 추가 후 재요청 |
| 성공 판정 | 응답 `status.code == "20000"` |
| FC 지원 모델 | HCX-007 / HCX-005 / HCX-DASH-002 (Chat Completions v3 + OpenAI 호환 API 만. 튜닝 모델 미지원) |

## 5. 파일 구성

| 파일 | 역할 |
|---|---|
| `agent/clova_client.py` | HCX 단일 provider 강제 클라이언트 (allowlist·엔드포인트 검증·키 fail-fast·감사 로그) |
| `agent/fc_hello.py` | 더미 도구 정의 + FC 왕복 루프 + `--dry-run` |
| `agent/logs/.gitignore` | 감사 로그 커밋 제외 (`*`, `.gitignore` 자신만 추적) |
| `tests/test_fc_hello.py` | httpx.MockTransport 기반 14개 테스트 (payload 규약 · 왕복 루프 · 규정 준수 예외 3종 · 감사 로그) |

의존성: `httpx`(설치돼 있음, 0.28.1 확인) · `pytest`(8.3.4). 신규 설치 없음.

## 6. 남은 확인 사항

- **[미실증] 실제 API 호출**: 키 발급 전이라 실 왕복은 미실증. 계정 생성 후 3절의
  명령 한 줄로 실증하고 이 문서에 결과를 기록할 것.
- **[8/7 갱신] 크레딧 확정·레이트리밋 일부 해소**: 크레딧은 8/6 설명회에서 확정 —
  20만원 + 웰컴 10만원, **유효기간 9/30**(만료 후 잔존 리소스 과금 주의). 평가는
  **순차 호출**(동시 아님) 확인으로 레이트리밋 부담 완화. 단 RPM/TPM 수치 자체는
  여전히 미확인 — 디스코드 Q&A 로 확인.
- **임베딩 회색지대**: 8/6 완화 발언이 임베딩을 명시하지 않음 — Embedding v2 로 통일해
  논란 자체를 없애는 보수적 방향 유지(ROADMAP.md 판정표).
- **[8/8 총검토] HCX-005 vs HCX-007 역할 분담**: 설명회에서 "가급적 HCX-005 권장"
  (튜닝 유리) 발언 — 모델 분담은 8/8 결정. 이 클라이언트의 allowlist 는 FC 지원 3종을
  모두 포함하므로 **모델명 결정만 바꾸면 되고 코드 변경은 불필요**.
- **`X-NCP-CLOVASTUDIO-REQUEST-ID` 필수 여부**: 문서 헤더 표에 필수 표기가 명확하지
  않으나 모든 예제에 등장 — 항상 보내는 쪽으로 구현(무해).
- **thinking 비활성 시 HCX-007 FC 품질**: 추론 꺼진 상태의 도구 선택 정확도는 실측
  필요. 미흡하면 라우팅(HCX-DASH-002) + 메인(HCX-007) 분담 재검토.
