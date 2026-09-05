# -*- coding: utf-8 -*-
"""9/6 주최 과제설명 p.4 예시 회귀 잠금 — "미국 증시에 상장된 주식형 ETF 중에서 총보수가 낮고 운용규모가 큰 상품 3개".

실측(9/6, 기술제안서 시나리오 예시를 뽑다가 발견): 해외 판정이 낱말 '해외'에만 걸려 있어 이 질문이
국내 ETF 표(11절 보수 규칙 etp_low_fee)로 새어 국내 채권 ETF 목록을 자신 있게 답했다. HCX 의도 분석은
'대상=해외ETF'를 맞게 뽑았지만 규칙·검증 우선 원칙에 따라 규칙 판정이 이겼다(형식만 보는 검문의 네 번째 사례).

수정(전부 덧붙이기):
  ① 상장 시장 구절(미국/뉴욕/나스닥/NYSE … + (증시|시장|거래소)? + (에|에서)? + 상장|거래|리스팅)도 해외 판정.
     해외 ETF 마스터 6,037종은 전부 미국 상장(pd_mkt_id='US')이라 상장시장 조건은 표 전체에 해당.
     '미국 ETF'처럼 상장 낱말이 없는 표현은 국내 ETF(미국 지수 추종)와 겹치므로 그대로 둔다.
  ② '보수 낮고 규모 큰' 두 조건 = 총보수 오름차순 순위 + 순자산 내림차순 순위의 합이 작은 순
     (global_etf_filter order='fee_aum' — AI 라우터 목록에서 숨긴 값, 프롬프트 해시 불변).
  ③ 해외 보수 순위 규칙에 자산유형(주식형·채권형…)·투자지역 조건 결합. 상장 시장 구절의 '미국'은 투자지역이 아님.
같은 뜻 다른 표현 2~3개씩 시험한다(TEAM_IMPROVEMENT_GUIDE §5 수정 원칙 2).
"""
import datetime
import hashlib

import duckdb
import pytest

from engine.answer_service import _fmt_row
from engine.policy import load_policy
from engine.router import route
from engine.router_llm import _template_catalog_text
from engine.sql_templates import run_template
from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index

TODAY = datetime.date(2026, 9, 6)
POLICY = load_policy()
OFFICIAL_Q = "미국 증시에 상장된 주식형 ETF 중에서 총보수가 낮고 운용규모가 큰 상품 3개만 비교해주세요."
_CATALOG_SHA256_FROZEN = "a3f8e65498b70ed5264da3fcf84f5336b52cb4b6448e483b0c9bce07ebf25855"

_RANK_SUM_SQL = """
SELECT pd_abrv_nm FROM (
  SELECT pd_abrv_nm,
         RANK() OVER (ORDER BY TRY_CAST(cu_charge_rt AS DOUBLE) ASC)
         + RANK() OVER (ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC) AS s,
         TRY_CAST(du_last_aum AS DOUBLE) AS aum, pd_itm_no
  FROM global_etf
  WHERE wu_inv_ast_type = ? AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0 AND TRY_CAST(du_last_aum AS DOUBLE) > 0)
ORDER BY s, aum DESC, pd_itm_no LIMIT ?"""


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


# ---------------------------------------------------------------------------
# 1. 라우팅 — 공식 예시와 표현 변형
# ---------------------------------------------------------------------------

def test_official_example_routes_to_global_rank_sum(index):
    plan = _route(index, OFFICIAL_Q)
    assert plan.intent == "global_fee_aum_rank"
    assert plan.behavior_hint == "partial"
    c = _call(plan, "global_etf_filter")
    assert c is not None
    assert c.params == {"ast_type": "Equity", "order": "fee_aum", "limit": 3}
    assert _call(plan, "etp_low_fee") is None                 # 종전 오답 경로(국내 보수 필터)
    assert any("순위의 합" in n for n in plan.notes)
    assert any("미국 상장" in n for n in plan.notes)          # 상장시장 조건이 표 전체에 해당함을 밝힘
    assert plan.hints.get("skip_generation") is True
    assert plan.hints.get("display_rows") == 3


@pytest.mark.parametrize("q, ast, limit", [
    ("미국에 상장된 ETF 중 운용 규모가 크고 보수가 저렴한 상품 3개 비교해줘", None, 3),
    ("해외 주식형 ETF 중 총보수 낮고 순자산 큰 5개", "Equity", 5),
    ("나스닥에 상장된 채권형 ETF 중 보수가 낮고 규모가 큰 상품 3개", "Bond", 3),
    ("뉴욕 증시에서 거래되는 주식형 ETF 가운데 총보수는 낮은데 AUM은 큰 상품 3개", "Equity", 3),
])
def test_fee_low_and_big_aum_variants(index, q, ast, limit):
    plan = _route(index, q)
    assert plan.intent == "global_fee_aum_rank", plan.intent
    c = _call(plan, "global_etf_filter")
    assert c.params.get("order") == "fee_aum"
    assert c.params.get("limit") == limit
    assert c.params.get("ast_type") == ast
    assert "region_pattern_raw" not in c.params              # 상장 시장의 '미국·뉴욕·나스닥'은 투자지역 조건이 아님


def test_listing_phrase_counts_global_table(index):
    plan = _route(index, "미국 증시에 상장된 ETF는 몇 개야?")
    assert plan.intent == "global_count"
    assert _call(plan, "global_etf_count") is not None


def test_fee_rank_keeps_asset_type_and_investment_region(index):
    plan = _route(index, "미국에 투자하는 해외 채권형 ETF 중 총보수 낮은 3개")
    assert plan.intent == "global_fee_rank"
    c = _call(plan, "global_etf_filter")
    assert c.params == {"ast_type": "Bond", "region_pattern_raw": "United States", "order": "fee_asc", "limit": 3}


@pytest.mark.parametrize("q, intent", [
    ("TIME 미국나스닥100액티브은 언제 상장됐어?", "product_detail"),      # v2 L-04: 상품명 속 '미국…상장' 은 해외 판정 아님
    ("KODEX 미국S&P500 상장일 알려줘", "product_detail"),
    ("미국 나스닥100 추종하는 국내 ETF 알려줘", "index_products"),
    ("삼성전자가 포함된 ETF 알려줘", "constituent_reverse"),
])
def test_domestic_questions_unaffected(index, q, intent):
    plan = _route(index, q)
    assert plan.intent == intent, plan.intent
    assert _call(plan, "global_etf_filter") is None


# ---------------------------------------------------------------------------
# 2. 조회문 — 순위 합 정렬(숨김값 fee_aum)과 표시 열
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ast", ["Equity", "Bond"])
def test_rank_sum_order_matches_independent_sql(con, ast):
    rows = run_template(con, "global_etf_filter", {"order": "fee_aum", "ast_type": ast, "limit": 3}).rows
    names = [r["pd_abrv_nm"] for r in rows]
    expected = [r[0] for r in con.execute(_RANK_SUM_SQL, [ast, 3]).fetchall()]
    assert names == expected
    assert all(float(r["cu_charge_rt"]) > 0 and float(r["du_last_aum"]) > 0 for r in rows)   # 값 0·결측 제외
    assert "cu_base_index" in rows[0]                          # 비교 항목(총보수·순자산·기초지수) 표시용 열


def test_rank_sum_equity_top_is_large_and_cheap(con):
    rows = run_template(con, "global_etf_filter", {"order": "fee_aum", "ast_type": "Equity", "limit": 3}).rows
    assert rows[0]["pd_abrv_nm"] == "VOO"                      # 8/23 기준 실측: 순자산 1위 · 총보수 0.02%
    assert all(float(r["cu_charge_rt"]) <= 0.05 for r in rows)
    assert all(float(r["du_last_aum"]) >= 1e11 for r in rows)  # 1,000억 달러 이상


def test_fee_asc_order_unchanged(con):
    rows = run_template(con, "global_etf_filter", {"order": "fee_asc", "ast_type": "Equity", "limit": 3}).rows
    min_fee = con.execute("SELECT min(TRY_CAST(cu_charge_rt AS DOUBLE)) FROM global_etf "
                          "WHERE wu_inv_ast_type='Equity' AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0").fetchone()[0]
    assert float(rows[0]["cu_charge_rt"]) == pytest.approx(min_fee)


def test_default_order_still_aum_desc(con):
    rows = run_template(con, "global_etf_filter", {"ast_type": "Equity", "limit": 3}).rows
    aums = [float(r["du_last_aum"]) for r in rows]
    assert aums == sorted(aums, reverse=True)


def test_row_display_shows_comparison_fields(con):
    row = run_template(con, "global_etf_filter", {"order": "fee_aum", "ast_type": "Equity", "limit": 1}).rows[0]
    text = _fmt_row(row, focus=("cu_charge_rt", "du_last_aum"))
    assert text.startswith("VOO (")
    assert "총보수" in text and "기초지수" in text and "USD" in text


def test_llm_catalog_unchanged_by_hidden_order_value():
    """order 파라미터 전체가 LLM_HIDDEN_PARAMS 에 있어 'fee_aum' 추가가 프롬프트를 바꾸지 않는다(v1 H-17 동결)."""
    assert hashlib.sha256(_template_catalog_text().encode("utf-8")).hexdigest() == _CATALOG_SHA256_FROZEN
