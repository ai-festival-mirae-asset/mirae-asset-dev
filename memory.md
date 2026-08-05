# Project Memory

> 마지막 갱신: 2026-08-05  
> 이 파일은 개인 브랜치 `papuagigi`의 작업 기록이다. 팀 공동 실행 가이드는 [main 브랜치 `PROJECT_GUIDE.md`](https://github.com/ai-festival-mirae-asset/mirae-asset-dev/blob/main/PROJECT_GUIDE.md)에 있다.

## 사용 규칙

이 파일은 세션이 바뀌어도 작업을 이어가기 위한 짧고 사실 중심의 기록이다.

1. 작업을 시작할 때 `현재 상태`, `열린 결정`, `다음 작업`을 읽는다.
2. 완료한 작업은 체크하고 결과 파일 또는 검증 명령을 함께 기록한다.
3. 추정은 `가정`, 확정되지 않은 선택은 `잠정 결정`으로 표시한다.
4. 실패와 blocker도 삭제하지 말고 원인과 다음 시도를 남긴다.
5. 오래된 설명을 누적하기보다 현재 상태를 먼저 갱신하고, 상세 이력은 작업 로그에 한 줄로 남긴다.
6. 토큰, 비밀번호, 고객정보 등 비밀값은 기록하지 않는다.

## 현재 상태

- 단계: 전처리 1차 완료(스테이징 CSV), 해석 메타데이터 사전 구축 완료, DB 적재 준비
- 목표: 미래에셋 AI Festival용 금융상품 에이전트 개발
- 핵심 판단: LLM 프롬프팅보다 데이터 정규화와 답변 불가 판정이 성패를 좌우함
- 기본 아키텍처: PostgreSQL 기반 Text-to-SQL, `pg_trgm` 상품명 검색, 해외 ETF 전략에만 `pgvector`
- 답변 원칙: 단정적 추천 대신 조건 부합 상품, 데이터 범위, 기준일, 근거를 제시
- 구현 상태: 데이터 원본만 존재하며 애플리케이션 코드와 실행 환경은 아직 없음
- 데이터 기준: schema 추출일 2026-07-11, 총 145,393개 원본 행
- 일정: 2026-09-06 마감, 2026-09-07~09-20 API 상시 운영 예상
- 검색 인덱스: Lance manifest는 불완전(fragment 누락)하여 이 브랜치에서 제거함. SQL 베이스라인의 blocker는 아님
- 브랜치 정책: `papuagigi`는 main과 분리해 개인 산출물만 담는다. main에서 상속한 `datasets/`·`manifest/`·`PROJECT_GUIDE.md`는 제거했고, 통합 시점에 main으로 합친다

## 산출물 상태

| 산출물 | 상태 | 위치/메모 |
|---|---|---|
| 프로젝트 설명 | 최소 | `README.md` |
| 프로젝트 실행 가이드 | 완료 | main 브랜치 `PROJECT_GUIDE.md` (이 브랜치에는 두지 않음) |
| 연속 작업 기록 | 사용 중 | `memory.md` |
| Excel 원본/스키마 | 확보 | 로컬 `datasets/` 8개 파일 — 커밋 대상 아님(.gitignore). `MIRAE_DATASETS`로 경로 변경 가능 |
| 데이터 프로파일링 코드 | 완료 | `preprocessing/profiling/` (재실행 가능 스크립트·컬럼별 CSV) |
| 해석 메타데이터 사전 | 완료 | `external_data/dictionaries/` 9종 — 컬럼 207개 전체 해석, 별칭·등급·코드·지수 사전, 출처·검증상태 포함 |
| 전처리 파이프라인 | 1차 완료 | `preprocessing/preprocess.py` → `preprocessing/processed/` CSV 4종 + quarantine 1행 + 규칙 리포트 56건 |
| 전처리·수집 방법 문서 | 완료 | `preprocessing/PREPROCESSING_METHOD.md`, `external_data/COLLECTION_METHOD.md`, `ROADMAP.md` |
| PostgreSQL 정규화 스키마 | 미착수 | fund master/attribute, taxonomy, quarantine 필요 |
| 정제 데이터(DB) | 미착수 | PostgreSQL 적재를 기본안으로 결정. 스테이징 CSV는 확보됨 |
| Text-to-SQL 베이스라인 | 미착수 | 2주 차 내 `/answer` 확보 목표 |
| 답변 가능성 판정기 | 미착수 | coverage와 불가 사유를 구조화해야 함 |
| 완전한 Lance 인덱스 | 폐기 | fragment 누락으로 복원 불가 → `manifest/` 제거. 해외 ETF 전략은 pgvector로 재생성 |
| 에이전트/API 코드 | 미착수 | HyperCLOVA X, LLM 호출 최대 2회 목표 |
| 평가셋/자동 테스트 | 미착수 | 대표 질문부터 정의 필요 |

## 확인된 사실

### Manifest (2026-08-05 이 브랜치에서 제거 — 조사 결과만 보존)

- 파일 자체는 main 브랜치에 남아 있다: `git show main:manifest/2.manifest`
- `manifest/2.manifest`는 텍스트 설정이 아니라 Lance 바이너리 manifest다.
- Lance `0.20.0`, 저장 형식 `2.0`으로 기록되어 있다.
- 스키마는 `id: string`, `text: string`, `vector: float[1536]`, `attributes: string`이다.
- 참조 fragment `fe6e565a-2b59-416b-a2f2-e45ed1a65462.lance`는 저장소에 없다.
- 임베딩 모델, 거리 함수, `attributes` 직렬화 계약은 알 수 없다.

### Datasets

- 국내채권: 42,394행 × 40컬럼. `PD_NO`가 공백과 중복 없이 유일하다.
- 국내채권 위험코드는 0~6이며 0은 58건이다. 1=매우 높은 위험, 6=매우 낮은 위험으로 표준화한다.
- 국내채권 수익률 계열은 881행(2.1%)에만 값이 있다. `BUYABLE_QUANTITY > 0`은 325행이다.
- 국내채권은 `MAT_DT < 20260711` 기준 16,496건이 만기 도래했고, 같은 날 만기 7건이 별도로 있다.
- 국내 ETF/ETN: 1,734행 × 73컬럼. `pd_itm_no`, `pd_itm_no_ma`가 각각 유일하다.
- 국내 ETF/ETN은 ETF 1,202건, ETN 532건이다.
- 국내 ETF 총보수는 217/1,734, 기초지수는 trim 후 58/1,734만 유효하다.
- 국내 ETF `pd_sect_nm`, `ru_mkt_price`, `ru_mkt_volume`, `nru_mkt_inav`, `nru_mkt_diff_rt`는 전부 비어 있다.
- 국내 ETF `du_chas_errt`, `du_diff_rt`, `pd_dvid_yield`는 비결측값이 모두 0이다.
- 해외 ETF: 5,646행 × 49컬럼. `pd_itm_no`, `pd_itm_no_ma`가 각각 유일하다.
- 해외 ETF `pd_isin_cd`는 9행이 비어 있고 비어 있지 않은 값 중 추가 중복 행이 50개이므로 기본 키로 쓰지 않는다.
- 해외 ETF에는 위험등급 컬럼이 없고 총보수는 5,646행 모두 존재한다.
- 해외 ETF `cu_strtegy`는 5,638행에 값이 있고 비결측 고유 문서가 5,566개다.
- 공모펀드: 95,619행 × 45컬럼. `itm_no`는 11,139개이고 중복되지만 `(itm_no, prfd_attr_cd)`는 전체 행에서 유일하다.
- 동일 공모펀드 `itm_no` 그룹에서 달라지는 컬럼은 trim 기준 `prfd_attr_cd` 하나뿐이며 평균 8.584행, 최대 16행이다.
- 공모펀드 `or_attr_desc='06'`은 5,436행, 686개 상품이며 미매핑 코드로 보존해야 한다.
- 공모펀드 위험명은 `높은 위험` 29,088행과 `높은위험` 163행처럼 표기가 오염되어 있다.
- 공모펀드 Excel 84,563행은 컬럼이 밀려 `itm_no='"'`, `exchdg_yn='00080008'`이 된 비정상 행이다.
- 모든 datarows 컬럼은 대응 schema 컬럼과 대소문자를 무시했을 때 일치한다.
- 네 schema 파일 모두 `PK/FK`가 지정되어 있지 않다.
- schema 샘플의 `axis_*`는 datarows에 없는 파생 분류값이다. 해외 ETF 샘플에는 별도 axis가 없다.
- 국내채권과 국내 ETF의 여러 문자열에 앞뒤 고정 폭 공백이 있다.
- 공모펀드 `zrin_fd_ivst_risk_gcd`에 실제 null이 아닌 문자열 `NULL`이 18,416건 있다.
- 해외 ETF `cu_base_index`에는 "Index is not provided by Management Company" 류 Lipper 센티널이 2,705건(48%) 있어 null 정규화가 필요하다.
- 국내채권 `CRD_GRD`의 `AA0` 표기는 무부호(플랫)이며 끝자리 0 제거로 정규화한다. 등급 서열은 AAA=1~D=20 rank로 관리한다.
- 국내채권 `DEPO_EQUIV_YIELD_154`의 154는 이자소득세율 15.4%를 뜻하는 예금환산수익률이다.
- KOFIA 20자리 펀드분류코드의 6번째 자리는 클래스 유형이다(금투협 공식 Q&A로 확인).
- 원본 행 전체를 trim한 기준으로 네 데이터셋 모두 완전히 동일한 중복 행은 없다.
- 지역·자산군 값은 상품군마다 언어와 세분화 수준이 달라 런타임 LLM이 아닌 수동 매핑 테이블이 필요하다.

## 현재 설계 결정

- 운영 저장소는 PostgreSQL을 기본으로 하고 GraphDB는 사용하지 않는다.
- 필터·정렬·집계·비교는 Text-to-SQL/SQL template을 주 경로로 사용한다.
- 상품명 fuzzy 검색은 `pg_trgm`, 의미 검색은 해외 ETF `cu_strtegy`의 `pgvector`로 제한한다.
- 원본 식별자와 날짜 코드는 문자열로 읽어 선행 0을 보존한다.
- 공모펀드는 `itm_no` 기준 master와 `(itm_no, prfd_attr_cd)` attribute bridge로 분리한다.
- 원본 행과 논리 상품을 별도 엔터티로 관리하고 모든 결과에서 source row를 역추적한다.
- 문자열 공백 제거와 null 정규화 이후에도 원본 값은 추적 가능하게 보존한다.
- 위험등급은 1=매우 높은 위험, 6=매우 낮은 위험으로 통일하며 해외 ETF는 unavailable로 둔다.
- 지역·자산군 mapping은 사람이 검토하고 버전 관리하며 런타임 LLM에 맡기지 않는다.
- 모든 핵심 수치에 availability와 eligible/available coverage를 계산한다.
- 데이터가 없거나 부분적인 경우 답변 상태와 범위를 명시하고 값을 추론하지 않는다.
- 최종 답변은 단정적 추천 대신 조건 부합 사실과 근거를 표현한다.
- API는 `GET /answer?question_id=&question=` 형태, 5초 이내, LLM 최대 2회 호출을 목표로 한다.

## 열린 결정 및 blocker

- [ ] 08-06 설명회 예시 질의를 확인해 사용자 시나리오와 우선순위를 확정해야 함
- [ ] 개인화 및 금융 조언 안전 경계가 정해지지 않음
- [ ] 샘플의 `axis_*`가 정답 라벨인지 단순 예시인지 확인 필요
- [ ] 공모펀드 비정상 1행의 올바른 원본 값 복구 또는 공식 제외 승인 필요
- [ ] `or_attr_desc='06'`을 파생형으로 매핑해도 되는지 업무 확인 필요
- [ ] `BUYABLE_QUANTITY > 0`을 채권의 `is_tradable`로 볼지 업무 규칙 확정 필요
- [ ] “위험등급이 낮은” 질의를 항상 5~6으로 해석할지 매번 역질문할지 결정 필요
- [ ] 부분 coverage에서 모집단 답변을 거부할 최소 coverage 정책 결정 필요
- [ ] ETF-공모펀드 중복 상품의 표시/연결 단위 결정 필요
- [ ] 실시간 시세 또는 외부 API 연동 필요 여부 미정
- [ ] HyperCLOVA X 모델/호출 계약과 임베딩 모델 확정 필요
- [ ] PostgreSQL/Docker 배포 환경과 09-07~09-20 모니터링 환경 확정 필요
- [ ] 채권 `NDY_*` 컬럼 5종의 공식 정의(익일 적용 민평 추정) 확인 필요 — 8/6 설명회
- [ ] `CRD_GRD` 대표값 산정 규칙(복수 평가등급 중 무엇)과 `C0` 137건의 실체 확인 필요
- [ ] 금액·가격 컬럼 단위(발행잔액 원/천원, 평가가격 액면 10,000원당 여부) 확인 필요
- [ ] `du_lpr`의 스키마 한글명 '시가'가 저가(low)의 오기인지 확인 필요 (전체 질문: `external_data/COLLECTION_SUMMARY.md` 5장)

## 다음 작업

1. PostgreSQL DDL과 `fund_master`, `fund_attribute`, `taxonomy_mapping`, quarantine 스키마를 작성한다.
2. 재현 가능한 Excel 적재·정규화·reconciliation 스크립트를 작성한다.
3. 지역·자산군 원본 고유값과 수동 매핑 seed를 만든다.
4. 답변 가능성 상태와 coverage 계산 로직을 구현한다.
5. 대표 사용자 질문 20개와 기대 SQL/답변 상태를 정의한다.
6. 2주 차 안에 구조화 근거를 반환하는 `/answer` 베이스라인을 완성한다.
7. SQL 평가 통과 후 해외 ETF 전략 pgvector 검색을 추가한다.

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

### 2026-08-04 — Tier1 해석 메타데이터 수집·검증 및 전처리 1차 파이프라인

- 목표: 컬럼/값 사전 완성과 원본 함정을 제거한 스테이징 CSV 확보
- 변경: `external_data/dictionaries/` 사전 9종(웹 리서치 428건, 적대 검증 정정 13건 반영), `preprocessing/preprocess.py`와 `preprocessing/processed/`(CSV 4종·quarantine·규칙 리포트 56건), `preprocessing/PREPROCESSING_METHOD.md`·`external_data/COLLECTION_METHOD.md`·`ROADMAP.md`·`preprocessing/profiling/` 추가
- 검증: 행수·키 assertion 통과(42,394 / 1,734 / 5,646 / 95,618+격리 1). 격리 1행이 기존에 확인된 컬럼 밀림 행(`itm_no='"'`)과 일치함을 확인. 별칭 110건·신용등급 45건·채권약어 40건은 독립 에이전트 반박 검증 수행
- 결정: 해외 ETF 기초지수 센티널 2종 → null 정규화, `or_attr_desc='06'` 보존 유지, 상품군 공통 `drv_risk_grade`(1~6) 파생으로 위험등급 통일, 신용등급은 `drv_crd_grd_rank`(AAA=1~D=20)로 서열화
- blocker: `NDY_*` 공식 정의, `CRD_GRD` 대표값 규칙, 금액 단위 미확인 — 8/6 설명회 질문 리스트로 정리(`external_data/COLLECTION_SUMMARY.md` 5장)
- 다음: PostgreSQL DDL 작성과 적재(fund master/attribute 분리), taxonomy_mapping seed에 값사전·코드표 활용

### 2026-08-05 — 브랜치 분리 정리 (main 상속 파일 제거)

- 목표: `papuagigi`를 main·타 브랜치와 분리해 개인 산출물만 담는 브랜치로 만든다
- 변경: `PROJECT_GUIDE.md`·`manifest/` 삭제, `datasets/` 추적 해제(`.gitignore`, 디스크에는 유지), `.gitignore` 신규, 두 스크립트에 `MIRAE_DATASETS` 환경변수 지원과 원본 폴더 부재 시 안내 메시지 추가, `README.md` 개편, 끊어진 참조(PROJECT_GUIDE·datasets·manifest) 정리
- 검증: `preprocess.py`·`profile_data.py` 재실행으로 동일 산출물 확인. `git ls-files`에 datasets/manifest/PROJECT_GUIDE 없음 확인
- 결정: 원본 xlsx는 참가자 전원 보유 → 저장소에 담지 않는다. 삭제 파일은 main에 그대로 있으므로 `git show main:<경로>`로 언제든 조회 가능
- 다음: PostgreSQL DDL 작성과 적재(fund master/attribute 분리)

### 2026-08-05 — dev-kyung 브랜치 교차검증 및 반영

- 목표: dev-kyung 브랜치 산출물(`PROJECT_GUIDE.md`, `reports/DATA_QUALITY_REPORT.md`, `pipeline/`, `tests/`, `config/`)을 정독해 이 브랜치 대비 우위 항목을 가려내고 문서에 반영한다
- 변경: `ROADMAP.md` v2.2 — "8/5 브랜치 교차검증" 장 신설(실측 정정 A / 신규 발견 B / 설계 채택 C / 판단 유지 D), 1장 상품군별 `[8/5 정정]` 인라인 반영, 2장 ⑤를 답변 가능성 상태 8종으로 격상, S1·S2·S4 작업표와 8/6 질문 리스트 보강. `preprocessing/PREPROCESSING_METHOD.md` 6장 신설 — R8 정정(만기 16,496→16,180, `MAT_DT=0` 오포함)과 신규 규칙 R24~R30 정의
- 검증: dev-kyung 수치는 `DATA_QUALITY_REPORT.md` 기준 문서 대조로 수용(코드 재실행 검증은 preprocess.py 반영 시). 주요 정정 — `REMAINING_DAYS` 기준일은 행별 `PD_STD_INFO_UPDATE`(중앙값 137일 지연), 국내ETF 총보수 양수 67건뿐, 국내ETP 1,155행은 299행(`KR70193M0005`)의 손상 중복, 유효 `itm_no` 11,138, `(itm_no, prfd_attr_cd)` 2컬럼 유일
- 결정: 아키텍처(파라미터화 도구 + FC)는 유지, 브랜치별 정답률 비교로 최종 판단. dev-kyung에서 채택 — 답변 가능성 상태 머신 8종(coverage 분모=eligible 집합), `time_policy.py` 모듈 이식(as_of_date 이원화), 구조화 retrieved_context + 직렬화 어댑터, HCX 단일 provider 코드 강제, immutable release 운영, 요구사항 추적표, taxonomy 매핑 계약, `manual_overrides.csv` 패턴, 파이프라인 단위 테스트·SHA-256 결정성 검증
- blocker: 총보수 0(국내 150·해외 363)의 의미, 손상 행 2건 정정본, 활성 상품 만기일 부재, 마감 후 장애 대응 허용 범위 — 8/6 질문 리스트에 추가됨
- 다음: `preprocess.py`에 R24~R30 및 R8 정정 구현, dev-kyung `pipeline/time_policy.py`+테스트 이식, S0 공통 계약 4종 초안에 answerability 스키마 포함

### 2026-08-05 — 3인 역할 분담 개편 (수평 분할 폐기)

- 목표: 실제 진행 방식(각자 브랜치 독립 개발 → 3개 브랜치 교차평가 → 최적 설계 통합)과 어긋난 `ROADMAP.md` 5장 A/B/C 트랙표를 정정한다
- 변경: `ROADMAP.md` 5장을 전면 개편 — 5.1 수직 분할(각자 E2E 완주) + 브랜치별 설계 가설표(papuagigi=파라미터화 도구+FC / dev-kyung=allowlist Text-to-SQL / jhnam=S0 확정), 5.2 공통 소유 3종(계약 4종·평가셋 러너·배포 골격 — 중복 금지), 5.3 교차평가 통합 절차 4단계, 5.4 S4 문서 분담, 5.5 기존 공통 계약 4종. Sprint 0에 가설 확정 게이트, Sprint 3에 교차평가 통합 행(8/27~8/29) 추가. "주간 리듬(권장)" 삭제
- 검증: 문서 전체 `A/B/C 트랙` 잔존 참조 grep 확인 후 7장 체크박스도 함께 정정
- 결정: 수평 분할(데이터/검색/서빙)은 브랜치 비교와 양립 불가 — 각 브랜치가 완결된 시스템이어야 정답률 비교가 성립하고, 한 명이 막혀도 전원이 멈추지 않는다. 통합은 **노드 단위 채택표** 기반이며 **통합본이 개별 브랜치 최고 정답률 이상**일 때만 확정(미달 시 롤백)
- blocker: jhnam 브랜치의 설계 가설 미정 — S0 내 확정 필요. 미확정 시 papuagigi 변형으로 수렴해 비교 가치가 사라짐. 공통 소유 3종 담당자도 미지정
- 다음: S0에서 3인 가설 상이성 확인 + 공통 소유 3종 담당자 지정, 공용 회귀 러너 I/O 규격 초안

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
