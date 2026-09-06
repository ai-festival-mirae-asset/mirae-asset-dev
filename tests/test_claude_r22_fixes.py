# -*- coding: utf-8 -*-
"""리더 세션 22바퀴(9/6 최종) — 짧은 실전형 20문항 점검이 찾은 공백 2건의 회귀 잠금.

찾은 것(규칙 엔진 실측): 'ETF 순자산 순위 1위부터 3위까지'('1위'만 읽어 1건) · 'ETF 추천 좀'(기준 없는 추천인데 폴백).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.policy import load_policy
from engine.router import extract_top_n, route
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


def test_rank_range_reads_upper_bound(index):
    assert extract_top_n("ETF 순자산 순위 1위부터 3위까지") == 3
    assert extract_top_n("순자산 1위 ETF") == 1
    plan = _route(index, "ETF 순자산 순위 1위부터 3위까지")
    c = next(c for c in plan.calls if c.op == "etp_top_aum")
    assert plan.intent == "etp_ranking" and c.params["limit"] == 3


def test_bare_recommendation_with_trailing_word(index):
    for q in ("ETF 추천 좀", "펀드 추천 부탁해", "ETF 추천해줘"):
        plan = _route(index, q)
        assert plan.intent == "action_request" and plan.behavior_hint == "refuse", q
    assert _route(index, "반도체 ETF 추천해줘").intent == "theme_fact_list"          # 기준이 있으면 사실 목록(종전 그대로)


def test_hidden_round22():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
