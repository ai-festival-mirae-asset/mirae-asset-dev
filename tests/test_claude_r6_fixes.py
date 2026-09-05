# -*- coding: utf-8 -*-
"""리더 세션 6바퀴(9/6) — 표현 변형 점검 2차 65문항(펀드·채권·해외·상세·오타/영어·함정)이 찾은 공백의 회귀 잠금.

찾은 것(전부 규칙 엔진 실측): 펀드 이름 조각(TDF 2045·연금저축·ESG·국민성장)이 폴백 · 펀드 건수가 조건을 버리고 전체 건수 ·
'ESG'가 해외 ETF 티커로 잡혀 펀드 검색 차단 · 클래스·수수료 질의를 이름 검색이 가로채기 · 채권 등급+만기+정렬 결합 ·
영문 운용사(iShares)가 국내 표 집계 0건(8.5 규칙이 먼저 잡음) · '미국 국채 ETF 순자산 큰 것'이 폴백/지역 누락 ·
브랜드·운용사 총보수 평균이 존재 검문 거절 · 국고채 표면금리 평균/해외 ETF 총보수 평균이 목록·폴백(평균 집계 조회문 없음) ·
평균 결과가 'avg_value 4.1' 원문 열 이름 · 함정 변형(목표주가·어제 종가·사야 해·공모주·세금·다음 달 분배금).
수정마다 같은 뜻 다른 표현 2~3개를 시험한다(TEAM_IMPROVEMENT_GUIDE §5 수정 원칙 2).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import answer_question, avg_sentence, serialize_answer
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


# ── 1. 펀드 — 이름 조각 검색·조건 건수·조각 상세·클래스 질의 양보 ─────────────────────────────

def test_fund_return_ranking_keeps_region_and_type(index):
    plan = _route(index, "국내 주식형 펀드 중 1년 수익률 높은 5개")
    assert plan.intent == "fund_ranking"
    c = _call(plan, "fund_top_return_1y")
    assert c.params["btyp_pattern"] == "%주식형%" and c.params["region"] == "국내" and c.params["limit"] == 5


@pytest.mark.parametrize("q,pat", [
    ("TDF 2045 펀드 알려줘", "TDF2045"),
    ("연금저축용 펀드 추천해줘", "연금저축"),
    ("ESG 펀드 뭐 있어", "ESG"),
])
def test_fund_name_search_by_fragment(index, con, q, pat):
    plan = _route(index, q)
    assert plan.intent == "fund_name_search", plan.intent
    c = _call(plan, "fund_filter")
    assert c.params["name_pattern_raw"] == pat
    assert len(_rows(con, c)) >= 3
    assert any(pat in n for n in plan.notes)


def test_global_ticker_does_not_hijack_fund_question(index):
    # 'ESG'는 해외 ETF 티커(product_global_etf)로도 있다 — 펀드 문맥 + 바로 뒤 '펀드'면 상품 해석을 버린다
    assert _route(index, "ESG 펀드 뭐 있어").intent == "fund_name_search"
    assert _route(index, "ESG 펀드 있어?").intent == "fund_name_search"


def test_fund_fragment_detail(index):
    plan = _route(index, "국민성장펀드 위험등급은?")
    assert plan.intent == "fund_detail_by_fragment"
    assert _call(plan, "fund_detail").params["itm_no"].startswith("KR51534801")


@pytest.mark.parametrize("q,params", [
    ("미래에셋자산운용이 운용하는 펀드 몇 개야?", {"name_pattern_raw": "미래에셋"}),
    ("공모펀드 중 위험등급 6등급인 상품 몇 개?", {"min_risk": 6, "max_risk": 6}),
])
def test_fund_count_keeps_conditions(index, q, params):
    plan = _route(index, q)
    assert plan.intent == "fund_count_filter", plan.intent
    c = _call(plan, "fund_filter")
    for k, v in params.items():
        assert c.params[k] == v
    assert c.params["limit"] >= 30000                      # 건수는 목록 머리의 '결과 N건'


def test_fund_count_matches_duckdb(index, con):
    plan = _route(index, "공모펀드 중 위험등급 6등급인 상품 몇 개?")
    n = con.execute("SELECT count(*) FROM fund_master WHERE TRY_CAST(drv_risk_grade AS INT)=6").fetchone()[0]
    assert len(_rows(con, _call(plan, "fund_filter"))) == n


@pytest.mark.parametrize("q", [
    "판매수수료 없는 클래스로 가입할 수 있는 인덕스펀드 알려줘",
    "온라인으로 가입할 수 있는 인덕스 펀드 클래스 알려줘",
])
def test_class_fee_questions_not_intercepted_by_name_search(index, q):
    plan = _route(index, q)
    assert plan.intent not in ("fund_name_search", "fund_detail_by_fragment"), plan.intent
    assert _call(plan, "fund_class_by_fee") is not None


def test_fund_holdings_question_answers_with_etf_and_limit_note(index):
    plan = _route(index, "삼성전자 담은 펀드 있어?")
    assert plan.intent == "constituent_reverse" and plan.behavior_hint == "partial"
    assert _call(plan, "constituent_holders").params["code"] == "005930"
    assert any("공모펀드" in n for n in plan.notes)


def test_fund_filter_on_sale_region_aum(index):
    c = _call(_route(index, "판매중인 해외 주식형 펀드 순자산 큰 순 5개"), "fund_filter")
    assert c.params["region"] == "해외" and c.params["order"] == "aum"
    assert c.params["on_sale_only"] == "Y" and c.params["attr_pattern_raw"] == "주식형"


# ── 2. 채권 — 등급+만기+정렬 결합 · 등급 정확 일치 · 만기 연도 ──────────────────────────────

def test_bond_rating_maturity_coupon_rank(index, con):
    plan = _route(index, "AA- 이상 회사채 중 만기 1년 이내 표면금리 높은 3개")
    c = _call(plan, "bond_maturing_within")
    assert c is not None and c.params["bond_class"] == "회사채" and c.params["max_rating_rank"] == 4
    assert c.params["limit"] == 3
    rows = _rows(con, c)
    key = next(k for k in rows[0] if k.upper() == "SRFC_IRT")
    coupons = [float(r[key]) for r in rows]
    assert len(rows) == 3 and coupons == sorted(coupons, reverse=True)


def test_bond_rating_exact_grade_list(index, con):
    plan = _route(index, "신용등급 BBB 회사채 목록")
    c = _call(plan, "bond_filter")
    assert c.params["min_rating_rank"] == 9 and c.params["max_rating_rank"] == 9 and c.params["bond_class"] == "회사채"
    rows = _rows(con, c)
    assert rows and all(str(r.get("drv_crd_grd_norm", "")).startswith("BBB") for r in rows)


def test_bond_maturity_year_with_class_and_coupon_order(index, con):
    plan = _route(index, "만기 2030년 이후 국공채 표면금리 높은 순")
    c = _call(plan, "bond_maturing_within")
    assert c.params["as_of_date"] == "2030-01-01" and c.params["bond_class"] == "국공채"
    assert [r["PD_NM"] for r in _rows(con, c)[:2]] == ["국고채권 04750-3012(10-7)", "국고채권 04500-5609(26-8)"]


@pytest.mark.parametrize("q,key,val", [
    ("표면금리 5% 넘는 특수채 몇 개?", "bond_class", "특수채"),
    ("2025년에 발행된 채권 몇 개", "min_issue_dt", "2025-01-01"),
])
def test_bond_count_conditions(index, q, key, val):
    assert _call(_route(index, q), "bond_count").params[key] == val


def test_bond_pension_filter(index):
    assert _call(_route(index, "퇴직연금에 넣을 수 있는 채권 5개"), "bond_filter").params["pension_only"] == "Y"


# ── 3. 평균 집계 — 채권·해외(숨김 조회문 2종)·브랜드/운용사 국내 ───────────────────────────

def test_bond_coupon_average(index, con):
    plan = _route(index, "국고채 표면금리 평균 얼마야")
    assert plan.intent == "bond_metric_avg" and plan.behavior_hint == "partial"
    c = _call(plan, "bond_metric_avg")
    assert c.params == {"bond_class": "국공채", "maturity_status": "active", "metric": "coupon"}
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(SRFC_IRT AS DOUBLE)),2), count(*) FROM kr_bond "
                      "WHERE STD_PD_MCLS_NM='국공채' AND drv_maturity_status='active' "
                      "AND coalesce(TRY_CAST(SRFC_IRT AS DOUBLE),0)<>0").fetchone()
    assert (float(row["avg_value"]), int(row["n"])) == (float(exp[0]), int(exp[1]))
    assert tuple(plan.hints["avg_label"]) == ("표면금리", "%")
    assert any("단순 평균" in n for n in plan.notes)


def test_bond_after_tax_average_with_rating(index, con):
    plan = _route(index, "AA 이상 회사채 세후수익률 평균")
    c = _call(plan, "bond_metric_avg")
    assert c.params["metric"] == "after_tax" and c.params["max_rating_rank"] == 3 and c.params["bond_class"] == "회사채"
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(AFTER_TAX_YIELD AS DOUBLE)),2), count(*) FROM kr_bond "
                      "WHERE STD_PD_MCLS_NM='회사채' AND drv_maturity_status='active' "
                      "AND TRY_CAST(drv_crd_grd_rank AS INT)<=3 AND coalesce(TRY_CAST(AFTER_TAX_YIELD AS DOUBLE),0)<>0").fetchone()
    assert (float(row["avg_value"]), int(row["n"])) == (float(exp[0]), int(exp[1]))


def test_global_fee_average(index, con):
    plan = _route(index, "해외 ETF 총보수 평균 얼마야")
    assert plan.intent == "global_metric_avg" and plan.behavior_hint == "partial"
    c = _call(plan, "global_etf_metric_avg")
    assert c.params == {"metric": "fee", "etf_only": "Y"}
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(cu_charge_rt AS DOUBLE)),2), count(*) FROM global_etf "
                      "WHERE coalesce(TRY_CAST(cu_charge_rt AS DOUBLE),0)>0 "
                      "AND upper(coalesce(drv_is_etn,'')) NOT IN ('Y','TRUE','1')").fetchone()
    assert (float(row["avg_value"]), int(row["n"])) == (float(exp[0]), int(exp[1]))


def test_global_aum_average_region_type(index):
    plan = _route(index, "미국 주식형 해외 ETF 순자산 평균")
    c = _call(plan, "global_etf_metric_avg")
    assert c.params["metric"] == "aum" and c.params["ast_type"] == "Equity"
    assert c.params["region_pattern_raw"] == "United States"
    assert tuple(plan.hints["avg_label"]) == ("순자산", " USD")


def test_brand_fee_average(index, con):
    plan = _route(index, "미래에셋 TIGER ETF 총보수 평균")
    assert plan.intent == "etp_metric_avg"
    c = _call(plan, "etp_metric_avg")
    assert c.params == {"metric": "fee", "type": "ETF", "name_pattern": "%TIGER%"}
    assert _call(plan, "coverage_check").params["field"] == "kr_etp.cu_charge_rt"
    row = _rows(con, c)[0]
    exp = con.execute("SELECT round(avg(TRY_CAST(cu_charge_rt AS DOUBLE)),2), count(*) FROM kr_etp "
                      "WHERE drv_listing_status='active' AND drv_instrument_type='ETF' AND pd_nm ILIKE '%TIGER%' "
                      "AND coalesce(TRY_CAST(cu_charge_rt AS DOUBLE),0)<>0").fetchone()
    assert (float(row["avg_value"]), int(row["n"])) == (float(exp[0]), int(exp[1]))


def test_manager_diff_average(index, con):
    plan = _route(index, "삼성자산운용 ETF 평균 괴리율")
    c = _call(plan, "etp_metric_avg")
    assert c.params["mgmt"] == "삼성" and c.params["metric"] == "diff"
    assert int(_rows(con, c)[0]["n"]) > 100


def test_brand_average_not_refused_by_existence_gate(ctx):
    ser = _ask(ctx, "미래에셋 TIGER ETF 총보수 평균")
    assert not ser["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"), ser["answer"][:200]
    assert "총보수 평균" in ser["answer"] and "TIGER" in ser["answer"]


def test_index_tracking_average_is_a_sentence(ctx):
    ser = _ask(ctx, "코스피200 추종 상품들의 평균 추적오차 얼마야")
    assert "추적오차율 평균" in ser["answer"] and "avg_value" not in ser["answer"]


def test_avg_sentence_formats():
    assert avg_sentence("etp_metric_avg", [{"avg_value": 4.1, "n": 94}], ("추적오차율", "%")) \
        .endswith("추적오차율 평균 4.1% — 값 보유 94건의 단순 평균")
    s = avg_sentence("global_etf_metric_avg", [{"avg_value": 6607349509.54, "n": 1468}], ("순자산", " USD"))
    assert "66억 USD" in s and "6,607,349,509" not in s
    assert "0건" in avg_sentence("bond_metric_avg", [{"avg_value": None, "n": 0}], ("표면금리", "%"))
    assert "평균값 평균 1.5" in avg_sentence("etp_metric_avg", [{"avg_value": 1.5, "n": 2}])


# ── 4. 국내 순위 — 지역+수식어/테마 결합, 표기 변형 OR, 수 없는 순위 표현 ─────────────────────

def test_region_plus_qualifier_ranking_keeps_both(index, con):
    plan = _route(index, "미국 국채 ETF 순자산 큰 것")
    assert plan.intent == "etp_ranking"
    c = _call(plan, "etp_top_aum")
    assert c.params["name_pattern"] == "%미국%국채%" and c.params["name_pattern2"] == "%미국채%"
    assert c.params["limit"] == 5
    rows = _rows(con, c)
    assert rows and all(("미국" in _name(r) and "국채" in _name(r)) or "미국채" in _name(r) for r in rows)


def test_region_plus_theme_ranking(index, con):
    c = _call(_route(index, "미국 반도체 ETF 순자산 1위"), "etp_top_aum")
    assert c.params["name_pattern"] == "%미국%반도체%"
    assert _name(_rows(con, c)[0]) == "TIGER 미국필라델피아반도체나스닥"


def test_region_name_variant_china(index, con):
    plan = _route(index, "중국 전기차 ETF 순자산 1위")
    c = _call(plan, "etp_top_aum")
    assert c.params["name_pattern"] == "%중국%전기차%" and c.params["name_pattern2"] == "%차이나%전기차%"
    assert _name(_rows(con, c)[0]) == "TIGER 차이나전기차SOLACTIVE"
    assert plan.hints.get("skip_generation") is True       # HCX 가 순자산을 '765조'로 쓰고 사후 대조가 상품명 줄을 지운 실측(R6-14)


def test_theme_ranking_answers_deterministically(ctx):
    ser = _ask(ctx, "미국 반도체 ETF 순자산 1위")
    assert "TIGER 미국필라델피아반도체나스닥" in ser["answer"] and "5.7조원" in ser["answer"]


def test_theme_only_and_region_only_ranking_unchanged(index):
    assert _call(_route(index, "2차전지 ETF 중 순자산 1위"), "etp_top_aum").params.get("name_pattern_raw") == "2차전지"
    assert _call(_route(index, "유럽 주식 ETF 순자산 큰 3개"), "etp_top_aum").params.get("name_pattern_raw") == "유럽"


def test_ranking_without_number(index):
    plan = _route(index, "순자산 큰 ETF 알려줘")
    assert plan.intent == "etp_ranking" and _call(plan, "etp_top_aum").params["limit"] == 5


# ── 5. 영문 운용사(해외 마스터) — 8.5 국내 운용사 규칙보다 먼저 ───────────────────────────────

def test_english_manager_count_uses_global_master(index, con):
    plan = _route(index, "iShares가 운용하는 ETF 몇 개")
    assert plan.intent == "global_count", plan.intent
    c = _call(plan, "global_etf_count")
    assert c.params["mgmt_pattern_raw"] == "iShares" and "iShares" in c.params["brand_word"]   # 8바퀴: 상품명은 낱말 경계 정규식
    n = con.execute("SELECT count(*) FROM global_etf WHERE regexp_matches(pd_nm, '(^|[^A-Za-z0-9])iShares([^A-Za-z0-9]|$)', 'i') "
                    "OR cu_fund_mgmt_co ILIKE '%ishares%'").fetchone()[0]   # 8바퀴: 상품명은 낱말 경계(ARK ≠ Markets)
    assert sum(int(r["n"]) for r in _rows(con, c)) == n and n > 100   # 운용사 표기(BlackRock)에만 기대면 8건


def test_english_manager_ranking(index, con):
    c = _call(_route(index, "Vanguard ETF 순자산 큰 5개"), "global_etf_filter")
    assert c.params["mgmt_pattern_raw"] == "Vanguard"
    assert _name(_rows(con, c)[0]) == "VOO"


def test_domestic_brand_not_treated_as_english_manager(index):
    assert _call(_route(index, "TIGER ETF 총 몇 종목?"), "global_etf_count") is None


# ── 6. 종목 별칭·붙여쓰기·정렬 동반 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("q,code", [
    ("삼전 포함 ETF", "005930"),
    ("삼성전자포함된ETF알려줘", "005930"),
    ("삼성전자가 포함된 이티에프 알려줘", "005930"),
])
def test_constituent_aliases_and_spacing(index, q, code):
    plan = _route(index, q)
    assert plan.intent == "constituent_reverse", plan.intent
    assert _call(plan, "constituent_holders").params["code"] == code


def test_constituent_reverse_with_aum_order(index, con):
    c = _call(_route(index, "SK 하이닉스 담은 ETF 순자산 큰 3개"), "constituent_holders")
    assert c.params["code"] == "000660" and c.params["order"] == "aum"
    assert [_name(r) for r in _rows(con, c)[:2]] == ["KODEX 200", "TIGER 200"]


# ── 7. 함정 변형 ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,intent", [
    ("삼성전자 목표주가 알려줘", "unsupported_field"),
    ("지금 KODEX 200 사는 게 좋아?", "action_request"),
    ("금리 인하되면 채권 ETF 사야 해?", "action_request"),
    ("내 나이 30인데 어떤 펀드가 좋아?", "action_request"),
    ("공모주 청약 일정 알려줘", "unsupported_asset"),
    ("ETF 팔면 세금 얼마 내야 해?", "action_request"),
    ("KODEX 200 어제 종가 얼마였어?", "time_violation"),
    ("채권 ETF 중 다음 달 분배금 얼마 줄까?", "time_violation"),
    ("코스피 전망 어때?", "time_violation"),
])
def test_trap_variants_refuse(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent and plan.behavior_hint == "refuse", (plan.intent, plan.behavior_hint)


def test_modifier_word_is_not_a_fund_name_fragment(index):
    # v3 T-15 회귀(6바퀴 관문 실측): '맞는'을 이름 조각으로 검색해 0건 '답변'이 나가던 것 — 개인 맞춤 추천 거절로
    plan = _route(index, "나한테 맞는 펀드 하나 골라줘")
    assert plan.intent == "action_request" and plan.behavior_hint == "refuse", plan.intent
    assert _call(plan, "fund_filter") is None


@pytest.mark.parametrize("q", ["우주여행 펀드 알려줘", "저에게 어울리는 펀드 추천해줘"])
def test_unknown_or_personal_fund_phrases_are_not_name_searched(index, q):
    assert _route(index, q).intent != "fund_name_search"


# ── 8. 해외 변형 파라미터 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,op,params", [
    ("나스닥 상장 인버스 ETF 몇 개", "global_etf_count", {"inverse_only": "Y"}),
    ("해외 ETN 몇 개 있어", "global_etf_count", {"etn_only": "Y"}),
    ("일본 주식형 해외 ETF 규모 순 3개", "global_etf_filter", {"ast_type": "Equity", "region_pattern_raw": "Japan", "limit": 3}),
    ("해외 ETF 중 총보수 0.1% 이하 주식형 5개", "global_etf_filter", {"max_fee_le": 0.1, "ast_type": "Equity"}),
])
def test_global_variants(index, q, op, params):
    c = _call(_route(index, q), op)
    assert c is not None
    for k, v in params.items():
        assert c.params[k] == v


# ── 9. 숨김 조회문·파라미터 — AI 라우터 목록(프롬프트)·도구 스키마 불변 ───────────────────────

def test_hidden_templates_exist_but_stay_out_of_llm_catalog_and_tool():
    text = _template_catalog_text()
    for tid in LLM_HIDDEN_TEMPLATES:
        assert tid in TEMPLATES and f"- {tid}:" not in text
    enum = build_router_tool()["function"]["parameters"]["properties"]["sql_calls"]["items"][
        "properties"]["template_id"]["enum"]
    assert not (set(enum) & set(LLM_HIDDEN_TEMPLATES))
    for tid, p in [("etp_metric_avg", "name_pattern"), ("etp_metric_avg", "mgmt"), ("etp_top_aum", "name_pattern2")]:
        assert (tid, p) in LLM_HIDDEN_PARAMS
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
