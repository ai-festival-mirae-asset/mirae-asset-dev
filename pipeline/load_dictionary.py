"""해석 메타데이터 사전을 통합 적재하고 원본 스냅샷과 대조 검증한다.

사전은 결측을 메꾸는 자산이 아니라 결측을 정확히 판별하고 있는 값을 정확히 해석하기 위한
자산이다. 따라서 사전이 주장하는 컬럼명과 값·건수가 실제 원본과 어긋나면 사전 자체가
새로운 오답 원인이 된다. 이 모듈은 적재 전에 세 가지를 대조한다.

1. 컬럼사전의 (테이블, 컬럼)이 실제 컬럼과 양방향으로 일치하는가
2. 값사전의 (컬럼, 값)이 실제로 존재하고 건수가 일치하는가
3. 신용등급 사전이 원본에서 관측되는 등급을 모두 덮는가
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline.prepare_data import (
    CREDIT_GRADE_RANK,
    DATASETS,
    clean_text,
    normalize_credit_grade,
    write_csv,
)

PRODUCT_GROUP_TO_CODE = {
    "국내채권": "PRBD01N001",
    "국내ETF": "PREF01N001",
    "해외ETF": "PREF02N001",
    "공모펀드": "PRFD01N001",
}

CONCEPT_DICTIONARIES = (
    "별칭사전",
    "사전_기초지수",
    "사전_신용등급",
    "사전_위험등급",
    "사전_채권분류",
    "사전_코드표",
    "사전_펀드클래스",
)

UNIFIED_COLUMNS = [
    "dictionary",
    "entry_kind",
    "dataset",
    "column",
    "key",
    "label",
    "meaning",
    "synonyms",
    "format_rule",
    "confidence",
    "verification",
    "source",
]


def read_dictionary(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    frame.columns = [str(column).lstrip("﻿").strip() for column in frame.columns]
    for column in frame.columns:
        frame[column] = frame[column].map(lambda value: str(value).strip())
    return frame


def load_raw_frames(input_dir: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for spec in DATASETS:
        raw = pd.read_excel(input_dir / spec.data_file, dtype=object, keep_default_na=False)
        raw.columns = [str(column).lower() for column in raw.columns]
        frames[spec.code] = raw
    return frames


def unify(dictionary_dir: Path) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []

    columns = read_dictionary(dictionary_dir / "컬럼사전.csv")
    rows.append(
        pd.DataFrame(
            {
                "dictionary": "컬럼사전",
                "entry_kind": "column",
                "dataset": columns["테이블ID"],
                "column": columns["컬럼명"].str.lower(),
                "key": columns["컬럼명"].str.lower(),
                "label": columns["한글명"],
                "meaning": columns["설명"],
                "synonyms": columns["동의어"],
                "format_rule": columns["단위_포맷_규칙"],
                "confidence": columns["신뢰도"],
                "verification": columns["검증상태"],
                "source": columns["출처"],
            }
        )
    )

    values = read_dictionary(dictionary_dir / "값사전.csv")
    rows.append(
        pd.DataFrame(
            {
                "dictionary": "값사전",
                "entry_kind": "value",
                "dataset": values["상품군"].map(PRODUCT_GROUP_TO_CODE).fillna(""),
                "column": values["컬럼"].str.lower(),
                "key": values["값"],
                "label": values["값"],
                "meaning": values["의미"],
                "synonyms": values["동의어"],
                "format_rule": values["정규화_규칙"],
                "confidence": values["신뢰도"],
                "verification": "",
                "source": values["출처"],
            }
        )
    )

    for name in CONCEPT_DICTIONARIES:
        concept = read_dictionary(dictionary_dir / f"{name}.csv")
        rows.append(
            pd.DataFrame(
                {
                    "dictionary": name,
                    "entry_kind": "concept",
                    "dataset": "",
                    "column": concept["분류"],
                    "key": concept["키"],
                    "label": concept["한글명"],
                    "meaning": concept["의미"],
                    "synonyms": concept["동의어"],
                    "format_rule": concept["단위_포맷_규칙"],
                    "confidence": concept["신뢰도"],
                    "verification": concept["검증상태"],
                    "source": concept["출처"],
                }
            )
        )

    unified = pd.concat(rows, ignore_index=True)[UNIFIED_COLUMNS]
    duplicated = unified.duplicated(["dictionary", "dataset", "column", "key"])
    if bool(duplicated.any()):
        raise AssertionError(
            f"Dictionary keys must be unique: {unified.loc[duplicated].head().to_dict('records')}"
        )
    return unified


def validate_columns(
    unified: pd.DataFrame, raw_frames: dict[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    entries = unified[unified["entry_kind"].eq("column")]
    for code, raw in raw_frames.items():
        actual = set(raw.columns)
        declared = set(entries.loc[entries["dataset"].eq(code), "column"])
        for column in sorted(declared - actual):
            findings.append(
                {
                    "check": "column_entry_without_data_column",
                    "dataset": code,
                    "column": column,
                    "key": column,
                    "expected": "존재하는 컬럼",
                    "observed": "원본에 없음",
                    "status": "error",
                }
            )
        for column in sorted(actual - declared):
            findings.append(
                {
                    "check": "data_column_without_entry",
                    "dataset": code,
                    "column": column,
                    "key": column,
                    "expected": "사전 항목",
                    "observed": "사전에 없음",
                    "status": "warning",
                }
            )
    return findings


def validate_values(
    unified: pd.DataFrame,
    dictionary_dir: Path,
    raw_frames: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    values = read_dictionary(dictionary_dir / "값사전.csv")
    values["dataset"] = values["상품군"].map(PRODUCT_GROUP_TO_CODE).fillna("")
    values["column_key"] = values["컬럼"].str.lower()
    findings: list[dict[str, Any]] = []
    for row in values.itertuples(index=False):
        code = row.dataset
        raw = raw_frames.get(code)
        if raw is None or row.column_key not in raw.columns:
            findings.append(
                {
                    "check": "value_entry_column_missing",
                    "dataset": code,
                    "column": row.column_key,
                    "key": row.값,
                    "expected": "존재하는 컬럼",
                    "observed": "원본에 없음",
                    "status": "error",
                }
            )
            continue
        observed = raw[row.column_key].map(clean_text)
        actual_count = int(observed.astype("string").eq(row.값).sum())
        try:
            declared_count = int(str(row.건수).replace(",", ""))
        except ValueError:
            declared_count = -1
        if actual_count == 0:
            status, check = "error", "value_not_found"
        elif declared_count < 0:
            status, check = "warning", "declared_count_unreadable"
        elif actual_count != declared_count:
            status, check = "error", "value_count_mismatch"
        else:
            continue
        findings.append(
            {
                "check": check,
                "dataset": code,
                "column": row.column_key,
                "key": row.값,
                "expected": declared_count,
                "observed": actual_count,
                "status": status,
            }
        )
    return findings


def value_coverage(
    dictionary_dir: Path, raw_frames: dict[str, pd.DataFrame]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """값사전이 다루는 컬럼에서 실제 관측값을 몇 %나 설명할 수 있는지 계산한다.

    사전에 없는 값을 만나면 에이전트는 그 값의 의미를 설명할 수 없다. 어느 컬럼을
    "값 설명까지 가능"으로 광고할 수 있는지 판정하려면 이 역방향 커버리지가 필요하다.
    """
    values = read_dictionary(dictionary_dir / "값사전.csv")
    values["dataset"] = values["상품군"].map(PRODUCT_GROUP_TO_CODE).fillna("")
    values["column_key"] = values["컬럼"].str.lower()
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for (code, column), group in values.groupby(["dataset", "column_key"]):
        raw = raw_frames.get(code)
        if raw is None or column not in raw.columns:
            continue
        observed = raw[column].map(clean_text).dropna().astype("string")
        documented = set(group["값"])
        distinct = set(observed.unique())
        undocumented = sorted(distinct - documented)
        explained_rows = int(observed.isin(documented).sum())
        rows.append(
            {
                "dataset": code,
                "column": column,
                "dictionary_entries": len(documented),
                "distinct_observed": len(distinct),
                "distinct_documented": len(distinct & documented),
                "nonnull_rows": int(len(observed)),
                "explained_rows": explained_rows,
                "row_coverage_pct": round(explained_rows / len(observed) * 100, 4)
                if len(observed)
                else 0.0,
            }
        )
        for value in undocumented[:20]:
            findings.append(
                {
                    "check": "observed_value_without_entry",
                    "dataset": code,
                    "column": column,
                    "key": value,
                    "expected": "값사전 항목",
                    "observed": int(observed.eq(value).sum()),
                    "status": "warning",
                }
            )
    return pd.DataFrame(rows), findings


def validate_credit_grades(
    dictionary_dir: Path, raw_frames: dict[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    concept = read_dictionary(dictionary_dir / "사전_신용등급.csv")
    declared = {key.upper() for key in concept["키"]}
    bond = raw_frames["PRBD01N001"]
    observed = {
        grade
        for grade in bond["crd_grd"].map(normalize_credit_grade).dropna().unique()
        if grade
    }
    findings: list[dict[str, Any]] = []
    for grade in sorted(observed - declared):
        findings.append(
            {
                "check": "observed_grade_without_dictionary_entry",
                "dataset": "PRBD01N001",
                "column": "crd_grd",
                "key": grade,
                "expected": "사전 항목",
                "observed": "사전에 없음",
                "status": "warning",
            }
        )
    for grade in sorted(observed - set(CREDIT_GRADE_RANK)):
        findings.append(
            {
                "check": "observed_grade_without_rank",
                "dataset": "PRBD01N001",
                "column": "crd_grd",
                "key": grade,
                "expected": "서열표 항목",
                "observed": "서열표에 없음",
                "status": "error",
            }
        )
    return findings


def run(input_dir: Path, dictionary_dir: Path, output_dir: Path) -> dict[str, Any]:
    raw_frames = load_raw_frames(input_dir)
    unified = unify(dictionary_dir)

    coverage_frame, coverage_findings = value_coverage(dictionary_dir, raw_frames)
    findings = (
        validate_columns(unified, raw_frames)
        + validate_values(unified, dictionary_dir, raw_frames)
        + validate_credit_grades(dictionary_dir, raw_frames)
        + coverage_findings
    )
    validation = pd.DataFrame(
        findings,
        columns=["check", "dataset", "column", "key", "expected", "observed", "status"],
    )
    if not validation.empty:
        validation = validation.sort_values(
            ["status", "check", "dataset", "column", "key"], kind="stable"
        )

    column_entries = unified[unified["entry_kind"].eq("column")]
    coverage = {}
    for code, raw in raw_frames.items():
        declared = set(column_entries.loc[column_entries["dataset"].eq(code), "column"])
        coverage[code] = {
            "data_columns": len(raw.columns),
            "documented": len(declared & set(raw.columns)),
            "coverage_pct": round(len(declared & set(raw.columns)) / len(raw.columns) * 100, 4),
        }

    summary = {
        "dictionary_dir": str(dictionary_dir),
        "entry_counts": unified.groupby("dictionary").size().to_dict(),
        "entry_kind_counts": unified.groupby("entry_kind").size().to_dict(),
        "total_entries": len(unified),
        "column_coverage": coverage,
        "confidence_counts": unified["confidence"].replace("", "unstated").value_counts().to_dict(),
        "validation_counts": (
            validation["status"].value_counts().to_dict() if not validation.empty else {}
        ),
        "validation_by_check": (
            validation["check"].value_counts().to_dict() if not validation.empty else {}
        ),
        "value_entries_verified": int(
            (unified["entry_kind"].eq("value")).sum()
            - (validation["check"].isin(
                ["value_count_mismatch", "value_not_found", "value_entry_column_missing"]
            ).sum() if not validation.empty else 0)
        ),
    }

    summary["value_column_coverage"] = {
        "columns_documented": len(coverage_frame),
        "fully_explained_columns": int(coverage_frame["row_coverage_pct"].eq(100).sum())
        if not coverage_frame.empty
        else 0,
        "min_row_coverage_pct": float(coverage_frame["row_coverage_pct"].min())
        if not coverage_frame.empty
        else 0.0,
    }

    # 사전이 원본과 어긋나면 해석 계층이 곧 오답 원인이 되므로 error는 통과시키지 않는다.
    # warning(사전에 없는 관측값 등)은 커버리지 정보로 남기고 진행한다.
    summary["assertions"] = {
        "column_dictionary_covers_every_column": all(
            entry["coverage_pct"] == 100.0 for entry in coverage.values()
        ),
        "value_dictionary_counts_match_snapshot": validation.empty
        or not validation["status"].eq("error").any(),
        "observed_credit_grades_are_ranked": not (
            not validation.empty
            and validation["check"].eq("observed_grade_without_rank").any()
        ),
    }
    summary["all_assertions_passed"] = all(summary["assertions"].values())

    write_csv(unified, output_dir / "reference" / "term_dictionary.csv")
    write_csv(coverage_frame, output_dir / "quality" / "dictionary_value_coverage.csv")
    write_csv(validation, output_dir / "quality" / "dictionary_validation.csv")
    if not summary["all_assertions_passed"]:
        failed = [name for name, passed in summary["assertions"].items() if not passed]
        raise AssertionError(
            f"Dictionary validation failed: {failed}. "
            f"자세한 내용은 {output_dir / 'quality' / 'dictionary_validation.csv'}를 확인하세요."
        )
    (output_dir / "quality" / "dictionary_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load and cross-check the interpretation dictionaries against the snapshot."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("datasets"))
    parser.add_argument("--dictionary-dir", type=Path, default=Path("외부데이터/사전"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/data"))
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.input_dir, args.dictionary_dir, args.output_dir),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
