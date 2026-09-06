# -*- coding: utf-8 -*-
"""리더 세션 21바퀴(9/6 최종) — 영어·붙여쓰기·숫자·교차·함정 25문항 점검이 찾은 공백 4건의 회귀 잠금.

찾은 것(규칙 엔진 실측): '9월 1일 KODEX 200 종가'(기준일과 다른 날짜인데 기준일 종가로 답하던 조용한 오답) ·
'국고채 30년 만기 있어?'(만기 조건 무시 → 전체 목록) · '회사채 중 콜옵션 있는 거 몇 개'(조건 무시 → 전체 건수) ·
'연금저축으로 살 수 있는 ETF'(폴백).
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


@pytest.mark.parametrize("q", ["9월 1일 KODEX 200 종가", "2026년 8월 21일 TIGER 200 NAV 얼마야"])
def test_dated_price_refused(index, q):
    plan = _route(index, q)
    assert plan.intent == "time_violation" and plan.behavior_hint == "refuse" and plan.hints.get("time_violation") == "dated_price", (q, plan.intent)


def test_as_of_date_still_answers(index):
    assert _route(index, "2026년 8월 22일 KODEX 200 종가").intent == "product_detail"
    assert _route(index, "TIGER 200 4월 분배금").intent == "product_detail"            # 월만 있으면 날짜 규칙이 아니다


def test_bond_tenor_words(index):
    plan = _route(index, "국고채 30년 만기 있어?")
    assert plan.intent == "unsupported_field" and plan.behavior_hint == "refuse"
    assert _route(index, "만기 3년 이내 국공채 표면금리 높은 순 5개").intent == "bond_maturing_filter"   # 잔존만기 조건은 종전 그대로


def test_call_option_count(index, con):
    plan = _route(index, "회사채 중 콜옵션 있는 거 몇 개")
    c = _call(plan, "bond_count")
    assert c is not None and c.params.get("name_pattern") == "%(콜%" and c.params.get("bond_class") == "회사채"
    n = int(run_template(con, c.op, resolve_raw_params(c.params)).rows[0]["n"])
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND (PD_NM ILIKE '%(콜%' OR PD_ABRV_NM ILIKE '%(콜%')").fetchone()[0]
    assert n == exp and 0 < n < 12000


def test_pension_account_refused(index):
    plan = _route(index, "연금저축으로 살 수 있는 ETF")
    assert plan.intent == "action_request" and plan.behavior_hint == "refuse"
    assert _route(index, "연금저축 펀드 위험등급 낮은 순 3개").intent == "fund_filter"       # 상품 유형 표현은 종전 그대로


def test_hidden_round21():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
