# -*- coding: utf-8 -*-
"""⑧ 2차 다듬기 — 블라인드 v2(8/22)가 드러낸 실패 유형의 회귀 잠금.

1. 이름 경계: 공백 제거 대조에서 "TIGER 코스피300"→'TIGER 코스피', "애플파이"→'애플', 펀드명 속 별칭
   'KB스타'가 정확 일치로 잡히던 것(함정 오답·엉뚱한 상품)을 막는다.
4. 거절 문장 통일: 행위 요청·실시간 지수·배당락일·표면금리 100% 초과·미존재 운용사·해외 ETF 위험등급은
   라우터가 거절로 확정하고, 생성기가 자기 말로 거절하면 출구에서 정해진 거절문으로 바꾼다.
"""
import os

import duckdb
import pytest

from engine.answer_service import _looks_like_free_refusal, answer_question
from engine.channels import RuntimeContext
from engine.policy import load_policy
from engine.router import route
from pipeline.entity_index import DB_PATH_DEFAULT, EntityIndex, EntityRef, build_entity_index

TODAY = "2026-08-22"
POLICY = load_policy()


@pytest.fixture(scope="module")
def index():
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    return build_entity_index(con)


@pytest.fixture(scope="module")
def ctx(index):
    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    return RuntimeContext(con=con, index=index, policy=POLICY)


# ---------------------------------------------------------------------------
# 1. 이름 경계
# ---------------------------------------------------------------------------

def test_scan_rejects_name_followed_by_digits_or_letters():
    idx = EntityIndex()
    idx.add("TIGER 코스피", EntityRef("product_kr_etp", "KR7277630000", "TIGER 코스피", "PREF01N001"))
    idx.add("KODEX 200", EntityRef("product_kr_etp", "KR7069500007", "KODEX 200", "PREF01N001"))
    assert idx.scan("TIGER 코스피300 순자산 얼마야?") == []          # 이름 뒤 숫자 → 다른 이름
    assert [n for n, _ in idx.scan("TIGER 코스피 순자산 얼마야?")] == ["tiger코스피"]
    assert idx.scan("KODEX 200TR 알려줘") == []                      # 이름 뒤 영문 → 다른 이름
    assert [n for n, _ in idx.scan("KODEX 200은 어때?")] == ["kodex200"]   # 조사 → 경계


def test_scan_hangul_continuation_needs_particle_or_question_word():
    idx = EntityIndex()
    idx.add("애플", EntityRef("constituent", "US0378331005", "APPLE INC", "constituent_aliases"))
    idx.add("KB스타", EntityRef("company", "KB", "KB", "alias_dictionary"))
    fund = "KB스타골드특별자산투자신탁(금-파생재간접형)C클래스"
    idx.add(fund, EntityRef("product_fund", "F1", fund, "PRFD01N001"))
    assert idx.scan("애플파이 주식을 담은 ETF 있어?") == []            # '파이' — 이름의 연속
    assert [n for n, _ in idx.scan("애플 주식을 담은 ETF 있어?")] == ["애플"]
    assert [n for n, _ in idx.scan("애플을 담은 ETF")] == ["애플"]
    hits = idx.scan(f"{fund} 펀드 위험등급이 몇 등급이야?")
    assert [r[0].kind for _n, r in hits] == ["product_fund"]           # 별칭 'KB스타'가 아니라 펀드명


def test_scan_keeps_preferred_share_convention():
    idx = EntityIndex()
    idx.add("삼성전자", EntityRef("constituent", "005930", "삼성전자", "KRX-PDF"))
    idx.add("삼성전자우", EntityRef("constituent", "005935", "삼성전자우", "KRX-PDF"))
    assert [n for n, _ in idx.scan("삼성전자 우선주를 담은 ETF도 있어?")] == ["삼성전자우"]


# ---------------------------------------------------------------------------
# 4. 거절 확정 규칙 + 거절 문장 통일
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question,intent", [
    ("TIGER 200 10주 매수 주문 넣어줘", "action_request"),
    ("오늘 코스피 지수 몇이야?", "time_violation"),
    ("WON 미국빌리어네어 배당락일이 언제야?", "unsupported_field"),
    ("표면금리 150%인 채권 있어?", "invalid_value"),
    ("한라산자산운용이 운용하는 ETF 알려줘", "existence_check"),
    ("Alger 35 ETF 위험등급 몇 등급이야?", "unsupported_field"),
])
def test_new_trap_rules_refuse(index, question, intent):
    plan = route(question, index, policy=POLICY, today=TODAY)
    assert plan.behavior_hint == "refuse" and plan.intent == intent


def test_company_formal_alias_grounds_in_router(index):
    plan = route("미래에셋자산운용이 운용하는 ETF 중에 순자산이 제일 큰 건 뭐야?", index, policy=POLICY, today=TODAY)
    kinds = [r.kind for _n, refs in plan.entities for r in refs]
    assert "company" in kinds and plan.behavior_hint == "answer"


def test_free_refusal_detector():
    assert _looks_like_free_refusal("죄송합니다. 조건에 맞는 항목을 데이터에서 확인하지 못했습니다.")
    assert _looks_like_free_refusal("질문에 대한 답변을 찾을 수 없습니다.\n\n근거·기준일: 2026-07-11")
    assert not _looks_like_free_refusal("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다.\n- 사유: …")
    assert not _looks_like_free_refusal("결과 3건\n1. KODEX 200\n2. TIGER 200\n3. RISE 200")


def test_trap_answers_start_with_fixed_refusal(ctx):
    """함정은 어느 경로(규칙·폴백)로 가든 정해진 거절문으로 시작한다 — 채점 인정 조건."""
    for q in ("TIGER 코스피300 순자산 얼마야?", "애플파이 주식을 담은 ETF 있어?",
              "한라산자산운용이 운용하는 ETF 알려줘", "TIGER 200 10주 매수 주문 넣어줘"):
        out = answer_question(q, ctx, today=TODAY)
        assert out["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"), q


# ---------------------------------------------------------------------------
# 2·3·5·6. 속성 규칙 · 운용사 결합 · 띄어쓰기 · 잔여
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question,intent,op", [
    ("하나캐피탈390-6 만기일이 언제야?", "bond_detail", "bond_detail"),
    ("신한카드2276-2 신용등급이 뭐야?", "bond_detail", "bond_detail"),
    ("KB스타골드특별자산투자신탁(금-파생재간접형)C클래스 펀드 위험등급이 몇 등급이야?", "fund_detail", "fund_detail"),
    ("키움투자자산운용이 운용하는 국내 ETF는 몇 개야?", "company_product_count", "mgmt_product_count"),
    ("미래에셋자산운용이 운용하는 ETF 중에 순자산이 제일 큰 건 뭐야?", "company_products_ranked", "etp_by_mgmt"),
    ("신한자산운용의 반도체 ETF 있어?", "company_products_ranked", "etp_by_mgmt"),
    ("순자산 상위 3개 국내 ETF의 운용사를 각각 알려줘", "etp_ranking", "etp_top_aum"),
    ("국내 ETF랑 ETN 중에 어느 쪽 상품 수가 더 많아?", "etp_count", "etp_count"),
    ("해외에 투자하는 주식형 공모펀드 중에서 순자산 큰 순으로 5개만 알려줘", "fund_filter", "fund_filter"),
])
def test_phase_b_rules_route(index, question, intent, op):
    plan = route(question, index, policy=POLICY, today=TODAY)
    assert plan.intent == intent and any(c.op == op for c in plan.calls), (plan.intent, [c.op for c in plan.calls])


def test_fund_filter_region_and_order(index):
    plan = route("해외에 투자하는 주식형 공모펀드 중에서 순자산 큰 순으로 5개만 알려줘", index, policy=POLICY, today=TODAY)
    params = next(c.params for c in plan.calls if c.op == "fund_filter")
    assert params.get("region") == "해외" and params.get("order") == "aum" and params["limit"] == 5


def test_bond_list_defaults_to_unmatured(index):
    plan = route("표면금리가 6% 이상인 회사채 알려줘", index, policy=POLICY, today=TODAY)
    params = next(c.params for c in plan.calls if c.op == "bond_filter")
    assert params.get("maturity_status") == "active" and params.get("bond_class") == "회사채"


def test_nospace_questions_are_not_refused(ctx):
    """띄어쓰기 없는 질문(v2 P-02/05)은 문장 전체를 상품명으로 보고 거절하지 않는다."""
    for q in ("레버리지ETF찾아줘", "현재판매가능한원화채권중신용등급AA이상인종목을알려줘"):
        out = answer_question(q, ctx, today=TODAY)
        assert not out["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"), q


def test_attribute_notes_from_detail_rows():
    from engine.answer_service import attribute_notes
    bond = [{"PD_NO": "X", "MAT_DT": "2026-11-03", "drv_crd_grd_norm": "AA-", "PD_RISK_NM": "낮은위험(5등급)", "SRFC_IRT": "4.5"}]
    notes = attribute_notes("하나캐피탈390-6 만기일이 언제야?", "bond_detail", bond)
    assert notes == ["만기일: 2026-11-03"]
    notes = attribute_notes("신한카드2276-2 신용등급이 뭐야?", "bond_detail", bond)
    assert "신용등급(대표): AA-" in notes            # 평가사별 컬럼은 8/27 재배포본에서 삭제
    notes = attribute_notes("이 채권 위험등급 알려줘", "bond_detail", bond)
    assert "상품위험등급명: 낮은위험(5등급)" in notes
    etp = [{"pd_lstg_dt": "20221220", "drv_instrument_type": "ETF"}]
    assert attribute_notes("KIWOOM 미국S&P500은 언제 상장됐어?", "etp_detail", etp) == ["상장일(원천 항목명: 상품거래가능일자): 2022-12-20"]
    fund = [{"drv_risk_grade": "2", "zrin_fd_ivst_risk_grd_nm": "높은 위험"}]
    assert attribute_notes("펀드 위험등급이 몇 등급이야?", "fund_detail", fund) == ["위험등급(1=매우 높음~6=매우 낮음): 2등급(높은 위험)"]
    assert attribute_notes("아무 질문", "etp_name_search", etp) == []


# ---------------------------------------------------------------------------
# ⑧ 3차 (8/26) — 블라인드 v2 잔여 7유형. 규칙마다 '같은 뜻 다른 표현'도 함께
# 시험한다(시험문제 맞춤 규칙이 아니라 일반 정책임을 잠그는 장치 — 8/26 조사 결론).
# ---------------------------------------------------------------------------

def test_buyable_matches_spacing_variants(index):
    """P-02: '판매가능한'(붙임)·'매수가능'·'살수있는' 전부 매수가능 필터로 (띄어쓰기 무시)."""
    for q in ("현재판매가능한원화채권중신용등급AA이상인종목을알려줘",
              "지금 매수가능한 원화 채권 알려줘",
              "지금 살수있는 원화 채권 알려줘"):
        plan = route(q, index, policy=POLICY, today=TODAY)
        params = next(c.params for c in plan.calls if c.op == "bond_filter")
        assert params.get("buyable_only") == "Y", (q, params)


def test_rating_band_alone_is_a_band():
    """O-07: 이상/이하 없는 'AA급'은 AA+·AA·AA- 묶음(서열 2~4) — AAA 미포함.
    'AA 이상'(문자 그대로)과 'BBB급'(다른 등급대) 해석은 그대로다."""
    from engine.router import rating_condition
    cond, notes = rating_condition("신용등급이 AA급이면서 표면금리 4% 이상인 원화채권", POLICY)
    assert cond == {"min_rating_rank": 2, "max_rating_rank": 4} and any("묶음" in n for n in notes)
    cond2, _ = rating_condition("신용등급 AA 이상인 채권 알려줘", POLICY)
    assert cond2 == {"max_rating_rank": 3}
    cond3, _ = rating_condition("BBB급 회사채 알려줘", POLICY)
    assert cond3 == {"min_rating_rank": 8, "max_rating_rank": 10}


def test_rating_band_reaches_bond_filter(index):
    """O-07: 하한(min_rating_rank)이 목록 조회(bond_filter)까지 실제로 전달된다."""
    plan = route("원화채권 중 신용등급이 AA급이면서 표면금리 4% 이상인 것 알려줘", index, policy=POLICY, today=TODAY)
    params = next(c.params for c in plan.calls if c.op == "bond_filter")
    assert params.get("min_rating_rank") == 2 and params.get("max_rating_rank") == 4
    assert params.get("min_coupon") == 4.0


def test_subsidiary_uses_prefix_holders(index):
    """O-05·H-01: 자회사 질의는 회사명 접두 집계(순자산 큰 순) — 어떤 회사든 같은 규칙."""
    for q, prefix in (("LG의 자회사를 편입한 ETF 중 순자산이 큰 상품의 위험요인 알려줘", "LG"),
                      ("삼성의 자회사를 담은 ETF 알려줘", "삼성")):
        plan = route(q, index, policy=POLICY, today=TODAY)
        params = next(c.params for c in plan.calls if c.op == "constituent_prefix_holders_by_aum")
        assert params["prefix_raw"].casefold() == prefix.casefold(), (q, params)
        assert plan.hints.get("order") == "aum"


def test_constituent_like_question_prefers_reverse_lookup(index):
    """O-06: 'X처럼 …을 담은 ETF'(빗댐 표현)는 종목 역질의(규칙 6) — 상품명 조각(6.1)이 가로채지 않는다.
    빗댐 없이 종목+테마가 함께 오면(O-03) 기존대로 상품명 조각 경로, 조각이 종목명을
    포함하면('애플 밸류체인') 빗댐이어도 상품 구성 질의다."""
    plan = route("캠브리콘처럼 중국 AI 반도체 기업을 담은 국내 ETF 알려줘", index, policy=POLICY, today=TODAY)
    assert plan.intent == "constituent_reverse"
    assert any(c.op == "constituent_holders" for c in plan.calls)
    plan1b = route("캠브리콘 같은 중국 반도체주 들어간 국내 ETF 있어?", index, policy=POLICY, today=TODAY)
    assert plan1b.intent == "constituent_reverse"
    plan2 = route("애플 밸류체인에 투자하는 ETF가 있다던데, 뭘 담고 있어?", index, policy=POLICY, today=TODAY)
    assert plan2.intent == "product_constituents_by_name"
    # v2 O-03(빗댐 아님): 종목 역질의로 가되 테마 낱말('2차전지')이 상품명 필터로 붙는다
    plan3 = route("에코프로비엠이 편입된 국내 2차전지 ETF 알려줘", index, policy=POLICY, today=TODAY)
    assert plan3.intent == "constituent_reverse"
    p3 = next(c.params for c in plan3.calls if c.op == "constituent_holders")
    assert p3.get("name_pattern_raw") == "2차전지", p3
    # 빗댐(O-06)의 테마('반도체')는 종목 쪽 수식 — 필터로 쓰지 않는다
    pl = route("캠브리콘처럼 중국 AI 반도체 기업을 담은 국내 ETF 알려줘", index, policy=POLICY, today=TODAY)
    pc = next(c.params for c in pl.calls if c.op == "constituent_holders")
    assert "name_pattern_raw" not in pc, pc


def test_theme_related_questions_use_name_search(index):
    """M-12: 'X 관련/테마 ETF'는 사전에 없는 낱말(원자력)도 이름 검색 + 의미 검색 규칙으로 —
    HCX 라우팅 변동에 기대지 않는다. 함정(kimi 관련)은 여전히 거절."""
    for q, term in (("원자력 관련 국내 ETF 알려줘", "원자력"),
                    ("바이오 테마 국내 ETF 알려줘", "바이오")):
        plan = route(q, index, policy=POLICY, today=TODAY)
        assert plan.intent == "etp_name_search", (q, plan.intent)
        params = next(c.params for c in plan.calls if c.op == "etp_name_search")
        assert params["pattern_raw"] == term, (q, params)
        assert any(c.channel == "vector" for c in plan.calls)
    trap = route("kimi 관련 투자 상품 있어?", index, policy=POLICY, today=TODAY)
    assert trap.behavior_hint == "refuse"


def test_constituent_reverse_mgmt_filter(index):
    """H-08: '…담은 ETF 중에 ○○운용이 운용하는' — 운용사 필터가 SQL 로 걸리고,
    운용사 말이 없는 기본 역질의(M-01)는 필터가 없다."""
    plan = route("STX엔진 담은 ETF 중에 미래에셋자산운용이 운용하는 거 있어?", index, policy=POLICY, today=TODAY)
    params = next(c.params for c in plan.calls if c.op == "constituent_holders")
    assert params.get("mgmt"), params
    assert plan.hints.get("mgmt_filter", {}).get("key") == params["mgmt"]
    plan2 = route("삼성전자가 포함된 ETF 알려줘", index, policy=POLICY, today=TODAY)
    ps = [c.params for c in plan2.calls if c.op == "constituent_holders"]
    assert ps and all("mgmt" not in p for p in ps)


def test_etp_count_amount_filter(index):
    """O-09: '순자산 1조원 넘는(초과)/5000억 이상' 금액 조건이 개수 조회에 걸린다.
    금액 말이 없는 개수 질문(L-13)은 그대로 전체 카운트."""
    plan = route("국내 ETF 중에 순자산이 1조원 넘는 상품은 몇 개야?", index, policy=POLICY, today=TODAY)
    params = next(c.params for c in plan.calls if c.op == "etp_count")
    assert params == {"min_aum_gt": 1e12}
    plan2 = route("순자산 5000억 이상인 국내 ETF는 몇 개야?", index, policy=POLICY, today=TODAY)
    params2 = next(c.params for c in plan2.calls if c.op == "etp_count")
    assert params2 == {"min_aum_ge": 5000 * 1e8}
    plan3 = route("국내에 상장된 ETN은 전부 몇 개야?", index, policy=POLICY, today=TODAY)
    assert next(c.params for c in plan3.calls if c.op == "etp_count") == {}


def test_fund_filter_lists_on_sale_first(ctx, index):
    """O-08: 정렬 미지정 펀드 목록은 판매중 상품 먼저 — 순자산 정렬(M-13) 요청은 그대로."""
    out = answer_question("공모펀드 중에 국내에 투자하는 채권형 펀드 알려줘", ctx, today=TODAY)
    first_row = next(l for l in out["answer"].splitlines() if l.strip().startswith("1."))
    assert "판매중" in first_row, first_row
    plan = route("해외에 투자하는 주식형 공모펀드 중에서 순자산 큰 순으로 5개만 알려줘", index, policy=POLICY, today=TODAY)
    assert next(c.params for c in plan.calls if c.op == "fund_filter").get("order") == "aum"


# ---------------------------------------------------------------------------
# ⑧ 4차 (8/26) — 블라인드 v3(64/80) 실패 16건의 회귀 잠금. 규칙마다 표현 변형 동반.
# ---------------------------------------------------------------------------

def test_trap_vocabulary_expansion(index):
    """v3 T-04/06/09/10/12: 방어 규칙의 어휘 폭 확장 — 배당·공매도, 미래 상장, 종목 시세, 행위."""
    for q, intent in (("KODEX 200 배당락일 알려줘", "unsupported_field"),
                      ("TIGER 200 분배락 일자 알려줘", "unsupported_field"),
                      ("KODEX 200 공매도 잔고 알려줘", "unsupported_field"),
                      ("다음 주에 상장하는 국내 ETF 뭐야?", "time_violation"),
                      ("내일 출시되는 ETF 있어?", "time_violation"),
                      ("지금 삼성전자 주가 얼마야?", "time_violation"),
                      ("현재 에코프로 종가 알려줘", "time_violation"),
                      ("미래에셋증권 계좌 개설해줘", "action_request"),
                      ("펀드 해지해줘", "action_request")):
        plan = route(q, index, policy=POLICY, today=TODAY)
        assert plan.behavior_hint == "refuse" and plan.intent == intent, (q, plan.intent)
    plan = route("고배당 ETF 알려줘", index, policy=POLICY, today=TODAY)   # '배당' 단독=테마, 정상
    assert plan.behavior_hint != "refuse"
    # 8/27 재배포본: 분배(배당) 필드 신설 — 수익률·분배금 질의는 거절이 아니라 조회다
    plan = route("KODEX 2차전지산업 배당수익률이 얼마야?", index, policy=POLICY, today=TODAY)
    assert plan.behavior_hint != "refuse" and plan.intent == "product_detail"
    plan = route("분배금 많이 주는 ETF 알려줘", index, policy=POLICY, today=TODAY)
    assert plan.intent == "etp_dividend_rank" and plan.behavior_hint == "partial"


def test_brand_token_boundary():
    """v3 M-04: 'HK'⊂'삼익THK' 같은 부분 문자열 오인 방지 — 영문·숫자 경계 검사."""
    from engine.router import find_brand_token
    assert find_brand_token("삼익THK을 편입한 ETF는 총 몇 개야?") is None
    assert find_brand_token("KODEX 250 ETF 정보 알려줘") == "KODEX"
    assert find_brand_token("TIGER 코스피300 순자산 얼마야?") == "TIGER"


def test_mixed_script_constituent_not_refused(ctx):
    out = answer_question("삼익THK을 편입한 ETF는 총 몇 개야?", ctx, today=TODAY)
    assert not out["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"), out["answer"][:120]


def test_rating_band_spaced():
    """v3 P-09: 'AA 등급대'처럼 띄어 써도 등급대 묶음(서열 2~4)으로 해석."""
    from engine.router import rating_condition
    cond, _ = rating_condition("원화 채권에서 AA 등급대만 골라줘, 살 수 있는 걸로", POLICY)
    assert cond == {"min_rating_rank": 2, "max_rating_rank": 4}
    cond2, _ = rating_condition("신용등급 AA 이상인 채권 알려줘", POLICY)   # 기존 해석 유지
    assert cond2 == {"max_rating_rank": 3}


def test_fee_combination_rules(index):
    """v3 C-03/C-13/H-04: 총보수 최저 결합 — 종목 편입×보수 · 운용사×보수 · 위험등급×보수."""
    plan = route("SK하이닉스를 담은 ETF 중에서 총보수가 가장 낮은 상품은 뭐야?", index, policy=POLICY, today=TODAY)
    p = next(c.params for c in plan.calls if c.op == "constituent_holders")
    assert p.get("order") == "fee" and plan.behavior_hint == "partial"
    plan2 = route("미래에셋자산운용 ETF 중에서 총보수가 가장 낮은 상품 알려줘", index, policy=POLICY, today=TODAY)
    p2 = next(c.params for c in plan2.calls if c.op == "etp_by_mgmt")
    assert p2.get("order") == "fee" and plan2.behavior_hint == "partial"
    plan2b = route("KB자산운용에서 보수 제일 저렴한 ETF 뭐야?", index, policy=POLICY, today=TODAY)
    assert any(c.op == "etp_by_mgmt" and c.params.get("order") == "fee" for c in plan2b.calls)
    plan3 = route("위험등급이 3등급인 국내 ETF 중에서 총보수가 0.3% 미만인 것 알려줘", index, policy=POLICY, today=TODAY)
    p3 = next(c.params for c in plan3.calls if c.op == "etp_low_fee")
    assert p3.get("min_grade") == 3 and p3.get("max_grade") == 3


def test_bond_coupon_order(index):
    """v3 C-06: '표면금리 제일 높은/낮은' 정렬이 목록 조회에 걸린다."""
    plan = route("AA급 원화채권 중에 표면금리가 제일 높은 종목이 뭐야?", index, policy=POLICY, today=TODAY)
    p = next(c.params for c in plan.calls if c.op == "bond_filter")
    assert p.get("order") == "coupon" and p.get("min_rating_rank") == 2 and p.get("max_rating_rank") == 4
    plan2 = route("표면금리 가장 낮은 회사채 알려줘", index, policy=POLICY, today=TODAY)
    p2 = next(c.params for c in plan2.calls if c.op == "bond_filter")
    assert p2.get("order") == "coupon_asc"


def test_theme_top_and_intersection_top(index):
    """v3 C-08/C-09: 테마×순위×구성 연결 · 교집합×순자산."""
    plan = route("2차전지 ETF 중에서 순자산이 제일 큰 상품의 구성종목 상위 3개 알려줘", index, policy=POLICY, today=TODAY)
    assert plan.intent == "theme_top_constituents"
    p = next(c.params for c in plan.calls if c.op == "etp_pattern_top_constituents")
    assert p["top_etfs"] == 1 and p["per_etf"] == 3
    plan1b = route("반도체 ETF 중 순자산 가장 큰 상품엔 어떤 종목이 담겨 있어?", index, policy=POLICY, today=TODAY)
    assert plan1b.intent == "theme_top_constituents"
    plan2 = route("삼성전자랑 SK하이닉스 둘 다 담은 ETF 중 순자산이 가장 큰 건 뭐야?", index, policy=POLICY, today=TODAY)
    assert plan2.intent == "constituent_intersection_top_aum"
    plan2b = route("현대차와 기아를 모두 편입한 ETF 중에 규모가 제일 큰 상품은 뭐야?", index, policy=POLICY, today=TODAY)
    assert plan2b.intent == "constituent_intersection_top_aum"


def test_top_rank_attribute_notes_pure():
    """v3 C-05/C-10: 정렬 목록 1위 행의 요청 속성을 노트로 명시(3단 질문의 마지막 고리)."""
    from types import SimpleNamespace
    from engine.answer_service import top_rank_attribute_notes
    o = SimpleNamespace(ok=True, channel="sql", op="constituent_holders",
                        rows=[{"pd_abrv_nm": "KODEX 200", "drv_risk_grade": "2",
                               "mgmt": "삼성", "pd_lstg_dt": "2002-10-14"}])
    result = SimpleNamespace(outcomes=[o])
    notes = top_rank_attribute_notes("현대차를 편입한 ETF 중 순자산 1위 상품의 위험등급은 몇 등급이야?", result)
    assert any("2등급" in n and "KODEX 200" in n for n in notes)
    notes2 = top_rank_attribute_notes("키움투자자산운용 ETF 중 순자산 1위 상품의 상장일 알려줘", result)
    assert any("상장일" in n and "2002-10-14" in n for n in notes2)
    notes3 = top_rank_attribute_notes("삼성전자를 담은 ETF 중에서 순자산이 제일 큰 상품의 운용사를 알려줘", result)
    assert any("운용사" in n and "삼성" in n for n in notes3)
    assert top_rank_attribute_notes("삼성전자 담은 ETF 알려줘", result) == []   # 순위 낱말 없으면 침묵


def test_unknown_hangul_stock_holder_refused(index, ctx):
    """v2 T-11 재발 방지: 'X 주식을 담은'에서 X 가 미등록 한글 토큰(부분 일치 0)이면 규칙이 거절 확정
    — HCX 경로 변동에 노출되지 않는다. 실존 종목·별칭(구글·애플)은 그대로 답변."""
    plan = route("애플파이 주식을 담은 ETF 있어?", index, policy=POLICY, today=TODAY)
    assert plan.behavior_hint == "refuse" and plan.intent == "existence_check"
    out = answer_question("애플파이 주식을 담은 ETF 있어?", ctx, today=TODAY)
    assert out["answer"].startswith("요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다")
    for q in ("구글 주식을 담은 국내 상장 ETF 알려주세요", "애플 주식을 담은 ETF 있어?",
              "삼성전자 주식을 편입한 ETF 알려줘"):
        plan2 = route(q, index, policy=POLICY, today=TODAY)
        assert plan2.behavior_hint != "refuse", q


def test_industry_sector_wording_uses_name_search(index):
    """v3 P-21: 'X 산업/섹터/분야에 투자하는'도 관련/테마와 같은 이름+의미 검색 규칙."""
    for q, term in (("게임 산업에 투자하는 국내 ETF 있어?", "게임"),
                    ("금융 섹터에 투자하는 국내 ETF 알려줘", "금융")):
        plan = route(q, index, policy=POLICY, today=TODAY)
        assert plan.intent == "etp_name_search", (q, plan.intent)
        params = next(c.params for c in plan.calls if c.op == "etp_name_search")
        assert params["pattern_raw"] == term
