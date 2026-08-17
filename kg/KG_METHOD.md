# KG 추출 파이프라인 — 무엇을·왜 이렇게 했는지 (S1 골격 8/11 · 구성종목 확장 8/13)

> 어휘 정의(클래스·관계·제약)는 [ontology/finance.ttl](../ontology/finance.ttl), 전략 배경은 [ROADMAP.md](../ROADMAP.md) §4·§7 참조.

## 1. 무엇

전처리 CSV 4종(`preprocessing/processed/`) + **구성종목 수집분**(`external_data/constituents/`)을 온톨로지 어휘 기반 **N-Triples 지식그래프**로 변환한다.

| 파일 | 역할 |
|---|---|
| `kg/build_kg.py` | 추출 파이프라인 — CSV 4종+구성종목 → `kg/output/*.nt` + `build_report.json` |
| `kg/kg_store.py` | 경량 트리플 스토어(spo/pos 인덱스 + 상품명 검색) — 저장소 중립 참조 구현 |
| `kg/query_kg.py` | 질의 데모 CLI — S1 DoD(CQ1)·역관계(CQ2)·구성종목(CQ6) 검증용 |
| `tests/test_kg.py` | 30개 테스트 — 직렬화·제약·매핑·구성종목·합성 E2E(DoD 질의) |

```bash
python kg/build_kg.py                                  # 전체 빌드 (약 1분, 구성종목 CSV 있으면 자동 포함)
python kg/query_kg.py "TIGER 200 증권" --tables kr_etf  # CQ1: 운용사는? → 미래에셋
python kg/query_kg.py --company "미래에셋" --tables kr_etf  # CQ2: 역관계
python kg/query_kg.py --holds "삼성전자" --tables constituents   # CQ6: 편입 ETF (8/13)
python kg/query_kg.py --holds "cambricon" --tables constituents  # 해외 종목도 (중-2 유형)
python -m pytest tests/test_kg.py -q                   # (권한 이슈 시 --basetemp 지정)
```

## 2. 왜 이 구조인가

- **저장소 중립(N-Triples)**: 그래프 저장소 제품(rdflib/Neo4j/AGE)은 8/8 총검토 안건(ROADMAP §8.1)이라 아직 미정. 표준 직렬화만 출력해 두면 어느 후보든 그대로 적재된다. `kg_store.py`는 그 결정 전에 "그래프에서 답할 수 있다"를 검증하기 위한 최소 구현이며 최종 저장소가 아니다.
- **결측 = 트리플 미생성**: 값이 없는 것은 없는 것으로 둔다. 해외ETF에 위험등급 트리플이 없는 것 자체가 "확인할 수 없음" 답변의 근거다(가드레일 §3).
- **제약은 적재 전 코드 검증**: 온톨로지의 범위형 제약(위험등급 1~6, ETF/ETN disjoint)을 `build_kg.py`가 검사하고 위반을 `build_report.json`에 집계한다. SHACL 선언 이관은 8/8 검토.
- **결정적**: 타임스탬프 등 휘발 값을 출력에 넣지 않고 수치는 원문 lexical 보존 — 같은 입력이면 같은 출력(전처리 파이프라인과 동일 원칙).
- **파일 자기완결**: 각 `.nt`는 참조하는 회사·지수 노드의 타입·라벨을 포함해 단독 적재 가능. 파일 간 중복 트리플은 RDF 집합 의미상 무해.

## 3. 모델링 규칙 (요약)

| 대상 | 규칙 | 근거 |
|---|---|---|
| 공모펀드 | `itm_no` 마스터 1노드 + `mf:shareClassCount` | 그룹 내 변동 컬럼은 `prfd_attr_cd` 뿐(8/5 검증) — 클래스 행 95,618 → 마스터 11,138 |
| ETF/ETN | `drv_instrument_type`으로 클래스 분리, 불명은 상위클래스(ETP)로만 타이핑 | disjoint 보호 — 불명 행을 ETF로 단정하지 않음 |
| ETN 규모 | `mf:aum` 미적재(`du_last_aum` 전량 0 실측), `mf:netAssets`만 | dev-kyung 실측(8/5) |
| 영구채 | `mf:maturityDate` 미생성 + `mf:isPerpetual=true` | 센티널 99991231 |
| 위험등급 | 1~6 범위 밖 값 거부 + 리포트 | 무결성 — "99등급" 차단, 실측 1~6 우선(§8.4) |
| 회사·지수 노드 | **원시 표기 기준** URI(`mfr:company/{슬러그}`), 펀드 운용사는 코드 노드 | entity resolution은 후속 — 아래 4장 발견 사항 참조 |
| 잔존만기 | 저장하지 않음 — 요청 시점 재계산 | `pipeline/time_policy.py` (as_of 이원화) |

## 4. 실측 결과 (2026-08-11 전체 빌드)

| 테이블 | 입력 행 | 상품 노드 | 참조 노드 | 트리플 | 제약 위반 |
|---|---|---|---|---|---|
| 국내채권 | 42,394 | 42,394 | 발행기관 8,018 | 740,914 | 0 |
| 국내ETF | 1,733 | 1,733 | 115 | 28,552 | 0 |
| 해외ETF | 5,646 | 5,646 | 2,103 | 86,086 | 0 |
| 공모펀드 | 95,618 | **11,138**(마스터) | 457 | 170,548 | 0 |
| 합계 | 145,391 | 60,911 | — | **1,026,100** | 0 |

DoD 검증: `"TIGER 200 증권"` 질의 → ETF · 운용사 미래에셋 · 위험등급 2 · 근거(PREF01N001 · KR7102110004 · 기준일 2026-07-11) ✅ / 역관계(미래에셋 → 운용 상품 목록) ✅

## 5. 구성종목 확장 (8/13 — CQ6 가동)

KRX 수집분(`external_data/constituents/constituents_20260710.csv`)을 `constituents.nt`로 변환한다. **기준일이 마스터(7/11)와 다르다: 2026-07-10(직전 거래일) 조회분.**

**membership 적재 정책** (실측 SECUGRP_ID 분포 기반 — `CONSTITUENTS_PLAN.md` 3장):

| 편입분 | 정책 | 키 |
|---|---|---|
| 국내 상장 증권 — 주식 ST(20,905)·리츠 RT(137)·인프라 IF(19)·DR(2)·상장펀드 MF(1) | ✅ `mf:holdsConstituent` → `mf:ListedCompany` | `mfr:company/krx-{6자리}` + `mf:tickerCode` |
| **해외 상장 주식** — SECUGRP 없음 + 비KR ISIN (~25,400행: 미국 19,449·중국 1,686·일본 861 등) | ✅ 동일 관계 | `mfr:company/isin-{ISIN}` + `mf:securityIsin` |
| 현금성(원화현금·CP·CD·스왑 — KRD/KRZ 코드)·채권 BN·파생 FU/OP·해외선물 심볼 | ❌ 그래프 제외 | CSV(SQL 채널)에 보존 |
| ETF/ETN 편입분 EF(248)·EN(7) — 회사가 아니라 상품 | ❌ 제외 (ETF-of-ETF 관계는 후속 검토) | CSV 보존 |

- **비중(COMPST_RTO)·수량 등 수치는 그래프에 넣지 않는다** — SQL 채널 소관(채널 역할 분담). 그래프는 membership 관계만.
- **해외 종목 이름 변형**: 운용사마다 표기가 달라("CAMBRICON TECHNOLOGIES-A" vs "Cambricon Technologies Corp Ltd") ISIN 키로 통합하고 **표기 전부를 `rdfs:label` 복수 보존** — 이름 검색 성립 조건. 한글명("캠브리콘")→영문 매핑은 별칭 사전 후속.
- 실측(**전량 1,139종목 · 현금 센티널 정정 후, 8/13**— 입력 75,081행): **ETF 923 · 종목(회사) 6,819 · 관계 67,205(국내 32,959 + 해외 34,246) · 트리플 90,986**. 관계 없는 216종목 = **61 순수 비주식 구성**(채권·파생 — 만기형 회사채 ETF 등) + **7 해당일 PDF 빈 응답**(TDF 등) + **148 현금 센티널만 보유**(아래). 정상 동작.
- **현금 센티널 버그 정정(8/13)**: `CASH00000001`(설정현금액)·`USDZZ0000001`(USD현금·예금)·`JPYZZ0000001` 이 ISIN 형식을 통과해 **가짜 회사 노드 3개·685관계**가 생겼었다 — 이름(현금·예금·설정현금액)·코드(CASH 접두) 이중 차단으로 수정, 회귀 테스트 고정. 이 정정으로 "설정현금액만 보유"였던 148종목이 관계 0으로 정상화됐다.
- 검증: `--holds 삼성전자` → 229종목(우선주 005935 별도 구분) / `--holds cambricon` → 16종목(차이나테크TOP10 등) — **중-2 유형("캠브리콘 편입 ETF")이 국내 상장 ETF 범위에서 답변 가능함을 실증** / `--holds 에코프로비엠` → 66종목(상-2 유형의 구성종목 부품).
- 커버리지 표기: 답변 시 "수집분 N종목 기준"을 명시한다(query_kg 데모에 포함) — 재수집·재빌드 시 자동 갱신되는 구조.

## 6. 구축 중 발견 — 국내ETF 운용사 컬럼 오염 (8/11 발견 → **8/13 복구 완료**)

`cu_fund_mgmt_co`(운용사)에 회사명이 아니라 **상품명 전체(공백 없는 형태)가 들어간 행 54건 + 브랜드 결합값("한화PLUS"·"미래에셋TIGER"·"삼성KODEX") 10건** = 오염 64행 실측.

- **복구(8/13)**: `pipeline/mgmt_resolution.py` — 운용사+브랜드 접두 매핑("미래에셋TIGER…"→미래에셋, "삼성KODEX…"→삼성 등 15조합)으로 결정적 복구. **전 1,733행 검증: recovered 54 · brand_split 10 · as_is 1,669 · unresolved 0(100%)**. 규칙 밖 오염은 원시값 유지+플래그(조용한 오귀속 금지).
- 원칙: 원천 CSV·KG 원시값은 보존하고 검색·집계 계층이 복구값을 쓴다. **"삼성"(자산운용)·"삼성액티브"·"삼성증권(주)"는 별개 법인 — 복구만 하고 병합하지 않는다**(법인 통합 entity resolution 은 별도).
- 해외ETF의 유사 의심 건은 실제 사명("First Trust Advisors LP" 등)으로 확인 — 오염 아님.

## 7. 후속 (우선순위)

1. 운용사 오염 55행 정정 방침 결정(8/8 총검토 또는 팀 논의) → 전처리 규칙 반영 후 재빌드
2. 회사·지수 entity resolution — 별칭 사전(`alias_dictionary.csv`) 기반 노드 통합 + **해외 종목 한글명 매핑("캠브리콘"→CAMBRICON, 8/13 신규)**
3. 저장소 선정(8/8) 후 `.nt` 적재 + 질의 채널(Federated Router의 그래프 채널) 연결
4. ~~ETF 구성 종목 → `mf:holdsConstituent`~~ **8/13 완료(국내 KRX 부분 수집분, CQ6 가동)** → 잔여: 전량 수집 후 재빌드 · `mf:subsidiaryOf`(자회사 — CQ7 멀티홉, DART 후보) · ETF-of-ETF(EF 편입분) 상품 간 관계 검토 · 해외ETF 마스터(PREF02N001)의 구성종목 소스
