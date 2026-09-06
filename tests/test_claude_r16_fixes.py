# -*- coding: utf-8 -*-
"""리더 세션 16바퀴(9/6) — 표현 변형 점검 12차 40문항(ETN·인버스, 채권 금리·발행일, 펀드 설정액·클래스, 지역, 상세, 집계, 함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '순자산 1000억 이상 펀드 몇 개'의 '이상'이 상품명 조건으로 새어 0건 · '설정액 1000억 이상 펀드'가 조건 없이 ·
'미국 ETF 중 보수 0.1% 이하'의 지역 소실 · '코스닥150 레버리지 ETF'가 '레버리지'만으로 검색(지수 소실) · '코스피200 ETF 뭐 있어' 폴백 ·
'코스피200 추종 ETF 알려줘'가 상품명 표기 2건만(KODEX 200 누락) · 'ETF 순자산 평균' 폴백 · 'KODEX 200 수수료 얼마' 초점 없음 ·
함정('지금 사도 돼?'(상품 상세) · 'ETF랑 펀드 중 뭐가 나아'(폴백) · '총보수 중앙값'(폴백) · '클래스 A 펀드 총보수 평균'(엉뚱한 정렬 목록)).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, avg_sentence, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import LLM_HIDDEN_ENUM_VALUES, TEMPLATES, run_template
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


# ── 1. 펀드 건수 — '이상' 오염 · 설정액 ─────────────────────────────────────────────────

def test_fund_aum_count_threshold_words_not_name(index, con):
    exp = con.execute("SELECT count(*) FROM fund_master WHERE TRY_CAST(fd_nast_suma AS DOUBLE) >= 100000000000").fetchone()[0]
    for q in ("순자산 1000억 이상 펀드 몇 개", "설정액 1000억 이상 펀드 몇 개"):
        plan = _route(index, q)
        c = _call(plan, "fund_filter")
        assert plan.intent == "fund_count_filter" and c.params.get("min_aum") == 1e11 and "name_pattern_raw" not in c.params, q
        assert len(_rows(con, c)) == exp
    assert any("설정액" in n for n in _route(index, "설정액 1000억 이상 펀드 몇 개").notes)


# ── 2. 보수 필터 지역 · 지수+테마 목록 · 지수 추종 목록 ────────────────────────────────────

def test_fee_filter_keeps_region_theme(index):
    c = _call(_route(index, "미국 ETF 중 보수 0.1% 이하"), "etp_low_fee")
    assert c is not None and c.params.get("name_pattern") == "%미국%" and c.params["max_fee"] == 0.1
    assert "name_pattern" not in _call(_route(index, "ETF 중 보수 0.1% 이하"), "etp_low_fee").params


def test_index_theme_list(index, con):
    plan = _route(index, "코스닥150 레버리지 ETF")
    c = _call(plan, "etp_top_aum")
    assert plan.intent == "index_theme_list" and c.params["index_pattern"] == "%KOSDAQ%150%" and c.params["name_pattern"] == "%레버리지%"
    names = [r["pd_abrv_nm"] for r in _rows(con, c)]
    assert names and all("레버리지" in n for n in names) and "KODEX 코스닥150레버리지" in names
    plan2 = _route(index, "코스피200 ETF 뭐 있어")
    c2 = _call(plan2, "etp_top_aum")
    assert plan2.intent == "index_theme_list" and c2.params["index_pattern"] == "%KOSPI%200%" and "name_pattern" not in c2.params
    assert _rows(con, c2)[0]["pd_abrv_nm"] == "KODEX 200"
    assert _route(index, "코스피200 ETF 몇 개").intent == "index_products_count"           # 건수는 종전 그대로


def test_index_tracking_list_uses_base_index(index, con):
    plan = _route(index, "코스피200 추종 ETF 알려줘")
    c = _call(plan, "etp_top_aum")
    assert plan.intent == "index_products" and c.params["index_pattern"] == "%KOSPI%200%" and c.params["instrument_type"] == "ETF"
    assert _rows(con, c)[0]["pd_abrv_nm"] == "KODEX 200"
    assert _call(_route(index, "S&P500 추종 국내 ETF 뭐 있어"), "etp_top_aum").params["index_pattern"] == "%S&P%500%"
    # 인버스 동반 조회(v1 M-18)는 종전 경로 유지
    assert _call(_route(index, "코스피200 추종 ETF랑 그 인버스 상품을 같이 알려줘"), "etp_name_search") is not None


# ── 3. 순자산 평균(숨김 metric 값 aum) · 수수료 초점 ──────────────────────────────────────

def test_aum_average(index, con):
    plan = _route(index, "ETF 순자산 평균")
    c = _call(plan, "etp_metric_avg")
    assert plan.intent == "etp_metric_avg" and c.params == {"metric": "aum", "type": "ETF"}
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(pd_net_tamt AS DOUBLE)),2), count(*) FROM kr_etp WHERE drv_listing_status='active' "
                      "AND drv_instrument_type='ETF' AND coalesce(TRY_CAST(pd_net_tamt AS DOUBLE),0) <> 0").fetchone()
    assert abs(float(row["avg_value"]) - float(exp[0])) < 1 and int(row["n"]) == exp[1]
    assert "억원" in avg_sentence("etp_metric_avg", [row], ("순자산총액", "KRW"))
    c2 = _call(_route(index, "삼성자산운용 ETF 순자산 평균"), "etp_metric_avg")
    assert c2.params.get("mgmt") == "삼성" and c2.params["metric"] == "aum"
    assert ("etp_metric_avg", "metric") in LLM_HIDDEN_ENUM_VALUES and "aum" in LLM_HIDDEN_ENUM_VALUES[("etp_metric_avg", "metric")]
    assert "aum" in TEMPLATES["etp_metric_avg"].params[0].enum


def test_fee_word_focus(ctx):
    a = _ask(ctx, "KODEX 200 수수료 얼마")["answer"]
    assert "※ 'KODEX 200' 총보수(%)" in a


# ── 4. 함정 ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent,term", [
    ("KODEX 200 지금 사도 돼?", "action_request", "매수·투자 판단"),
    ("ETF랑 펀드 중 뭐가 나아", "action_request", "유형 간 우열"),
    ("ETF 총보수 중앙값", "unsupported_field", "중앙값·표준편차"),
    ("클래스 A 펀드 총보수 평균", "unsupported_field", "펀드 보수 평균"),
])
def test_round16_traps(index, q, intent, term):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (q, plan.intent)
    assert any(term in n for n in plan.notes)


def test_round16_traps_do_not_overreach(index):
    assert _route(index, "KODEX 200과 TIGER 200 중 뭐가 나아").intent == "pair_compare"         # 두 상품 사실 비교는 그대로
    assert _route(index, "ETF 총보수 평균").behavior_hint != "refuse"
    assert _route(index, "분산투자 ETF 알려줘").intent != "unsupported_field"


# ── 5. AI 라우터 목록 불변 ────────────────────────────────────────────────────────────

def test_hidden_round16():
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
