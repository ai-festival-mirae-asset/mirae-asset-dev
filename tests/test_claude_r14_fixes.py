# -*- coding: utf-8 -*-
"""리더 세션 14바퀴(9/6) — 표현 변형 점검 10차 40문항(펀드·ETN·채권·구성종목·해외·구어체 순위·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '삼성증권 ETN 몇 개'가 삼성증권 '종목'을 담은 ETF 로 · 'KODEX 200 상위 10종목 비중 합계'가 KODEX 200 을 담은 ETF 로 ·
'TIGER 200과 KODEX 200 공통 종목'이 상품 상세 둘 · '듀레이션 5년 이상 채권 몇 개'가 조건 없는 전체 건수(노트만 조건) ·
'현대차 비중 5% 이상 ETF 순자산 큰 순'이 비중 순 · '미국 ETF 중 총보수 0.1% 이하 몇 개'·'채권 ETF 중 순자산 1조 넘는 것'의 테마 조건 소실 ·
'펀드 총보수 1% 넘는 것 몇 개' 전체 건수 · '제일 싼 ETF' 폴백 · '요즘 뜨는/인기 있는 ETF' 사유 없는 폴백 거절.
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_PARAMS, LLM_HIDDEN_TEMPLATES, TEMPLATES, run_template
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


def _rows(con, c):
    return run_template(con, c.op, resolve_raw_params(c.params)).rows


def _ask(ctx, q):
    out = answer_question(q, ctx, today=TODAY)
    return out if isinstance(out, dict) else serialize_answer(out)


# ── 1. 증권사 + ETN = 발행사 범위 ─────────────────────────────────────────────────────

def test_securities_firm_etn_is_issuer_scope(index, con):
    plan = _route(index, "삼성증권 ETN 몇 개")
    c = _call(plan, "mgmt_product_count")
    assert plan.intent == "company_product_count" and c.params == {"mgmt": "삼성증권"}
    rows = _rows(con, c)
    assert all(r["drv_instrument_type"] == "ETN" for r in rows) and int(next(r["n"] for r in rows if r["drv_listing_status"] == "active")) > 0
    p2 = _route(index, "삼성증권 ETN 알려줘")
    c2 = _call(p2, "etp_by_mgmt")
    assert p2.intent == "company_products" and c2.params["instrument_type"] == "ETN"
    r2 = _rows(con, c2)
    assert r2 and all(str(r["mgmt"]).startswith("삼성증권") for r in r2)
    assert _route(index, "삼성증권 담은 ETF").intent == "constituent_reverse"          # 편입 동사가 있으면 종목 그대로


# ── 2. 상품 상위 N 종목 비중 합계 · 두 상품 공통 종목 ───────────────────────────────────

def test_top_n_weight_sum(index, con, ctx):
    plan = _route(index, "KODEX 200 상위 10종목 비중 합계")
    assert plan.intent == "product_constituents"
    c = _call(plan, "constituent_top_weight_sum")
    assert c.params == {"etf_id": "KR7069500007", "top_n": 10}
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(sum(w), 2) FROM (SELECT TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) AS w FROM etf_constituent "
                      "WHERE etf_isin='KR7069500007' ORDER BY w DESC NULLS LAST LIMIT 10)").fetchone()[0]
    assert int(row["n"]) == 10 and abs(float(row["weight_sum"]) - float(exp)) < 0.01
    a = _ask(ctx, "KODEX 200 상위 10종목 비중 합계")["answer"]
    assert "비중 합계" in a and "삼성전자" in a and f"{float(exp):g}%" in a
    assert _route(index, "KODEX 200 담은 ETF").intent == "constituent_reverse"          # 편입 동사가 있으면 종목으로


def test_pair_common_constituents(index, con):
    plan = _route(index, "TIGER 200과 KODEX 200 공통 종목")
    c = _call(plan, "constituent_pair_common")
    assert plan.intent == "constituent_pair_common" and {c.params["etf_a"], c.params["etf_b"]} == {"KR7102110004", "KR7069500007"}
    rows = _rows(con, c)
    assert rows and rows[0]["COMPST_ISU_NM"] == "삼성전자" and float(rows[0]["weight_a"]) > 30
    c2 = _call(_route(index, "TIGER 200과 KODEX 200 공통 종목 몇 개"), "constituent_pair_common")
    exp = con.execute("SELECT count(*) FROM etf_constituent c1 JOIN etf_constituent c2 ON c1.COMPST_ISU_CD=c2.COMPST_ISU_CD "
                      "WHERE c1.etf_isin='KR7102110004' AND c2.etf_isin='KR7069500007' AND trim(coalesce(c1.COMPST_ISU_CD,'')) <> ''").fetchone()[0]
    assert c2.params["limit"] == 2000 and len(_rows(con, c2)) == exp
    assert _route(index, "KODEX 200과 TIGER 200 비교").intent == "pair_compare"           # 비교는 그대로


# ── 3. 조건 전달 — 듀레이션 건수 · 비중 문턱 순자산 정렬 · 테마 조건 ───────────────────────

def test_bond_duration_count(index, con):
    c = _call(_route(index, "듀레이션 5년 이상 채권 몇 개"), "bond_count")
    assert c is not None and "min_dur" in c.params
    n = int(_rows(con, c)[0]["n"])
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE TRY_CAST(DUR AS DOUBLE) >= 5").fetchone()[0]
    assert n == exp and n < 20000


def test_weight_threshold_aum_order(index, con):
    c = _call(_route(index, "현대차 비중 5% 이상 ETF 순자산 큰 순"), "constituent_weight_above")
    assert c.params["order"] == "aum"
    rows = _rows(con, c)
    aums = [float(r["pd_net_tamt"] or 0) for r in rows]
    assert aums == sorted(aums, reverse=True) and all(float(r["weight_pct"]) > 5 for r in rows)
    assert "order" not in _call(_route(index, "현대차 비중 5% 이상 ETF"), "constituent_weight_above").params


def test_theme_kept_in_fee_count_and_aum_list(index):
    c = _call(_route(index, "미국 ETF 중 총보수 0.1% 이하 몇 개"), "etp_low_fee")
    assert c.params["name_pattern"] == "%미국%" and c.params["max_fee"] == 0.1
    c2 = _call(_route(index, "채권 ETF 중 순자산 1조 넘는 것"), "etp_top_aum")
    assert c2.params["name_pattern"] == "%채권%" and c2.params["min_aum_gt"] == 1e12
    assert "name_pattern" not in _call(_route(index, "순자산 1조 넘는 ETF"), "etp_top_aum").params


def test_fund_fee_threshold_count(index, con):
    plan = _route(index, "펀드 총보수 1% 넘는 것 몇 개")
    c = _call(plan, "fund_by_fee")
    assert plan.intent == "fund_fee_count" and c.params["min_total_fee"] == 1.0 and c.params["limit"] == 30000
    rows = _rows(con, c)
    assert rows and all(float(r["total_fee_pct"]) >= 1.0 for r in rows)      # 표시값은 소수 4자리 반올림(조건은 원값 > 1)
    exp = con.execute("SELECT count(*) FROM fund_master WHERE (coalesce(TRY_CAST(sale_co_rwrd_r AS DOUBLE), 0) + coalesce(TRY_CAST(or_co_rwrd_r AS DOUBLE), 0) "
                      "+ coalesce(TRY_CAST(trusc_rwrd_r AS DOUBLE), 0) + coalesce(TRY_CAST(ofwk_trus_rwrd_r AS DOUBLE), 0)) > 1").fetchone()[0]
    assert len(rows) == exp
    assert _route(index, "펀드 몇 개").intent == "fund_count"                                # 조건 없는 건수는 종전 그대로


# ── 4. 구어체 순위 · 함정 ────────────────────────────────────────────────────────────

def test_cheapest_etf_reads_as_fee(index):
    plan = _route(index, "제일 싼 ETF 뭐야")
    assert plan.intent == "etp_fee_filter" and any("총보수(비용)가 낮은 순으로 해석" in n for n in plan.notes)


@pytest.mark.parametrize("q", ["요즘 뜨는 ETF 뭐야", "인기 있는 ETF 추천해줘", "핫한 ETF 알려줘"])
def test_popularity_refuse(index, q):
    plan = _route(index, q)
    assert plan.intent == "unsupported_field" and plan.behavior_hint == "refuse" and any("인기·유행" in n for n in plan.notes)


# ── 5. 숨김 조회문·파라미터 — AI 라우터 목록 불변 ────────────────────────────────────────

def test_hidden_round14():
    text = _template_catalog_text()
    for tid in ("constituent_top_weight_sum", "constituent_pair_common"):
        assert tid in TEMPLATES and tid in LLM_HIDDEN_TEMPLATES and f"- {tid}:" not in text
    for tid, p in (("fund_by_fee", "min_total_fee"), ("bond_count", "min_dur"), ("bond_count", "max_dur"), ("constituent_weight_above", "order")):
        assert (tid, p) in LLM_HIDDEN_PARAMS and p in [x.name for x in TEMPLATES[tid].params], (tid, p)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
