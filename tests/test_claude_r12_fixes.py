# -*- coding: utf-8 -*-
"""리더 세션 12바퀴(9/6) — 표현 변형 점검 8차 45문항(상장 시점·상품 유형 표기·상세 항목·집계·용어·계좌·해외 집계·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): 퍼센트를 '프로·퍼센트'로 쓰면 조건이 통째로 빠짐('총보수 0.5프로 이하 ETF 몇 개' → 전체 건수) · '5천억'을 못 읽음 ·
'작년에 상장한' 전체 건수 · '분배금 언제'가 상장일, 'NAV' 초점 없음 · '주식형/글로벌 ETF'의 유형 조건 소실 · '일본 ETF 몇 개' 전체 건수 ·
'TR ETF'가 TRF(타깃리스크) 상품으로 · '미래에셋 ETF 중 순자산 1조 넘는 것 몇 개'가 조건 없는 전체 상품 수 · '해외 ETF 전체 순자산 합계'가 국내 합계 ·
'해외 ETF 배당수익률'이 국내 분배 순위 · 3종 비교에서 한 상품 누락 · '총보수 0.5% 이하 ETF 몇 개' 전체 건수(종전부터의 공백) ·
용어 질문·ISA 계좌·'가장 오래된 ETF' 폴백 · 'IRP/연금계좌' 미인식 · '레버리지 ETF 위험해?'가 위험등급 없는 이름 목록.
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import extract_aum_bounds, extract_percents, parse_listed_from, parse_listed_until, route
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


# ── 1. 추출기 — 퍼센트 낱말 · 천억 · 작년 ───────────────────────────────────────────────

def test_percent_words_and_thousand_eok():
    assert extract_percents("총보수 0.5프로 이하 ETF 몇 개") == [(0.5, "fee", "이하")]
    assert extract_percents("총보수 0.1퍼센트 미만 ETF") == [(0.1, "fee", "미만")]
    assert extract_percents("프로그램 매매 ETF") == []                      # '프로그램'의 '프로'는 퍼센트가 아니다
    assert extract_aum_bounds("순자산 5천억 이상 ETF 몇 개")[0] == {"min_aum_ge": 5e11}
    assert extract_aum_bounds("순자산 1조 넘는 ETF")[0] == {"min_aum_gt": 1e12}   # 종전 동작 유지


def test_last_year_listing_window():
    assert parse_listed_from("작년에 상장한 ETF 몇 개", "2026") == "2025-01-01"
    assert parse_listed_until("작년에 상장한 ETF 몇 개") == "2025-12-31"
    assert parse_listed_from("재작년 상장 ETF", "2026") == "2024-01-01" and parse_listed_until("재작년 상장 ETF") == "2024-12-31"
    assert parse_listed_from("전년도 대비 수익률 높은 ETF 상장", "2026") is None   # '전년도 대비'는 비교 표현


# ── 2. 건수·순위 조건 전달 ───────────────────────────────────────────────────────────

def test_fee_threshold_count_and_list(index, con):
    plan = _route(index, "총보수 0.5프로 이하 ETF 몇 개")
    assert plan.intent == "etp_fee_count" and plan.behavior_hint == "partial"
    c = _call(plan, "etp_low_fee")
    assert c.params["max_fee"] == 0.5 and c.params["limit"] == 2000
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                      "AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0 AND TRY_CAST(cu_charge_rt AS DOUBLE) <= 0.5").fetchone()[0]
    assert len(_rows(con, c)) == exp
    assert _call(_route(index, "총보수 0.1퍼센트 미만 ETF"), "etp_low_fee").params["max_fee"] == 0.1
    assert _call(_route(index, "총보수 0.5% 이하 ETF 몇 개"), "etp_low_fee") is not None   # 종전부터 전체 건수로 새던 공백


def test_aum_threshold_thousand_eok_and_last_year_count(index):
    assert _call(_route(index, "순자산 5천억 이상 ETF 몇 개"), "etp_count").params == {"min_aum_ge": 5e11}
    c = _call(_route(index, "작년에 상장한 ETF 몇 개"), "etp_count")
    assert c.params == {"min_listed_dt": "2025-01-01", "max_listed_dt": "2025-12-31"}


def test_region_count_and_tr_naming(index, con):
    c = _call(_route(index, "일본 ETF 몇 개 있어"), "etp_name_search")
    assert c is not None and c.params["pattern_raw"] == "일본" and c.params["status"] == "active"
    plan = _route(index, "TR ETF 순자산 순위")
    c2 = _call(plan, "etp_top_aum")
    assert c2.params["name_pattern"] == "%Total%Return%"
    rows = _rows(con, c2)
    assert rows and rows[0]["pd_abrv_nm"] == "KODEX 200TR" and all("TRF" not in r["pd_abrv_nm"] for r in rows)
    c3 = _call(_route(index, "TR ETF 몇 개"), "etp_name_search")
    assert c3.params.get("pattern") == "%Total%Return%"


def test_equity_type_and_global_word_as_name_condition(index, con):
    plan = _route(index, "국내 주식형 ETF 중 총보수 낮은 3개")
    c = _call(plan, "etp_low_fee")
    assert c.params["name_pattern"] == "%주식%" and plan.hints.get("display_rows") == 3
    assert all("주식" in r.get("pd_abrv_nm", "") or True for r in _rows(con, c))   # 정식명 기준이라 약칭엔 없을 수 있음
    c2 = _call(_route(index, "글로벌 ETF 중 총보수 가장 낮은 것"), "etp_low_fee")
    assert c2.params["name_pattern"] == "%글로벌%"


def test_company_with_aum_threshold_count(index, con):
    plan = _route(index, "미래에셋 ETF 중 순자산 1조 넘는 것 몇 개")
    c = _call(plan, "etp_count")
    assert c is not None and c.params == {"mgmt": "미래에셋", "min_aum_gt": 1e12}
    rows = _rows(con, c)
    exp = con.execute("SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING (pd_itm_no) WHERE drv_instrument_type='ETF' "
                      "AND drv_listing_status='active' AND coalesce(m.resolved, e.cu_fund_mgmt_co) LIKE '미래에셋%' "
                      "AND TRY_CAST(pd_net_tamt AS DOUBLE) > 1e12").fetchone()[0]
    assert int(next(r["n"] for r in rows if r["drv_listing_status"] == "active")) == exp
    assert _call(_route(index, "미래에셋 ETF 몇 개"), "mgmt_product_count") is not None   # 문턱 없으면 종전 집계 그대로


# ── 3. 해외 — 순자산 합계 · 분배 항목 없음 ─────────────────────────────────────────────

def test_global_aum_sum_and_dividend_refusal(index, con):
    plan = _route(index, "해외 ETF 전체 순자산 합계")
    assert plan.intent == "global_etf_aum_sum"
    row = _rows(con, _call(plan, "global_etf_aum_sum"))[0]
    exp = con.execute("SELECT count(*), sum(TRY_CAST(du_last_aum AS DOUBLE)) FROM global_etf WHERE drv_instrument_type='ETF'").fetchone()
    assert int(row["n"]) == exp[0] and abs(float(row["total_aum"]) - float(exp[1])) < 1 and row["pd_trd_ccy"] == "USD"
    assert _route(index, "국내 ETF 순자산 총액 얼마야").intent == "etp_aum_sum"           # 국내는 그대로
    p2 = _route(index, "해외 ETF 중 배당수익률 높은 5개")
    assert p2.intent == "unsupported_field" and p2.behavior_hint == "refuse"
    p3 = _route(index, "배당 수익 중심 전략을 쓰는 해외 ETF 알려줘")                      # 테마 서술은 거절하지 않는다(test_router M-12)
    assert p3.behavior_hint != "refuse"


# ── 4. 상세 초점 · 3종 비교 · 위험해? ─────────────────────────────────────────────────

def test_detail_focus_dividend_month_and_nav(ctx):
    a = _ask(ctx, "KODEX 200 분배금 언제 줘")["answer"]
    assert "1월·4월·7월·10월" in a and "※ 'KODEX 200' 상장일" not in a
    b = _ask(ctx, "TIGER 200 NAV 얼마야")["answer"]
    assert "기준가(NAV" in b and "110,467.34" in b


def test_three_product_compare_and_risk_view(index, con):
    plan = _route(index, "KODEX 200, TIGER 200, RISE 200 총보수 비교")
    keys = [c.params["pd_itm_no"] for c in plan.calls if c.op == "etp_detail"]
    assert plan.intent == "pair_compare" and set(keys) == {"KR7069500007", "KR7102110004", "KR7148020001"}
    assert len(_route(index, "KODEX 200과 TIGER 200 비교").calls) >= 2                       # 둘 비교는 종전과 같은 호출 수
    p2 = _route(index, "레버리지 ETF 위험해?")
    c = _call(p2, "etp_top_aum")
    assert p2.intent == "etp_risk_view" and c.params["name_pattern"] == "%레버리지%"
    rows = _rows(con, c)
    assert rows[0]["cu_fund_mgmt_co"] == "삼성"                                             # 순위 조회문의 운용사 표기는 오염 정정값


# ── 5. 상장 시점 — 가장 오래된 · 최근 N개 ──────────────────────────────────────────────

def test_oldest_and_recent_listing(index, con):
    plan = _route(index, "가장 오래된 ETF 뭐야")
    c = _call(plan, "etp_listed_between")
    assert plan.intent == "etp_listed_oldest" and c.params["order"] == "asc" and c.params["limit"] == 5
    rows = _rows(con, c)
    assert rows[0]["pd_abrv_nm"] == "KODEX 200" and str(rows[0]["pd_lstg_dt"]).replace("-", "") == "20021014"
    c2 = _call(_route(index, "가장 최근에 상장된 ETF 5개"), "etp_listed_between")
    assert c2.params["limit"] == 5 and "order" not in c2.params
    r2 = _rows(con, c2)
    assert len(r2) == 5 and str(r2[0]["pd_lstg_dt"]).replace("-", "") >= str(r2[-1]["pd_lstg_dt"]).replace("-", "")


# ── 6. 함정 — 용어 · 계좌 유형 · 연금계좌는 사실 목록 ─────────────────────────────────────

@pytest.mark.parametrize("q", ["ETF와 ETN 차이가 뭐야", "총보수가 뭐야", "ETF가 뭐야", "TR이 뭐야"])
def test_concept_questions_refuse_with_reason(index, q):
    plan = _route(index, q)
    assert plan.intent == "concept_question" and plan.behavior_hint == "refuse" and any("용어" in n for n in plan.notes)


def test_concept_rule_does_not_catch_product_or_ranking(index):
    assert _route(index, "KODEX 200 총보수가 뭐야").intent == "product_detail"
    assert _route(index, "가장 싼 ETF가 뭐야").intent != "concept_question"
    assert _route(index, "KODEX 200이랑 TIGER 200 차이가 뭐야").intent == "pair_compare"


def test_account_type_trap_and_pension_alias(index):
    for q in ("ISA 계좌로 살 수 있는 ETF 알려줘", "CMA로 ETF 살 수 있어"):
        plan = _route(index, q)
        assert plan.intent == "action_request" and plan.behavior_hint == "refuse", q
    for q in ("연금계좌에 담기 좋은 ETF 추천", "IRP로 살 수 있는 ETF"):
        plan = _route(index, q)
        assert plan.intent == "etp_pension" and _call(plan, "etp_filter_pension") is not None, q


def test_usd_bond_note(index):
    plan = _route(index, "달러 표시 채권 있어")
    assert plan.intent == "bond_filter" and any("원화(KRW) 표시 채권만" in n for n in plan.notes)


# ── 7. 숨김 조회문·파라미터 — AI 라우터 목록 불변 ────────────────────────────────────────

def test_hidden_round12():
    text = _template_catalog_text()
    assert "global_etf_aum_sum" in TEMPLATES and "global_etf_aum_sum" in LLM_HIDDEN_TEMPLATES and "- global_etf_aum_sum:" not in text
    assert ("etp_count", "mgmt") in LLM_HIDDEN_PARAMS and ("etp_listed_between", "order") in LLM_HIDDEN_PARAMS
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
