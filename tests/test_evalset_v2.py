# -*- coding: utf-8 -*-
"""블라인드 평가셋 v2 무결성 — 80문항·검사표 구조·검사 SQL 실행 가능(8/22).

생성기(make_evalset_v2.py)가 seed 고정이라 재생성해도 같은 파일이 나와야 하고, 검사표의
SQL 은 전부 실행돼야 한다(채점기는 검사 오류도 실패로 드러내므로 미리 잡는다).
"""
import io
import json
import os

import duckdb
import pytest

from pipeline.entity_index import DB_PATH_DEFAULT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL = os.path.join(ROOT, "evalset", "evalset_v2.jsonl")
CHECKS = os.path.join(ROOT, "evalset", "checks_v2.jsonl")
KNOWN = {"answer_has_any", "answer_has_all", "answer_has_none", "answer_regex", "note_any",
         "sql_names", "sql_number", "evidence_source_any", "evidence_min", "any_of"}


def _rows(path):
    return [json.loads(l) for l in io.open(path, encoding="utf-8") if l.strip()]


def test_v2_structure():
    items, checks = _rows(EVAL), _rows(CHECKS)
    assert len(items) == 80 and len(checks) == 80
    ids = [it["id"] for it in items]
    assert len(set(ids)) == 80 and ids == [c["id"] for c in checks]
    assert {it["level"] for it in items} <= {"하", "중", "상", "트랩"}
    assert {it["behavior"] for it in items} <= {"answer", "partial", "refuse"}
    assert sum(1 for it in items if it["behavior"] == "refuse") == 15          # 함정 15
    assert sum(1 for it in items if it["id"].startswith("V2-P-")) == 15        # 표현 변형 15
    for it in items:
        assert it["question"].strip() and it["id"].startswith("V2-")


def _flatten(check):
    if check["type"] == "any_of":
        for c in check["checks"]:
            yield from _flatten(c)
    else:
        yield check


@pytest.mark.skipif(not os.path.exists(DB_PATH_DEFAULT), reason="DuckDB 미적재")
def test_v2_checks_executable():
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    for row in _rows(CHECKS):
        for ch in row["checks"]:
            for c in _flatten(ch):
                assert c["type"] in KNOWN, (row["id"], c["type"])
                if c["type"] in ("sql_names", "sql_number"):
                    rows = con.execute(c["sql"]).fetchall()
                    assert rows, (row["id"], c["name"])
