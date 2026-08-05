"""국내ETF 총보수·기초지수 외부 보강 (KRX 종목정보).

주최 측 제공 cu_charge_rt는 12.5%(217/1,734)만 채워져 있다. KRX ETF 종목정보
(표준코드=pd_itm_no로 정확히 조인됨, 1,132/1,734=65.3%)로 보강한다.

주의: 기존에 제공 데이터가 있던 215건 중 201건(93.5%)이 KRX 값과 다르다.
특히 제공 데이터=0.00%인데 KRX는 0이 아닌 경우가 많아, 0.00%가 미수집
대체값이었을 가능성이 높다(과제 규칙: 상충 시 주최 측 데이터 우선이므로
값을 덮어쓰지 않고, 결측만 채우고 상충 여부는 별도 플래그로 남긴다).
"""

from pathlib import Path

import pandas as pd

DATA_CLEAN_DIR = Path(__file__).resolve().parent.parent / "data_clean"
KRX_FILE = Path(__file__).resolve().parent.parent / "external_data" / "KRX_ETF_종목정보_20260806.xlsx"
FEE_SOURCE = "external_krx_20260806"
CONFLICT_TOLERANCE = 0.01


def load_krx() -> pd.DataFrame:
    krx = pd.read_excel(KRX_FILE)
    krx["표준코드"] = krx["표준코드"].astype(str).str.strip()
    krx = krx.rename(columns={"총보수": "fee_krx_pct", "기초지수명": "base_index_krx", "기초자산분류": "asset_class_krx"})
    krx = krx.drop_duplicates(subset="표준코드", keep="first")
    return krx[["표준코드", "fee_krx_pct", "base_index_krx", "asset_class_krx"]]


def enrich() -> pd.DataFrame:
    dom = pd.read_csv(DATA_CLEAN_DIR / "domestic_etf_clean.csv")
    dom["pd_itm_no"] = dom["pd_itm_no"].astype(str).str.strip()

    krx = load_krx()
    merged = dom.merge(krx, left_on="pd_itm_no", right_on="표준코드", how="left").drop(columns=["표준코드"])

    has_provided = merged["cu_charge_rt"].notna()
    has_krx = merged["fee_krx_pct"].notna()

    merged["fee_conflict"] = (
        has_provided & has_krx & ((merged["cu_charge_rt"] - merged["fee_krx_pct"]).abs() >= CONFLICT_TOLERANCE)
    )

    # 결측만 채움. 제공 데이터가 있으면(상충하더라도) 절대 덮어쓰지 않음.
    merged["expense_ratio_filled"] = merged["cu_charge_rt"]
    fill_mask = (~has_provided) & has_krx
    merged.loc[fill_mask, "expense_ratio_filled"] = merged.loc[fill_mask, "fee_krx_pct"]

    merged["expense_ratio_source"] = None
    merged.loc[has_provided, "expense_ratio_source"] = "provided"
    merged.loc[fill_mask, "expense_ratio_source"] = FEE_SOURCE

    return merged


if __name__ == "__main__":
    enriched = enrich()
    out_path = DATA_CLEAN_DIR / "domestic_etf_clean.csv"
    enriched.to_csv(out_path, index=False)

    n_provided = (enriched["expense_ratio_source"] == "provided").sum()
    n_filled = (enriched["expense_ratio_source"] == FEE_SOURCE).sum()
    n_conflict = enriched["fee_conflict"].sum()
    total_available = enriched["expense_ratio_filled"].notna().sum()

    print(f"제공 데이터 유지: {n_provided}건 / KRX로 결측 보강: {n_filled}건")
    print(f"총보수 커버리지: {total_available}/{len(enriched)} ({total_available/len(enriched):.1%})")
    print(f"제공값과 KRX값이 상충하는 건(값은 안 바꾸고 플래그만): {n_conflict}건")
