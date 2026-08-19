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

from pipeline.evidence import AS_OF_CONSTITUENTS, AS_OF_MASTER, Evidence  # noqa: E402

MAX_EVIDENCE_ROWS = 10   # 근거는 상위 N행까지 개별 생성, 나머지는 총계 주석으로


def like_param(text):
    """사용자 텍스트 → 안전한 ILIKE 패턴(%text%). %·_ 이스케이프."""
    t = str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


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
       "대상: L-01/03/05, H-26. 매수가능 판정 기준(§8.4 미확정)은 답변에 명시할 것.",
       """SELECT PD_NO, PD_NM, STD_PD_MCLS_NM, CURR_CD, drv_crd_grd_norm, drv_crd_grd_rank,
                 SRFC_IRT, MAT_DT, drv_maturity_status, drv_is_buyable
          FROM kr_bond
          WHERE ($currency IS NULL OR CURR_CD = $currency)
            AND ($max_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) <= $max_rating_rank)
            AND ($maturity_status IS NULL OR drv_maturity_status = $maturity_status)
            AND ($buyable_only IS NULL OR upper(coalesce(drv_is_buyable,'')) IN ('Y','TRUE','1'))
            AND ($min_coupon IS NULL OR TRY_CAST(SRFC_IRT AS DOUBLE) >= $min_coupon)
            AND ($max_coupon IS NULL OR TRY_CAST(SRFC_IRT AS DOUBLE) < $max_coupon)
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)
          ORDER BY TRY_CAST(drv_crd_grd_rank AS INT) NULLS LAST,
                   TRY_CAST(SRFC_IRT AS DOUBLE) DESC NULLS LAST, PD_NO
          LIMIT $limit""",
       [Param("currency"), Param("max_rating_rank"), Param("maturity_status"),
        Param("buyable_only"), Param("min_coupon"), Param("max_coupon"),
        Param("bond_class"), Param("limit", required=True)],
       source="PRBD01N001", key_col="PD_NO"),

    _t("bond_count",
       "국내채권 조건 카운트 — bond_filter 와 동일 필터의 건수. 대상: L-02/05.",
       """SELECT count(*) AS n FROM kr_bond
          WHERE ($currency IS NULL OR CURR_CD = $currency)
            AND ($max_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) <= $max_rating_rank)
            AND ($min_rating_rank IS NULL OR TRY_CAST(drv_crd_grd_rank AS INT) >= $min_rating_rank)
            AND ($maturity_status IS NULL OR drv_maturity_status = $maturity_status)
            AND ($buyable_only IS NULL OR upper(coalesce(drv_is_buyable,'')) IN ('Y','TRUE','1'))
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)""",
       [Param("currency"), Param("max_rating_rank"), Param("min_rating_rank"),
        Param("maturity_status"), Param("buyable_only"), Param("bond_class")],
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
       """SELECT PD_NO, PD_NM, STD_PD_MCLS_NM, CURR_CD, MAT_DT,
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
       "활성 채권 만기일 내림차순(잔존만기 긴 순) — 영구채(만기 없음) 제외, 대분류 필터 "
       "선택. 잔존 일수는 답변 시 time_policy 로 재계산해 병기. 대상: L-04(국고채→국공채).",
       """SELECT PD_NO, PD_NM, STD_PD_MCLS_NM, MAT_DT, drv_crd_grd_norm, SRFC_IRT
          FROM kr_bond
          WHERE drv_maturity_status = 'active'
            AND upper(coalesce(drv_is_perpetual,'')) NOT IN ('Y','TRUE','1')
            AND ($bond_class IS NULL OR STD_PD_MCLS_NM = $bond_class)
          ORDER BY replace(coalesce(MAT_DT,''),'-','') DESC, PD_NO LIMIT $limit""",
       [Param("bond_class"), Param("limit", required=True)],
       source="PRBD01N001", key_col="PD_NO"),

    # ---------------- 국내 ETP (L-09~16, L-26, L-28, L-30, M-15/17/18, H-16/24/28/30) ----------------
    _t("etp_detail",
       "국내 ETP 1종 상세 — 키(pd_itm_no)로 조회. grounding 이 이름→키를 먼저 푼다. "
       "대상: L-09/10/28, H-30.",
       """SELECT pd_itm_no, pd_nm, pd_abrv_nm, drv_instrument_type, drv_listing_status,
                 cu_fund_mgmt_co, cu_base_index, cu_charge_rt, drv_risk_grade,
                 pd_net_tamt, du_er_1y, du_er_ytd, pd_lstg_dt, drv_curr_cd
          FROM kr_etp WHERE pd_itm_no = $pd_itm_no""",
       [Param("pd_itm_no", required=True)], source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_top_aum",
       "국내 ETP 순자산총액 상위 — ETF/ETN 혼재(30.7%) 함정 방어를 위해 유형 필수. "
       "대상: L-11(KODEX 200 1위 검증 완료), M-02 후단.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_net_tamt, cu_fund_mgmt_co FROM kr_etp
          WHERE drv_instrument_type = $instrument_type AND drv_listing_status = 'active'
          ORDER BY TRY_CAST(pd_net_tamt AS DOUBLE) DESC NULLS LAST LIMIT $limit""",
       [Param("instrument_type", required=True, enum=("ETF", "ETN")),
        Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_top_return",
       "국내 ETP 수익률 상위 — metric: ytd(=2026-01-01~07-11 규칙)/1y. 대상: L-14, M-15, H-09.",
       """SELECT pd_itm_no, pd_abrv_nm, du_er_ytd, du_er_1y, drv_risk_grade FROM kr_etp
          WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
            AND ($min_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_risk)
            AND ($max_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_risk)
          ORDER BY CASE WHEN $metric = 'ytd' THEN TRY_CAST(du_er_ytd AS DOUBLE)
                        ELSE TRY_CAST(du_er_1y AS DOUBLE) END DESC NULLS LAST
          LIMIT $limit""",
       [Param("metric", required=True, enum=("ytd", "1y")),
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
            AND ($instrument_type IS NULL OR drv_instrument_type = $instrument_type)
            AND ($status IS NULL OR drv_listing_status = $status)
          ORDER BY pd_itm_no LIMIT $limit""",
       [Param("pattern", required=True), Param("instrument_type", enum=("ETF", "ETN")),
        Param("status"), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_listed_between",
       "상장일 구간 필터(포함) — 기준일(7/11) 이후는 데이터 밖임을 답변에 명시. "
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
       "국내 ETP 유형·상태별 카운트. 대상: L-13.",
       """SELECT drv_instrument_type, drv_listing_status, count(*) AS n FROM kr_etp
          GROUP BY 1, 2 ORDER BY 1, 2""",
       [], source="PREF01N001"),

    _t("etp_low_fee",
       "총보수 상한 필터(값 보유분만) — 커버리지(실질결측 87.5%)와 0의 의미 미확정을 "
       "답변에 반드시 명시(partial). 대상: L-26, H-03/30.",
       """SELECT pd_itm_no, pd_abrv_nm, cu_charge_rt FROM kr_etp
          WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
            AND TRY_CAST(cu_charge_rt AS DOUBLE) IS NOT NULL
            AND TRY_CAST(cu_charge_rt AS DOUBLE) <= $max_fee
          ORDER BY TRY_CAST(cu_charge_rt AS DOUBLE), pd_itm_no LIMIT $limit""",
       [Param("max_fee", required=True), Param("limit", required=True)],
       source="PREF01N001", key_col="pd_itm_no"),

    _t("etp_currency_dist",
       "국내 ETP 거래통화 분포. 대상: L-30.",
       "SELECT drv_curr_cd, count(*) AS n FROM kr_etp GROUP BY 1 ORDER BY n DESC",
       [], source="PREF01N001"),

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
       "해외ETF 필터 — 지역·인버스·거래통화. 위험등급 컬럼은 원천에 없음(요청 시 "
       "Validation 이 refuse — T-13/M-29). 대상: L-18/19/20.",
       """SELECT pd_itm_no, pd_abrv_nm, pd_nm, wu_inv_rgn, pd_trd_ccy FROM global_etf
          WHERE ($region_pattern IS NULL OR wu_inv_rgn ILIKE $region_pattern ESCAPE '\\')
            AND ($inverse_only IS NULL OR upper(coalesce(drv_is_inverse,'')) IN ('Y','TRUE','1'))
            AND ($ccy IS NULL OR pd_trd_ccy = $ccy)
            AND ($exclude_ccy IS NULL OR pd_trd_ccy <> $exclude_ccy)
          ORDER BY pd_itm_no LIMIT $limit""",
       [Param("region_pattern"), Param("inverse_only"), Param("ccy"),
        Param("exclude_ccy"), Param("limit", required=True)],
       source="PREF02N001", key_col="pd_itm_no"),

    _t("global_etf_count",
       "해외ETF 유형별 카운트(ETF 5,587/ETN 59 구분). 대상: L-17.",
       "SELECT drv_instrument_type, count(*) AS n FROM global_etf GROUP BY 1 ORDER BY n DESC",
       [], source="PREF02N001"),

    _t("global_ccy_dist",
       "해외ETF 거래통화 분포. 대상: L-20.",
       "SELECT pd_trd_ccy, count(*) AS n FROM global_etf GROUP BY 1 ORDER BY n DESC",
       [], source="PREF02N001"),

    # ---------------- 공모펀드 (L-21~25, L-29 는 사전 기반) ----------------
    _t("fund_counts",
       "공모펀드 상품(마스터)·판매 클래스 수 — 95,619행≠상품 수 함정 방어. 대상: L-21.",
       """SELECT (SELECT count(*) FROM fund_master) AS products,
                 (SELECT count(*) FROM fund_class)  AS share_classes""",
       [], source="PRFD01N001"),

    _t("fund_filter",
       "공모펀드 필터 — 현재 판매상태(sale_yn)와 당사판매여부(thco_sale_yn)를 구분하고 "
       "운용속성·위험등급을 함께 적용. 마스터(상품) 단위. 대상: L-22/23.",
       """SELECT itm_no, itm_nm, or_attr_desc, drv_risk_grade, sale_yn, thco_sale_yn,
                 share_class_count
          FROM fund_master
          WHERE ($on_sale_only IS NULL OR replace(trim(coalesce(sale_yn,'')), ' ', '') = '판매중')
            AND ($thco_sale_only IS NULL OR upper(trim(coalesce(thco_sale_yn,'')))
                                             IN ('Y','TRUE','1'))
            AND ($attr_pattern IS NULL OR or_attr_desc ILIKE $attr_pattern ESCAPE '\\')
            AND ($min_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) >= $min_risk)
            AND ($max_risk IS NULL OR TRY_CAST(drv_risk_grade AS INT) <= $max_risk)
          ORDER BY itm_no LIMIT $limit""",
       [Param("on_sale_only"), Param("thco_sale_only"), Param("attr_pattern"), Param("min_risk"),
        Param("max_risk"), Param("limit", required=True)],
       source="PRFD01N001", key_col="itm_no"),

    _t("fund_top_return_1y",
       "공모펀드 1년 수익률 상위(값 보유분만) — 커버리지는 coverage_check 로 병행 조회해 "
       "답변에 명시. 대상: L-24.",
       """SELECT itm_no, itm_nm, fd_yr1_ern_r, drv_risk_grade, sale_yn, thco_sale_yn
          FROM fund_master
          WHERE TRY_CAST(fd_yr1_ern_r AS DOUBLE) IS NOT NULL
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
       "명시 필수. 대상: M-01/02/16/21, H-06/27.",
       """SELECT c.etf_isin, c.etf_name, c.COMPST_ISU_NM,
                 TRY_CAST(replace(c.COMPST_RTO, ',', '') AS DOUBLE) AS weight_pct,
                 e.pd_net_tamt, e.drv_risk_grade
          FROM etf_constituent c LEFT JOIN kr_etp e ON c.etf_isin = e.pd_itm_no
          WHERE c.COMPST_ISU_CD = $code
          ORDER BY weight_pct DESC NULLS LAST, c.etf_isin LIMIT $limit""",
       [Param("code", required=True), Param("limit", required=True)],
       source="KRX-PDF", key_col="etf_isin", as_of=AS_OF_CONSTITUENTS),

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
            AND TRY_CAST(e.cu_charge_rt AS DOUBLE) IS NOT NULL
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

    _t("mgmt_top_share",
       "운용사별 국내 ETF 수·순자산 합계 상위 — 오염 복구값(mgmt_resolved) 기준. "
       "대상: M-09, H-29.",
       """SELECT m.resolved AS mgmt_co, count(*) AS n_etf,
                 sum(TRY_CAST(e.pd_net_tamt AS DOUBLE)) AS total_aum
          FROM kr_etp e JOIN mgmt_resolved m ON e.pd_itm_no = m.pd_itm_no
          WHERE e.drv_instrument_type = 'ETF' AND e.drv_listing_status = 'active'
            AND m.resolved IS NOT NULL
          GROUP BY 1 ORDER BY total_aum DESC NULLS LAST LIMIT $limit""",
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


def run_template(con, template_id, params=None):
    """템플릿 실행 — validate_params 검증 후 실행. 결과 행 + Evidence 목록."""
    t, params = validate_params(template_id, params)

    if t.id == "coverage_check":
        rows = _run_coverage(con, params["field"])
    else:
        cur = con.execute(t.sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

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
