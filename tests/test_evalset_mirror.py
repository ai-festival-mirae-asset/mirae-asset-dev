# -*- coding: utf-8 -*-
"""실전 미러 평가셋 무결성 — 35문항(실전 분포 하10·중10·상10·답변불가5)·검사 SQL 실행 가능(8/26).

문항은 전부 공식 예시 8개의 '유형'에서만 나온다(출제자 정렬 — evalset/EVALSET_README.md §10).
관계가 핵심인 문항은 '관계 표현' 검사를 갖는다(주최 채점 문구 '기대 개체·관계 포함' 미러).
"""
import io
import json
import os

import duckdb
import pytest

from pipeline.entity_index import DB_PATH_DEFAULT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, "evalset", "evalset_mirror.jsonl")
CHECKS = os.path.join(ROOT, "evalset", "checks_mirror.jsonl")
KNOWN = {"answer_has_any", "answer_has_all", "answer_has_none", "answer_regex", "note_any",
         "sql_names", "sql_number", "evidence_source_any", "evidence_min", "any_of"}


def _rows(path):
    return [json.loads(l) for l in io.open(path, encoding="utf-8") if l.strip()]


def test_mirror_structure_follows_real_exam():
    # 8/27: 본 35(실전 분포 미러) + 부록 A 3(MR-A — 8/26 공지의 교차질의·분배 유형)
    items, checks = _rows(EVAL), _rows(CHECKS)
    assert len(items) == 38 and len(checks) == 38
    ids = [it["id"] for it in items]
    assert len(set(ids)) == 38 and ids == [c["id"] for c in checks]
    core = [it for it in items if not it["id"].startswith("MR-A-")]
    by_level = {}
    for it in core:
        by_level[it["level"]] = by_level.get(it["level"], 0) + 1
    assert by_level == {"하": 10, "중": 10, "상": 10, "트랩": 5}      # 실전 분포(30+5)는 본 35 기준 유지
    assert sum(1 for it in items if it["behavior"] == "refuse") == 5
    assert sum(1 for it in items if it["id"].startswith("MR-A-")) == 3


def _flatten(check):
    if check["type"] == "any_of":
        for c in check["checks"]:
            yield from _flatten(c)
    else:
        yield check


def test_mirror_has_relation_axis():
    """관계 질문(운용·편입·추종)에는 관계 표현 검사가 있다 — 주최 채점 방식 미러."""
    checks = {c["id"]: c["checks"] for c in _rows(CHECKS)}
    relation_items = ["MR-M-04", "MR-M-05", "MR-M-06", "MR-M-08", "MR-M-09", "MR-M-10", "MR-H-08", "MR-H-10"]
    for iid in relation_items:
        names = [c.get("name") for ch in checks[iid] for c in _flatten(ch)]
        assert "관계 표현" in names, iid


@pytest.mark.skipif(not os.path.exists(DB_PATH_DEFAULT), reason="DuckDB 미적재")
def test_mirror_checks_executable():
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    for row in _rows(CHECKS):
        for ch in row["checks"]:
            for c in _flatten(ch):
                assert c["type"] in KNOWN, (row["id"], c["type"])
                if c["type"] in ("sql_names", "sql_number"):
                    con.execute(c["sql"]).fetchall()
