# -*- coding: utf-8 -*-
"""리더 세션 18바퀴(9/6 최종) — 채권·펀드·ETF·해외·함정 30문항 점검이 찾은 공백 3건의 회귀 잠금.

찾은 것(규칙 엔진 실측): '최근에 발행된 채권 5개'(정렬 없이 20건) · '위험등급 6등급 펀드 몇 개'('등급'이 상품명 조건으로 새어 0건) ·
'월배당 ETF 몇 개'(상품명 '월배당' 검색 0건 — 지급횟수 12회 조건이어야 한다).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.channels import resolve_raw_params
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import run_template
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)
POLICY = load_policy()
_CATALOG_SHA256_FROZEN = "a3f8e65498b70ed5264da3fcf84f5336b52cb4b6448e483b0c9bce07ebf25855"


@pytest.fixture(scope="module")
def con():
    return duckdb.connect(DB_PATH_DEFAULT, read_only=True)


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


def _call(plan, op):
    return next((c for c in plan.calls if c.op == op), None)


def _rows(con, c):
    return run_template(con, c.op, resolve_raw_params(c.params)).rows


def test_recently_issued_bonds_sorted(index, con):
    plan = _route(index, "최근에 발행된 채권 5개")
    c = _call(plan, "bond_filter")
    assert c is not None and c.params.get("order") == "issue_desc" and plan.hints.get("display_rows") == 5
    rows = _rows(con, c)
    dates = [str(r.get("ISU_DT") or "").replace("-", "") for r in rows]
    assert dates and dates == sorted(dates, reverse=True)
    assert _call(_route(index, "발행일이 최근인 채권"), "bond_filter").params.get("order") == "issue_desc"   # 종전 어순 그대로


def test_fund_grade_count_not_name(index, con):
    plan = _route(index, "위험등급 6등급 펀드 몇 개")
    c = _call(plan, "fund_filter")
    assert plan.intent == "fund_count_filter" and c.params.get("min_risk") == 6 and "name_pattern_raw" not in c.params
    exp = con.execute("SELECT count(*) FROM fund_master WHERE TRY_CAST(drv_risk_grade AS INT) = 6").fetchone()[0]
    assert len(_rows(con, c)) == exp and exp > 0
    assert _call(_route(index, "TDF 펀드 몇 개"), "fund_filter").params.get("name_pattern_raw") == "TDF"   # 이름 조각은 종전 그대로


def test_monthly_dividend_count(index, con):
    plan = _route(index, "월배당 ETF 몇 개")
    c = _call(plan, "etp_by_dividend")
    assert plan.intent == "etp_dividend_rank" and c.params.get("min_pay_cnt") == 12 and c.params["limit"] == 2000
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                      "AND TRY_CAST(pd_dvid_pay_cnt AS DOUBLE) >= 12 AND TRY_CAST(pd_dvid_yield AS DOUBLE) > 0").fetchone()[0]
    assert len(_rows(con, c)) == exp and exp > 100
    assert _route(index, "월배당 ETF 순자산 상위 3개").intent == "etp_dividend_rank"       # 종전 그대로


def test_hidden_round18():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
