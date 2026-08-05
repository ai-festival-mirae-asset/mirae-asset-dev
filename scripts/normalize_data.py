"""4종 원본 엑셀을 읽어 정제된 CSV로 저장한다.

독립 검증(scripts/check_keys.py, scripts/check_sentinels.py, notes/data-verification-log.md)에서
확인한 사실만 반영한다. 검증하지 않은 파생 분류(ETF/ETN 구분 등)는 추가하지 않는다.
"""

from pathlib import Path

import pandas as pd

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_clean"
OUT_DIR.mkdir(exist_ok=True)

AS_OF_DATE = "2026-07-11"

# 검증 완료: 전 행이 정확히 0인 죽은 컬럼. 진짜 값은 옆에 적은 컬럼에 있음.
BOND_DEAD_COLS = ["AVG_ANNUAL_TAX_YIELD"]
DOM_ETF_DEAD_COLS = [
    "pd_lst_price",  # 진짜 종가: du_clpr
    "du_chas_errt",
    "du_diff_rt",
    "pd_divd_amt_pshr",
    "pd_dvid_yield",
    "pd_net_ast_pshr",  # 진짜 NAV: pd_nav_pshr / du_last_nav
    "pd_net_prft_pshr",
    "pd_net_rt_ast_pshr",
    "cu_charge_etc_rt",  # 1,553건 있지만 전부 0.0. 총보수는 cu_charge_rt(217/1734)만 유효
]
OVS_ETF_DEAD_COLS = [
    "pd_lst_price",  # 진짜 종가: du_clpr
    "du_er_1d",
    "du_diff_rt",  # 3건만 존재, 사실상 사용 불가
]


def _trim_strings(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace({"": None, "nan": None, "NaN": None})
    return df


_TAXONOMY_PATH = OUT_DIR / "taxonomy_mapping.csv"
_taxonomy_cache: pd.DataFrame | None = None


def _load_taxonomy() -> pd.DataFrame:
    global _taxonomy_cache
    if _taxonomy_cache is None:
        if not _TAXONOMY_PATH.exists():
            raise FileNotFoundError(
                f"{_TAXONOMY_PATH}가 없습니다. 먼저 scripts/build_taxonomy_mapping.py를 실행하세요."
            )
        _taxonomy_cache = pd.read_csv(_TAXONOMY_PATH)
    return _taxonomy_cache


def _apply_taxonomy(
    df: pd.DataFrame, table: str, source_column: str, dimension: str, out_prefix: str
) -> pd.DataFrame:
    """source_column 값을 표준 코드로 매핑해 {out_prefix}_std, {out_prefix}_mapping_status 컬럼을 추가한다."""
    tax = _load_taxonomy()
    sub = tax[
        (tax["source_table"] == table)
        & (tax["source_column"] == source_column)
        & (tax["standard_dimension"] == dimension)
    ][["source_value", "standard_code", "mapping_status"]]

    merged = df.merge(
        sub, left_on=source_column, right_on="source_value", how="left"
    ).drop(columns=["source_value"])
    merged = merged.rename(
        columns={"standard_code": f"{out_prefix}_std", "mapping_status": f"{out_prefix}_mapping_status"}
    )
    return merged


def normalize_bond() -> pd.DataFrame:
    df = pd.read_excel(
        DATASETS_DIR / "PRBD01N001_국내채권마스터_20260711_datarows.xlsx", dtype=str
    )
    df = _trim_strings(df)
    df = df.drop(columns=[c for c in BOND_DEAD_COLS if c in df.columns])

    df["issue_date"] = pd.to_datetime(df["ISU_DT"], format="%Y%m%d", errors="coerce")
    df["maturity_date"] = pd.to_datetime(df["MAT_DT"], format="%Y%m%d", errors="coerce")
    df["is_matured"] = df["maturity_date"] < pd.Timestamp(AS_OF_DATE)

    risk = pd.to_numeric(df["PD_RISK_GCD"], errors="coerce")
    df["risk_grade"] = risk.where(risk != 0)  # 0=미분류 -> null
    df["risk_available"] = df["risk_grade"].notna()

    buyable_qty = pd.to_numeric(df["BUYABLE_QUANTITY"], errors="coerce")
    df["is_buyable"] = buyable_qty > 0
    df["buyable_info_available"] = buyable_qty.notna()

    df = _apply_taxonomy(df, "bond", "PD_CTRY_CD", "region", "region")
    df["asset_class_std"] = "BOND"  # 국내채권 테이블 전체가 채권 (build_taxonomy_mapping.py의 상수와 동일)
    df["asset_class_mapping_status"] = "mapped"

    df["as_of_date"] = AS_OF_DATE
    df["source_table"] = "PRBD01N001"
    return df


def normalize_domestic_etf() -> pd.DataFrame:
    df = pd.read_excel(
        DATASETS_DIR / "PREF01N001_국내ETF마스터_20260711_datarows.xlsx", dtype=str
    )
    df = _trim_strings(df)
    df = df.drop(columns=[c for c in DOM_ETF_DEAD_COLS if c in df.columns])

    # pd_risk_cd는 'PD_RISK_GCD_11'~'PD_RISK_GCD_16' 형태 -> 마지막 숫자가 1~6 등급
    df["risk_grade"] = df["pd_risk_cd"].str.extract(r"(\d)$").astype(float)
    df["risk_available"] = df["risk_grade"].notna()

    # pd_itm_no 접두사가 ETF/ETN을 완전히 구분함 (KRX ETF/ETN 종목정보 파일과 대조해 검증됨,
    # 교차매칭 0건). KR7=ETF 1,201건, KRG=ETN 532건, 그 외 1건(코드가 'KR'뿐인 깨진 행)은 미분류.
    df["instrument_type"] = None
    df.loc[df["pd_itm_no"].str.startswith("KR7", na=False), "instrument_type"] = "ETF"
    df.loc[df["pd_itm_no"].str.startswith("KRG", na=False), "instrument_type"] = "ETN"

    for col in ["du_clpr", "pd_nav_pshr", "du_last_aum", "cu_charge_rt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["expense_ratio_available"] = df["cu_charge_rt"].notna()

    df = _apply_taxonomy(df, "domestic_etf", "wu_inv_rgn", "region", "region")
    df = _apply_taxonomy(df, "domestic_etf", "wu_inv_ast_type", "asset_class", "asset_class")

    df["as_of_date"] = AS_OF_DATE
    df["source_table"] = "PREF01N001"
    return df


def normalize_overseas_etf() -> pd.DataFrame:
    df = pd.read_excel(
        DATASETS_DIR / "PREF02N001_해외ETF마스터_20260711_datarows.xlsx", dtype=str
    )
    df = _trim_strings(df)
    df = df.drop(columns=[c for c in OVS_ETF_DEAD_COLS if c in df.columns])

    df["risk_grade"] = pd.NA  # 해외ETF에는 위험등급 컬럼 자체가 없음 (검증됨)
    df["risk_available"] = False

    for col in ["du_clpr", "du_last_aum", "cu_charge_rt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["expense_ratio_available"] = df["cu_charge_rt"].notna()

    df = _apply_taxonomy(df, "overseas_etf", "wu_inv_rgn", "region", "region")
    df = _apply_taxonomy(df, "overseas_etf", "wu_inv_ast_type", "asset_class", "asset_class")

    df["as_of_date"] = AS_OF_DATE
    df["source_table"] = "PREF02N001"
    return df


def normalize_fund() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = pd.read_excel(
        DATASETS_DIR / "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx", dtype=str
    )
    df = _trim_strings(df)

    is_malformed = df["itm_no"] == '"'
    quarantine = df[is_malformed].copy()
    df = df[~is_malformed].copy()

    if "zrin_fd_ivst_risk_gcd" in df.columns:
        df["zrin_fd_ivst_risk_gcd"] = df["zrin_fd_ivst_risk_gcd"].replace({"NULL": None})
        risk = pd.to_numeric(df["zrin_fd_ivst_risk_gcd"], errors="coerce")
        df["risk_grade"] = risk
        df["risk_available"] = risk.notna()

    df = _apply_taxonomy(df, "fund", "fd_ivst_rgn_desc", "region", "region")
    df = _apply_taxonomy(df, "fund", "or_attr_desc", "asset_class", "asset_class")

    df["as_of_date"] = AS_OF_DATE
    df["source_table"] = "PRFD01N001"

    # task#3에서 검증됨: itm_no 그룹 내에서 prfd_attr_cd 외에 값이 달라지는 컬럼은 없음
    master_cols = [c for c in df.columns if c != "prfd_attr_cd"]
    master = df[master_cols].drop_duplicates(subset="itm_no").reset_index(drop=True)
    attribute = df[["itm_no", "prfd_attr_cd"]].reset_index(drop=True) if "prfd_attr_cd" in df.columns else pd.DataFrame()

    return master, attribute, quarantine


if __name__ == "__main__":
    bond = normalize_bond()
    bond.to_csv(OUT_DIR / "bond_clean.csv", index=False)
    print(f"국내채권: {len(bond)}행 -> data_clean/bond_clean.csv")

    dom_etf = normalize_domestic_etf()
    dom_etf.to_csv(OUT_DIR / "domestic_etf_clean.csv", index=False)
    print(f"국내ETF: {len(dom_etf)}행 -> data_clean/domestic_etf_clean.csv")

    ovs_etf = normalize_overseas_etf()
    ovs_etf.to_csv(OUT_DIR / "overseas_etf_clean.csv", index=False)
    print(f"해외ETF: {len(ovs_etf)}행 -> data_clean/overseas_etf_clean.csv")

    fund_master, fund_attribute, fund_quarantine = normalize_fund()
    fund_master.to_csv(OUT_DIR / "fund_master.csv", index=False)
    fund_attribute.to_csv(OUT_DIR / "fund_attribute.csv", index=False)
    fund_quarantine.to_csv(OUT_DIR / "fund_quarantine.csv", index=False)
    print(f"공모펀드 master: {len(fund_master)}행, attribute: {len(fund_attribute)}행, quarantine: {len(fund_quarantine)}행")
