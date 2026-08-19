# Tier 1 해석 메타데이터 수집 요약

> 수집일 2026-08-04 · 로드맵 1장 "외부 데이터 수집 전략 — 3-Tier"의 **Tier 1(적극 수집)** 실행 산출물
> 방식: 4종 xlsx 전수 실측(스키마+datarows 고유값) → 8개 도메인 병렬 웹 리서치(에이전트 11개, 출처 필수) → 고위험 3개 도메인 적대적 검증(정정 13건 반영)

---

## 1. 산출물 목록

| 파일 | 행수 | 내용 |
|---|---|---|
| `dictionaries/column_dictionary.csv` | 207 | 4개 테이블 전체 컬럼: 한글명·설명·동의어·단위/포맷·신뢰도·검증상태·출처. **미해결 0건** |
| `dictionaries/value_dictionary.csv` | 423 | 범주형 컬럼(고유값≤60)의 실측 값 원장 + 의미·정규화 규칙 129건 보강 |
| `dictionaries/bond_classification.csv` | 63 | BD_KND 39종·중분류 6종·소분류 16종 정의 + "여전채"류 질의 동의어 |
| `dictionaries/credit_rating.csv` | 45 | AAA~D 서열표(rank), 투자/투기등급 경계, AA0 플랫 표기 규칙, 신평 3사 |
| `dictionaries/risk_grade.csv` | 36 | 펀드/ETF/채권 위험등급 1~6 두 법적 축, 연금 위험/안전자산 구분 |
| `dictionaries/code_table.csv` | 29 | ISO 4217 통화, 미국 거래소(AMX/NAS/NYS), MIC, XS·410 국가코드, 센티널 |
| `dictionaries/alias_dictionary.csv` | 110 | ETF/ETN 브랜드↔운용사, 리브랜딩 연표, 해외 운용사, 투자 은어("곱버스" 등) |
| `dictionaries/base_index.csv` | 62 | 지수명 정규화, TR/PR/NR/CR 접미어, 산출기관, Lipper 센티널 규칙 |
| `dictionaries/fund_class.csv` | 43 | 클래스 문자(A/C/E/P/S/W…) 보수 구조, KOFIA 20자리 코드 구조, 운용속성 |
| `_raw/tier1_research_results.json` | — | 리서치·검증 원본(출처·open questions 포함, 재현/감사용) |

컬럼사전 커버리지: 스키마 제공 118 + 웹 리서치(국내채권) 40 + 교차참조 36 + 약어 해석 13 = **207/207**.
신뢰도 분포: high 183 · medium 21 · low 3 (low: `PD_STD_INFO_UPDATE`, `du_base_dt_match_yn` 등 내부 관리성 컬럼).

---

## 2. 핵심 확인 사항 (가설 → 검증 결과)

**국내채권 약어 — 대부분 확인됨**

- `DEPO_EQUIV_YIELD_154` = **예금환산수익률, 154 = 이자소득세율 15.4%**(소득세 14%+지방소득세 1.4%). 대신증권 장외채권 화면 각주("세후수익률을 이자소득세율로 세전 역산")로 개념 실재 확인.
- `DUR`=듀레이션, `COV`=컨벡시티, `DIRTY`=경과이자 포함 가격, `EVAL_PRICE`=민평가격, `APPLIED_YIELD`=민평 적용수익률 — 공식/증권사 출처로 확인.
- `NDY_*` 5종 = **익일(다음 영업일) 적용 민평값으로 추정** — 민평 데이터의 발표일/적용일 관행(전일 발표분이 익일 기준값)과 정합하나, 약어 자체의 공식 정의는 웹에 없음 → **8/6 확인 필수**.
- `PD_EVCO_CRD_GRD` = 평가사별 신용등급 콤마 병기("AAA, AAA, AAA"). 무보증회사채 복수평가 관행과 일치. 병기 순서↔평가사 매칭은 미확인.
- `EVAL_PRICE` 관련: 금투협 등재 채권평가회사는 **5개사**(키스·한국·나이스P&I·에프앤·이지자산평가), 관행상 "민평 4사 평균" — 검증에서 정정 반영.

**등급 체계**

- `CRD_GRD`의 "AA0" = **무부호(플랫) 표기** 확인. 정규화: 끝자리 `0` 제거(AA0→AA). rank 서열 AAA=1 ~ D=20.
- 질의 변환 규칙 통일(검증 정정): **"AA 이상" = rank≤3**(AAA·AA+·AA), "AA- 이상"·"AA급 이상" = rank≤4. 모호하면 역질문.
- 위험등급 1~6은 **법적 축이 2개 병존**: 2016 금감원 펀드 등급(수익률 표준편차 기준)과 2023 금소법 가이드라인(VaR 기준, 2024.1~ 신규판매분). 등급 명칭은 동일해 데이터 매핑엔 지장 없음.
- 채권 `PD_RISK_GCD`는 **정방향(1=매우 높은 위험) 확인** — 5등급 13,696건(AAA~AA-)·6등급 10,408건(국공채)이 신용등급 분포와 정합. 1등급 15,950건은 무등급(미평가) 채권 → 1등급 매핑 관행으로 추정. `0`(58건)은 미분류 추정.
- **데이터로 검증된 발견**: 국내ETF `pd_pen_risk_nm`의 위험자산 788 + 안전자산 214 = 1,002 = `pd_pen_tr_yn` Y 건수와 정확히 일치 → **N = 연금거래불가 종목**.

**별칭·브랜드 (110건, 검증 106 확인·4 정정)**

- 리브랜딩 연표(구명 질의 대응에 필수): KINDEX→**ACE**(2022-10), SMART→**SOL**(2021-08, 정정), KBSTAR→**RISE**(2024-07-17), ARIRANG→**PLUS**(2024-07-23), KOSEF·히어로즈→**KIWOOM**(2025-01), KTOP→**1Q**(2024-04), QV→**N2**(2024-06, ETN), MASTER→**KCGI**(2024), ITF→**IBK**(2026-05).
- **국내ETF 테이블에 ETN 532건 혼재**(`pd_grp_no`로 구분). 혼동 페어 주의: 삼성증권 ETN vs KODEX/KoAct, KB증권 ETN vs RISE, 미래에셋증권 ETN vs TIGER, 신한투자증권 ETN vs SOL, 한투 ETN vs ACE, 키움 ETN vs KIWOOM, 하나 ETN vs 1Q, 대신 ETN vs DAISHIN343.
- 미래에셋 계열 3주체 구분: **TIGER=미래에셋자산운용(ETF) / '미래에셋 ○○ ETN'=미래에셋증권(주최사) / Global X=미래에셋 해외 자회사**.
- 해외: iShares=BlackRock, SPDR=SSGA, Direxion=Rafferty, YieldMax=Tidal, Xtrackers=DBX, KraneShares=Krane Funds 등 법인명↔브랜드 매핑 수록.

**기초지수·코드**

- 해외ETF `cu_base_index`는 Lipper 표기 `[지수명]+[TR/PR/NR/CR]+[통화]` 구조. **센티널 2종("Index is not provided by Management Company" 1,984 + "Index is not available on Lipper Database" 721)이 약 48%** — 로드맵의 "해외ETF 기초지수 결측 0%"는 **실질결측 48%로 정정 필요**. 기초지수 질의는 해외ETF에서도 절반이 "확인할 수 없음" 대상.
- CR=Capital Return(가격지수) 추정(DJIA CR·비트코인 CR 병존으로 방증, 공식 문서 미확보). NR=MSCI 세후 배당재투자 표준.
- `AMX`(해외ETF 75%)는 NYSE Arca 상장분까지 포괄하는 **레거시 AMEX 표기 관행**으로 추정(medium). 숫자 코드 101/102는 미상.
- `XS`=국제예탁기구(유로본드) ISIN, `410`=ISO 3166-1 숫자코드 한국, `000`류는 센티널.
- **KOFIA 20자리 펀드코드의 6번째 자리=클래스 유형**을 금투협 공식 Q&A로 확인(A→2, C→4, E→6, P→A…). 전체 자리 정의는 금투협 시행세칙 별지 제15호(HWP) 확보 시 완성 가능.

---

## 3. 적재 파이프라인(S1)에 직결되는 정규화 규칙

1. 공백 문자열 → NULL (국내ETF `cu_base_index` 공백 40칸 등)
2. 해외ETF `cu_base_index` 센티널 2종 → NULL
3. `MAT_DT`=99991231 → 영구채 플래그 분리
4. 센티널 → NULL: `CURR_CD='000'`, `pd_curr_cd='CURR_CD_000'`, `kofia_fd_ccd='0'×20`, `or_attr_desc='06'`
5. **행 밀림(shift) 오류 의심 3건 검수**: `exchdg_yn='00080008'`, `thco_sale_yn='KRZ50226929C'`, `zrin_fd_ivst_risk_gcd='00020054'` (각 1건 — 같은 행일 가능성, 적재 시 격리)
6. `CRD_GRD` 정규화: 끝 `0` 제거 + rank 매핑(사전_신용등급.csv) / `PD_EVCO_CRD_GRD` 콤마 분리
7. 플래그 컬럼 NULL=N: `cu_etn_yn`, `cu_inverse_short_yn`, `wu_core_yn`(해외), `cu_index_tracking_yn`(주의: 결측 58%와 동시 발생)
8. `pd_pen_risk_nm='N'` → 연금거래불가로 해석 (실측 검증됨)
9. 해외ETF `du_er_1d` 전 행 0 → 사용 불가 컬럼으로 제외
10. 채권 날짜형 double(`ISU_DT`·`MAT_DT`·`CRD_GRD_DT`·`PD_STD_INFO_UPDATE`) → date 캐스팅
11. `du_lpr` 한글명 "시가"는 저가(low)의 오기 가능성 → 확인 전까지 시가/저가 질의 모두 보수적 처리

---

## 4. 검증(적대적 교차 검증) 정정 13건 요약

| 도메인 | 정정 | 내용 |
|---|---|---|
| 채권용어 | 7건 | EVAL_PRICE 평가사 "4사"→금투협 등재 5개사(관행상 4사 평균), NDY_* 5종 출처 정밀화(익일 적용 민평 추정으로 하향), PREF_TAX_YIELD 세금우대저축 폐지(2015) 반영 |
| 신용등급 | 2건 | 서울신용평가≠SCI평가정보(별개 회사, 회사채 인가 없음), "AA 이상"=rank≤3으로 규칙 통일 |
| 별칭 | 4건 | SOL 리브랜딩 2021년 9월→**8월**, TLT 국내 대응상품 엔화노출 설명 오류, 파워 K200레버리지 실존 미확인, 마이티 예시상품 2023 상폐 |

---

## 5. 8/6 설명회·디스코드 질문 리스트 (우선순위순)

**상 — 평가 정답에 직결**

1. `NDY_*` 접두어의 공식 정의 — 익일 적용 민평값이 맞는지, 기본 컬럼(EVAL_PRICE 등)이 당일/익일 중 어느 쪽인지
2. `CRD_GRD`의 대표값 규칙(복수 평가등급 중 최저? 최신?) + `C0` 137건의 실체(진짜 C등급인지 무등급/평가중지 코드 오버로딩인지)
3. `PD_EVCO_CRD_GRD` 콤마 병기 순서와 평가사 매칭
4. 금액·가격·수량 단위 — `ISU_BAL_AMT`(원/천원?), `EVAL_PRICE`·`DIRTY`(액면 10,000원당?), `BUYABLE_QUANTITY`(좌/액면단위?)
5. `prfd_attr_cd` 228종 코드표 제공 여부(M/V/N 계열 의미, CHN/USA 혼재 이유)
6. 국내ETF `cu_base_index`("종합채권 3개월~1년" 등)의 산출기관 — KAP/KIS/제로인 BM 중 무엇인지
7. `du_lpr` 한글명 "시가"가 저가(low price)의 오기인지
8. `PD_RISK_GCD=0`(58건)·`PD_STD_INFO_UPDATE`의 공식 의미

**중 — 커버리지 확장용**

9. 해외ETF `pd_exg_mkt_cd` 숫자 코드 101/102 정의, AMX가 NYSE Arca 포괄 레거시 표기인지 + 원천 코드표(Lipper/코스콤) 제공 여부
10. `kofia_fd_ccd` 자리별 정의 — 금투협 시행세칙 별지 제15호 원문 기준 답변 가능한지
11. `PREF_TAX_YIELD` 적용 세율(9.5%? 1.4%?) / `AVG_ANNUAL_TAX_YIELD` 전부 0인 이유
12. `fd_set_pcd` 10/20 의미(추가형/단위형?), `fd_estb_ctry_cd='000'`이 국내 기본값인지 미입력인지
13. 데이터 원천 분류체계(코스콤/KIS자산평가/KOFIA/Lipper) — 채권종류(BD_KND) 코드표 원문 입수 가능 여부
14. 국내ETF 파일의 ETN 532건 — 평가 질의에서 "ETF"와 "ETN"을 구분해 출제하는지

**참고**: 로드맵 7장의 기존 질문(크레딧·RPM/TPM·임베딩 허용 범위 등)과 병합해서 사용.

---

## 6. 평가 대응 원칙 메모 (결측치 질의)

- 채점 기준은 제공 데이터(2026-07-11 스냅샷)다. 결측 필드 질의의 1차 답변은 항상 **"제공 데이터로 확인할 수 없음"** + 근거(어느 테이블·컬럼이 비어 있는지) 명시.
- Tier 1 사전(이 폴더)은 결측을 "메꾸는" 게 아니라 **결측을 정확히 판별하고**(센티널 인식) **있는 값을 정확히 해석하기 위한** 자산이다.
- Tier 2(외부 값 보강)는 8/6 예시 질의 확인 후 별도 테이블+출처 컬럼으로만. 제공 데이터 답변을 대체하지 않는다.
