# -*- coding: utf-8 -*-
"""
실전 미러 평가셋 생성기 — 실전과 같은 구성·같은 채점 기준의 대표 지표 35문항 (8/26).

무엇: 실전 평가는 일반 30문항(하10·중10·상10) + 답변불가 5문항이고, 채점은
      "기대 개체·관계 포함 여부 + 근거 유의성"(8/6 설명회)이다. 이 세트는 그 구성을
      그대로 본떠, 문항 전부를 **공식 예시 8개의 '유형'에서만** 뽑는다(8/13 재확인:
      예시는 유형 예시 — 같은 유형·다른 대상이 출제된다, SEED_QUESTIONS.md).
왜  : 자체 무작위 세트(v2·v3)는 시스템 결함을 찾는 도구이고, 이 세트는 "실전과 가장
      비슷한 잣대"다(8/26 조사 — 출제자 정렬). 앞으로 대표 지표는 이 세트 점수로 삼는다.
채점: 기존 4축(태도·근거·내용·시간)에 더해, 관계가 핵심인 문항은 **관계 낱말 검사**
      (운용/편입·담/추종 …)를 넣어 주최 채점 문구("개체·관계 포함")를 명시적으로 미러한다.
실행: python evalset/make_evalset_mirror.py → evalset_mirror.jsonl, checks_mirror.jsonl
"""
import io
import json
import os
import random
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import duckdb  # noqa: E402
from pipeline.entity_index import DB_PATH_DEFAULT  # noqa: E402

SEED = 20260827
OUT_EVAL = os.path.join(HERE, "evalset_mirror.jsonl")
OUT_CHECKS = os.path.join(HERE, "checks_mirror.jsonl")

rng = random.Random(SEED)
con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
items, checks = [], []


def q(sql):
    return con.execute(sql).fetchall()


def esc(s):
    return str(s).replace("'", "''")


def add(id_, level, category, question, behavior, gold, basis, check_list, channels=None):
    items.append({"id": id_, "level": level, "category": category, "question": question,
                  "channels": channels or [], "behavior": behavior, "gold": gold, "basis": basis})
    checks.append({"id": id_, "checks": check_list})


def src(*tables):
    return {"type": "evidence_source_any", "name": "근거 출처", "sources": list(tables)}


def rel(*terms):
    """주최 채점 문구('기대 개체·관계 포함')의 관계 축 — 답변 문장에 관계 낱말이 있는지."""
    return {"type": "answer_has_any", "name": "관계 표현", "terms": list(terms)}


AUM_COLS = ("du_last_aum", "pd_net_tamt")

# ---------------------------------------------------------------------------
# 하 10 — 공식 하-1 유형(조건 필터 단문) + 주최 채점 예시(순자산 상위 5)
# ---------------------------------------------------------------------------
add("MR-L-01", "하", "채권/필터", "현재 판매 가능한 원화채권 중 A+ 이상 종목 알려줘", "answer",
    "KRW·매수가능·서열<=5(문자 그대로)", "공식 하 예시의 등급 변형",
    [{"type": "sql_names", "name": "A+ 이상·매수가능·만기미경과", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND drv_is_buyable='Y' AND drv_maturity_status='active' AND TRY_CAST(drv_crd_grd_rank AS INT)<=5"},
     src("PRBD01N001")])
add("MR-L-02", "하", "채권/필터", "현재 매수 가능한 원화채권 중 BBB급 종목 알려줘", "answer",
    "KRW·매수가능·BBB 등급대(서열 8~10)", "공식 하 예시의 등급대 변형",
    [{"type": "sql_names", "name": "BBB급·매수가능", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND drv_is_buyable='Y' AND drv_maturity_status='active' AND TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 8 AND 10"},
     src("PRBD01N001")])
add("MR-L-03", "하", "채권/필터", "지금 매수 가능한 국공채 알려줘", "answer", "국공채·매수가능", "대분류 변형",
    [{"type": "sql_names", "name": "국공채·매수가능", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='국공채' AND drv_is_buyable='Y' AND drv_maturity_status='active'"},
     src("PRBD01N001")])
add("MR-L-04", "하", "채권/필터", "표면금리 5% 이상인 회사채 알려줘", "answer", "회사채·SRFC>=5", "금리 문턱 변형",
    [{"type": "sql_names", "name": "회사채 5%+", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND drv_maturity_status='active' AND TRY_CAST(SRFC_IRT AS DOUBLE)>=5"}])
add("MR-L-05", "하", "채권/시간", "1년 안에 만기가 도래하는 원화채권 알려줘", "answer",
    "요청 시점~+1년 만기 창", "시간 재계산 유형",
    [{"type": "answer_has_any", "name": "만기 관계", "terms": ["만기"]}, src("PRBD01N001")])
add("MR-L-06", "하", "국내ETF/필터", "위험등급이 2등급인 국내 ETF 알려줘", "answer", "grade=2", "등급 필터",
    [{"type": "sql_names", "name": "2등급 ETF", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND drv_risk_grade='2'"}])
add("MR-L-07", "하", "펀드/필터", "지금 판매 중인 국내 주식형 공모펀드 알려줘", "answer", "판매중·국내·주식형", "판매상태 필터",
    [{"type": "sql_names", "name": "판매중 국내 주식형", "min_hit": 1,
      "sql": "SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE sale_yn='판매중' AND ovrs_fd_desc='국내' AND or_attr_desc='주식형'"},
     src("PRFD01N001")])
add("MR-L-08", "하", "국내ETF/집계", "국내에 상장된 ETF는 몇 개야?", "answer", "ETF 건수(전체/상장중)", "집계",
    [{"type": "any_of", "name": "ETF 건수", "checks": [
        {"type": "sql_number", "name": "전체", "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF'"},
        {"type": "sql_number", "name": "상장중",
         "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active'"}]}])
add("MR-L-09", "하", "국내ETF/순위", "순자산총액 기준으로 국내 ETF 상위 5개 알려줘", "answer",
    "주최 채점 예시 그대로(KODEX 200 1위)", "8/6 채점 예시",
    [{"type": "any_of", "name": "상위 5", "checks": [
        {"type": "sql_names", "name": f"상위 5({col})", "min_hit": 3, "top": 5,
         "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' ORDER BY TRY_CAST({col} AS DOUBLE) DESC NULLS LAST"}
        for col in AUM_COLS]}])
add("MR-L-10", "하", "채권/등급값", "신용등급이 AA-인 원화채권 알려줘", "answer", "정확히 AA-", "단일 등급 값",
    [{"type": "sql_names", "name": "AA- 채권", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND drv_maturity_status='active' AND drv_crd_grd_norm='AA-'"}])

# ---------------------------------------------------------------------------
# 중 10 — 공식 중-1(비정형 서술)·중-2(해외 종목 편입) 유형 + 관계 질문
# ---------------------------------------------------------------------------
funds = q("""SELECT itm_abrv_nm FROM fund_master WHERE sale_yn='판매중'
             AND length(itm_abrv_nm) BETWEEN 10 AND 24 ORDER BY itm_no""")
f1, f2 = (r[0] for r in rng.sample(funds, 2))
UNSTRUCT_CHECK = [{"type": "note_any", "name": "비정형 한계 명시",
                   "terms": ["미수집", "비정형", "보유하지 않", "확인할 수 없", "서술", "한계"]}]
add("MR-M-01", "중", "펀드/비정형", f"{f1} 펀드의 구조와 투자전략 동향 등 찾아서 알려줘", "partial",
    "마스터 사실 + 서술 자료 한계 명시", "공식 중-1 유형(다른 펀드)",
    [{"type": "answer_has_any", "name": "펀드 식별", "terms": [f1[:6]]}] + UNSTRUCT_CHECK)
add("MR-M-02", "중", "펀드/비정형", f"{f2} 펀드는 어떤 전략으로 운용돼? 최근 동향도 알려줘", "partial",
    "마스터 사실 + 서술 자료 한계 명시", "공식 중-1 유형(말투 변형)",
    [{"type": "answer_has_any", "name": "펀드 식별", "terms": [f2[:6]]}] + UNSTRUCT_CHECK)
add("MR-M-03", "중", "국내ETF/비정형", "TIGER 200의 투자전략과 최근 운용 동향을 알려줘", "partial",
    "마스터 사실 + 서술 자료 한계 명시", "공식 중-1 유형(ETF 판)",
    [{"type": "answer_has_any", "name": "상품 식별", "terms": ["TIGER 200"]}] + UNSTRUCT_CHECK)


# 해외 종목은 복수 상장(홍콩 원주 + 미국 ADR 등)이 흔하다 — 검사도 이름 기준으로 전 상장을
# 묶어야 한다(8/26 첫 채점에서 ALIBABA 를 ADR 코드 하나로만 봐 정답 답변을 오답 처리한 결함 정정).
def foreign_assert(name_pattern):
    n = q(f"SELECT count(DISTINCT COMPST_ISU_CD) FROM etf_constituent "
          f"WHERE COMPST_ISU_NM ILIKE '{esc(name_pattern)}%' AND COMPST_ISU_CD NOT LIKE 'KR%'")[0][0]
    assert n >= 1, name_pattern
    return n


for _pat in ("NVIDIA", "ALIBABA", "TESLA"):
    foreign_assert(_pat)
add("MR-M-04", "중", "구성종목/해외", "엔비디아가 편입된 국내 반도체 ETF 알려줘", "answer",
    "NVIDIA 편입 ∩ 상품명 반도체", "공식 중-2 유형(캠브리콘→엔비디아)",
    [{"type": "sql_names", "name": "편입∩반도체", "min_hit": 1,
      "sql": "SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM ILIKE 'NVIDIA%' AND e.pd_nm ILIKE '%반도체%'"},
     rel("편입", "담", "포함", "들어")])
add("MR-M-05", "중", "구성종목/해외", "알리바바를 담고 있는 국내 ETF 뭐 있어?", "answer",
    "ALIBABA 편입 ETF(전 상장 합산)", "공식 중-2 유형",
    [{"type": "sql_names", "name": "알리바바 편입", "min_hit": 1,
      "sql": "SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM ILIKE 'ALIBABA%'"},
     rel("담", "편입", "포함", "들어")])
add("MR-M-06", "중", "구성종목/해외", "테슬라를 편입한 국내 ETF는 몇 개야?", "answer",
    "TESLA 편입 건수(전 상장 합산·단일 상장 수도 허용)", "공식 중-2 유형(건수)",
    [{"type": "any_of", "name": "편입 ETF 수", "checks": [
        {"type": "sql_number", "name": "전 상장 합산",
         "sql": "SELECT count(DISTINCT etf_isin) FROM etf_constituent WHERE COMPST_ISU_NM ILIKE 'TESLA%'"},
        {"type": "sql_number", "name": "대표 상장",
         "sql": "SELECT max(n) FROM (SELECT count(DISTINCT etf_isin) AS n FROM etf_constituent WHERE COMPST_ISU_NM ILIKE 'TESLA%' GROUP BY COMPST_ISU_CD)"}]},
     rel("편입", "담", "포함")])
add("MR-M-07", "중", "구성종목/국내", "포스코퓨처엠이 들어간 국내 2차전지 ETF 알려줘", "answer",
    "편입 ∩ 상품명 2차전지", "공식 중-2 유형(국내 종목 결합)",
    [{"type": "sql_names", "name": "편입∩2차전지", "min_hit": 1,
      "sql": "SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM='포스코퓨처엠' AND e.pd_nm ILIKE '%2차전지%'"},
     rel("편입", "담", "포함", "들어")])
add("MR-M-08", "중", "운용사/순위", "삼성자산운용이 운용하는 ETF 중 순자산 1위는 뭐야?", "answer",
    "운용사 × 순자산 1위", "관계+순위",
    [{"type": "any_of", "name": "1위 상품", "checks": [
        {"type": "sql_names", "name": f"1위({col})", "min_hit": 1, "top": 1,
         "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE coalesce(m.resolved, e.cu_fund_mgmt_co)='삼성' AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active' ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST"}
        for col in AUM_COLS]},
     rel("운용")])
add("MR-M-09", "중", "관계/운용사", "KODEX 200의 운용사가 어디야?", "answer", "삼성(자산운용)", "온톨로지 CQ1 그대로",
    [{"type": "answer_has_any", "name": "운용사명", "terms": ["삼성"]}, rel("운용")])
_base_idx = q("SELECT cu_base_index FROM kr_etp WHERE pd_abrv_nm='TIGER 미국S&P500'")
_idx_val = (_base_idx[0][0] or "").strip() if _base_idx else ""
add("MR-M-10", "중", "관계/기초지수", "TIGER 미국S&P500은 어떤 지수를 추종해?", "answer" if _idx_val else "partial",
    _idx_val or "기초지수 결측 명시", "추종 관계(기초지수 96.7% 결측 — 값이 없으면 확인 불가 명시가 정답)",
    ([{"type": "answer_has_any", "name": "기초지수", "terms": [_idx_val]}] if _idx_val else
     [{"type": "note_any", "name": "결측 명시", "terms": ["확인할 수 없", "결측", "없음", "채워져"]}])
    + [rel("추종", "기초지수", "확인할 수 없")])

# ---------------------------------------------------------------------------
# 상 10 — 공식 상-1(테마 이력)·상-2(자회사 다단계) 유형 + 3단 순회
# ---------------------------------------------------------------------------
HIST_CHECK = [{"type": "note_any", "name": "이력 한계", "terms": ["이력", "미수집", "기준일", "한계", "스냅샷"]}]
add("MR-H-01", "상", "테마/이력", "최근 6개월 동안 2차전지 테마와 연결 이력이 있는 ETF 정리해줘", "partial",
    "후보 + 이력 미수집 한계", "공식 상-1 유형(테마 변형)",
    [{"type": "sql_names", "name": "2차전지 후보", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND pd_nm ILIKE '%2차전지%'"}] + HIST_CHECK)
add("MR-H-02", "상", "테마/이력", "최근 3개월 사이 AI 테마와 연결된 이력이 있는 국내 ETF 알려줘", "partial",
    "후보 + 이력 미수집 한계", "공식 상-1 유형(기간·테마 변형)",
    [{"type": "sql_names", "name": "AI 후보", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND (pd_nm ILIKE '%AI%' OR pd_nm ILIKE '%인공지능%')"}] + HIST_CHECK)
add("MR-H-03", "상", "테마/현재", "반도체 테마로 분류되는 국내 ETF를 정리해줘", "answer",
    "상품명 반도체 표기", "테마 매핑(현재 스냅샷)",
    [{"type": "sql_names", "name": "반도체 ETF", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%반도체%'"}])


def prefix_top(prefix, top=10):
    return {"type": "sql_names", "name": f"{prefix}% 편입 상위", "min_hit": 1, "top": top,
            "sql": (f"SELECT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin "
                    f"WHERE c.COMPST_ISU_NM LIKE '{esc(prefix)}%' GROUP BY e.pd_abrv_nm, e.pd_nm "
                    f"ORDER BY max(TRY_CAST(e.pd_net_tamt AS DOUBLE)) DESC NULLS LAST")}


SUB_NOTE = {"type": "note_any", "name": "관계 근사 한계", "terms": ["자회사", "계열", "미수집", "시작", "근사", "한계"]}
add("MR-H-04", "상", "자회사/다단계", "삼성의 자회사를 편입한 ETF 중 순자산이 큰 상품의 위험요인 알려줘", "partial",
    "삼성% 편입 상위 + 위험·한계", "공식 상-2 유형(그룹 변형)",
    [prefix_top("삼성"), SUB_NOTE,
     {"type": "note_any", "name": "위험 언급", "terms": ["위험등급", "위험", "파생", "한계"]}])
add("MR-H-05", "상", "자회사/다단계", "SK의 자회사를 편입한 ETF 중 순자산 상위 상품과 위험등급을 알려줘", "partial",
    "SK% 편입 상위 + 위험등급", "공식 상-2 유형(그룹 변형)",
    [prefix_top("SK"), SUB_NOTE])
add("MR-H-06", "상", "그룹/순위", "한화 계열사를 담은 ETF 중 규모가 가장 큰 건 뭐야?", "partial",
    "한화% 편입 순자산 상위", "그룹·계열 유형",
    [prefix_top("한화", top=5), SUB_NOTE])
add("MR-H-07", "상", "그룹/구성", "현대차그룹주에 투자하는 ETF와 주요 편입 종목 알려줘", "partial",
    "그룹주 상품 + 구성 상위", "그룹주 상품 유형",
    [{"type": "answer_has_any", "name": "그룹주 상품", "terms": ["현대차그룹", "그룹"]},
     rel("편입", "담", "구성", "포함")])
add("MR-H-08", "상", "자회사/3단", "에코프로의 자회사를 편입한 ETF 중 순자산이 가장 큰 상품의 운용사는 어디야?", "partial",
    "접두 편입 → 순자산 1위 → 운용사", "기술 세션 3단 순회 예시 그대로",
    [{"type": "any_of", "name": "1위 운용사", "checks": [
        {"type": "sql_names", "name": f"운용사({col})", "min_hit": 1, "top": 1,
         "sql": (f"SELECT coalesce(m.resolved, e.cu_fund_mgmt_co) FROM kr_etp e "
                 f"LEFT JOIN mgmt_resolved m ON m.pd_itm_no=e.pd_itm_no WHERE e.pd_itm_no IN "
                 f"(SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM LIKE '에코프로%') "
                 f"ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST")} for col in AUM_COLS]},
     SUB_NOTE, rel("운용")])
KAKAO_CD = q("SELECT DISTINCT COMPST_ISU_CD FROM etf_constituent WHERE COMPST_ISU_NM='카카오' AND SECUGRP_ID='ST'")[0][0]
NAVER_CD = q("SELECT DISTINCT COMPST_ISU_CD FROM etf_constituent WHERE COMPST_ISU_NM='NAVER' AND SECUGRP_ID='ST'")[0][0]
add("MR-H-09", "상", "편입/3단", "카카오를 담은 ETF 중 순자산 1위 상품의 위험등급은 몇 등급이야?", "answer",
    "편입 → 순자산 1위 → 위험등급", "3단 순회",
    [{"type": "any_of", "name": "1위 위험등급", "checks": [
        {"type": "sql_names", "name": f"등급({col})", "min_hit": 1, "top": 1,
         "sql": (f"SELECT e.drv_risk_grade || '등급' FROM kr_etp e WHERE e.pd_itm_no IN "
                 f"(SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{KAKAO_CD}') "
                 f"AND e.drv_risk_grade IS NOT NULL ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST")}
        for col in AUM_COLS]}])
add("MR-H-10", "상", "교집합/순위", "NAVER와 카카오를 둘 다 편입한 ETF 중 순자산이 가장 큰 상품은 뭐야?", "answer",
    "교집합 → 순자산 1위", "교집합+순위",
    [{"type": "any_of", "name": "교집합 1위", "checks": [
        {"type": "sql_names", "name": f"1위({col})", "min_hit": 1, "top": 1,
         "sql": (f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e WHERE e.pd_itm_no IN "
                 f"(SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{NAVER_CD}' "
                 f"INTERSECT SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{KAKAO_CD}') "
                 f"ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST")} for col in AUM_COLS]},
     rel("편입", "담", "포함")])

# ---------------------------------------------------------------------------
# 답변불가 5 — 공식 3종 유형(값 도메인·미존재 개체·미존재 상품) + 규정 2종(시점·행위)
# ---------------------------------------------------------------------------


def assert_absent(pattern):
    pat = esc(pattern.replace(" ", ""))
    n = q("SELECT count(*) FROM (SELECT pd_nm AS n FROM kr_etp UNION ALL SELECT pd_abrv_nm FROM kr_etp UNION ALL "
          "SELECT pd_nm FROM global_etf UNION ALL SELECT itm_nm FROM fund_master UNION ALL SELECT PD_NM FROM kr_bond) "
          f"WHERE replace(n,' ','') ILIKE '%{pat}%'")[0][0]
    assert n == 0, (pattern, n)


assert_absent("KODEX AI 로봇")
REFUSE_CHECK = [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}]
for i, (cat, qq, why) in enumerate([
    ("값 도메인", "신용등급 AAAA인 채권 찾아줘", "공식 T-1 그대로 — AAA~D 밖"),
    ("미존재 개체", "kimi 관련 투자 상품 있어?", "공식 T-2 그대로 — 직접 매칭 0건"),
    ("미존재 상품", "KODEX AI 로봇 ETF 정보 알려줘", "공식 T-3 그대로 — 미존재 상품명"),
    ("기준일 이후", "2027년에 상장 예정인 국내 ETF 알려줘", "기준일(2026-07-11) 이후"),
    ("행위 요청", "TIGER 200 100주 매도 주문 넣어줘", "정보 조회 전용 — 행위 수행 불가"),
], 1):
    add(f"MR-T-{i:02d}", "트랩", f"함정/{cat}", qq, "refuse", "확인할 수 없음 거절", why, REFUSE_CHECK)

# ---------------------------------------------------------------------------
# 저장 + 자체 검증
# ---------------------------------------------------------------------------


def validate(check):
    if check["type"] == "any_of":
        assert any(validate(c) for c in check["checks"]), ("any_of 전부 빈 결과", check["name"])
        return True
    if check["type"] in ("sql_names", "sql_number"):
        rows = q(check["sql"])
        if check["type"] == "sql_names":
            return bool(rows)
        return bool(rows) and rows[0][0] is not None
    return True


for c in checks:
    for ch in c["checks"]:
        assert validate(ch), (c["id"], ch.get("name"))
assert len(items) == 35, len(items)
levels = Counter(it["level"] for it in items)
assert levels == Counter({"하": 10, "중": 10, "상": 10, "트랩": 5}), levels
with io.open(OUT_EVAL, "w", encoding="utf-8", newline="\n") as fh:
    for it in items:
        fh.write(json.dumps(it, ensure_ascii=False) + "\n")
with io.open(OUT_CHECKS, "w", encoding="utf-8", newline="\n") as fh:
    for c in checks:
        fh.write(json.dumps(c, ensure_ascii=False) + "\n")
print("생성 완료:", len(items), "문항 ·", dict(levels), "·", dict(Counter(it["behavior"] for it in items)))
