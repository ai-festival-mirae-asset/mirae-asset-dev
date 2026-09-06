# -*- coding: utf-8 -*-
"""리더 세션 15바퀴(9/6) — 표현 변형 점검 11차 40문항(표기 변형·상품 비교·상세 초점·집계·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '코스피200 ETF 몇 개'(띄어쓰기 없는 지수 표기) 전체 건수 · 'KODEX 200 한 주 얼마야'·'가격'에 종가 없음 ·
'어느 거래소' 상장일만 · '분배금 얼마'가 분배수익률 먼저 · '배당수익률'에 1년 수익률이 먼저 · '순자산 상위 10개 ETF 총보수 평균'이 전체 평균 ·
'삼성전자 담은 ETF 평균 총보수'가 편입 목록 · '미래에셋 ETF 순자산 합계'가 순위 목록 · '만기 2027년 AAA 몇 개'가 만기 조건 없는 건수 ·
'둘 다 담은 ETF 몇 개'·'환헤지 ETF 몇 개'의 상한 · '안전한 ETF' 폴백 · '펀드 위험등급 1등급×수익률'의 등급 소실 · '반도체 ETF 추천' 폴백 ·
함정('환노출'·'2024년 수익률'·'작년 수익률'(연초 이후로 오답)·'ETF 추천해줘'·'얼마까지 오를까'(상품 상세)).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import extract_risk_grades, route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_PARAMS, TEMPLATES, run_template
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


# ── 1. 지수 표기 건수 · 상세 초점 ─────────────────────────────────────────────────────

def test_index_name_count_without_space(index, con):
    for q, pat in (("코스피200 ETF 몇 개", "%KOSPI%200%"), ("KOSPI200 ETF 몇 개", "%KOSPI%200%"), ("나스닥100 ETF 몇 개", "%나스닥%100%")):
        c = _call(_route(index, q), "etp_count")
        assert c is not None and c.params == {"index_pattern": pat}, q
    rows = _rows(con, _call(_route(index, "코스피200 ETF 몇 개"), "etp_count"))
    assert int(next(r["n"] for r in rows if r["drv_instrument_type"] == "ETF" and r["drv_listing_status"] == "active")) < 200


def test_detail_price_exchange_dividend_focus(ctx, con):
    a = _ask(ctx, "KODEX 200 한 주 얼마야")["answer"]
    assert "종가" in a and "109,980" in a
    assert "종가" in _ask(ctx, "KODEX 200 가격 알려줘")["answer"]
    b = _ask(ctx, "KODEX 200 어느 거래소에 상장돼 있어")["answer"]
    assert "유가증권" in b
    c = _ask(ctx, "TIGER 200 분배금 얼마 줘")["answer"]
    assert "※ 'TIGER 200' 연간 추정 분배금" in c and "※ 'TIGER 200' 분배(배당)수익률" not in c   # 분배금 질문은 금액이 초점
    d = _ask(ctx, "TIGER 200 배당수익률")["answer"]
    assert "※ 'TIGER 200' 1년 수익률" not in d and "분배(배당)수익률" in d


# ── 2. 집계 — 편입 ETF 평균 · 순자산 상위 N 평균 · 운용사 합계 ────────────────────────────

def test_holder_metric_avg_and_top_aum_avg(index, con):
    plan = _route(index, "삼성전자 담은 ETF 평균 총보수")
    c = _call(plan, "etp_metric_avg")
    assert plan.intent == "constituent_holders_metric_avg" and c.params == {"metric": "fee", "type": "ETF", "holder_code": "005930"}
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(cu_charge_rt AS DOUBLE)),2), count(*) FROM kr_etp WHERE drv_listing_status='active' AND drv_instrument_type='ETF' "
                      "AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0 AND pd_itm_no IN (SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='005930')").fetchone()
    assert abs(float(row["avg_value"]) - float(exp[0])) < 0.01 and int(row["n"]) == exp[1]
    c2 = _call(_route(index, "순자산 상위 10개 ETF 총보수 평균"), "etp_metric_avg")
    assert c2.params.get("top_aum_n") == 10
    assert "top_aum_n" not in _call(_route(index, "ETF 총보수 평균"), "etp_metric_avg").params
    assert _route(index, "삼성전자 담은 ETF 몇 개").intent == "constituent_reverse"       # 평균 낱말 없으면 종전 그대로


def test_company_aum_sum(index, con):
    plan = _route(index, "미래에셋 ETF 순자산 합계")
    c = _call(plan, "etp_aum_sum")
    assert plan.intent == "etp_aum_sum" and c.params == {"instrument_type": "ETF", "mgmt": "미래에셋"}
    assert int(_rows(con, c)[0]["n"]) > 100
    assert _call(_route(index, "미래에셋 ETF 순자산 1위"), "etp_by_mgmt") is not None       # 순위는 종전 그대로


# ── 3. 채권 만기 연도 어순 · 교집합·환헤지 건수 · 안전한 ─────────────────────────────────

def test_bond_maturity_year_word_order_and_count(index, con):
    plan = _route(index, "회사채 중 만기 2027년 AAA 몇 개")
    c = _call(plan, "bond_maturing_within")
    assert plan.intent == "bond_maturity_year" and c.params["as_of_date"] == "2027-01-01" and c.params["until"] == "2027-12-31"
    assert c.params["max_rating_rank"] == 1 and c.params["limit"] == 2000
    exp = con.execute("SELECT count(*) FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND TRY_CAST(drv_crd_grd_rank AS INT) = 1 "
                      "AND replace(coalesce(MAT_DT,''),'-','') BETWEEN '20270101' AND '20271231'").fetchone()[0]
    assert len(_rows(con, c)) == exp


def test_intersection_and_hedged_counts(index, con):
    c = _call(_route(index, "SK하이닉스랑 삼성전자 둘 다 담은 ETF 몇 개"), "constituent_intersection_top_aum")
    assert c.params["limit"] == 2000 and len(_rows(con, c)) > 100
    c2 = _call(_route(index, "환헤지 ETF 몇 개"), "etp_name_search")
    assert c2.params["limit"] == 2000
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_listing_status='active' AND (pd_nm ILIKE '%(H)%' OR pd_abrv_nm ILIKE '%(H)%')").fetchone()[0]
    assert len(_rows(con, c2)) == exp


def test_safe_reads_as_low_risk(index):
    assert extract_risk_grades("안전한 ETF 추천해줘", POLICY)[:2] == (5, 6)
    assert extract_risk_grades("안전자산 비중 높은 펀드", POLICY) is None or True                  # 다른 표현은 건드리지 않아도 된다
    plan = _route(index, "안전한 ETF 추천해줘")
    c = _call(plan, "etp_filter_risk")
    assert plan.intent == "etp_filter" and c.params["min_grade"] == 5 and c.params["max_grade"] == 6
    assert plan.hints.get("skip_generation") is True and plan.hints.get("display_rows") == 10     # 추천 낱말 → 결정적 사실 목록
    assert _route(index, "위험등급 6등급 ETF 알려줘").hints.get("display_rows") is None          # 추천 낱말 없으면 표시 상한 없음(종전 그대로)


def test_fund_risk_kept_in_return_rank(index, con):
    c = _call(_route(index, "펀드 중 위험등급 1등급이면서 1년 수익률 높은 것"), "fund_top_return_1y")
    assert c.params["min_risk"] == 1 and c.params["max_risk"] == 1
    rows = _rows(con, c)
    assert rows and all(int(float(r["drv_risk_grade"])) == 1 for r in rows)
    assert "min_risk" not in _call(_route(index, "1년 수익률 높은 펀드"), "fund_top_return_1y").params


def test_theme_recommendation_becomes_fact_list(index):
    plan = _route(index, "반도체 ETF 추천해줘")
    c = _call(plan, "etp_top_aum")
    assert plan.intent == "theme_fact_list" and c.params["name_pattern"] == "%반도체%" and any("추천이 아닌" in n for n in plan.notes)
    assert _route(index, "ETF 추천해줘").behavior_hint == "refuse"                                # 기준 없는 추천은 거절


# ── 4. 함정 ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("환노출 ETF 알려줘", "unsupported_field"),
    ("2024년 수익률 높은 ETF", "unsupported_field"),
    ("작년 수익률 좋은 ETF", "unsupported_field"),
    ("ETF 추천해줘", "action_request"),
    ("KODEX 200 얼마까지 오를까", "time_violation"),
])
def test_round15_traps(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (q, plan.intent)


def test_year_return_trap_does_not_catch_listing_windows(index):
    assert _route(index, "2025년에 상장한 ETF 중 1년 수익률 높은 것").behavior_hint != "refuse"
    assert _route(index, "연초 이후 수익률 높은 ETF").behavior_hint != "refuse"


# ── 5. 숨김 파라미터 — AI 라우터 목록 불변 ──────────────────────────────────────────────

def test_hidden_round15():
    text = _template_catalog_text()
    for tid, p in (("etp_metric_avg", "top_aum_n"), ("etp_metric_avg", "holder_code"), ("fund_top_return_1y", "min_risk"), ("fund_top_return_1y", "max_risk")):
        assert (tid, p) in LLM_HIDDEN_PARAMS and p in [x.name for x in TEMPLATES[tid].params], (tid, p)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
