# 금융상품 Agent — 로드맵 (jhnam)

> 제출 마감 2026-09-06 / 평가 API 상시운영 2026-09-07~09-30 기준.
> 실행 방법은 README.md(작성 예정), 데이터 설계 배경은 PROJECT_GUIDE.md 참고.

## 완료

**데이터**
- [x] 4종 원본 독립 검증 (키 유일성, 죽은 컬럼 11개 확인, 공모펀드 비정상행 1건 확정)
- [x] 정규화 파이프라인 (`scripts/normalize_data.py`) — 죽은 컬럼 제외, 위험등급 표준화, 공모펀드 master/attribute 분리, quarantine
- [x] 지역·자산군 taxonomy 매핑 (`scripts/build_taxonomy_mapping.py`, 104값: 92 mapped / 11 ambiguous / 1 unmapped)
- [x] `product_unified` 상품군 교차 뷰 (`scripts/build_unified_view.py`, 60,912행)

**질의 · API**
- [x] 조건 필터·정렬 질의 엔진 (`scripts/query_engine.py`)
- [x] 답변가능성 판정 4종 (`answerable`/`partial_coverage`/`unsupported_field`/`no_matching_rows`)
- [x] 채권 만기상품 기본 제외 규칙
- [x] `/answer` API 뼈대 (`app/main.py`, FastAPI, 과제 JSON 스펙 준수) — 질문 패턴 1개만 인식

## 미완료

**Agent (LLM)**
- [ ] HyperCLOVA X 연동 — **필수 요구사항, 미착수**
- [ ] 질문 해석(intent parsing) 확장 — 현재 1개 패턴만 인식
- [ ] 답변 문장 생성 LLM화 — 현재 템플릿 문자열

**데이터 · 로직**
- [ ] 답변가능성 8개 상태로 확장 (현재 4개, `ambiguous_condition`/`insufficient_condition`/`stale_or_inactive`/`invalid_data` 미구현)
- [ ] 대표 질의 세트 (20개+) 및 기대 결과 정의
- [ ] 해외ETF 운용전략 의미검색 (벡터)
- [ ] 국내ETF ETF/ETN 구분 — 신뢰할 컬럼 못 찾음 (미해결 open item)

**인프라**
- [ ] DB 이전 (CSV → PostgreSQL 또는 DuckDB), read-only 권한, 인덱스
- [ ] 서버 배포 및 평가기간 상시운영
- [ ] Dockerfile / 재현 가능한 실행 환경

**문서 · 제출**
- [ ] README (실행 방법)
- [ ] 기술 제안서 — **제출 필수**
- [ ] 테스트 코드 (`tests/`)
- [ ] 평가용 API 명세서 정리
