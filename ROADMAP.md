# 로드맵 (jhnam)

> 마감 2026-09-06 · 평가 API 상시운영 2026-09-07~09-30
> 상세 설계는 [PROJECT_GUIDE.md](./PROJECT_GUIDE.md), 외부데이터 보강 과정은 [EXTERNAL_DATA_PLAN.md](./EXTERNAL_DATA_PLAN.md) 참고.

## ✅ 한 것

- **데이터 정리**: 원본 4종(국내채권/국내ETF/해외ETF/공모펀드) 정규화 — `scripts/normalize_data.py`
- **외부데이터로 빈값 채우기**: KRX·KOFIA로 총보수·기초지수·ETF/ETN구분 보강, 제공값과 상충 시 항상 제공값 유지 + 플래그만 — `EXTERNAL_DATA_PLAN.md`
- **표기 통일(통합 뷰)**: 4종 상품을 공통 컬럼으로 합침 — `scripts/build_unified_view.py`
- **조건검색·정렬 검색엔진**: 필터/정렬 실행 함수 — `scripts/query_engine.py`
- **답변 가능/불가 판정** (4종): `answerable`/`partial_coverage`/`unsupported_field`/`no_matching_rows`
- **API 뼈대**: `/answer` 엔드포인트 — `app/main.py` (질문 패턴 1개만 인식)

## ❌ 안 한 것 (우선순위 순)

1. **HyperCLOVA X 연동** — 필수 요구사항, 아직 미착수
2. **질문 이해 범위 확장** — 지금은 딱 1가지 질문 패턴만 알아들음
3. **DB(PostgreSQL) 이전** — 지금은 CSV로만 돌아가는 중
4. **서버 배포** — 평가 기간 상시운영 환경 없음
5. **README·기술제안서** — 제출 필수 서류
6. **테스트 코드**

## 급하지 않은 것

- 답변가능성 8개 상태로 확장 (현재 4개)
- 해외ETF 운용전략 의미검색(벡터)
- 국내채권 신용등급 결측 45% 보강 (외부 소스 접근성 불확실)
- taxonomy 애매한 값 11개 정리
