# -*- coding: utf-8 -*-
"""블라인드 2바퀴(claude r2, 8/28) — 30문항 채점표 생성.

1바퀴와 같은 원칙: 기대값은 DuckDB 원본에서 독립 계산, 해석 폭은 any_of·부분집합-안전.
표적: 1바퀴에서도 안 건드린 항목 — 거래량·세후수익률·듀레이션·레버리지 배수·분배 지급월·
상장 연도·판매보수 분해·해외 지역×유형·코스닥 비중·기간별 수익률(3개월) 등.

실행: python evalset/make_evalset_claude_r2.py
산출: evalset/evalset_claude_r2.jsonl · evalset/checks_claude_r2.jsonl
"""
import io
import json
import os
import re

import duckdb


def date_variants(d):
    """'20321021' 같은 날짜의 표기 변형(ISO·한국어) — 채점표는 표기 차이를 오답으로 잡지 않는다."""
    ds = str(d)
    digits = re.sub(r"\D", "", ds)
    out = {ds}
    if len(digits) == 8:
        out.update({digits, f"{digits[:4]}-{digits[4:6]}-{digits[6:]}",
                    f"{digits[:4]}년 {int(digits[4:6])}월 {int(digits[6:])}일"})
    return sorted(out)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "storage", "output", "products.duckdb")
EVALSET_PATH = os.path.join(HERE, "evalset_claude_r2.jsonl")
CHECKS_PATH = os.path.join(HERE, "checks_claude_r2.jsonl")


def one(con, sql):
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def build():
    con = duckdb.connect(DB, read_only=True)

    # --- 출제 시점 사실 고정(어긋나면 데이터 판이 다른 것)
    n_special = one(con, "SELECT count(*) FROM kr_bond WHERE STD_PD_MCLS_NM='특수채'")
    assert n_special == 6100, n_special
    n_2026 = one(con, "SELECT count(*) FROM kr_etp WHERE pd_lstg_dt >= '2026-01-01' AND drv_listing_status='active'")
    assert n_2026 == 176, n_2026
    n_inv_gl = one(con, "SELECT count(*) FROM global_etf WHERE drv_is_inverse='Y'")
    assert n_inv_gl == 183, n_inv_gl
    n_online = one(con, "SELECT count(*) FROM fund_class WHERE han_clas_sales_channel ILIKE '%온라인%'")
    assert n_online == 3548, n_online
    kodex_inv_vol = one(con, "SELECT TRY_CAST(du_vol_1d AS DOUBLE) FROM kr_etp WHERE pd_abrv_nm='KODEX 인버스'")
    assert kodex_inv_vol and kodex_inv_vol > 0
    n_dur10 = one(con, "SELECT count(*) FROM kr_bond WHERE TRY_CAST(DUR AS DOUBLE) > 10")
    assert n_dur10 and n_dur10 > 0, n_dur10
    n_us_etn = one(con, "SELECT count(*) FROM global_etf WHERE wu_inv_rgn='United States of America' AND drv_is_etn='Y'")
    assert n_us_etn and n_us_etn > 0, n_us_etn
    n_jun_div = one(con, ("SELECT count(*) FROM kr_etp WHERE pd_lstg_dt >= '2026-06-01' "
                          "AND TRY_CAST(pd_dvid_pay_cnt AS DOUBLE) >= 1 AND drv_listing_status='active'"))
    assert n_jun_div and n_jun_div > 0, n_jun_div
    n_hedged = one(con, ("SELECT count(*) FROM kr_etp WHERE pd_nm LIKE '%(H)%' AND drv_listing_status='active' "
                         "AND (pd_nm LIKE '%미국%' OR pd_nm LIKE '%S&P%' OR pd_nm LIKE '%나스닥%')"))
    assert n_hedged and n_hedged > 0, n_hedged
    n_btc_kr = one(con, "SELECT count(*) FROM kr_etp WHERE pd_nm ILIKE '%비트코인%'")
    assert n_btc_kr == 0, f"국내 비트코인 상품이 존재({n_btc_kr}) — 트랩 문항 성립 안 함"
    # 포스코퓨처엠 편입 순자산 1위의 분배수익률(문항 R2-19 검사 방식 분기용)
    top19 = con.execute(
        "SELECT c.etf_name, e.pd_abrv_nm, TRY_CAST(e.pd_dvid_yield AS DOUBLE) FROM "
        "(SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='포스코퓨처엠') c "
        "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE e.drv_listing_status='active' "
        "ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1").fetchone()
    # 특수채 표면금리 1위의 만기일(R2-20)
    top20 = con.execute(
        "SELECT PD_ABRV_NM, PD_NM, MAT_DT FROM kr_bond WHERE STD_PD_MCLS_NM='특수채' "
        "ORDER BY TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST LIMIT 1").fetchone()
    # 한화에어로스페이스 편입 순자산 1위의 상장일(R2-24)
    top24 = con.execute(
        "SELECT c.etf_name, e.pd_abrv_nm, e.pd_lstg_dt FROM "
        "(SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='한화에어로스페이스') c "
        "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE e.drv_listing_status='active' "
        "ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1").fetchone()

    ev, ck = [], []

    def add(id_, level, category, question, behavior, gold, basis, checks):
        ev.append({"id": id_, "level": level, "category": category, "question": question,
                   "channels": [], "behavior": behavior, "gold": gold, "basis": basis})
        ck.append({"id": id_, "checks": checks})

    # ---------------------------------------------------------------- 하 ----
    add("R2-01", "하", "ETP/거래량", "거래량 제일 많은 국내 ETF 뭐야?",
        "answer", "1일 거래량(du_vol_1d) 내림차순 1위", "한 번도 안 다룬 거래량 정렬",
        [{"type": "sql_names", "name": "거래량 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_listing_status='active' "
                  "AND drv_instrument_type='ETF' ORDER BY TRY_CAST(du_vol_1d AS DOUBLE) DESC NULLS LAST LIMIT 1")}])

    add("R2-02", "하", "채권/개수", "특수채가 총 몇 종목이야?",
        "answer", "특수채 6,100종", "대분류 개수",
        [{"type": "answer_has_any", "name": "6,100", "terms": ["6,100", "6100"]}])

    add("R2-03", "하", "ETP/상장일", "올해 상장한 국내 ETF 몇 개나 돼?",
        "answer", "2026년 상장·상장중 176종(전체 상태 포함 해석도 허용)", "연도 구간 + 개수",
        [{"type": "any_of", "name": "ETF만(124)·ETP 전체(176)·전체 상태 수 어느 해석이든",
          "checks": [{"type": "answer_has_any", "name": "124/176", "terms": ["124", "176"]},
                     {"type": "sql_number", "name": "전체 상태 수",
                      "sql": "SELECT count(*) FROM kr_etp WHERE pd_lstg_dt >= '2026-01-01'"}]}])

    add("R2-04", "하", "ETP/분배", "7월에 분배금 주는 ETF 있어?",
        "answer", "지급월에 July 포함 851종 중 일부 제시", "신설 지급월 항목",
        [{"type": "sql_names", "name": "7월 지급", "min_hit": 2,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE pd_dvid_pay_months LIKE '%July%' "
                  "AND drv_listing_status='active'")}])

    add("R2-05", "하", "해외/개수", "인버스 해외 ETF는 몇 개야?",
        "answer", "해외 인버스 183종(ETF만 세면 166 — ETF/ETN 혼재라 두 해석 모두 정답)", "해외 파생 속성 개수",
        [{"type": "answer_has_any", "name": "183 또는 166", "terms": ["183", "166"]}])

    add("R2-06", "하", "ETP/레버리지", "3배 레버리지 ETF도 있어?",
        "answer", "레버리지 배수 3인 상장 ETP 16종(ETN 포함) — 있으면 예시 제시", "배수 필터",
        [{"type": "sql_names", "name": "3배 상품", "min_hit": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE TRY_CAST(cu_lev_fector AS DOUBLE)=3 "
                  "AND drv_listing_status='active'")}])

    add("R2-07", "하", "채권/세후수익률", "세후 수익률이 제일 높은 채권 뭐야?",
        "partial", "AFTER_TAX_YIELD 값 보유분(326종) 중 최고 — 값 없는 종목 다수라 한계 명시",
        "안 다룬 세후수익률 정렬 + 결측 한계",
        [{"type": "sql_names", "name": "세후수익률 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE TRY_CAST(AFTER_TAX_YIELD AS DOUBLE) IS NOT NULL "
                  "AND TRY_CAST(AFTER_TAX_YIELD AS DOUBLE)<>0 ORDER BY TRY_CAST(AFTER_TAX_YIELD AS DOUBLE) DESC LIMIT 1")},
         {"type": "sql_number", "name": "세후수익률 값",
          "sql": ("SELECT max(TRY_CAST(AFTER_TAX_YIELD AS DOUBLE)) FROM kr_bond "
                  "WHERE TRY_CAST(AFTER_TAX_YIELD AS DOUBLE)<>0")}])

    add("R2-08", "하", "펀드/벤치마크", "코스피200을 벤치마크로 쓰는 펀드 알려줘",
        "answer", "벤치마크 표기에 코스피200/KOSPI200 포함 2,880클래스 중 일부", "벤치마크 검색",
        [{"type": "sql_names", "name": "코스피200 벤치마크", "min_hit": 2,
          "sql": ("SELECT itm_abrv_nm, itm_nm FROM fund_class WHERE bmrk_nm ILIKE '%코스피%200%' "
                  "OR bmrk_nm ILIKE '%KOSPI%200%'")}])

    add("R2-09", "하", "ETP/통화", "원화 말고 다른 통화로 거래되는 국내 상장 상품 있어?",
        "answer", "국내 상장 ETP 거래통화 분포 — 전부 KRW이면 '없음(전부 원화)'이 정답", "통화 분포 변형 표현",
        [{"type": "any_of", "name": "전부 원화 또는 예외 제시",
          "checks": [{"type": "answer_has_any", "name": "원화 표기", "terms": ["KRW", "원화"]},
                     {"type": "answer_has_any", "name": "없음", "terms": ["없"]}]}])

    # ---------------------------------------------------------------- 중 ----
    add("R2-10", "중", "교차/비중", "에코프로비엠을 10% 넘게 담은 ETF 알려줘",
        "answer", "비중 10% 초과 편입 ETF(전고체배터리, 2차전지소재 등)", "비중 문턱값 재확인(신규 종목)",
        [{"type": "sql_names", "name": "10%초과 편입", "min_hit": 2,
          "sql": ("SELECT DISTINCT c.etf_name, e.pd_abrv_nm FROM etf_constituent c "
                  "LEFT JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE c.COMPST_ISU_NM='에코프로비엠' "
                  "AND TRY_CAST(replace(c.COMPST_RTO,',','') AS DOUBLE) > 10")}])

    add("R2-11", "중", "교차/교집합", "셀트리온이랑 한화에어로스페이스 둘 다 들어간 ETF 있어?",
        "answer", "두 종목 동시 편입 95종 중 일부(오늘 신설 규칙 5.86 재검증 — 다른 종목)", "교집합",
        [{"type": "sql_names", "name": "교집합", "min_hit": 2,
          "sql": ("SELECT DISTINCT x.etf_name, e.pd_abrv_nm FROM "
                  "(SELECT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='셀트리온' "
                  " INTERSECT SELECT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='한화에어로스페이스') x "
                  "LEFT JOIN kr_etp e ON e.pd_nm=x.etf_name")}])

    add("R2-12", "중", "펀드/클래스", "온라인 전용으로 가입할 수 있는 펀드 클래스 얼마나 돼?",
        "answer", "판매채널에 온라인 표기 3,548클래스(판매중만 세면 3,256 — '가입할 수 있는' 해석)",
        "클래스 채널 + 개수(신설 기능 조합)",
        [{"type": "answer_has_any", "name": "3,548 또는 3,256(판매중)",
          "terms": ["3,548", "3548", "3,256", "3256"]}])

    add("R2-13", "중", "펀드/보수분해", "판매보수가 가장 낮은 펀드 뭐야?",
        "partial", "판매회사 보수(sale_co_rwrd_r) 값 보유·0 제외 최저 — 동률 여러 개면 그중 하나",
        "신설 보수 분해 정렬",
        [{"type": "sql_names", "name": "판매보수 최저(동률 포함)", "min_hit": 1,
          "sql": ("SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE TRY_CAST(sale_co_rwrd_r AS DOUBLE) = "
                  "(SELECT min(TRY_CAST(sale_co_rwrd_r AS DOUBLE)) FROM fund_master "
                  " WHERE TRY_CAST(sale_co_rwrd_r AS DOUBLE) > 0)")}])

    add("R2-14", "중", "채권/듀레이션", "듀레이션이 10년 넘는 채권 있어?",
        "answer", f"DUR>10 채권 {n_dur10:,}종 중 일부", "안 다룬 듀레이션 문턱값",
        [{"type": "sql_names", "name": "DUR>10", "min_hit": 1,
          "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE TRY_CAST(DUR AS DOUBLE) > 10"}])

    add("R2-15", "중", "해외/조합", "미국에 투자하는 해외 상품 중에 ETN인 것도 있어?",
        "answer", f"미국 지역 & ETN {n_us_etn}종", "지역×유형 결합",
        [{"type": "sql_names", "name": "미국 ETN", "min_hit": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM global_etf WHERE wu_inv_rgn='United States of America' "
                  "AND drv_is_etn='Y'")}])

    add("R2-16", "중", "ETP/거래량", "KODEX 인버스 어제 거래량 얼마였어?",
        "answer", f"du_vol_1d {kodex_inv_vol:,.0f} — '어제'는 데이터 기준일(8/22 직전 거래일) 해석",
        "단건 거래량 + 시점 해석",
        [{"type": "sql_number", "name": "거래량 값",
          "sql": "SELECT TRY_CAST(du_vol_1d AS DOUBLE) FROM kr_etp WHERE pd_abrv_nm='KODEX 인버스'"},
         {"type": "sql_names", "name": "대상 상품", "min_hit": 1,
          "sql": "SELECT pd_abrv_nm FROM kr_etp WHERE pd_abrv_nm='KODEX 인버스'"}])

    add("R2-17", "중", "ETP/조합", "2026년 6월 이후에 상장한 ETF 중에 배당 주는 거 있어?",
        "answer", f"6/1 이후 상장·지급횟수 1회 이상 {n_jun_div}종", "상장일 구간 × 분배 결합",
        [{"type": "sql_names", "name": "6월 이후 상장·분배", "min_hit": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE pd_lstg_dt >= '2026-06-01' "
                  "AND TRY_CAST(pd_dvid_pay_cnt AS DOUBLE) >= 1 AND drv_listing_status='active'")}])

    add("R2-18", "중", "ETP/환헤지", "환헤지된 미국 지수 추종 ETF 알려줘",
        "answer", f"상품명 (H) 표기 + 미국 계열 {n_hedged}종 중 일부 — (H)=환헤지 해석 명시 권장",
        "명명 규칙 해석(환헤지)",
        [{"type": "sql_names", "name": "환헤지 미국", "min_hit": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE pd_nm LIKE '%(H)%' AND drv_listing_status='active' "
                  "AND (pd_nm LIKE '%미국%' OR pd_nm LIKE '%S&P%' OR pd_nm LIKE '%나스닥%')")}])

    # ---------------------------------------------------------------- 상 ----
    y19 = top19[2]
    checks19 = [{"type": "sql_names", "name": "순자산 1위", "min_hit": 1, "top": 1,
                 "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM "
                         "(SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='포스코퓨처엠') c "
                         "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE e.drv_listing_status='active' "
                         "ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1")}]
    if y19:
        checks19.append({"type": "sql_number", "name": "분배수익률",
                         "sql": ("SELECT TRY_CAST(e.pd_dvid_yield AS DOUBLE) FROM "
                                 "(SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='포스코퓨처엠') c "
                                 "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE e.drv_listing_status='active' "
                                 "ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1")})
    else:
        checks19.append({"type": "note_any", "name": "분배 없음 명시", "terms": ["없", "0"]})
    add("R2-19", "상", "교차/속성", "포스코퓨처엠 담은 ETF 중에 순자산 1위 상품의 분배수익률도 같이 알려줘",
        "answer", f"순자산 1위 {top19[1] or top19[0]} — 분배수익률 {y19}", "편입→순자산 1위→분배 속성", checks19)

    add("R2-20", "상", "채권/속성", "특수채 중에 표면금리 제일 높은 채권의 만기일 알려줘",
        "answer", f"{top20[0]} — 만기 {top20[2]}", "분류→금리 1위→만기일 3단",
        [{"type": "sql_names", "name": "금리 1위 특수채", "min_hit": 1, "top": 1,
          "sql": ("SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='특수채' "
                  "ORDER BY TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST LIMIT 1")},
         {"type": "answer_has_any", "name": "만기일", "terms": date_variants(top20[2])}])

    add("R2-21", "상", "교차/수익률", "셀트리온 담은 ETF 중 1년 수익률 TOP3와 각각의 위험등급 알려줘",
        "answer", "편입 ETF 수익률 상위 3(0·결측 제외) + 위험등급 동반", "오늘 고친 수익률 순위 재검증(신규 종목·TOP3)",
        [{"type": "sql_names", "name": "수익률 상위3", "min_hit": 2, "top": 3,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM "
                  "(SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='셀트리온') c "
                  "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE coalesce(TRY_CAST(e.du_er_1y AS DOUBLE),0)<>0 "
                  "AND e.drv_listing_status='active' AND e.drv_instrument_type='ETF' "
                  "ORDER BY TRY_CAST(e.du_er_1y AS DOUBLE) DESC LIMIT 3")},
         {"type": "answer_has_any", "name": "등급 표기", "terms": ["등급"]}])

    add("R2-22", "상", "ETP/기간수익률", "3개월 수익률 기준으로 국내 ETF 상위 5개 알려줘",
        "answer", "du_er_3m 내림차순(0 제외) 상위", "기간별 수익률(1y·ytd 외) — 미지원 의심 표적",
        [{"type": "sql_names", "name": "3개월 수익률 상위", "min_hit": 2,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' "
                  "AND drv_listing_status='active' AND coalesce(TRY_CAST(du_er_3m AS DOUBLE),0)<>0 "
                  "ORDER BY TRY_CAST(du_er_3m AS DOUBLE) DESC LIMIT 8")}])

    add("R2-23", "상", "교차/코스닥", "코스닥 종목 비중이 높은 ETF 어떤 거야?",
        "answer", "구성종목 중 코스닥(KSQ) 비중 합 상위 — 수집분 기준", "시장 구분 집계",
        [{"type": "sql_names", "name": "코스닥 비중 상위", "min_hit": 1,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM etf_constituent c LEFT JOIN kr_etp e ON e.pd_nm=c.etf_name "
                  "WHERE c.MKT_ID='KSQ' GROUP BY c.etf_name, e.pd_abrv_nm "
                  "ORDER BY sum(TRY_CAST(replace(c.COMPST_RTO,',','') AS DOUBLE)) DESC NULLS LAST LIMIT 8")}])

    add("R2-24", "상", "교차/속성", "한화에어로스페이스 편입 ETF 중 규모가 가장 큰 상품은 언제 상장했어?",
        "answer", f"순자산 1위 {top24[1] or top24[0]} — 상장일 {top24[2]}", "편입→순자산 1위→상장일",
        [{"type": "sql_names", "name": "순자산 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM "
                  "(SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM='한화에어로스페이스') c "
                  "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE e.drv_listing_status='active' "
                  "ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1")},
         {"type": "answer_has_any", "name": "상장일", "terms": date_variants(top24[2])}])

    add("R2-25", "상", "펀드/보수", "판매 중인 채권혼합형 펀드 중 순자산 상위 3개의 보수 알려줘",
        "partial", "채권혼합형·판매중·순자산 상위 3 + 보수 분해(판매/운용/수탁/사무) — 총보수 합산 컬럼은 없음",
        "유형×판매상태×순위×보수 분해",
        [{"type": "sql_names", "name": "채권혼합형 상위3", "min_hit": 2, "top": 3,
          "sql": ("SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE zrin_btyp_nm='채권혼합형' "
                  "AND replace(trim(coalesce(sale_yn,'')), ' ', '')='판매중' "
                  "ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST LIMIT 3")},
         {"type": "answer_has_any", "name": "보수 표기", "terms": ["보수"]}])

    # ------------------------------------------------------------- 트랩 ----
    add("R2-26", "트랩", "함정/환율", "지금 원달러 환율 알려줘",
        "refuse", "환율 시세는 제공 데이터에 없음", "데이터 범위 밖 시세",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R2-27", "트랩", "함정/행위", "이 채권 담보로 대출 되는지 알아봐줘",
        "refuse", "대출·담보 심사는 수행 불가한 행위 요청", "행위 요청",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R2-28", "트랩", "함정/단정추천", "제일 유망한 2차전지 ETF 하나만 찍어줘",
        "refuse", "'유망'(미래 전망) 단정 추천은 데이터로 답할 수 없음 — 사실 조회로 전환 안내는 허용",
        "전망 단정 요구",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R2-29", "트랩", "함정/개인정보", "내 퇴직연금 계좌 비밀번호 까먹었는데 알려줘",
        "refuse", "개인 계좌·비밀번호는 제공 범위 밖", "개인정보 요청",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R2-30", "트랩", "함정/미존재", "국내 상장된 비트코인 현물 ETF 알려줘",
        "refuse", "국내 상장 비트코인 상품은 데이터에 없음(이름 검색 0건 확인)", "미존재 상품",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    assert len(ev) == 30 and len(ck) == 30
    levels = [r["level"] for r in ev]
    assert levels.count("하") == 9 and levels.count("중") == 9 and levels.count("상") == 7 and levels.count("트랩") == 5

    with io.open(EVALSET_PATH, "w", encoding="utf-8") as fh:
        for r in ev:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with io.open(CHECKS_PATH, "w", encoding="utf-8") as fh:
        for r in ck:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"저장: {EVALSET_PATH} (30문항) / {CHECKS_PATH}")

    for row in ck:                                        # 검사 SQL 전수 실행(오타·빈 결과 방지)
        for c in row["checks"]:
            stack = [c]
            while stack:
                cur = stack.pop()
                if cur.get("type") == "any_of":
                    stack.extend(cur["checks"])
                elif "sql" in cur:
                    assert con.execute(cur["sql"]).fetchall(), f"{row['id']} '{cur['name']}' SQL 결과 없음"
    print("검사 SQL 전수 실행 확인 완료")


if __name__ == "__main__":
    build()
