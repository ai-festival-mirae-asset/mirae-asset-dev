"""네 정제 테이블을 상품군 교차 질의용 공통 계층으로 합친다.

세 가지 원칙으로 만든다.

1. 상품군에 원래 없는 개념은 비슷한 값으로 대체하지 않고 결측으로 남긴다. 채권에 총보수가
   없다고 0을 넣으면 "총보수가 가장 낮은 상품"이 전부 채권이 된다.
2. 기준일은 지표별로 따로 싣는다. 추출일 2026-07-11 하나로 뭉뚱그리면 6월 중순 값을
   7월 값이라고 답하게 된다.
3. 값의 출처와 매핑 상태를 버리지 않는다. 승인되지 않은 taxonomy 매핑을 확정 매핑처럼
   내려보내면 downstream이 그것을 사실로 취급한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.prepare_data import EXTRACT_DATE, write_csv

UNIFIED_COLUMNS = [
    "item_id",
    "product_type",
    "instrument_type",
    "name",
    "asset_class_raw",
    "asset_class_std",
    "asset_class_mapping_status",
    "region_raw",
    "region_std",
    "region_mapping_status",
    "risk_grade",
    "risk_available",
    "expense_ratio",
    "expense_ratio_available",
    "expense_ratio_source",
    "expense_ratio_as_of",
    "aum",
    "aum_available",
    "aum_source",
    "aum_as_of",
    "return_1y",
    "return_1y_available",
    "return_1y_source",
    "return_1y_as_of",
    "extract_date",
    "quality_status",
    "default_search_eligible",
    "source_origin",
    "source_table",
    "source_row_key",
    "source_row_number",
]

OFFICIAL = "official_snapshot"


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, low_memory=False).replace("", pd.NA)


def _flag(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return frame[column].astype("string").eq("True").fillna(False)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce").astype("Float64")


def _base(frame: pd.DataFrame, product_type: str, source_table: str, key_column: str) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out["item_id"] = frame[key_column]
    out["product_type"] = product_type
    out["source_table"] = source_table
    out["source_origin"] = frame.get("source_origin", OFFICIAL)
    out["source_row_number"] = frame.get("source_row_number")
    out["source_row_key"] = f"{key_column}=" + frame[key_column].astype(str)
    out["extract_date"] = EXTRACT_DATE.strftime("%Y-%m-%d")
    out["risk_grade"] = pd.to_numeric(frame.get("risk_grade"), errors="coerce").astype("Int64")
    out["risk_available"] = _flag(frame, "risk_available")
    out["quality_status"] = frame.get("quality_status")
    out["default_search_eligible"] = _flag(frame, "default_search_eligible")
    return out


def _taxonomy(out: pd.DataFrame, frame: pd.DataFrame, asset_column: str | None, region_column: str | None) -> None:
    """원본 분류값을 보존하고 매핑 상태를 명시한다.

    승인된 taxonomy 매핑이 아직 없으므로 표준 코드는 비우고 `unmapped`로 남긴다. 빈 값을
    조용히 내려보내면 downstream이 '분류 없음'으로 오해하므로 상태를 함께 싣는다.
    """
    out["asset_class_raw"] = frame[asset_column] if asset_column else pd.NA
    out["region_raw"] = frame[region_column] if region_column else pd.NA
    out["asset_class_std"] = pd.NA
    out["region_std"] = pd.NA
    out["asset_class_mapping_status"] = "unmapped" if asset_column else "not_applicable"
    out["region_mapping_status"] = "unmapped" if region_column else "not_applicable"


def _absent_metric(out: pd.DataFrame, name: str, reason: str) -> None:
    """상품군에 개념 자체가 없는 지표. 0이 아니라 결측으로 남기고 사유를 출처에 적는다."""
    out[name] = pd.Series(pd.NA, index=out.index, dtype="Float64")
    out[f"{name}_available"] = False
    if f"{name}_source" in UNIFIED_COLUMNS:
        out[f"{name}_source"] = reason
    out[f"{name}_as_of"] = pd.NA


def from_bond(frame: pd.DataFrame) -> pd.DataFrame:
    out = _base(frame, "BOND", "PRBD01N001", "pd_no")
    out["instrument_type"] = "bond"
    out["name"] = frame["pd_nm"]
    _taxonomy(out, frame, None, None)
    _absent_metric(out, "expense_ratio", "not_applicable_for_product_type")
    _absent_metric(out, "aum", "not_applicable_for_product_type")
    _absent_metric(out, "return_1y", "not_applicable_for_product_type")
    return out.reindex(columns=UNIFIED_COLUMNS)


def _from_etp(frame: pd.DataFrame, source_table: str, prefix: str) -> pd.DataFrame:
    instrument = frame["instrument_type"].astype("string").fillna("etf")
    out = _base(frame, prefix, source_table, "pd_itm_no")
    out["product_type"] = prefix + "_" + instrument.str.upper()
    out["instrument_type"] = instrument
    out["name"] = frame["pd_nm"]
    _taxonomy(out, frame, "wu_inv_ast_type", "wu_inv_rgn")

    out["expense_ratio"] = _numeric(frame, "expense_ratio")
    out["expense_ratio_available"] = _flag(frame, "expense_ratio_available")
    out["expense_ratio_source"] = out["expense_ratio_available"].map({True: OFFICIAL, False: pd.NA})
    out["expense_ratio_as_of"] = frame.get("cu_upt_dt")

    out["aum"] = _numeric(frame, "aum")
    out["aum_available"] = _flag(frame, "aum_available")
    out["aum_source"] = out["aum_available"].map({True: OFFICIAL, False: pd.NA})
    out["aum_as_of"] = frame.get("du_upt_dt")
    return out


def from_domestic_etp(frame: pd.DataFrame) -> pd.DataFrame:
    out = _from_etp(frame, "PREF01N001", "DOMESTIC")
    out["return_1y"] = _numeric(frame, "du_er_1y")
    # 종료 상품의 -100은 종가 0에서 계산된 대체값이라 관측값으로 취급하지 않는다.
    ended = frame["listing_status"].astype("string").eq("ended_before_extract")
    out.loc[ended, "return_1y"] = pd.NA
    out["return_1y_available"] = out["return_1y"].notna()
    out["return_1y_source"] = out["return_1y_available"].map({True: OFFICIAL, False: pd.NA})
    out["return_1y_as_of"] = frame.get("du_upt_dt")
    return out.reindex(columns=UNIFIED_COLUMNS)


def from_overseas_etp(frame: pd.DataFrame) -> pd.DataFrame:
    out = _from_etp(frame, "PREF02N001", "OVERSEAS")
    # 해외 ETF에는 1년 수익률 컬럼이 없다. du_er_1d는 전 행 0이라 대체로 쓸 수 없다.
    _absent_metric(out, "return_1y", "column_absent_in_source")
    out["aum_as_of"] = frame.get("du_nav_base_dt").fillna(frame.get("du_upt_dt"))
    return out.reindex(columns=UNIFIED_COLUMNS)


def from_fund(frame: pd.DataFrame) -> pd.DataFrame:
    out = _base(frame, "FUND", "PRFD01N001", "itm_no")
    out["instrument_type"] = "fund"
    out["name"] = frame["itm_nm"]
    _taxonomy(out, frame, "or_attr_desc", "fd_ivst_rgn_desc")

    # 공모펀드 원본에는 보수 컬럼이 없다. 외부 보강은 별도 출처 컬럼으로만 채운다.
    _absent_metric(out, "expense_ratio", "column_absent_in_source")

    out["aum"] = _numeric(frame, "fd_nast_suma")
    out["aum_available"] = out["aum"].notna()
    out["aum_source"] = out["aum_available"].map({True: OFFICIAL, False: pd.NA})
    # 공모펀드에는 개별 기준일 컬럼이 없어 추출일 외에는 증명할 수 없다.
    out["aum_as_of"] = pd.NA

    out["return_1y"] = _numeric(frame, "fd_yr1_ern_r")
    out["return_1y_available"] = out["return_1y"].notna()
    out["return_1y_source"] = out["return_1y_available"].map({True: OFFICIAL, False: pd.NA})
    # 공모펀드에는 개별 기준일 컬럼이 없어 추출일 외에는 증명할 수 없다.
    out["return_1y_as_of"] = pd.NA
    return out.reindex(columns=UNIFIED_COLUMNS)


def build(clean_dir: Path) -> pd.DataFrame:
    unified = pd.concat(
        [
            from_bond(_read(clean_dir / "bond.csv.gz")),
            from_domestic_etp(_read(clean_dir / "domestic_etp.csv.gz")),
            from_overseas_etp(_read(clean_dir / "overseas_etf.csv.gz")),
            from_fund(_read(clean_dir / "fund_master.csv.gz")),
        ],
        ignore_index=True,
    )
    if unified["item_id"].isna().any():
        raise AssertionError("Unified view must not contain rows without an item_id")
    duplicated = unified.duplicated(["source_table", "item_id"])
    if bool(duplicated.any()):
        raise AssertionError(
            f"Unified key must be unique per source table: {int(duplicated.sum())} duplicates"
        )
    for metric in ("expense_ratio", "aum", "return_1y"):
        inconsistent = unified[f"{metric}_available"] & unified[metric].isna()
        if bool(inconsistent.any()):
            raise AssertionError(
                f"{metric}_available is true without a value in {int(inconsistent.sum())} rows"
            )
    return unified


def summarize(unified: pd.DataFrame) -> dict[str, Any]:
    coverage = {}
    for product_type, group in unified.groupby("product_type"):
        coverage[product_type] = {
            "rows": int(len(group)),
            "default_search_eligible": int(group["default_search_eligible"].sum()),
            **{
                metric: round(float(group[f"{metric}_available"].mean() * 100), 4)
                for metric in ("risk", "expense_ratio", "aum", "return_1y")
                if f"{metric}_available" in group.columns
            },
        }
    return {
        "rows": int(len(unified)),
        "product_types": unified["product_type"].value_counts().to_dict(),
        "coverage_pct_by_product_type": coverage,
        "unmapped_taxonomy_rows": {
            "asset_class": int(unified["asset_class_mapping_status"].eq("unmapped").sum()),
            "region": int(unified["region_mapping_status"].eq("unmapped").sum()),
        },
        "metric_as_of_available_rows": {
            metric: int(unified[f"{metric}_as_of"].notna().sum())
            for metric in ("expense_ratio", "aum", "return_1y")
        },
    }


def run(clean_dir: Path, output_dir: Path) -> dict[str, Any]:
    unified = build(clean_dir)
    summary = summarize(unified)
    write_csv(unified, output_dir / "clean" / "product_unified.csv.gz", compressed=True)
    (output_dir / "quality" / "unified_view_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the cross-product-type unified view.")
    parser.add_argument("--clean-dir", type=Path, default=Path("artifacts/data/clean"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/data"))
    args = parser.parse_args()
    print(json.dumps(run(args.clean_dir, args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
