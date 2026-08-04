"""schema에 numeric으로 선언된 컬럼에서 0, -100 같은 값이 결측 대체값으로 쓰였는지 직접 확인한다."""

from pathlib import Path

import pandas as pd

DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"

NUMERIC_TYPES = {"double precision", "numeric", "bigint"}

TABLES = {
    "국내채권": (
        "PRBD01N001_국내채권마스터_schema.xlsx",
        "PRBD01N001_국내채권마스터_20260711_datarows.xlsx",
    ),
    "국내ETF": (
        "PREF01N001_국내ETF마스터_schema.xlsx",
        "PREF01N001_국내ETF마스터_20260711_datarows.xlsx",
    ),
    "해외ETF": (
        "PREF02N001_해외ETF마스터_schema.xlsx",
        "PREF02N001_해외ETF마스터_20260711_datarows.xlsx",
    ),
    "공모펀드": (
        "PRFD01N001_공모펀드마스터_schema.xlsx",
        "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx",
    ),
}

SUSPECT_VALUES = {"0", "0.0", "-100", "-100.0"}


def numeric_columns(schema_file: str) -> list[str]:
    schema = pd.read_excel(DATASETS_DIR / schema_file, sheet_name="Sheet1_Schema", header=1)
    return schema.loc[schema["컬럼타입"].isin(NUMERIC_TYPES), "컬럼명"].tolist()


def scan(name: str, schema_file: str, data_file: str) -> None:
    cols = numeric_columns(schema_file)
    df = pd.read_excel(DATASETS_DIR / data_file, dtype=str)
    cols = [c for c in cols if c in df.columns]

    print(f"\n=== {name}: numeric 선언 컬럼 {len(cols)}개 중 의심 값 상위 ===")
    flagged = []
    for col in cols:
        s = df[col].astype(str).str.strip()
        non_null = s[(s != "") & (s.str.lower() != "nan")]
        if len(non_null) == 0:
            continue
        vc = non_null.value_counts()
        top_val, top_count = vc.index[0], vc.iloc[0]
        top_ratio = top_count / len(non_null)
        is_suspect_value = top_val in SUSPECT_VALUES
        if top_ratio >= 0.3 or is_suspect_value and top_ratio >= 0.05:
            flagged.append((col, top_val, top_count, len(non_null), top_ratio))

    flagged.sort(key=lambda x: -x[4])
    for col, top_val, top_count, n, ratio in flagged[:15]:
        print(f"  {col}: 최빈값={top_val!r} {top_count}/{n} ({ratio:.1%})")

    if not flagged:
        print("  의심 값 없음")


if __name__ == "__main__":
    for name, (schema_file, data_file) in TABLES.items():
        scan(name, schema_file, data_file)
