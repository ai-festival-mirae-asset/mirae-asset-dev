# 미래에셋 AI Festival 에이전트 프로젝트 가이드

> 기준일: 2026-08-03  
> 데이터 추출 기준일: 2026-07-11  
> 연속 작업 기록: [`memory.md`](./memory.md)

## 1. 문서 목적

이 문서는 `README.md`, `datasets/`, `manifest/2.manifest`와 사용자가 제공한 데이터 분석 메모를 바탕으로 프로젝트의 구현 순서, 데이터 계약, 답변 가능성 판정, 검증 기준을 정리한다.

프로젝트의 핵심은 LLM 프롬프팅이 아니라 **데이터 정규화와 답변 불가 판정**이다. 실제 질문은 필터·정렬·순위·집계·비교가 중심이므로 Text-to-SQL을 주 경로로 사용하고, 벡터 검색은 해외 ETF 운용전략처럼 의미 검색이 필요한 텍스트에만 제한한다.

답변은 “추천”보다 **주어진 조건에 부합하는 상품과 그 근거를 제시하는 것**을 기본으로 한다. 원본에 없는 값, 결측이 많은 값, 중의적인 조건을 LLM이 임의로 보완하지 않도록 데이터 계층에서 먼저 판정한다.

## 2. 현재 상태 요약

- 현재 단계: 데이터 탐색 완료, 정규화 파이프라인 설계
- 보유 자산: Excel 원본/스키마 4세트와 Lance manifest 1개
- 소스 코드, 실행 환경, 테스트, 평가 데이터: 아직 없음
- 전체 원본 행: 145,393행
- 전체 원본 행은 고유 상품 수가 아니다. 공모펀드 95,619행은 11,139개 `itm_no`에 평균 8.584개의 속성코드가 결합된 구조다.
- 1차 구현 방향: PostgreSQL + Text-to-SQL + 명시적 답변 가능성 판정
- 보조 검색 방향: `pg_trgm` 상품명 검색, `pgvector` 해외 ETF 전략 검색
- Lance manifest는 불완전하지만 Text-to-SQL 베이스라인의 blocker는 아니다.

## 3. 데이터 인벤토리

모든 datarows 파일의 실제 컬럼은 대응 schema 파일과 대소문자를 무시했을 때 정확히 일치한다. schema 파일의 `PK/FK` 값은 모두 비어 있으므로 아래 키는 원본 검증을 통해 정한 후보 키다.

| 코드 | 도메인 | 원본 행 | 컬럼 | 선언 타입 | 검증된 행 키 | 핵심 관찰 |
|---|---|---:|---:|---|---|---|
| `PRBD01N001` | 국내채권 | 42,394 | 40 | text 14, double 25, bigint 1 | `PD_NO` | 공백·중복 없이 유일 |
| `PREF01N001` | 국내 ETF/ETN | 1,734 | 73 | text 34, numeric 38, timestamp 1 | `pd_itm_no` | `pd_itm_no_ma`도 공백·중복 없이 유일 |
| `PREF02N001` | 해외 ETF | 5,646 | 49 | text 31, numeric 17, timestamp 1 | `pd_itm_no` | `pd_itm_no_ma`도 유일. `pd_isin_cd`는 공백 9행과 중복 50행이 있어 키로 부적합 |
| `PRFD01N001` | 공모펀드 | 95,619 | 45 | text 35, numeric 10 | `(itm_no, prfd_attr_cd)` | 조합 키는 유일. `itm_no`만 보면 11,139종이고 각 번호에 1~16개 속성 변형이 존재 |

원본과 schema 파일은 [`datasets/`](./datasets)에 있다. 각 schema 파일에는 다음 시트가 있다.

- `Sheet1_Schema`: 컬럼명, 타입, 한글명, 예시
- `Sheet2_Sample`: 100개 샘플 및 일부 데이터셋의 분류 축 예시

### 데이터셋별 주요 필드

- 국내채권: 상품번호·이름·발행자·채권종류·통화·발행/만기일·표면금리·신용등급·수익률·듀레이션·평가가격
- 국내 ETF/ETN: 상품번호·이름·운용사·기초지수·전략·보수·가격/NAV·수익률·AUM·위험등급·투자자산·지역
- 해외 ETF: 티커/상품번호·ISIN·이름·운용사·기초지수·전략 설명·보수·가격·AUM·통화·자산유형·지역
- 공모펀드: 상품번호·속성코드·이름·벤치마크·통화·환헤지·투자지역·수익률·순자산·펀드 속성·위험등급

### 정규화를 강제하는 핵심 사실

#### 공모펀드 행 폭증

- 동일 `itm_no` 안에서 달라지는 컬럼은 trim 기준으로 `prfd_attr_cd` 하나뿐이다.
- `fund_master`는 `itm_no` 기준 11,139행, `fund_attribute`는 `(itm_no, prfd_attr_cd)` 기준 95,619행의 N:M bridge로 분리한다.
- 분리하지 않으면 상품 수가 평균 8.584배 부풀고 같은 상품이 순위 결과를 반복 점유한다.
- 단, 공모펀드 Excel 84,563행은 컬럼이 밀린 비정상 레코드다. `itm_no='"'`, `exchdg_yn='00080008'`처럼 타입/도메인 규칙을 위반하므로 자동 적재하지 말고 quarantine 후 원본 재확인 또는 복구한다.

#### 위험등급 체계

| 상품군 | 원본 값 | 표준화 |
|---|---|---|
| 국내 ETF/ETN | `PD_RISK_GCD_11` ~ `_16` | 1=매우 높은 위험 ~ 6=매우 낮은 위험 |
| 공모펀드 | `1` ~ `6`, `NULL` | 1=매우 높은 위험 ~ 6=매우 낮은 위험, `NULL`은 미제공 |
| 국내채권 | `0` ~ `6` | 1=매우 높은 위험 ~ 6=매우 낮은 위험, 0은 미분류 58건 |
| 해외 ETF | 컬럼 없음 | `risk_grade=NULL`, `risk_available=false` |

“위험등급이 낮은 상품”은 숫자가 낮다는 뜻과 위험도가 낮다는 뜻이 충돌한다. “저위험/위험이 낮은”은 기본적으로 표준등급 5~6으로 해석해 그 기준을 답변에 밝히고, 표현이 모호하면 역질문한다.

#### 핵심 조건의 결측과 무정보 값

| 데이터 | 컬럼/조건 | 유효 범위 | 처리 원칙 |
|---|---|---:|---|
| 국내 ETF | `cu_charge_rt` 총보수 | 217/1,734 (12.5%) | 값이 있는 범위에서만 비교하고 coverage 표시 |
| 국내 ETF | `cu_base_index` 기초지수 | 58/1,734 (3.3%) | 나머지 공백 문자열은 null 처리 |
| 국내 ETF | `pd_sect_nm`, `ru_mkt_price`, `ru_mkt_volume`, `nru_mkt_inav`, `nru_mkt_diff_rt` | 0/1,734 | 해당 컬럼 기반 질문은 답변 불가 |
| 국내 ETF | `du_chas_errt`, `du_diff_rt`, `pd_dvid_yield` | 비결측값이 전부 0 | 실제 0인지 미수집 대체값인지 확인 전 순위/비교 금지 |
| 국내채권 | 매수·세전/세후수익률 계열 | 881/42,394 (2.1%) | 관측 범위와 기준일 표시 |
| 해외 ETF | `cu_charge_rt` 총보수 | 5,646/5,646 (100%) | 전체 범위 비교 가능 |

국내채권은 `MAT_DT < 20260711` 기준 16,496건이 이미 만기 도래했다. 같은 날 만기인 7건까지 포함할지는 정책으로 고정하되, 기본 검색에서는 만기 상품을 제외하고 사용자가 과거/만기 상품을 요구한 경우만 포함한다. `BUYABLE_QUANTITY > 0`인 행은 325건뿐이므로 “매수 가능”의 정의도 별도 검증해야 한다.

#### Taxonomy 불일치

- 투자지역은 국내 ETF 한글 11종, 해외 ETF 영문 59개 비결측값과 공백, 공모펀드 한글 7종과 공백으로 표현 체계가 다르다.
- 자산군도 국내 ETF의 `주식/채권/...`, 해외 ETF의 `Equity/Bond/Alternatives/...`, 공모펀드의 `주식형/재간접/06/...`처럼 다르다.
- 공모펀드에는 “미국”이 없고 `남미/북미`로 묶여 있으며, 해외 ETF에는 `United States of America`, `Global Ex US` 같은 세부 표현이 있다.
- 런타임 LLM 매핑을 금지하고, 원본 값별 `taxonomy_mapping` 테이블을 사람이 검토하여 버전 관리한다.

#### 문자열과 코드 오염

- 공모펀드 위험명은 `높은 위험` 29,088행과 `높은위험` 163행처럼 공백 유무가 다르다.
- 공모펀드 `or_attr_desc='06'`은 5,436행, 686개 `itm_no`에 나타난다. 결측으로 버리지 말고 파생형 상품 코드 후보로 별도 매핑·검증한다.
- 공모펀드 `zrin_fd_ivst_risk_gcd='NULL'`은 18,416행이며 실제 null로 정규화한다.
- 국내채권과 국내 ETF의 다수 문자열은 고정 폭 패딩이 있어 trim 전에 조인·필터하면 누락된다.
- 국내 ETF 데이터에는 ETF 1,202건과 ETN 532건이 함께 있으므로 `product_type`을 구분한다.

### 샘플 시트의 파생 분류값

다음 값은 datarows 원본 컬럼이 아니라 일부 schema 파일의 샘플 시트에만 있다. 학습 정답 또는 목표 taxonomy일 가능성이 있지만 생성 근거가 없으므로 원본 사실로 취급하면 안 된다.

- 국내채권: `axis_issuerType`, `axis_maturityClass`, `axis_couponType`, `axis_creditRating`, `axis_collateralType`, `axis_currency`, `axis_issuanceMarket`, `axis_issuerCategory`, `listingCountry`, `issuerCountry`
- 국내 ETF: `legacy_leaf`, `axis_assetType`, `axis_region`, `axis_strategy`, `axis_replicationMethod`, `axis_leverageType`, `axis_underlyingScope`, `axis_distributionType`
- 공모펀드: `axis_fundType`, `axis_redemptionType`, `axis_issuanceType`, `axis_listingType`, `axis_classDifferentiation`, `axis_investorEligibility`
- 해외 ETF 샘플에는 별도 `axis_*` 컬럼이 없다.

## 4. Lance manifest 분석

[`manifest/2.manifest`](./manifest/2.manifest)는 366바이트의 Lance 바이너리 manifest이며 다음 정보를 담는다.

| 필드 | 저장 타입 | 용도 추정 |
|---|---|---|
| `id` | string | 검색 문서 식별자 |
| `text` | string | 임베딩 및 검색 대상 텍스트 |
| `vector` | fixed-size list of float, 1536차원 | 임베딩 벡터 |
| `attributes` | string | 메타데이터 직렬화 값으로 추정 |

추가로 Lance 라이브러리 `0.20.0`, 저장 형식 `2.0`, fragment `fe6e565a-2b59-416b-a2f2-e45ed1a65462.lance` 참조가 확인된다. 그러나 저장소에는 이 fragment와 트랜잭션 파일이 없다. 따라서 다음 중 하나가 필요하다.

1. 원래 Lance 데이터셋 디렉터리 전체를 복구한다.
2. Excel 원본에서 검색 문서와 임베딩을 다시 생성한다.

1536차원만으로 임베딩 모델을 식별할 수 없다. 재생성 시 모델명, 모델 버전, 벡터 차원, 거리 함수, 생성 시각을 별도 메타데이터로 기록해야 한다. `attributes`가 JSON이라면 JSON Schema 또는 필드 계약도 함께 고정한다.

벡터 검색의 1차 대상은 해외 ETF `cu_strtegy`로 제한한다. 5,646행 중 5,638행에 값이 있고 서로 다른 비결측 전략 문서가 5,566개이므로 “커버드콜 전략”, “반도체 집중” 같은 의미 검색에 적합하다. 수치 필터·정렬·집계에는 벡터를 사용하지 않는다. 기존 Lance 복구는 선택 사항이며, PostgreSQL `pgvector`로 통합하면 운영 구성이 단순해진다.

## 5. 통합 데이터 모델

PostgreSQL 안에서 원본, 정규화 상품, 공통 조회 계층을 분리한다.

### 원본 및 상품군별 테이블

- `raw_*`: 원본 행과 1:1로 보존하고 `source_file`, `source_row_number`, `extracted_at`, `quality_status`를 추가한다.
- `bond_master`: 국내채권 상세값과 `is_matured`, `is_buyable` 파생값
- `domestic_etp_master`: 국내 ETF/ETN 상세값과 명시적인 `instrument_type`
- `overseas_etf_master`: 해외 ETF 상세값과 `cu_strtegy` 원문
- `fund_master`: `itm_no` 기준 논리 상품. 정상화 후 예상 11,139행이며 비정상 원본 1건의 복구 결과에 따라 재검증한다.
- `fund_attribute`: `(itm_no, prfd_attr_cd)` bridge. 비정상 원본은 quarantine에서 복구되기 전까지 제외한다.
- `taxonomy_mapping`: 원본 분류값을 표준 지역·자산군으로 연결하는 수동 검토 매핑
- `data_quality_issue`: quarantine, 결측률, 상수값, 매핑 실패 이력

### 공통 조회 뷰 `product_unified`

교차 상품군 질의는 원본 테이블이 아니라 아래 의미 계층만 조회한다.

```sql
CREATE VIEW product_unified AS
SELECT
  item_id,
  product_type,           -- bond/domestic_etf/domestic_etn/overseas_etf/public_fund
  name,
  name_norm,
  issuer_or_manager,
  asset_class_std,        -- 주식/채권/원자재/혼합/단기자금/부동산/대체/기타
  region_std,             -- 국내/미국/중국/일본/유럽/글로벌/이머징/...
  risk_grade,             -- 1=고위험 ... 6=저위험
  risk_available,
  expense_ratio,
  expense_ratio_available,
  aum,
  aum_available,
  return_1y,
  return_1y_available,
  return_ytd,
  return_ytd_available,
  is_tradable,
  as_of_date,
  source_table,
  source_row_key,
  quality_status
FROM normalized_products;
```

`*_available`은 값을 0으로 대체하기 위한 컬럼이 아니라 “원본에서 관측된 값인가”를 나타낸다. 원본 null을 0으로 `COALESCE`하여 순위나 평균에 넣지 않는다.

국내 ETF 1,734 + 해외 ETF 5,646 + 공모펀드 11,139 = 18,519개 논리 항목에 조건을 충족하는 채권을 추가한다. 채권을 `BUYABLE_QUANTITY > 0`으로 제한하면 325건이므로 약 18,844행이지만, 최종 크기는 `is_tradable` 업무 규칙을 확정한 뒤 고정한다.

### Taxonomy 매핑 계약

```text
source_table
source_column
source_value
standard_dimension     # region / asset_class
standard_code
standard_label
mapping_status         # mapped / ambiguous / unmapped
mapping_version
reviewed_by
reviewed_at
note
```

모든 원본 고유값이 `mapped` 또는 의도적인 `ambiguous/unmapped` 상태인지 CI에서 검사한다. 새 값이 들어오면 자동 추론하지 않고 배포를 실패시키거나 quarantine한다.

## 6. 정제 규칙

다음 규칙은 ETL 구현 전에 테스트 가능한 계약으로 만든다.

1. 상품번호, 종목코드, 국가/통화코드, `YYYYMMDD` 값은 숫자가 아닌 문자열로 읽는다.
2. 문자열 앞뒤 공백을 제거하되 원본 값은 `raw`에 보존한다.
3. 빈 문자열과 공백만 있는 문자열은 null로 통일한다.
4. 공모펀드 `zrin_fd_ivst_risk_gcd`의 문자열 `NULL` 18,416건은 실제 null로 변환한다.
5. 날짜는 추출일, 상품 기준일, 가격/NAV 기준일, 갱신일로 의미를 분리하고 ISO 8601 형식으로 변환한다.
6. numeric 값은 표시용 문자열이 아니라 계산 가능한 decimal/number로 변환한다. 통화 금액의 정밀도를 유지한다.
7. 원본의 0이 실제 값인지 결측치 대용인지 컬럼별로 판단한다. 일괄 null 변환은 금지한다.
8. 공모펀드를 `fund_master`와 `fund_attribute`로 분리하고, `(itm_no, prfd_attr_cd)` 중복을 금지한다.
9. 공모펀드 비정상 1행은 타입·도메인 검증에서 반드시 quarantine한다. 조용히 밀린 컬럼을 적재하지 않는다.
10. 해외 ETF의 `pd_isin_cd`는 공백 9행과 중복 50행이 있으므로 보조 식별자로만 사용한다.
11. 위험등급은 상품군별 변환 후 1~6으로 통일하고, 원본 코드와 이름을 함께 보존한다.
12. 국내 ETF의 ETF/ETN을 분리하고, 만기채권을 기본 활성 상품에서 제외한다.
13. 분류값은 `taxonomy_mapping`의 버전과 검토 상태를 기록한다.
14. schema 추출 기준일과 가격/NAV/갱신 기준일을 분리한다. 추출일만 보고 값을 “현재”라고 표현하지 않는다.
15. 상품군 간 동일 상품 연결은 별도 alias/relationship 규칙으로 처리한다. 공모펀드와 국내 ETF를 이름만으로 강제 병합하지 않는다.

### 적재 후 필수 assertion

- 원본 행 수: 채권 42,394 / 국내 ETF·ETN 1,734 / 해외 ETF 5,646 / 공모펀드 95,619
- 키 유일성: `PD_NO`, 국내/해외 `pd_itm_no`, 공모펀드 `(itm_no, prfd_attr_cd)`
- 공모펀드 그룹 내 변동 컬럼: `prfd_attr_cd` 외 0개
- 비정상 행: 알려진 1건이 quarantine되거나 명시적으로 복구되었는지 확인
- taxonomy: 미등록 원본 값 0개 또는 승인된 `unmapped` 목록과 정확히 일치
- coverage: 핵심 수치별 분자/분모를 빌드 산출물로 저장
- 날짜: 만기·기준일 파싱 실패 0개 또는 quarantine 목록과 일치
- 원본과 정제본의 탈락·병합·분리 행 수가 reconciliation 보고서로 설명됨

## 7. Text-to-SQL 중심 아키텍처

```text
사용자 질의
  -> 의도/슬롯 추출: 상품군, 조회·비교·랭킹·집계·설명, 조건
  -> 슬롯 정규화: 미국·저위험·ETF/ETN 등 표준 코드 변환
  -> 실행 계획
       ├─ SQL: 수치, 필터, 정렬, 집계, 비교
       ├─ pg_trgm: 상품명 오탈자·부분 일치
       └─ pgvector: 해외 ETF cu_strtegy 의미 검색
  -> 답변 가능성 및 coverage 판정
  -> 구조화 근거 조립
  -> HyperCLOVA X 답변 생성: 근거 밖 추론 금지
```

PostgreSQL 하나를 운영 저장소로 사용한다. 14.5만 원본 행은 별도 GraphDB가 필요한 규모나 관계 구조가 아니며, 상품명 fuzzy 검색과 해외 ETF 전략 벡터까지 `pg_trgm`, `pgvector`로 함께 처리할 수 있다.

Text-to-SQL은 무제한 자유 SQL보다 allowlist 의미 계층과 템플릿/검증기를 우선한다.

- 쿼리 대상은 `product_unified`와 승인된 상세 view로 제한한다.
- DB role은 read-only, SQL은 `SELECT`만 허용한다.
- 허용 컬럼·함수·JOIN을 AST 수준에서 검사하고 행 수와 실행 시간을 제한한다.
- 위험등급, 지역, 자산군은 원본 문자열이 아니라 표준 컬럼만 조회한다.
- SQL 실행 실패 시 LLM이 임의의 답을 만들지 않고 실패 상태를 반환한다.
- LLM 호출은 의도/슬롯 추출 1회와 최종 답변 1회, 최대 2회를 목표로 한다.

## 8. 답변 가능성 판정

SQL 또는 벡터 검색 전에 스키마 지원 여부를 확인하고, 실행 후에는 대상 집합의 coverage와 품질 상태를 확인한다.

| 상태 | 조건 | 응답 원칙 |
|---|---|---|
| `answerable` | 필요한 컬럼과 대상 행 값이 충분함 | 조건, 기준일, 근거와 함께 답변 |
| `partial_coverage` | 컬럼은 있으나 일부 행만 값이 있음 | 관측값만 대상으로 답하고 `available/eligible` 범위를 명시 |
| `unsupported_field` | 상품군에 컬럼 자체가 없음 | “해당 데이터에 정보가 없습니다” |
| `ambiguous_condition` | “위험등급이 낮은” 등 조건이 중의적 | 역질문하거나 적용한 해석을 명시 |
| `insufficient_condition` | 순위·비교에 필요한 범위가 없음 | 상품군·지역·기간 등 필요한 조건을 질문 |
| `no_matching_rows` | 유효한 조건이지만 결과가 0건 | 데이터 부재와 조건 불일치를 구분하여 표시 |
| `stale_or_inactive` | 만기 또는 기준일이 요구에 부적합 | 과거 데이터임을 밝히거나 활성 상품만 재조회 |
| `invalid_data` | quarantine/파싱 오류가 결과에 영향 | 해당 레코드를 제외하고 영향 범위를 표시하거나 답변 중단 |

coverage는 전체 테이블이 아니라 **사용자 조건을 적용한 eligible 집합**을 분모로 계산한다. 예를 들어 국내 ETF 총보수 순위는 “조건에 맞는 N개 중 값이 있는 M개만 비교”라고 출력한다.

- Top-K는 관측값이 K개 이상일 때 관측 범위 내 순위를 제공할 수 있다.
- 평균·비율·“전체에서 가장 낮음” 같은 모집단 주장은 결측이 있으면 전체에 대한 단정으로 표현하지 않는다.
- null과 0을 구분하고, 비결측값이 모두 0인 무정보 의심 컬럼은 확인 전 사용하지 않는다.
- 최종 문장은 “추천합니다” 대신 “요청 조건에 부합하는 상품은 …입니다”를 기본으로 한다.

### 근거와 API 응답

평가 가능한 근거를 자연어 문단이 아니라 구조화 데이터로 반환한다.

```json
{
  "question_id": "...",
  "answer": "...",
  "answerability": {
    "status": "partial_coverage",
    "reason": "expense_ratio is partially populated",
    "eligible_count": 1734,
    "available_count": 217
  },
  "retrieved_context": {
    "source_tables": ["PREF01N001"],
    "columns": ["pd_itm_no", "pd_nm", "cu_charge_rt"],
    "as_of_date": "2026-07-11",
    "filters": [],
    "sql": "SELECT ..."
  },
  "think_trace": [
    {"tool": "sql", "status": "success", "row_count": 3}
  ]
}
```

`think_trace`에는 내부 추론문이 아니라 실제 도구 호출과 검증 이벤트만 기록한다. API 계약은 `GET /answer?question_id=&question=`을 기준으로 하고, 목표 응답시간은 5초 이내로 둔다.

## 9. 5주 실행 로드맵

마감은 2026-09-06, API 운영 예상 기간은 2026-09-07~09-20으로 잡는다. 화려한 Agent loop보다 2주 차에 정확한 베이스라인 엔드포인트를 확보하는 것이 우선이다.

### 1주 차 · 08-03~08-09 — 적재와 정규화

- [ ] Python/PostgreSQL/Docker 실행 환경 고정
- [ ] 네 Excel 원본 적재, schema·행 수·키 assertion 자동화
- [ ] 공모펀드 master/attribute 분리와 비정상 1행 quarantine
- [ ] trim, null, 날짜, 위험등급, ETF/ETN, 만기채권 정규화
- [ ] 지역·자산군 고유값을 추출하고 수동 표준 매핑표 작성
- [ ] 08-06 설명회 공개 예시 질의를 요구사항과 평가셋에 반영

### 2주 차 · 08-10~08-16 — 동작하는 베이스라인

- [ ] `product_unified`와 상품군별 상세 view 구현
- [ ] 의도/슬롯 스키마와 표준화 사전 구현
- [ ] allowlist Text-to-SQL 또는 SQL template 실행기 구현
- [ ] 답변 가능성·coverage 판정기 구현
- [ ] 구조화 `retrieved_context`를 포함한 `/answer` 엔드포인트 확보
- [ ] 대표 질문으로 5초 이내 응답과 LLM 최대 2회 호출 검증

### 3주 차 · 08-17~08-23 — 교차 질의와 의미 검색

- [ ] 지역·자산군 통합 질의와 상품군별 원본 역추적 완성
- [ ] `pg_trgm` 상품명 fuzzy 검색 추가
- [ ] 해외 ETF `cu_strtegy`에 한해 `pgvector` 의미 검색 추가
- [ ] SQL 결과와 벡터 후보의 결합 규칙 및 중복 제거 구현
- [ ] 근거 밖 추론과 단정적 추천을 막는 응답 정책 적용

### 4주 차 · 08-24~08-30 — 100문항 평가

- [ ] 쉬움/보통/어려움 및 답변 불가 사례를 포함한 100문항 작성
- [ ] 조회·랭킹·집계·비교·교차 상품군·전략 검색을 균형 있게 포함
- [ ] SQL 정답률, 답변 가능성 F1, 근거 완전성, P95 지연 측정
- [ ] 결측, 상수 0, 위험등급 중의성, 만기채권, 비정상 행 회귀 테스트
- [ ] 실패 유형별 수정 후 평가 결과를 버전별 보존

### 5주 차 · 08-31~09-06 — 안정화와 제출

- [ ] Docker, 환경변수, 마이그레이션, seed/ingest 절차 고정
- [ ] timeout, 재시도, DB/LLM 장애 fallback과 로깅 구현
- [ ] README, API 명세, 기술제안서, 데이터 lineage 정리
- [ ] 운영 환경 배포 및 health check/부하 테스트
- [ ] 09-07~09-20 상시 운영 모니터링 준비

## 10. 바로 이어서 할 작업

1. PostgreSQL DDL과 `fund_master`, `fund_attribute`, `taxonomy_mapping`, quarantine 스키마를 작성한다.
2. 네 Excel 파일을 재현 가능하게 적재하는 프로파일링/정제 스크립트를 만든다.
3. 지역·자산군 원본 고유값과 초안 매핑표를 CSV 또는 DB seed로 만든다.
4. 답변 가능성 상태와 coverage 계산을 순수 함수/SQL로 먼저 구현한다.
5. 대표 질의 20개를 SQL 기대값과 함께 작성하고 `/answer` 베이스라인을 연결한다.
6. 해외 ETF 전략 검색은 SQL 베이스라인이 통과한 뒤 추가한다.

## 11. 완료 기준

최소한 다음 조건을 만족하면 첫 번째 작동 버전으로 본다.

- 동일 입력으로 PostgreSQL 정규화 스키마와 선택적 벡터 인덱스를 재생성할 수 있다.
- 네 데이터셋의 행 수, 컬럼 수, 키 제약이 자동 검증된다.
- 공모펀드가 논리 상품과 속성 bridge로 분리되고 1건의 비정상 행 처리 이력이 남는다.
- taxonomy의 모든 원본 값이 명시적인 매핑 상태를 가진다.
- 검색 결과에서 원본 파일과 행 키까지 추적할 수 있다.
- 구조화 조건과 SQL 집계가 정확히 적용되고 의미 검색은 허용된 텍스트에만 사용된다.
- 답변의 상품명, 수치, 기준일이 원본과 일치한다.
- 지원 불가, 부분 coverage, 중의성, 결측, 만기 상태를 각각 올바르게 판정한다.
- API가 구조화 근거와 도구 이벤트를 반환하고 정상 질문을 5초 내 처리한다.
- 100문항 평가 결과와 실패 원인이 재현 가능하게 기록된다.
- 답변은 조건 부합 사실을 말하고 단정적 투자 추천을 하지 않는다.
- 주요 결정, 진행 상태, 다음 작업이 [`memory.md`](./memory.md)에 갱신된다.
