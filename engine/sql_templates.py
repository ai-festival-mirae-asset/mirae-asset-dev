# -*- coding: utf-8 -*-
"""
SQL 채널 — 사전 작성·검증된 템플릿 카탈로그 (S2 순서 ②, 8/13).

무엇: DuckDB 를 때리는 파라미터라이즈드 쿼리 25종. LLM 은 SQL 을 쓰지 않고
      **템플릿 id + 파라미터만** 고른다(Router 의 FC 도구 스키마에서 id 는 enum).
왜  : 환각 SQL 을 구조적으로 차단(ROADMAP §4.1 확정 방침). 평가셋 105문항의
      gold 스펙에서 역산해 만들었다 — 각 템플릿 주석에 대상 문항을 명시.

규약:
  - 수치 비교·정렬은 TRY_CAST(... AS DOUBLE/INT) — 실패=NULL=값 없음(무손실 VARCHAR 적재).
  - 콤마 포함 수치(COMPST_RTO 등)는 replace 후 캐스트.
  - Y/N·불리언 파생 컬럼은 upper(...) IN ('Y','TRUE','1') 로 판정(원천 표기 편차 방어).
  - 날짜 비교는 replace(col,'-','') 압축형 문자열 비교(ISO·YYYYMMDD 혼재 방어).
  - LIKE 파라미터는 반드시 like_param() 으로 이스케이프해 넘긴다.
  - 선택 파라미터는 ($p IS NULL OR ...) 패턴 — 미지정 시 필터 미적용.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))            # engine/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.evidence import AS_OF_CONSTITUENTS, AS_OF_MASTER, AS_OF_MASTER_GL, Evidence  # noqa: E402

MAX_EVIDENCE_ROWS = 10   # 근거는 상위 N행까지 개별 생성, 나머지는 총계 주석으로


def like_param(text):
    """사용자 텍스트 → 안전한 ILIKE 패턴(%text%). %·_ 이스케이프."""
    t = str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def like_prefix_param(text):
    """사용자 텍스트 → 앞부분 일치 ILIKE 패턴(text%) — 그룹 계열사 후보(회사명 접두) 조회용."""
    t = str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{t}%"


@dataclass(frozen=True)
class Param:
    name: str
    required: bool = False
    enum: tuple = None
    desc: str = ""


@dataclass(frozen=True)
class Template:
    id: str
    description: str            # FC 도구 설명에 그대로 들어간다 — 대상 문항 명시
    sql: str
    params: tuple
    source: str                 # Evidence.source (근거 표시)
    key_col: str                # Evidence.source_id 로 쓸 행 키 컬럼 ('' = 집계 결과)
    as_of: str = AS_OF_MASTER


def _t(id, description, sql, params=(), source="", key_col="", as_of=AS_OF_MASTER):
    return Template(id, description, sql, tuple(params), source, key_col, as_of)


# 주의(8/18): 공모펀드 sale_yn 의 실제 값은 'Y/N' 이 아니라 '판매중'/'판매완료' 다 — 8/13 판은 'Y' 만 보아
#   판매중 필터가 항상 0건이었다(자동 채점기 L-22 가 잡음). 8/18 kyungrae 반영으로 판매상태(sale_yn='판매중')와
#   당사판매여부(thco_sale_yn='Y')를 별도 파라미터로 구분한다.
TEMPLATES = {t.id: t for t in [

    # ---------------- 채권 (L-01~08, L-27, H-26) ----------------
    _t("bond_filter",
       "국내채권 필터 목록 — 통화·신용등급 서열(AA이상=4 이하)·만기상태·매수가능·표면금리·대분류. "
       "min_rating_rank 는 'BBB 이하'·'AA급'(등급대 하한) 용 — bond_count 에만 있고 여기 없어서 "
       "목록의 하한이 안 걸리던 것을 8/26 (v2 O-07) 에 맞춤. "
       "대상: L-01/03/05, H-26. 매수가능 판정은 8/26 주최 공지 확정 규칙(만기 도래 제외 전부 구매가능, "
       "BUYABLE_QUANTITY 무효)을 답변에 명시할 것.",
       """SELECT PD_NO, PD_NM, PD_ABRV_NM, STD_PD_MCLS_NM, CURR_CD, drv_crd_grd_norm, drv_crd_grd_rank,
                 SRFC_IRT, MAT_DT, drv_maturity_status, drv_is_buyable, AFTER_TAX_YIELD
          FROM kr_bond
          WHERE ($currency IS NULL OR CURR_CD = $currency)
            AND ($max_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) <= $max_rating_rank)
            AND ($min_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) >= $min_rating_rank)
            AND ($maturity_status IS NULL OR drv_maturity_status = $maturity_status)
            AND ($buyable_only IS NULL OR upper(coalesce(drv_is_buyable,'')) IN ('Y','TRUE','1'))
            AND ($min_coupon IS NULL OR TRY_CAST(SRFC_IRT AS DOUBLE) >= $min_coupon)
            AND ($max_coupon IS NULL OR TRY_CAST(SRFC_IRT AS DOUBLE) < $max_coupon)
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)
            AND ($pension_only IS NULL OR upper(trim(coalesce(PD_PEN_TR_YN,''))) IN ('Y','TRUE','1'))
            AND ($order IS NULL OR $order NOT IN ('after_tax', 'after_tax_asc')
                 OR coalesce(TRY_CAST(AFTER_TAX_YIELD AS DOUBLE), 0) <> 0)
          ORDER BY CASE WHEN $order = 'coupon' THEN TRY_CAST(SRFC_IRT AS DOUBLE) END DESC NULLS LAST,
                   CASE WHEN $order = 'coupon_asc' THEN TRY_CAST(SRFC_IRT AS DOUBLE) END ASC NULLS LAST,
                   CASE WHEN $order = 'after_tax' THEN TRY_CAST(AFTER_TAX_YIELD AS DOUBLE) END DESC NULLS LAST,
                   CASE WHEN $order = 'after_tax_asc' THEN TRY_CAST(AFTER_TAX_YIELD AS DOUBLE) END ASC NULLS LAST,
                   TRY_CAST(drv_crd_grd_rank AS INT) NULLS LAST,
                   TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST, PD_NO
          LIMIT $limit""",
       [Param("currency"), Param("max_rating_rank"), Param("min_rating_rank"),
        Param("maturity_status"), Param("buyable_only"), Param("min_coupon"),
        Param("max_coupon"), Param("bond_class"), Param("order", enum=("coupon", "coupon_asc", "after_tax", "after_tax_asc")),
        Param("pension_only"), Param("limit", required=True)],
       source="PRBD01N001", key_col="PD_NO"),

    _t("bond_count",
       "국내채권 조건 카운트 — bond_filter 와 동일 필터의 건수. 대상: L-02/05. "
       "8/28 블라인드(claude) B-01: 퇴직연금 조건이 카운트에서 빠져 전체 AAA 건수를 세던 공백 보강.",
       """SELECT count(*) AS n FROM kr_bond
          WHERE ($currency IS NULL OR CURR_CD = $currency)
            AND ($max_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) <= $max_rating_rank)
            AND ($min_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) >= $min_rating_rank)
            AND ($maturity_status IS NULL OR drv_maturity_status = $maturity_status)
            AND ($buyable_only IS NULL OR upper(coalesce(drv_is_buyable,'')) IN ('Y','TRUE','1'))
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)
            AND ($pension_only IS NULL OR upper(trim(coalesce(PD_PEN_TR_YN,''))) IN ('Y','TRUE','1'))""",
       [Param("currency"), Param("max_rating_rank"), Param("min_rating_rank"),
        Param("maturity_status"), Param("buyable_only"), Param("bond_class"),
        Param("pension_only")],
       source="PRBD01N001"),

    _t("bond_class_dist",
       "국내채권 대분류별 건수 분포. 대상: L-27.",
       "SELECT STD_PD_MCLS_NM, count(*) AS n FROM kr_bond GROUP BY 1 ORDER BY n DESC",
       [], source="PRBD01N001"),

    _t("bond_currency_dist",
       "국내채권 통화 분포 — '000'(미지정 센티널) 제외. 대상: L-07.",
       """SELECT CURR_CD, count(*) AS n FROM kr_bond
          WHERE CURR_CD IS NOT NULL AND CURR_CD <> '000' GROUP BY 1 ORDER BY n DESC""",
       [], source="PRBD01N001"),

    _t("bond_perpetual_list",
       "영구채 목록(만기 없음 — 센티널 99991231 은 전처리에서 플래그화됨). 대상: L-06.",
       """SELECT PD_NO, PD_NM, STD_PD_MCLS_NM, SRFC_IRT FROM kr_bond
          WHERE upper(coalesce(drv_is_perpetual,'')) IN ('Y','TRUE','1') ORDER BY PD_NO""",
       [], source="PRBD01N001", key_col="PD_NO"),

    _t("bond_maturing_within",
       "잔존만기 상한 복합 필터 — 만기일이 $as_of_date(요청 시점, time_policy)와 $until 사이인 "
       "활성 채권에 통화·신용등급·표면금리·대분류 조건을 함께 적용한다. 저장된 잔존일수 "
       "컬럼은 행별 기준일이 달라 쓰지 않는다. 대상: L-04/H-26.",
       """SELECT PD_NO, PD_NM, PD_ABRV_NM, STD_PD_MCLS_NM, CURR_CD, MAT_DT,
                 drv_crd_grd_norm, drv_crd_grd_rank, SRFC_IRT
          FROM kr_bond
          WHERE drv_maturity_status = 'active'
            AND replace(coalesce(MAT_DT,''),'-','') BETWEEN replace($as_of_date,'-','')
                                                        AND replace($until,'-','')
            AND ($currency IS NULL OR CURR_CD = $currency)
            AND ($max_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) <= $max_rating_rank)
            AND ($min_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) >= $min_rating_rank)
            AND ($min_coupon IS NULL OR TRY_CAST(SRFC_IRT AS DOUBLE) >= $min_coupon)
            AND ($max_coupon IS NULL OR TRY_CAST(SRFC_IRT AS DOUBLE) < $max_coupon)
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)
          ORDER BY TRY_CAST(drv_crd_grd_rank AS INT) NULLS LAST,
                   TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST,
                   replace(MAT_DT,'-',''), PD_NO LIMIT $limit""",
       [Param("as_of_date", required=True), Param("until", required=True), Param("currency"),
        Param("max_rating_rank"), Param("min_rating_rank"), Param("min_coupon"),
        Param("max_coupon"), Param("bond_class"),
        Param("limit", required=True)],
       source="PRBD01N001", key_col="PD_NO"),

    _t("bond_top_maturity",
       "활성 채권 만기일 내림차순(잔존만기 긴 순) — 영구채(만기 없음) 제외, 대분류 필터 선택. "
       "잔존만기(년·일)는 $as_of_date(요청 시점) 기준으로 SQL 이 계산해 돌려준다(생성기가 계산하면 "
       "사후 대조에 걸림 — 8/19 L-04 실측). 대상: L-04(국고채→국공채).",
       """SELECT PD_NO, PD_NM, PD_ABRV_NM, STD_PD_MCLS_NM, MAT_DT,
                 round(datediff('day', TRY_CAST($as_of_date AS DATE),
                                TRY_CAST(TRY_STRPTIME(replace(MAT_DT,'-',''), '%Y%m%d') AS DATE)) / 365.25, 1)
                     AS residual_years,
                 datediff('day', TRY_CAST($as_of_date AS DATE),
                          TRY_CAST(TRY_STRPTIME(replace(MAT_DT,'-',''), '%Y%m%d') AS DATE)) AS residual_days,
                 drv_crd_grd_norm, SRFC_IRT
          FROM kr_bond
          WHERE drv_maturity_status = 'active'
            AND upper(coalesce(drv_is_perpetual,'')) NOT IN ('Y','TRUE','1')
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)
          ORDER BY replace(coalesce(MAT_DT,''),'-','') DESC, PD_NO LIMIT $limit""",
       [Param("bond_class"), Param("as_of_date", required=True), Param("limit", required=True)],
       source="PRBD01N001", key_col="PD_NO"),

    # ---------------- 국내 ETP (L-09~16, L-26, L-28, L-30, M-15/17/18, H-16/24/28/30) ----------------
    _t("bond_detail",
       "국내채권 1종 상세 — 키(PD_NO)로 조회: 만기일·신용등급·표면금리·발행일·분류·통화·"
       "매수가능·영구채·위험등급명·퇴직연금 편입 가능·장내종가·매매단가. "
       "(8/27: 재배포본에서 평가사별 등급 PD_EVCO_CRD_GRD 삭제 → CRD_GRD 단일 원천, "
       "위험등급명·퇴직연금·장내종가·매매단가 신설.) "
       "대상: 8/22 블라인드 v2 L-06~09(채권 만기일·신용등급 질문 — 상품은 잡혔는데 속성 규칙이 없었음).",
       """SELECT PD_NO, PD_NM, PD_ABRV_NM, STD_PD_MCLS_NM, CURR_CD, ISU_DT, MAT_DT, SRFC_IRT,
                 drv_crd_grd_norm, drv_crd_grd_rank, drv_maturity_status,
                 drv_is_buyable, drv_is_perpetual, drv_risk_grade, PD_RISK_NM,
                 PD_PEN_TR_YN, EXG_CLOSE_PRICE, TRADE_PRICE
          FROM kr_bond WHERE PD_NO = $pd_no""",
       [Param("pd_no", required=True)], source="PRBD01N001", key_col="PD_NO"),

    _t("etp_by_mgmt",
       "운용사(복구값 기준) 상품 목록 — 순자산총액 내림차순, 상품명 패턴·유형·상장중 선택. "
       "대상: 8/22 v2 M-08/09(운용사 순자산 1위)·H-05(운용사×테마) — 그래프 목록엔 순자산이 없어 AI 가 포기했던 유형.",
       """SELECT e.pd_itm_no, e.pd_abrv_nm, e.pd_nm, e.drv_instrument_type, e.drv_listing_status,
                 e.pd_net_tamt, e.pd_lstg_dt, e.drv_risk_grade, e.cu_charge_rt,
                 coalesce(m.resolved, e.cu_fund_mgmt_co) AS mgmt
          FROM kr_etp e LEFT JOIN mgmt_resolved m USING (pd_itm_no)
          WHERE coalesce(m.resolved, e.cu_fund_mgmt_co) = $mgmt
            AND ($instrument_type IS NULL OR e.drv_instrument_type = $instrument_type)
            AND ($active_only IS NULL OR e.drv_listing_status = 'active')
            AND ($name_pattern IS NULL OR e.pd_nm ILIKE $name_pattern ESCAPE '\\')
            AND (coalesce($order, '') <> 'fee' OR TRY_CAST(e.cu_charge_rt AS DOUBLE) > 0)
          ORDER BY CASE WHEN $order = 'fee' THEN TRY_CAST(e.cu_charge_rt AS DOUBLE) END ASC NULLS LAST,
                   TRY_CAST(e.pd_net_tamt AS DOUBLE) DESC NULLS LAST, e.pd_itm_no LIMIT $limit""",
       [Param("mgmt", required=True), Param("instrument_type"), Param("active_only"),
        Param("name_pattern"), Param("order", enum=("fee",)), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("mgmt_product_count",
       "운용사(복구값 기준) 상품 수 — 유형(ETF/ETN)·상장상태별. 대상: 8/22 v2 M-06/07(근거 10줄을 세어 '10개'라던 오답).",
       """SELECT coalesce(m.resolved, e.cu_fund_mgmt_co) AS mgmt, e.drv_instrument_type,
                 e.drv_listing_status, count(*) AS n
          FROM kr_etp e LEFT JOIN mgmt_resolved m USING (pd_itm_no)
          WHERE coalesce(m.resolved, e.cu_fund_mgmt_co) = $mgmt
          GROUP BY 1, 2, 3 ORDER BY 2, 3""",
       [Param("mgmt", required=True)], source="PREF01N001"),

    _t("etp_detail",
       "국내 ETP 1종 상세 — 키(pd_itm_no)로 조회. grounding 이 이름→키를 먼저 푼다. "
       "8/27 재배포본 신설 필드 포함: 분배(배당)수익률·연간 추정 분배금·지급횟수·지급월·"
       "추적오차율·괴리율·1년 변동성 (구본은 전부 0/결측이라 미제공이던 항목). "
       "대상: L-09/10/28, H-30 + 분배·추적오차 단건 질의.",
       """SELECT pd_itm_no, pd_nm, pd_abrv_nm, drv_instrument_type, drv_listing_status,
                 cu_fund_mgmt_co, cu_base_index, ref_base_index, ref_geo_focus,
                 cu_charge_rt, drv_risk_grade,
                 pd_net_tamt, du_er_1y, du_er_ytd, pd_lstg_dt, drv_curr_cd,
                 pd_dvid_yield, pd_divd_amt_ann, pd_dvid_pay_cnt, pd_dvid_pay_months,
                 du_chas_errt, du_diff_rt, du_vlty_1y, du_vol_1d, cu_strtegy
          FROM kr_etp WHERE pd_itm_no = $pd_itm_no""",
       [Param("pd_itm_no", required=True)], source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_top_aum",
       "국내 ETP 순자산총액 상위 — ETF/ETN 혼재(30.7%) 함정 방어를 위해 유형 필수. "
       "name_pattern 은 테마어 결합('2차전지 ETF 중 순자산 1위' — 8/26 v3 C-08). "
       "대상: L-11(KODEX 200 1위 검증 완료), M-02 후단.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_net_tamt, cu_fund_mgmt_co, drv_risk_grade, pd_lstg_dt
          FROM kr_etp
          WHERE drv_instrument_type = $instrument_type AND drv_listing_status = 'active'
            AND ($name_pattern IS NULL OR pd_nm ILIKE $name_pattern ESCAPE '\\')
          ORDER BY TRY_CAST(pd_net_tamt AS DOUBLE) DESC NULLS LAST LIMIT $limit""",
       [Param("instrument_type", required=True, enum=("ETF", "ETN")),
        Param("limit", required=True), Param("name_pattern")],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_top_return",
       "국내 ETP 수익률 상위 — metric: ytd(=2026-01-01~08-22 규칙)/1y. "
       "정렬 기준 값이 0인 행은 제외한다(8/26 주최 공지: '값이 0인 행들은 아예 포함하지 않도록'). "
       "대상: L-14, M-15, H-09.",
       """SELECT pd_itm_no, pd_abrv_nm, du_er_ytd, du_er_1y, du_er_1m, du_er_3m, du_er_6m,
                 drv_risk_grade FROM kr_etp
          WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
            AND ($min_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_risk)
            AND ($max_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_risk)
            AND coalesce(CASE $metric WHEN 'ytd' THEN TRY_CAST(du_er_ytd AS DOUBLE)
                                      WHEN '1m' THEN TRY_CAST(du_er_1m AS DOUBLE)
                                      WHEN '3m' THEN TRY_CAST(du_er_3m AS DOUBLE)
                                      WHEN '6m' THEN TRY_CAST(du_er_6m AS DOUBLE)
                                      ELSE TRY_CAST(du_er_1y AS DOUBLE) END, 0) <> 0
          ORDER BY CASE $metric WHEN 'ytd' THEN TRY_CAST(du_er_ytd AS DOUBLE)
                                WHEN '1m' THEN TRY_CAST(du_er_1m AS DOUBLE)
                                WHEN '3m' THEN TRY_CAST(du_er_3m AS DOUBLE)
                                WHEN '6m' THEN TRY_CAST(du_er_6m AS DOUBLE)
                                ELSE TRY_CAST(du_er_1y AS DOUBLE) END DESC NULLS LAST
          LIMIT $limit""",
       [Param("metric", required=True, enum=("ytd", "1y", "1m", "3m", "6m")),
        Param("min_risk"), Param("max_risk"), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_filter_risk",
       "국내 ETP 위험등급 범위 필터(1=매우 높음~6=매우 낮음). '낮은 위험'=5~6 해석은 "
       "답변에 명시(§8.4). 대상: L-12, M-15, H-06/13.",
       """SELECT pd_itm_no, pd_abrv_nm, drv_risk_grade, pd_net_tamt FROM kr_etp
          WHERE drv_instrument_type = $instrument_type AND drv_listing_status = 'active'
            AND TRY_CAST(drv_risk_grade AS INT) BETWEEN $min_grade AND $max_grade
          ORDER BY TRY_CAST(pd_net_tamt AS DOUBLE) DESC NULLS LAST LIMIT $limit""",
       [Param("instrument_type", required=True, enum=("ETF", "ETN")),
        Param("min_grade", required=True), Param("max_grade", required=True),
        Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_name_search",
       "국내 ETP 상품명 패턴 검색 — pattern 은 like_param() 이스케이프 필수. "
       "대상: L-15(레버리지), M-17/18, H-16/24.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_nm, drv_instrument_type, drv_listing_status
          FROM kr_etp
          WHERE (pd_nm ILIKE $pattern ESCAPE '\\' OR pd_abrv_nm ILIKE $pattern ESCAPE '\\')
            AND ($pattern2 IS NULL OR pd_nm ILIKE $pattern2 ESCAPE '\\'
                 OR pd_abrv_nm ILIKE $pattern2 ESCAPE '\\')
            AND ($instrument_type IS NULL OR drv_instrument_type = $instrument_type)
            AND ($status IS NULL OR drv_listing_status = $status)
          ORDER BY pd_itm_no LIMIT $limit""",
       [Param("pattern", required=True), Param("pattern2"),
        Param("instrument_type", enum=("ETF", "ETN")),
        Param("status"), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_listed_between",
       "상장일 구간 필터(포함) — 기준일(8/22) 이후는 데이터 밖임을 답변에 명시. "
       "대상: L-16, H-28.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_lstg_dt FROM kr_etp
          WHERE drv_instrument_type = 'ETF'
            AND replace(coalesce(pd_lstg_dt,''),'-','') BETWEEN replace($date_from,'-','')
                                                            AND replace($date_to,'-','')
          ORDER BY replace(pd_lstg_dt,'-','') DESC, pd_itm_no LIMIT $limit""",
       [Param("date_from", required=True), Param("date_to", required=True),
        Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_count",
       "국내 ETP 유형·상태별 카운트 — 순자산총액(pd_net_tamt) 구간 필터 선택(초과/이상·미만/이하 구분). "
       "대상: L-13 · 8/26 v2 O-09('순자산 1조 넘는 상품 몇 개' — 금액 조건을 무시하고 전체를 세던 오답).",
       """SELECT drv_instrument_type, drv_listing_status, count(*) AS n FROM kr_etp
          WHERE ($min_aum_gt IS NULL OR TRY_CAST(pd_net_tamt AS DOUBLE) >  $min_aum_gt)
            AND ($min_aum_ge IS NULL OR TRY_CAST(pd_net_tamt AS DOUBLE) >= $min_aum_ge)
            AND ($max_aum_lt IS NULL OR TRY_CAST(pd_net_tamt AS DOUBLE) <  $max_aum_lt)
            AND ($max_aum_le IS NULL OR TRY_CAST(pd_net_tamt AS DOUBLE) <= $max_aum_le)
            AND ($min_listed_dt IS NULL OR pd_lstg_dt >= $min_listed_dt)
            AND ($max_listed_dt IS NULL OR pd_lstg_dt <= $max_listed_dt)
          GROUP BY 1, 2 ORDER BY 1, 2""",
       [Param("min_aum_gt"), Param("min_aum_ge"), Param("max_aum_lt"), Param("max_aum_le"),
        Param("min_listed_dt"), Param("max_listed_dt")],
       source="PREF01N001"),

    _t("etp_low_fee",
       "총보수 상한 필터(값 보유분만, 0 표기 제외) — 커버리지(실질결측 87.5%)와 0의 의미 미확정을 "
       "답변에 반드시 명시(partial). 0 은 '무보수'가 아니라 미수집일 가능성이 커(KODEX 200 도 0 으로 "
       "표기됨 — 실제 0.15%) 순위에서 뺀다(8/19). 대상: L-26, H-03/30.",
       """SELECT pd_itm_no, pd_abrv_nm, cu_charge_rt, drv_risk_grade FROM kr_etp
          WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
            AND TRY_CAST(cu_charge_rt AS DOUBLE) > 0
            AND TRY_CAST(cu_charge_rt AS DOUBLE) <= $max_fee
            AND ($min_grade IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_grade)
            AND ($max_grade IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_grade)
          ORDER BY TRY_CAST(cu_charge_rt AS DOUBLE), pd_itm_no LIMIT $limit""",
       [Param("max_fee", required=True), Param("limit", required=True),
        Param("min_grade"), Param("max_grade")],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_currency_dist",
       "국내 ETP 거래통화 분포. 대상: L-30.",
       "SELECT drv_curr_cd, count(*) AS n FROM kr_etp GROUP BY 1 ORDER BY n DESC",
       [], source="PREF01N001"),

    _t("etp_by_dividend",
       "국내 ETF 분배(배당) 정렬 — metric: yield(분배수익률 pd_dvid_yield)/amount(연간 추정 분배금). "
       "8/27 재배포본에서 신설된 분배 필드 기반. 값 0·결측 행은 제외(8/26 주최 공지 '값이 0인 행 미포함'). "
       "month_pattern 은 지급월 필터('월배당'=매월 지급 등 — pd_dvid_pay_months 영문 월 이름 ILIKE). "
       "대상: 분배수익률·분배금 상위 질의(구본에서는 데이터가 없어 거절하던 유형).",
       """SELECT pd_itm_no, pd_abrv_nm, pd_dvid_yield, pd_divd_amt_ann, pd_dvid_pay_cnt,
                 pd_dvid_pay_months, pd_net_tamt, drv_risk_grade
          FROM kr_etp
          WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
            AND coalesce(CASE WHEN $metric = 'amount' THEN TRY_CAST(pd_divd_amt_ann AS DOUBLE)
                              ELSE TRY_CAST(pd_dvid_yield AS DOUBLE) END, 0) <> 0
            AND ($month_pattern IS NULL OR pd_dvid_pay_months ILIKE $month_pattern ESCAPE '\\')
            AND ($min_pay_cnt IS NULL OR TRY_CAST(pd_dvid_pay_cnt AS INT) >= $min_pay_cnt)
            AND ($min_listed_dt IS NULL OR pd_lstg_dt >= $min_listed_dt)
          ORDER BY CASE WHEN $metric = 'amount' THEN TRY_CAST(pd_divd_amt_ann AS DOUBLE)
                        ELSE TRY_CAST(pd_dvid_yield AS DOUBLE) END DESC NULLS LAST,
                   pd_itm_no LIMIT $limit""",
       [Param("metric", required=True, enum=("yield", "amount")), Param("month_pattern"),
        Param("min_pay_cnt"), Param("min_listed_dt"), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_metric_rank",
       "국내 ETP 수치 항목(괴리율·추적오차율·변동성) 순위 — 8/26 재배포 신설 수치의 최대/최소·"
       "정렬 질의가 길이 없어 거절·이름검색으로 새던 공백(8/28 블라인드(claude) B-04/12/16). "
       "정렬 기준 값이 0·결측인 행은 제외(8/26 주최 공지), 상장중(active) 기준. "
       "괴리율은 부호 유지 값(절댓값 아님) — 해석은 라우터 노트로 명시. "
       "$index_pattern 은 기초지수(cu/ref_base_index)와 상품명을 함께 검색('%S&P%500%' 식).",
       """SELECT pd_itm_no, pd_abrv_nm, pd_nm, drv_instrument_type,
                 TRY_CAST(du_diff_rt AS DOUBLE) AS du_diff_rt,
                 TRY_CAST(du_chas_errt AS DOUBLE) AS du_chas_errt,
                 TRY_CAST(du_vlty_1m AS DOUBLE) AS du_vlty_1m,
                 TRY_CAST(du_vlty_3m AS DOUBLE) AS du_vlty_3m,
                 TRY_CAST(du_vlty_6m AS DOUBLE) AS du_vlty_6m,
                 TRY_CAST(du_vlty_1y AS DOUBLE) AS du_vlty_1y,
                 TRY_CAST(du_vol_1d AS DOUBLE) AS du_vol_1d,
                 cu_charge_rt, drv_risk_grade,
                 coalesce(cu_base_index, ref_base_index) AS base_index, du_last_aum
          FROM kr_etp
          WHERE drv_listing_status = 'active'
            AND ($type IS NULL OR drv_instrument_type = $type)
            AND ($index_pattern IS NULL OR coalesce(cu_base_index, ref_base_index) ILIKE $index_pattern
                 OR pd_nm ILIKE $index_pattern OR pd_abrv_nm ILIKE $index_pattern)
            AND coalesce(CASE $metric WHEN 'diff' THEN TRY_CAST(du_diff_rt AS DOUBLE)
                                      WHEN 'tracking' THEN TRY_CAST(du_chas_errt AS DOUBLE)
                                      WHEN 'vol_1m' THEN TRY_CAST(du_vlty_1m AS DOUBLE)
                                      WHEN 'vol_3m' THEN TRY_CAST(du_vlty_3m AS DOUBLE)
                                      WHEN 'vol_6m' THEN TRY_CAST(du_vlty_6m AS DOUBLE)
                                      WHEN 'volume' THEN TRY_CAST(du_vol_1d AS DOUBLE)
                                      ELSE TRY_CAST(du_vlty_1y AS DOUBLE) END, 0) <> 0
          ORDER BY CASE WHEN $direction = 'asc' THEN
                          CASE $metric WHEN 'diff' THEN TRY_CAST(du_diff_rt AS DOUBLE)
                                       WHEN 'tracking' THEN TRY_CAST(du_chas_errt AS DOUBLE)
                                       WHEN 'vol_1m' THEN TRY_CAST(du_vlty_1m AS DOUBLE)
                                       WHEN 'vol_3m' THEN TRY_CAST(du_vlty_3m AS DOUBLE)
                                       WHEN 'vol_6m' THEN TRY_CAST(du_vlty_6m AS DOUBLE)
                                       WHEN 'volume' THEN TRY_CAST(du_vol_1d AS DOUBLE)
                                       ELSE TRY_CAST(du_vlty_1y AS DOUBLE) END END ASC NULLS LAST,
                   CASE WHEN $direction = 'desc' THEN
                          CASE $metric WHEN 'diff' THEN TRY_CAST(du_diff_rt AS DOUBLE)
                                       WHEN 'tracking' THEN TRY_CAST(du_chas_errt AS DOUBLE)
                                       WHEN 'vol_1m' THEN TRY_CAST(du_vlty_1m AS DOUBLE)
                                       WHEN 'vol_3m' THEN TRY_CAST(du_vlty_3m AS DOUBLE)
                                       WHEN 'vol_6m' THEN TRY_CAST(du_vlty_6m AS DOUBLE)
                                       WHEN 'volume' THEN TRY_CAST(du_vol_1d AS DOUBLE)
                                       ELSE TRY_CAST(du_vlty_1y AS DOUBLE) END END DESC NULLS LAST,
                   pd_itm_no
          LIMIT $limit""",
       [Param("metric", required=True,
              enum=("diff", "tracking", "vol_1m", "vol_3m", "vol_6m", "vol_1y", "volume")),
        Param("direction", required=True, enum=("asc", "desc")),
        Param("type", enum=("ETF", "ETN")), Param("index_pattern"),
        Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("risk_grade_product_counts",
       "금융상품 위험등급별 국내채권·ETF·ETN·공모펀드 상품 수. 국내채권은 원천의 "
       "상품 위험등급 1~6을 사용하고, 해외ETF는 위험등급 필드가 없어 제외한다. 대상: H-13.",
       """SELECT '국내채권' AS product_group, count(*) AS n
          FROM kr_bond WHERE TRY_CAST(drv_risk_grade AS INT) = $grade
          UNION ALL
          SELECT '국내ETF' AS product_group, count(*) AS n
          FROM kr_etp WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
            AND TRY_CAST(drv_risk_grade AS INT) = $grade
          UNION ALL
          SELECT '국내ETN' AS product_group, count(*) AS n
          FROM kr_etp WHERE drv_instrument_type = 'ETN' AND drv_listing_status = 'active'
            AND TRY_CAST(drv_risk_grade AS INT) = $grade
          UNION ALL
          SELECT '공모펀드' AS product_group, count(*) AS n
          FROM fund_master WHERE TRY_CAST(drv_risk_grade AS INT) = $grade
          ORDER BY product_group""",
       [Param("grade", required=True, enum=(1, 2, 3, 4, 5, 6))],
       source="PRBD01N001·PREF01N001·PRFD01N001"),

    # ---------------- 해외 ETF (L-17~20, M-29 방어는 Validation) ----------------
    _t("global_etf_filter",
       "해외ETF 필터 — 지역(포함/제외)·상품명/전략 서술 패턴·인버스·거래통화. 위험등급 컬럼은 원천에 없음(요청 시 "
       "Validation 이 refuse — T-13/M-29). exclude_region_pattern='United States' + name_pattern='dividend' 이면 "
       "'미국 말고 다른 지역 배당형'(H-18). 대상: L-18/19/20, H-18.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_nm, wu_inv_rgn, pd_trd_ccy, du_last_aum FROM global_etf
          WHERE ($region_pattern IS NULL OR wu_inv_rgn ILIKE $region_pattern ESCAPE '\\')
            AND ($exclude_region_pattern IS NULL OR coalesce(wu_inv_rgn, '') NOT ILIKE $exclude_region_pattern ESCAPE '\\')
            AND ($name_pattern IS NULL OR pd_nm ILIKE $name_pattern ESCAPE '\\'
                 OR pd_abrv_nm ILIKE $name_pattern ESCAPE '\\' OR cu_strtegy ILIKE $name_pattern ESCAPE '\\')
            AND ($inverse_only IS NULL OR upper(coalesce(drv_is_inverse,'')) IN ('Y','TRUE','1'))
            AND ($etn_only IS NULL OR upper(coalesce(drv_is_etn,'')) IN ('Y','TRUE','1'))
            AND ($ccy IS NULL OR pd_trd_ccy = $ccy)
            AND ($exclude_ccy IS NULL OR pd_trd_ccy <> $exclude_ccy)
          ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC NULLS LAST, pd_itm_no LIMIT $limit""",
       [Param("region_pattern"), Param("exclude_region_pattern"), Param("name_pattern"),
        Param("inverse_only"), Param("etn_only"), Param("ccy"), Param("exclude_ccy"),
        Param("limit", required=True)],
       source="PREF02N001", key_col="pd_itm_no", as_of=AS_OF_MASTER_GL),

    _t("global_etf_count",
       "해외ETF 유형별 카운트(ETF/ETN 구분 — 8/27 재배포본 5,972/65). 인버스·ETN 조건 선택 "
       "(8/28 r2 R2-05: 인버스 조건을 무시하고 전체를 세던 오답). 대상: L-17.",
       """SELECT drv_instrument_type, count(*) AS n FROM global_etf
          WHERE ($inverse_only IS NULL OR upper(coalesce(drv_is_inverse,'')) IN ('Y','TRUE','1'))
            AND ($etn_only IS NULL OR upper(coalesce(drv_is_etn,'')) IN ('Y','TRUE','1'))
          GROUP BY 1 ORDER BY n DESC""",
       [Param("inverse_only"), Param("etn_only")], source="PREF02N001", as_of=AS_OF_MASTER_GL),

    _t("global_ccy_dist",
       "해외ETF 거래통화 분포. 대상: L-20.",
       "SELECT pd_trd_ccy, count(*) AS n FROM global_etf GROUP BY 1 ORDER BY n DESC",
       [], source="PREF02N001", as_of=AS_OF_MASTER_GL),

    # ---------------- 공모펀드 (L-21~25, L-29 는 사전 기반) ----------------
    _t("fund_counts",
       "공모펀드 상품(마스터)·판매 클래스 수 — 클래스 행수(23,676)≠상품 수(23,622) 함정 방어. "
       "상품 단위는 금감원 펀드코드(fss_itm_no) 그룹, 코드 없는 행은 행 자체가 상품(8/27 재배포본). 대상: L-21.",
       """SELECT (SELECT count(*) FROM fund_master) AS products,
                 (SELECT count(*) FROM fund_class)  AS share_classes,
                 (SELECT count(*) FROM fund_master
                   WHERE replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중') AS on_sale_products,
                 (SELECT count(*) FROM fund_class
                   WHERE replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중') AS on_sale_classes""",
       [], source="PRFD01N001"),

    _t("fund_filter",
       "공모펀드 필터 — 현재 판매상태(sale_yn)와 당사판매여부(thco_sale_yn)를 구분하고 "
       "운용속성·위험등급을 함께 적용. 마스터(상품) 단위. 대상: L-22/23.",
       """SELECT itm_no, itm_nm, itm_abrv_nm, or_attr_desc, drv_risk_grade, sale_yn, thco_sale_yn,
                 share_class_count, ovrs_fd_desc, fd_nast_suma, fd_last_dstb_r,
                 sale_co_rwrd_r, or_co_rwrd_r, trusc_rwrd_r, ofwk_trus_rwrd_r
          FROM fund_master
          WHERE ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
            AND ($thco_sale_only IS NULL OR upper(trim(coalesce(thco_sale_yn,'')))
                                             IN ('Y','TRUE','1'))
            AND ($attr_pattern IS NULL OR or_attr_desc ILIKE $attr_pattern ESCAPE '\\')
            AND ($btyp_pattern IS NULL OR zrin_btyp_nm ILIKE $btyp_pattern)
            AND ($min_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_risk)
            AND ($max_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_risk)
            AND ($region IS NULL OR ovrs_fd_desc = $region)
          ORDER BY CASE WHEN $order = 'aum' THEN TRY_CAST(fd_nast_suma AS DOUBLE) END DESC NULLS LAST,
                   CASE WHEN $order IS NULL AND $on_sale_only IS NULL
                             AND replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중' THEN 0
                        WHEN $order IS NULL AND $on_sale_only IS NULL THEN 1
                        ELSE 0 END,                       -- 8/26 v2 O-08: 정렬 미지정 시 판매중 우선
                   itm_no LIMIT $limit""",
       [Param("on_sale_only"), Param("thco_sale_only"), Param("attr_pattern"),
        Param("btyp_pattern"), Param("min_risk"),
        Param("max_risk"), Param("region"), Param("order"), Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    _t("fund_by_fee",
       "공모펀드 보수 정렬 — 재배포본 신설 보수 분해 4종(판매 sale_co_rwrd_r·운용 or_co_rwrd_r·"
       "수탁 trusc_rwrd_r·사무 ofwk_trus_rwrd_r)의 합을 총보수로 계산해 정렬한다. "
       "8/28 r2: 구본 기준 '펀드 총보수 없음' 일괄 거절이 낡은 규칙이 된 것(v1 T-14 전환). "
       "합 0·결측 상품 제외(주최 공지 0값 원칙), 커버리지는 라우터 노트로 명시.",
       """SELECT itm_no, itm_nm, itm_abrv_nm, zrin_btyp_nm, sale_yn,
                 TRY_CAST(sale_co_rwrd_r AS DOUBLE) AS sale_co_rwrd_r,
                 TRY_CAST(or_co_rwrd_r AS DOUBLE) AS or_co_rwrd_r,
                 TRY_CAST(trusc_rwrd_r AS DOUBLE) AS trusc_rwrd_r,
                 TRY_CAST(ofwk_trus_rwrd_r AS DOUBLE) AS ofwk_trus_rwrd_r,
                 round(coalesce(TRY_CAST(sale_co_rwrd_r AS DOUBLE), 0)
                       + coalesce(TRY_CAST(or_co_rwrd_r AS DOUBLE), 0)
                       + coalesce(TRY_CAST(trusc_rwrd_r AS DOUBLE), 0)
                       + coalesce(TRY_CAST(ofwk_trus_rwrd_r AS DOUBLE), 0), 4) AS total_fee_pct,
                 fd_nast_suma
          FROM fund_master
          WHERE coalesce(TRY_CAST(sale_co_rwrd_r AS DOUBLE), 0)
                + coalesce(TRY_CAST(or_co_rwrd_r AS DOUBLE), 0)
                + coalesce(TRY_CAST(trusc_rwrd_r AS DOUBLE), 0)
                + coalesce(TRY_CAST(ofwk_trus_rwrd_r AS DOUBLE), 0) > 0
            AND ($order <> 'sale_asc' OR TRY_CAST(sale_co_rwrd_r AS DOUBLE) > 0)
            AND ($attr_pattern IS NULL OR or_attr_desc ILIKE $attr_pattern ESCAPE '\\')
            AND ($btyp_pattern IS NULL OR zrin_btyp_nm ILIKE $btyp_pattern)
            AND ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
          ORDER BY CASE WHEN $order = 'total_asc' THEN total_fee_pct END ASC NULLS LAST,
                   CASE WHEN $order = 'total_desc' THEN total_fee_pct END DESC NULLS LAST,
                   CASE WHEN $order = 'sale_asc' THEN TRY_CAST(sale_co_rwrd_r AS DOUBLE) END ASC NULLS LAST,
                   itm_no LIMIT $limit""",
       [Param("order", required=True, enum=("total_asc", "total_desc", "sale_asc")),
        Param("attr_pattern"), Param("btyp_pattern"), Param("on_sale_only"),
        Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    _t("etp_filter_leverage",
       "레버리지 배수(cu_lev_fector) 필터 — 8/28 r2 R2-06: '3배 레버리지 ETF 있어?'가 이름 검색으로 "
       "새서 배수 조건을 무시하던 공백. 상장중 기준, 순자산 내림차순. ETF·ETN 모두 포함.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_nm, drv_instrument_type, cu_lev_fector,
                 du_last_aum, drv_risk_grade
          FROM kr_etp
          WHERE drv_listing_status = 'active'
            AND TRY_CAST(cu_lev_fector AS DOUBLE) = $factor
          ORDER BY TRY_CAST(du_last_aum AS DOUBLE) DESC NULLS LAST, pd_itm_no LIMIT $limit""",
       [Param("factor", required=True), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("fund_by_composition",
       "공모펀드 자산구성 비율(zrin 4종: 국내/해외 × 주식/채권) 문턱값 필터 — 값 보유 상품만. "
       "8/28 블라인드(claude) B-09: '해외 채권 비중 50% 넘는' 조건이 필터로 안 걸려 HCX 가 "
       "상품명으로 비중을 추측하는 오답이 나오던 공백(추측 금지 원칙 위반). "
       "$strict='Y' 면 초과(>), 없으면 이상(>=). 결측 다수 — 커버리지 한계는 라우터 노트로 명시. "
       "$field 는 필수 짝($min_rt 와 함께)이며 라우터만 채운다.",
       """SELECT itm_no, itm_nm, itm_abrv_nm, zrin_btyp_nm,
                 TRY_CAST(zrin_dmst_stk_cmst_rt AS DOUBLE) AS zrin_dmst_stk_cmst_rt,
                 TRY_CAST(zrin_ovrs_stk_cmst_rt AS DOUBLE) AS zrin_ovrs_stk_cmst_rt,
                 TRY_CAST(zrin_dmst_bd_cmst_rt AS DOUBLE) AS zrin_dmst_bd_cmst_rt,
                 TRY_CAST(zrin_ovrs_bd_cmst_rt AS DOUBLE) AS zrin_ovrs_bd_cmst_rt,
                 drv_risk_grade, sale_yn, fd_nast_suma
          FROM fund_master
          WHERE CASE $field WHEN 'dmst_stk' THEN TRY_CAST(zrin_dmst_stk_cmst_rt AS DOUBLE)
                            WHEN 'ovrs_stk' THEN TRY_CAST(zrin_ovrs_stk_cmst_rt AS DOUBLE)
                            WHEN 'dmst_bd' THEN TRY_CAST(zrin_dmst_bd_cmst_rt AS DOUBLE)
                            ELSE TRY_CAST(zrin_ovrs_bd_cmst_rt AS DOUBLE) END IS NOT NULL
            AND (($strict IS NULL AND CASE $field WHEN 'dmst_stk' THEN TRY_CAST(zrin_dmst_stk_cmst_rt AS DOUBLE)
                                                  WHEN 'ovrs_stk' THEN TRY_CAST(zrin_ovrs_stk_cmst_rt AS DOUBLE)
                                                  WHEN 'dmst_bd' THEN TRY_CAST(zrin_dmst_bd_cmst_rt AS DOUBLE)
                                                  ELSE TRY_CAST(zrin_ovrs_bd_cmst_rt AS DOUBLE) END >= $min_rt)
                 OR ($strict = 'Y' AND CASE $field WHEN 'dmst_stk' THEN TRY_CAST(zrin_dmst_stk_cmst_rt AS DOUBLE)
                                                   WHEN 'ovrs_stk' THEN TRY_CAST(zrin_ovrs_stk_cmst_rt AS DOUBLE)
                                                   WHEN 'dmst_bd' THEN TRY_CAST(zrin_dmst_bd_cmst_rt AS DOUBLE)
                                                   ELSE TRY_CAST(zrin_ovrs_bd_cmst_rt AS DOUBLE) END > $min_rt))
            AND ($btyp_pattern IS NULL OR zrin_btyp_nm ILIKE $btyp_pattern)
            AND ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
          ORDER BY CASE $field WHEN 'dmst_stk' THEN TRY_CAST(zrin_dmst_stk_cmst_rt AS DOUBLE)
                               WHEN 'ovrs_stk' THEN TRY_CAST(zrin_ovrs_stk_cmst_rt AS DOUBLE)
                               WHEN 'dmst_bd' THEN TRY_CAST(zrin_dmst_bd_cmst_rt AS DOUBLE)
                               ELSE TRY_CAST(zrin_ovrs_bd_cmst_rt AS DOUBLE) END DESC NULLS LAST,
                   TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST, itm_no
          LIMIT $limit""",
       [Param("field", required=True, enum=("dmst_stk", "ovrs_stk", "dmst_bd", "ovrs_bd")),
        Param("min_rt", required=True), Param("strict"), Param("btyp_pattern"),
        Param("on_sale_only"), Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    _t("fund_class_by_fee",
       "공모펀드 클래스(판매 단위) 필터 — 수수료 유형(han_clas_fee_type: 수수료미징구/선취/후취)·"
       "판매채널·운용전략(인덱스 등)·위험등급 결합. 8/28 블라인드(claude) B-11: "
       "'판매수수료 없는 클래스' 조건이 필터로 안 걸려 MMF 대형 목록이 나가던 공백. "
       "수수료 유형 값이 없는 클래스(원천 결측 다수)는 판정에서 제외 — 한계는 라우터 노트로 명시.",
       """SELECT itm_no, itm_nm, itm_abrv_nm, han_clas_fee_type, han_clas_sales_channel,
                 zrin_ptn_nm, zrin_btyp_nm, drv_risk_grade, sale_yn, fd_nast_suma
          FROM fund_class
          WHERE ($fee_type IS NULL OR han_clas_fee_type = $fee_type)
            AND ($channel_pattern IS NULL OR han_clas_sales_channel ILIKE $channel_pattern)
            AND ($strategy_pattern IS NULL OR zrin_ptn_nm ILIKE $strategy_pattern
                 OR itm_nm ILIKE $strategy_pattern)
            AND ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
            AND ($min_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_risk)
            AND ($max_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_risk)
          ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST, itm_no
          LIMIT $limit""",
       [Param("fee_type", enum=("수수료미징구", "수수료선취", "수수료후취")),
        Param("channel_pattern"), Param("strategy_pattern"), Param("on_sale_only"),
        Param("min_risk"), Param("max_risk"), Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    _t("fund_class_count",
       "공모펀드 클래스 조건 건수 — fund_class_by_fee 와 동일 필터의 카운트 "
       "(8/28 r2 R2-12: 온라인 전용 클래스 수를 묻는데 목록만 내던 공백).",
       """SELECT count(*) AS n FROM fund_class
          WHERE ($fee_type IS NULL OR han_clas_fee_type = $fee_type)
            AND ($channel_pattern IS NULL OR han_clas_sales_channel ILIKE $channel_pattern)
            AND ($strategy_pattern IS NULL OR zrin_ptn_nm ILIKE $strategy_pattern
                 OR itm_nm ILIKE $strategy_pattern)
            AND ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
            AND ($min_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_risk)
            AND ($max_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_risk)""",
       [Param("fee_type", enum=("수수료미징구", "수수료선취", "수수료후취")),
        Param("channel_pattern"), Param("strategy_pattern"), Param("on_sale_only"),
        Param("min_risk"), Param("max_risk")],
       source="PRFD01N001"),

    _t("fund_top_return_1y",
       "공모펀드 1년 수익률 상위(값 보유분만) — 커버리지는 coverage_check 로 병행 조회해 "
       "답변에 명시. 대상: L-24.",
       """SELECT itm_no, itm_nm, fd_yr1_ern_r, drv_risk_grade, sale_yn, thco_sale_yn
          FROM fund_master
          WHERE TRY_CAST(fd_yr1_ern_r AS DOUBLE) IS NOT NULL
            AND TRY_CAST(fd_yr1_ern_r AS DOUBLE) <> 0   -- 8/26 공지: 값 0 행은 미포함
            AND ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
            AND ($thco_sale_only IS NULL OR upper(trim(coalesce(thco_sale_yn,'')))
                                             IN ('Y','TRUE','1'))
          ORDER BY TRY_CAST(fd_yr1_ern_r AS DOUBLE) DESC LIMIT $limit""",
       [Param("on_sale_only"), Param("thco_sale_only"), Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    _t("fund_by_benchmark",
       "벤치마크 명칭 패턴으로 펀드 검색(표기 변형은 like_param + 정규화 사전으로 대응). "
       "대상: L-25.",
       """SELECT itm_no, itm_nm, bmrk_nm FROM fund_master
          WHERE bmrk_nm ILIKE $pattern ESCAPE '\\' ORDER BY itm_no LIMIT $limit""",
       [Param("pattern", required=True), Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    # ---------------- 구성종목 (M-01~07/16/21/25, H-10/14/22 — 기준일 7/10!) ----------------
    _t("constituent_holders",
       "특정 종목(코드/ISIN)을 편입한 ETF 목록 + 비중 — 기준일 2026-07-10(직전 거래일) "
       "명시 필수. order='aum' 이면 순자산 큰 순(M-02 '순자산 큰 순서로'), 기본은 비중 큰 순. "
       "mgmt(운용사 복구값)를 주면 그 운용사 상품만(8/26 v2 H-08 '…중에 미래에셋자산운용이 운용하는'). "
       "상품명은 마스터 약칭(pd_abrv_nm)으로 표시. 대상: M-01/02/16/21, H-06/27.",
       """SELECT c.etf_isin, coalesce(e.pd_abrv_nm, c.etf_name) AS pd_abrv_nm, c.COMPST_ISU_NM,
                 TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct,
                 e.pd_net_tamt, e.drv_risk_grade, e.cu_charge_rt,
                 e.pd_lstg_dt, e.pd_dvid_yield,
                 coalesce(m.resolved, e.cu_fund_mgmt_co) AS mgmt
          FROM etf_constituent c LEFT JOIN kr_etp e ON c.etf_isin = e.pd_itm_no
          LEFT JOIN mgmt_resolved m ON m.pd_itm_no = e.pd_itm_no
          WHERE c.COMPST_ISU_CD = $code
            AND ($mgmt IS NULL OR coalesce(m.resolved, e.cu_fund_mgmt_co) = $mgmt)
            AND ($name_pattern IS NULL OR coalesce(e.pd_nm, c.etf_name) ILIKE $name_pattern ESCAPE '\\')
            AND (coalesce($order, '') <> 'fee' OR TRY_CAST(e.cu_charge_rt AS DOUBLE) > 0)
          ORDER BY CASE WHEN $order = 'fee' THEN TRY_CAST(e.cu_charge_rt AS DOUBLE) END ASC NULLS LAST,
                   CASE WHEN $order = 'aum' THEN TRY_CAST(e.pd_net_tamt AS DOUBLE) END DESC NULLS LAST,
                   weight_pct DESC NULLS LAST, c.etf_isin LIMIT $limit""",
       [Param("code", required=True), Param("limit", required=True),
        Param("order", enum=("aum", "weight", "fee")), Param("mgmt"), Param("name_pattern")],
       source="KRX-PDF", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_holders_top_return",
       "특정 종목을 편입한 국내 상장중 ETF 를 1년 수익률 내림차순으로 조회 — 8/26 주최 교차질의 "
       "공식 예시('삼성전자를 보유한 국내/해외ETF와 공모펀드를 연 수익률 기준 TOP10') 대응. "
       "해외 ETF 는 1년 수익률 원천이 없어 제외해도 무방(주최 문답 확정), 펀드 보유종목 자료는 "
       "제공 데이터에 없음 — 두 한계는 라우터 노트로 답변에 명시한다. 수익률 0·결측 행 제외.",
       """SELECT c.etf_isin, coalesce(e.pd_abrv_nm, c.etf_name) AS pd_abrv_nm,
                 e.du_er_1y, e.du_er_ytd, e.drv_risk_grade,
                 max(TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE)) AS weight_pct,
                 e.pd_net_tamt, coalesce(m.resolved, e.cu_fund_mgmt_co) AS mgmt
          FROM etf_constituent c
          JOIN kr_etp e ON c.etf_isin = e.pd_itm_no
          LEFT JOIN mgmt_resolved m ON m.pd_itm_no = e.pd_itm_no
          WHERE c.COMPST_ISU_CD = $code
            AND e.drv_instrument_type = 'ETF' AND e.drv_listing_status = 'active'
            AND coalesce(TRY_CAST(e.du_er_1y AS DOUBLE), 0) <> 0
          GROUP BY c.etf_isin, coalesce(e.pd_abrv_nm, c.etf_name), e.du_er_1y, e.du_er_ytd,
                   e.drv_risk_grade, e.pd_net_tamt, coalesce(m.resolved, e.cu_fund_mgmt_co)
          ORDER BY TRY_CAST(e.du_er_1y AS DOUBLE) DESC NULLS LAST, c.etf_isin
          LIMIT $limit""",
       [Param("code", required=True), Param("limit", required=True)],
       source="KRX-PDF·PREF01N001", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_top_weights",
       "ETF 1종의 구성종목 비중 상위 — 대상: M-25(TIGER 200 상위 3), H-10.",
       """SELECT COMPST_ISU_NM, COMPST_ISU_CD, SECUGRP_ID,
                 TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct
          FROM etf_constituent WHERE etf_isin = $etf_id
          ORDER BY weight_pct DESC NULLS LAST LIMIT $limit""",
       [Param("etf_id", required=True), Param("limit", required=True)],
       source="KRX-PDF", key_col="COMPST_ISU_CD", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_intersection_low_fee",
       "두 구성종목을 모두 편입한 국내 ETF 교집합에서 총보수 값 보유분을 오름차순 조회. "
       "총보수 결측률과 0 값 의미 한계를 함께 밝혀야 한다. 대상: H-03.",
       """WITH a AS (
              SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD = $code_a
          ), b AS (
              SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD = $code_b
          )
          SELECT e.pd_itm_no, e.pd_abrv_nm, e.cu_charge_rt, e.pd_net_tamt
          FROM a JOIN b USING (etf_isin)
          JOIN kr_etp e ON e.pd_itm_no = etf_isin
          WHERE e.drv_instrument_type = 'ETF' AND e.drv_listing_status = 'active'
            AND TRY_CAST(e.cu_charge_rt AS DOUBLE) > 0          -- 0 표기(의미 미확정·미수집 추정)는 순위에서 제외(8/19)
          ORDER BY TRY_CAST(e.cu_charge_rt AS DOUBLE),
                   TRY_CAST(e.pd_net_tamt AS DOUBLE) DESC NULLS LAST, e.pd_itm_no
          LIMIT $limit""",
       [Param("code_a", required=True), Param("code_b", required=True),
        Param("limit", required=True)],
       source="KRX-PDF·PREF01N001", key_col="pd_itm_no", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_candidate_holders_by_aum",
       "복수 구성종목 후보 중 하나 이상을 편입한 ETF를 합쳐 순자산 내림차순 조회. "
       "법적 관계가 미수집된 자회사 질의에서 후보 전체의 전역 순위를 낼 때 사용. 대상: H-01.",
       """SELECT c.etf_isin, c.etf_name,
                 string_agg(DISTINCT c.COMPST_ISU_NM, ' / ' ORDER BY c.COMPST_ISU_NM) AS matched_candidates,
                 max(TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE)) AS max_weight_pct,
                 e.pd_net_tamt, e.drv_risk_grade
          FROM etf_constituent c
          LEFT JOIN kr_etp e ON c.etf_isin = e.pd_itm_no
          WHERE c.COMPST_ISU_CD IN ($code_a, $code_b, $code_c, $code_d)
          GROUP BY c.etf_isin, c.etf_name, e.pd_net_tamt, e.drv_risk_grade
          ORDER BY TRY_CAST(e.pd_net_tamt AS DOUBLE) DESC NULLS LAST, c.etf_isin
          LIMIT $limit""",
       [Param("code_a", required=True), Param("code_b"), Param("code_c"), Param("code_d"),
        Param("limit", required=True)],
       source="KRX-PDF·PREF01N001", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_prefix_holders_by_aum",
       "회사명이 $prefix 로 시작하는 종목(자회사·계열사 후보 — 이름 접두 기준 근사)을 하나 이상 "
       "편입한 ETF 를 순자산 내림차순으로 조회. 법적 관계 데이터가 없어 접두 후보임을 답변에 "
       "반드시 명시할 것. 대상: H-01(에코프로 자회사) · 8/26 v2 O-05(LG 자회사 — 후보 4종 나열 "
       "방식이 엉뚱한 상품을 잡던 것을 접두 집계로 교체).",
       """SELECT c.etf_isin, coalesce(e.pd_abrv_nm, c.etf_name) AS pd_abrv_nm,
                 string_agg(DISTINCT c.COMPST_ISU_NM, ' / ' ORDER BY c.COMPST_ISU_NM) AS matched_candidates,
                 max(TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE)) AS max_weight_pct,
                 e.pd_net_tamt, e.drv_risk_grade,
                 coalesce(m.resolved, e.cu_fund_mgmt_co) AS mgmt
          FROM etf_constituent c
          LEFT JOIN kr_etp e ON c.etf_isin = e.pd_itm_no
          LEFT JOIN mgmt_resolved m ON m.pd_itm_no = e.pd_itm_no
          WHERE c.SECUGRP_ID IS NOT NULL AND c.COMPST_ISU_NM ILIKE $prefix ESCAPE '\\'
          GROUP BY c.etf_isin, coalesce(e.pd_abrv_nm, c.etf_name), e.pd_net_tamt, e.drv_risk_grade,
                   coalesce(m.resolved, e.cu_fund_mgmt_co)
          ORDER BY TRY_CAST(e.pd_net_tamt AS DOUBLE) DESC NULLS LAST, c.etf_isin
          LIMIT $limit""",
       [Param("prefix", required=True), Param("limit", required=True)],
       source="KRX-PDF·PREF01N001", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_intersection_top_aum",
       "두 구성종목을 모두 편입한 국내 상장중 ETF 를 순자산총액 내림차순으로 조회 — "
       "'둘 다 담은 ETF 중 순자산 1위'(8/26 v3 C-09). 상품명 조각 규칙이 가로채던 유형.",
       """WITH a AS (
              SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD = $code_a
          ), b AS (
              SELECT DISTINCT etf_isin FROM etf_constituent WHERE COMPST_ISU_CD = $code_b
          )
          SELECT e.pd_itm_no, e.pd_abrv_nm, e.pd_net_tamt, e.drv_risk_grade,
                 coalesce(m.resolved, e.cu_fund_mgmt_co) AS mgmt
          FROM a JOIN b USING (etf_isin)
          JOIN kr_etp e ON e.pd_itm_no = etf_isin
          LEFT JOIN mgmt_resolved m ON m.pd_itm_no = e.pd_itm_no
          WHERE e.drv_instrument_type = 'ETF' AND e.drv_listing_status = 'active'
          ORDER BY TRY_CAST(e.pd_net_tamt AS DOUBLE) DESC NULLS LAST, e.pd_itm_no
          LIMIT $limit""",
       [Param("code_a", required=True), Param("code_b", required=True),
        Param("limit", required=True)],
       source="KRX-PDF·PREF01N001", key_col="pd_itm_no", as_of=AS_OF_CONSTITUENTS),

    _t("bond_etf_rating_dist",
       "상품명에 회사채가 표시된 ETF의 BN 구성종목을 채권 마스터와 조인한 신용등급 분포. "
       "키 미매칭·등급 결측은 미확인으로 남기고 매칭 커버리지를 함께 반환한다. 대상: H-15.",
       """WITH base AS (
              SELECT DISTINCT c.etf_isin, c.etf_name, c.COMPST_ISU_CD
              FROM etf_constituent c
              WHERE c.SECUGRP_ID = 'BN' AND c.etf_name ILIKE '%회사채%'
          ), joined AS (
              SELECT base.*, b.PD_NO, b.drv_crd_grd_norm
              FROM base LEFT JOIN kr_bond b ON base.COMPST_ISU_CD = b.PD_NO
          ), totals AS (
              SELECT count(*) AS total_constituents,
                     count(drv_crd_grd_norm) AS matched_ratings,
                     count(DISTINCT etf_isin) AS target_etfs
              FROM joined
          ), dist AS (
              SELECT coalesce(drv_crd_grd_norm, '미확인') AS credit_rating, count(*) AS n
              FROM joined GROUP BY 1
          )
          SELECT dist.credit_rating, dist.n, totals.total_constituents,
                 totals.matched_ratings, totals.target_etfs
          FROM dist CROSS JOIN totals
          ORDER BY CASE WHEN dist.credit_rating = '미확인' THEN 999 ELSE 0 END,
                   dist.credit_rating""",
       [], source="KRX-PDF·PRBD01N001", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_weight_above",
       "특정 종목을 비중 X% 초과로 담은 ETF — 대상: H-14(삼성전자 30%+ 실측 존재).",
       """SELECT c.etf_isin, c.etf_name,
                 TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct
          FROM etf_constituent c WHERE c.COMPST_ISU_CD = $code
            AND TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) > $min_weight
          ORDER BY weight_pct DESC LIMIT $limit""",
       [Param("code", required=True), Param("min_weight", required=True),
        Param("limit", required=True)],
       source="KRX-PDF", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

    # ---------------- 8/19 ⑧ 신규 — 상품명 우선·그룹(계열사)·운용사×테마·펀드 상세 ----------------
    _t("etp_pattern_top_constituents",
       "상품명 패턴에 맞는 국내 ETP(상장중) 중 순자산 상위 $top_etfs 개의 구성종목 상위 $per_etf 개. "
       "운용사($mgmt — 오염 복구값 또는 원시값)로 좁힐 수 있다. 구성 공시가 빈 상품은 행이 없다 → "
       "'구성 공시 없음'을 답변에 명시할 것. 대상: M-19(애플밸류체인), H-08(미래에셋×중국), "
       "H-10(한화그룹주 계열사별 비중), H-20(위클리커버드콜 보유 종목).",
       """WITH t AS (
              SELECT e.pd_itm_no, e.pd_abrv_nm, TRY_CAST(e.pd_net_tamt AS DOUBLE) AS aum
              FROM kr_etp e LEFT JOIN mgmt_resolved m ON e.pd_itm_no = m.pd_itm_no
              WHERE (e.pd_nm ILIKE $pattern ESCAPE '\\' OR e.pd_abrv_nm ILIKE $pattern ESCAPE '\\')
                AND e.drv_listing_status = 'active'
                AND ($mgmt IS NULL OR m.resolved = $mgmt OR e.cu_fund_mgmt_co = $mgmt)
              ORDER BY aum DESC NULLS LAST, e.pd_itm_no LIMIT $top_etfs
          ), c AS (
              SELECT t.pd_itm_no, t.pd_abrv_nm, t.aum, x.COMPST_ISU_NM, x.COMPST_ISU_CD, x.SECUGRP_ID,
                     TRY_CAST(replace(x.COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct,
                     -- 정렬 키에 NULL 을 남기지 않는다(coalesce): 비중이 전부 비어 있는 ETF(차이나전기차 등)에서
                     -- DuckDB 1.5 의 row_number()<=N 최적화가 행을 전부 버리는 현상을 8/19 실측 — 회피책
                     row_number() OVER (PARTITION BY t.pd_itm_no
                                        ORDER BY coalesce(TRY_CAST(replace(x.COMPST_RTO, ',', '') AS DOUBLE), -1) DESC,
                                                 x.COMPST_ISU_NM) AS rn
              FROM t JOIN etf_constituent x ON x.etf_isin = t.pd_itm_no
          )
          SELECT pd_itm_no, pd_abrv_nm AS etf_name, COMPST_ISU_NM, COMPST_ISU_CD, SECUGRP_ID, weight_pct
          FROM c WHERE rn <= $per_etf
          ORDER BY aum DESC NULLS LAST, pd_itm_no, rn""",
       [Param("pattern", required=True), Param("mgmt"),
        Param("top_etfs", required=True), Param("per_etf", required=True)],
       source="KRX-PDF·PREF01N001", key_col="pd_itm_no", as_of=AS_OF_CONSTITUENTS),

    _t("constituent_group_holders",
       "회사명이 $prefix 로 시작하는 국내 상장 종목(그룹 계열사 후보)별 편입 ETF 수와 최대 비중 ETF. "
       "법적 계열 관계 데이터는 없으므로 '회사명 접두 기준 후보'임을 답변에 명시할 것. "
       "대상: M-14(한화그룹 계열사), H-23(SK 계열사별 정리).",
       """WITH w AS (
              SELECT c.COMPST_ISU_NM, c.COMPST_ISU_CD, c.etf_isin, c.etf_name,
                     TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct
              FROM etf_constituent c
              WHERE c.SECUGRP_ID IS NOT NULL AND c.COMPST_ISU_NM ILIKE $prefix ESCAPE '\\'
          ), agg AS (
              SELECT COMPST_ISU_NM, COMPST_ISU_CD, count(DISTINCT etf_isin) AS n_etfs,
                     max(weight_pct) AS max_weight_pct
              FROM w GROUP BY 1, 2
          )
          SELECT agg.COMPST_ISU_NM, agg.COMPST_ISU_CD, agg.n_etfs, agg.max_weight_pct,
                 (SELECT w2.etf_name FROM w w2 WHERE w2.COMPST_ISU_CD = agg.COMPST_ISU_CD
                   ORDER BY coalesce(w2.weight_pct, -1) DESC, w2.etf_isin LIMIT 1) AS top_etf
          FROM agg ORDER BY n_etfs DESC, COMPST_ISU_NM LIMIT $limit""",
       [Param("prefix", required=True), Param("limit", required=True)],
       source="KRX-PDF", key_col="COMPST_ISU_CD", as_of=AS_OF_CONSTITUENTS),

    _t("fund_detail",
       "공모펀드 1종 상세(마스터 단위) — 키(itm_no)로 조회: 운용속성·위험등급·수익률·순자산·판매상태·"
       "벤치마크·투자지역·판매 클래스 수 + 8/27 재배포본 신설(제로인 유형명·자산구성 비율 4종·"
       "최근 분배율·클래스 표기·보수 분해 4종). "
       "대상: M-10(국민성장펀드 — 비정형 서술은 없지만 마스터 필드는 답한다) + 구조·전략 요약 재료.",
       """SELECT itm_no, itm_nm, itm_abrv_nm, or_attr_desc, drv_risk_grade, zrin_fd_ivst_risk_grd_nm,
                 fd_yr1_ern_r, fd_mm3_ern_r, fd_nast_suma, sale_yn, thco_sale_yn, bmrk_nm,
                 fd_ivst_rgn_desc, share_class_count,
                 zrin_btyp_nm, zrin_ptn_nm, zrin_dmst_stk_cmst_rt, zrin_ovrs_stk_cmst_rt,
                 zrin_dmst_bd_cmst_rt, zrin_ovrs_bd_cmst_rt, fd_last_dstb_r, han_clas_nm,
                 or_co_rwrd_r, sale_co_rwrd_r, trusc_rwrd_r, ofwk_trus_rwrd_r
          FROM fund_master WHERE itm_no = $itm_no""",
       [Param("itm_no", required=True)], source="PRFD01N001", key_col="itm_no"),

    _t("constituent_ksq_share",
       "상품명 패턴 ETF별 코스닥(MKT_ID=KSQ) 종목 비중 합계·종목 수 — 비중 높은 순. "
       "대상: H-22(바이오 ETF 중 코스닥 비중 높은 상품).",
       """WITH t AS (
              SELECT etf_isin, etf_name,
                     sum(CASE WHEN MKT_ID = 'KSQ' THEN TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) ELSE 0 END) AS ksq_weight_pct,
                     sum(TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE)) AS total_weight_pct,
                     count(CASE WHEN MKT_ID = 'KSQ' THEN 1 END) AS n_ksq_stocks
              FROM etf_constituent
              WHERE etf_name ILIKE $pattern ESCAPE '\\'
              GROUP BY 1, 2
          )
          SELECT t.etf_isin, coalesce(e.pd_abrv_nm, t.etf_name) AS pd_abrv_nm, t.ksq_weight_pct,
                 t.total_weight_pct, t.n_ksq_stocks, e.pd_net_tamt
          FROM t LEFT JOIN kr_etp e ON e.pd_itm_no = t.etf_isin
          WHERE e.drv_listing_status IS NULL OR e.drv_listing_status = 'active'
          ORDER BY t.ksq_weight_pct DESC, t.etf_isin LIMIT $limit""",
       [Param("pattern", required=True), Param("limit", required=True)],
       source="KRX-PDF·PREF01N001", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

    _t("etp_top_return_common_holdings",
       "수익률(metric: ytd/1y) 상위 $top_n 개 국내 ETF 가 공통으로 담은 종목 — 종목별 보유 ETF 수(상위 N 중)·"
       "평균 비중·보유 ETF 명. 2개 이상이 담은 종목만, 많이 겹치는 순. 대상: H-09.",
       """WITH top AS (
              SELECT pd_itm_no, pd_abrv_nm FROM kr_etp
              WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
              ORDER BY CASE WHEN $metric = 'ytd' THEN TRY_CAST(du_er_ytd AS DOUBLE)
                            ELSE TRY_CAST(du_er_1y AS DOUBLE) END DESC NULLS LAST, pd_itm_no
              LIMIT $top_n
          ), held AS (
              SELECT c.COMPST_ISU_NM, c.COMPST_ISU_CD, t.pd_abrv_nm,
                     TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct
              FROM top t JOIN etf_constituent c ON c.etf_isin = t.pd_itm_no
              WHERE (c.SECUGRP_ID IS NOT NULL OR regexp_matches(c.COMPST_ISU_CD, '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'))
                AND NOT starts_with(c.COMPST_ISU_CD, 'CASH')
                AND NOT regexp_matches(c.COMPST_ISU_CD, '^KR[DZY]')
                AND NOT regexp_matches(coalesce(c.COMPST_ISU_NM, ''), '현금|예금|설정현금액')   -- 현금성은 종목이 아니다
          )
          SELECT COMPST_ISU_NM, COMPST_ISU_CD, count(DISTINCT pd_abrv_nm) AS n_etfs_holding,
                 round(avg(weight_pct), 2) AS avg_weight_pct,
                 string_agg(DISTINCT pd_abrv_nm, ' / ' ORDER BY pd_abrv_nm) AS held_by
          FROM held GROUP BY 1, 2
          HAVING count(DISTINCT pd_abrv_nm) >= 2
          ORDER BY n_etfs_holding DESC, avg_weight_pct DESC NULLS LAST, COMPST_ISU_NM LIMIT $limit""",
       [Param("metric", required=True, enum=("ytd", "1y")), Param("top_n", required=True),
        Param("limit", required=True)],
       source="PREF01N001·KRX-PDF", key_col="COMPST_ISU_CD", as_of=AS_OF_CONSTITUENTS),

    _t("etp_target_maturity_within",
       "만기형(존속기한) 채권 ETF — 상품명의 'YY-MM' 표기(예: 'KODEX 25-11 은행채(AA-이상)액티브')를 만기 연월로 "
       "읽어 $date_from ~ $date_to 사이에 만기가 오는 상장중 ETF 를 만기 순으로. 상품명 표기 규칙에 기댄 파싱임을 "
       "답변에 명시할 것. 대상: H-12.",
       """WITH parsed AS (
              SELECT pd_itm_no, pd_abrv_nm, pd_nm, drv_risk_grade, pd_net_tamt,
                     regexp_extract(coalesce(pd_abrv_nm, pd_nm), '(\\d{2})-(\\d{2})', 1) AS yy,
                     regexp_extract(coalesce(pd_abrv_nm, pd_nm), '(\\d{2})-(\\d{2})', 2) AS mm
              FROM kr_etp
              WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
                AND regexp_matches(coalesce(pd_abrv_nm, pd_nm), '\\d{2}-\\d{2}')
                AND (pd_nm ILIKE '%채%' OR pd_abrv_nm ILIKE '%채%')
          ), dated AS (
              SELECT *, ('20' || yy || '-' || mm || '-01')::DATE AS maturity_month
              FROM parsed WHERE yy <> '' AND mm BETWEEN '01' AND '12'
          )
          SELECT pd_itm_no, pd_abrv_nm, strftime(maturity_month, '%Y-%m') AS maturity_yyyymm,
                 drv_risk_grade, pd_net_tamt
          FROM dated
          WHERE maturity_month >= TRY_CAST($date_from AS DATE)::DATE
            AND maturity_month <= TRY_CAST($date_to AS DATE)::DATE
          ORDER BY maturity_month, pd_itm_no LIMIT $limit""",
       [Param("date_from", required=True), Param("date_to", required=True), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("reit_constituents",
       "ETF 구성종목에 등장하는 개별 상장 리츠(SECUGRP_ID=RT) 목록 — 편입 ETF 수 많은 순. "
       "대상: H-07(리츠 ETF vs 개별 상장 리츠).",
       """SELECT COMPST_ISU_NM, COMPST_ISU_CD, count(DISTINCT etf_isin) AS n_etfs
          FROM etf_constituent WHERE SECUGRP_ID = 'RT'
          GROUP BY 1, 2 ORDER BY n_etfs DESC, COMPST_ISU_NM LIMIT $limit""",
       [Param("limit", required=True)], source="KRX-PDF", key_col="COMPST_ISU_CD",
       as_of=AS_OF_CONSTITUENTS),

    _t("mgmt_top_share",
       "운용사별 국내 ETF 수·순자산 합계·점유율(%) 상위 — 오염 복구값(mgmt_resolved) 기준. "
       "점유율은 상장중 ETF 순자산 합계 대비이며 템플릿이 미리 계산한다(생성기가 계산하면 사후 대조에 걸림). "
       "대상: M-09, H-29.",
       """WITH by_mgmt AS (
              SELECT m.resolved AS mgmt_co, count(*) AS n_etf,
                     sum(TRY_CAST(e.pd_net_tamt AS DOUBLE)) AS total_aum
              FROM kr_etp e JOIN mgmt_resolved m ON e.pd_itm_no = m.pd_itm_no
              WHERE e.drv_instrument_type = 'ETF' AND e.drv_listing_status = 'active'
                AND m.resolved IS NOT NULL
              GROUP BY 1
          )
          SELECT mgmt_co, n_etf, total_aum,
                 round(total_aum / sum(total_aum) OVER () * 100, 1) AS share_pct
          FROM by_mgmt ORDER BY total_aum DESC NULLS LAST LIMIT $limit""",
       [Param("limit", required=True)], source="PREF01N001(복구)"),

    # ---------------- 커버리지 (Validation 게이트 ⑤ 의 분모) ----------------
    _t("coverage_check",
       "필드 커버리지 — partial 판정·'값 보유분 기준' 문구의 분모. field 는 화이트리스트 enum.",
       None,   # SQL 은 FIELD_MAP 에서 조립(식별자는 파라미터 바인딩 불가 — enum 화이트리스트로 안전)
       [Param("field", required=True, enum=(
           "kr_etp.cu_charge_rt", "kr_etp.du_er_ytd", "kr_etp.du_er_1y",
           "kr_etp.drv_risk_grade", "kr_etp.cu_base_index",
           "fund_master.fd_yr1_ern_r", "fund_master.drv_risk_grade",
           "global_etf.cu_strtegy", "kr_bond.drv_crd_grd_rank", "kr_bond.SRFC_IRT"))],
       source=""),
]}


# coverage_check 전용 — enum 값별 (테이블, 컬럼, 기본 모집단 필터)
_COVERAGE_BASE_FILTER = {
    "kr_etp": "drv_instrument_type = 'ETF' AND drv_listing_status = 'active'",
    "fund_master": "1=1",
    "global_etf": "1=1",
    "kr_bond": "1=1",
}


def _run_coverage(con, field):
    table, col = field.split(".", 1)
    base = _COVERAGE_BASE_FILTER[table]
    row = con.execute(
        f"SELECT count(*) AS total, count({col}) AS non_null FROM {table} WHERE {base}"
    ).fetchone()
    total, non_null = row
    pct = round(non_null / total * 100, 1) if total else 0.0
    return [{"field": field, "total": total, "non_null": non_null, "coverage_pct": pct}]


@dataclass
class TemplateResult:
    template_id: str
    rows: list
    evidences: list = field(default_factory=list)


def validate_params(template_id, params=None):
    """플랜 검증 공유 지점 — 실행 없이 (Template, 정규화된 파라미터) 반환.

    Router Stage B(LLM 플랜)와 run_template 이 같은 검증을 쓴다: 모르는
    템플릿 KeyError, 모르는 파라미터·필수 누락·enum 위반 ValueError.
    선택 파라미터는 None 으로 채워 돌려준다.
    """
    if template_id not in TEMPLATES:
        raise KeyError(f"알 수 없는 템플릿: {template_id!r} (가능: {sorted(TEMPLATES)})")
    t = TEMPLATES[template_id]
    params = dict(params or {})
    known = {p.name for p in t.params}
    unknown = set(params) - known
    if unknown:
        raise ValueError(f"[{t.id}] 모르는 파라미터: {sorted(unknown)}")
    for p in t.params:
        if p.required and params.get(p.name) is None:
            raise ValueError(f"[{t.id}] 필수 파라미터 누락: {p.name}")
        if p.enum and params.get(p.name) is not None and params[p.name] not in p.enum:
            raise ValueError(f"[{t.id}] {p.name} 는 {p.enum} 중 하나여야 함: {params[p.name]!r}")
        params.setdefault(p.name, None)
    return t, params


# 원화 금액 열 — 사람이 읽는 환산 표기(억원·조원)를 같은 행에 붙인다(8/19).
# 생성기(HCX)가 원 단위 큰 수를 스스로 환산하다 단위를 틀리는 일(346,687,108,988원 → "약 346조",
# M-03 실측)을 막는다: 환산값을 근거에 넣어 두면 그대로 옮겨 쓰고, 사후 대조도 그 숫자를 허용한다.
KRW_AMOUNT_COLS = ("pd_net_tamt", "du_last_aum", "total_aum", "fd_nast_suma")


def krw_readable(value):
    """원 단위 숫자 → '28.4조원' / '3,467억원' / '1,235만원' / '900원'. 숫자가 아니면 None."""
    try:
        v = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if v != v:                                            # NaN
        return None
    if abs(v) >= 1e12:
        return f"{v / 1e12:,.1f}조원"
    if abs(v) >= 1e8:
        return f"{v / 1e8:,.0f}억원"
    if abs(v) >= 1e4:
        return f"{v / 1e4:,.0f}만원"
    return f"{v:,.0f}원"


def _attach_readable_amounts(rows, template):
    """원화 금액 열에 환산 표기 첨부 — 해외ETF(PREF02N001) 결과는 통화(USD 등)가 원이 아니므로 붙이지 않는다."""
    if (template.source or "").startswith("PREF02N001"):
        return rows
    for row in rows:
        for col in KRW_AMOUNT_COLS:
            if col in row and row.get(col) is not None:
                readable = krw_readable(row[col])
                if readable is not None:
                    row[col + "_krw"] = readable
    return rows


def run_template(con, template_id, params=None):
    """템플릿 실행 — validate_params 검증 후 실행. 결과 행 + Evidence 목록."""
    t, params = validate_params(template_id, params)

    if t.id == "coverage_check":
        rows = _run_coverage(con, params["field"])
    else:
        cur = con.execute(t.sql, params)
        cols = [d[0] for d in cur.description]
        rows = _attach_readable_amounts([dict(zip(cols, r)) for r in cur.fetchall()], t)

    evidences = []
    for row in rows[:MAX_EVIDENCE_ROWS]:
        key = str(row.get(t.key_col, "")) if t.key_col else ""
        fields = {k: v for k, v in row.items() if v is not None}
        evidences.append(Evidence(source=t.source or "storage", source_id=key,
                                  channel="sql", as_of=t.as_of, fields=fields))
    if len(rows) > MAX_EVIDENCE_ROWS and evidences:
        evidences[-1] = Evidence(source=evidences[-1].source, source_id=evidences[-1].source_id,
                                 channel="sql", as_of=t.as_of, fields=evidences[-1].fields,
                                 note=f"외 {len(rows) - MAX_EVIDENCE_ROWS:,}건(총 {len(rows):,}건)")
    return TemplateResult(t.id, rows, evidences)
