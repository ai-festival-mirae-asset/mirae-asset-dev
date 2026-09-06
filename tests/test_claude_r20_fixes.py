# -*- coding: utf-8 -*-
"""리더 세션 20바퀴(9/6 최종) — 실전 유형 재변형 25문항 점검이 찾은 공백 2건의 회귀 잠금.

찾은 것(규칙 엔진 실측): 'KODEX 200 내일 가격'(미래 시점인데 기준일 종가로 답하던 조용한 오답) ·
'미국 소형주 해외 ETF'(동사 없는 짧은 형이 폴백 — '미국 소형주에 투자하는 해외 ETF 찾아줘'는 테마 검색이었음).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)
POLICY = load_policy()
_CATALOG_SHA256_FROZEN = "a3f8e65498b70ed5264da3fcf84f5336b52cb4b6448e483b0c9bce07ebf25855"


@pytest.fixture(scope="module")
def index():
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    return build_entity_index(con)


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


@pytest.mark.parametrize("q", ["KODEX 200 내일 가격", "TIGER 200 다음 주 종가", "KODEX 200 내년 순자산 얼마"])
def test_future_price_refused(index, q):
    plan = _route(index, q)
    assert plan.intent == "time_violation" and plan.behavior_hint == "refuse", (q, plan.intent)


def test_present_price_still_answers(index):
    assert _route(index, "KODEX 200 종가 얼마야").intent == "product_detail"
    assert _route(index, "KODEX 200 순자산").intent == "product_detail"


def test_global_theme_short_form(index):
    plan = _route(index, "미국 소형주 해외 ETF")
    assert plan.intent == "theme_search"
    c = next((c for c in plan.calls if c.op == "global_etf_filter"), None)
    assert c is not None and c.params.get("name_pattern_raw") == "small cap" and c.params.get("region_pattern_raw") == "United States"
    assert _route(index, "미국 소형주에 투자하는 해외 ETF 찾아줘").intent == "theme_search"     # 종전 그대로


def test_hidden_round20():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
