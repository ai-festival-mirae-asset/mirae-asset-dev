# -*- coding: utf-8 -*-
"""리더 세션 7바퀴(9/6) — 표현 변형 점검 3차 60문항(숫자·수사·지수·운용사·교차·해외·펀드·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): '코스피200 추종 ETF 몇 개'가 상품명 검색 2건(KODEX 200 은 이름에 지수가 없음) · 'S&P500 따라가는 ETF 중
순자산 제일 큰 거'가 정렬 없는 이름 검색 · '키움 ETF 순자산 1위'가 전체 1위(두 글자 운용사 미인식) · '미래에셋 ETF 중 배당수익률/수익률/
괴리율/총보수 …'가 운용사 조건 없는 전체 순위 · 'QQQ 정보'·'SPY 순자산'·'VOO 총보수' 폴백(해외 상세 조회문 없음) ·
'KODEX 200에서 삼성전자 비중'이 상품 상세(상품 자신도 종목이라 교집합으로도 새던 것) · '구성종목 몇 개'가 상위 10 나열 ·
'해외 레버리지 ETF 몇 개'가 전체 건수 · 'MMF 몇 개'·'혼합형 펀드'(0건)·'미래에셋 펀드 중 위험등급 1등급'(등급 누락)·'TDF 펀드 순자산/수익률' 폴백 ·
'펀드 클래스 A와 C 차이' 폴백 · '금 ETF' 폴백 거절 · 함정 변형(어제 NAV·오늘 올랐어·손절·가입 방법·최고 추천·수익 보장·예금 금리).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, serialize_answer
from engine.channels import RuntimeContext, resolve_raw_params
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text, build_router_tool
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


def _name(r):
    return next((str(r[k]) for k in ("pd_abrv_nm", "etf_abrv_nm", "pd_nm", "itm_abrv_nm", "PD_NM") if r.get(k)), "")


def _ask(ctx, q):
    out = answer_question(q, ctx, today=TODAY)
    return out if isinstance(out, dict) else serialize_answer(out)


# ── 1. 지수 추종 — 건수·순자산 순위는 기초지수 열로 ─────────────────────────────────────────

def test_index_tracking_count_uses_base_index(index, con):
    plan = _route(index, "코스피200 추종 ETF 몇 개 있어")
    assert plan.intent == "index_products_count"
    c = _call(plan, "etp_count")
    assert c.params == {"index_pattern": "%KOSPI%200%"}
    rows = _rows(con, c)
    n_etf_active = next(int(r["n"]) for r in rows if r["drv_instrument_type"] == "ETF" and r["drv_listing_status"] == "active")
    exp = con.execute("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                      "AND (coalesce(cu_base_index, ref_base_index) ILIKE '%KOSPI%200%' OR pd_nm ILIKE '%KOSPI%200%' "
                      "OR pd_abrv_nm ILIKE '%KOSPI%200%')").fetchone()[0]
    assert n_etf_active == exp and exp > 50                 # 종전 상품명 검색은 2건


@pytest.mark.parametrize("q,top", [
    ("S&P500 따라가는 ETF 중 순자산 제일 큰 거", "TIGER 미국S&P500"),
    ("나스닥100 추종 ETF 순자산 큰 3개", "TIGER 미국나스닥100"),
])
def test_index_tracking_aum_rank(index, con, q, top):
    plan = _route(index, q)
    assert plan.intent == "index_products_rank" and plan.hints.get("skip_generation") is True
    c = _call(plan, "etp_top_aum")
    assert c.params["index_pattern"].startswith("%") and c.params["instrument_type"] == "ETF"
    assert _name(_rows(con, c)[0]) == top


def test_index_tracking_list_unchanged(index):
    plan = _route(index, "코스닥150을 따라가는 ETF랑 그 인버스 상품을 같이 알려줘")   # v1 M-18
    assert plan.intent == "index_products" and _call(plan, "etp_name_search") is not None


# ── 2. 운용사 범위 — 두 글자 운용사 인식·지표별 순위에 mgmt ──────────────────────────────

def test_two_char_company_before_product_word_is_grounded(index, con):
    plan = _route(index, "키움 ETF 순자산 1위")
    assert plan.intent == "company_products_ranked"
    c = _call(plan, "etp_by_mgmt")
    assert c.params["mgmt"] == "키움" and c.params["limit"] == 1
    assert _name(_rows(con, c)[0]) == "KIWOOM 200TR"


def test_two_char_company_count(index, con):
    plan = _route(index, "신한 ETF 몇 개")
    assert plan.intent == "company_product_count"
    assert _call(plan, "mgmt_product_count").params["mgmt"] == "신한"


def test_two_char_company_without_product_word_stays_unmatched(index):
    # '삼성전자 목표주가'류·일반 문장에서 두 글자 회사가 튀어나오지 않는다(종전 정책 유지)
    plan = _route(index, "삼성 갤럭시 신제품 언제 나와")
    assert _call(plan, "etp_by_mgmt") is None


def test_company_scoped_dividend_rank(index, con):
    plan = _route(index, "미래에셋 ETF 중 배당수익률 높은 순 3개")
    c = _call(plan, "etp_by_dividend")
    assert c.params["mgmt"] == "미래에셋" and c.params["metric"] == "yield"
    rows = _rows(con, c)
    exp = con.execute("SELECT e.pd_abrv_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING (pd_itm_no) "
                      "WHERE e.drv_instrument_type='ETF' AND e.drv_listing_status='active' "
                      "AND coalesce(m.resolved, e.cu_fund_mgmt_co) LIKE '미래에셋%' AND coalesce(TRY_CAST(e.pd_dvid_yield AS DOUBLE),0)<>0 "
                      "ORDER BY TRY_CAST(e.pd_dvid_yield AS DOUBLE) DESC, e.pd_itm_no LIMIT 1").fetchone()[0]
    assert _name(rows[0]) == exp and all(_name(r).startswith("TIGER") for r in rows[:3])


def test_company_scoped_return_rank_with_count_word(index, con):
    plan = _route(index, "미래에셋 ETF 중 1년 수익률 높은 5개")   # TOP_WORDS 없이 '높은 5개'
    c = _call(plan, "etp_top_return")
    assert c is not None and c.params["mgmt"] == "미래에셋" and c.params["metric"] == "1y" and c.params["limit"] == 5
    assert all(_name(r).startswith("TIGER") for r in _rows(con, c))


def test_company_scoped_metric_rank(index, con):
    plan = _route(index, "삼성자산운용 ETF 중 괴리율 가장 큰 것")
    c = _call(plan, "etp_metric_rank")
    assert c.params["mgmt"] == "삼성" and c.params["metric"] == "diff"
    assert all(_name(r).startswith("KODEX") for r in _rows(con, c))


def test_company_scoped_low_fee(index, con):
    plan = _route(index, "미래에셋 ETF 중 총보수 낮은 것")
    c = _call(plan, "etp_low_fee")
    assert c.params["mgmt"] == "미래에셋"
    assert all(_name(r).startswith("TIGER") for r in _rows(con, c))


def test_brand_count_still_name_count(index):
    plan = _route(index, "TIGER ETF 총 몇 종목?")                # r5 정책 유지 — 8.5 인접 트리거는 건수·브랜드 제외
    assert plan.intent == "etp_name_count" and _call(plan, "mgmt_product_count") is None


# ── 3. 해외 티커 상세(숨김 조회문) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("q,key,frag", [
    ("QQQ 정보 알려줘", "QQQ.O", "0.18%"),
    ("SPY 순자산 얼마야", "SPY", "8,201억 USD"),
    ("VOO 총보수", "VOO", "0.02%"),
])
def test_global_ticker_detail(index, ctx, q, key, frag):
    plan = _route(index, q)
    assert plan.intent == "global_product_detail"
    assert _call(plan, "global_etf_detail").params == {"pd_itm_no": key}
    ser = _ask(ctx, q)
    assert frag in ser["answer"] and "PREF02N001" in ser["answer"]


def test_global_ticker_risk_grade_still_refused(index):
    plan = _route(index, "QQQ 위험등급 알려줘")               # v1 T-13 가족
    assert plan.behavior_hint == "refuse"


# ── 4. 상품 × 종목 편입 비중 · 구성종목 수 ────────────────────────────────────────────────

@pytest.mark.parametrize("q,etf,code", [
    ("KODEX 200에서 삼성전자 비중 얼마야", "KR7069500007", "005930"),
    ("TIGER 200에 SK하이닉스 몇 % 들어있어", "KR7102110004", "000660"),
    ("TIGER 200에 삼성전자 있어?", "KR7102110004", "005930"),
])
def test_constituent_pair_weight(index, con, q, etf, code):
    plan = _route(index, q)
    assert plan.intent == "constituent_pair_weight", plan.intent
    c = _call(plan, "constituent_pair_weight")
    assert c.params == {"etf_id": etf, "code": code}
    rows = _rows(con, c)
    exp = con.execute("SELECT TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) FROM etf_constituent WHERE etf_isin=? AND COMPST_ISU_CD=?",
                      [etf, code]).fetchone()[0]
    assert len(rows) == 1 and float(rows[0]["weight_pct"]) == float(exp)


def test_product_own_constituent_code_is_not_a_pair(index):
    # KODEX 200·TIGER 200 은 다른 ETF 의 구성종목이기도 하다 — 자기 자신을 종목으로 읽어 쌍 규칙이 가로채면 안 된다
    assert _route(index, "TIGER 미국S&P500 총보수 얼마야").intent == "product_detail"
    assert _route(index, "TIGER 200 구성종목 중 비중 상위 3개가 뭐야").intent == "product_constituents"   # v1 M-25
    assert _route(index, "KODEX 2차전지산업 배당수익률이 얼마야?").intent == "product_detail"
    plan = _route(index, "KODEX 200 담은 ETF 있어")           # 상품 자신이 유일한 종목이면 종전대로 역질의
    assert plan.intent == "constituent_reverse" and _call(plan, "constituent_holders").params["code"] == "069500"


def test_two_real_constituents_still_intersect(index):
    plan = _route(index, "삼성전자랑 SK하이닉스 동시에 편입한 ETF 순자산 큰 3개")
    assert plan.intent == "constituent_intersection_top_aum"


def test_constituent_count_uses_result_count(index, con, ctx):
    plan = _route(index, "KODEX 반도체 구성종목 몇 개")
    assert plan.intent == "product_constituents" and plan.hints.get("skip_generation") is True
    c = _call(plan, "constituent_top_weights")
    assert c.params["limit"] >= 2000
    n = con.execute("SELECT count(*) FROM etf_constituent WHERE etf_isin='KR7091160002'").fetchone()[0]
    assert len(_rows(con, c)) == n
    assert f"결과 {n:,}건" in _ask(ctx, "KODEX 반도체 구성종목 몇 개")["answer"]


def test_missing_weights_are_explained(ctx):
    ser = _ask(ctx, "TIGER 미국S&P500 구성종목 상위 5개")
    assert "비중 값이 없어" in ser["answer"]


# ── 5. 동사 없는 종목 역질의 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,code", [("삼성전자 ETF 알려줘", "005930"), ("현대차 ETF 몇 개", "005380")])
def test_verbless_constituent_reverse(index, q, code):
    plan = _route(index, q)
    assert plan.intent == "constituent_reverse", plan.intent
    assert _call(plan, "constituent_holders").params["code"] == code


# ── 6. 해외 레버리지 ───────────────────────────────────────────────────────────────────

def test_global_leveraged_count_and_list(index, con):
    plan = _route(index, "해외 레버리지 ETF 몇 개")
    c = _call(plan, "global_etf_count")
    assert c.params == {"leveraged_only": "Y"}
    rows = _rows(con, c)
    exp = con.execute("SELECT count(*) FROM global_etf WHERE abs(TRY_CAST(cu_lev_fector AS DOUBLE)) > 1 "
                      "AND drv_instrument_type='ETF'").fetchone()[0]
    assert next(int(r["n"]) for r in rows if r["drv_instrument_type"] == "ETF") == exp and exp < 2000   # 종전 전체 5,972
    plan2 = _route(index, "해외 레버리지 ETF 알려줘")
    assert _call(plan2, "global_etf_filter").params["leveraged_only"] == "Y"


# ── 7. 펀드 — MMF·혼합형·운용사×등급·이름×순자산/수익률 ───────────────────────────────────

def test_mmf_is_fund_domain(index, con):
    plan = _route(index, "MMF 몇 개 있어")
    assert plan.intent == "fund_count_filter"
    c = _call(plan, "fund_filter")
    assert c.params["attr_pattern_raw"] == "MMF"
    assert len(_rows(con, c)) == con.execute("SELECT count(*) FROM fund_master WHERE or_attr_desc ILIKE '%MMF%'").fetchone()[0]


def test_mixed_type_maps_to_partial_pattern(index, con):
    plan = _route(index, "혼합형 펀드 중 판매중인 것 몇 개")
    c = _call(plan, "fund_filter")
    assert c.params["attr_pattern_raw"] == "혼합" and c.params["on_sale_only"] == "Y"
    assert len(_rows(con, c)) > 1000                        # 종전 '혼합형' 정확 표기는 0건


def test_company_fund_count_keeps_risk(index, con):
    plan = _route(index, "미래에셋 펀드 중 위험등급 1등급 몇 개")
    c = _call(plan, "fund_filter")
    assert c.params["name_pattern_raw"] == "미래에셋" and c.params["min_risk"] == 1 and c.params["max_risk"] == 1
    exp = con.execute("SELECT count(*) FROM fund_master WHERE (itm_nm ILIKE '%미래에셋%' OR itm_abrv_nm ILIKE '%미래에셋%') "
                      "AND TRY_CAST(drv_risk_grade AS INT)=1").fetchone()[0]
    assert len(_rows(con, c)) == exp


def test_fund_name_with_aum_rank(index, con):
    plan = _route(index, "TDF 펀드 순자산 큰 3개")
    assert plan.intent == "fund_name_search"
    c = _call(plan, "fund_filter")
    assert c.params["name_pattern_raw"] == "TDF" and c.params["order"] == "aum" and c.params["limit"] == 3
    exp = con.execute("SELECT itm_nm FROM fund_master WHERE itm_nm ILIKE '%TDF%' OR itm_abrv_nm ILIKE '%TDF%' "
                      "ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST, itm_no LIMIT 1").fetchone()[0]
    assert _rows(con, c)[0]["itm_nm"] == exp


def test_fund_name_with_return_rank(index, con):
    plan = _route(index, "TDF 펀드 1년 수익률 높은 순 3개")
    assert plan.intent == "fund_name_return_rank"
    c = _call(plan, "fund_top_return_1y")
    assert c.params["name_pattern_raw"] == "TDF" and c.params["limit"] == 3
    assert all("TDF" in (r["itm_nm"] + str(r.get("itm_abrv_nm"))) for r in _rows(con, c))


def test_fund_fragment_detail_not_used_for_rankings(index):
    assert _route(index, "국민성장펀드 위험등급은?").intent == "fund_detail_by_fragment"   # 6바퀴 유지
    assert _route(index, "TDF 펀드 순자산 큰 3개").intent != "fund_detail_by_fragment"


def test_class_dictionary_word_order(index):
    plan = _route(index, "펀드 클래스 A와 C 차이가 뭐야")
    assert plan.intent == "fund_class_compare"
    assert _call(plan, "fund_class_dictionary").params["classes"] == ["A", "C"]


# ── 8. 금(골드) 상품 ───────────────────────────────────────────────────────────────────

def test_gold_search_and_rank(index, con):
    plan = _route(index, "금 ETF 있어")
    assert plan.intent == "gold_search"
    pats = {c.params["pattern_raw"] for c in plan.calls if c.op == "etp_name_search"}
    assert {"골드", "금현물"} <= pats
    plan2 = _route(index, "금 ETF 순자산 큰 3개")
    assert plan2.intent == "gold_rank"
    assert _name(_rows(con, _call(plan2, "etp_top_aum"))[0]) == "ACE KRX금현물"
    assert _route(index, "금리 인하되면 채권 ETF 사야 해?").intent != "gold_search"   # '금리'는 금이 아니다


# ── 9. 함정 변형 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("어제 TIGER 200 NAV 얼마였어", "time_violation"),
    ("코스닥 ETF 오늘 얼마나 올랐어", "time_violation"),
    ("KODEX 200 손절해야 할까", "action_request"),
    ("KODEX 200 가입 방법 알려줘", "action_request"),
    ("최고의 ETF 추천해줘", "action_request"),
    ("수익 보장되는 펀드 있어", "action_request"),
    ("달러 예금 금리 얼마야", "unsupported_asset"),
])
def test_trap_variants_refuse(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


def test_trap_words_do_not_overreach(index):
    assert _route(index, "온라인으로 가입할 수 있는 인덕스 펀드 클래스 알려줘").behavior_hint != "refuse"
    assert _route(index, "수익률 가장 좋은 ETF 알려줘").behavior_hint != "refuse"
    assert _route(index, "TIGER 미국S&P500 배당금 언제 들어와").behavior_hint != "refuse"


# ── 10. 숨김 조회문·파라미터 — AI 라우터 목록(프롬프트)·도구 스키마 불변 ─────────────────────

def test_hidden_templates_and_params_round7():
    text = _template_catalog_text()
    for tid in ("global_etf_detail", "constituent_pair_weight", "bond_metric_avg", "global_etf_metric_avg"):
        assert tid in TEMPLATES and tid in LLM_HIDDEN_TEMPLATES and f"- {tid}:" not in text
    enum = build_router_tool()["function"]["parameters"]["properties"]["sql_calls"]["items"][
        "properties"]["template_id"]["enum"]
    assert not (set(enum) & set(LLM_HIDDEN_TEMPLATES))
    for tid, p in [("etp_count", "index_pattern"), ("etp_top_aum", "index_pattern"), ("etp_by_dividend", "mgmt"),
                   ("etp_top_return", "mgmt"), ("etp_low_fee", "mgmt"), ("etp_metric_rank", "mgmt"),
                   ("global_etf_count", "leveraged_only"), ("global_etf_filter", "leveraged_only")]:
        assert (tid, p) in LLM_HIDDEN_PARAMS and any(x.name == p for x in TEMPLATES[tid].params)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
