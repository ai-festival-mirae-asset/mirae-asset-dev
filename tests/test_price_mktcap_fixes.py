# -*- coding: utf-8 -*-
"""9/2 사용자 실측 3건의 회귀 잠금 — 종가·시가총액·규모 동의어.

1. '종가가 가장 높은 ETF' — 엔진 어디서도 du_clpr 를 안 써서 규칙 라우터가 못 잡고, AI 라우터가
   '상위 N개' 모양이 같은 수익률 템플릿을 고르던 오답 → etp_metric_rank metric='price'
2. '시가총액' — 원천에 열이 없어 종가×상장주식수로 계산(사용자 결정) → metric='mkt_cap',
   구성종목 역질의는 constituent_holders order='mkt_cap'
3. '…포함된 ETF 중 가장 큰·자산이 가장 많은' 이 비중순 목록으로 새던 것 → 순자산순.
   '비중이 가장 큰' 은 비중순 유지, '수익률이 가장 큰' 은 수익률 템플릿 유지.

각 수정은 같은 뜻 다른 표현 2~3개로 시험한다(TEAM_IMPROVEMENT_GUIDE §5 수정 원칙 2).
"""
import datetime

import duckdb
import pytest

from engine.answer_service import _fmt_row
from engine.policy import load_policy
from engine.router import route
from engine.sql_templates import run_template, validate_params
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 2)
POLICY = load_policy()


@pytest.fixture(scope="module")
def con():
    return duckdb.connect(DB_PATH_DEFAULT, read_only=True)


@pytest.fixture(scope="module")
def index(con):
    return build_entity_index(con)


def _route(index, q):
    return route(q, index, policy=POLICY, today=TODAY)


def _call(plan, template_id):
    return next((c for c in plan.calls if c.op == template_id), None)


def _rows(con, template_id, params):
    return run_template(con, template_id, params).rows


# ---------------------------------------------------------------------------
# 1. 종가 순위 — etp_metric_rank metric='price'
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "데이터 기준일 기준으로 종가가 가장 높은 ETF 5개를 종가와 함께 보여줘",
    "종가가 가장 높은 ETF 5개를 보여줘",
    "국내 ETF 중 가격이 제일 비싼 거 3개",
])
def test_price_rank_desc(index, q):
    plan = _route(index, q)
    c = _call(plan, "etp_metric_rank")
    assert c is not None, (q, plan.intent, plan.stage)
    assert c.params["metric"] == "price" and c.params["direction"] == "desc"
    assert c.params.get("type") == "ETF"
    assert plan.stage == "rule"                          # AI 라우터까지 가지 않는다


@pytest.mark.parametrize("q", ["가격이 저렴한 국내 ETF 순으로 5개", "가격 싼 ETF 순으로 보여줘"])
def test_price_rank_asc(index, q):
    c = _call(_route(index, q), "etp_metric_rank")
    assert c is not None and c.params["metric"] == "price" and c.params["direction"] == "asc"


@pytest.mark.parametrize("q", [
    "종가가 가장 높은 종목 알려줘",              # ETF/ETN 낱말 없음 — 개별 종목 시세는 원천에 없다
    "가격이 가장 많이 오른 ETF 알려줘",           # 수익률 질의 — 종가 순위로 오독 금지
    "채권 중 가격이 가장 높은 거 알려줘",         # 채권 가격은 ETP 표가 아니다
])
def test_price_rank_not_hijacked(index, q):
    assert _call(_route(index, q), "etp_metric_rank") is None


def test_realtime_price_still_refused(index):
    # 기존 잠금(v2 '현재 에코프로 종가', r2 '작년 12월 31일 종가')과 같은 시간 경계 거절이 순위 규칙보다 앞선다
    plan = _route(index, "오늘 종가가 가장 높은 ETF 알려줘")
    assert plan.behavior_hint == "refuse" and plan.hints.get("time_violation")


def test_price_sql_top_is_cd_rate_etf(con):
    rows = _rows(con, "etp_metric_rank", {"metric": "price", "direction": "desc", "limit": 5, "type": "ETF"})
    assert rows and rows[0]["pd_abrv_nm"].startswith("KODEX CD금리액티브")   # 9/2 DuckDB 직접 계산 정답
    assert rows[0]["du_clpr"] > 1_000_000
    assert all(rows[i]["du_clpr"] >= rows[i + 1]["du_clpr"] for i in range(len(rows) - 1))


def test_price_value_survives_display_cut():
    # 정렬 기준 값이 SELECT 뒤쪽 열이라 표시 상한(앞 4열)에 잘리던 유형(기준가 NAV 실측) — focus 로 앞세운다
    row = {"pd_abrv_nm": "KODEX CD금리액티브(합성)", "pd_nm": "삼성 KODEX …", "drv_instrument_type": "ETF",
           "du_diff_rt": -0.01, "du_chas_errt": 0.06, "du_vlty_1y": 0.1, "du_vol_1d": 100.0,
           "du_val_1d": 1.0, "du_last_nav": 1075400.0, "du_clpr": 1075450.0}
    out = _fmt_row(row, focus=["du_clpr"])
    assert out.startswith("KODEX CD금리액티브(합성) (종가(원) 1,075,450")   # 9/3 표기: 한글 라벨


# ---------------------------------------------------------------------------
# 2. 시가총액 — 종가×상장주식수 계산값
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", ["시가총액이 가장 큰 국내 ETF 5개 알려줘", "시총 상위 3개 ETF"])
def test_mktcap_rank(index, q):
    plan = _route(index, q)
    c = _call(plan, "etp_metric_rank")
    assert c is not None and c.params["metric"] == "mkt_cap" and c.params["direction"] == "desc"
    assert any("종가" in n and "상장주식수" in n for n in plan.notes)   # 계산값임을 답변에 명시


def test_mktcap_sql_kodex200_first_with_krw(con):
    rows = _rows(con, "etp_metric_rank", {"metric": "mkt_cap", "direction": "desc", "limit": 3, "type": "ETF"})
    assert rows[0]["pd_abrv_nm"] == "KODEX 200"                          # 25.9조원(9/2 직접 계산)
    assert rows[0]["mkt_cap"] > 2e13 and rows[0]["mkt_cap_krw"].endswith("조원")


def test_mktcap_display_uses_krw_sibling():
    row = {"pd_abrv_nm": "KODEX 200", "pd_nm": "삼성 KODEX 200", "drv_instrument_type": "ETF",
           "du_diff_rt": 0.1, "du_chas_errt": 0.2, "du_vlty_1y": 1.0, "du_vol_1d": 1.0,
           "mkt_cap": 2.588e13, "mkt_cap_krw": "25.9조원"}
    out = _fmt_row(row, focus=["mkt_cap"])
    assert out.startswith("KODEX 200 (시가총액(계산값) 25.9조원")          # 9/3 표기: 한글 라벨


def test_constituent_mktcap_order(index, con):
    plan = _route(index, "삼성전자가 포함된 ETF 중 시가총액 가장 높은 ETF를 알려줘")
    c = _call(plan, "constituent_holders")
    assert c is not None and c.params.get("order") == "mkt_cap"
    assert plan.hints.get("order") == "mkt_cap"
    assert any("종가" in n and "상장주식수" in n for n in plan.notes)
    rows = _rows(con, "constituent_holders", dict(c.params))
    assert rows[0]["pd_abrv_nm"] == "KODEX 200" and rows[0]["mkt_cap"] > 2e13


def test_constituent_mktcap_column_hidden_for_other_orders(con):
    # order 가 mkt_cap 이 아니면 mkt_cap 은 NULL → 근거·표시에 실리지 않아 기존 질의의 근거가 그대로다
    rows = _rows(con, "constituent_holders", {"code": "005930", "limit": 3, "order": "aum"})
    assert rows and all(r.get("mkt_cap") is None and "mkt_cap_krw" not in r for r in rows)


def test_validate_params_accepts_new_enums():
    validate_params("etp_metric_rank", {"metric": "price", "direction": "desc", "limit": 5})
    validate_params("etp_metric_rank", {"metric": "mkt_cap", "direction": "asc", "limit": 5})
    validate_params("constituent_holders", {"code": "005930", "limit": 5, "order": "mkt_cap"})


# ---------------------------------------------------------------------------
# 3. 규모 동의어 — 구성종목 역질의
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "삼성전자가 포함된 ETF 중 가장 큰 ETF는?",
    "삼성전자를 담은 ETF 중 자산이 가장 많은 것 알려줘",
    "삼성전자 편입 ETF 중 덩치 제일 큰 거",
])
def test_constituent_size_synonyms_go_aum(index, q):
    plan = _route(index, q)
    c = _call(plan, "constituent_holders")
    assert c is not None, (q, plan.intent)
    assert c.params.get("order") == "aum" and plan.hints.get("order") == "aum"


@pytest.mark.parametrize("q", ["삼성전자 비중이 가장 큰 ETF 알려줘", "삼성전자 비중 제일 높은 ETF"])
def test_constituent_weight_phrase_stays_weight_order(index, q):
    c = _call(_route(index, q), "constituent_holders")
    assert c is not None and c.params.get("order") is None      # 기본 = 비중 큰 순


def test_constituent_return_phrase_unchanged(index):
    plan = _route(index, "삼성전자 담은 ETF 중 수익률이 가장 큰 거")
    assert _call(plan, "constituent_holders_top_return") is not None


def test_constituent_aum_sql_kodex200_first(con):
    rows = _rows(con, "constituent_holders", {"code": "005930", "limit": 3, "order": "aum"})
    assert rows[0]["pd_abrv_nm"] == "KODEX 200"                          # 25.5조원 — 비중순 1위(KODEX KTOP30 190억원)와 다르다


# ---------------------------------------------------------------------------
# 4. (같은 날 발견) v1 H-25 회귀 — r4 '해외 자산유형' 규칙이 '국내·해외 각각' 질문을 해외 전용으로 낚아챔
#    r3·r4 커밋(8/28 22:58 · 8/29 04:20) 뒤 v1 전체 재채점이 없어 9/2 무료 구성 재채점에서 처음 발견.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", [
    "금이나 원자재에 투자하는 상품이 국내·해외에 각각 있어?",         # v1 H-25 원문
    "원자재 ETF 국내랑 해외 둘 다 알려줘",
])
def test_both_markets_asset_question_not_global_only(index, q):
    plan = _route(index, q)
    assert _call(plan, "global_etf_filter") is None, plan.intent         # 해외 전용 필터로 가면 국내 상품 0건
    assert plan.behavior_hint != "refuse"


def test_global_only_asset_question_keeps_r4_route(index):
    # r4 잠금(test_blind_r2_fixes.test_global_asset_type_but_not_fund)과 같은 뜻 — '국내'가 없으면 종전대로
    c = _call(_route(index, "해외 ETF 중에 원자재에 투자하는 상품 알려줘"), "global_etf_filter")
    assert c is not None and c.params.get("ast_type") == "Commodity"


# ---------------------------------------------------------------------------
# 5. AI 라우터 프롬프트 동결 — 새 허용값(price·mkt_cap)은 HCX 에게 보이지 않는다
#    9/2 A/B 실측: 허용값 2개가 목록에 실리는 것만으로 v1 H-17(AI 라우터 의존 문항)이 5/5 통과 → 1/5 로 흔들림.
#    규칙 라우터만 쓰는 값은 LLM_HIDDEN_ENUM_VALUES 로 숨겨 목록을 검증된 상태 그대로 둔다.
# ---------------------------------------------------------------------------

# 8/29 04:20 커밋(ca75976) 기준 목록의 sha256 — H-17 5/5·T-09 통과가 확인된 프롬프트. 목록(템플릿 설명·파라미터·
# 허용값)을 바꾸면 이 시험이 멈춘다. 바꿔야 한다면: AI 라우터 의존 문항(v1 H-17·T-09)을 HCX 로 3회 이상 재검해
# 통과를 확인한 뒤 이 값을 갱신한다 — 프롬프트 변경은 코드 변경과 같은 급의 회귀 위험이라는 뜻이다.
_CATALOG_SHA256_FROZEN = "a3f8e65498b70ed5264da3fcf84f5336b52cb4b6448e483b0c9bce07ebf25855"


def test_llm_catalog_hides_rule_only_enum_values():
    import hashlib
    from engine.router_llm import _template_catalog_text
    from engine.sql_templates import LLM_HIDDEN_ENUM_VALUES, LLM_HIDDEN_PARAMS, TEMPLATES
    text = _template_catalog_text()
    for tid, pname in LLM_HIDDEN_PARAMS:                      # 9/3: 규칙 전용 파라미터는 통째로 숨긴다
        assert any(p.name == pname for p in TEMPLATES[tid].params)   # 템플릿에는 존재(규칙 라우터 호출용)
        line = next(l for l in text.splitlines() if l.startswith(f"- {tid}:"))
        assert pname not in line, line
    for (tid, pname), hidden in LLM_HIDDEN_ENUM_VALUES.items():
        enum = next(p.enum for p in TEMPLATES[tid].params if p.name == pname)
        assert all(v in enum for v in hidden)                 # 숨긴 값은 여전히 유효(규칙 라우터 호출용)
        line = next(l for l in text.splitlines() if l.startswith(f"- {tid}:"))
        assert all(f"'{v}'" not in line for v in hidden), line
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN, (
        "AI 라우터 목록(프롬프트)이 바뀌었다 — 위 주석대로 H-17·T-09 를 HCX 로 재검한 뒤 해시를 갱신할 것")


# ---------------------------------------------------------------------------
# 6. (9/3 사용자 실측) 채권 표면금리 조건 — '이자율이 3.5%인 채권'의 3.5% 가 조용히 버려지던 공백
#    방향 낱말이 이상·초과·넘·대 일 때만 조건이 되고, '이하·미만'과 방향 없음('~인/짜리')은 무시돼 조건 없는
#    전체 목록(7.1% 부터)이 나가던 것. 건수 조회도 금리 조건 없이 전체 건수를 내던 불일치 포함.
# ---------------------------------------------------------------------------

def _coupon(plan, op):
    c = next((c for c in plan.calls if c.op == op), None)
    assert c is not None, (op, plan.intent, [x.op for x in plan.calls])
    return c.params.get("min_coupon"), c.params.get("max_coupon")


@pytest.mark.parametrize("q, lo, hi", [
    ("이자율이 3.5%인 채권을 하나 보여줘", 3.5, 3.5 + 1e-6),       # 9/3 실측 원문 — 정확 일치
    ("표면금리 3.5%짜리 채권 알려줘", 3.5, 3.5 + 1e-6),
    ("표면금리 3% 이하인 채권 알려줘", None, 3.0 + 1e-6),          # 이하 = 경계 포함
    ("금리 3% 미만 채권 있어?", None, 3.0),                         # 미만 = 경계 제외
    ("표면금리 3% 이상 채권 알려줘", 3.0, None),                    # 종전에도 되던 것 — 회귀 방어
    ("금리 3%대 채권 보여줘", 3.0, 4.0),
    ("표면금리 3% 이상 4% 이하 채권 몇 개야?", 3.0, 4.0 + 1e-6),  # 하한+상한 결합
])
def test_bond_coupon_condition_variants(index, q, lo, hi):
    plan = _route(index, q)
    op = "bond_count" if "몇 개" in q else "bond_filter"
    got_lo, got_hi = _coupon(plan, op)
    assert (got_lo is None) == (lo is None) and (got_hi is None) == (hi is None), (q, got_lo, got_hi)
    if lo is not None:
        assert abs(got_lo - lo) < 1e-9
    if hi is not None:
        assert abs(got_hi - hi) < 1e-9
    assert any("표면금리(SRFC_IRT)" in n for n in plan.notes)            # 채택한 해석을 답변에 명시
    if op == "bond_filter":                                             # 건수 조회에도 같은 조건
        assert _coupon(plan, "bond_count") == (got_lo, got_hi)


def test_bond_coupon_exact_sql_matches_only_that_rate(con, index):
    plan = _route(index, "이자율이 3.5%인 채권을 하나 보여줘")
    f = next(c for c in plan.calls if c.op == "bond_filter")
    rows = _rows(con, "bond_filter", dict(f.params))
    assert rows and all(float(r["SRFC_IRT"]) == 3.5 for r in rows)
    n = next(c for c in plan.calls if c.op == "bond_count")
    assert _rows(con, "bond_count", dict(n.params))[0]["n"] == 67    # 9/3 DuckDB 직접 계산(만기 미경과·정확히 3.5%)


def test_bond_coupon_below_sql_never_returns_higher_rate(con, index):
    plan = _route(index, "표면금리 3% 이하인 채권 알려줘")
    f = next(c for c in plan.calls if c.op == "bond_filter")
    rows = _rows(con, "bond_filter", dict(f.params))
    assert rows and all(float(r["SRFC_IRT"]) <= 3.0 for r in rows)    # 종전엔 7.1% 부터 나왔다


# ---------------------------------------------------------------------------
# 7. (9/3 사용자 실측) "배당수익률과 ETF이름만 보여주세요" — 표시 요청을 무시하고 필드 7개를 원문 그대로 쌓던 것
#    '…만 보여/알려/출력…' 앞의 속성 낱말만 골라 상품명 + 그 항목을 한글 라벨로 표시한다. '5개만'·'AA급만'·
#    '국내 ETF만' 같은 개수·범주 제한은 속성 낱말이 아니라 건드리지 않는다(기존 시험지 4문항 유지).
# ---------------------------------------------------------------------------

from engine.answer_service import _fmt_row_only, _only_fields   # noqa: E402


@pytest.mark.parametrize("q, labels", [
    ("배당수익률이 4% 이상인 고배당 ETF 상위 10개를 찾고 배당수익률과 ETF이름만 보여주세요.", ["분배(배당)수익률"]),
    ("이름과 순자산만 알려줘", ["순자산총액"]),
    ("상장일, 운용사 및 총보수만 정리해줘", ["상장일", "운용사", "총보수"]),
    ("수익률만 보여줘", ["1년 수익률"]),
    ("이름만 알려줘", []),                                       # 상품명만 — 속성 없이도 유효
])
def test_only_fields_parses_requested_attributes(q, labels):
    got = _only_fields(q)
    assert got is not None and [lab for _c, lab, _f in got] == labels


@pytest.mark.parametrize("q", [
    "위험등급 1등급(매우 위험)인 국내 ETF 아무거나 5개만 보여주세요",   # v1 B-03: 개수 제한
    "듀레이션이 제일 짧은 채권 5개만 알려줘",                            # r4 R4-01: 개수 제한
    "지금 살 수 있는 원화채권 중 AA급만 알려줘",                         # v3 P-07: 범주 제한
    "국내 ETF만 보여줘",
    "순자산총액 기준으로 국내 ETF 상위 5개 알려줘",                      # 표시 요청 없음
])
def test_only_fields_ignores_count_and_category_limits(q):
    assert _only_fields(q) is None


def test_fmt_row_only_shows_name_and_requested_fields_with_korean_labels():
    row = {"pd_itm_no": "KR7", "pd_abrv_nm": "SOL 팔란티어커버드콜OTM채권혼합", "pd_dvid_yield": 27.783191,
           "pd_dvid_pay_cnt": 12.0, "pd_dvid_pay_months": "January,February,March,April,May,June,July,August,"
           "September,October,November,December", "pd_divd_amt_ann": 226130.17, "drv_risk_grade": 3,
           "pd_net_tamt": 356400000000.0, "pd_net_tamt_krw": "3,564억원"}
    only = _only_fields("배당수익률과 ETF이름만 보여주세요")
    out = _fmt_row_only(row, only)
    assert out == "SOL 팔란티어커버드콜OTM채권혼합 — 분배(배당)수익률 27.78%"
    assert "pd_dvid_pay_months" not in out and "January" not in out and "drv_risk_grade" not in out
    assert _fmt_row_only(row, _only_fields("지급월과 순자산만 알려줘")) == \
        "SOL 팔란티어커버드콜OTM채권혼합 — 분배 지급월 매월 · 순자산총액 3,564억원"


def test_fmt_row_only_falls_back_when_requested_field_missing():
    row = {"pd_abrv_nm": "KODEX 200", "pd_net_tamt": 1.0, "pd_net_tamt_krw": "25.5조원"}
    out = _fmt_row_only(row, _only_fields("배당수익률만 보여줘"))    # 행에 배당수익률 열이 없다
    assert out.startswith("KODEX 200 (")                              # 기본 표시로 되돌아간다


# ---------------------------------------------------------------------------
# 8. (9/3 사용자 지시) 규칙 요약 표기 전반 — 열 이름은 한글 라벨, 숫자는 소수 2자리, 영문 월은 N월/매월,
#    이름의 다른 표기(정식명)는 중복 표시 안 함, 건수·분포 원문 행(n=…)은 문장 노트로만.
# ---------------------------------------------------------------------------

def test_fmt_row_uses_korean_labels_and_rounded_values():
    row = {"pd_itm_no": "KR7", "pd_abrv_nm": "SOL 팔란티어커버드콜OTM채권혼합",
           "pd_nm": "신한 SOL 팔란티어커버드콜OTM채권혼합증권상장지수투자신탁", "pd_dvid_yield": "27.783191",
           "pd_dvid_pay_cnt": "12.0", "pd_dvid_pay_months": "January,February,March,April,May,June,July,August,"
           "September,October,November,December", "pd_divd_amt_ann": 226130.1698681, "drv_risk_grade": "3",
           "pd_net_tamt": 356442628701.0, "pd_net_tamt_krw": "3,564억원"}
    out = _fmt_row(row, max_fields=8)
    assert out == ("SOL 팔란티어커버드콜OTM채권혼합 (정식명 신한 SOL 팔란티어커버드콜OTM채권혼합증권상장지수투자신탁 · "
                   "분배(배당)수익률 27.78% · 연간 분배 지급횟수 12회 · 분배 지급월 매월 · "
                   "연간 추정 분배금(원) 226,130.17 · 위험등급 3등급 · 순자산총액 3,564억원)")
    assert "pd_nm" not in out and "January" not in out and "=" not in out   # 정식명은 라벨로 남긴다(채점 기대 이름 대비)


def test_fmt_row_bond_labels_status_and_date():
    row = {"PD_NO": "KR6000113573", "PD_NM": "스탠다드차타드은행15-07-단(콜)03-20", "PD_ABRV_NM": "스탠다드차타드은행15-07-단(콜)03-20",
           "STD_PD_MCLS_NM": "회사채", "CURR_CD": "KRW", "drv_crd_grd_norm": "AAA", "SRFC_IRT": "7.1",
           "MAT_DT": "2030-07-20", "drv_maturity_status": "active", "drv_is_buyable": "Y", "DUR": "3.8715"}
    out = _fmt_row(row, max_fields=9)
    assert out == ("스탠다드차타드은행15-07-단(콜)03-20 (대분류 회사채 · 통화 KRW · 신용등급 AAA · 표면금리 7.1% · "
                   "만기일 2030-07-20 · 만기상태 상장중 · 매수가능 예 · 듀레이션(년) 3.87)")   # 약칭=정식명이면 중복 생략


def test_fmt_row_unknown_column_keeps_raw_name():
    assert _fmt_row({"pd_abrv_nm": "X", "some_new_col": 1.5}) == "X (some_new_col 1.5)"


def test_op_label_is_korean_head_of_description():
    from engine.answer_service import _op_label
    assert _op_label("etp_by_dividend") == "국내 ETF 분배(배당) 정렬"
    assert _op_label("bond_filter") == "국내채권 필터 목록"
    assert _op_label("no_such_template") == "no_such_template"
