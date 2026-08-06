"""질의 시점 답변 가능성 판정.

`field_policy.csv`는 "이 컬럼을 조회에 쓸 수 있는가"를 데이터 계층에서 판정한 결과다.
이 모듈은 그 정책과 실제 대상 행을 결합해 "이 질문에 답할 수 있는가"를 판정한다.

핵심은 세 가지 상태를 섞지 않는 것이다.

- `unsupported_field`  : 컬럼이 없거나, 있어도 전량 결측·전량 0이라 조회가 금지된 경우
- `no_matching_rows`   : 데이터는 있는데 조건에 맞는 상품이 없는 경우
- `partial_coverage`   : 조건에 맞는 상품 중 일부만 요청한 값을 가진 경우

이 셋을 구분하지 않으면 "기초지수가 KOSPI200인 ETF"처럼 데이터에 필드가 없는 질문에
"그런 상품이 없습니다"라고 답하게 된다. 상품은 실재하는데 데이터가 없는 것이므로 오답이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ANSWERABLE = "answerable"
PARTIAL_COVERAGE = "partial_coverage"
UNSUPPORTED_FIELD = "unsupported_field"
NO_MATCHING_ROWS = "no_matching_rows"

# field_policy.status 중 조회를 허용하지 않는 상태
BLOCKING_STATUSES = {"unavailable", "unsupported"}
BLOCKING_ACTIONS = {"reject_query"}

# 값이 있어도 해석에 단서를 붙여야 하는 조회 동작
CAUTION_ACTIONS = {
    "positive_observed_only": "관측된 양수 값만 비교했습니다. 0은 미수집 대체값일 수 있어 제외했습니다.",
    "observed_only_no_negation": "값이 관측된 상품만 대상으로 했습니다. 값이 없는 상품이 해당하지 않는다는 뜻은 아닙니다.",
    "compare_observed_only": "값이 관측된 상품만 비교했습니다.",
    "calculate_at_query_time": "저장된 값 대신 요청 기준일로 다시 계산한 값입니다.",
    "use_worst_grade": "평가사 간 등급이 갈리는 경우 보수적으로 최저등급을 사용했습니다.",
    "compare_by_rank": "등급 문자열이 아니라 표준화된 서열로 비교했습니다.",
    "exclude_non_kr_from_domestic_queries": "국내발행채권 조건에서는 국제등록채권을 제외했습니다.",
}


# field_policy는 원본 컬럼명으로 기록되고 질의는 파생 컬럼명을 쓴다. 이 대응이 없으면
# 정책이 조용히 무시되어 차단 대상 컬럼도 통과하고 주의 문구도 붙지 않는다.
COLUMN_POLICY_ALIASES: dict[str, dict[str, str]] = {
    "PRBD01N001": {
        "credit_grade_norm": "crd_grd",
        "credit_grade_rank": "crd_grd",
        "evaluator_grade_worst": "pd_evco_crd_grd",
        "evaluator_grade_worst_rank": "pd_evco_crd_grd",
        "remaining_days_at_snapshot": "remaining_days",
    },
    "PREF01N001": {
        "expense_ratio": "cu_charge_rt",
        "aum": "pd_net_tamt",
        "base_index": "cu_base_index",
    },
    "PREF02N001": {
        "expense_ratio": "cu_charge_rt",
        "aum": "du_last_aum",
        "base_index": "cu_base_index",
        "is_inverse_or_short": "cu_inverse_short_yn",
        "is_index_tracking": "cu_index_tracking_yn",
        "is_core_product": "wu_core_yn",
    },
    "PRFD01N001": {},
}


def policy_column(dataset: str, column: str) -> str:
    return COLUMN_POLICY_ALIASES.get(dataset, {}).get(column, column)


@dataclass(frozen=True)
class Answerability:
    status: str
    reason: str | None
    eligible_count: int | None
    available_count: int | None
    ranked_count: int | None
    excluded_count: int | None
    coverage_by_column: dict[str, float] = field(default_factory=dict)
    blocked_columns: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "eligible_count": self.eligible_count,
            "available_count": self.available_count,
            "ranked_count": self.ranked_count,
            "excluded_count": self.excluded_count,
            "coverage_by_column": self.coverage_by_column,
            "blocked_columns": self.blocked_columns,
            "warnings": self.warnings,
        }


def load_field_policy(path: Path) -> pd.DataFrame:
    policy = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"dataset", "column", "status", "query_action", "reason"}
    if not required.issubset(policy.columns):
        raise AssertionError(f"field_policy is missing columns: {sorted(required - set(policy.columns))}")
    return policy


def policy_for(policy: pd.DataFrame, dataset: str, column: str) -> dict[str, str] | None:
    match = policy[policy["dataset"].eq(dataset) & policy["column"].eq(column)]
    if match.empty:
        return None
    return match.iloc[0].to_dict()


def available_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    """해당 컬럼이 그 행에서 실제로 관측됐는지. `<column>_available` 플래그를 우선한다."""
    flag = f"{column}_available"
    if flag in frame.columns:
        return frame[flag].fillna(False).astype(bool)
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].notna()


def comparable_rows(frame: pd.DataFrame, columns: Iterable[str]) -> pd.Series:
    """요청한 모든 컬럼이 동시에 관측된 행. 컬럼별 최솟값이 아니라 교집합이다.

    컬럼마다 결측 행이 다르면 최솟값은 실제로 비교 가능한 행 수보다 크게 나온다.
    순위를 매기기 전에 이 교집합으로 행을 걸러야 답변 문구와 실제 계산이 일치한다.
    """
    mask = pd.Series(True, index=frame.index)
    for column in columns:
        mask &= available_mask(frame, column)
    return mask


def assess(
    frame: pd.DataFrame,
    dataset: str,
    requested_columns: Iterable[str],
    policy: pd.DataFrame,
    *,
    eligible_mask: pd.Series | None = None,
    condition_note: str | None = None,
    min_coverage: float | None = None,
) -> Answerability:
    """조건이 적용된 대상 집합과 요청 컬럼으로 답변 가능성을 판정한다.

    `frame`은 이미 사용자 조건이 적용된 대상 집합이어야 한다. `eligible_mask`를 주면
    기본 검색 자격(거래종료·정지 제외 등)을 추가로 적용한다.
    """
    requested = list(requested_columns)
    eligible = frame if eligible_mask is None else frame.loc[eligible_mask.reindex(frame.index, fill_value=False)]

    blocked: dict[str, str] = {}
    warnings: list[str] = []
    for column in requested:
        entry = policy_for(policy, dataset, policy_column(dataset, column))
        if column not in frame.columns and entry is None:
            blocked[column] = f"'{dataset}' 데이터에 '{column}' 컬럼이 없습니다."
            continue
        if entry is None:
            continue
        if entry["status"] in BLOCKING_STATUSES or entry["query_action"] in BLOCKING_ACTIONS:
            blocked[column] = entry["reason"] or f"'{column}'은 조회 가능한 상태가 아닙니다."
            continue
        caution = CAUTION_ACTIONS.get(entry["query_action"])
        if caution and caution not in warnings:
            warnings.append(caution)

    if blocked:
        return Answerability(
            status=UNSUPPORTED_FIELD,
            reason="요청한 값이 제공 데이터에 없거나 신뢰할 수 없습니다. 상품이 없다는 뜻이 아닙니다.",
            eligible_count=int(len(eligible)),
            available_count=0,
            ranked_count=0,
            excluded_count=int(len(eligible)),
            blocked_columns=blocked,
            warnings=warnings,
        )

    eligible_count = int(len(eligible))
    if eligible_count == 0:
        return Answerability(
            status=NO_MATCHING_ROWS,
            reason=condition_note
            or "조건에 맞는 상품이 없습니다. 데이터 부재가 아니라 조건 불일치입니다.",
            eligible_count=0,
            available_count=0,
            ranked_count=0,
            excluded_count=0,
            warnings=warnings,
        )

    coverage = {
        column: round(float(available_mask(eligible, column).mean()), 6) for column in requested
    }
    comparable = comparable_rows(eligible, requested)
    available_count = int(comparable.sum())
    excluded_count = eligible_count - available_count

    if not requested or available_count == eligible_count:
        return Answerability(
            status=ANSWERABLE,
            reason=None,
            eligible_count=eligible_count,
            available_count=available_count,
            ranked_count=available_count,
            excluded_count=0,
            coverage_by_column=coverage,
            warnings=warnings,
        )

    if available_count == 0:
        return Answerability(
            status=UNSUPPORTED_FIELD,
            reason=(
                f"조건에 맞는 상품 {eligible_count}건이 있지만 요청한 값을 가진 상품이 하나도 없습니다. "
                "상품이 없는 것이 아니라 데이터가 없는 것입니다."
            ),
            eligible_count=eligible_count,
            available_count=0,
            ranked_count=0,
            excluded_count=eligible_count,
            coverage_by_column=coverage,
            warnings=warnings,
        )

    ratio = available_count / eligible_count
    if min_coverage is not None and ratio < min_coverage:
        return Answerability(
            status=UNSUPPORTED_FIELD,
            reason=(
                f"조건에 맞는 상품 {eligible_count}건 중 요청한 값을 가진 상품이 {available_count}건"
                f"({ratio:.2%})뿐이라 모집단을 대표하는 답변을 만들 수 없습니다. "
                "상품이 없는 것이 아니라 데이터 커버리지가 부족한 것입니다."
            ),
            eligible_count=eligible_count,
            available_count=available_count,
            ranked_count=0,
            excluded_count=excluded_count,
            coverage_by_column=coverage,
            warnings=warnings,
        )

    return Answerability(
        status=PARTIAL_COVERAGE,
        reason=(
            f"조건에 맞는 상품 {eligible_count}건 중 {available_count}건만 요청한 값을 모두 가지고 있어 "
            f"{excluded_count}건은 비교에서 제외했습니다."
        ),
        eligible_count=eligible_count,
        available_count=available_count,
        ranked_count=available_count,
        excluded_count=excluded_count,
        coverage_by_column=coverage,
        warnings=warnings,
    )


def rank(
    frame: pd.DataFrame,
    order_by: list[tuple[str, str]],
    *,
    required_columns: Iterable[str] | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """비교에 필요한 값이 모두 있는 행만 남기고 정렬한다.

    결측 행을 남긴 채 정렬하면 답변 문구("값이 있는 상품만 비교했습니다")와 실제 결과가
    어긋난다. 순위 산출 전에 교집합으로 거르는 것이 판정과 계산을 일치시키는 유일한 방법이다.
    """
    required = list(required_columns) if required_columns is not None else [c for c, _ in order_by]
    comparable = frame.loc[comparable_rows(frame, required)]
    if order_by:
        comparable = comparable.sort_values(
            by=[column for column, _ in order_by],
            ascending=[direction == "asc" for _, direction in order_by],
            kind="stable",
        )
    if limit is not None:
        comparable = comparable.head(limit)
    return comparable.reset_index(drop=True)
