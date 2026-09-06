# -*- coding: utf-8 -*-
"""리더 세션 19바퀴(9/6 최종) — 오타·수량·구어체 25문항 점검이 찾은 공백 5건의 회귀 잠금.

찾은 것(규칙 엔진 실측): 'ETF 상위 10개'·'ETF 상위 열 개'(기준 낱말 없음 → 폴백) · 'etf 3종만 순자산순으로'('종' 단위 미인식 → 5건) ·
'삼성전자랑 sk하이닉스 둘 다 든 etf 상위 3'('든' 동사 → 폴백) · 'ETN 중 총보수 낮은 3개'(ETN 총보수 값이 원천에 없는데 ETF 목록으로 답하던 조용한 오답) ·
'TIGER 200 vs KODEX 200 뭐가 더 싸'(총보수 초점 없음).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext
from engine.policy import load_policy
from engine.router import extract_top_n, route
from engine.router_llm import _template_catalog_text
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


@pytest.fixture(scope="module")
def ctx(con, index):
    return RuntimeContext(con=con, index=index)


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


def _call(plan, op):
    return next((c for c in plan.calls if c.op == op), None)


def _ask(ctx, q):
    out = answer_question(q, ctx, today=TODAY)
    return out if isinstance(out, dict) else serialize_answer(out)


def test_top_n_units_and_numerals():
    assert extract_top_n("etf 3종만 순자산순으로") == 3
    assert extract_top_n("ETF 상위 열 개") == 10
    assert extract_top_n("종합채권 ETF 몇 개") is None                 # '종합'은 단위가 아니다
    assert extract_top_n("3종목 알려줘") == 3


def test_top_without_metric_defaults_to_aum(index):
    for q, n in (("ETF 상위 10개", 10), ("ETF 상위 열 개", 10), ("etf 3종만 순자산순으로", 3)):
        plan = _route(index, q)
        c = _call(plan, "etp_top_aum")
        assert plan.intent == "etp_ranking" and c.params == {"instrument_type": "ETF", "limit": n}, q
    assert any("'상위'는 순자산총액" in n for n in _route(index, "ETF 상위 10개").notes)
    assert _route(index, "수익률 상위 10개 ETF").intent != "etp_ranking" or _call(_route(index, "수익률 상위 10개 ETF"), "etp_top_return") is not None


def test_intersection_colloquial_verb(index):
    plan = _route(index, "삼성전자랑 sk하이닉스 둘 다 든 etf 상위 3")
    c = _call(plan, "constituent_intersection_top_aum")
    assert plan.intent == "constituent_intersection_top_aum" and c.params["code_a"] == "005930" and c.params["code_b"] == "000660"


def test_etn_fee_refused_with_reason(index, con):
    assert con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETN' AND drv_listing_status='active' "
                       "AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0").fetchone()[0] == 0
    plan = _route(index, "ETN 중 총보수 낮은 3개")
    assert plan.intent == "unsupported_field" and plan.behavior_hint == "refuse" and any("ETN 의 총보수" in n for n in plan.notes)
    assert _route(index, "ETF 총보수 낮은 3개").intent == "etp_fee_filter"          # ETF 는 종전 그대로


def test_cheaper_pair_focuses_fee(ctx):
    a = _ask(ctx, "TIGER 200 vs KODEX 200 뭐가 더 싸")["answer"]
    assert "총보수" in a


def test_hidden_round19():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
