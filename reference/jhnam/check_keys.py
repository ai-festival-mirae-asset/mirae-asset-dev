"""후보 키의 유일성을 원본 데이터로 직접 검증한다. 결측률과 별개로, 공백 처리 전/후를 모두 본다."""

from pathlib import Path

import pandas as pd

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"


def load(filename: str) -> pd.DataFrame:
    return pd.read_excel(DATASETS_DIR / filename, dtype=str)


def report_key(df: pd.DataFrame, name: str, cols: list[str]) -> None:
    sub = df[cols].copy()
    for c in cols:
        sub[c] = sub[c].astype(str).str.strip()

    n_rows = len(sub)
    n_blank = (sub == "").any(axis=1).sum()
    n_na = sub.isna().any(axis=1).sum()
    n_dupe = sub.duplicated().sum()
    n_unique = sub.drop_duplicates().shape[0]

    print(f"\n[{name}] key={cols}")
    print(f"  전체 행: {n_rows}")
    print(f"  공백 포함 행: {n_blank}")
    print(f"  NaN 포함 행: {n_na}")
    print(f"  중복 조합 수(행 기준): {n_dupe}")
    print(f"  고유 조합 수: {n_unique}")
    print(f"  유일성: {'OK' if n_dupe == 0 and n_blank == 0 else 'FAIL'}")


if __name__ == "__main__":
    bond = load("PRBD01N001_국내채권마스터_20260711_datarows.xlsx")
    report_key(bond, "국내채권", ["PD_NO"])

    dom_etf = load("PREF01N001_국내ETF마스터_20260711_datarows.xlsx")
    report_key(dom_etf, "국내ETF", ["pd_itm_no"])
    if "pd_itm_no_ma" in dom_etf.columns:
        report_key(dom_etf, "국내ETF (pd_itm_no_ma)", ["pd_itm_no_ma"])

    ovs_etf = load("PREF02N001_해외ETF마스터_20260711_datarows.xlsx")
    report_key(ovs_etf, "해외ETF", ["pd_itm_no"])
    if "pd_isin_cd" in ovs_etf.columns:
        report_key(ovs_etf, "해외ETF (pd_isin_cd)", ["pd_isin_cd"])

    fund = load("PRFD01N001_공모펀드마스터_20260711_datarows.xlsx")
    if "itm_no" in fund.columns:
        report_key(fund, "공모펀드 (itm_no만)", ["itm_no"])
        if "prfd_attr_cd" in fund.columns:
            report_key(fund, "공모펀드 (itm_no+prfd_attr_cd)", ["itm_no", "prfd_attr_cd"])
        else:
            print("\n[경고] prfd_attr_cd 컬럼을 찾지 못함. 실제 컬럼명:")
            print(list(fund.columns))
    else:
        print("\n[경고] itm_no 컬럼을 찾지 못함. 실제 컬럼명:")
        print(list(fund.columns))
