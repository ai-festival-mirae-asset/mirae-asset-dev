"""KRX 종목정보로 국내 ETP를 보강한다 (Tier 2).

원칙은 하나다. **공식 스냅샷 값은 절대 덮어쓰지 않는다.** 외부 값은 별도 컬럼과 출처를
달고 따로 저장하며, 공식 값이 관측되지 않은 자리에만 들어간다. 공식 값과 외부 값이
다르면 값을 고르지 않고 `*_conflict` 플래그로 남긴다. 평가 기준이 주최 측 데이터이므로
외부 값으로 답을 바꾸는 순간 규칙 위반이 된다.

이 보강이 특히 중요한 이유는 국내 ETF 총보수의 0이 진짜 무보수가 아니라는 근거를 주기
때문이다. 공식 값과 KRX 값이 모두 있는 215건 중 201건이 다르고, 그중 148건은 공식 값이
0인데 KRX는 양수다. 0을 실제 보수로 집계하면 안 된다는 기존 판단이 외부 근거로 뒷받침된다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.prepare_data import clean_text, write_csv

EXTERNAL_SOURCE = "external_krx_20260806"
EXTERNAL_AS_OF = "2026-08-06"

KRX_FILES = (
    ("KRX_ETF_종목정보_20260806.xlsx", "etf"),
    ("KRX_ETN_종목정보_20260806.xlsx", "etn"),
)

# KRX 컬럼 -> 우리 파생 컬럼
KRX_COLUMN_MAP = {
    "총보수": "external_expense_ratio",
    "기초지수명": "external_base_index",
    "지수산출기관": "external_index_provider",
    "기초자산분류": "external_asset_class",
    "기초시장분류": "external_market_scope",
    "추적배수": "external_leverage_kind",
    "최종거래일": "external_final_trade_date",
}

REFERENCE_COLUMNS = [
    "item_id",
    "instrument_type_external",
    "external_expense_ratio",
    "external_base_index",
    "external_index_provider",
    "external_asset_class",
    "external_market_scope",
    "external_leverage_kind",
    "external_final_trade_date",
    "external_source",
    "external_as_of",
]


def _date(value: Any) -> Any:
    value = clean_text(value)
    if value is pd.NA:
        return pd.NA
    parsed = pd.to_datetime(str(value).replace("/", "-"), errors="coerce")
    return parsed.strftime("%Y-%m-%d") if pd.notna(parsed) else pd.NA


def load_krx(external_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for filename, instrument in KRX_FILES:
        raw = pd.read_excel(external_dir / filename, dtype=object)
        frame = pd.DataFrame({"item_id": raw["표준코드"].map(clean_text)})
        frame["instrument_type_external"] = instrument
        for source_column, target in KRX_COLUMN_MAP.items():
            if source_column not in raw.columns:
                frame[target] = pd.NA
            elif target == "external_final_trade_date":
                frame[target] = raw[source_column].map(_date)
            elif target == "external_expense_ratio":
                frame[target] = pd.to_numeric(raw[source_column], errors="coerce")
            else:
                frame[target] = raw[source_column].map(clean_text)
        frames.append(frame)
    krx = pd.concat(frames, ignore_index=True)
    krx["external_source"] = EXTERNAL_SOURCE
    krx["external_as_of"] = EXTERNAL_AS_OF
    if krx["item_id"].duplicated().any():
        raise AssertionError("KRX reference must have one row per 표준코드")
    return krx[REFERENCE_COLUMNS]


def build(domestic: pd.DataFrame, krx: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    merged = domestic[["pd_itm_no", "instrument_type", "cu_charge_rt", "expense_ratio", "cu_base_index"]].merge(
        krx, left_on="pd_itm_no", right_on="item_id", how="left"
    )

    official_fee = pd.to_numeric(merged["cu_charge_rt"], errors="coerce")
    # 공식 값의 0은 우리 정책상 미수집 대체값이라 관측값으로 보지 않는다.
    official_fee_observed = pd.to_numeric(merged["expense_ratio"], errors="coerce")
    external_fee = pd.to_numeric(merged["external_expense_ratio"], errors="coerce")

    both_fee = official_fee.notna() & external_fee.notna()
    merged["expense_ratio_conflict"] = both_fee & official_fee.round(4).ne(external_fee.round(4))
    merged["expense_ratio_zero_refuted"] = both_fee & official_fee.eq(0) & external_fee.gt(0)
    merged["expense_ratio_resolved"] = official_fee_observed.combine_first(external_fee)
    merged["expense_ratio_resolved_source"] = pd.NA
    merged.loc[official_fee_observed.notna(), "expense_ratio_resolved_source"] = "official_snapshot"
    merged.loc[
        official_fee_observed.isna() & external_fee.notna(), "expense_ratio_resolved_source"
    ] = EXTERNAL_SOURCE

    official_index = merged["cu_base_index"]
    external_index = merged["external_base_index"]
    both_index = official_index.notna() & external_index.notna()
    merged["base_index_conflict"] = both_index & official_index.astype("string").str.strip().ne(
        external_index.astype("string").str.strip()
    )
    merged["base_index_resolved"] = official_index.combine_first(external_index)
    merged["base_index_resolved_source"] = pd.NA
    merged.loc[official_index.notna(), "base_index_resolved_source"] = "official_snapshot"
    merged.loc[
        official_index.isna() & external_index.notna(), "base_index_resolved_source"
    ] = EXTERNAL_SOURCE

    merged["maturity_date_external"] = merged["external_final_trade_date"]
    merged["maturity_source"] = merged["maturity_date_external"].notna().map(
        {True: EXTERNAL_SOURCE, False: pd.NA}
    )

    summary = {
        "external_source": EXTERNAL_SOURCE,
        "external_as_of": EXTERNAL_AS_OF,
        "clean_rows": int(len(domestic)),
        "krx_rows": int(len(krx)),
        "matched_rows": int(merged["item_id"].notna().sum()),
        "matched_by_instrument_type": merged.loc[merged["item_id"].notna()]
        .groupby("instrument_type")
        .size()
        .to_dict(),
        "expense_ratio": {
            "official_observed": int(official_fee_observed.notna().sum()),
            "external_available": int(external_fee.notna().sum()),
            "filled_from_external": int(
                (official_fee_observed.isna() & external_fee.notna()).sum()
            ),
            "resolved_total": int(merged["expense_ratio_resolved"].notna().sum()),
            "conflict": int(merged["expense_ratio_conflict"].sum()),
            "zero_refuted_by_external": int(merged["expense_ratio_zero_refuted"].sum()),
            "coverage_pct_before": round(float(official_fee_observed.notna().mean() * 100), 4),
            "coverage_pct_after": round(
                float(merged["expense_ratio_resolved"].notna().mean() * 100), 4
            ),
        },
        "base_index": {
            "official": int(official_index.notna().sum()),
            "external_available": int(external_index.notna().sum()),
            "filled_from_external": int((official_index.isna() & external_index.notna()).sum()),
            "conflict": int(merged["base_index_conflict"].sum()),
            "coverage_pct_before": round(float(official_index.notna().mean() * 100), 4),
            "coverage_pct_after": round(
                float(merged["base_index_resolved"].notna().mean() * 100), 4
            ),
        },
        "maturity_date": {
            "external_available": int(merged["maturity_date_external"].notna().sum()),
            "active_etn_covered": int(
                merged.loc[
                    merged["instrument_type"].eq("etn"), "maturity_date_external"
                ].notna().sum()
            ),
        },
    }

    # 공식 관측값이 외부 값으로 바뀌지 않았는지 확인한다. 이 assertion이 Tier 2의 전부다.
    overwritten = official_fee_observed.notna() & merged["expense_ratio_resolved"].ne(
        official_fee_observed
    )
    if bool(overwritten.any()):
        raise AssertionError(
            f"Official expense_ratio was overwritten in {int(overwritten.sum())} rows"
        )
    index_overwritten = official_index.notna() & merged["base_index_resolved"].ne(official_index)
    if bool(index_overwritten.any()):
        raise AssertionError(
            f"Official cu_base_index was overwritten in {int(index_overwritten.sum())} rows"
        )
    summary["assertions"] = {
        "official_expense_ratio_never_overwritten": True,
        "official_base_index_never_overwritten": True,
        "external_rows_are_unique": not krx["item_id"].duplicated().any(),
    }
    summary["all_assertions_passed"] = all(summary["assertions"].values())

    reference = merged[
        [
            "pd_itm_no",
            "instrument_type_external",
            "external_expense_ratio",
            "external_base_index",
            "external_index_provider",
            "external_asset_class",
            "external_market_scope",
            "external_leverage_kind",
            "external_final_trade_date",
            "expense_ratio_conflict",
            "expense_ratio_zero_refuted",
            "expense_ratio_resolved",
            "expense_ratio_resolved_source",
            "base_index_conflict",
            "base_index_resolved",
            "base_index_resolved_source",
            "maturity_date_external",
            "maturity_source",
            "external_source",
            "external_as_of",
        ]
    ].rename(columns={"pd_itm_no": "item_id"})
    reference = reference.loc[reference["external_source"].notna() | reference["item_id"].notna()]
    return reference, summary


def run(clean_dir: Path, external_dir: Path, output_dir: Path) -> dict[str, Any]:
    domestic = pd.read_csv(
        clean_dir / "domestic_etp.csv.gz", keep_default_na=False, low_memory=False
    ).replace("", pd.NA)
    krx = load_krx(external_dir)
    reference, summary = build(domestic, krx)
    write_csv(reference, output_dir / "reference" / "external_domestic_etp.csv.gz", compressed=True)
    (output_dir / "quality" / "external_enrichment_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich domestic ETP with KRX reference data.")
    parser.add_argument("--clean-dir", type=Path, default=Path("artifacts/data/clean"))
    parser.add_argument("--external-dir", type=Path, default=Path("external_data"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/data"))
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.clean_dir, args.external_dir, args.output_dir),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
