# Project Memory

> 마지막 갱신: 2026-08-04
> 상세 실행 가이드: [`PROJECT_GUIDE.md`](./PROJECT_GUIDE.md)

## 사용 규칙

이 파일은 세션이 바뀌어도 작업을 이어가기 위한 짧고 사실 중심의 기록이다.

1. 작업을 시작할 때 `현재 상태`, `열린 결정`, `다음 작업`을 읽는다.
2. 완료한 작업은 체크하고 결과 파일 또는 검증 명령을 함께 기록한다.
3. 추정은 `가정`, 확정되지 않은 선택은 `잠정 결정`으로 표시한다.
4. 실패와 blocker도 삭제하지 말고 원인과 다음 시도를 남긴다.
5. 오래된 설명을 누적하기보다 현재 상태를 먼저 갱신하고, 상세 이력은 작업 로그에 한 줄로 남긴다.
6. 토큰, 비밀번호, 고객정보 등 비밀값은 기록하지 않는다.

## 현재 상태

- 단계: 공식 과제 요구사항·데이터 탐색 완료, 1주 차 비파괴 정규화·품질검증 파이프라인 구현 완료
- 목표: 미래에셋 AI Festival용 금융상품 에이전트 개발
- 핵심 판단: LLM 프롬프팅보다 데이터 정규화와 답변 불가 판정이 성패를 좌우함
- 기본 아키텍처: PostgreSQL 기반 Text-to-SQL, `pg_trgm` 상품명 검색, 해외 ETF 전략에만 `pgvector`
- 답변 원칙: 단정적 추천 대신 조건 부합 상품, 데이터 범위, 기준일, 근거를 제시
- 구현 상태: 데이터 파이프라인·단위 테스트·품질 보고서 완료, PostgreSQL/API/평가셋은 아직 없음
- 데이터 기준: schema 추출일 2026-07-11, 총 145,393개 원본 행
- 일정: 2026-09-06 마감, 2026-09-07~09-30 공식 평가, 2026-10-01 결과 발표
- 제출 동결: 09-06 이후 커밋·푸시·서버 배포 등 코드와 결과물 변경 금지
- 모델 제약: 자연어 이해·답변 생성 LLM은 HyperCLOVA X만 사용
- 출처 원칙: 외부 금융상품 데이터는 보강 가능하나 주최 측 데이터와 상충하면 공식 2026-07-11 스냅샷 우선
- 검색 인덱스: 기존 Lance manifest는 현재 삭제 상태이며 SQL 베이스라인의 blocker가 아님

## 산출물 상태

| 산출물 | 상태 | 위치/메모 |
|---|---|---|
| 프로젝트 설명 | 최소 | `README.md` |
| 프로젝트 실행 가이드 | 완료 | `PROJECT_GUIDE.md` |
| 연속 작업 기록 | 사용 중 | `memory.md` |
| Excel 원본/스키마 | 확보 | `datasets/` 8개 파일 |
| 공식 과제 소개서 | 확보·반영 | `manifest/금융상품Agent_과제소개.pdf`, 8쪽 |
| 데이터 프로파일링 코드 | 완료 | `pipeline/prepare_data.py` |
| PostgreSQL 정규화 스키마 | 미착수 | fund master/attribute, taxonomy, quarantine 필요 |
| 정제 데이터 | 완료·재생성 가능 | `artifacts/data/`는 gitignore, reconciliation 통과 |
| 데이터 품질 보고서 | 완료 | `reports/DATA_QUALITY_REPORT.md` |
| 데이터 파이프라인 단위 테스트 | 완료 | `tests/test_prepare_data.py`, 7건 통과 |
| Text-to-SQL 베이스라인 | 미착수 | 2주 차 내 `/answer` 확보 목표 |
| 답변 가능성 판정기 | 미착수 | coverage와 불가 사유를 구조화해야 함 |
| 완전한 Lance 인덱스 | 선택 | 기존 manifest도 현재 없음. 해외 ETF 전략은 pgvector 재생성 가능 |
| 에이전트/API 코드 | 미착수 | HyperCLOVA X, LLM 호출 최대 2회 목표 |
| 평가셋/자동 테스트 | 미착수 | 대표 질문부터 정의 필요 |

## 확인된 사실

### Manifest

- 현재 `manifest/`에는 공식 과제 소개 PDF가 있고 기존 `2.manifest`는 삭제 상태다.
- 아래 Lance 정보는 2026-08-03 조사 이력이며 현행 실행 자산이 아니다.
- 당시 `manifest/2.manifest`는 텍스트 설정이 아니라 Lance 바이너리 manifest였다.
- Lance `0.20.0`, 저장 형식 `2.0`으로 기록되어 있다.
- 스키마는 `id: string`, `text: string`, `vector: float[1536]`, `attributes: string`이다.
- 참조 fragment `fe6e565a-2b59-416b-a2f2-e45ed1a65462.lance`는 저장소에 없다.
- 임베딩 모델, 거리 함수, `attributes` 직렬화 계약은 알 수 없다.

### 공식 과제 소개서

- 과제는 4종 상품 마스터를 기반으로 자연어 조건 검색·조회·비교·정렬·순위·집계·교차 상품군 질의를 수행하는 Agent RAG/QA 구현이다.
- 답변은 제공 데이터 근거와 참조 데이터를 표시해야 하며, 확인 불가 시 이를 명시하거나 필요한 조건을 역질문해야 한다.
- 데이터에 근거 없는 수익률 전망과 단정적 투자 추천은 금지다.
- 금융상품 외부 데이터는 사용할 수 있으나 평가 기준은 주최 측 데이터이며 충돌 시 주최 측 데이터가 우선한다.
- 답변 생성용 LLM은 HyperCLOVA X만 허용되며 다른 LLM 사용 시 평가 제외다.
- 예시 질의와 구체적인 평가 기준은 PDF에 없고 08-06 오프라인 설명회 공지 예정이다.
- 제출물은 주최 측 GitHub Organization Private Repository에 올릴 소스코드·재현 환경·README, 기술제안서, 평가 API endpoint와 요청/응답 JSON 명세 3종이다. 대용량 제출물은 범용 클라우드 스토리지 다운로드 링크로 공유할 수 있다.
- 공식 API 예시는 `GET /answer?question_id=&question=`과 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer` 응답 필드를 사용한다.
- 09-06 이후 커밋·푸시·서버 배포 등 코드와 결과물 변경이 발견되면 실격이다.

### Datasets

- 국내채권: 42,394행 × 40컬럼. `PD_NO`가 공백과 중복 없이 유일하다.
- 국내채권 위험코드는 0~6이며 0은 58건이다. 1=매우 높은 위험, 6=매우 낮은 위험으로 표준화한다.
- 국내채권 수익률 계열은 881행(2.1%)에만 값이 있다. `BUYABLE_QUANTITY > 0`은 325행이다.
- 국내채권 공식 `MAT_DT` 기준 추출일 전 만기 16,180건, 당일 7건, 이후 25,884건, 만기 불명 323건이다. 사용자 승인 상품명 해석을 반영한 resolved 기준은 이후 25,885건, 불명 322건이다. 기존 16,496건에는 `MAT_DT=0` 316건이 잘못 포함됐다.
- 국내채권 원본 `REMAINING_DAYS`는 행별 `PD_STD_INFO_UPDATE` 기준과 정확히 일치한다. ETL은 재현용 `remaining_days_at_snapshot`만 생성하고, 운영 응답은 요청 시작 시 확정한 서울 `as_of_date`와 `maturity_date_resolved`로 매번 계산한다.
- `XS3067881758 / BAC 0 05/14/55 Corp`는 사용자 확인에 따라 발행자 Bank of America Corporation, 표면금리 0%, 만기 2055-05-14를 승인된 파생 필드로 보강했다. 국내채권 마스터 42,394건 중 유일한 비-`KR` ISIN이며 `XS` 국제등록채권으로 분리한다. `CURR_CD=000`과 국내 마스터 포함 경위는 미확정이다.
- 국내 ETF/ETN: 1,734행 × 73컬럼. `pd_itm_no`, `pd_itm_no_ma`가 각각 유일하다.
- 국내 ETF/ETN 원본은 ETF 1,202건, ETN 532건이다. 핵심 필드 불완전 ETF 1행을 격리한 정제본은 ETF 1,201건, ETN 532건이다.
- 국내 ETP는 추출일 전 거래종료 212건이며 `-100` 수익률은 모두 이 종료 상품에서만 나타난다.
- 국내 ETP Excel 1,155행은 결손 행이 아니라 Excel 299행 `KR70193M0005`의 손상된 중복이다. `pd_itm_no_ma`가 `A0193M0`(정상)과 `A0193MO`(손상)로 끝자리만 다르고 위험등급코드가 같다. 격리로 유실된 상품은 없다.
- 국내 ETP `pd_lste_dt`에 미래 종료예정일은 0건이다. `99991231`은 “만기 없음”이 아니라 “추출일 시점 미종료”다. 활성 ETN 381건과 목표만기 표기 활성 ETF 19건은 계약상 만기가 있는데도 같은 값이다. 활성 상품의 만기일은 이 데이터에 없다.
- 국내 ETP `-100` 수익률은 종가 `du_clpr=0`에서 계산된 값이다. 종료 212건 중 167건이 종가 0이고 활성 상품 중 종가 0은 0건이다. `-100`이 찍히는 기간 컬럼은 종료 시점이 그 기간 창에 들어오는지로 결정된다.
- `-100`으로 상장폐지를 판정하면 오탐 0건, 미탐 114건이다. 판정은 `listing_status`로 해야 한다.
- 국내 ETP 기본 검색 대상은 미종료·미정지 1,520건이다.
- 국내 ETF 총보수는 217/1,734, 기초지수는 trim 후 58/1,734만 유효하다.
- 국내 ETF `pd_sect_nm`, `ru_mkt_price`, `ru_mkt_volume`, `nru_mkt_inav`, `nru_mkt_diff_rt`는 전부 비어 있다.
- 국내 ETF `du_chas_errt`, `du_diff_rt`, `pd_dvid_yield`는 비결측값이 모두 0이다.
- 해외 ETF: 5,646행 × 49컬럼. `pd_itm_no`, `pd_itm_no_ma`가 각각 유일하다.
- 해외 ETF 파일에는 실제로 ETF 5,587건과 ETN 59건이 섞여 있다.
- 해외 ETF `pd_isin_cd`는 9행이 비어 있고 비어 있지 않은 값 중 추가 중복 행이 50개이므로 기본 키로 쓰지 않는다.
- 해외 ETF에는 위험등급 컬럼이 없고 총보수는 5,646행 모두 존재한다.
- 해외 ETF `cu_strtegy`는 5,638행에 값이 있고 비결측 고유 문서가 5,566개다.
- 해외 ETF 핵심 필드가 대부분 빈 8행, ISIN만 빠진 추가 1행, 통화·상품분류 충돌 1행과 괴리율 `37585` 오류 1건이 있다.
- 해외 ETF 일간 데이터가 추출일보다 30일 이상 오래된 행은 252건이다.
- 공모펀드: 원본 95,619행 중 유효 95,618행이며 유효 `itm_no` 11,138개다. 유효 `(itm_no, prfd_attr_cd)`는 유일하다.
- 동일 공모펀드 `itm_no` 그룹에서 달라지는 컬럼은 trim 기준 `prfd_attr_cd` 하나뿐이며 평균 8.584행, 최대 16행이다.
- 공모펀드 `or_attr_desc='06'`은 5,436행, 686개 상품이며 미매핑 코드로 보존해야 한다.
- 공모펀드 위험명은 `높은 위험` 29,088행과 `높은위험` 163행처럼 표기가 오염되어 있다.
- 공모펀드 Excel 84,563행은 컬럼이 밀려 `itm_no='"'`, `exchdg_yn='00080008'`이 된 비정상 행이다.
- `KCGI베트남증권투자신탁(주식혼합)`의 18개월·2년·3년·5년 수익률은 -100% 미만이어서 정제본에서 null 처리하고 원본 값을 품질 이력에 남긴다.
- 모든 datarows 컬럼은 대응 schema 컬럼과 대소문자를 무시했을 때 일치한다.
- 네 schema 파일 모두 `PK/FK`가 지정되어 있지 않다.
- schema 샘플의 `axis_*`는 datarows에 없는 파생 분류값이다. 해외 ETF 샘플에는 별도 axis가 없다.
- 국내채권과 국내 ETF의 여러 문자열에 앞뒤 고정 폭 공백이 있다.
- 공모펀드 `zrin_fd_ivst_risk_gcd`에 실제 null이 아닌 문자열 `NULL`이 18,416건 있다.
- 원본 행 전체를 trim한 기준으로 네 데이터셋 모두 완전히 동일한 중복 행은 없다.
- 지역·자산군 값은 상품군마다 언어와 세분화 수준이 달라 런타임 LLM이 아닌 수동 매핑 테이블이 필요하다.
- 동일 입력으로 파이프라인을 두 번 실행한 모든 산출물 SHA-256이 일치했다.

## 현재 설계 결정

- 운영 저장소는 PostgreSQL을 기본으로 하고 GraphDB는 사용하지 않는다.
- 필터·정렬·집계·비교는 Text-to-SQL/SQL template을 주 경로로 사용한다.
- 상품명 fuzzy 검색은 `pg_trgm`, 의미 검색은 해외 ETF `cu_strtegy`의 `pgvector`로 제한한다.
- 원본 식별자와 날짜 코드는 문자열로 읽어 선행 0을 보존한다.
- 공모펀드는 `itm_no` 기준 master와 `(itm_no, prfd_attr_cd)` attribute bridge로 분리한다.
- 원본 Excel은 수정하지 않고 재생성 가능한 `artifacts/data/clean` 정제 계층을 사용한다.
- 국내 ETP Excel 1,155행과 공모펀드 Excel 84,563행은 공식 정정본 전까지 quarantine한다.
- 국내 ETP 기본 검색은 거래종료 전이고 거래정지되지 않은 상품으로 제한한다.
- 원본 날짜 sentinel과 -100% 미만 수익률·범위 밖 비율은 추정 보정하지 않고 null과 품질 이력으로 분리한다.
- 원본 행과 논리 상품을 별도 엔터티로 관리하고 모든 결과에서 source row를 역추적한다.
- 문자열 공백 제거와 null 정규화 이후에도 원본 값은 추적 가능하게 보존한다.
- 위험등급은 1=매우 높은 위험, 6=매우 낮은 위험으로 통일하며 해외 ETF는 unavailable로 둔다.
- 지역·자산군 mapping은 사람이 검토하고 버전 관리하며 런타임 LLM에 맡기지 않는다.
- 모든 핵심 수치에 availability와 eligible/available coverage를 계산한다.
- 데이터가 없거나 부분적인 경우 답변 상태와 범위를 명시하고 값을 추론하지 않는다.
- 최종 답변은 단정적 추천 대신 조건 부합 사실과 근거를 표현한다.
- API는 `GET /answer?question_id=&question=` 형태, 5초 이내, LLM 최대 2회 호출을 목표로 한다.
- API 최상위 필수 필드는 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`로 고정한다.
- `retrieved_context`에는 실제 결과 행의 참조 테이블·원본 행 키·필드·값·기준일·출처를 담는다.
- `think_trace`에는 숨은 내부 추론문 대신 도구 호출과 검증 이벤트만 기록한다.
- 자연어 이해와 답변 생성 provider는 HyperCLOVA X만 허용하며 타 LLM fallback은 금지한다.
- 공식 스냅샷은 `source_origin=official_snapshot`으로 표시하고 외부 보강 데이터보다 우선한다.
- 요청에 명시적 기준일이 없으면 `datetime.now(ZoneInfo("Asia/Seoul")).date()`를 한 번 확정한다. 동일 요청의 SQL·계산·답변은 같은 `as_of_date`를 쓰고 응답에 노출한다.
- 사람이 승인한 상품명 해석은 `config/manual_overrides.csv`의 파생 필드에만 적용하고 원본 공란은 보존한다.
- 채권의 국내/국제 범위는 파일명이나 발행자 국적이 아니라 ISIN 등록 범위로 분리한다. `XS3067881758`은 국내채권 기본 검색에서 제외하고 국제·일반 채권 조회에서만 명시적으로 포함한다.
- 09-06 전 immutable release를 배포하고 commit SHA, image digest, dependency lock, 설정 checksum을 보관한다.

## 열린 결정 및 blocker

- [ ] 08-06 설명회 예시 질의를 확인해 사용자 시나리오와 우선순위를 확정해야 함
- [ ] PDF에 빠진 구체적인 평가 지표와 가중치를 08-06 설명회에서 확인해야 함
- [ ] 09-06 이후 허용되는 모니터링·재시작·장애 복구 범위를 주최 측에 확인해야 함
- [ ] HyperCLOVA X 외 임베딩 모델 사용이 LLM 제한에 해당하는지 확인해야 함
- [ ] 공식 API 예시의 `retrieved_context`·`think_trace` 필드 타입과 추가 필드 허용 여부를 확인해야 함
- [ ] 개인화 및 금융 조언 안전 경계가 정해지지 않음
- [ ] 샘플의 `axis_*`가 정답 라벨인지 단순 예시인지 확인 필요
- [ ] 공모펀드 비정상 1행의 올바른 원본 값 복구 또는 공식 제외 승인 필요
- [ ] 국내 ETP 비정상 1행은 `KR70193M0005`의 중복으로 확인되어 상품 유실이 없다. 정정 행 확보 대신 제공처의 중복 발생 원인 확인만 남았다
- [ ] 데이터에 없는 속성을 물었을 때 “확인 불가” 침묵과 근거 등급을 붙인 부분 답변 중 어느 쪽이 평가에 유리한지 결정 필요. 08-06 평가 지표 공개 후 판단한다. 시금석 질의는 “만기가 2027년인 ETF 알려줘”이며 선택지는 (a) 만기 필드 부재만 알리고 종료 (b) 부재를 알리되 상품명 목표만기 표기 활성 ETF 19건과 계약상 만기가 있는 ETN 381건을 출처 등급과 함께 제시 (c) `config/manual_overrides.csv` 방식으로 승인된 파생 필드에 정식 보강. 과제 소개서는 “확인 불가 시 명시 또는 역질문”만 요구하므로 (b)가 요구사항과 충돌하지 않는지 확인해야 한다.
- [ ] `listing_status='active_open_ended'`를 `active_not_ended`로 리네이밍할지 결정 필요. 1,521건 중 최소 400건(ETN 381 + 존속기한형 ETF 19)은 계약상 만기가 있어 현재 라벨명이 사실과 다르다. 코드 3곳 수정과 파이프라인 재실행이 필요하다.
- [ ] `or_attr_desc='06'`을 파생형으로 매핑해도 되는지 업무 확인 필요
- [ ] 국내 ETF 총보수 0 150건과 해외 ETF 총보수 0 363건의 업무 의미 확인 필요
- [ ] 국가 단위 지역값과 광역 지역값을 어떤 수준으로 통합할지 결정 필요
- [ ] `BUYABLE_QUANTITY > 0`을 채권의 `is_tradable`로 볼지 업무 규칙 확정 필요
- [ ] “위험등급이 낮은” 질의를 항상 5~6으로 해석할지 매번 역질문할지 결정 필요
- [ ] 부분 coverage에서 모집단 답변을 거부할 최소 coverage 정책 결정 필요
- [ ] ETF-공모펀드 중복 상품의 표시/연결 단위 결정 필요
- [ ] 실시간 시세 또는 외부 API 연동 필요 여부 미정
- [ ] HyperCLOVA X 모델/호출 계약과 임베딩 모델 확정 필요
- [ ] PostgreSQL/Docker 배포 환경과 09-07~09-30 모니터링 환경 확정 필요

## 다음 작업

1. PostgreSQL DDL과 `fund_master`, `fund_attribute`, `taxonomy_mapping`, quarantine 스키마를 작성한다.
2. 정제 CSV와 품질 정책을 PostgreSQL staging/normalized 테이블에 적재한다.
3. `taxonomy_mapping_seed.csv`의 국가/상위지역·자산군 매핑을 사람 검토 후 승인한다.
4. 격리된 국내 ETP·공모펀드 각 1행의 공식 정정본을 확보한다.
5. HyperCLOVA X 단일 provider client와 타 LLM 차단 검증을 구현한다.
6. 답변 가능성 상태와 coverage 계산 로직을 구현한다.
7. 대표 사용자 질문 20개와 기대 SQL/답변 상태를 정의한다.
8. 공식 필수 5개 응답 필드의 JSON Schema와 계약 테스트를 작성한다.
9. 2주 차 안에 구조화 근거를 반환하는 `/answer` 베이스라인을 완성한다.
10. SQL 평가 통과 후 해외 ETF 전략 pgvector 검색과 선택적 외부 보강을 추가한다.

## 작업 로그

### 2026-08-03 — 저장소 초기 조사 및 문서화

- `README.md`, Excel schema/datarows 8개, `manifest/2.manifest`를 조사했다.
- 네 원본 데이터셋의 행/컬럼 수, 선언 타입, 후보 키 유일성, 주요 결측과 공백 문제를 확인했다.
- 공모펀드의 안전한 행 키가 `(itm_no, prfd_attr_cd)`임을 전체 95,619행에서 검증했다.
- Lance manifest의 스키마와 누락 fragment를 확인했다.
- `PROJECT_GUIDE.md`와 `memory.md`를 생성했다.

### 2026-08-03 — 정규화·답변 불가 중심으로 설계 개편

- 사용자가 제공한 상세 데이터 분석을 원본 Excel 전체 행과 대조했다.
- 공모펀드 그룹 내 변동 컬럼, 위험등급, 결측 coverage, 만기채권, ETF/ETN 혼재, 해외 ETF 전략 고유값을 재검증했다.
- 공모펀드 Excel 84,563행의 컬럼 밀림과 도메인 위반을 확인했다.
- PostgreSQL + Text-to-SQL을 주 경로로, 해외 ETF 전략 pgvector를 보조 경로로 정했다.
- 답변 가능성 상태, coverage, 구조화 근거, 5초/LLM 2회 API 목표를 문서화했다.
- 2026-09-06 마감에 맞춘 5주 로드맵으로 `PROJECT_GUIDE.md`를 개편했다.

### 2026-08-04 — 공식 과제 소개서 기반 계획 보강

- `manifest/금융상품Agent_과제소개.pdf` 8쪽을 기존 설계 문서와 대조했다.
- 평가 기간을 09-07~09-30으로 바로잡고 09-06 이후 변경 금지와 immutable release 준비를 계획에 추가했다.
- HyperCLOVA X 단일 LLM 제약, 공식 데이터 우선순위, 외부 데이터 provenance 계약을 추가했다.
- API의 필수 5개 최상위 필드와 근거 행 중심 `retrieved_context`, 도구 이벤트 중심 `think_trace` 계약을 보강했다.
- 제출 3종의 구성과 마감 전 commit/image/config 식별자 보관을 완료 기준에 연결했다.
- PDF에 없는 평가 지표, 임베딩 허용 범위, 마감 후 장애 대응 범위는 08-06 확인 항목으로 남겼다.

### 2026-08-04 — 데이터 품질 분석 및 정제 파이프라인 구현

- 145,393개 원본 행의 스키마·키·결측·공백·상수값·날짜·도메인·범위·상품단위를 전체 재검증했다.
- 이전 만기채권 16,496건 계산이 `MAT_DT=0` 316건을 포함한 오류임을 확인하고 유효 만기 16,180건으로 바로잡았다.
- 국내 ETP Excel 1,155행과 공모펀드 Excel 84,563행을 quarantine했다.
- 공모펀드를 master 11,138행과 attribute 95,618행으로 분리했다.
- 문자열 `NULL`, 위험명 공백, 날짜 sentinel, 위험등급, ETF/ETN, 만기·거래종료·데이터 지연 상태를 정규화했다.
- 해외 ETF 괴리율 `37585`와 공모펀드 -100% 미만 수익률 4개를 null 처리하고 품질 이력에 남겼다.
- column profile, field policy, taxonomy seed, issue log, quarantine, reconciliation과 정제 CSV를 생성했다.
- 12개 reconciliation assertion과 단위 테스트 3건을 통과하고 재실행 산출물 SHA-256 일치를 확인했다.
- 상세 근거와 사용자 결정 항목을 `reports/DATA_QUALITY_REPORT.md`에 기록했다.

### 2026-08-04 — 동적 잔존일수와 XS 채권 보강

- 고정 추출일 통계와 운영 요청 기준일을 분리했다. 운영은 명시적 기준일을 우선하고, 없으면 서울 현재 날짜를 요청 시작 시 한 번 계산한다.
- `pipeline/time_policy.py`에 잔존일수·만기상태 계산을 구현하고 서울 자정 경계와 기준일 우선순위를 포함한 테스트 4건을 추가했다.
- `XS3067881758`의 사용자 확인 정보를 `config/manual_overrides.csv`에 기록하고 원본 공란을 덮어쓰지 않는 resolved/source 파생 필드로 반영했다.
- 해당 상품은 `incomplete_core`가 아니라 통화만 미확정인 `partial_currency`로 변경했다. 보강 후 스냅샷 기준 이후 만기는 25,885건, 만기 불명은 322건이다.
- reconciliation assertion 13개와 전체 단위 테스트 7건을 통과했다.

### 2026-08-04 — 국내채권 마스터의 XS 범위 예외 분리

- 국내채권 원본 42,394건에서 `PD_NO`/`PD_CTRY_CD`가 `KR`인 행은 42,393건, `XS`는 `XS3067881758` 1건뿐임을 확인했다.
- `XS`는 발행자 국적이 아니라 국제발행·예탁 범위이므로 `security_registration_scope=international_isin`과 `dataset_scope_exception_partial_currency`로 표시했다.
- 해당 행은 국내채권 기본 검색에서 제외하고 `international_bond_search_eligible`과 `general_bond_search_eligible`에만 남겼다.
- 실제 통화와 국내 마스터 포함 사유는 확인 항목으로 유지하고 reconciliation assertion을 14개로 늘렸다.

## 로그 추가 템플릿

새 작업은 아래 형식을 복사해 `작업 로그`의 가장 최근 항목 아래에 추가한다.

```markdown
### YYYY-MM-DD — 작업 제목

- 목표:
- 변경:
- 검증:
- 결정:
- blocker:
- 다음:
```
