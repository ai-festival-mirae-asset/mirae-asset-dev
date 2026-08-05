from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


EXTRACT_DATE = pd.Timestamp("2026-07-11")
OFFICIAL_ORIGIN = "official_snapshot"


@dataclass(frozen=True)
class DatasetSpec:
    code: str
    slug: str
    data_file: str
    schema_file: str
    date_columns: tuple[str, ...]


DATASETS = (
    DatasetSpec(
        "PRBD01N001",
        "bond",
        "PRBD01N001_국내채권마스터_20260711_datarows.xlsx",
        "PRBD01N001_국내채권마스터_schema.xlsx",
        ("ISU_DT", "MAT_DT", "PD_STD_INFO_UPDATE", "CRD_GRD_DT"),
    ),
    DatasetSpec(
        "PREF01N001",
        "domestic_etp",
        "PREF01N001_국내ETF마스터_20260711_datarows.xlsx",
        "PREF01N001_국내ETF마스터_schema.xlsx",
        ("cu_upt_dt", "du_upt_dt", "pd_lste_dt", "pd_lstg_dt", "wu_upt_dt"),
    ),
    DatasetSpec(
        "PREF02N001",
        "overseas_etf",
        "PREF02N001_해외ETF마스터_20260711_datarows.xlsx",
        "PREF02N001_해외ETF마스터_schema.xlsx",
        (
            "cu_upt_dt",
            "du_clpr_base_dt",
            "du_nav_base_dt",
            "du_upt_dt",
            "pd_lstg_dt",
            "wu_upt_dt",
        ),
    ),
    DatasetSpec(
        "PRFD01N001",
        "public_fund",
        "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx",
        "PRFD01N001_공모펀드마스터_schema.xlsx",
        (),
    ),
)

EXPECTED_ROWS = {
    "PRBD01N001": 42_394,
    "PREF01N001": 1_734,
    "PREF02N001": 5_646,
    "PRFD01N001": 95_619,
}

RISK_LABELS = {
    1: "매우 높은 위험",
    2: "높은 위험",
    3: "다소 높은 위험",
    4: "보통 위험",
    5: "낮은 위험",
    6: "매우 낮은 위험",
}

DATE_SENTINELS = {"0", "00000000", "10001231", "99991231"}

# 신용등급 서열. 원본 CRD_GRD는 무부호(플랫) 등급을 끝자리 0으로 표기하고
# (AA0·A0·BBB0), PD_EVCO_CRD_GRD는 같은 등급 집합을 부호 없이 표기한다(AA·A·BBB).
# 두 컬럼의 등급 집합이 정확히 대응하므로 플랫 표기는 외부 근거 없이 원본만으로 확정된다.
CREDIT_GRADE_RANK = {
    "AAA": 1,
    "AA+": 2,
    "AA": 3,
    "AA-": 4,
    "A+": 5,
    "A": 6,
    "A-": 7,
    "BBB+": 8,
    "BBB": 9,
    "BBB-": 10,
    "BB+": 11,
    "BB": 12,
    "BB-": 13,
    "B+": 14,
    "B": 15,
    "B-": 16,
    "CCC": 17,
    "CC": 18,
    "C": 19,
    "D": 20,
}

# Lipper 데이터베이스가 기초지수 부재를 나타내는 문자열. 값이 아니라 결측이다.
LIPPER_INDEX_SENTINELS = (
    "Index is not provided by Management Company",
    "Index is not available on Lipper Database",
)


def clean_text(value: Any) -> Any:
    if value is None or value is pd.NA:
        return pd.NA
    if isinstance(value, float) and pd.isna(value):
        return pd.NA
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text if text else pd.NA


def date_token(value: Any) -> Any:
    value = clean_text(value)
    if value is pd.NA:
        return pd.NA
    text = str(value)
    if " " in text:
        text = text.split(" ", 1)[0]
    return text.replace("-", "")


def normalize_credit_grade(value: Any) -> Any:
    """무부호 표기 `AA0`을 `AA`로 정규화한다. `AAA`·`CCC`처럼 0이 없는 값은 그대로 둔다."""
    value = clean_text(value)
    if value is pd.NA:
        return pd.NA
    text = str(value).upper()
    if re.fullmatch(r"[A-D]{1,3}0", text):
        return text[:-1]
    return text


def credit_grade_rank(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values.map(CREDIT_GRADE_RANK), errors="coerce").astype("Int64")


def normalize_observed_flag(
    frame: pd.DataFrame,
    column: str,
    positive: str,
    code: str,
    issues: IssueLog,
    *,
    corroborating: pd.Series | None = None,
    corroboration_note: str = "",
) -> pd.Series:
    """한쪽 값만 관측되는 플래그의 결측을 반대값으로 접을지 판정한다.

    "관측값이 Y뿐이니 null은 N이다"는 추론일 뿐이다. 이 함수는 독립 컬럼으로 교차 검증된
    경우에만 결측을 접고, 그렇지 않으면 결측을 unknown으로 유지한다. 근거 없이 접으면
    "값이 없다"가 "아니다"로 둔갑해 조용히 오답이 된다.
    """
    observed = set(frame[column].dropna().unique())
    positive_mask = frame[column].eq(positive)
    if observed != {positive}:
        issues.add(
            code,
            "flag_fill_rule_not_applicable",
            "warning",
            "keep_null_as_unknown",
            f"관측값이 {{{positive}}}가 아니라 {sorted(observed)}여서 결측을 반대값으로 접지 않았습니다.",
            column=column,
        )
        return positive_mask.where(frame[column].notna(), pd.NA)
    if corroborating is not None and not bool((positive_mask ^ corroborating).any()):
        return positive_mask
    issues.add(
        code,
        "flag_fill_needs_corroboration",
        "warning",
        "keep_null_as_unknown",
        f"관측값이 {positive}뿐이지만 독립 근거가 없어 결측을 반대값으로 접지 않았습니다. "
        + (corroboration_note or f"결측 {int(frame[column].isna().sum())}건은 unknown으로 유지합니다."),
        column=column,
    )
    return positive_mask.where(frame[column].notna(), pd.NA)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_schema(path: Path) -> pd.DataFrame:
    schema = pd.read_excel(path, sheet_name="Sheet1_Schema", skiprows=1, dtype=object)
    schema.columns = ["column", "pk_fk", "declared_type", "label", "example"]
    schema = schema[schema["column"].notna()].copy()
    schema["column"] = schema["column"].astype(str)
    schema["column_key"] = schema["column"].str.lower()
    return schema


def read_manual_overrides(path: Path) -> pd.DataFrame:
    overrides = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "dataset",
        "item_id",
        "target_field",
        "corrected_value",
        "value_type",
        "status",
        "source",
        "reason",
    }
    if set(overrides.columns) != required:
        raise AssertionError(f"Unexpected manual override columns: {list(overrides.columns)}")
    if overrides[["dataset", "item_id", "target_field"]].duplicated().any():
        raise AssertionError("Manual override target keys must be unique")
    return overrides


def apply_manual_overrides(
    frame: pd.DataFrame,
    code: str,
    item_column: str,
    overrides: pd.DataFrame,
    issues: "IssueLog",
) -> None:
    selected = overrides[(overrides["dataset"] == code) & (overrides["status"] == "approved")]
    for target in selected["target_field"].unique():
        if target not in frame.columns:
            frame[target] = pd.NA
    for override in selected.itertuples(index=False):
        mask = frame[item_column].eq(override.item_id)
        if int(mask.sum()) != 1:
            raise AssertionError(
                f"Manual override item must resolve once: {code}/{override.item_id}"
            )
        value: Any = override.corrected_value
        if override.value_type == "numeric":
            value = float(value)
        elif override.value_type == "date":
            value = pd.Timestamp(value).strftime("%Y-%m-%d")
        elif override.value_type != "text":
            raise AssertionError(f"Unsupported override type: {override.value_type}")
        frame.loc[mask, override.target_field] = value
        index = frame.index[mask][0]
        issues.add(
            code,
            "approved_manual_enrichment",
            "info",
            "populate_derived_field_only",
            f"{override.source}: {override.reason}",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=override.item_id,
            column=override.target_field,
            observed_value=value,
        )


def profile_columns(code: str, raw: pd.DataFrame, schema: pd.DataFrame) -> pd.DataFrame:
    declared = dict(zip(schema["column_key"], schema["declared_type"], strict=True))
    rows: list[dict[str, Any]] = []
    for column in raw.columns:
        values = raw[column]
        text = values.map(lambda value: "" if clean_text(value) is pd.NA else str(value))
        stripped = text.str.strip()
        blank = stripped.eq("")
        literal_null = stripped.str.upper().isin({"NULL", "N/A", "NA", "NONE"}) & ~blank
        padded = text.ne(stripped) & ~blank
        numeric = pd.to_numeric(stripped.where(~blank), errors="coerce")
        rows.append(
            {
                "dataset": code,
                "column": column,
                "declared_type": declared[column.lower()],
                "row_count": len(raw),
                "available_count": int((~blank).sum()),
                "coverage_pct": round(float((~blank).mean() * 100), 4),
                "blank_count": int(blank.sum()),
                "literal_null_count": int(literal_null.sum()),
                "padded_count": int(padded.sum()),
                "unique_nonblank_count": int(stripped[~blank].nunique()),
                "numeric_count": int(numeric.notna().sum()),
                "zero_count": int(numeric.eq(0).sum()),
                "negative_count": int(numeric.lt(0).sum()),
            }
        )
    return pd.DataFrame(rows)


class IssueLog:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(
        self,
        dataset: str,
        issue_code: str,
        severity: str,
        action: str,
        detail: str,
        *,
        source_row_number: int | None = None,
        item_id: Any = None,
        column: str | None = None,
        observed_value: Any = None,
    ) -> None:
        self.rows.append(
            {
                "dataset": dataset,
                "issue_code": issue_code,
                "severity": severity,
                "action": action,
                "source_row_number": source_row_number,
                "item_id": None if item_id is pd.NA else item_id,
                "column": column,
                "observed_value": None if observed_value is pd.NA else observed_value,
                "detail": detail,
            }
        )

    def frame(self) -> pd.DataFrame:
        columns = [
            "dataset",
            "issue_code",
            "severity",
            "action",
            "source_row_number",
            "item_id",
            "column",
            "observed_value",
            "detail",
        ]
        result = pd.DataFrame(self.rows, columns=columns)
        if not result.empty:
            result = result.sort_values(
                ["dataset", "source_row_number", "column", "issue_code"],
                na_position="first",
                kind="stable",
            )
        return result


def normalize_base(
    raw: pd.DataFrame, schema: pd.DataFrame, date_columns: Iterable[str]
) -> pd.DataFrame:
    result = raw.copy()
    result.columns = [column.lower() for column in result.columns]
    date_keys = {column.lower() for column in date_columns}
    declared = dict(zip(schema["column_key"], schema["declared_type"], strict=True))
    for column in result.columns:
        result[column] = result[column].map(clean_text)
        kind = str(declared[column]).lower()
        if column not in date_keys and kind in {"numeric", "double precision", "bigint"}:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def normalize_date_column(
    frame: pd.DataFrame,
    column: str,
    code: str,
    issues: IssueLog,
    *,
    item_column: str,
    ignored_sentinels: set[str] | None = None,
) -> None:
    ignored_sentinels = ignored_sentinels or set()
    tokens = frame[column].map(date_token)
    sentinel_mask = tokens.isin(DATE_SENTINELS)
    parse_values = tokens.mask(sentinel_mask)
    parsed = pd.to_datetime(parse_values, format="%Y%m%d", errors="coerce")
    invalid = parse_values.notna() & parsed.isna()
    report_mask = invalid | (sentinel_mask & ~tokens.isin(ignored_sentinels))
    for index in frame.index[report_mask]:
        issues.add(
            code,
            "invalid_or_sentinel_date",
            "warning",
            "set_null_in_clean_layer",
            "날짜로 확정할 수 없는 값이어서 정제 계층에서는 null로 처리했습니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, item_column],
            column=column,
            observed_value=tokens.at[index],
        )
    frame[column] = parsed.dt.strftime("%Y-%m-%d").where(parsed.notna(), pd.NA)


def add_lineage(frame: pd.DataFrame, spec: DatasetSpec) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "source_origin", OFFICIAL_ORIGIN)
    result.insert(1, "source_table", spec.code)
    result.insert(2, "source_file", spec.data_file)
    result.insert(3, "source_row_number", range(2, len(result) + 2))
    return result


def raw_record_json(row: pd.Series, source_columns: list[str]) -> str:
    record: dict[str, Any] = {}
    for column in source_columns:
        value = row[column]
        if value is pd.NA or (isinstance(value, float) and pd.isna(value)):
            record[column] = None
        elif isinstance(value, (pd.Timestamp, datetime, date)):
            record[column] = value.isoformat()
        else:
            record[column] = value
    return json.dumps(record, ensure_ascii=False, default=str, sort_keys=True)


def quarantine_rows(
    frame: pd.DataFrame,
    mask: pd.Series,
    spec: DatasetSpec,
    reason: str,
) -> pd.DataFrame:
    source_columns = [column for column in frame.columns if not column.startswith("source_")]
    rows: list[dict[str, Any]] = []
    for _, row in frame.loc[mask].iterrows():
        rows.append(
            {
                "source_origin": OFFICIAL_ORIGIN,
                "source_table": spec.code,
                "source_file": spec.data_file,
                "source_row_number": int(row["source_row_number"]),
                "reason": reason,
                "raw_record_json": raw_record_json(row, source_columns),
            }
        )
    return pd.DataFrame(rows)


def prepare_bond(
    frame: pd.DataFrame,
    spec: DatasetSpec,
    issues: IssueLog,
    overrides: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    maturity_tokens = frame["mat_dt"].map(date_token)
    frame["maturity_source_kind"] = "dated"
    frame.loc[maturity_tokens.isna() | maturity_tokens.eq("0"), "maturity_source_kind"] = "unknown"
    frame.loc[maturity_tokens.eq("99991231"), "maturity_source_kind"] = "open_ended_or_unknown"
    for column in (name.lower() for name in spec.date_columns):
        ignored = {"99991231"} if column == "mat_dt" else set()
        normalize_date_column(
            frame,
            column,
            spec.code,
            issues,
            item_column="pd_no",
            ignored_sentinels=ignored,
        )

    apply_manual_overrides(frame, spec.code, "pd_no", overrides, issues)
    frame["maturity_date_resolved"] = frame["mat_dt"].combine_first(
        frame.get("maturity_date_inferred", pd.Series(pd.NA, index=frame.index))
    )
    frame["maturity_date_source"] = "unavailable"
    frame.loc[frame["mat_dt"].notna(), "maturity_date_source"] = "official_mat_dt"
    frame.loc[
        frame["mat_dt"].isna() & frame["maturity_date_resolved"].notna(),
        "maturity_date_source",
    ] = "approved_product_name_parse"
    frame.loc[
        frame["mat_dt"].isna() & frame["maturity_date_resolved"].notna(),
        "maturity_source_kind",
    ] = "inferred_from_product_name"

    maturity = pd.to_datetime(frame["maturity_date_resolved"], errors="coerce")
    frame["maturity_status_at_snapshot"] = "unknown"
    frame.loc[
        maturity.lt(EXTRACT_DATE), "maturity_status_at_snapshot"
    ] = "matured_before_snapshot"
    frame.loc[
        maturity.eq(EXTRACT_DATE), "maturity_status_at_snapshot"
    ] = "matures_on_snapshot_date"
    frame.loc[
        maturity.gt(EXTRACT_DATE), "maturity_status_at_snapshot"
    ] = "active_after_snapshot"
    frame["remaining_days_at_snapshot"] = (
        (maturity - EXTRACT_DATE).dt.days.clip(lower=0).astype("Int64")
    )

    risk = pd.to_numeric(frame["pd_risk_gcd"], errors="coerce").astype("Int64")
    frame["risk_grade"] = risk.where(risk.between(1, 6))
    frame["risk_label_std"] = frame["risk_grade"].map(RISK_LABELS)
    frame["risk_available"] = frame["risk_grade"].notna()
    frame["buyable_quantity_observed"] = frame["buyable_quantity"].notna()
    frame["is_buyable_observed"] = frame["buyable_quantity"].gt(0).where(
        frame["buyable_quantity"].notna(), pd.NA
    )
    frame["source_remaining_days_as_of"] = frame["pd_std_info_update"]

    frame["credit_grade_norm"] = frame["crd_grd"].map(normalize_credit_grade)
    frame["credit_grade_rank"] = credit_grade_rank(frame["credit_grade_norm"])
    frame["credit_grade_available"] = frame["credit_grade_rank"].notna()
    unmapped = frame["credit_grade_norm"].notna() & frame["credit_grade_rank"].isna()
    for value in frame.loc[unmapped, "credit_grade_norm"].drop_duplicates():
        first = frame.index[unmapped & frame["credit_grade_norm"].eq(value)][0]
        issues.add(
            spec.code,
            "unmapped_credit_grade",
            "warning",
            "exclude_from_grade_comparison",
            "서열표에 없는 신용등급이어서 등급 비교에서 제외합니다.",
            source_row_number=int(frame.at[first, "source_row_number"]),
            item_id=frame.at[first, "pd_no"],
            column="crd_grd",
            observed_value=value,
        )
    flat_c = frame["crd_grd"].eq("C0")
    if bool(flat_c.any()):
        first = frame.index[flat_c][0]
        issues.add(
            spec.code,
            "ambiguous_grade_code",
            "warning",
            "treat_as_c_pending_confirmation",
            "원본에 `C`와 `C0`가 함께 존재해 `C0`가 무부호 C인지 별도 코드인지 확정되지 않았습니다. "
            f"잠정적으로 C로 정규화한 행이 {int(flat_c.sum())}건입니다.",
            source_row_number=int(frame.at[first, "source_row_number"]),
            item_id=frame.at[first, "pd_no"],
            column="crd_grd",
            observed_value="C0",
        )

    evaluator_grades = (
        frame["pd_evco_crd_grd"]
        .astype("string")
        .str.split(",")
        .map(
            lambda parts: [normalize_credit_grade(part) for part in parts]
            if isinstance(parts, list)
            else parts
        )
    )
    frame["evaluator_grade_count"] = evaluator_grades.map(
        lambda parts: len([p for p in parts if p is not pd.NA]) if isinstance(parts, list) else 0
    ).astype("Int64")
    frame["evaluator_grade_worst_rank"] = evaluator_grades.map(
        lambda parts: max(
            (CREDIT_GRADE_RANK[p] for p in parts if p in CREDIT_GRADE_RANK), default=None
        )
        if isinstance(parts, list)
        else None
    ).astype("Int64")
    rank_to_grade = {rank: grade for grade, rank in CREDIT_GRADE_RANK.items()}
    frame["evaluator_grade_worst"] = frame["evaluator_grade_worst_rank"].map(rank_to_grade)
    frame["evaluator_grade_split"] = evaluator_grades.map(
        lambda parts: len({p for p in parts if p in CREDIT_GRADE_RANK}) > 1
        if isinstance(parts, list)
        else False
    )

    frame["currency_available"] = frame["curr_cd"].notna() & ~frame["curr_cd"].eq("000")
    frame["country_code_semantics"] = "country_or_market_code"
    frame.loc[frame["pd_ctry_cd"].eq("XS"), "country_code_semantics"] = (
        "international_isin_prefix"
    )
    frame["security_registration_scope"] = "unknown"
    frame.loc[frame["pd_ctry_cd"].eq("KR"), "security_registration_scope"] = (
        "korean_isin"
    )
    frame.loc[frame["pd_ctry_cd"].eq("XS"), "security_registration_scope"] = (
        "international_isin"
    )
    frame["coupon_rate_resolved"] = frame["srfc_irt"].combine_first(
        pd.to_numeric(
            frame.get("coupon_rate_inferred", pd.Series(pd.NA, index=frame.index)),
            errors="coerce",
        )
    )
    frame["coupon_rate_source"] = "unavailable"
    frame.loc[frame["srfc_irt"].notna(), "coupon_rate_source"] = "official_srfc_irt"
    frame.loc[
        frame["srfc_irt"].isna() & frame["coupon_rate_resolved"].notna(),
        "coupon_rate_source",
    ] = "approved_product_name_parse"
    frame["issuer_name_resolved"] = frame["pd_pbcm"].combine_first(
        frame.get("issuer_name_inferred", pd.Series(pd.NA, index=frame.index))
    )
    frame["issuer_name_source"] = "unavailable"
    frame.loc[frame["pd_pbcm"].notna(), "issuer_name_source"] = "official_pd_pbcm"
    frame.loc[
        frame["pd_pbcm"].isna() & frame["issuer_name_resolved"].notna(),
        "issuer_name_source",
    ] = "approved_product_name_parse"

    incomplete = frame["pd_no"].isna()
    partial_currency = ~frame["currency_available"] & ~incomplete
    scope_exception = ~frame["pd_ctry_cd"].eq("KR") & ~incomplete
    frame["quality_status"] = "usable"
    frame.loc[incomplete, "quality_status"] = "incomplete_core"
    frame.loc[partial_currency, "quality_status"] = "partial_currency"
    frame.loc[scope_exception & ~partial_currency, "quality_status"] = "dataset_scope_exception"
    frame.loc[scope_exception & partial_currency, "quality_status"] = (
        "dataset_scope_exception_partial_currency"
    )
    frame["dataset_scope_consistent"] = ~scope_exception & ~incomplete
    frame["domestic_bond_search_eligible"] = frame["pd_ctry_cd"].eq("KR") & ~incomplete
    frame["international_bond_search_eligible"] = frame["pd_ctry_cd"].eq("XS") & ~incomplete
    frame["general_bond_search_eligible"] = ~incomplete
    frame["default_search_eligible"] = frame["domestic_bond_search_eligible"]
    for index in frame.index[scope_exception]:
        issues.add(
            spec.code,
            "dataset_scope_exception",
            "warning",
            "exclude_from_domestic_bond_queries",
            "국내채권 마스터에서 유일한 비-KR ISIN입니다. 일반 채권으로는 보존하되 국내발행채권 조건에서는 제외합니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "pd_no"],
            column="pd_ctry_cd",
            observed_value=frame.at[index, "pd_ctry_cd"],
        )

    if frame["maturity_date_resolved"].isna().all():
        raise AssertionError("Bond maturity normalization unexpectedly removed every value")
    return frame, pd.DataFrame()


def prepare_domestic_etp(
    frame: pd.DataFrame, spec: DatasetSpec, issues: IssueLog
) -> tuple[pd.DataFrame, pd.DataFrame]:
    invalid_core = (
        ~frame["pd_itm_no"].fillna("").astype(str).str.fullmatch(r"[A-Z0-9]{12}")
        | frame["pd_nm"].isin([pd.NA, "."])
        | frame["cu_fund_mgmt_co"].isin([pd.NA, "."])
    )
    quarantine = quarantine_rows(
        frame,
        invalid_core,
        spec,
        "핵심 식별자·상품명·운용사 값이 불완전하여 안전한 상품 레코드로 사용할 수 없음",
    )
    for index in frame.index[invalid_core]:
        issues.add(
            spec.code,
            "incomplete_core_record",
            "error",
            "quarantine",
            "공식 원본의 정정 행을 받기 전까지 검색·집계에서 제외합니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "pd_itm_no"],
        )
    frame = frame.loc[~invalid_core].copy()

    listing_end_tokens = frame["pd_lste_dt"].map(date_token)
    frame["listing_end_is_open"] = listing_end_tokens.eq("99991231")
    for column in (name.lower() for name in spec.date_columns):
        ignored = {"99991231"} if column == "pd_lste_dt" else set()
        normalize_date_column(
            frame,
            column,
            spec.code,
            issues,
            item_column="pd_itm_no",
            ignored_sentinels=ignored,
        )

    for column in ("cu_charge_etc_rt", "cu_charge_rt"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    risk_code = frame["pd_risk_cd"].astype("string").str.extract(r"_(1[1-6])$")[0]
    frame["risk_grade"] = pd.to_numeric(risk_code, errors="coerce").sub(10).astype("Int64")
    frame["risk_label_std"] = frame["risk_grade"].map(RISK_LABELS)
    frame["risk_available"] = frame["risk_grade"].notna()
    frame["instrument_type"] = frame["pd_grp_no"].str.lower()
    frame["expense_ratio"] = frame["cu_charge_rt"].where(frame["cu_charge_rt"].gt(0))
    frame["expense_ratio_available"] = frame["expense_ratio"].notna()
    frame["aum"] = frame["pd_net_tamt"].where(frame["pd_net_tamt"].gt(0))
    frame["aum_available"] = frame["aum"].notna()
    frame["is_sale_available"] = frame["pd_sale_yn"].eq("1").where(
        frame["pd_sale_yn"].notna(), pd.NA
    )
    frame["is_suspended"] = frame["pd_tr_yn"].eq("1").where(
        frame["pd_tr_yn"].notna(), pd.NA
    )

    # 연금 플래그: pd_pen_risk_nm의 위험/안전자산 건수와 pd_pen_tr_yn='Y' 건수가 일치하면
    # pd_pen_risk_nm='N'은 위험구분 결측이 아니라 연금거래 불가를 뜻한다.
    pension_classified = frame["pd_pen_risk_nm"].isin(["위험자산", "안전자산"])
    pension_tradable = frame["pd_pen_tr_yn"].eq("Y")
    if int(pension_classified.sum()) == int(pension_tradable.sum()) and not bool(
        (pension_classified ^ pension_tradable).any()
    ):
        frame["is_pension_tradable"] = pension_tradable
        frame["pension_asset_class"] = frame["pd_pen_risk_nm"].where(pension_classified)
    else:
        frame["is_pension_tradable"] = pd.NA
        frame["pension_asset_class"] = pd.NA
        issues.add(
            spec.code,
            "pension_flag_cross_check_failed",
            "warning",
            "keep_null_as_unknown",
            "pd_pen_risk_nm의 자산구분 건수와 pd_pen_tr_yn='Y' 건수가 더 이상 일치하지 않아 "
            "'N'=연금거래불가 해석을 적용하지 않았습니다.",
            column="pd_pen_risk_nm",
        )

    listing_end = pd.to_datetime(frame["pd_lste_dt"], errors="coerce")
    frame["listing_status"] = "unknown"
    frame.loc[frame["listing_end_is_open"], "listing_status"] = "active_open_ended"
    frame.loc[listing_end.lt(EXTRACT_DATE), "listing_status"] = "ended_before_extract"
    frame.loc[listing_end.eq(EXTRACT_DATE), "listing_status"] = "ends_on_extract_date"
    frame.loc[listing_end.gt(EXTRACT_DATE), "listing_status"] = "active_until_date"
    daily_update = pd.to_datetime(frame["du_upt_dt"], errors="coerce")
    frame["market_data_lag_days"] = (EXTRACT_DATE - daily_update).dt.days.astype("Int64")
    frame["market_data_within_30_days"] = frame["market_data_lag_days"].le(30).where(
        frame["market_data_lag_days"].notna(), pd.NA
    )
    frame["quality_status"] = "usable"
    active = frame["listing_status"].isin(["active_open_ended", "active_until_date"])
    suspension_unknown = frame["is_suspended"].isna()
    if bool(suspension_unknown.any()):
        issues.add(
            spec.code,
            "suspension_unknown",
            "warning",
            "exclude_from_default_search",
            "거래정지 여부를 알 수 없는 상품은 거래 가능으로 단정하지 않고 기본 검색에서 제외합니다. "
            f"해당 행 {int(suspension_unknown.sum())}건.",
            column="pd_tr_yn",
        )
    frame["default_search_eligible"] = active & ~frame["is_suspended"].fillna(True).astype(bool)
    return frame, quarantine


def prepare_overseas_etf(
    frame: pd.DataFrame, spec: DatasetSpec, issues: IssueLog
) -> tuple[pd.DataFrame, pd.DataFrame]:
    for column in (name.lower() for name in spec.date_columns):
        normalize_date_column(
            frame,
            column,
            spec.code,
            issues,
            item_column="pd_itm_no",
        )

    # Lipper 센티널은 결측을 문자열로 표현한 값이다. 남겨두면 IS NULL이 작동하지 않아
    # "확인할 수 없음"으로 답해야 할 기초지수 질의에 센티널 문장을 지수명처럼 답하게 된다.
    sentinel_index = frame["cu_base_index"].isin(LIPPER_INDEX_SENTINELS)
    for value in frame.loc[sentinel_index, "cu_base_index"].drop_duplicates():
        matched = sentinel_index & frame["cu_base_index"].eq(value)
        first = frame.index[matched][0]
        issues.add(
            spec.code,
            "sentinel_string_as_missing",
            "warning",
            "set_null_in_clean_layer",
            f"기초지수 부재를 나타내는 Lipper 센티널이어서 null로 변환했습니다. 해당 행 {int(matched.sum())}건.",
            source_row_number=int(frame.at[first, "source_row_number"]),
            item_id=frame.at[first, "pd_itm_no"],
            column="cu_base_index",
            observed_value=value,
        )
    frame["base_index_sentinel"] = sentinel_index
    frame.loc[sentinel_index, "cu_base_index"] = pd.NA
    frame["base_index_available"] = frame["cu_base_index"].notna()

    # cu_etn_yn만 독립 근거가 있다. Y 59건이 pd_grp_no='ETN' 59건과 행 단위로 완전히
    # 일치하므로 결측을 N으로 접어도 새로운 주장을 만들지 않는다.
    frame["is_etn_flagged"] = normalize_observed_flag(
        frame,
        "cu_etn_yn",
        "Y",
        spec.code,
        issues,
        corroborating=frame["pd_grp_no"].eq("ETN"),
    )
    frame["is_inverse_or_short"] = normalize_observed_flag(
        frame,
        "cu_inverse_short_yn",
        "Y",
        spec.code,
        issues,
        corroboration_note=(
            "상품명에 Inverse/Short/Bear 표기가 있는 306건 중 플래그가 Y인 행은 167건뿐이라, "
            "결측을 N으로 접으면 인버스로 보이는 139건을 '인버스 아님'으로 단정하게 됩니다."
        ),
    )
    frame["is_core_product"] = normalize_observed_flag(
        frame, "wu_core_yn", "Y", spec.code, issues
    )
    frame["is_index_tracking"] = normalize_observed_flag(
        frame,
        "cu_index_tracking_yn",
        "Y",
        spec.code,
        issues,
        corroboration_note="Y 2,360건에 결측 3,286건이라 결측이 '추종 안 함'인지 미수집인지 구분할 수 없습니다.",
    )

    impossible_diff = frame["du_diff_rt"].abs().gt(100)
    for index in frame.index[impossible_diff.fillna(False)]:
        issues.add(
            spec.code,
            "implausible_percentage",
            "error",
            "set_null_in_clean_layer",
            "괴리율 절대값이 100을 초과하여 비교·순위에서 제외합니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "pd_itm_no"],
            column="du_diff_rt",
            observed_value=frame.at[index, "du_diff_rt"],
        )
    frame.loc[impossible_diff.fillna(False), "du_diff_rt"] = pd.NA

    core_columns = [
        "pd_isin_cd",
        "cu_fund_mgmt_co",
        "wu_inv_ast_type",
        "wu_inv_rgn",
        "du_clpr",
    ]
    missing_core_count = frame[core_columns].isna().sum(axis=1)
    sparse = missing_core_count.ge(4)
    identifier_missing = frame["pd_isin_cd"].isna()
    taxonomy_conflict = frame["pd_curr_cd"].eq("INR") & frame["pd_trd_ccy"].eq("USD")

    for index in frame.index[sparse]:
        issues.add(
            spec.code,
            "sparse_core_record",
            "warning",
            "exclude_from_default_ranking",
            "티커 조회 외 조건 검색·비교에 필요한 핵심 필드가 부족합니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "pd_itm_no"],
        )
    for index in frame.index[identifier_missing & ~sparse]:
        issues.add(
            spec.code,
            "missing_isin",
            "warning",
            "retain_with_primary_item_id",
            "ISIN 대신 고유한 pd_itm_no를 기본 식별자로 사용합니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "pd_itm_no"],
            column="pd_isin_cd",
        )
    for index in frame.index[taxonomy_conflict]:
        issues.add(
            spec.code,
            "currency_or_product_taxonomy_conflict",
            "warning",
            "needs_review",
            "상품통화 INR과 거래통화 USD가 충돌하고 ETF 분류도 확인이 필요합니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "pd_itm_no"],
        )

    frame["risk_grade"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    frame["risk_label_std"] = pd.NA
    frame["risk_available"] = False
    frame["instrument_type"] = frame["pd_grp_no"].str.lower()
    frame["expense_ratio"] = frame["cu_charge_rt"]
    frame["expense_ratio_available"] = frame["cu_charge_rt"].notna()
    frame["aum"] = frame["du_last_aum"].where(frame["du_last_aum"].gt(0))
    frame["aum_available"] = frame["aum"].notna()
    frame["is_sale_available"] = frame["pd_sale_yn"].eq("1").where(
        frame["pd_sale_yn"].notna(), pd.NA
    )
    frame["is_suspended"] = frame["pd_tr_yn"].eq("1").where(
        frame["pd_tr_yn"].notna(), pd.NA
    )
    daily_update = pd.to_datetime(frame["du_upt_dt"], errors="coerce")
    frame["market_data_lag_days"] = (EXTRACT_DATE - daily_update).dt.days.astype("Int64")
    frame["market_data_within_30_days"] = frame["market_data_lag_days"].le(30).where(
        frame["market_data_lag_days"].notna(), pd.NA
    )
    frame["quality_status"] = "usable"
    frame.loc[identifier_missing, "quality_status"] = "incomplete_identifier"
    frame.loc[sparse, "quality_status"] = "incomplete_core"
    frame.loc[taxonomy_conflict, "quality_status"] = "needs_review"
    frame["default_search_eligible"] = ~(sparse | taxonomy_conflict)
    return frame, pd.DataFrame()


def prepare_public_fund(
    frame: pd.DataFrame, spec: DatasetSpec, issues: IssueLog
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    valid_item = frame["itm_no"].fillna("").astype(str).str.fullmatch(r"[A-Z0-9]{12}")
    valid_attr = frame["prfd_attr_cd"].fillna("").astype(str).str.fullmatch(r"[A-Z0-9]+")
    valid_domains = (
        frame["exchdg_yn"].isin(["Y", "N", pd.NA])
        & frame["thco_sale_yn"].isin(["Y", "N", pd.NA])
        & frame["zrin_fd_ivst_risk_gcd"].isin(["1", "2", "3", "4", "5", "6", "NULL", pd.NA])
    )
    invalid = ~(valid_item & valid_attr & valid_domains)
    quarantine = quarantine_rows(
        frame,
        invalid,
        spec,
        "열 밀림으로 여러 필드의 도메인이 동시에 깨져 원본 재추출 없이는 안전한 복구 불가",
    )
    for index in frame.index[invalid]:
        issues.add(
            spec.code,
            "shifted_or_corrupt_record",
            "error",
            "quarantine",
            "표준종목번호 K55301CE3408 계열로 보이나 itm_no와 속성코드를 확정할 수 없습니다.",
            source_row_number=int(frame.at[index, "source_row_number"]),
            item_id=frame.at[index, "itm_no"],
        )
    valid = frame.loc[~invalid].copy()
    valid["zrin_fd_ivst_risk_gcd"] = valid["zrin_fd_ivst_risk_gcd"].replace("NULL", pd.NA)
    valid["zrin_fd_ivst_risk_grd_nm"] = (
        valid["zrin_fd_ivst_risk_grd_nm"].astype("string").str.replace(" ", "", regex=False)
    )

    return_columns = (
        "fd_wk1_ern_r",
        "fd_mm1_ern_r",
        "fd_mm3_ern_r",
        "fd_mm6_ern_r",
        "fd_mm18_ern_r",
        "fd_yr1_ern_r",
        "fd_yr2_ern_r",
        "fd_yr3_ern_r",
        "fd_yr5_ern_r",
    )
    for column in return_columns:
        impossible = valid[column].lt(-100)
        for item_id in valid.loc[impossible, "itm_no"].drop_duplicates():
            first = valid.index[impossible & valid["itm_no"].eq(item_id)][0]
            issues.add(
                spec.code,
                "impossible_fund_return",
                "error",
                "set_null_in_clean_layer",
                "펀드 수익률은 -100%보다 작을 수 없어 단위·소수점 오류 후보로 격리했습니다.",
                source_row_number=int(valid.at[first, "source_row_number"]),
                item_id=item_id,
                column=column,
                observed_value=valid.at[first, column],
            )
        valid.loc[impossible, column] = pd.NA

    risk = pd.to_numeric(valid["zrin_fd_ivst_risk_gcd"], errors="coerce").astype("Int64")
    valid["risk_grade"] = risk.where(risk.between(1, 6))
    valid["risk_label_std"] = valid["risk_grade"].map(RISK_LABELS)
    valid["risk_available"] = valid["risk_grade"].notna()
    valid["is_sale_available"] = valid["sale_yn"].eq("판매중").where(
        valid["sale_yn"].notna(), pd.NA
    )
    valid["is_our_company_sale_available"] = valid["thco_sale_yn"].eq("Y").where(
        valid["thco_sale_yn"].notna(), pd.NA
    )
    valid["exchange_hedge_available"] = valid["exchdg_yn"].notna()
    valid["is_exchange_hedged"] = valid["exchdg_yn"].eq("Y").where(
        valid["exchdg_yn"].notna(), pd.NA
    )

    original_columns = [
        column
        for column in valid.columns
        if not column.startswith("source_")
        and column
        not in {
            "prfd_attr_cd",
            "risk_grade",
            "risk_label_std",
            "risk_available",
            "is_sale_available",
            "is_our_company_sale_available",
            "exchange_hedge_available",
            "is_exchange_hedged",
        }
    ]
    varying: list[str] = []
    for column in original_columns:
        if valid.groupby("itm_no", dropna=False)[column].nunique(dropna=False).gt(1).any():
            varying.append(column)
    if varying:
        raise AssertionError(f"Fund master columns vary within itm_no: {varying}")

    valid = valid.sort_values(["itm_no", "prfd_attr_cd", "source_row_number"], kind="stable")
    attribute = valid[
        [
            "source_origin",
            "source_table",
            "source_file",
            "source_row_number",
            "itm_no",
            "prfd_attr_cd",
        ]
    ].copy()
    master = valid.drop_duplicates("itm_no", keep="first").drop(columns=["prfd_attr_cd"])
    master["quality_status"] = "usable"
    master.loc[master["or_attr_desc"].eq("06"), "quality_status"] = "needs_taxonomy_mapping"
    master["default_search_eligible"] = True
    return master, attribute, quarantine


def field_policy(
    bond: pd.DataFrame,
    domestic: pd.DataFrame,
    overseas: pd.DataFrame,
    fund: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def add(
        dataset: str,
        column: str,
        status: str,
        eligible: int,
        available: int,
        action: str,
        reason: str,
    ) -> None:
        rows.append(
            {
                "dataset": dataset,
                "column": column,
                "status": status,
                "eligible_count": eligible,
                "available_count": available,
                "coverage_pct": round(available / eligible * 100, 4) if eligible else 0,
                "query_action": action,
                "reason": reason,
            }
        )

    for column in (
        "buy_yield",
        "corp_pretax_yield",
        "corp_after_tax_yield",
        "after_tax_yield",
        "pref_tax_yield",
        "depo_equiv_yield_154",
    ):
        add(
            "PRBD01N001",
            column,
            "partial",
            len(bond),
            int(bond[column].notna().sum()),
            "compare_observed_only",
            "장외 매수 관련 881개 행에만 값이 있습니다.",
        )
    add(
        "PRBD01N001",
        "avg_annual_tax_yield",
        "unavailable",
        len(bond),
        0,
        "reject_query",
        "비결측 881개가 전부 0이라 미수집 대체값으로 취급합니다.",
    )
    add(
        "PRBD01N001",
        "crd_grd",
        "partial",
        len(bond),
        int(bond["credit_grade_available"].sum()),
        "compare_by_rank",
        "무부호 표기(AA0 등)를 정규화한 뒤 credit_grade_rank로 비교합니다. 'AA 이상'은 rank<=3입니다. "
        "C0의 의미는 미확정이라 잠정적으로 C로 취급하고 품질 이력에 남겼습니다.",
    )
    add(
        "PRBD01N001",
        "pd_evco_crd_grd",
        "partial",
        len(bond),
        int(bond["evaluator_grade_worst_rank"].notna().sum()),
        "use_worst_grade",
        "평가사별 등급 콤마 병기를 분해했습니다. 평가사 간 불일치 시 보수적으로 최저등급을 씁니다. "
        "병기 순서와 평가사 매칭이 미확인이라 순서에 의미를 부여하지 않습니다.",
    )
    add(
        "PRBD01N001",
        "remaining_days",
        "stale_source_field",
        len(bond),
        int(bond["remaining_days_at_snapshot"].notna().sum()),
        "calculate_at_query_time",
        "원본 값은 행별 갱신일 기준입니다. 스냅샷 검증은 remaining_days_at_snapshot을 쓰고 실시간 답변은 요청 as_of_date로 다시 계산합니다.",
    )
    add(
        "PRBD01N001",
        "pd_ctry_cd",
        "scope_exception",
        len(bond),
        int(bond["dataset_scope_consistent"].sum()),
        "exclude_non_kr_from_domestic_queries",
        "42,394건 중 XS ISIN 1건은 국제발행·예탁 범위이므로 국내발행채권 검색에서 제외합니다.",
    )

    domestic_etf = domestic["instrument_type"].eq("etf") & domestic[
        "listing_status"
    ].isin(["active_open_ended", "active_until_date"])
    for column, status, available, action, reason in (
        (
            "cu_base_index",
            "partial",
            int((domestic_etf & domestic["cu_base_index"].notna()).sum()),
            "compare_observed_only",
            "ETF 중 일부에만 기초지수가 있습니다.",
        ),
        (
            "cu_charge_rt",
            "partial_needs_review",
            int((domestic_etf & domestic["expense_ratio_available"]).sum()),
            "positive_observed_only",
            "비결측 217개 중 150개가 0이라 양수 67개만 우선 사용합니다.",
        ),
        (
            "pd_net_tamt",
            "partial",
            int((domestic_etf & domestic["aum_available"]).sum()),
            "compare_observed_only",
            "du_last_aum보다 pd_net_tamt의 양수 커버리지가 높아 AUM 기준 필드로 채택합니다.",
        ),
    ):
        add("PREF01N001", column, status, int(domestic_etf.sum()), available, action, reason)
    for column in (
        "nru_mkt_diff_rt",
        "nru_mkt_inav",
        "pd_dvid_cycl",
        "pd_sect_nm",
        "ru_mkt_price",
        "ru_mkt_volume",
        "du_chas_errt",
        "du_diff_rt",
        "pd_dvid_yield",
    ):
        add(
            "PREF01N001",
            column,
            "unavailable",
            int(domestic_etf.sum()),
            0,
            "reject_query",
            "전체 결측이거나 비결측값이 모두 0입니다.",
        )

    add(
        "PREF02N001",
        "cu_charge_rt",
        "usable",
        len(overseas),
        int(overseas["cu_charge_rt"].notna().sum()),
        "use_observed",
        "공식 데이터에서 전 행에 값이 있습니다. 0 값은 원본 관측값으로 보존합니다.",
    )
    add(
        "PREF02N001",
        "cu_strtegy",
        "partial",
        len(overseas),
        int(overseas["cu_strtegy"].notna().sum()),
        "search_observed_only",
        "핵심 필드가 비어 있는 희소 레코드에는 전략 설명도 없습니다.",
    )
    add(
        "PREF02N001",
        "du_last_aum",
        "partial",
        len(overseas),
        int(overseas["aum_available"].sum()),
        "compare_observed_only",
        "양수 AUM 관측값만 집계합니다.",
    )
    add(
        "PREF02N001",
        "cu_base_index",
        "partial",
        len(overseas),
        int(overseas["base_index_available"].sum()),
        "compare_observed_only",
        "Lipper 센티널 2종을 null로 변환한 뒤의 실질 커버리지입니다. 센티널을 값으로 두면 "
        "기초지수 결측이 8건으로 보이지만 실제로는 2,705건이 더 결측입니다.",
    )
    for column, note in (
        (
            "cu_inverse_short_yn",
            "결측을 N으로 접을 독립 근거가 없어 unknown으로 유지합니다. 인버스 조건 검색은 "
            "관측된 Y만 대상으로 하고 결측 행의 부재를 단정하지 않습니다.",
        ),
        (
            "cu_index_tracking_yn",
            "Y 2,360건에 결측 3,286건이라 결측의 의미를 확정할 수 없습니다.",
        ),
        ("wu_core_yn", "관측값이 N뿐이라 Y의 부재를 확인할 수 없습니다."),
    ):
        add(
            "PREF02N001",
            column,
            "partial_needs_review",
            len(overseas),
            int(overseas[column].notna().sum()),
            "observed_only_no_negation",
            note,
        )
    for column, status in (
        ("du_er_1d", "unavailable"),
        ("du_diff_rt", "unavailable"),
        ("pd_lst_price", "unavailable"),
        ("risk_grade", "unsupported"),
    ):
        add(
            "PREF02N001",
            column,
            status,
            len(overseas),
            0,
            "reject_query",
            "컬럼이 없거나 전체 0·극저커버리지라 신뢰할 수 없습니다.",
        )

    for column in (
        "fd_nast_suma",
        "fd_wk1_ern_r",
        "fd_mm1_ern_r",
        "fd_mm3_ern_r",
        "fd_mm6_ern_r",
        "fd_mm18_ern_r",
        "fd_yr1_ern_r",
        "fd_yr2_ern_r",
        "fd_yr3_ern_r",
        "fd_yr5_ern_r",
        "risk_grade",
        "exchdg_yn",
    ):
        add(
            "PRFD01N001",
            column,
            "partial",
            len(fund),
            int(fund[column].notna().sum()),
            "compare_observed_only",
            "fund_master의 논리 상품 수를 분모로 coverage를 계산합니다.",
        )
    add(
        "PRFD01N001",
        "expense_ratio",
        "unsupported",
        len(fund),
        0,
        "reject_query",
        "공모펀드 원본에는 보수 필드가 없습니다.",
    )
    return pd.DataFrame(rows).sort_values(["dataset", "column"], kind="stable")


def taxonomy_seed(
    domestic: pd.DataFrame, overseas: pd.DataFrame, fund: pd.DataFrame
) -> pd.DataFrame:
    sources = (
        ("PREF01N001", domestic, "wu_inv_ast_type", "asset_class"),
        ("PREF01N001", domestic, "wu_inv_rgn", "region"),
        ("PREF02N001", overseas, "wu_inv_ast_type", "asset_class"),
        ("PREF02N001", overseas, "wu_inv_rgn", "region"),
        ("PRFD01N001", fund, "or_attr_desc", "asset_class"),
        ("PRFD01N001", fund, "fd_ivst_rgn_desc", "region"),
    )
    rows: list[dict[str, Any]] = []
    for code, frame, column, dimension in sources:
        counts = frame[column].dropna().astype(str).value_counts().sort_index()
        for value, count in counts.items():
            rows.append(
                {
                    "dataset": code,
                    "source_column": column,
                    "source_value": value,
                    "row_count": int(count),
                    "standard_dimension": dimension,
                    "standard_code": "",
                    "standard_label": "",
                    "mapping_status": "needs_review",
                    "note": "원본 의미와 목표 분류 수준을 사람이 확인한 뒤 승인",
                }
            )
    return pd.DataFrame(rows)


def build_reconciliation(
    inputs: dict[str, Path],
    raw_frames: dict[str, pd.DataFrame],
    clean_frames: dict[str, pd.DataFrame],
    fund_attribute: pd.DataFrame,
    quarantine: pd.DataFrame,
    manual_overrides: pd.DataFrame,
) -> dict[str, Any]:
    fund_return_columns = [
        "fd_wk1_ern_r",
        "fd_mm1_ern_r",
        "fd_mm3_ern_r",
        "fd_mm6_ern_r",
        "fd_mm18_ern_r",
        "fd_yr1_ern_r",
        "fd_yr2_ern_r",
        "fd_yr3_ern_r",
        "fd_yr5_ern_r",
    ]
    fund_returns = clean_frames["PRFD01N001"][fund_return_columns]
    domestic = clean_frames["PREF01N001"]
    overseas = clean_frames["PREF02N001"]
    assertions = {
        "source_row_counts_match_manifest": all(
            len(raw_frames[code]) == expected for code, expected in EXPECTED_ROWS.items()
        ),
        "bond_primary_key_unique": not clean_frames["PRBD01N001"]["pd_no"].duplicated().any(),
        "domestic_primary_key_unique": not clean_frames["PREF01N001"]["pd_itm_no"].duplicated().any(),
        "overseas_primary_key_unique": not clean_frames["PREF02N001"]["pd_itm_no"].duplicated().any(),
        "fund_attribute_key_unique": not fund_attribute[["itm_no", "prfd_attr_cd"]]
        .duplicated()
        .any(),
        "fund_master_primary_key_unique": not clean_frames["PRFD01N001"]["itm_no"]
        .duplicated()
        .any(),
        "quarantine_count_is_two": len(quarantine) == 2,
        "fund_reconciliation": len(fund_attribute) + int(
            quarantine["source_table"].eq("PRFD01N001").sum()
        )
        == EXPECTED_ROWS["PRFD01N001"],
        "domestic_reconciliation": len(clean_frames["PREF01N001"]) + int(
            quarantine["source_table"].eq("PREF01N001").sum()
        )
        == EXPECTED_ROWS["PREF01N001"],
        "fund_returns_respect_lower_bound": not fund_returns.lt(-100).any().any(),
        "ended_domestic_products_excluded_by_default": not domestic.loc[
            domestic["listing_status"].eq("ended_before_extract"), "default_search_eligible"
        ].any(),
        "overseas_implausible_diff_removed": not overseas["du_diff_rt"].abs().gt(100).any(),
        "overseas_index_sentinels_removed": not overseas["cu_base_index"]
        .isin(LIPPER_INDEX_SENTINELS)
        .any(),
        "overseas_index_sentinel_count_matches_flag": int(
            overseas["base_index_sentinel"].sum()
        )
        == int(
            raw_frames["PREF02N001"]["cu_base_index"]
            .map(clean_text)
            .isin(LIPPER_INDEX_SENTINELS)
            .sum()
        ),
        "bond_flat_credit_grades_normalized": not clean_frames["PRBD01N001"][
            "credit_grade_norm"
        ]
        .dropna()
        .astype(str)
        .str.fullmatch(r"[A-D]{1,3}0")
        .any(),
        "bond_credit_grade_rank_covers_observed": not (
            clean_frames["PRBD01N001"]["credit_grade_norm"].notna()
            & clean_frames["PRBD01N001"]["credit_grade_rank"].isna()
        ).any(),
        "bond_aa_or_better_includes_flat_aa": int(
            (clean_frames["PRBD01N001"]["credit_grade_rank"] <= 3).sum()
        )
        == int(
            clean_frames["PRBD01N001"]["crd_grd"].isin(["AAA", "AA+", "AA", "AA0"]).sum()
        ),
        "bond_evaluator_grade_count_matches_raw": int(
            clean_frames["PRBD01N001"]["evaluator_grade_count"].sum()
        )
        == int(
            raw_frames["PRBD01N001"]["PD_EVCO_CRD_GRD"]
            .map(clean_text)
            .dropna()
            .astype(str)
            .str.count(",")
            .add(1)
            .sum()
        ),
        "domestic_pension_flag_cross_check": int(
            domestic["pension_asset_class"].notna().sum()
        )
        == int(domestic["is_pension_tradable"].fillna(False).astype(bool).sum()),
        "derived_booleans_preserve_missing": all(
            int(clean_frames[code][derived].isna().sum())
            == int(clean_frames[code][source].isna().sum())
            for code, source, derived in (
                ("PREF01N001", "pd_tr_yn", "is_suspended"),
                ("PREF01N001", "pd_sale_yn", "is_sale_available"),
                ("PREF02N001", "cu_inverse_short_yn", "is_inverse_or_short"),
                ("PRFD01N001", "sale_yn", "is_sale_available"),
            )
        ),
        "bond_scope_exceptions_excluded_from_domestic_default": not clean_frames[
            "PRBD01N001"
        ].loc[
            ~clean_frames["PRBD01N001"]["dataset_scope_consistent"],
            "default_search_eligible",
        ].any(),
        "approved_bond_enrichment_applied": (
            clean_frames["PRBD01N001"]
            .loc[
                clean_frames["PRBD01N001"]["pd_no"].eq("XS3067881758"),
                [
                    "maturity_date_resolved",
                    "maturity_date_source",
                    "coupon_rate_resolved",
                    "issuer_name_resolved",
                    "country_code_semantics",
                    "security_registration_scope",
                    "quality_status",
                    "default_search_eligible",
                    "international_bond_search_eligible",
                    "general_bond_search_eligible",
                ],
            ]
            .astype(object)
            .values.tolist()
            == [
                [
                    "2055-05-14",
                    "approved_product_name_parse",
                    0.0,
                    "Bank of America Corporation",
                    "international_isin_prefix",
                    "international_isin",
                    "dataset_scope_exception_partial_currency",
                    False,
                    True,
                    True,
                ]
            ]
        ),
    }
    result = {
        "extract_date": EXTRACT_DATE.strftime("%Y-%m-%d"),
        "source_files": {
            code: {"path": str(path), "sha256": sha256(path)} for code, path in inputs.items()
        },
        "raw_row_counts": {code: len(frame) for code, frame in raw_frames.items()},
        "clean_row_counts": {code: len(frame) for code, frame in clean_frames.items()},
        "fund_attribute_row_count": len(fund_attribute),
        "quarantine_row_count": len(quarantine),
        "quarantine_by_dataset": quarantine["source_table"].value_counts().to_dict(),
        "derived_metrics": {
            "bond_matured_before_extract": int(
                clean_frames["PRBD01N001"]["maturity_status_at_snapshot"]
                .eq("matured_before_snapshot")
                .sum()
            ),
            "bond_matures_on_extract_date": int(
                clean_frames["PRBD01N001"]["maturity_status_at_snapshot"]
                .eq("matures_on_snapshot_date")
                .sum()
            ),
            "bond_unknown_maturity": int(
                clean_frames["PRBD01N001"]["maturity_status_at_snapshot"].eq("unknown").sum()
            ),
            "bond_active_after_extract": int(
                clean_frames["PRBD01N001"]["maturity_status_at_snapshot"]
                .eq("active_after_snapshot")
                .sum()
            ),
            "bond_source_maturity_unknown": int(
                clean_frames["PRBD01N001"]["mat_dt"].isna().sum()
            ),
            "bond_dataset_scope_exception_count": int(
                (~clean_frames["PRBD01N001"]["dataset_scope_consistent"]).sum()
            ),
            "approved_manual_enrichment_count": int(
                manual_overrides["status"].eq("approved").sum()
            ),
            "domestic_etf_count": int(
                clean_frames["PREF01N001"]["instrument_type"].eq("etf").sum()
            ),
            "domestic_etn_count": int(
                clean_frames["PREF01N001"]["instrument_type"].eq("etn").sum()
            ),
            "domestic_ended_before_extract_count": int(
                domestic["listing_status"].eq("ended_before_extract").sum()
            ),
            "domestic_default_search_eligible_count": int(
                domestic["default_search_eligible"].sum()
            ),
            "overseas_blank_isin_count": int(
                clean_frames["PREF02N001"]["pd_isin_cd"].isna().sum()
            ),
            "overseas_market_data_over_30_days_count": int(
                overseas["market_data_lag_days"].gt(30).sum()
            ),
            "overseas_default_search_eligible_count": int(
                overseas["default_search_eligible"].sum()
            ),
            "overseas_base_index_sentinel_count": int(
                overseas["base_index_sentinel"].sum()
            ),
            "overseas_base_index_available_count": int(
                overseas["base_index_available"].sum()
            ),
            "bond_credit_grade_available_count": int(
                clean_frames["PRBD01N001"]["credit_grade_available"].sum()
            ),
            "bond_credit_grade_aa_or_better_count": int(
                (clean_frames["PRBD01N001"]["credit_grade_rank"] <= 3).sum()
            ),
            "bond_evaluator_grade_split_count": int(
                clean_frames["PRBD01N001"]["evaluator_grade_split"].sum()
            ),
            "domestic_pension_tradable_count": int(
                domestic["is_pension_tradable"].fillna(False).astype(bool).sum()
            ),
            "fund_master_count": len(clean_frames["PRFD01N001"]),
            "fund_risk_available_count": int(
                clean_frames["PRFD01N001"]["risk_available"].sum()
            ),
        },
        "assertions": assertions,
        "all_assertions_passed": all(assertions.values()),
    }
    if not result["all_assertions_passed"]:
        failed = [name for name, passed in assertions.items() if not passed]
        raise AssertionError(f"Reconciliation assertions failed: {failed}")
    return result


def write_csv(frame: pd.DataFrame, path: Path, *, compressed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    compression: Any = {"method": "gzip", "mtime": 0} if compressed else None
    frame.to_csv(path, index=False, encoding="utf-8", compression=compression)


def run(
    input_dir: Path,
    output_dir: Path,
    manual_overrides_path: Path = Path("config/manual_overrides.csv"),
) -> dict[str, Any]:
    issues = IssueLog()
    overrides = read_manual_overrides(manual_overrides_path)
    raw_frames: dict[str, pd.DataFrame] = {}
    clean_frames: dict[str, pd.DataFrame] = {}
    profiles: list[pd.DataFrame] = []
    quarantines: list[pd.DataFrame] = []
    input_paths: dict[str, Path] = {}
    fund_attribute = pd.DataFrame()
    input_paths["manual_overrides"] = manual_overrides_path

    for spec in DATASETS:
        data_path = input_dir / spec.data_file
        schema_path = input_dir / spec.schema_file
        input_paths[spec.code] = data_path
        schema = read_schema(schema_path)
        raw = pd.read_excel(data_path, dtype=object, keep_default_na=False)
        if {column.lower() for column in raw.columns} != set(schema["column_key"]):
            raise AssertionError(f"Schema columns do not match data columns for {spec.code}")
        raw_frames[spec.code] = raw
        profiles.append(profile_columns(spec.code, raw, schema))
        normalized = add_lineage(normalize_base(raw, schema, spec.date_columns), spec)

        if spec.slug == "bond":
            clean, quarantine = prepare_bond(normalized, spec, issues, overrides)
        elif spec.slug == "domestic_etp":
            clean, quarantine = prepare_domestic_etp(normalized, spec, issues)
        elif spec.slug == "overseas_etf":
            clean, quarantine = prepare_overseas_etf(normalized, spec, issues)
        elif spec.slug == "public_fund":
            clean, fund_attribute, quarantine = prepare_public_fund(normalized, spec, issues)
        else:
            raise AssertionError(f"Unknown dataset slug: {spec.slug}")
        clean_frames[spec.code] = clean
        if not quarantine.empty:
            quarantines.append(quarantine)

    quarantine = pd.concat(quarantines, ignore_index=True)
    policies = field_policy(
        clean_frames["PRBD01N001"],
        clean_frames["PREF01N001"],
        clean_frames["PREF02N001"],
        clean_frames["PRFD01N001"],
    )
    taxonomy = taxonomy_seed(
        clean_frames["PREF01N001"],
        clean_frames["PREF02N001"],
        clean_frames["PRFD01N001"],
    )
    reconciliation = build_reconciliation(
        input_paths,
        raw_frames,
        clean_frames,
        fund_attribute,
        quarantine,
        overrides,
    )

    clean_dir = output_dir / "clean"
    quality_dir = output_dir / "quality"
    write_csv(clean_frames["PRBD01N001"], clean_dir / "bond.csv.gz", compressed=True)
    write_csv(clean_frames["PREF01N001"], clean_dir / "domestic_etp.csv.gz", compressed=True)
    write_csv(clean_frames["PREF02N001"], clean_dir / "overseas_etf.csv.gz", compressed=True)
    write_csv(clean_frames["PRFD01N001"], clean_dir / "fund_master.csv.gz", compressed=True)
    write_csv(fund_attribute, clean_dir / "fund_attribute.csv.gz", compressed=True)
    write_csv(quarantine, quality_dir / "quarantine_records.csv.gz", compressed=True)
    write_csv(pd.concat(profiles, ignore_index=True), quality_dir / "column_profile.csv")
    write_csv(issues.frame(), quality_dir / "data_quality_issues.csv")
    write_csv(policies, quality_dir / "field_policy.csv")
    write_csv(taxonomy, quality_dir / "taxonomy_mapping_seed.csv")
    quality_dir.mkdir(parents=True, exist_ok=True)
    (quality_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return reconciliation


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile and normalize the four official financial product snapshots."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("datasets"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/data"))
    parser.add_argument(
        "--manual-overrides",
        type=Path,
        default=Path("config/manual_overrides.csv"),
        help="Approved derived-field enrichments; raw source columns are never overwritten.",
    )
    args = parser.parse_args()
    result = run(args.input_dir, args.output_dir, args.manual_overrides)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
