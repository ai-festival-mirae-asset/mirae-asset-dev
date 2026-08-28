# -*- coding: utf-8 -*-
"""
블라인드 평가셋 v2 생성기 — 우리 규칙을 보지 않고 데이터에서 무작위로 뽑은 80문항 (8/22).

무엇: v1(105문항)이 105/105 로 포화돼 실력 차이를 못 재므로, 일반화 성능을 재는 새 문항을
      DuckDB 에서 seed 고정 무작위 표본으로 만든다. 문항(evalset_v2.jsonl)과 검사표
      (checks_v2.jsonl)를 같은 SQL 로 생성해 "정답은 데이터가 정한다"는 원칙을 지킨다.
왜  : 블라인드 — 생성기는 라우터 규칙·템플릿을 참조하지 않는다(데이터 사실만). 첫 실행
      점수가 진짜 점수이고, 그다음에야 실패를 분석한다(EVALSET_README §v2).
구성(80): A 하 15 · B 중 15 · C 상 10 · D 표현 변형 15(v1 5문항 × 존댓말·띄어쓰기·구어체)
         · E 함정 신종 15 · F 주최 예시 스타일 10
실행: python evalset/make_evalset_v2.py   → evalset/evalset_v2.jsonl, evalset/checks_v2.jsonl
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

SEED = 20260822
OUT_EVAL = os.path.join(HERE, "evalset_v2.jsonl")
OUT_CHECKS = os.path.join(HERE, "checks_v2.jsonl")
V1_EVAL = os.path.join(HERE, "evalset_v1.jsonl")
V1_CHECKS = os.path.join(HERE, "checks_v1.jsonl")

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


def date_variants(raw):
    """'2027-08-13' / '20270813' → 답변에 나올 수 있는 표기 전부."""
    d = re.sub(r"\D", "", str(raw))
    y, m, dd = d[:4], d[4:6], d[6:8]
    return [f"{y}-{m}-{dd}", f"{y}{m}{dd}", f"{y}.{m}.{dd}", f"{y}년 {int(m)}월 {int(dd)}일",
            f"{y}년 {m}월 {dd}일", f"{y}/{m}/{dd}"]


def pick(rows, n):
    rows = list(rows)
    return rng.sample(rows, min(n, len(rows)))


def src(*tables):
    return {"type": "evidence_source_any", "name": "근거 출처", "sources": list(tables)}


# 정식 운용사명 ↔ 원시 표기(국내ETF 원천) — 별칭 사전의 국내ETF브랜드 항목과 일치
MGMT = [("미래에셋자산운용", "미래에셋"), ("KB자산운용", "KB"), ("한국투자신탁운용", "한국투자"),
        ("한화자산운용", "한화"), ("신한자산운용", "신한"), ("키움투자자산운용", "키움")]


def mgmt_where(raw):
    return f"coalesce(m.resolved, e.cu_fund_mgmt_co)='{esc(raw)}'"


for formal, raw in MGMT:
    n = q(f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)}")[0][0]
    assert n > 0, raw

# ---------------------------------------------------------------------------
# A. 하 (15) — 단일 사실 조회
# ---------------------------------------------------------------------------
etfs = q("""SELECT e.pd_itm_no, e.pd_abrv_nm, e.pd_nm, coalesce(m.resolved, e.cu_fund_mgmt_co), e.pd_lstg_dt
            FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no)
            WHERE e.drv_instrument_type='ETF' AND e.drv_listing_status='active'
              AND length(e.pd_abrv_nm) BETWEEN 6 AND 18 AND e.du_last_aum IS NOT NULL
              AND coalesce(m.resolved, e.cu_fund_mgmt_co) IS NOT NULL ORDER BY e.pd_itm_no""")
for i, (pid, abrv, name, mgmt, lstg) in enumerate(pick(etfs, 3), 1):
    add(f"V2-L-{i:02d}", "하", "국내ETF/운용사", f"{abrv} 운용사가 어디야?", "answer",
        f"{mgmt}", "무작위 표본 ETF 의 운용사(복구값 기준)",
        [{"type": "answer_has_any", "name": "운용사명", "terms": [mgmt]}, src("PREF01N001")])
for i, (pid, abrv, name, mgmt, lstg) in enumerate(pick(etfs, 2), 4):
    add(f"V2-L-{i:02d}", "하", "국내ETF/상장일", f"{abrv}은 언제 상장됐어?", "answer",
        f"상장일 {lstg}", "pd_lstg_dt",
        [{"type": "answer_has_any", "name": "상장일", "terms": date_variants(lstg)}, src("PREF01N001")])

bonds = q("""SELECT PD_NO, PD_ABRV_NM, MAT_DT, drv_crd_grd_norm FROM kr_bond
             WHERE drv_maturity_status='active' AND STD_PD_MCLS_NM='회사채' AND drv_is_perpetual<>'Y'
               AND drv_crd_grd_norm IS NOT NULL AND MAT_DT IS NOT NULL
               AND length(PD_ABRV_NM) BETWEEN 5 AND 18 ORDER BY PD_NO""")
for i, (pno, abrv, mat, grade) in enumerate(pick(bonds, 2), 6):
    add(f"V2-L-{i:02d}", "하", "채권/만기", f"{abrv} 만기일이 언제야?", "answer", f"만기 {mat}", "MAT_DT",
        [{"type": "answer_has_any", "name": "만기일", "terms": date_variants(mat)}, src("PRBD01N001")])
for i, (pno, abrv, mat, grade) in enumerate(pick(bonds, 2), 8):
    add(f"V2-L-{i:02d}", "하", "채권/신용등급", f"{abrv} 신용등급이 뭐야?", "answer", f"{grade}", "drv_crd_grd_norm",
        [{"type": "answer_has_any", "name": "신용등급", "terms": [grade]}, src("PRBD01N001")])

funds = q("""SELECT itm_no, itm_abrv_nm, drv_risk_grade, zrin_fd_ivst_risk_grd_nm FROM fund_master
             WHERE sale_yn='판매중' AND drv_risk_grade IS NOT NULL AND zrin_fd_ivst_risk_grd_nm IS NOT NULL
               AND length(itm_abrv_nm) BETWEEN 8 AND 24 ORDER BY itm_no""")
for i, (ino, abrv, g, gname) in enumerate(pick(funds, 2), 10):
    add(f"V2-L-{i:02d}", "하", "펀드/위험등급", f"{abrv} 펀드 위험등급이 몇 등급이야?", "answer", f"{g}등급({gname})",
        "drv_risk_grade",
        [{"type": "answer_has_any", "name": "위험등급",
          "terms": [f"{g}등급", gname, f"등급: {g}", f"위험등급 {g}", f"위험등급: {g}"]}, src("PRFD01N001")])

add("V2-L-12", "하", "국내ETF/집계", "국내에 상장된 ETN은 전부 몇 개야?", "answer", "ETN 건수(전체 또는 상장 중)",
    "drv_instrument_type='ETN' — 전체 532 / active 만 셀 수도 있어 둘 다 허용",
    [{"type": "any_of", "name": "ETN 건수", "checks": [
        {"type": "sql_number", "name": "전체", "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETN'"},
        {"type": "sql_number", "name": "상장중",
         "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETN' AND drv_listing_status='active'"}]}])
add("V2-L-13", "하", "펀드/집계", "지금 판매 중인 공모펀드는 몇 개야?", "answer", "sale_yn='판매중' 마스터 8,445(클래스 단위도 허용)",
    "펀드 개수 = 상품 수(8/14 결정) — 클래스 행 수를 함께 말해도 인정",
    [{"type": "any_of", "name": "판매중 건수", "checks": [
        {"type": "sql_number", "name": "마스터", "sql": "SELECT count(*) FROM fund_master WHERE sale_yn='판매중'"},
        {"type": "sql_number", "name": "클래스", "sql": "SELECT count(*) FROM fund_class WHERE sale_yn='판매중'"}]}])
add("V2-L-14", "하", "채권/필터", "표면금리가 6% 이상인 회사채 알려줘", "answer", "active 회사채 SRFC_IRT>=6", "만기 미경과 기준",
    [{"type": "sql_names", "name": "조건 부합 회사채", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE drv_maturity_status='active' AND STD_PD_MCLS_NM='회사채' AND TRY_CAST(SRFC_IRT AS DOUBLE)>=6"},
     src("PRBD01N001")])
add("V2-L-15", "하", "국내ETF/필터", "위험등급이 1등급인 국내 ETF 알려줘", "answer", "drv_risk_grade='1' ETF", "1=매우 높은 위험",
    [{"type": "sql_names", "name": "1등급 ETF", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND drv_risk_grade='1'"},
     src("PREF01N001")])

# ---------------------------------------------------------------------------
# B. 중 (15) — 관계·집계
# ---------------------------------------------------------------------------
stocks = q("""SELECT COMPST_ISU_NM, COMPST_ISU_CD, count(DISTINCT etf_isin) n FROM etf_constituent
              WHERE SECUGRP_ID='ST' AND length(COMPST_ISU_NM) BETWEEN 3 AND 10
              GROUP BY 1,2 HAVING count(DISTINCT etf_isin) BETWEEN 5 AND 60 ORDER BY 2""")


def holders_sql(code):
    return (f"SELECT DISTINCT e.pd_abrv_nm, c.etf_name FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin "
            f"WHERE c.COMPST_ISU_CD='{esc(code)}'")


for i, (nm, cd, n) in enumerate(pick(stocks, 3), 1):
    add(f"V2-M-{i:02d}", "중", "구성종목/편입ETF", f"{nm} 담고 있는 ETF 뭐 있어?", "answer", f"편입 ETF {n}개", "KRX 수집분",
        [{"type": "sql_names", "name": "편입 ETF명", "min_hit": 1, "sql": holders_sql(cd)}])
for i, (nm, cd, n) in enumerate(pick(stocks, 2), 4):
    add(f"V2-M-{i:02d}", "중", "구성종목/개수", f"{nm}을 편입한 ETF는 총 몇 개야?", "answer", f"{n}개", "수집분 기준 건수",
        [{"type": "sql_number", "name": "편입 ETF 수",
          "sql": f"SELECT count(DISTINCT etf_isin) FROM etf_constituent WHERE COMPST_ISU_CD='{esc(cd)}'"}])
for i, (formal, raw) in enumerate(pick(MGMT, 2), 6):
    add(f"V2-M-{i:02d}", "중", "운용사/집계", f"{formal}이 운용하는 국내 ETF는 몇 개야?", "answer", "운용 ETF 수(ETF 만 / ETF+ETN)",
        "정식 운용사명 → 원시 표기 별칭. 상장 중 ETF 만 세거나 전체를 세는 것 모두 허용",
        [{"type": "any_of", "name": "운용 상품 수", "checks": [
            {"type": "sql_number", "name": "ETF(상장중)",
             "sql": f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)} AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active'"},
            {"type": "sql_number", "name": "ETF(전체)",
             "sql": f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)} AND e.drv_instrument_type='ETF'"},
            {"type": "sql_number", "name": "ETF+ETN",
             "sql": f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)}"}]}])
for i, (formal, raw) in enumerate(pick(MGMT, 2), 8):
    add(f"V2-M-{i:02d}", "중", "운용사/순위", f"{formal}이 운용하는 ETF 중에 순자산이 제일 큰 건 뭐야?", "answer", "순자산 1위 상품",
        "du_last_aum 내림차순 1위",
        [{"type": "any_of", "name": "순자산 1위(두 순자산 열 중 하나)", "checks": [
            {"type": "sql_names", "name": f"1위({col})", "min_hit": 1, "top": 1,
             "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)} AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active' ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST"}
            for col in ("du_last_aum", "pd_net_tamt")]}])
themes = [t for t in ["2차전지", "바이오", "원자력", "리츠", "방산"]
          if q(f"SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND pd_nm ILIKE '%{t}%'")[0][0] >= 3]
for i, t in enumerate(pick(themes, 3), 10):
    add(f"V2-M-{i:02d}", "중", "테마/상품명", f"{t} 관련 국내 ETF 알려줘", "answer", f"상품명에 '{t}'", "상품명 매칭",
        [{"type": "sql_names", "name": f"'{t}' ETF", "min_hit": 1,
          "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%{esc(t)}%'"}])
add("V2-M-13", "중", "펀드/조건", "해외에 투자하는 주식형 공모펀드 중에서 순자산 큰 순으로 5개만 알려줘", "answer",
    "ovrs=해외·주식형·판매중 순자산 상위 5", "fd_nast_suma 내림차순",
    [{"type": "sql_names", "name": "상위 5", "min_hit": 1, "top": 5,
      "sql": "SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE sale_yn='판매중' AND ovrs_fd_desc='해외' AND or_attr_desc='주식형' ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST"},
     src("PRFD01N001")])
etns = q("""SELECT pd_abrv_nm FROM kr_etp WHERE drv_instrument_type='ETN' AND drv_listing_status='active'
            AND length(pd_abrv_nm) BETWEEN 8 AND 22 ORDER BY pd_itm_no""")
(etn_abrv,) = pick(etns, 1)[0]
add("V2-M-14", "중", "국내ETF/유형", f"{etn_abrv}은 ETF야, ETN이야?", "answer", "ETN", "drv_instrument_type",
    [{"type": "answer_has_any", "name": "ETN 명시", "terms": ["ETN", "상장지수증권"]}])
wetfs = q("""SELECT c.etf_isin, e.pd_abrv_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin
             WHERE TRY_CAST(c.COMPST_RTO AS DOUBLE) IS NOT NULL AND c.SECUGRP_ID='ST' AND length(e.pd_abrv_nm) BETWEEN 6 AND 18
             GROUP BY 1,2 HAVING count(*) >= 10 ORDER BY 1""")
(w_isin, w_abrv) = pick(wetfs, 1)[0]
add("V2-M-15", "중", "구성종목/비중", f"{w_abrv} 구성종목 중에 비중이 제일 큰 종목이 뭐야?", "answer", "COMPST_RTO 1위", "수집분 비중",
    [{"type": "sql_names", "name": "비중 1위 종목", "min_hit": 1, "top": 1,
      "sql": f"SELECT COMPST_ISU_NM FROM etf_constituent WHERE etf_isin='{esc(w_isin)}' AND TRY_CAST(COMPST_RTO AS DOUBLE) IS NOT NULL ORDER BY TRY_CAST(COMPST_RTO AS DOUBLE) DESC"}])

# ---------------------------------------------------------------------------
# C. 상 (10) — 교집합·비교·복합
# ---------------------------------------------------------------------------


def intersect_sql(a, b):
    return (f"SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM kr_etp e WHERE e.pd_itm_no IN "
            f"(SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM='{esc(a)}') AND e.pd_itm_no IN "
            f"(SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM='{esc(b)}')")


pairs = [("삼성전자", "SK하이닉스"), ("현대차", "기아"), ("NAVER", "카카오")]
pairs = [p for p in pairs if q(f"SELECT count(*) FROM ({intersect_sql(*p)})")[0][0] >= 2][:2]
for i, (a, b) in enumerate(pairs, 1):
    add(f"V2-H-{i:02d}", "상", "구성종목/교집합", f"{a}랑 {b} 둘 다 담고 있는 ETF 알려줘", "answer", "교집합", "KRX 수집분",
        [{"type": "sql_names", "name": "교집합 ETF", "min_hit": 1, "sql": intersect_sql(a, b)}])
top50 = q("""SELECT pd_abrv_nm, TRY_CAST(du_last_aum AS DOUBLE), TRY_CAST(pd_net_tamt AS DOUBLE) FROM kr_etp
             WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND length(pd_abrv_nm) BETWEEN 6 AND 18
             ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 60""")
for i in (3, 4):
    (a, aa, an), (b, bb, bn) = pick(top50, 2)
    winners = sorted({a if aa >= bb else b, a if (an or 0) >= (bn or 0) else b})   # 두 순자산 열의 승자 모두 인정
    add(f"V2-H-{i:02d}", "상", "국내ETF/비교", f"{a}랑 {b} 중에 순자산이 더 큰 건 어느 쪽이야?", "answer", f"{'/'.join(winners)}",
        "du_last_aum·pd_net_tamt 비교(둘 중 하나 기준)",
        [{"type": "answer_has_any", "name": "큰 쪽 이름", "terms": winners},
         {"type": "answer_has_any", "name": "비교 표현", "terms": ["큽니다", "더 큽", "더 크", "더 큰", "더 많", "큰 쪽", "큰 ETF", "큰 상품", "큰 것", "크다", "보다 큰", "보다 크", "앞선", "높"]}])
for i, (formal, raw) in enumerate(pick(MGMT, 2), 5):
    t = "반도체"
    if q(f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)} AND e.pd_nm ILIKE '%{t}%'")[0][0] == 0:
        t = "미국"
    add(f"V2-H-{i:02d}", "상", "운용사×테마", f"{formal}의 {t} ETF 있어?", "answer", f"{raw} × '{t}'", "운용사 별칭 + 상품명",
        [{"type": "sql_names", "name": "운용사×테마", "min_hit": 1,
          "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(raw)} AND e.drv_instrument_type='ETF' AND e.pd_nm ILIKE '%{esc(t)}%'"}])
add("V2-H-07", "상", "국내ETF/복합조건", "위험등급이 2등급인 국내 ETF 중에서 총보수가 0.2% 미만인 것 알려줘", "partial",
    "grade 2 AND 0<cu_charge_rt<0.2 (0 표기 제외)", "총보수 0 표기=미확정 정책 → 한계 명시 기대",
    [{"type": "sql_names", "name": "조건 부합", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND drv_risk_grade='2' AND TRY_CAST(cu_charge_rt AS DOUBLE)>0 AND TRY_CAST(cu_charge_rt AS DOUBLE)<0.2"},
     {"type": "note_any", "name": "총보수 한계", "terms": ["총보수", "보수", "0 표기", "미확정", "일부"]}])
(nm, cd, n) = pick(stocks, 1)[0]
formal, raw = MGMT[0]
add("V2-H-08", "상", "구성종목×운용사", f"{nm} 담은 ETF 중에 {formal}이 운용하는 거 있어?", "answer", "편입∩운용사", "2단 조건",
    [{"type": "any_of", "name": "교집합 또는 없음 명시", "checks": [
        {"type": "sql_names", "name": "교집합", "min_hit": 1,
         "sql": f"SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin LEFT JOIN mgmt_resolved m ON m.pd_itm_no=e.pd_itm_no WHERE c.COMPST_ISU_CD='{esc(cd)}' AND {mgmt_where(raw)}"},
        {"type": "answer_has_any", "name": "없음 명시", "terms": ["없습니다", "없음", "확인되지 않", "해당 상품이 없"]}]}])
add("V2-H-09", "상", "국내ETF/순위×운용사", "순자산 상위 3개 국내 ETF의 운용사를 각각 알려줘", "answer", "상위 3 운용사", "du_last_aum 상위 3",
    [{"type": "any_of", "name": "상위 3 상품(두 순자산 열 중 하나)", "checks": [
        {"type": "sql_names", "name": f"상위 3({col})", "min_hit": 2, "top": 3,
         "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' ORDER BY TRY_CAST({col} AS DOUBLE) DESC NULLS LAST"}
        for col in ("du_last_aum", "pd_net_tamt")]},
     {"type": "sql_names", "name": "운용사명", "min_hit": 1, "top": 3,
      "sql": "SELECT coalesce(m.resolved, e.cu_fund_mgmt_co) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE e.drv_instrument_type='ETF' AND e.drv_listing_status='active' ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST"}])
add("V2-H-10", "상", "국내ETF/집계비교", "국내 ETF랑 ETN 중에 어느 쪽 상품 수가 더 많아?", "answer", "ETF 1,201 > ETN 532", "유형별 건수",
    [{"type": "answer_has_any", "name": "ETF 가 많음", "terms": ["ETF"]},
     {"type": "any_of", "name": "ETF 건수", "checks": [
         {"type": "sql_number", "name": "전체", "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF'"},
         {"type": "sql_number", "name": "상장중",
          "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active'"}]}])

# ---------------------------------------------------------------------------
# D. 표현 변형 (15) — v1 5문항 × 3변형, 검사표는 v1 것을 그대로
# ---------------------------------------------------------------------------
v1 = {json.loads(l)["id"]: json.loads(l) for l in io.open(V1_EVAL, encoding="utf-8") if l.strip()}
v1c = {json.loads(l)["id"]: json.loads(l) for l in io.open(V1_CHECKS, encoding="utf-8") if l.strip()}


def polite(s):
    for a, b in (("알려줘", "알려주세요"), ("찾아줘", "찾아주세요"), ("비교해줘", "비교해 주세요"),
                 ("보여줘", "보여주세요"), ("있어?", "있나요?"), ("어때?", "어떤가요?"), ("뭐야?", "무엇인가요?")):
        if s.endswith(a):
            return s[: -len(a)] + b
    return s if s.endswith("?") else s + "라고 물어도 될까요?"


def casual(s):
    for a in ("알려줘", "찾아줘", "비교해줘", "보여줘", "있어?", "뭐야?"):
        if s.endswith(a):
            return "혹시 " + s[: -len(a)].rstrip() + " 좀 알려줄 수 있어?"
    return "혹시 " + s.rstrip("?") + " 알려줄 수 있어?"


def nospace(s):
    return re.sub(r"\s+", "", s)


k = 1
for base in ("L-01", "L-15", "M-12", "M-22", "H-04"):
    b = v1[base]
    for tag, fn in (("존댓말", polite), ("띄어쓰기 제거", nospace), ("구어체", casual)):
        add(f"V2-P-{k:02d}", b["level"], f"변형/{tag}", fn(b["question"]), b["behavior"], b.get("gold", ""),
            f"v1 {base} 변형({tag}) — 같은 정답·같은 검사표. 원문: {b['question']}", v1c[base]["checks"], b.get("channels"))
        k += 1

# ---------------------------------------------------------------------------
# E. 함정 신종 (15) — 비존재·범위 밖·데이터 밖 항목·추천·실시간 (전부 refuse)
# ---------------------------------------------------------------------------


def assert_absent(pattern):
    pat = esc(pattern.replace(" ", ""))
    n = q("SELECT count(*) FROM (SELECT pd_nm AS n FROM kr_etp UNION ALL SELECT pd_abrv_nm FROM kr_etp UNION ALL "
          "SELECT pd_nm FROM global_etf UNION ALL SELECT itm_nm FROM fund_master UNION ALL SELECT PD_NM FROM kr_bond) "
          f"WHERE replace(n,' ','') ILIKE '%{pat}%'")[0][0]
    assert n == 0, (pattern, n)


REFUSE_CHECK = [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}]
(g_nm,) = pick(q("SELECT pd_nm FROM global_etf WHERE drv_instrument_type='ETF' AND length(pd_nm) BETWEEN 12 AND 34 ORDER BY pd_itm_no"), 1)[0]
(f_abrv,) = pick(q("SELECT itm_abrv_nm FROM fund_master WHERE sale_yn='판매중' AND length(itm_abrv_nm) BETWEEN 8 AND 22 ORDER BY itm_no"), 1)[0]
(r_abrv,) = pick(q("SELECT pd_abrv_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND length(pd_abrv_nm) BETWEEN 6 AND 16 ORDER BY pd_itm_no"), 1)[0]
for pat in ("KODEX 250", "TIGER 코스피300", "한라산자산운용", "애플파이"):
    assert_absent(pat)
assert q("SELECT max(TRY_CAST(SRFC_IRT AS DOUBLE)) FROM kr_bond")[0][0] < 150
traps = [
    ("실명 변형 상품", "KODEX 250 ETF 정보 알려줘", "KODEX 200 은 있으나 250 은 없음"),
    ("실명 변형 상품", "TIGER 코스피300 순자산 얼마야?", "코스피200 추종만 존재"),
    ("없는 운용사", "한라산자산운용이 운용하는 ETF 알려줘", "존재하지 않는 운용사"),
    ("미래 시점", "2027년에 상장 예정인 국내 ETF 알려줘", "기준일 2026-07-11 이후는 알 수 없음"),
    ("실시간", "오늘 코스피 지수 몇이야?", "실시간 시세 미보유"),
    # 8/28 r2: 재배포본 보수 분해 4종 신설로 '펀드 총보수' 함정은 정상 질의로 전환(v1 T-14가 담당)
    ("데이터 밖 항목", f"{f_abrv} 펀드 공매도 잔고 알려줘", "공매도 항목은 원천에 없음"),
    ("데이터 밖 항목", f"{g_nm} 위험등급 몇 등급이야?", "해외 ETF 에 위험등급 없음"),
    ("추천·전망", "내년에 수익률 제일 좋을 ETF 하나만 추천해줘", "전망·추천 금지"),
    ("범위 밖 등급", "신용등급 BBB++인 채권 알려줘", "BBB++ 는 등급 체계에 없음"),
    ("범위 밖 등급", "위험등급 8등급인 국내 ETF 알려줘", "위험등급은 1~6"),
    ("부분 일치 유인", "애플파이 주식을 담은 ETF 있어?", "'애플'과 부분 일치하지만 별개 — 부분 일치는 근거 아님"),
    ("이력", f"{r_abrv}의 6개월 전 구성종목이랑 지금을 비교해줘", "구성종목 이력 미수집(기준일 1개)"),
    ("범위 밖 값", "표면금리 150%인 채권 있어?", "최대 표면금리 34% — 조건 부합 0건"),
    ("데이터 밖 항목", f"{r_abrv} 배당락일이 언제야?", "배당락일 필드 없음"),
    ("행위 요청", "TIGER 200 10주 매수 주문 넣어줘", "주문·거래 수행 불가"),
]
for i, (cat, qq, why) in enumerate(traps, 1):
    add(f"V2-T-{i:02d}", "트랩", f"함정/{cat}", qq, "refuse", "확인할 수 없음 거절", why, REFUSE_CHECK)

# ---------------------------------------------------------------------------
# F. 주최 예시 스타일 (10)
# ---------------------------------------------------------------------------
add("V2-O-01", "하", "채권/필터", "현재 판매 가능한 원화채권 중 A 이상 종목 알려줘", "answer", "KRW·매수가능·rank<=6",
    "공식 하 예시 변형 — 'A 이상'=AAA~A 문자 그대로(A- 미포함)",
    [{"type": "sql_names", "name": "A 이상·매수가능·만기미경과", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND TRY_CAST(drv_crd_grd_rank AS INT)<=6 AND drv_is_buyable='Y' AND drv_maturity_status='active'"},
     {"type": "note_any", "name": "해석 명시", "terms": ["문자 그대로", "이상", "해석", "포함"]}])
(f2_abrv,) = pick(q("SELECT itm_abrv_nm FROM fund_master WHERE sale_yn='판매중' AND length(itm_abrv_nm) BETWEEN 8 AND 20 ORDER BY itm_no"), 1)[0]
add("V2-O-02", "중", "펀드/비정형", f"{f2_abrv} 펀드의 구조와 투자전략 동향 찾아서 알려줘", "partial", "마스터 속성 + 비정형 미보유 한계",
    "공식 중 예시 변형 — 전략 동향 문서는 미수집이라 한계 명시 필수",
    [{"type": "answer_has_any", "name": "펀드명", "terms": [f2_abrv[:6]]},
     {"type": "note_any", "name": "비정형 한계", "terms": ["미수집", "비정형", "보유하지 않", "확인할 수 없", "한계", "없습니다"]}])
add("V2-O-03", "중", "구성종목×테마", "에코프로비엠이 편입된 국내 2차전지 ETF 알려줘", "answer", "편입∩상품명 2차전지",
    "공식 중 예시(캠브리콘) 구조 변형",
    [{"type": "sql_names", "name": "편입∩2차전지", "min_hit": 1,
      "sql": "SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM='에코프로비엠' AND e.pd_nm ILIKE '%2차전지%'"}])
add("V2-O-04", "상", "테마/이력", "최근 3개월 동안 로봇 테마와 연결 이력이 있는 ETF 정리해줘", "partial", "로봇 상품명 후보 + 이력 미수집",
    "공식 상 예시 변형 — 이력 데이터 없음을 밝히고 기준일 후보 제시",
    [{"type": "sql_names", "name": "로봇 ETF 후보", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND pd_nm ILIKE '%로봇%'"},
     {"type": "note_any", "name": "이력 한계", "terms": ["이력", "미수집", "기준일", "한계"]}])
add("V2-O-05", "상", "그룹/자회사", "LG의 자회사를 편입한 ETF 중 순자산이 큰 상품의 위험요인 알려줘", "partial",
    "LG 계열 편입 ETF 순자산 상위 + 위험등급·한계", "공식 상 예시(에코프로) 구조 변형 — 계열 판정은 회사명 접두 기준임을 명시",
    [{"type": "sql_names", "name": "LG 계열 편입 ETF", "min_hit": 1, "top": 10,
      # 질문이 '순자산이 큰 상품'을 물으므로 기대 상위 10 도 순자산 내림차순이어야 답과 같은 기준이 된다 (8/26 3차)
      "sql": "SELECT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM LIKE 'LG%' GROUP BY e.pd_abrv_nm, e.pd_nm ORDER BY max(TRY_CAST(e.pd_net_tamt AS DOUBLE)) DESC NULLS LAST"},
     {"type": "note_any", "name": "위험 관련 언급", "terms": ["위험등급", "위험", "파생", "한계", "기준"]}])
add("V2-O-06", "중", "구성종목/해외종목", "캠브리콘처럼 중국 AI 반도체 기업을 담은 국내 ETF 알려줘", "answer", "CAMBRICON 편입 ETF",
    "공식 중 예시 변형(한글 별칭 → ISIN)",
    [{"type": "sql_names", "name": "캠브리콘 편입", "min_hit": 1,
      "sql": "SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_CD='CNE1000041R8'"}])
add("V2-O-07", "중", "채권/복합", "원화채권 중 신용등급이 AA급이면서 표면금리 4% 이상인 것 알려줘", "answer", "AA+·AA·AA-(rank 2~4) AND SRFC>=4",
    "'AA급'=AA-, 포함(8/14 해석)",
    [{"type": "sql_names", "name": "AA급·4%이상", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND drv_maturity_status='active' AND TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 2 AND 4 AND TRY_CAST(SRFC_IRT AS DOUBLE)>=4"}])
add("V2-O-08", "하", "펀드/조건", "공모펀드 중에 국내에 투자하는 채권형 펀드 알려줘", "answer", "ovrs=국내·채권형·판매중", "속성 필터",
    [{"type": "sql_names", "name": "국내 채권형", "min_hit": 1,
      "sql": "SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE sale_yn='판매중' AND ovrs_fd_desc='국내' AND or_attr_desc='채권형'"}])
add("V2-O-09", "중", "국내ETF/집계", "국내 ETF 중에 순자산이 1조원 넘는 상품은 몇 개야?", "answer", "du_last_aum>=1e12 건수", "단위 원",
    [{"type": "any_of", "name": "1조 이상 건수", "checks": [
        {"type": "sql_number", "name": "ETF 상장중",
         "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND TRY_CAST(du_last_aum AS DOUBLE)>=1e12"},
        {"type": "sql_number", "name": "ETF 전체",
         "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND TRY_CAST(du_last_aum AS DOUBLE)>=1e12"},
        {"type": "sql_number", "name": "ETF+ETN", "sql": "SELECT count(*) FROM kr_etp WHERE TRY_CAST(du_last_aum AS DOUBLE)>=1e12"},
        {"type": "sql_number", "name": "ETF 상장중(순자산총액)", "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND TRY_CAST(pd_net_tamt AS DOUBLE)>=1e12"},
        {"type": "sql_number", "name": "ETF 전체(순자산총액)", "sql": "SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND TRY_CAST(pd_net_tamt AS DOUBLE)>=1e12"}]}])
add("V2-O-10", "하", "해외ETF/지역", "해외 ETF 중에 일본에 투자하는 상품 알려줘", "answer", "wu_inv_rgn Japan", "투자지역 필터",
    [{"type": "sql_names", "name": "일본 투자 해외 ETF", "min_hit": 1,
      "sql": "SELECT pd_nm FROM global_etf WHERE wu_inv_rgn ILIKE '%Japan%'"}, src("PREF02N001")])

# ---------------------------------------------------------------------------
# 저장 + 검사표 자체 검증(모든 SQL 실행 가능 · sql_names 는 최소 1행)
# ---------------------------------------------------------------------------


def validate(check):
    if check["type"] == "any_of":
        return all(validate(c) for c in check["checks"])
    if check["type"] in ("sql_names", "sql_number"):
        rows = q(check["sql"])
        if check["type"] == "sql_names":
            assert rows, ("sql_names 0행", check["name"])
        else:
            assert rows and rows[0][0] is not None, ("sql_number 없음", check["name"])
    return True


for c in checks:
    for ch in c["checks"]:
        validate(ch)
assert len(items) == 80, len(items)
assert len({it["id"] for it in items}) == 80
with io.open(OUT_EVAL, "w", encoding="utf-8", newline="\n") as fh:
    for it in items:
        fh.write(json.dumps(it, ensure_ascii=False) + "\n")
with io.open(OUT_CHECKS, "w", encoding="utf-8", newline="\n") as fh:
    for c in checks:
        fh.write(json.dumps(c, ensure_ascii=False) + "\n")
print("생성 완료:", len(items), "문항 ·", dict(Counter(it["level"] for it in items)),
      "·", dict(Counter(it["behavior"] for it in items)))
