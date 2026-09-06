# -*- coding: utf-8 -*-
"""리더 세션 17바퀴(9/6 최종) — 구어체 점검 25문항이 찾은 공백 2건의 회귀 잠금.

찾은 것(규칙 엔진 실측): '국내 etf 중에 제일 큰 거 하나만'(순자산 낱말 없음 → 폴백) ·
'위험등급 2등급 펀드 중 1년 수익률 높은 3개'('등급'이 상품명 조각으로 새어 등급 조건 소실 — 3등급 상품이 나가던 조용한 오답).
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


def test_biggest_without_aum_word(index, con):
    plan = _route(index, "국내 etf 중에 제일 큰 거 하나만")
    c = _call(plan, "etp_top_aum")
    assert plan.intent == "etp_ranking" and c.params == {"instrument_type": "ETF", "limit": 1}
    assert _rows(con, c)[0]["pd_abrv_nm"] == "KODEX 200"
    assert any("'큰'은 순자산총액" in n for n in plan.notes)
    assert _call(_route(index, "제일 큰 ETF 뭐야"), "etp_top_aum").params["limit"] == 5
    assert _route(index, "거래량 제일 큰 ETF").intent == "etp_metric_rank"          # 다른 기준 낱말이 있으면 종전 그대로
    assert _call(_route(index, "순자산 큰 ETF 5개"), "etp_top_aum").params["limit"] == 5


def test_fund_grade_word_not_name_fragment(index, con):
    plan = _route(index, "위험등급 2등급 펀드 중 1년 수익률 높은 3개")
    c = _call(plan, "fund_top_return_1y")
    assert plan.intent == "fund_ranking" and c.params.get("min_risk") == 2 and c.params.get("max_risk") == 2
    assert "name_pattern_raw" not in c.params and c.params["limit"] == 3
    rows = _rows(con, c)
    assert rows and all(int(float(r["drv_risk_grade"])) == 2 for r in rows)
    assert _call(_route(index, "TDF 펀드 1년 수익률 높은 순 3개"), "fund_top_return_1y").params.get("name_pattern_raw") == "TDF"   # 이름 조각은 종전 그대로


def test_hidden_round17():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
