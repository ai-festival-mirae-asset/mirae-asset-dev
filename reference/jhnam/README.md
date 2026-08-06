# jhnam 브랜치 참조 사본

> 출처: `jhnam` 브랜치 커밋 `737972d` (2026-08-05)
> 반입일: 2026-08-05 · 반입 목적: 스펙 참조

이 디렉터리의 파일은 **실행 대상이 아니다.** jhnam 브랜치는 `data_clean/` 디렉터리와 자체
정규화 스크립트를 전제로 동작하므로 이 저장소의 `pipeline/`·`artifacts/` 구조에서 그대로
돌지 않는다. 설계와 계약을 확인하기 위한 읽기용 사본이다.

## 이미 우리 쪽에 반영한 것

| 참조 파일 | 반영 위치 | 반영 내용 |
|---|---|---|
| `query_engine.py` | [`pipeline/answerability.py`](../../pipeline/answerability.py) | `answerable`/`partial_coverage`/`unsupported_field`/`no_matching_rows` 4상태 모델 |
| `build_unified_view.py` | [`pipeline/build_unified_view.py`](../../pipeline/build_unified_view.py) | 상품군 교차 통합 뷰, 어댑터 함수 구조, 지표별 출처 컬럼 |
| `main.py` | — | `think_trace` 도구 이벤트 형식, LLM 교체 지점(`parse_question`/`render_answer`) 분리 구조 |

## 반영하면서 교정한 것

1. **`available_count`를 교집합으로 계산한다.** `query_engine.assess_answerability()`는 컬럼별
   유효 건수의 최솟값을 쓴다. 컬럼마다 결측 행이 다르면 실제 비교 가능한 행보다 크게 나온다.
2. **전량 0 컬럼을 차단한다.** 원본은 컬럼 존재 여부만 확인해 `du_chas_errt`처럼 비결측값이
   전부 0인 컬럼도 통과시킨다. 우리는 `field_policy`의 `reject_query`로 판정한다.
3. **ETF와 ETN을 분리한다.** `build_unified_view.py`는 국내·해외 전체를 각각
   `DOMESTIC_ETF`/`OVERSEAS_ETF`로 고정한다. 실제로는 국내에 ETN 532건, 해외에 59건이 섞여 있다.
4. **기준일을 지표별로 싣는다.** 원본은 모든 값을 추출일 2026-07-11로 표기한다. 실제 총보수
   기준일은 2026-06-14~15, AUM 기준일은 2026-06-14~16이다.
5. **taxonomy 매핑 상태를 버리지 않는다.** 원본은 `region_mapping_status`를 통합 뷰에서
   제거해 `ambiguous` 매핑이 확정 매핑처럼 보인다.
6. **국내 AUM은 `pd_net_tamt`을 쓴다.** 원본은 `du_last_aum`을 쓰는데, 이 컬럼은 ETN 409건이
   전부 0이라 양수 커버리지가 1,042건에 그친다. `pd_net_tamt`는 1,551건이고 값도 사실상 같다.

## 아직 반영하지 않은 것

- `enrich_fund_fees.py` + `../../external_data/펀드별 보수비용비교_20260805.xls` — 공모펀드
  보수의 KOFIA 외부 보강(Tier 2). 우리 정책상 08-06 예시 질의 확인 후 진입 여부를 재판단한다.
  현재 스크립트는 자기 산출물을 다시 입력으로 읽어 재실행 시 실패한다.
- `check_keys.py` — `astype(str)`를 먼저 호출해 null이 문자열 `"nan"`이 되므로 이후 `isna()`가
  항상 false다. 우리 reconciliation assertion이 같은 검사를 다르게 수행한다.
- `build_taxonomy_mapping.py`의 산출물은 [`config/taxonomy_mapping_proposal.csv`](../../config/taxonomy_mapping_proposal.csv)로
  가져왔으나 승인 전까지 파이프라인에 연결하지 않는다.
