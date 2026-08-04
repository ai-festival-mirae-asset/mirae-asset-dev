"""4개 정제 테이블을 공통 컬럼으로 합쳐 상품군 교차 질의가 가능한 통합 뷰를 만든다.

원칙: 상품군에 원래 없는 개념(예: 채권의 총보수, 해외ETF의 위험등급, 채권/해외ETF의 1년수익률)은
비슷한 다른 값으로 대체하지 않고 결측(-_available=False)으로 남긴다.
"""

from pathlib import Path

import pandas as pd

DATA_CLEAN_DIR = Path(__file__).resolve().parent.parent / "data_clean"

COMMON_COLS = [
    "item_id",
    "product_type",
    "name",
    "asset_class_std",
    "region_std",
    "risk_grade",
    "risk_available",
    "expense_ratio",
    "expense_ratio_available",
    "aum",
    "aum_available",
    "return_1y",
    "return_1y_available",
    "as_of_date",
    "source_table",
    "source_row_key",
]


def _base(df: pd.DataFrame, product_type: str, source_table: str) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["product_type"] = product_type
    out["source_table"] = source_table
    out["asset_class_std"] = df["asset_class_std"]
    out["region_std"] = df["region_std"]
    out["risk_grade"] = df["risk_grade"]
    out["risk_available"] = df["risk_available"]
    out["as_of_date"] = df["as_of_date"]
    return out


def from_bond(df: pd.DataFrame) -> pd.DataFrame:
    out = _base(df, "BOND", "PRBD01N001")
    out["item_id"] = df["PD_NO"]
    out["name"] = df["PD_NM"]
    out["source_row_key"] = "PD_NO=" + df["PD_NO"].astype(str)
    # 채권은 총보수/AUM/1년수익률 개념이 ETF·펀드와 다르므로 강제로 채우지 않음
    out["expense_ratio"] = pd.NA
    out["expense_ratio_available"] = False
    out["aum"] = pd.NA
    out["aum_available"] = False
    out["return_1y"] = pd.NA
    out["return_1y_available"] = False
    return out[COMMON_COLS]


def from_domestic_etf(df: pd.DataFrame) -> pd.DataFrame:
    out = _base(df, "DOMESTIC_ETF", "PREF01N001")
    out["item_id"] = df["pd_itm_no"]
    out["name"] = df["pd_nm"]
    out["source_row_key"] = "pd_itm_no=" + df["pd_itm_no"].astype(str)
    out["expense_ratio"] = df["cu_charge_rt"]
    out["expense_ratio_available"] = df["expense_ratio_available"]
    out["aum"] = df["du_last_aum"]
    out["aum_available"] = df["du_last_aum"].notna()
    out["return_1y"] = df["du_er_1y"]
    out["return_1y_available"] = df["du_er_1y"].notna()
    return out[COMMON_COLS]


def from_overseas_etf(df: pd.DataFrame) -> pd.DataFrame:
    out = _base(df, "OVERSEAS_ETF", "PREF02N001")
    out["item_id"] = df["pd_itm_no"]
    out["name"] = df["pd_nm"]
    out["source_row_key"] = "pd_itm_no=" + df["pd_itm_no"].astype(str)
    out["expense_ratio"] = df["cu_charge_rt"]
    out["expense_ratio_available"] = df["expense_ratio_available"]
    out["aum"] = df["du_last_aum"]
    out["aum_available"] = df["du_last_aum"].notna()
    # 해외ETF에는 1년수익률에 해당하는 컬럼이 없음(du_hpr/du_lpr은 고가/저가일 뿐 수익률 아님) -> 결측 유지
    out["return_1y"] = pd.NA
    out["return_1y_available"] = False
    return out[COMMON_COLS]


def from_fund(df: pd.DataFrame) -> pd.DataFrame:
    out = _base(df, "FUND", "PRFD01N001")
    out["item_id"] = df["itm_no"]
    out["name"] = df["itm_nm"]
    out["source_row_key"] = "itm_no=" + df["itm_no"].astype(str)
    # 공모펀드는 보수 정보 자체가 없음(과제 자료에 명시, 검증됨)
    out["expense_ratio"] = pd.NA
    out["expense_ratio_available"] = False
    out["aum"] = df["fd_nast_suma"]
    out["aum_available"] = df["fd_nast_suma"].notna()
    out["return_1y"] = df["fd_yr1_ern_r"]
    out["return_1y_available"] = df["fd_yr1_ern_r"].notna()
    return out[COMMON_COLS]


if __name__ == "__main__":
    bond = pd.read_csv(DATA_CLEAN_DIR / "bond_clean.csv")
    dom_etf = pd.read_csv(DATA_CLEAN_DIR / "domestic_etf_clean.csv")
    ovs_etf = pd.read_csv(DATA_CLEAN_DIR / "overseas_etf_clean.csv")
    fund = pd.read_csv(DATA_CLEAN_DIR / "fund_master.csv")

    unified = pd.concat(
        [from_bond(bond), from_domestic_etf(dom_etf), from_overseas_etf(ovs_etf), from_fund(fund)],
        ignore_index=True,
    )

    out_path = DATA_CLEAN_DIR / "product_unified.csv"
    unified.to_csv(out_path, index=False)

    print(f"통합 뷰 {len(unified)}행 -> {out_path}")
    print(unified["product_type"].value_counts())
    print()
    print("항목별 커버리지:")
    for col in ["risk_available", "expense_ratio_available", "aum_available", "return_1y_available"]:
        print(unified.groupby("product_type")[col].mean().rename(col))
