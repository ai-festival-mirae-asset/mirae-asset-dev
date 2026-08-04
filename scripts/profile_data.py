"""4종 원본 데이터셋을 로드해 행/컬럼 수, 타입, 결측률을 독립적으로 확인한다."""

from pathlib import Path

import pandas as pd

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"

TABLES = {
    "PRBD01N001_국내채권": "PRBD01N001_국내채권마스터_20260711_datarows.xlsx",
    "PREF01N001_국내ETF": "PREF01N001_국내ETF마스터_20260711_datarows.xlsx",
    "PREF02N001_해외ETF": "PREF02N001_해외ETF마스터_20260711_datarows.xlsx",
    "PRFD01N001_공모펀드": "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx",
}


def profile(name: str, filename: str) -> pd.DataFrame:
    df = pd.read_excel(DATASETS_DIR / filename, dtype=str)
    print(f"\n=== {name} ===")
    print(f"행 {len(df)} × 컬럼 {len(df.columns)}")

    null_rate = (df.isna() | (df.map(lambda v: isinstance(v, str) and v.strip() == ""))).mean()
    worst = null_rate.sort_values(ascending=False).head(10)
    print("결측률 상위 10개 컬럼:")
    for col, rate in worst.items():
        print(f"  {col}: {rate:.1%}")

    return df


if __name__ == "__main__":
    frames = {name: profile(name, filename) for name, filename in TABLES.items()}
