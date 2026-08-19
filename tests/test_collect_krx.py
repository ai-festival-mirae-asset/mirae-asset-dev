# -*- coding: utf-8 -*-
"""KRX 구성종목 수집기 순수 함수 테스트 — 네트워크 없이 게이트·분류·병합만 검증."""
import importlib.util
import os

import pytest

# collectors/ 는 패키지가 아니므로 파일 경로로 직접 로드한다
_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "external_data", "collectors", "collect_krx_etf_constituents.py")
_spec = importlib.util.spec_from_file_location("collect_krx", _PATH)
ck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ck)


@pytest.mark.parametrize("date, allow, ok", [
    ("20260710", False, True),    # 기준일 직전 거래일 — 기본 수집일
    ("20260711", False, True),    # 기준일 당일까지 허용
    ("20260712", False, False),   # 기준일 이후 — 규정 게이트 차단
    ("20260712", True, True),     # 명시 플래그로만 해제
    ("2026-07-10", False, False), # 형식 오류
    ("", False, False),
])
def test_baseline_gate(date, allow, ok):
    assert ck.baseline_ok(date, allow) == ok


def test_classify_response_variants():
    assert ck.classify_response("LOGOUT") == ("logout", None)
    assert ck.classify_response(" logout ".upper()) == ("logout", None)
    kind, rows = ck.classify_response('{"output": [{"ISU_NM": "삼성전자", "COMPST_RTO": "31.1"}]}')
    assert kind == "rows" and rows[0]["ISU_NM"] == "삼성전자"
    assert ck.classify_response('{"output": []}') == ("empty", [])
    kind, rows = ck.classify_response('{"OutBlock_1": [{"a": 1}]}')
    assert kind == "rows"
    assert ck.classify_response("<html>error</html>")[0] == "error"
    assert ck.classify_response('{"message": "no block"}')[0] == "error"
    assert ck.classify_response("")[0] == "error"


def test_merge_rows_keeps_raw_columns_and_ids():
    records = [
        ("KR1", "ETF하나", [{"ISU_NM": "삼성전자", "SHRS": "10"}]),
        ("KR2", "ETF둘", [{"ISU_NM": "에코프로", "WT": "5.5"}, {"ISU_NM": "현대차", "WT": "3.1"}]),
    ]
    fieldnames, rows = ck.merge_rows(records)
    assert fieldnames[:2] == ["etf_isin", "etf_name"]
    assert set(fieldnames) == {"etf_isin", "etf_name", "ISU_NM", "SHRS", "WT"}
    assert len(rows) == 3
    assert rows[0]["etf_isin"] == "KR1" and rows[2]["ISU_NM"] == "현대차"
