"""정제된 CSV 위에서 조건 필터·정렬을 실행하는 질의 함수.

LLM은 이 함수를 호출할 구조화된 조건(filters/sort/limit)만 만들고,
실제 필터링·정렬·숫자 비교는 이 함수가 정확하게 계산한다.
"""

from pathlib import Path
from typing import Any

import pandas as pd

DATA_CLEAN_DIR = Path(__file__).resolve().parent.parent / "data_clean"

TABLES = {
    "bond": "bond_clean.csv",
    "domestic_etf": "domestic_etf_clean.csv",
    "overseas_etf": "overseas_etf_clean.csv",
    "fund_master": "fund_master.csv",
    "product_unified": "product_unified.csv",  # 상품군 교차 질의용 (scripts/build_unified_view.py로 생성)
}


def load_table(table: str) -> pd.DataFrame:
    return pd.read_csv(DATA_CLEAN_DIR / TABLES[table])


# 검증된 사실: 해당 컬럼의 0은 진짜 0일 수도, 미수집 대체값일 수도 있어 확정할 수 없음.
# (예: 해외ETF cu_charge_rt=0.00이 363건인데 액티브 펀드까지 섞여 있어 의심스러움)
ZERO_UNVERIFIED_COLUMNS = {
    ("overseas_etf", "cu_charge_rt"): "총보수 0.00%는 실제 무보수 상품일 수도, 미수집 데이터가 0으로 채워진 것일 수도 있어 확인되지 않았습니다.",
}


def _apply_filters(df: pd.DataFrame, table: str, filters: dict[str, Any]) -> pd.DataFrame:
    for col, cond in filters.items():
        if col not in df.columns:
            raise KeyError(f"'{table}' 테이블에 컬럼 '{col}'이 없습니다.")
        if isinstance(cond, tuple):
            op, value = cond
            if op == "eq":
                df = df[df[col] == value]
            elif op == "gte":
                df = df[df[col] >= value]
            elif op == "lte":
                df = df[df[col] <= value]
            elif op == "gt":
                df = df[df[col] > value]
            elif op == "lt":
                df = df[df[col] < value]
            else:
                raise ValueError(f"알 수 없는 연산자: {op}")
        else:
            df = df[df[col] == cond]
    return df


def _eligible_set(table: str, filters: dict[str, Any] | None) -> tuple[pd.DataFrame, list[str]]:
    """필터만 적용한(정렬/limit 전) 대상 집합. 만기채권은 명시적으로 요청하지 않으면 기본 제외."""
    df = load_table(table)
    filters = dict(filters or {})
    warnings: list[str] = []

    if table == "bond" and "is_matured" not in filters and "is_matured" in df.columns:
        df = df[df["is_matured"] == False]  # noqa: E712
        warnings.append("만기 도래한 채권은 기본적으로 제외했습니다. 만기 상품도 포함하려면 명시적으로 요청하세요.")

    df = _apply_filters(df, table, filters)
    return df, warnings


def query(
    table: str,
    filters: dict[str, Any] | None = None,
    sort: list[tuple[str, str]] | None = None,
    limit: int | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """조건에 맞는 행을 필터링·정렬해서 반환한다.

    filters: {"컬럼명": 값} (등호 매칭) 또는 {"컬럼명": (연산자, 값)} 형태.
             연산자는 "eq", "gte", "lte", "gt", "lt" 지원.
    sort: [(컬럼명, "asc"|"desc"), ...]
    limit: 상위 N개만 반환

    반환값: (결과 DataFrame, 경고 메시지 목록)
    """
    df, warnings = _eligible_set(table, filters)

    if sort:
        by = [c for c, _ in sort]
        ascending = [d == "asc" for _, d in sort]
        df = df.sort_values(by=by, ascending=ascending)

    if limit is not None:
        df = df.head(limit)

    df = df.reset_index(drop=True)

    for (t, col), message in ZERO_UNVERIFIED_COLUMNS.items():
        if t == table and col in df.columns and (df[col] == 0).any():
            warnings.append(message)

    return df, warnings


def assess_answerability(
    table: str,
    filters: dict[str, Any] | None = None,
    requested_columns: list[str] | None = None,
) -> dict[str, Any]:
    """조회 전/후로 이 질의에 답할 수 있는 상태인지 판정한다. (PROJECT_GUIDE.md §8 축소판)

    status: answerable / partial_coverage / unsupported_field / no_matching_rows
    """
    all_cols = load_table(table).columns
    requested_columns = requested_columns or []

    missing = [c for c in requested_columns if c not in all_cols]
    if missing:
        return {
            "status": "unsupported_field",
            "reason": f"'{table}' 테이블에 없는 컬럼: {missing}",
            "eligible_count": None,
            "available_count": None,
        }

    eligible, _ = _eligible_set(table, filters)
    eligible_count = len(eligible)

    if eligible_count == 0:
        return {
            "status": "no_matching_rows",
            "reason": "조건에 맞는 상품이 없습니다 (데이터 부재가 아니라 조건 불일치일 수 있음).",
            "eligible_count": 0,
            "available_count": 0,
        }

    coverage_by_column: dict[str, float] = {}
    min_available_count = eligible_count
    for col in requested_columns:
        avail_col = f"{col}_available" if f"{col}_available" in eligible.columns else None
        if avail_col:
            available_count = int(eligible[avail_col].sum())
        else:
            available_count = int(eligible[col].notna().sum())
        coverage_by_column[col] = available_count / eligible_count
        min_available_count = min(min_available_count, available_count)

    if requested_columns and min_available_count < eligible_count:
        return {
            "status": "partial_coverage",
            "reason": "조건에 맞는 상품 중 일부만 요청한 값을 보유하고 있습니다.",
            "eligible_count": eligible_count,
            "available_count": min_available_count,
            "coverage_by_column": coverage_by_column,
        }

    return {
        "status": "answerable",
        "reason": None,
        "eligible_count": eligible_count,
        "available_count": eligible_count,
    }


if __name__ == "__main__":
    # 과제 소개 예시 질의 검증:
    # "미국 증시에 상장된 주식형 ETF 중에서 총보수가 낮고 운용 규모가 큰 상품 3개만 비교"
    # pd_mkt_id가 해외ETF 테이블 전체에서 'US'로 고정이라 상장시장 조건은 자동 충족됨(검증됨).
    result, warns = query(
        table="overseas_etf",
        filters={"wu_inv_ast_type": "Equity"},
        sort=[("cu_charge_rt", "asc"), ("du_last_aum", "desc")],
        limit=3,
    )
    cols = ["pd_itm_no", "pd_nm", "wu_inv_ast_type", "cu_charge_rt", "du_last_aum", "as_of_date"]
    print(result[cols].to_string(index=False))
    for w in warns:
        print(f"[경고] {w}")
