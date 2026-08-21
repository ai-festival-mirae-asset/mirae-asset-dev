# KG 추출 파이프라인 — 무엇을·왜 이렇게 했는지 (S1 골격 8/11 · 구성종목 확장 8/13 · 온톨로지 5파일 8/19)

> 어휘 정의(클래스·관계·제약)는 `ontology/` 의 **공식 형식 5파일** — [common.ttl](../ontology/common.ttl)(공통 상위 `fp:Product`·기관·지수·공통 속성) + [bond_kr.ttl](../ontology/bond_kr.ttl) · [etf_kr.ttl](../ontology/etf_kr.ttl) · [etf_gl.ttl](../ontology/etf_gl.ttl) · [fund_pub.ttl](../ontology/fund_pub.ttl) — 와 데이터 규칙 [shapes.ttl](../ontology/shapes.ttl)(SHACL, 부속). 전략 배경은 [ROADMAP.md](../ROADMAP.md) §4·§7 참조.

## 1. 무엇

전처리 CSV 4종(`preprocessing/processed/`) + **구성종목 수집분**(`external_data/constituents/`)을 온톨로지 어휘 기반 **N-Triples 지식그래프**로 변환한다.

| 파일 | 역할 |
|---|---|
| `ontology/*.ttl` | 어휘(클래스 15 · 관계 8 · 속성 37, 접두어 `fp: <http://mafest.ai/product#>`) — 공식 지정 파일명 5개 + SHACL 규칙 1개. 8/22 별칭 이름표 `skos:altLabel` 도입(common.ttl §0 — 병합 없이 별칭 선언, [KG_NEXT.md](KG_NEXT.md) 1순위) |
| `kg/build_kg.py` | 추출 파이프라인 — CSV 4종+구성종목 → `kg/output/*.nt` + `build_report.json` |
| `kg/kg_store.py` | 경량 트리플 스토어(spo/pos 인덱스 + 상품명 검색) — 저장소 중립 참조 구현. 서버가 기동 때 적재 |
| `kg/query_kg.py` | 질의 데모 CLI — S1 DoD(CQ1)·역관계(CQ2)·구성종목(CQ6) 검증용 |
| `kg/validate_shacl.py` | SHACL 규칙으로 `.nt` 를 독립 재검사(오프라인 — rdflib·pyshacl) |
| `tests/test_kg.py` | 36개 테스트 — 직렬화·제약·매핑·구성종목·합성 E2E(DoD 질의)·옛 어휘 파일 거부 |
| `tests/test_ontology.py` | 12개 테스트 — 5파일 파싱·공식 예시 선언·**코드 어휘 ⊆ 온톨로지**·SHACL 이 위반을 실제로 잡는지·생성기 출력 규칙 준수 |

```bash
python kg/build_kg.py                                  # 전체 빌드 (약 1분, 구성종목 CSV 있으면 자동 포함)
python kg/query_kg.py "TIGER 200 증권" --tables kr_etf  # CQ1: 운용사는? → 미래에셋
python kg/query_kg.py --company "미래에셋" --tables kr_etf  # CQ2: 역관계
python kg/query_kg.py --holds "삼성전자" --tables constituents   # CQ6: 편입 ETF (8/13)
python kg/query_kg.py --holds "cambricon" --tables constituents  # 해외 종목도 (중-2 유형)
python kg/validate_shacl.py --tables kr_etf --limit 0  # SHACL 규칙 검사 (국내ETF 전량 3.5초)
python -m pytest tests/test_kg.py tests/test_ontology.py -q   # (권한 이슈 시 --basetemp 지정)
```

## 2. 왜 이 구조인가

- **온톨로지는 공식 형식(8/19)**: 주최 자료(과제 설명 p.9)가 `ontology/common.ttl·bond_kr.ttl·etf_kr.ttl·etf_gl.ttl·fund_pub.ttl` 5파일과 접두어 `fp:`·공통 상위 클래스 `fp:Product`·예시 선언(`fp:ForeignETF rdfs:subClassOf fp:ETF`)을 제시했다. 8/11~13 의 단일 `finance.ttl` 어휘를 그대로 계승해 5파일로 나누고, 국내/해외를 클래스로 갈랐다(`fp:DomesticETF·DomesticETN` / `fp:ForeignETF·ForeignETN`). 인스턴스는 **가장 구체적인 클래스 하나로만** 타이핑하고 상위(`fp:ETF`·`fp:Product`)는 온톨로지의 `rdfs:subClassOf` 로 추론되는 몫이다. 코드는 온톨로지에 선언된 항만 쓴다(테스트가 잠금).
- **저장소 중립(N-Triples)**: 표준 직렬화만 출력해 두면 어떤 그래프 저장소든 그대로 적재된다. `kg_store.py`(인메모리, 서버 기동 시 적재)가 현재 저장소이며, 제품 저장소 전환은 필요해질 때(§8.1 — 현재는 불필요 판단).
- **결측 = 트리플 미생성**: 값이 없는 것은 없는 것으로 둔다. 해외ETF에 위험등급 트리플이 없는 것 자체가 "확인할 수 없음" 답변의 근거다(가드레일 §3). 온톨로지도 같은 말을 한다 — `etf_gl.ttl` 은 해외ETP 에 `fp:riskGrade` 최대 개수 0 제한(owl:Restriction)을 선언한다.
- **제약은 두 겹으로 검사**: ① `build_kg.py`가 적재 전에 코드로 검사하고 위반을 `build_report.json`에 집계한다(위험등급 1~6, ETF/ETN disjoint, 9999 센티널 등). ② 같은 규칙을 `ontology/shapes.ttl` 에 SHACL 로 선언해 두고 `kg/validate_shacl.py`(pyshacl)로 독립 재검사한다 — 8/18 기술세션의 "SHACL 검증" 강조에 대응. 서버 실행에는 ②가 필요 없다(오프라인·테스트 전용).
- **결정적**: 타임스탬프 등 휘발 값을 출력에 넣지 않고 수치는 원문 lexical 보존 — 같은 입력이면 같은 출력(전처리 파이프라인과 동일 원칙).
- **파일 자기완결**: 각 `.nt`는 참조하는 회사·지수 노드의 타입·라벨을 포함해 단독 적재 가능. 파일 간 중복 트리플은 RDF 집합 의미상 무해.
- **옛 어휘 파일 방어**: 8/18 이전(`mf:` 네임스페이스)에 만든 `kg/output/*.nt` 를 새 코드가 적재하면 그래프 채널이 조용히 "상품 없음"으로 오답할 수 있어, `kg_store` 가 첫 줄에서 옛 네임스페이스를 발견하면 "재생성 필요" 오류로 즉시 멈춘다.

## 3. 모델링 규칙 (요약)

| 대상 | 규칙 | 근거 |
|---|---|---|
| 공모펀드 | `itm_no` 마스터 1노드 + `fp:shareClassCount` · `fp:isOnSale`(sale_yn '판매중'/'판매완료' — Y/N 아님, 8/19 정정) | 그룹 내 변동 컬럼은 `prfd_attr_cd` 뿐(8/5 검증) — 클래스 행 95,618 → 마스터 11,138 |
| ETF/ETN × 국내/해외 | `drv_instrument_type` × 마스터 → `fp:DomesticETF`·`DomesticETN`·`ForeignETF`·`ForeignETN`, 불명은 상위클래스(ETP)로만 타이핑 | disjoint 보호 — 불명 행을 ETF로 단정하지 않음 · 공식 예시 클래스 |
| ETN 규모 | `fp:aum` 미적재(`du_last_aum` 전량 0 실측), `fp:netAssets`만 | dev-kyung 실측(8/5) — shapes.ttl `fp:EtnShape` 도 검사 |
| 영구채 | `fp:maturityDate` 미생성 + `fp:isPerpetual=true` | 센티널 99991231 — shapes.ttl `fp:BondShape` sh:or |
| 위험등급 | 1~6 범위 밖 값 거부 + 리포트 · 해외ETP 는 속성 없음 | 무결성 — "99등급" 차단, 실측 1~6 우선(§8.4) · 채권도 위험등급 있음(신용등급과 별개 축, 8/19) |
| 회사·지수 노드 | **원시 표기 기준** URI(`fpr:company/{슬러그}`), 펀드 운용사는 코드 노드 | entity resolution은 검색 계층 — 아래 6장 발견 사항 참조 |
| 별칭(8/22) | 노드 병합 없이 `skos:altLabel` 이름표 — 운용사 정식명(별칭 사전 국내ETF브랜드·해외운용사) · 지수 통칭(정규화 동등만) · 해외 종목 한글명(constituent_aliases.csv). 검색은 정식명+별칭 한 색인, 같은 별칭 다중 노드는 합집합 | "미래에셋자산운용" 질의가 원시 표기 "미래에셋" 노드를 못 찾던 실측 결함 해소 — [KG_NEXT.md](KG_NEXT.md) 1순위, 이름표 218개 |
| 잔존만기 | 저장하지 않음 — 요청 시점 재계산 | `pipeline/time_policy.py` (as_of 이원화) |

## 4. 실측 결과 (2026-08-22 전체 재빌드 — 별칭 이름표 추가 후)

| 테이블 | 입력 행 | 상품 노드 | 참조 노드 | 트리플 | 제약 위반 |
|---|---|---|---|---|---|
| 국내채권 | 42,394 | 42,394 | 발행기관 8,018 | 740,914 | 0 |
| 국내ETF | 1,733 | 1,733 (ETF 1,201 · ETN 532) | 115 | 28,580 (별칭 +28) | 0 |
| 해외ETF | 5,646 | 5,646 (ETF 5,587 · ETN 59) | 2,103 | 86,212 (별칭 +126) | 0 |
| 공모펀드 | 95,618 | **11,138**(마스터) | 457 | 181,686 (+11,138 = 판매중 여부 신규) | 0 |
| 구성종목 | 75,081 | ETF 923 · 종목 6,819 | — | 91,050 (관계 67,205 · 별칭 +64) | 0 |
| 합계 | — | 60,911 상품 | — | **1,128,442** (별칭 이름표 218 포함) | 0 |

별칭 검증(8/22): 정식명 질의 `find_company_products("미래에셋자산운용")` → 원시 표기 "미래에셋" 노드 상품 목록 ✅ · `find_holding_etfs("캠브리콘")` → CAMBRICON 16종목(그래프 선언 별칭만으로) ✅

SHACL 재검사(8/19): 국내ETF 전량(28,552 트리플, 3.5초) + 채권·해외ETF·펀드·구성종목 표본 각 30,000줄(120,006 트리플, 12.7초) → **위반 0건**. (전량 검사는 채권 133MB 기준 수 분 이상 — 필요할 때 `--limit 0`.)

DoD 검증: `"TIGER 200 증권"` 질의 → 국내ETF · 운용사 미래에셋 · 위험등급 2 · 근거(PREF01N001 · KR7102110004 · 기준일 2026-07-11) ✅ / 역관계(미래에셋 → 운용 상품 목록) ✅

## 5. 구성종목 확장 (8/13 — CQ6 가동)

KRX 수집분(`external_data/constituents/constituents_20260710.csv`)을 `constituents.nt`로 변환한다. **기준일이 마스터(7/11)와 다르다: 2026-07-10(직전 거래일) 조회분.**

**membership 적재 정책** (실측 SECUGRP_ID 분포 기반 — `CONSTITUENTS_PLAN.md` 3장):

| 편입분 | 정책 | 키 |
|---|---|---|
| 국내 상장 증권 — 주식 ST(20,905)·리츠 RT(137)·인프라 IF(19)·DR(2)·상장펀드 MF(1) | ✅ `fp:holdsConstituent` → `fp:ListedCompany` | `fpr:company/krx-{6자리}` + `fp:tickerCode` |
| **해외 상장 주식** — SECUGRP 없음 + 비KR ISIN (~25,400행: 미국 19,449·중국 1,686·일본 861 등) | ✅ 동일 관계 | `fpr:company/isin-{ISIN}` + `fp:securityIsin` |
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

> 8/22 연구·구현: 발전 후보 7개를 비교해 1순위(별칭 통합 skos:altLabel)·2순위(국내 벡터 확장 — 합성 문장)를 **당일 구현**했고, 채권·펀드 임베딩은 실측 근거로 만들지 않기로 결정 — [KG_NEXT.md](KG_NEXT.md).

1. 운용사 오염 55행 정정 방침 결정(8/8 총검토 또는 팀 논의) → 전처리 규칙 반영 후 재빌드
2. 회사·지수 entity resolution — 별칭 사전(`alias_dictionary.csv`) 기반 노드 통합 + **해외 종목 한글명 매핑("캠브리콘"→CAMBRICON, 8/13 신규)**
3. 저장소 선정(8/8) 후 `.nt` 적재 + 질의 채널(Federated Router의 그래프 채널) 연결
4. ~~ETF 구성 종목 → `fp:holdsConstituent`~~ **8/13 완료(국내 KRX 부분 수집분, CQ6 가동)** → 잔여: 전량 수집 후 재빌드 · `fp:subsidiaryOf`(자회사 — CQ7 멀티홉, DART 후보) · ETF-of-ETF(EF 편입분) 상품 간 관계 검토 · 해외ETF 마스터(PREF02N001)의 구성종목 소스
