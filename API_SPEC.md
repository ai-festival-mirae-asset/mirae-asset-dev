# API 명세서 — 평가용 `GET /answer`

> **이 문서는**: 대회 제출 필수 항목인 **"평가용 API 서버 정보 = 접속 주소(End-point URL) + API 명세서(요청/응답 JSON 스키마)"** 입니다. 채점 프로그램이 우리 서버를 어떻게 부르고, 우리 서버가 무엇을 돌려주는지를 적습니다.
> 규격의 출처는 주최 공식 자료 두 가지 — `AI페스티벌_과제설명_금융상품Agent_참여자공유용.pdf` p.11(요청/응답 상세)과 8/13 배포 안내문 `API 호출 관련 참고.txt`(호출 방식·서버 구성) — 이며, 아래 예시 응답은 **실제 서버가 낸 값**을 그대로 붙였습니다(2026-08-18, AI 호출 없는 가벼운 구성).
> 모르는 용어는 [GLOSSARY.md](GLOSSARY.md), 서버 실행법은 [README.md](README.md) §5.

## 1. 접속 정보

| 항목 | 값 |
|---|---|
| **End-point URL** | `http://<공인 IP>/answer` — **NCP 배포(구현 순서 ⑧) 후 확정해서 이 칸과 README.md 에 적는다.** 개발 중에는 `http://127.0.0.1:8000/answer` |
| 프로토콜 · 포트 | HTTP · 80 (주최 안내: HTTP 기본, HTTPS 는 선택. 도메인 없이 공인 IP 제출 가능) |
| 인증 | 없음 — 인증 헤더·API 키·POST 본문을 쓰지 않는다 |
| 접근 제어 | 기본 전체 허용. 주최가 발신 IP 대역을 공지하면 방화벽(ACG)에서 그 대역만 허용할 수 있다(선택) |
| 운영 기간 | 예선 평가 **2026-09-07 ~ 09-20 상시 가동**(주최: "상시 활성". 실제 호출은 이 중 별도 공지 기간, 주제별 최대 1주) |
| 문자 인코딩 | 요청 파라미터는 URL 인코딩(UTF-8), 응답은 UTF-8 JSON |

## 2. 요청 — `GET /answer`

| 파라미터 | 타입 | 필수 | 설명 | 빠졌을 때 우리 서버의 동작 |
|---|---|---|---|---|
| `question_id` | string | 필수 | 주최가 부여한 문항 번호 (예: `Q-001`) | 빈 문자열로 처리하고 정상 응답(오류 아님) |
| `question` | string | 필수 | 평가 질의 원문 (URL 인코딩) | "질문이 비어 있습니다 …" 안내를 `answer` 에 담아 200 응답 |

- 규격에 없는 파라미터(예: `&foo=bar`)가 붙어 와도 **무시하고 정상 처리**한다 — 주최 요구사항 "미정의 파라미터가 들어와도 500 없이 처리"를 자동 테스트로 잠가 두었다(`tests/test_server.py`).
- 같은 질문이 다시 오면 캐시된 정상 응답을 즉시 돌려준다(`think_trace` 끝에 `(캐시 응답)` 표시). 실패·강등된 응답은 캐시하지 않으므로, 주최의 재시도(타임아웃·5xx 시 최대 2회)는 항상 새로 계산된다.

**요청 예시**

```bash
curl -G "http://<공인 IP>/answer" --data-urlencode "question_id=Q-001" --data-urlencode "question=순자산총액 기준으로 국내 ETF 상위 5개 알려줘"
```

```python
import requests
r = requests.get("http://<공인 IP>/answer",
                 params={"question_id": "Q-001", "question": "순자산총액 기준으로 국내 ETF 상위 5개 알려줘"},
                 timeout=300)
print(r.json())
```

## 3. 응답 — `200 OK`, `Content-Type: application/json; charset=utf-8`

JSON 객체 하나. **필드 5개, 값은 전부 문자열(string)** — 공식 규격 그대로이며, 확인 불가(거절) 응답도 오류 응답도 같은 5필드를 유지한다.

| 필드 | 타입 | 내용 | 우리 서버가 채우는 방식 |
|---|---|---|---|
| `question_id` | string | 요청값 그대로 | 요청의 `question_id` (없으면 `""`) |
| `question` | string | 요청값 그대로 | 요청의 `question` 을 공백까지 그대로 |
| `retrieved_context` | string | 답변 근거로 참조한 데이터 | 근거 1건당 한 줄 — `[근거N \| 출처: 테이블ID \| 키: 행 키 \| 채널: sql/graph/vector/keyword/validation \| 기준일: YYYY-MM-DD] 컬럼=값 · 컬럼=값 …` (세로줄 `\|` 는 구분 기호). 근거가 없으면 `(근거 없음)`. 확인 불가 판정 자체도 `출처: validation` 근거로 남긴다 |
| `think_trace` | string | 사고·추론·도구 사용 과정 | 줄 단위 기록 — 처리 단계(`stage=rule` 규칙 처리 / `stage=llm` HCX 계획), 의도(`intent`), 답변 태도(`behavior=answer/partial/refuse`), 연결한 개체(`grounded`), 실행한 조회(`call 채널.조회명 {조건}`), 5중 검문 결과(`검문[value/existence/time/field/coverage] pass|refuse|partial`), 해석 노트(`note:`), 마지막 줄 `응답 시간: N초` |
| `answer` | string | 최종 답변 | 근거로 확인된 사실만으로 작성. 확인 불가일 때는 정해진 문장 `요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다.` 로 시작하고 사유를 붙인다. 답변 끝에 데이터 기준일(마스터 2026-07-11 · 구성종목 2026-07-10)을 항상 표기 |

**응답 JSON 스키마** (JSON Schema 표기)

```json
{
  "type": "object",
  "required": ["question_id", "question", "retrieved_context", "think_trace", "answer"],
  "properties": {
    "question_id":       {"type": "string"},
    "question":          {"type": "string"},
    "retrieved_context": {"type": "string"},
    "think_trace":       {"type": "string"},
    "answer":            {"type": "string"}
  },
  "additionalProperties": false
}
```

### 3.1 응답 예시 ① — 정상 답변 (실제 출력)

```json
{
  "question_id": "Q-001",
  "question": "순자산총액 기준으로 국내 ETF 상위 5개 알려줘",
  "retrieved_context": "[근거1 | 출처: PREF01N001 | 키: KR7069500007 | 채널: sql | 기준일: 2026-07-11] pd_itm_no=KR7069500007 · pd_abrv_nm=KODEX 200 · pd_net_tamt=28359162282520.0 · cu_fund_mgmt_co=삼성\n[근거2 | 출처: PREF01N001 | 키: KR7360750004 | 채널: sql | 기준일: 2026-07-11] pd_itm_no=KR7360750004 · pd_abrv_nm=TIGER 미국S&P500 · pd_net_tamt=19057036378642.0 · cu_fund_mgmt_co=미래에셋\n… (근거3~5 생략)",
  "think_trace": "stage=rule intent=etp_ranking behavior=answer(라우터 힌트 answer)\ncall sql.etp_top_aum {'instrument_type': 'ETF', 'limit': 5}\n검문[value] pass\n검문[existence] pass\n검문[time] pass\n검문[field] pass\n검문[coverage] pass\nnote: 상장중(active) 기준 · ETF/ETN 구분 적용\n응답 시간: 3.96초",
  "answer": "[etp_top_aum] 결과 5건\n  1. KODEX 200 (pd_net_tamt=28359162282520.0 · cu_fund_mgmt_co=삼성)\n  2. TIGER 미국S&P500 (pd_net_tamt=19057036378642.0 · cu_fund_mgmt_co=미래에셋)\n  3. TIGER 반도체TOP10 (…)\n  4. TIGER 200 (…)\n  5. TIGER 미국나스닥100 (…)\n\n※ 상장중(active) 기준 · ETF/ETN 구분 적용\n(데이터 기준일: 마스터 2026-07-11 · 구성종목 2026-07-10)"
}
```

(위 예시는 AI 생성기를 끈 가벼운 구성이라 답변이 규칙 요약 형태다. 실제 운영 구성에서는 같은 근거로 HCX-005 가 문장을 다듬고, 근거에 없는 상품명·숫자가 섞이면 사후 대조가 그 줄을 삭제한다.)

### 3.2 응답 예시 ② — 확인 불가(답변 불가 문항) (실제 출력)

```json
{
  "question_id": "Q-002",
  "question": "신용등급 AAAA인 채권 찾아줘",
  "retrieved_context": "[근거1 | 출처: validation | 키: value | 채널: validation | 기준일: 2026-07-11] 판정=확인 불가 · 사유='AAAA'는 신용등급 체계(AAA~D)에 존재하지 않는 표기",
  "think_trace": "stage=rule intent=invalid_value behavior=refuse(라우터 힌트 refuse)\n검문[value] refuse — 'AAAA'는 신용등급 체계(AAA~D)에 존재하지 않는 표기\n검문[existence] pass\n검문[time] pass\n검문[field] pass\n검문[coverage] pass\n응답 시간: 0.13초",
  "answer": "요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다.\n- 사유: 'AAAA'는 신용등급 체계(AAA~D)에 존재하지 않는 표기\n(데이터 기준일: 마스터 2026-07-11 · 구성종목 2026-07-10)"
}
```

같은 형식으로 `Kimi 관련 투자상품 있어?` 는 `- 사유: 'Kimi'로 식별되는 상품·종목이 기준일 데이터에 없음(이름 일부가 겹치는 후보도 0건 — 간접 연상으로 답하지 않음)` 을 돌려준다. 확인 불가 문항에서는 AI 생성기를 호출하지 않는다(거절문은 정해진 틀만 사용).

## 4. 오류·예외 상황의 동작 (전부 HTTP 200 + 5필드 유지)

| 상황 | 동작 |
|---|---|
| 내부 오류(예외) | `answer` = "일시적인 내부 오류로 이번 요청을 처리하지 못했습니다. 같은 질문으로 다시 시도해 주세요.", `think_trace` 에 `전역 오류: …` 기록. 캐시하지 않음 |
| 처리 시간 초과 | 요청마다 시간 예산을 둔다 — 의미 검색이 6초, AI 문장 생성이 8초를 넘기면 그 단계를 건너뛰고 더 가벼운 방법으로 답한다(강등, `think_trace` 에 기록). 내부 목표 15초, 주최 권장 60초, 주최 타임아웃 300초 |
| 빈 질문 / 파라미터 누락 | §2 표 참조 — 200 + 안내 문구 |
| 규격 밖 파라미터 | 무시 |
| 5xx | 설계상 발생하지 않음(전역 예외 처리) — 만약 서버 프로세스 자체가 죽으면 systemd 가 자동 재기동(배포 가이드 `infra/NCP_SERVER_SETUP.md`) |

## 5. 보조 주소 (평가 대상 아님, 운영 확인용)

| 주소 | 용도 |
|---|---|
| `GET /health` | 서버 상태 JSON — 예: `{"status":"ok","db":true,"index_entries":119698,"graph_triples":…,"vector":…,"hcx_router":…,"hcx_generator":…,"cache_size":…}` |
| `GET /` | 브라우저 질문 시험대 — 질문을 입력하면 위 `/answer` 를 호출해 답변·근거·처리 과정을 화면에 보여준다 |

## 6. 규격 대조표 (주최 요구 → 우리 구현)

| 주최 요구(출처) | 우리 구현 | 확인 방법 |
|---|---|---|
| `GET /answer`, 파라미터 `question_id`·`question` (PDF p.11) | 동일 | `tests/test_server.py::test_answer_returns_five_string_fields` |
| 인증 헤더·POST 본문 사용 안 함 (PDF p.11) | 동일 | — |
| 미정의 파라미터에도 500 없이 처리 (PDF p.11) | 무시하고 정상 응답 | `test_undefined_params_and_missing_id_never_500` |
| 응답 200 OK · JSON · 5필드 전부 string (PDF p.11 · 안내문) | 동일, `additionalProperties` 없음 | 위 테스트 + `test_empty_question_still_valid_json` |
| 확인 불가 질의도 200 + 동일 스키마 (PDF p.11) | 거절문 템플릿 + 5필드 | `test_trap_question_refused_via_server` |
| `Content-Type: application/json; charset=utf-8` (PDF p.11) | 동일하게 명시 | `test_undefined_params_and_missing_id_never_500` |
| 문항당 60초 이내 권장 (PDF p.11) / 타임아웃 300초·재시도 2회 (안내문) | 내부 목표 15초, 단계별 자동 강등 | 실측 2.3~9.7초 (PLAN.md 부록 A 8/14) |
| 근거 표시 필수 (PDF p.6) | `retrieved_context` 근거 블록 + `answer` 안 기준일 표기 | 위 예시 |
| 확인 불가 시 "확인할 수 없음" 명시 (PDF p.6) | 정해진 거절문 | 위 예시 ② |
