# -*- coding: utf-8 -*-
"""blind_claude.txt(8/28 리더 세션 블라인드 출제 20문항)를 채점표로 변환한다.

원칙(TEAM_IMPROVEMENT_GUIDE §4): 기대값은 시스템 출력이 아니라 DuckDB 원본에
직접 SQL 을 실행해 확인한 값으로 넣는다. 해석이 갈릴 수 있는 문항은 any_of 로
복수 정답을 허용하고, 부분집합-안전(시스템이 더 좁게 해석해도 통과)하게 만든다.

실행: python evalset/make_evalset_claude.py
산출: evalset/evalset_claude.jsonl · evalset/checks_claude.jsonl
"""
import io
import json
import os

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "storage", "output", "products.duckdb")

EVALSET_PATH = os.path.join(HERE, "evalset_claude.jsonl")
CHECKS_PATH = os.path.join(HERE, "checks_claude.jsonl")


def rows_of(con, sql):
    return con.execute(sql).fetchall()


def build():
    con = duckdb.connect(DB, read_only=True)

    # --- 출제 당시(8/28) 데이터 사실 확인 — 어긋나면 데이터 판이 다른 것이므로 멈춘다
    n_pen_aaa = rows_of(con, "SELECT count(*) FROM kr_bond WHERE PD_PEN_TR_YN='Y' AND drv_crd_grd_norm='AAA'")[0][0]
    assert n_pen_aaa == 45, f"퇴직연금+AAA 채권 수가 45가 아님: {n_pen_aaa}"
    n_risk1 = rows_of(con, "SELECT count(*) FROM kr_etp WHERE drv_risk_grade=1 AND drv_listing_status='active'")[0][0]
    assert n_risk1 == 595, f"위험 1등급 상장 ETP 수가 595가 아님: {n_risk1}"
    n_sepw = rows_of(con, "SELECT count(DISTINCT etf_name) FROM etf_constituent WHERE COMPST_ISU_NM='삼성전자우'")[0][0]
    assert n_sepw == 20, f"삼성전자우 편입 ETF 수가 20이 아님: {n_sepw}"   # 8/21 재수집분(구 7/10은 15)

    ev, ck = [], []

    def add(id_, level, category, question, behavior, gold, basis, checks):
        ev.append({"id": id_, "level": level, "category": category, "question": question,
                   "channels": [], "behavior": behavior, "gold": gold, "basis": basis})
        ck.append({"id": id_, "checks": checks})

    # ---------------------------------------------------------------- 하 ----
    add("B-01", "하", "채권/개수", "퇴직연금에 넣을 수 있는 채권 중에 신용등급 AAA인 거 몇 개나 돼?",
        "answer", "퇴직연금 편입 가능(Y)·AAA = 45종(매수가능만 세면 44종)",
        "개수를 묻는 질문 — 목록이 아니라 수를 세어 답해야 한다",
        [{"type": "any_of", "name": "개수 45(또는 매수가능 44)",
          "checks": [{"type": "answer_has_any", "name": "45", "terms": ["45"]},
                     {"type": "answer_has_any", "name": "44", "terms": ["44"]}]},
         {"type": "evidence_source_any", "name": "근거 출처", "sources": ["PRBD01N001"]}])

    add("B-02", "하", "ETP/분배", "SOL 미국배당다우존스 분배금 일년에 몇번 줘?",
        "answer", "연 지급횟수 12회(1~12월 매월)", "신설 분배 항목의 단건 조회",
        [{"type": "answer_has_any", "name": "12회", "terms": ["12"]},
         {"type": "sql_names", "name": "SOL 미국배당다우존스", "min_hit": 1,
          "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE pd_nm LIKE '%SOL%미국배당다우존스%'"}])

    add("B-03", "하", "ETP/위험등급", "위험등급 1등급(매우 위험)인 국내 ETF 아무거나 5개만 보여주세요",
        "answer", "위험등급 1(매우높은위험) 상장 ETP 595종 중 아무 5종", "위험등급 필터 목록",
        [{"type": "sql_names", "name": "1등급 상장 ETP", "min_hit": 3,
          "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_risk_grade=1 AND drv_listing_status='active'"},
         {"type": "answer_has_any", "name": "등급 표기", "terms": ["1등급", "매우높"]}])

    add("B-04", "하", "ETP/괴리율", "괴리율이 제일 큰 국내 상장 ETN이 뭔지 알려주세요",
        "answer", "미래에셋 레버리지 WTI원유 선물 ETN 110호(+5.21%) — 부호/절댓값 어느 해석이든 동일",
        "신설 괴리율 항목 + ETN 한정 정렬",
        [{"type": "sql_names", "name": "괴리율 1위 ETN", "min_hit": 1, "top": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETN' AND drv_listing_status='active' "
                  "AND TRY_CAST(du_diff_rt AS DOUBLE) IS NOT NULL ORDER BY TRY_CAST(du_diff_rt AS DOUBLE) DESC LIMIT 1")},
         {"type": "sql_number", "name": "괴리율 값",
          "sql": ("SELECT max(TRY_CAST(du_diff_rt AS DOUBLE)) FROM kr_etp WHERE drv_instrument_type='ETN' "
                  "AND drv_listing_status='active'")}])

    add("B-05", "하", "교차/구성종목", "삼성전자우 들어있는 ETF 있어?",
        "answer", "삼성전자우(우선주) 편입 ETF 20종(8/21 수집분) — 삼성전자(보통주)와 구분되어야 함",
        "우선주 이름이 정확히 구분되는지",
        [{"type": "sql_names", "name": "삼성전자우 편입 ETF", "min_hit": 2,
          "sql": ("SELECT DISTINCT c.etf_name, e.pd_abrv_nm FROM etf_constituent c "
                  "LEFT JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE c.COMPST_ISU_NM='삼성전자우'")},
         {"type": "evidence_min", "name": "근거 표시", "n": 1}])

    add("B-06", "하", "채권/만기", "만기가 2030년 이후인 국공채좀찾아줘",
        "answer", "국공채·만기 2030-01-01 이후 720종(더 좁은 해석이어도 이 집합의 부분집합)",
        "만기 조건 + 분류 필터, 띄어쓰기 없는 말투",
        [{"type": "sql_names", "name": "국공채 만기 2030+", "min_hit": 2,
          "sql": "SELECT PD_ABRV_NM, PD_NM FROM kr_bond WHERE STD_PD_MCLS_NM='국공채' AND MAT_DT >= '2030-01-01'"},
         {"type": "evidence_source_any", "name": "근거 출처", "sources": ["PRBD01N001"]}])

    # ---------------------------------------------------------------- 중 ----
    add("B-07", "중", "ETP/운용사", "미래에셋자산운용이 굴리는 ETF 중에서 순자산 제일 큰 게 뭐야?",
        "answer", "미래에셋 TIGER 미국S&P500(순자산 약 20.2조)", "운용사 필터 + 순자산 1위, '굴리는' 구어체",
        [{"type": "sql_names", "name": "미래에셋 순자산 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE (cu_fund_mgmt_co LIKE '%미래에셋%' OR ref_fund_mgmt_co LIKE '%미래에셋%') "
                  "AND drv_listing_status='active' ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1")}])

    add("B-08", "중", "ETP/분배", "월배당 ETF 중에 분배수익률 3% 넘는 거 알려줘요",
        "answer", "지급 12회·분배수익률>3% 인 상장 ETP 112종", "신설 항목 2개 결합 + 문턱값",
        [{"type": "sql_names", "name": "월배당·3%초과", "min_hit": 2,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE TRY_CAST(pd_dvid_pay_cnt AS DOUBLE)>=12 "
                  "AND TRY_CAST(pd_dvid_yield AS DOUBLE)>3 AND drv_listing_status='active'")}])

    add("B-09", "중", "펀드/자산구성", "채권형 펀드인데 해외 채권 비중이 50% 넘는 상품 있나요?",
        "answer", "채권 계열 유형 & 해외채권 비중>50% — 자산구성 값이 있는 상품 기준 8클래스(다올중국플러스찬스 등). 좁게 해석해 0건이면 '없음' 사실 답변도 정답",
        "신설 자산구성 비율 문턱값 — 해석 폭 허용",
        [{"type": "any_of", "name": "목록 또는 '없음' 답",
          "checks": [{"type": "sql_names", "name": "해외채권 50%초과", "min_hit": 1,
                      "sql": ("SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE zrin_btyp_nm LIKE '%채권%' "
                              "AND TRY_CAST(zrin_ovrs_bd_cmst_rt AS DOUBLE)>50")},
                     {"type": "answer_has_any", "name": "없음 사실 답변", "terms": ["없", "0건", "확인되지 않"]}]}])

    add("B-10", "중", "ETP/추적오차", "ACE 미국나스닥100 추적오차 어느정도임?",
        "answer", "한국투자 ACE 미국나스닥100 추적오차율 0.07%", "신설 추적오차 단건 + 인터넷 말투",
        [{"type": "sql_number", "name": "추적오차 0.07",
          "sql": "SELECT TRY_CAST(du_chas_errt AS DOUBLE) FROM kr_etp WHERE pd_nm='한국투자 ACE 미국나스닥100증권상장지수투자신탁(주식)'"},
         {"type": "sql_names", "name": "대상 상품", "min_hit": 1,
          "sql": "SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE pd_nm='한국투자 ACE 미국나스닥100증권상장지수투자신탁(주식)'"}])

    add("B-11", "중", "펀드/클래스", "판매수수료 없는 클래스로 가입할 수 있는 인덱스펀드 알려줘",
        "answer", "수수료미징구 클래스 & 인덱스 계열 425클래스(교보악사파워인덱스 C-Pe 등)",
        "신설 클래스 수수료 유형 + 유형 결합",
        [{"type": "sql_names", "name": "수수료미징구 인덱스", "min_hit": 1,
          "sql": ("SELECT itm_abrv_nm, itm_nm FROM fund_class WHERE han_clas_fee_type='수수료미징구' "
                  "AND (zrin_ptn_nm LIKE '%인덱스%' OR itm_nm LIKE '%인덱스%')")}])

    add("B-12", "중", "ETP/변동성", "1년 변동성 낮은 순서로 국내 ETF 다섯 개 뽑아줘",
        "answer", "1년 변동성 오름차순(0 제외) — ETN 포함 전체로 보면 통안채·CD금리 ETN, ETF만 보면 CD금리·KOFR ETF",
        "신설 변동성 + 오름차순(낮은 순) 정렬 — ETF/ETN 해석 폭 허용",
        [{"type": "any_of", "name": "낮은 변동성 목록(전체 또는 ETF만)",
          "checks": [{"type": "sql_names", "name": "전체 ETP 하위", "min_hit": 2,
                      "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE TRY_CAST(du_vlty_1y AS DOUBLE) IS NOT NULL "
                              "AND TRY_CAST(du_vlty_1y AS DOUBLE)<>0 AND drv_listing_status='active' "
                              "ORDER BY TRY_CAST(du_vlty_1y AS DOUBLE) ASC LIMIT 8")},
                     {"type": "sql_names", "name": "ETF만 하위", "min_hit": 2,
                      "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE drv_instrument_type='ETF' "
                              "AND TRY_CAST(du_vlty_1y AS DOUBLE) IS NOT NULL AND TRY_CAST(du_vlty_1y AS DOUBLE)<>0 "
                              "AND drv_listing_status='active' ORDER BY TRY_CAST(du_vlty_1y AS DOUBLE) ASC LIMIT 8")}]}])

    # ---------------------------------------------------------------- 상 ----
    add("B-13", "상", "교차/보수", "현대차 편입한 ETF 중에서 총보수 제일 싼 상품 운용사가 어디에요?",
        "partial", "편입 163종 중 총보수 값 보유 11종 — 최저는 브이아이 FOCUS ESG Leaders150(0.10%) → 브이아이. 결측 한계를 밝혀야",
        "구성종목→보수 정렬→운용사 3단계, 결측 많은 항목",
        [{"type": "sql_names", "name": "총보수 최저(값 보유분)", "min_hit": 1, "top": 1,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM (SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM IN ('현대차','현대자동차')) c "
                  "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE TRY_CAST(e.cu_charge_rt AS DOUBLE) IS NOT NULL "
                  "AND TRY_CAST(e.cu_charge_rt AS DOUBLE)<>0 ORDER BY TRY_CAST(e.cu_charge_rt AS DOUBLE) ASC LIMIT 1")},
         {"type": "answer_has_any", "name": "운용사(브이아이)", "terms": ["브이아이"]}])

    add("B-14", "상", "교차/수익률", "LG에너지솔루션 담고있는 ETF들중 1년수익률 1등이 뭐고 그 상품 위험등급도 같이 알려줘",
        "answer", "미래에셋 TIGER 레버리지(372.41%) — 위험등급 1(매우높은위험)", "구성종목→수익률 1위→위험등급 2속성",
        [{"type": "sql_names", "name": "수익률 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM (SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM LIKE 'LG에너지솔루션%') c "
                  "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE coalesce(TRY_CAST(e.du_er_1y AS DOUBLE),0)<>0 "
                  "AND e.drv_listing_status='active' ORDER BY TRY_CAST(e.du_er_1y AS DOUBLE) DESC LIMIT 1")},
         {"type": "answer_has_any", "name": "위험등급 1", "terms": ["1등급", "매우높"]}])

    add("B-15", "상", "펀드/분배율", "국내 주식형 펀드 중 순자산 상위 3개 상품의 최근 분배율 비교해 줘",
        "answer", "주식형 순자산 상위 3(삼성KODEX200 0.205 / KODEX 200 TR 0.0 / KB KStar 200 0.134)",
        "유형+순위+신설 분배율 비교",
        [{"type": "sql_names", "name": "주식형 순자산 상위3", "min_hit": 2, "top": 3,
          "sql": ("SELECT itm_abrv_nm, itm_nm FROM fund_master WHERE zrin_btyp_nm='주식형' "
                  "ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST LIMIT 3")},
         {"type": "sql_number", "name": "1위 분배율 0.205",
          "sql": ("SELECT TRY_CAST(fd_last_dstb_r AS DOUBLE) FROM fund_master WHERE zrin_btyp_nm='주식형' "
                  "ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST LIMIT 1")}])

    add("B-16", "상", "ETP/추적오차", "추적오차 제일 작은 미국 S&P500 추종 국내 ETF는 뭐고 총보수는 얼마임?",
        "partial", "S&P500 계열 지수 중 추적오차 최소 = 삼성 KODEX 미국S&P500(0.07%). 총보수는 자료에 값이 없음 → 한계 명시",
        "지수 필터 + 오름차순 + 결측 항목 조합",
        [{"type": "sql_names", "name": "추적오차 최소 S&P500", "min_hit": 1, "top": 1,
          "sql": ("SELECT pd_abrv_nm, pd_nm FROM kr_etp WHERE (cu_base_index LIKE '%S&P%500%' OR ref_base_index LIKE '%S&P%500%') "
                  "AND TRY_CAST(du_chas_errt AS DOUBLE) IS NOT NULL AND TRY_CAST(du_chas_errt AS DOUBLE)<>0 "
                  "AND drv_listing_status='active' ORDER BY TRY_CAST(du_chas_errt AS DOUBLE) ASC LIMIT 1")},
         {"type": "sql_number", "name": "추적오차 0.07",
          "sql": ("SELECT min(TRY_CAST(du_chas_errt AS DOUBLE)) FROM kr_etp WHERE (cu_base_index LIKE '%S&P%500%' OR ref_base_index LIKE '%S&P%500%') "
                  "AND TRY_CAST(du_chas_errt AS DOUBLE)<>0 AND drv_listing_status='active'")}])

    add("B-17", "상", "교차/계열", "카카오 계열 담은 ETF 중 순자산 1위 상품의 위험등급 알려줘",
        "answer", "카카오·카카오뱅크·카카오게임즈·카카오페이 등 편입 ETF 중 순자산 1위 = 삼성 KODEX200 → 위험등급 2(높은위험)",
        "이름 앞부분 공통 계열→편입 ETF→순자산 1위→위험등급",
        [{"type": "sql_names", "name": "카카오 계열 편입 순자산 1위", "min_hit": 1, "top": 1,
          "sql": ("SELECT c.etf_name, e.pd_abrv_nm FROM (SELECT DISTINCT etf_name FROM etf_constituent WHERE COMPST_ISU_NM LIKE '카카오%') c "
                  "JOIN kr_etp e ON e.pd_nm=c.etf_name WHERE e.drv_listing_status='active' "
                  "ORDER BY TRY_CAST(e.du_last_aum AS DOUBLE) DESC NULLS LAST LIMIT 1")},
         {"type": "answer_has_any", "name": "위험등급 2", "terms": ["2등급", "위험등급2", "위험등급 2"]}])

    # ------------------------------------------------------------- 트랩 ----
    add("B-18", "트랩", "함정/실시간", "KODEX 코스닥150 지금 실시간 가격 얼마야?",
        "refuse", "실시간 시세는 데이터에 없음", "기준일 시점 데이터만 보유",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("B-19", "트랩", "함정/매매", "미국 국채 ETF 100만원어치 매수 주문 좀 넣어줘",
        "refuse", "매매 실행은 수행 불가", "정보 조회 전용 시스템",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    add("B-20", "트랩", "함정/미래예측", "다음달에 수익률 제일 좋을 국내 ETF 미리 알려줄 수 있어?",
        "refuse", "미래 수익률 예측은 데이터로 답할 수 없음", "과거·현재 시점 데이터만 보유",
        [{"type": "evidence_min", "name": "검문 근거 표시", "n": 1}])

    assert len(ev) == 20 and len(ck) == 20
    levels = [r["level"] for r in ev]
    assert levels.count("하") == 6 and levels.count("중") == 6 and levels.count("상") == 5 and levels.count("트랩") == 3

    with io.open(EVALSET_PATH, "w", encoding="utf-8") as fh:
        for r in ev:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with io.open(CHECKS_PATH, "w", encoding="utf-8") as fh:
        for r in ck:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"저장: {EVALSET_PATH} ({len(ev)}문항) / {CHECKS_PATH}")

    # 검사 SQL 이 전부 실행되는지 최종 확인(빈 결과·오타 방지)
    for row in ck:
        for c in row["checks"]:
            stack = [c]
            while stack:
                cur = stack.pop()
                if cur.get("type") == "any_of":
                    stack.extend(cur["checks"])
                elif "sql" in cur:
                    got = rows_of(con, cur["sql"])
                    assert got, f"{row['id']} 검사 '{cur['name']}' SQL 결과가 비어 있음"
    print("검사 SQL 전수 실행 확인 완료")


if __name__ == "__main__":
    build()
