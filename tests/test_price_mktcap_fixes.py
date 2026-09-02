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
    assert out.startswith("KODEX CD금리액티브(합성) (du_clpr=1,075,450")


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
    assert out.startswith("KODEX 200 (mkt_cap_krw=25.9조원")


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
    from engine.sql_templates import LLM_HIDDEN_ENUM_VALUES, TEMPLATES
    text = _template_catalog_text()
    for (tid, pname), hidden in LLM_HIDDEN_ENUM_VALUES.items():
        enum = next(p.enum for p in TEMPLATES[tid].params if p.name == pname)
        assert all(v in enum for v in hidden)                 # 숨긴 값은 여전히 유효(규칙 라우터 호출용)
        line = next(l for l in text.splitlines() if l.startswith(f"- {tid}:"))
        assert all(f"'{v}'" not in line for v in hidden), line
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN, (
        "AI 라우터 목록(프롬프트)이 바뀌었다 — 위 주석대로 H-17·T-09 를 HCX 로 재검한 뒤 해시를 갱신할 것")
