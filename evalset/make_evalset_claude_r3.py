# -*- coding: utf-8 -*-
"""블라인드 3바퀴(claude r3, 8/28 밤) — 사용자 실측 5문항 + 신규 20문항.

U-01~05 는 사용자가 localhost 검증에서 직접 던진 질문(잔존만기×금리 라우팅 오답을
찾아낸 그 세트)을 그대로 고정한 것. 나머지는 아직 안 다룬 축 — 발행연도·거래대금·
NAV·통화 존재·등급별 집계·클래스 사전·벤치마크 결측·만기형 연도·그룹 ETF 구성 제외·
편입 수 비교·복합 카운트·지수×보수 — 을 표적.

실행: python evalset/make_evalset_claude_r3.py
"""
import io
import json
import os

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "storage", "output", "products.duckdb")
EVALSET_PATH = os.path.join(HERE, "evalset_claude_r3.jsonl")
CHECKS_PATH = os.path.join(HERE, "checks_claude_r3.jsonl")

WINDOW3 = ("2026-08-28", "2029-08-28")   # 잔존만기 3년(요청 시점 기준 — 채점 재현용 고정)
WINDOW5 = ("2026-08-28", "2031-08-28")


def one(con, sql):
    row = con.execute(sql).fetchone()
    return row[0] if row else None


def build():
    con = duckdb.connect(DB, read_only=True)

    n_2026_corp = one(con, "SELECT count(*) FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' AND ISU_DT >= '2026-01-01'")
    assert n_2026_corp == 2849, n_2026_corp
    assert one(con, "SELECT count(*) FROM global_etf WHERE pd_trd_ccy='EUR'") == 0
    n_nobmrk = one(con, "SELECT count(*) FROM fund_master WHERE bmrk_nm IS NULL OR trim(bmrk_nm)=''")
    assert n_nobmrk == 12372, n_nobmrk
    n_1jo_g2 = one(con, ("SELECT count(*) FROM kr_etp WHERE drv_instrument_type='ETF' AND drv_listing_status='active' "
                         "AND TRY_CAST(pd_net_tamt AS DOUBLE)>1e12 AND drv_risk_grade=2"))
    assert n_1jo_g2 == 41, n_1jo_g2
    cmp_cnt = dict(con.execute("SELECT COMPST_ISU_CD, count(DISTINCT etf_isin) FROM etf_constituent "
                               "WHERE COMPST_ISU_CD IN ('005930','000660') GROUP BY 1").fetchall())
    assert cmp_cnt["005930"] > cmp_cnt["000660"], cmp_cnt
    # KODEX 삼성그룹(순자산 최대 클래스)의 삼성전자 다음 비중 종목
    row10 = con.execute(
        "SELECT c.COMPST_ISU_NM FROM etf_constituent c JOIN kr_etp e ON c.etf_isin=e.pd_itm_no "
        "WHERE e.pd_abrv_nm LIKE 'KODEX 삼성그룹%' AND e.drv_listing_status='active' "
        "AND c.COMPST_ISU_NM <> '삼성전자' "
        "ORDER BY TRY_CAST(replace(c.COMPST_RTO,',','') AS DOUBLE) DESC LIMIT 2").fetchall()
    second_name = row10[0][0]

    ev, ck = [], []

    def add(id_, level, category, question, behavior, gold, basis, checks):
        ev.append({"id": id_, "level": level, "category": category, "question": question,
                   "channels": [], "behavior": behavior, "gold": gold, "basis": basis})
        ck.append({"id": id_, "checks": checks})

    # ------------------------------------------------ 사용자 실측 5문항 ----
    add("U-01", "상", "채권/구간정렬", "잔존만기 3년 이내 중 표면 금리 가장 높은 회사채 알려줘",
        "answer", "만기 3년 구간 안 회사채 금리 1위 = 중진공 스케일업 사모채(표면 20%) — 8/28 실측 오답(30년물 제시) 수정 검증",
        "사용자 localhost 실측 — 구간 낱말('이내')과 순위 낱말('가장')의 결합",
        [{"type": "sql_names", "name": "구간 내 금리 1위", "min_hit": 1, "top": 2,
          "sql": (f"SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' "
                  f"AND MAT_DT > '{WINDOW3[0]}' AND MAT_DT <= '{WINDOW3[1]}' "
                  "ORDER BY TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST LIMIT 2")},
         {"type": "answer_has_any", "name": "금리 20%", "terms": ["20"]},
         {"type": "answer_has_none", "name": "30년물 미출현", "terms": ["신종자본증권"]}])

    add("U-02", "중", "채권/구간필터", "잔존만기 3년 이내이면서 표면금리 4% 이상인 회사채 알려줘",
        "answer", "구간+금리 하한 복합", "사용자 실측(정답이었음 — 고정)",
        [{"type": "sql_names", "name": "구간+4%이상", "min_hit": 2,
          "sql": (f"SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' "
                  f"AND MAT_DT > '{WINDOW3[0]}' AND MAT_DT <= '{WINDOW3[1]}' "
                  "AND TRY_CAST(SRFC_IRT AS DOUBLE) >= 4")}])

    add("U-03", "중", "채권/구간필터", "잔존만기 5년 이내이면서 표면금리 5% 이상인 회사채 알려줘",
        "answer", "구간+금리 하한 복합(5년/5%)", "사용자 실측(정답이었음 — 고정)",
        [{"type": "sql_names", "name": "구간+5%이상", "min_hit": 2,
          "sql": (f"SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' "
                  f"AND MAT_DT > '{WINDOW5[0]}' AND MAT_DT <= '{WINDOW5[1]}' "
                  "AND TRY_CAST(SRFC_IRT AS DOUBLE) >= 5")}])

    add("U-04", "상", "교차/비중순위", "삼성전자 비중이 가장 높은 ETF 3개 알려줘",
        "answer", "KTOP30 계열(38.7%대)이 상위 — 비중 내림차순 상위 3", "사용자 실측(정답이었음 — 고정)",
        [{"type": "sql_names", "name": "비중 상위 3", "min_hit": 2, "top": 3,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM etf_constituent c JOIN kr_etp e ON c.etf_isin=e.pd_itm_no "
                  "WHERE c.COMPST_ISU_CD='005930' AND e.drv_listing_status='active' "
                  "ORDER BY TRY_CAST(replace(c.COMPST_RTO,',','') AS DOUBLE) DESC LIMIT 3")}])

    add("U-05", "하", "교차/목록", "삼성전자를 담은 ETF 중 세 가지 알려줘",
        "answer", "비중 상위 표시(결정적) — 지어낸 '1위' 주장 금지", "사용자 실측 — 생성기 재배열 결함 검증",
        [{"type": "sql_names", "name": "비중 상위 8 중 2", "min_hit": 2,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM etf_constituent c JOIN kr_etp e ON c.etf_isin=e.pd_itm_no "
                  "WHERE c.COMPST_ISU_CD='005930' AND e.drv_listing_status='active' "
                  "ORDER BY TRY_CAST(replace(c.COMPST_RTO,',','') AS DOUBLE) DESC LIMIT 8")}])

    # ---------------------------------------------------------------- 하 ----
    add("R3-01", "하", "채권/발행일", "2026년에 발행된 회사채가 몇 개야?",
        "answer", "발행일 2026-01-01 이후 회사채 2,849종", "발행일 축 + 개수",
        [{"type": "answer_has_any", "name": "2,849", "terms": ["2,849", "2849"]}])

    add("R3-02", "하", "해외/통화존재", "유로화로 거래되는 해외 ETF도 있어?",
        "answer", "0건 — '없음' 사실 답변이 정답(전 종목 USD)", "존재 질문의 '없음' 답",
        [{"type": "answer_has_any", "name": "없음/USD", "terms": ["없", "USD", "0건"]}])

    add("R3-03", "하", "ETP/NAV", "기준가(NAV)가 가장 높은 국내 ETF 뭐야?",
        "answer", "KODEX CD금리액티브(합성) — NAV 약 107만원", "NAV 정렬(미지원 의심 표적)",
        [{"type": "sql_names", "name": "NAV 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_listing_status='active' "
                  "ORDER BY TRY_CAST(du_last_nav AS DOUBLE) DESC NULLS LAST LIMIT 1")}])

    add("R3-04", "하", "ETP/거래대금", "거래대금이 제일 큰 국내 ETF 뭐야?",
        "answer", "KODEX 200(1일 거래대금 약 3.6조) — 거래량(주수)과 다른 금액 축", "거래대금 정렬(미지원 의심)",
        [{"type": "sql_names", "name": "거래대금 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_listing_status='active' AND drv_instrument_type='ETF' "
                  "ORDER BY TRY_CAST(du_val_1d AS DOUBLE) DESC NULLS LAST LIMIT 1")}])

    add("R3-05", "하", "전체/등급집계", "위험등급별로 상품이 각각 몇 개씩 있는지 알려줘",
        "partial", "채권·ETF·ETN·펀드 등급별 집계(해외 ETF는 등급 없음 명시)", "전 상품군 집계",
        [{"type": "answer_has_any", "name": "등급 표기", "terms": ["1등급", "등급별", "1~6"]},
         {"type": "note_any", "name": "해외 제외 명시", "terms": ["해외"]}])

    add("R3-06", "하", "ETP/원자재", "원자재에 투자하는 ETN 뭐 있어?",
        "answer", "구리·원유 등 원자재 ETN 제시", "테마×유형(ETN) 결합",
        [{"type": "sql_names", "name": "원자재 ETN", "min_hit": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETN' AND drv_listing_status='active' "
                  "AND (pd_nm LIKE '%원자재%' OR pd_nm LIKE '%구리%' OR pd_nm LIKE '%원유%' OR pd_nm LIKE '%금%선물%' "
                  "OR pd_nm LIKE '%은%선물%' OR pd_nm LIKE '%니켈%' OR pd_nm LIKE '%천연가스%')")}])

    # ---------------------------------------------------------------- 중 ----
    add("R3-07", "중", "펀드/클래스사전", "펀드 A클래스랑 C클래스 차이가 뭐야?",
        "answer", "A=선취 판매수수료·낮은 보수 / C=수수료 없이 높은 보수 — 사전 답변", "클래스 사전 표현 변형",
        [{"type": "answer_has_any", "name": "선취", "terms": ["선취"]},
         {"type": "answer_has_all", "name": "A·C 언급", "terms": ["A", "C"]}])

    add("R3-08", "중", "펀드/벤치마크결측", "벤치마크가 아예 없는 펀드도 있어?",
        "answer", "벤치마크 표기 없는 클래스 12,372건 — 있음+규모 답", "결측 존재 질문",
        [{"type": "answer_has_any", "name": "있음+수", "terms": ["12,372", "12372", "있"]}])

    add("R3-09", "중", "ETP/만기형", "존속기한이 있는 채권 ETF 중에 2027년 만기인 상품 알려줘",
        "answer", "상품명 '27-MM' 표기 만기형(KODEX 27-12 회사채 등)", "만기형 연도 지정",
        [{"type": "sql_names", "name": "27-표기 만기형", "min_hit": 1,
          "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE pd_nm LIKE '%27-%' AND drv_listing_status='active'"}])

    add("R3-10", "중", "교차/구성제외", "KODEX 삼성그룹 ETF에서 삼성전자 다음으로 비중 큰 종목이 뭐야?",
        "answer", f"삼성전자 제외 최대 비중 = {second_name}", "구성 상위 + 1위 제외 해석",
        [{"type": "answer_has_any", "name": "2위 종목", "terms": [second_name]}])

    add("R3-11", "중", "교차/편입수비교", "삼성전자랑 SK하이닉스 중에 ETF들이 더 많이 담고 있는 종목은 뭐야?",
        "answer", f"삼성전자({cmp_cnt['005930']}종) > SK하이닉스({cmp_cnt['000660']}종)", "편입 ETF 수 비교(미지원 의심)",
        [{"type": "answer_has_any", "name": "삼성전자 우세", "terms": ["삼성전자"]},
         {"type": "any_of", "name": "편입 수 근거",
          "checks": [{"type": "answer_has_any", "name": "239", "terms": [str(cmp_cnt['005930'])]},
                     {"type": "answer_has_any", "name": "더 많", "terms": ["더 많", "더많"]}]}])

    add("R3-12", "중", "ETP/복합개수", "순자산 1조 넘는 ETF 중에 위험등급 2등급인 건 몇 개야?",
        "answer", "41종 — 금액 문턱값+등급+개수 3중 결합", "복합 카운트(등급 조건 미지원 의심)",
        [{"type": "answer_has_any", "name": "41", "terms": ["41"]}])

    # ---------------------------------------------------------------- 상 ----
    add("R3-13", "상", "채권/등급대금리", "회사채 중에 신용등급이 A급인데 표면금리 6% 넘는 채권 있어?",
        "answer", "A급(A+·A·A-, 서열 5~7)·6%초과 64종 중 일부", "등급대 해석+문턱값",
        [{"type": "sql_names", "name": "A급 6%초과", "min_hit": 1,
          "sql": ("SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' "
                  "AND TRY_CAST(drv_crd_grd_rank AS INT) BETWEEN 5 AND 7 AND TRY_CAST(SRFC_IRT AS DOUBLE)>6")}])

    add("R3-14", "상", "ETP/지수보수", "코스피200 추종 ETF 중에 총보수가 제일 싼 거 뭐야?",
        "partial", "지수 필터+보수 오름차순(값 보유분) — 결측 다수 한계 명시", "지수×보수 결합(미지원 의심)",
        [{"type": "sql_names", "name": "보수 최저 후보", "min_hit": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE (cu_base_index LIKE '%코스피%200%' "
                  "OR cu_base_index ILIKE '%KOSPI%200%' OR ref_base_index ILIKE '%KOSPI%200%' OR pd_nm LIKE '%200%') "
                  "AND TRY_CAST(cu_charge_rt AS DOUBLE)>0 AND drv_listing_status='active' "
                  "ORDER BY TRY_CAST(cu_charge_rt AS DOUBLE) ASC LIMIT 5")}])

    add("R3-15", "상", "채권/3중결합", "잔존만기 3년 이내 AA등급 이상 회사채 중에 표면금리 높은 순으로 3개 알려줘",
        "answer", "구간+등급 상한+금리 정렬 3중 결합(오늘 수정 경로의 확장 검증)", "복합 정렬",
        [{"type": "sql_names", "name": "3중 결합 상위", "min_hit": 1, "top": 3,
          "sql": (f"SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='회사채' "
                  f"AND MAT_DT > '{WINDOW3[0]}' AND MAT_DT <= '{WINDOW3[1]}' "
                  "AND TRY_CAST(drv_crd_grd_rank AS INT) <= 4 "
                  "ORDER BY TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST LIMIT 3")}])

    add("R3-16", "상", "교차/속성동반", "삼성전자 담은 ETF 중 순자산 상위 3개의 추적오차도 같이 알려줘",
        "answer", "순자산 상위 3 + 각 상품 추적오차 동반 표기", "순위+속성 동반(focus 열 검증)",
        [{"type": "sql_names", "name": "순자산 상위 3", "min_hit": 2, "top": 3,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM etf_constituent c JOIN kr_etp e ON c.etf_isin=e.pd_itm_no "
                  "WHERE c.COMPST_ISU_CD='005930' AND e.drv_listing_status='active' "
                  "GROUP BY c.etf_name, e.pd_abrv_nm, e.pd_net_tamt "
                  "ORDER BY TRY_CAST(e.pd_net_tamt AS DOUBLE) DESC NULLS LAST LIMIT 3")},
         {"type": "answer_has_any", "name": "추적오차 표기", "terms": ["추적오차"]}])

    # ------------------------------------------------------------- 트랩 ----
    add("R3-17", "트랩", "함정/외부시세", "지금 미국 기준금리가 몇 퍼센트야?",
        "refuse", "기준금리(정책금리)는 제공 데이터 밖", "외부 거시 지표",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R3-18", "트랩", "함정/운용위임", "KODEX 200이랑 똑같이 포트폴리오 만들어서 운용해줘",
        "refuse", "운용 위임·실행은 수행 불가", "행위 요청",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R3-19", "트랩", "함정/과거시세", "작년 12월 31일 KODEX 200 종가 알려줘",
        "refuse", "과거 특정일 시세 이력은 미보유(스냅샷 1개)", "시점 이력",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("R3-20", "트랩", "함정/맞춤자문", "나 30살인데 뭐에 투자하는 게 좋아?",
        "refuse", "개인 맞춤 투자 자문은 제공 범위 밖(사실 조회로 전환 안내는 허용)", "맞춤 자문",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    assert len(ev) == 25 and len(ck) == 25
    levels = [r["level"] for r in ev]
    assert levels.count("트랩") == 4

    with io.open(EVALSET_PATH, "w", encoding="utf-8") as fh:
        for r in ev:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with io.open(CHECKS_PATH, "w", encoding="utf-8") as fh:
        for r in ck:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"저장: {EVALSET_PATH} (25문항) / {CHECKS_PATH}")

    for row in ck:
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
