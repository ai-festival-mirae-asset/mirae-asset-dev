# -*- coding: utf-8 -*-
"""verify_determinism.diff_cells 단위 테스트.

무엇: 결정성 검증기의 셀 단위 diff 함수가 불일치 좌표(행·컬럼·양쪽 값)를
      정확히 짚는지 검증한다.
왜: 8/7 검증에서 채권 CSV 해시가 1회 간헐 불일치했으나 초판 검증기는 해시만
    남겨 원인 조사가 불가능했다. 재발 시 이 diff 가 유일한 원인 규명 수단이므로,
    diff 자체가 틀리면 증거 보존 전체가 무의미해진다 — 경계(동일 파일, 단일 셀,
    행수·컬럼 불일치, 상한 잘림)를 고정한다.
"""
import os

import pandas as pd
import pytest

import verify_determinism as vd
from verify_determinism import diff_cells


def _write(path, df):
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


class TestDiffCells:
    def test_identical_files_no_records(self, tmp_path):
        """동일 파일 → diff 0건, 잘림 없음 (거짓 양성 금지)."""
        df = pd.DataFrame({"PD_NO": ["KR1", "KR2"], "MAT_DT": ["2026-01-01", "2027-01-01"]})
        a = _write(tmp_path / "a.csv", df)
        b = _write(tmp_path / "b.csv", df)
        recs, truncated = diff_cells(a, b)
        assert recs == [] and truncated is False

    def test_single_cell_diff_coordinates(self, tmp_path):
        """단일 셀 차이 → 행번호·컬럼명·양쪽 값이 정확히 기록된다."""
        a = _write(tmp_path / "a.csv", pd.DataFrame({"k": ["x", "y"], "v": ["1", "2"]}))
        b = _write(tmp_path / "b.csv", pd.DataFrame({"k": ["x", "y"], "v": ["1", "9"]}))
        recs, truncated = diff_cells(a, b)
        assert truncated is False
        assert recs == [{"row": 1, "column": "v", "run_a": "2", "run_b": "9"}]

    def test_empty_vs_value_diff(self, tmp_path):
        """NULL(빈 문자열) vs 값 차이도 잡는다 — keep_default_na=False 원문 비교."""
        a = _write(tmp_path / "a.csv", pd.DataFrame({"k": ["x"], "v": [""]}))
        b = _write(tmp_path / "b.csv", pd.DataFrame({"k": ["x"], "v": ["0"]}))
        recs, _ = diff_cells(a, b)
        assert recs == [{"row": 0, "column": "v", "run_a": "", "run_b": "0"}]

    def test_row_count_mismatch_special_record(self, tmp_path):
        """행수가 다르면 row=-1 특수 레코드로 먼저 알린다 (공통 구간은 계속 비교)."""
        a = _write(tmp_path / "a.csv", pd.DataFrame({"k": ["x", "y"]}))
        b = _write(tmp_path / "b.csv", pd.DataFrame({"k": ["x"]}))
        recs, _ = diff_cells(a, b)
        assert recs[0]["row"] == -1 and "행수" in recs[0]["column"]
        assert recs[0]["run_a"] == "2" and recs[0]["run_b"] == "1"

    def test_column_list_mismatch_special_record(self, tmp_path):
        """컬럼 목록이 다르면 row=-1 특수 레코드 + 공통 컬럼만 셀 비교한다."""
        a = _write(tmp_path / "a.csv", pd.DataFrame({"k": ["x"], "only_a": ["1"]}))
        b = _write(tmp_path / "b.csv", pd.DataFrame({"k": ["z"]}))
        recs, _ = diff_cells(a, b)
        assert recs[0]["row"] == -1 and "컬럼" in recs[0]["column"]
        assert {"row": 0, "column": "k", "run_a": "x", "run_b": "z"} in recs

    def test_max_records_truncation(self, tmp_path):
        """diff 가 상한을 넘으면 잘림 플래그 True — 증거 파일 폭주 방지."""
        a = _write(tmp_path / "a.csv", pd.DataFrame({"v": [str(i) for i in range(10)]}))
        b = _write(tmp_path / "b.csv", pd.DataFrame({"v": [str(i + 1) for i in range(10)]}))
        recs, truncated = diff_cells(a, b, max_records=3)
        assert truncated is True and len(recs) == 3


class TestPreserveEvidence:
    def test_evidence_files_created(self, tmp_path, monkeypatch):
        """불일치 시 증거 일체(.gitignore·runN 스냅샷·셀 diff·메타)가 남는지 검증.

        왜: 8/7 실패의 교훈 — 증거 보존 경로 자체가 죽어 있으면 재발해도
        또 빈손이 된다. 실제 파이프라인 없이 경로만 스위칭해 흐름을 고정한다.
        """
        out = tmp_path / "processed"
        diag = tmp_path / "diag"
        out.mkdir()
        name = "PRBD01N001_kr_bond_processed.csv"
        _write(out / name, pd.DataFrame({"PD_NO": ["KR1"], "v": ["1"]}))
        monkeypatch.setattr(vd, "OUT", str(out))
        monkeypatch.setattr(vd, "DIAG", str(diag))

        vd.snapshot_outputs(os.path.join(str(diag), "run1"))     # 1회차 보존
        _write(out / name, pd.DataFrame({"PD_NO": ["KR1"], "v": ["2"]}))  # 2회차가 다른 값 생성
        vd.preserve_evidence(2, [name], input_changed=False)

        assert (diag / ".gitignore").read_text(encoding="utf-8").strip() == "*"
        assert (diag / "run2" / name).exists()
        diff = pd.read_csv(diag / f"celldiff_run1_vs_run2__{name}", dtype=str, encoding="utf-8-sig")
        assert len(diff) == 1
        assert diff.loc[0, "column"] == "v" and diff.loc[0, "run_a"] == "1" and diff.loc[0, "run_b"] == "2"
        meta = (diag / "meta.txt").read_text(encoding="utf-8")
        assert "파이프라인 비결정 의심" in meta and name in meta
