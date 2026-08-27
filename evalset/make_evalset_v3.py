# -*- coding: utf-8 -*-
"""
블라인드 평가셋 v3 생성기 — v1(105/105)·v2(80/80) 포화 후의 새 시험지 80문항 (8/26).

무엇: 실력을 재려면 처음 보는 문제가 필요하다. v2 와 같은 원칙(문항·검사표를 같은 SQL 로
      생성, 데이터가 정답을 정함)에 새 씨앗과 새 구성을 얹는다.
왜  : 8/26 조사 결론 — 시험지가 포화되면 성능을 잴 수단이 없다. 국제 시험 방법론
      (CheckList)의 '불변 시험'(같은 뜻 다른 표현이면 답도 같아야 함)을 대폭 확장하고,
      주최 기술 세션이 예시로 든 3단 순회형(다단계) 질문을 처음으로 문항화한다.
구성(80): A 새 씨앗 무작위 표본 26(하 12·중 10·상 4 — 회귀 감시)
         · P 표현 변형 25(유의어·단위 변형·어순·붙여쓰기 — 불변 시험)
         · C 3단 순회형 14(상 — "X 편입 ETF 중 순자산 1위의 운용사" 류)
         · T 함정 신종 15(실명 변형·없는 필드·범위 밖·미래·실시간·행위·전망)
실행: python evalset/make_evalset_v3.py   → evalset/evalset_v3.jsonl, evalset/checks_v3.jsonl
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

SEED = 20260826
OUT_EVAL = os.path.join(HERE, "evalset_v3.jsonl")
OUT_CHECKS = os.path.join(HERE, "checks_v3.jsonl")

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
    d = re.sub(r"\D", "", str(raw))
    y, m, dd = d[:4], d[4:6], d[6:8]
    return [f"{y}-{m}-{dd}", f"{y}{m}{dd}", f"{y}.{m}.{dd}", f"{y}년 {int(m)}월 {int(dd)}일",
            f"{y}년 {m}월 {dd}일", f"{y}/{m}/{dd}"]


def pick(rows, n):
    rows = list(rows)
    return rng.sample(rows, min(n, len(rows)))


def src(*tables):
    return {"type": "evidence_source_any", "name": "근거 출처", "sources": list(tables)}


def nospace(s):
    return re.sub(r"\s+", "", s)


MGMT = [("미래에셋자산운용", "미래에셋"), ("KB자산운용", "KB"), ("한국투자신탁운용", "한국투자"),
        ("한화자산운용", "한화"), ("신한자산운용", "신한"), ("키움투자자산운용", "키움")]


def mgmt_where(raw):
    return f"coalesce(m.resolved, e.cu_fund_mgmt_co)='{esc(raw)}'"


def stock_code(name):
    rows = q(f"SELECT DISTINCT COMPST_ISU_CD FROM etf_constituent WHERE COMPST_ISU_NM='{esc(name)}' AND SECUGRP_ID='ST'")
    assert len(rows) == 1, (name, rows)
    return rows[0][0]


def holders_sql(code):
    return (f"SELECT DISTINCT e.pd_abrv_nm, c.etf_name FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin "
            f"WHERE c.COMPST_ISU_CD='{esc(code)}'")


AUM_COLS = ("du_last_aum", "pd_net_tamt")

# ---------------------------------------------------------------------------
# A. 새 씨앗 무작위 표본 26 (하 12 · 중 10 · 상 4) — 회귀 감시
# ---------------------------------------------------------------------------
etfs = q("""SELECT e.pd_itm_no, e.pd_abrv_nm, e.pd_nm, coalesce(m.resolved, e.cu_fund_mgmt_co), e.pd_lstg_dt
            FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no)
            WHERE e.drv_instrument_type='ETF' AND e.drv_listing_status='active'
              AND length(e.pd_abrv_nm) BETWEEN 6 AND 18 AND e.du_last_aum IS NOT NULL
              AND coalesce(m.resolved, e.cu_fund_mgmt_co) IS NOT NULL ORDER BY e.pd_itm_no""")
for i, (pid, abrv, name, mgmt, lstg) in enumerate(pick(etfs, 3), 1):
    add(f"V3-L-{i:02d}", "하", "국내ETF/운용사", f"{abrv} 운용사가 어디야?", "answer",
        f"{mgmt}", "무작위 표본 ETF 의 운용사(복구값 기준)",
        [{"type": "answer_has_any", "name": "운용사명", "terms": [mgmt]}, src("PREF01N001")])
for i, (pid, abrv, name, mgmt, lstg) in enumerate(pick(etfs, 2), 4):
    add(f"V3-L-{i:02d}", "하", "국내ETF/상장일", f"{abrv}은 언제 상장됐어?", "answer",
        f"상장일 {lstg}", "pd_lstg_dt",
        [{"type": "answer_has_any", "name": "상장일", "terms": date_variants(lstg)}, src("PREF01N001")])

bonds = q("""SELECT PD_NO, PD_ABRV_NM, MAT_DT, drv_crd_grd_norm FROM kr_bond
             WHERE drv_maturity_status='active' AND STD_PD_MCLS_NM='회사채' AND drv_is_perpetual<>'Y'
               AND drv_crd_grd_norm IS NOT NULL AND MAT_DT IS NOT NULL
               AND length(PD_ABRV_NM) BETWEEN 5 AND 18 ORDER BY PD_NO""")
for i, (pno, abrv, mat, grade) in enumerate(pick(bonds, 2), 6):
    add(f"V3-L-{i:02d}", "하", "채권/만기", f"{abrv} 만기일이 언제야?", "answer", f"만기 {mat}", "MAT_DT",
        [{"type": "answer_has_any", "name": "만기일", "terms": date_variants(mat)}, src("PRBD01N001")])
for i, (pno, abrv, mat, grade) in enumerate(pick(bonds, 2), 8):
    add(f"V3-L-{i:02d}", "하", "채권/신용등급", f"{abrv} 신용등급이 뭐야?", "answer", f"{grade}", "drv_crd_grd_norm",
        [{"type": "answer_has_any", "name": "신용등급", "terms": [grade]}, src("PRBD01N001")])

funds = q("""SELECT itm_no, itm_abrv_nm, drv_risk_grade, zrin_fd_ivst_risk_grd_nm FROM fund_master
             WHERE sale_yn='판매중' AND drv_risk_grade IS NOT NULL AND zrin_fd_ivst_risk_grd_nm IS NOT NULL
               AND length(itm_abrv_nm) BETWEEN 8 AND 24 ORDER BY itm_no""")
for i, (ino, abrv, g, gname) in enumerate(pick(funds, 2), 10):
    add(f"V3-L-{i:02d}", "하", "펀드/위험등급", f"{abrv} 펀드 위험등급이 몇 등급이야?", "answer", f"{g}등급({gname})",
        "drv_risk_grade",
        [{"type": "answer_has_any", "name": "위험등급",
          "terms": [f"{g}등급", gname, f"등급: {g}", f"위험등급 {g}", f"위험등급: {g}"]}, src("PRFD01N001")])
add("V3-L-12", "하", "채권/필터", "표면금리가 7% 이상인 회사채 알려줘", "answer", "active 회사채 SRFC_IRT>=7",
    "만기 미경과 기준(새 문턱값 — v2 L-14 는 6%)",
    [{"type": "sql_names", "name": "조건 부합 회사채", "min_hit": 1,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE drv_maturity_status='active' AND STD_PD_MCLS_NM='회사채' AND TRY_CAST(SRFC_IRT AS DOUBLE)>=7"},
     src("PRBD01N001")])

stocks = q("""SELECT COMPST_ISU_NM, COMPST_ISU_CD, count(DISTINCT etf_isin) n FROM etf_constituent
              WHERE SECUGRP_ID='ST' AND length(COMPST_ISU_NM) BETWEEN 3 AND 10
              GROUP BY 1,2 HAVING count(DISTINCT etf_isin) BETWEEN 5 AND 60 ORDER BY 2""")
picked_stocks = pick(stocks, 5)
for i, (nm, cd, n) in enumerate(picked_stocks[:3], 1):
    add(f"V3-M-{i:02d}", "중", "구성종목/편입ETF", f"{nm} 담고 있는 ETF 뭐 있어?", "answer", f"편입 ETF {n}개", "KRX 수집분",
        [{"type": "sql_names", "name": "편입 ETF명", "min_hit": 1, "sql": holders_sql(cd)}])
for i, (nm, cd, n) in enumerate(picked_stocks[3:5], 4):
    add(f"V3-M-{i:02d}", "중", "구성종목/개수", f"{nm}을 편입한 ETF는 총 몇 개야?", "answer", f"{n}개", "수집분 기준 건수",
        [{"type": "sql_number", "name": "편입 ETF 수",
          "sql": f"SELECT count(DISTINCT etf_isin) FROM etf_constituent WHERE COMPST_ISU_CD='{esc(cd)}'"}])
(m_formal, m_raw) = pick(MGMT, 1)[0]
add("V3-M-06", "중", "운용사/집계", f"{m_formal}이 운용하는 국내 ETF는 몇 개야?", "answer", "운용 ETF 수",
    "정식 운용사명 → 원시 표기 별칭. ETF 만/ETF+ETN·전체/상장중 모두 허용",
    [{"type": "any_of", "name": "운용 상품 수", "checks": [
        {"type": "sql_number", "name": "ETF(상장중)",
         "sql": f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(m_raw)} AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active'"},
        {"type": "sql_number", "name": "ETF(전체)",
         "sql": f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(m_raw)} AND e.drv_instrument_type='ETF'"},
        {"type": "sql_number", "name": "ETF+ETN",
         "sql": f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(m_raw)}"}]}])
(m2_formal, m2_raw) = pick(MGMT, 1)[0]
add("V3-M-07", "중", "운용사/순위", f"{m2_formal}이 운용하는 ETF 중에 순자산이 제일 큰 건 뭐야?", "answer", "순자산 1위",
    "du_last_aum·pd_net_tamt 중 하나 기준",
    [{"type": "any_of", "name": "순자산 1위", "checks": [
        {"type": "sql_names", "name": f"1위({col})", "min_hit": 1, "top": 1,
         "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(m2_raw)} AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active' ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST"}
        for col in AUM_COLS]}])
etns = q("""SELECT pd_abrv_nm FROM kr_etp WHERE drv_instrument_type='ETN' AND drv_listing_status='active'
            AND length(pd_abrv_nm) BETWEEN 8 AND 22 ORDER BY pd_itm_no""")
(etn_abrv,) = pick(etns, 1)[0]
add("V3-M-08", "중", "국내ETF/유형", f"{etn_abrv}은 ETF야, ETN이야?", "answer", "ETN", "drv_instrument_type",
    [{"type": "answer_has_any", "name": "ETN 명시", "terms": ["ETN", "상장지수증권"]}])
wetfs = q("""SELECT c.etf_isin, e.pd_abrv_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin
             WHERE TRY_CAST(c.COMPST_RTO AS DOUBLE) IS NOT NULL AND c.SECUGRP_ID='ST' AND length(e.pd_abrv_nm) BETWEEN 6 AND 18
             GROUP BY 1,2 HAVING count(*) >= 10 ORDER BY 1""")
(w_isin, w_abrv) = pick(wetfs, 1)[0]
add("V3-M-09", "중", "구성종목/비중", f"{w_abrv} 구성종목 중에 비중이 제일 큰 종목이 뭐야?", "answer", "COMPST_RTO 1위", "수집분 비중",
    [{"type": "sql_names", "name": "비중 1위 종목", "min_hit": 1, "top": 1,
      "sql": f"SELECT COMPST_ISU_NM FROM etf_constituent WHERE etf_isin='{esc(w_isin)}' AND TRY_CAST(COMPST_RTO AS DOUBLE) IS NOT NULL ORDER BY TRY_CAST(COMPST_RTO AS DOUBLE) DESC"}])
new_themes = [t for t in ["조선", "인공지능", "금융", "게임", "수소"]
              if q(f"SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%{t}%'")[0][0] >= 3]
t0 = pick(new_themes, 1)[0]
add("V3-M-10", "중", "테마/상품명", f"{t0} 관련 국내 ETF 알려줘", "answer", f"상품명에 '{t0}'", "상품명 매칭(새 테마어)",
    [{"type": "sql_names", "name": f"'{t0}' ETF", "min_hit": 1,
      "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%{esc(t0)}%'"}])


def intersect_sql(a, b):
    return (f"SELECT DISTINCT e.pd_abrv_nm, e.pd_nm FROM kr_etp e WHERE e.pd_itm_no IN "
            f"(SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM='{esc(a)}') AND e.pd_itm_no IN "
            f"(SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM='{esc(b)}')")


pairs = [p for p in [("NAVER", "카카오"), ("셀트리온", "삼성바이오로직스"), ("POSCO홀딩스", "LG화학")]
         if q(f"SELECT count(*) FROM ({intersect_sql(*p)})")[0][0] >= 2][:1]
(pa, pb) = pairs[0]
add("V3-H-01", "상", "구성종목/교집합", f"{pa}랑 {pb} 둘 다 담고 있는 ETF 알려줘", "answer", "교집합", "KRX 수집분",
    [{"type": "sql_names", "name": "교집합 ETF", "min_hit": 1, "sql": intersect_sql(pa, pb)}])
top60 = q("""SELECT pd_abrv_nm, TRY_CAST(du_last_aum AS DOUBLE), TRY_CAST(pd_net_tamt AS DOUBLE) FROM kr_etp
             WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND length(pd_abrv_nm) BETWEEN 6 AND 18
             ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 60""")
(ca, caa, can), (cb, cba, cbn) = pick(top60, 2)
winners = sorted({ca if caa >= cba else cb, ca if (can or 0) >= (cbn or 0) else cb})
add("V3-H-02", "상", "국내ETF/비교", f"{ca}랑 {cb} 중에 순자산이 더 큰 건 어느 쪽이야?", "answer", "/".join(winners),
    "du_last_aum·pd_net_tamt 비교(둘 중 하나 기준)",
    [{"type": "answer_has_any", "name": "큰 쪽 이름", "terms": winners},
     {"type": "answer_has_any", "name": "비교 표현",
      "terms": ["큽니다", "더 큽", "더 크", "더 큰", "더 많", "큰 쪽", "큰 ETF", "큰 상품", "큰 것", "크다", "보다 큰", "보다 크", "앞선", "높"]}])
(m3_formal, m3_raw) = pick(MGMT, 1)[0]
t3 = "미국" if q(f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(m3_raw)} AND e.pd_nm ILIKE '%배당%'")[0][0] == 0 else "배당"
add("V3-H-03", "상", "운용사×테마", f"{m3_formal}의 {t3} ETF 있어?", "answer", f"{m3_raw} × '{t3}'", "운용사 별칭 + 상품명",
    [{"type": "sql_names", "name": "운용사×테마", "min_hit": 1,
      "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(m3_raw)} AND e.drv_instrument_type='ETF' AND e.pd_nm ILIKE '%{esc(t3)}%'"}])
add("V3-H-04", "상", "국내ETF/복합조건", "위험등급이 3등급인 국내 ETF 중에서 총보수가 0.3% 미만인 것 알려줘", "partial",
    "grade 3 AND 0<cu_charge_rt<0.3 (0 표기 제외)", "총보수 0 표기=미확정 정책 → 한계 명시 기대(새 조합)",
    [{"type": "sql_names", "name": "조건 부합", "min_hit": 1,
      "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND drv_risk_grade='3' AND TRY_CAST(cu_charge_rt AS DOUBLE)>0 AND TRY_CAST(cu_charge_rt AS DOUBLE)<0.3"},
     {"type": "note_any", "name": "총보수 한계", "terms": ["총보수", "보수", "0 표기", "미확정", "일부"]}])

# ---------------------------------------------------------------------------
# P. 표현 변형 25 — 불변 시험(같은 뜻 다른 표현 → 같은 검사표)
# ---------------------------------------------------------------------------
(v_nm, v_cd, v_n) = picked_stocks[0]
holder_check = [{"type": "sql_names", "name": "편입 ETF명", "min_hit": 1, "sql": holders_sql(v_cd)}]
add("V3-P-01", "중", "변형/유의어", f"{v_nm} 들어간 ETF 뭐 있어?", "answer", "편입 ETF", "'담다'의 유의어 '들어가다'", holder_check)
add("V3-P-02", "중", "변형/유의어", f"{v_nm}이 포함돼 있는 ETF 알려줘", "answer", "편입 ETF", "'담다'의 유의어 '포함되다'", holder_check)
add("V3-P-03", "중", "변형/유의어", f"{v_nm} 편입 ETF 목록 좀 알려줄래?", "answer", "편입 ETF", "명사형 '편입'+구어체", holder_check)

mgmt_count_checks = [{"type": "any_of", "name": "운용 상품 수", "checks": [
    {"type": "sql_number", "name": "ETF(상장중)",
     "sql": "SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE coalesce(m.resolved, e.cu_fund_mgmt_co)='미래에셋' AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active'"},
    {"type": "sql_number", "name": "ETF(전체)",
     "sql": "SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE coalesce(m.resolved, e.cu_fund_mgmt_co)='미래에셋' AND e.drv_instrument_type='ETF'"},
    {"type": "sql_number", "name": "ETF+ETN",
     "sql": "SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE coalesce(m.resolved, e.cu_fund_mgmt_co)='미래에셋'"}]}]
add("V3-P-04", "중", "변형/명사형", "미래에셋자산운용 ETF 개수 알려줘", "answer", "운용 ETF 수", "'몇 개'의 명사형 '개수'", mgmt_count_checks)
add("V3-P-05", "중", "변형/유의어", "미래에셋자산운용이 굴리는 국내 ETF 총 몇 개야?", "answer", "운용 ETF 수", "'운용하다'의 구어 '굴리다'", mgmt_count_checks)
add("V3-P-06", "중", "변형/붙여쓰기", nospace("미래에셋자산운용이 운용하는 국내 ETF는 몇 개야?"), "answer", "운용 ETF 수", "띄어쓰기 제거", mgmt_count_checks)

band_buyable_checks = [
    {"type": "sql_names", "name": "AA급·매수가능·만기미경과", "min_hit": 1,
     "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND drv_maturity_status='active' AND drv_is_buyable='Y' AND TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 2 AND 4"},
    {"type": "note_any", "name": "등급대 해석 명시", "terms": ["묶음", "AA-", "등급대", "해석"]}, src("PRBD01N001")]
add("V3-P-07", "중", "변형/유의어", "지금 살 수 있는 원화채권 중 AA급만 알려줘", "answer", "AA 등급대·매수가능", "'판매 가능'의 유의어 '살 수 있는'", band_buyable_checks)
add("V3-P-08", "중", "변형/붙여쓰기", "매수가능한원화채권중신용등급AA급인것알려줘", "answer", "AA 등급대·매수가능", "붙여쓰기+매수가능", band_buyable_checks)
add("V3-P-09", "중", "변형/어순", "원화 채권에서 AA 등급대만 골라줘, 살 수 있는 걸로", "answer", "AA 등급대·매수가능", "어순 도치+'등급대' 띄어쓰기", band_buyable_checks)


def aum_count_checks(op, threshold):
    subs = []
    for col in AUM_COLS:
        for st, stw in (("상장중", " AND drv_listing_status='active'"), ("전체", "")):
            subs.append({"type": "sql_number", "name": f"ETF {st}({col})",
                         "sql": f"SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF'{stw} AND TRY_CAST({col} AS DOUBLE){op}{threshold}"})
    return [{"type": "any_of", "name": "금액 조건 건수", "checks": subs}]


add("V3-P-10", "중", "변형/단위", "순자산 10000억 넘는 국내 ETF 몇 개야?", "answer", "1조=10000억", "단위 변형(조→억)", aum_count_checks(">=", "1e12"))
add("V3-P-11", "중", "변형/단위", "국내 ETF 가운데 순자산 5000억 이상은 총 몇 개?", "answer", ">=5000억 건수", "새 문턱값", aum_count_checks(">=", "5e11"))
add("V3-P-12", "중", "변형/단위", "순자산이 2조 이상인 국내 ETF 개수 알려줘", "answer", ">=2조 건수", "새 문턱값", aum_count_checks(">=", "2e12"))

fund_bond_checks = [{"type": "sql_names", "name": "국내 채권형(판매중)", "min_hit": 1,
                     "sql": "SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE sale_yn='판매중' AND ovrs_fd_desc='국내' AND or_attr_desc='채권형'"}]
add("V3-P-13", "하", "변형/구어체", "국내 투자하는 채권형 공모펀드 뭐 있어?", "answer", "국내·채권형", "'~에'-조사 생략 구어체", fund_bond_checks)
add("V3-P-14", "하", "변형/유의어", "가입 가능한 국내 채권형 펀드 알려줘", "answer", "국내·채권형·판매중", "'판매 중'의 유의어 '가입 가능'", fund_bond_checks)
add("V3-P-15", "하", "변형/붙여쓰기", nospace("공모펀드 중에 국내에 투자하는 채권형 펀드 알려줘"), "answer", "국내·채권형", "띄어쓰기 제거(v2 O-08 문형)", fund_bond_checks)


def prefix_holders_check(prefix):
    return [{"type": "sql_names", "name": f"{prefix} 접두 편입 ETF", "min_hit": 1, "top": 10,
             "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin WHERE c.COMPST_ISU_NM LIKE '{esc(prefix)}%' GROUP BY e.pd_abrv_nm, e.pd_nm ORDER BY max(TRY_CAST(e.pd_net_tamt AS DOUBLE)) DESC NULLS LAST"},
            {"type": "note_any", "name": "접두 근사 한계", "terms": ["자회사", "미수집", "시작", "근사", "한계"]}]


add("V3-P-16", "상", "변형/자회사", "삼성의 자회사를 담은 ETF 알려줘", "partial", "삼성% 접두 편입", "자회사 규칙의 다른 회사 일반화", prefix_holders_check("삼성"))
add("V3-P-17", "상", "변형/자회사", "LG 자회사가 들어간 ETF 뭐 있어?", "partial", "LG% 접두 편입", "조사 생략+유의어", prefix_holders_check("LG"))
add("V3-P-18", "상", "변형/자회사", "현대의 자회사를 편입한 ETF 있어?", "partial", "현대% 접두 편입", "다른 그룹 일반화", prefix_holders_check("현대"))


def theme_name_check(t):
    return [{"type": "sql_names", "name": f"'{t}' ETF", "min_hit": 1,
             "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%{esc(t)}%'"}]


for pid, t, wording in (("V3-P-19", "조선", "조선 관련 국내 ETF 알려줘"),
                        ("V3-P-20", "바이오", "바이오 테마 ETF 뭐 있어?"),
                        ("V3-P-21", "게임", "게임 산업에 투자하는 국내 ETF 있어?")):
    assert q(f"SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%{t}%'")[0][0] >= 1, t
    add(pid, "하", "변형/테마", wording, "answer", f"상품명 '{t}'", "테마어 일반화(사전 미등재 포함)", theme_name_check(t))


def top_aum_names(top):
    return [{"type": "any_of", "name": f"순자산 상위 {top}", "checks": [
        {"type": "sql_names", "name": f"상위 {top}({col})", "min_hit": 1 if top == 1 else 2, "top": top,
         "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' ORDER BY TRY_CAST({col} AS DOUBLE) DESC NULLS LAST"}
        for col in AUM_COLS]}]


add("V3-P-22", "중", "변형/외래어", "국내 ETF 순자산 톱3 알려줘", "answer", "상위 3", "'상위'의 외래어 '톱'", top_aum_names(3))
add("V3-P-23", "중", "변형/유의어", "순자산 기준으로 제일 큰 국내 ETF 뭐야?", "answer", "1위", "'가장'의 유의어 '제일'", top_aum_names(1))
add("V3-P-24", "상", "변형/붙여쓰기", nospace("순자산 상위 3개 국내 ETF의 운용사를 각각 알려줘"), "answer", "상위 3+운용사",
    "v2 H-09 붙여쓰기", top_aum_names(3) + [
        {"type": "sql_names", "name": "운용사명", "min_hit": 1, "top": 3,
         "sql": "SELECT coalesce(m.resolved, e.cu_fund_mgmt_co) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE e.drv_instrument_type='ETF' AND e.drv_listing_status='active' ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST"}])
add("V3-P-25", "하", "변형/붙여쓰기", nospace("지금 판매 중인 공모펀드는 몇 개야?"), "answer", "판매중 건수", "v2 L-13 붙여쓰기",
    [{"type": "any_of", "name": "판매중 건수", "checks": [
        {"type": "sql_number", "name": "마스터", "sql": "SELECT count(*) FROM fund_master WHERE sale_yn='판매중'"},
        {"type": "sql_number", "name": "클래스", "sql": "SELECT count(*) FROM fund_class WHERE sale_yn='판매중'"}]}])

# ---------------------------------------------------------------------------
# C. 3단 순회형 14 (상) — 관계→순위→속성 다단계. 주최 기술 세션 예시 유형의 첫 문항화.
# ---------------------------------------------------------------------------
CD_SAMSUNG = stock_code("삼성전자")
CD_HYNIX = stock_code("SK하이닉스")
CD_HYUNDAI = stock_code("현대차")
CD_KIA = stock_code("기아")


def top_holder_attr_checks(where_in, attr_sql, name):
    """편입 ETF 를 순자산 내림차순 1위로 좁힌 뒤 그 상품의 속성을 묻는 검사(두 순자산 열 모두 인정)."""
    return [{"type": "any_of", "name": name, "checks": [
        {"type": "sql_names", "name": f"{name}({col})", "min_hit": 1, "top": 1,
         "sql": attr_sql.format(where_in=where_in, col=col)} for col in AUM_COLS]}]


MGMT_OF_TOP = ("SELECT coalesce(m.resolved, e.cu_fund_mgmt_co) FROM kr_etp e "
               "LEFT JOIN mgmt_resolved m ON m.pd_itm_no=e.pd_itm_no WHERE e.pd_itm_no IN ({where_in}) "
               "ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST")
NAME_OF_TOP = ("SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e WHERE e.pd_itm_no IN ({where_in}) "
               "ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST")
GRADE_OF_TOP = ("SELECT e.drv_risk_grade || '등급' FROM kr_etp e WHERE e.pd_itm_no IN ({where_in}) "
                "AND e.drv_risk_grade IS NOT NULL ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST")

IN_ECO = "SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_NM LIKE '에코프로%'"
add("V3-C-01", "상", "3단/자회사→순위→운용사", "에코프로의 자회사를 편입한 ETF 중 순자산이 가장 큰 상품의 운용사는 어디야?",
    "partial", "접두 편입 → 순자산 1위 → 운용사", "주최 기술 세션 3단 순회 예시 그대로(자회사 관계는 접두 근사 한계 명시)",
    top_holder_attr_checks(IN_ECO, MGMT_OF_TOP, "1위 운용사")
    + [{"type": "note_any", "name": "접두 근사 한계", "terms": ["자회사", "미수집", "시작", "근사", "한계"]}])
IN_SAMSUNG = f"SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{CD_SAMSUNG}'"
add("V3-C-02", "상", "3단/편입→순위→운용사", "삼성전자를 담은 ETF 중에서 순자산이 제일 큰 상품의 운용사를 알려줘",
    "answer", "편입 → 순자산 1위 → 운용사", "3단 순회(실데이터 관계)",
    top_holder_attr_checks(IN_SAMSUNG, MGMT_OF_TOP, "1위 운용사"))
IN_HYNIX = f"SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{CD_HYNIX}'"
add("V3-C-03", "상", "3단/편입→보수→상품", "SK하이닉스를 담은 ETF 중에서 총보수가 가장 낮은 상품은 뭐야?",
    "partial", "편입 → 총보수 최저(0 표기 제외)", "총보수 0=미확정 정책과 결합된 다단계 — 한계 명시 기대",
    [{"type": "sql_names", "name": "보수 최저", "min_hit": 1, "top": 3,
      "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e WHERE e.pd_itm_no IN ({IN_HYNIX}) AND TRY_CAST(e.cu_charge_rt AS DOUBLE)>0 ORDER BY TRY_CAST(e.cu_charge_rt AS DOUBLE) ASC, e.pd_itm_no"},
     {"type": "note_any", "name": "보수 한계", "terms": ["총보수", "보수", "0 표기", "미확정", "값 보유"]}])
add("V3-C-04", "상", "3단/운용사→편입→비중", "미래에셋자산운용이 운용하는 ETF 중에서 삼성전자 비중이 가장 큰 상품은 뭐야?",
    "answer", "운용사 × 편입 → 비중 1위", "운용사 필터 + 비중 정렬",
    [{"type": "sql_names", "name": "비중 1위", "min_hit": 1, "top": 1,
      "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM etf_constituent c JOIN kr_etp e ON e.pd_itm_no=c.etf_isin LEFT JOIN mgmt_resolved m ON m.pd_itm_no=e.pd_itm_no WHERE c.COMPST_ISU_CD='{CD_SAMSUNG}' AND coalesce(m.resolved, e.cu_fund_mgmt_co)='미래에셋' ORDER BY TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) DESC NULLS LAST"}])
IN_HYUNDAI = f"SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{CD_HYUNDAI}'"
add("V3-C-05", "상", "3단/편입→순위→위험등급", "현대차를 편입한 ETF 중 순자산 1위 상품의 위험등급은 몇 등급이야?",
    "answer", "편입 → 순자산 1위 → 위험등급", "3단 순회",
    top_holder_attr_checks(IN_HYUNDAI, GRADE_OF_TOP, "1위 위험등급"))
add("V3-C-06", "상", "3단/등급대→금리→종목", "AA급 원화채권 중에 표면금리가 제일 높은 종목이 뭐야?",
    "answer", "AA 등급대 → 표면금리 최고", "등급대 해석 + 정렬 결합",
    [{"type": "sql_names", "name": "표면금리 최고", "min_hit": 1, "top": 3,
      "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE CURR_CD='KRW' AND drv_maturity_status='active' AND TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 2 AND 4 ORDER BY TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST, PD_NO"}])
add("V3-C-07", "상", "3단/펀드→순위→위험등급", "국내 채권형 펀드 중에서 순자산이 가장 큰 상품의 위험등급은?",
    "answer", "국내·채권형 → 순자산 1위 → 위험등급", "펀드 다단계",
    [{"type": "any_of", "name": "1위 위험등급", "checks": [
        {"type": "sql_names", "name": f"1위 등급({tag})", "min_hit": 1, "top": 1,
         "sql": f"SELECT drv_risk_grade || '등급' FROM fund_master WHERE ovrs_fd_desc='국내' AND or_attr_desc='채권형' AND drv_risk_grade IS NOT NULL{cond} ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST"}
        for tag, cond in (("판매중", " AND sale_yn='판매중'"), ("전체", ""))]}])
add("V3-C-08", "상", "3단/테마→순위→구성", "2차전지 ETF 중에서 순자산이 제일 큰 상품의 구성종목 상위 3개 알려줘",
    "answer", "테마 → 순자산 1위 → 구성 상위 3", "테마·순위·구성 결합",
    [{"type": "any_of", "name": "1위 상품의 구성 상위", "checks": [
        {"type": "sql_names", "name": f"구성 상위({col})", "min_hit": 1, "top": 3,
         "sql": f"SELECT COMPST_ISU_NM FROM etf_constituent WHERE etf_isin=(SELECT pd_itm_no FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND pd_nm ILIKE '%2차전지%' ORDER BY TRY_CAST({col} AS DOUBLE) DESC NULLS LAST LIMIT 1) AND TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) IS NOT NULL ORDER BY TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) DESC"}
        for col in AUM_COLS]}])
IN_BOTH = (f"SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{CD_SAMSUNG}' "
           f"INTERSECT SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{CD_HYNIX}'")
add("V3-C-09", "상", "3단/교집합→순위→상품", "삼성전자랑 SK하이닉스 둘 다 담은 ETF 중 순자산이 가장 큰 건 뭐야?",
    "answer", "교집합 → 순자산 1위", "교집합+순위 결합",
    top_holder_attr_checks(IN_BOTH, NAME_OF_TOP, "교집합 1위"))
kiwoom_tops = {col: q(f"SELECT e.pd_abrv_nm, e.pd_lstg_dt FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) "
                      f"WHERE {mgmt_where('키움')} AND e.drv_instrument_type='ETF' AND e.drv_listing_status='active' "
                      f"ORDER BY TRY_CAST(e.{col} AS DOUBLE) DESC NULLS LAST LIMIT 1")[0] for col in AUM_COLS}
kiwoom_dates = sorted({d for _n, d in kiwoom_tops.values()})
add("V3-C-10", "상", "3단/운용사→순위→상장일", "키움투자자산운용 ETF 중 순자산 1위 상품의 상장일 알려줘",
    "answer", "운용사 → 순자산 1위 → 상장일", "운용사 순위 + 속성",
    [{"type": "answer_has_any", "name": "1위 상장일",
      "terms": [v for d in kiwoom_dates for v in date_variants(d)]}])
add("V3-C-11", "상", "3단/등급→순위→상품", "위험등급이 1등급인 국내 ETF 중에서 순자산이 가장 큰 상품은 뭐야?",
    "answer", "1등급 → 순자산 1위", "필터+순위 결합",
    [{"type": "any_of", "name": "1등급 1위", "checks": [
        {"type": "sql_names", "name": f"1위({col})", "min_hit": 1, "top": 1,
         "sql": f"SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' AND drv_risk_grade='1' ORDER BY TRY_CAST({col} AS DOUBLE) DESC NULLS LAST"}
        for col in AUM_COLS]}])
n_mirae_battery = q("SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) "
                    "WHERE coalesce(m.resolved, e.cu_fund_mgmt_co)='미래에셋' AND e.pd_nm ILIKE '%2차전지%'")[0][0]
assert n_mirae_battery >= 1, n_mirae_battery
add("V3-C-12", "상", "3단/운용사×테마→구성", "미래에셋자산운용의 2차전지 ETF 구성종목 중 비중 1위는 뭐야?",
    "answer", "운용사×테마 → 구성 비중 1위", "운용사·테마·구성 결합",
    [{"type": "sql_names", "name": "비중 1위", "min_hit": 1, "top": 3,
      "sql": "SELECT c.COMPST_ISU_NM FROM etf_constituent c WHERE c.etf_isin IN (SELECT e.pd_itm_no FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE coalesce(m.resolved, e.cu_fund_mgmt_co)='미래에셋' AND e.pd_nm ILIKE '%2차전지%') AND TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) IS NOT NULL ORDER BY TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) DESC"}])
# 총보수는 실질결측 87.5% — 값(>0) 보유 상품이 있는 운용사를 골라야 검사가 성립한다
fee_formal, fee_raw = next(
    (f, r) for f, r in MGMT
    if q(f"SELECT count(*) FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(r)} "
         f"AND e.drv_instrument_type='ETF' AND TRY_CAST(e.cu_charge_rt AS DOUBLE)>0")[0][0] >= 1)
add("V3-C-13", "상", "3단/운용사→보수→상품", f"{fee_formal} ETF 중에서 총보수가 가장 낮은 상품 알려줘",
    "partial", "운용사 → 총보수 최저(0 표기 제외)", "총보수 0=미확정 정책 결합 — 한계 명시 기대",
    [{"type": "sql_names", "name": "보수 최저", "min_hit": 1, "top": 3,
      "sql": f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e LEFT JOIN mgmt_resolved m USING(pd_itm_no) WHERE {mgmt_where(fee_raw)} AND e.drv_instrument_type='ETF' AND TRY_CAST(e.cu_charge_rt AS DOUBLE)>0 ORDER BY TRY_CAST(e.cu_charge_rt AS DOUBLE) ASC, e.pd_itm_no"},
     {"type": "note_any", "name": "보수 한계", "terms": ["총보수", "보수", "0 표기", "미확정", "값 보유"]}])
def pair_fee_sql(cd_a, cd_b):
    in_pair = (f"SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{cd_a}' "
               f"INTERSECT SELECT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD='{cd_b}'")
    return (f"SELECT e.pd_abrv_nm, e.pd_nm FROM kr_etp e WHERE e.pd_itm_no IN ({in_pair}) "
            f"AND TRY_CAST(e.cu_charge_rt AS DOUBLE)>0 ORDER BY TRY_CAST(e.cu_charge_rt AS DOUBLE) ASC, e.pd_itm_no")


# 교집합에 총보수 값(>0) 보유 상품이 있는 종목쌍을 고른다 — 현대차·기아가 비면 반도체 쌍으로
pair_names, pair_sql = ("현대차", "기아"), pair_fee_sql(CD_HYUNDAI, CD_KIA)
if not q(pair_sql):
    pair_names, pair_sql = ("삼성전자", "SK하이닉스"), pair_fee_sql(CD_SAMSUNG, CD_HYNIX)
add("V3-C-14", "상", "3단/교집합→보수→상품", f"{pair_names[0]}와 {pair_names[1]}를 둘 다 담은 ETF 중 총보수가 제일 낮은 건 뭐야?",
    "partial", "교집합 → 총보수 최저(0 표기 제외)", "v1 H-03 유형의 다른 종목 일반화",
    [{"type": "sql_names", "name": "교집합 보수 최저", "min_hit": 1, "top": 3, "sql": pair_sql},
     {"type": "note_any", "name": "보수 한계", "terms": ["총보수", "보수", "0 표기", "미확정", "값 보유"]}])

# ---------------------------------------------------------------------------
# T. 함정 신종 15 — 전부 refuse
# ---------------------------------------------------------------------------


def assert_absent(pattern):
    pat = esc(pattern.replace(" ", ""))
    n = q("SELECT count(*) FROM (SELECT pd_nm AS n FROM kr_etp UNION ALL SELECT pd_abrv_nm FROM kr_etp UNION ALL "
          "SELECT pd_nm FROM global_etf UNION ALL SELECT itm_nm FROM fund_master UNION ALL SELECT PD_NM FROM kr_bond) "
          f"WHERE replace(n,' ','') ILIKE '%{pat}%'")[0][0]
    assert n == 0, (pattern, n)


REFUSE_CHECK = [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}]
for pat in ("TIGER 미국S&P600", "TIGER 코스닥250", "설악산자산운용"):
    assert_absent(pat)
traps = [
    ("실명 변형 상품", "TIGER 미국S&P600 순자산 알려줘", "S&P500 만 존재 — 600 은 없음"),
    ("실명 변형 상품", "TIGER 코스닥250 알려줘", "코스닥150 만 존재"),
    ("없는 운용사", "설악산자산운용 ETF 알려줘", "존재하지 않는 운용사"),
    # 8/27 재배포본: 분배수익률·분배금·지급월 필드 신설 — 배당수익률/분배금 질의는 함정에서
    # 제외(정상 조회로 전환). 여전히 없는 배당락·분배락 일자만 함정으로 유지한다.
    ("데이터 밖 항목", "KODEX 2차전지산업 배당락일이 언제야?", "배당락(분배락) 일자 필드 없음"),
    ("데이터 밖 항목", "TIGER 200 분배락 일자 알려줘", "배당락(분배락) 일자 필드 없음 — 어휘 변형"),
    ("데이터 밖 항목", "KODEX 200 공매도 잔고 알려줘", "공매도 잔고 필드 없음"),
    ("범위 밖 값", "위험등급 0등급인 국내 ETF 알려줘", "위험등급은 1~6"),
    ("범위 밖 값", "신용등급 AAAA인 채권 있어?", "AAAA 는 등급 체계에 없음"),
    ("미래 시점", "다음 주에 상장하는 국내 ETF 뭐야?", "기준일 이후는 알 수 없음"),
    ("실시간", "지금 삼성전자 주가 얼마야?", "실시간 시세 미보유"),
    ("행위 요청", "TIGER 200 전부 팔아줘", "매매 행위 수행 불가"),
    ("행위 요청", "미래에셋증권 계좌 개설해줘", "계좌 개설 행위 수행 불가"),
    ("추천·전망", "다음 달에 오를 채권 추천해줘", "전망·추천 금지"),
    ("이력", "KODEX 200의 작년 구성종목이랑 지금 비교해줘", "구성종목 이력 미수집(기준일 1개)"),
    ("추천·개인화", "나한테 맞는 펀드 하나 골라줘", "개인 맞춤 권유 — 데이터 근거 없음"),
]
for i, (cat, qq, why) in enumerate(traps, 1):
    add(f"V3-T-{i:02d}", "트랩", f"함정/{cat}", qq, "refuse", "확인할 수 없음 거절", why, REFUSE_CHECK)

# ---------------------------------------------------------------------------
# 저장 + 검사표 자체 검증
# ---------------------------------------------------------------------------


def validate(check):
    if check["type"] == "any_of":
        oks = []
        for c in check["checks"]:
            oks.append(validate(c))
        assert any(oks), ("any_of 전부 빈 결과", check["name"])
        return True
    if check["type"] in ("sql_names", "sql_number"):
        rows = q(check["sql"])
        if check["type"] == "sql_names":
            if not rows:
                return False
        else:
            if not rows or rows[0][0] is None:
                return False
    return True


for c in checks:
    for ch in c["checks"]:
        assert validate(ch), (c["id"], ch.get("name"))
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
