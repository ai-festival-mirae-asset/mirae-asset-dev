# -*- coding: utf-8 -*-
"""
금융상품 4종 마스터 전처리 파이프라인 (2차 — 8/5 dev-kyung 교차검증 정정분 반영)

입력 : datasets/*.xlsx (원본 datarows 4종 + schema 4종)
       원본은 참가자 전원이 보유하므로 저장소에 커밋하지 않는다(.gitignore).
       repo 루트의 datasets/ 에 두거나, 환경변수 MIRAE_DATASETS 로 경로를 지정한다.
출력 : preprocessing/processed/<테이블ID>_<상품군>_processed.csv (전처리 완료 데이터)
       preprocessing/processed/quarantine_PRFD01N001.csv       (공모펀드 격리 행)
       preprocessing/processed/quarantine_PREF01N001.csv       (국내ETP 격리 행)
       preprocessing/processed/preprocessing_report.csv        (규칙별 영향 행수)

원칙
  1. 원본 컬럼은 삭제·변형을 최소화하고(전량 무정보 컬럼 제외), 해석이 필요한
     정규화 값은 drv_* 파생 컬럼으로 추가한다. 원본 값 자체는 datasets/에 보존된다.
  2. 결측은 메꾸지 않고 "판별"한다. 센티널(무의미 값)을 NULL로 통일해
     IS NULL 필터가 작동하게 만드는 것이 목적이다.
  3. 모든 규칙은 규칙ID와 영향 행수를 리포트로 남긴다 (preprocessing_report.csv).
  4. 재실행 가능(멱등): 같은 입력이면 항상 같은 출력.
     (검증: preprocessing/verify_determinism.py — N회 실행 SHA-256 비교,
      불일치 시 셀 diff·입력 해시 증거 보존. 8/7 간헐 불일치 1회 관측 — 원인 조사 중)

구조 주의: 이 모듈은 테스트(tests/test_preprocess.py)에서 순수 함수를 import 한다.
           따라서 import 시점에는 부작용(폴더 생성·존재 검사·sys.exit)이 없어야 하며,
           해당 검사는 전부 main() 실행 경로에서만 수행한다.

근거 문서 : preprocessing/PREPROCESSING_METHOD.md (6장 = 8/5 교차검증 반영 명세)
            external_data/COLLECTION_SUMMARY.md
            (팀 공동 가이드 PROJECT_GUIDE.md는 main 브랜치에 있다)
실행      : python preprocessing/preprocess.py  (repo 루트에서, 또는 preprocessing/ 안에서 python preprocess.py)
"""
import os
import re
import sys
import io

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))   # preprocessing/
ROOT = os.path.dirname(HERE)                        # repo 루트

# --- .env 파일 지원 -------------------------------------------------------
# 저장소 최상위의 .env 를 읽어 환경변수로 올린다(없으면 아무 일도 안 함).
# 운영체제 환경변수가 이미 있으면 그쪽이 우선이다.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config.env_loader import load_env  # noqa: E402
load_env()
# --------------------------------------------------------------------------
# 원본 xlsx 위치: 기본은 repo 루트의 datasets/ (커밋 대상 아님).
# 데이터를 저장소 밖에 두는 경우 MIRAE_DATASETS 환경변수로 지정한다.
DS = os.environ.get("MIRAE_DATASETS") or os.path.join(ROOT, "datasets")
OUT = os.path.join(HERE, "processed")

AS_OF = "2026-07-11"          # 데이터 스냅샷 기준일 (ISO)
AS_OF_COMPACT = "20260711"    # 동일 기준일 (YYYYMMDD — 원본 날짜 토큰 비교용)

TABLES = {
    "PRBD01N001": ("국내채권", "PRBD01N001_국내채권마스터_20260711_datarows.xlsx", "PRBD01N001_국내채권마스터_schema.xlsx"),
    "PREF01N001": ("국내ETF", "PREF01N001_국내ETF마스터_20260711_datarows.xlsx", "PREF01N001_국내ETF마스터_schema.xlsx"),
    "PREF02N001": ("해외ETF", "PREF02N001_해외ETF마스터_20260711_datarows.xlsx", "PREF02N001_해외ETF마스터_schema.xlsx"),
    "PRFD01N001": ("공모펀드", "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx", "PRFD01N001_공모펀드마스터_schema.xlsx"),
}

# 출력 파일명용 상품군 영문 슬러그 (표시용 한글명은 리포트 내용에 그대로 유지)
SLUG = {"국내채권": "kr_bond", "국내ETF": "kr_etf",
        "해외ETF": "global_etf", "공모펀드": "public_fund"}

# 신용등급 서열 (external_data/dictionaries/credit_rating.csv): AAA=1(최상) ~ D=20
CRD_RANK = {g: i + 1 for i, g in enumerate(
    ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
     "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"])}

# 해외ETF cu_base_index 센티널 (Lipper 원천, 실질결측 ~48%)
GL_INDEX_SENTINELS = {
    "Index is not provided by Management Company",
    "Index is not available on Lipper Database",
}

# 국내ETF 기간수익률 컬럼 (R27 대상 — du_er_* 계열, 실제 존재 컬럼만 사용)
KR_ETF_ER_COLS = ["du_er_1d", "du_er_1m", "du_er_3m", "du_er_6m", "du_er_1y", "du_er_ytd"]

# 해외ETF 핵심 필드 (R29 희소 행 판정 기준 — dev-kyung prepare_data.py 와 동일 5종)
GL_CORE_COLS = ["pd_isin_cd", "cu_fund_mgmt_co", "wu_inv_ast_type", "wu_inv_rgn", "du_clpr"]

report = []  # (테이블, 규칙ID, 컬럼, 영향행수, 처리, 비고)


def log_rule(table, rule_id, col, n, action, note=""):
    report.append({"테이블": table, "규칙ID": rule_id, "컬럼": col,
                   "영향행수": int(n), "처리": action, "비고": note})


def load_schema_types(schema_file):
    """schema xlsx의 Sheet1_Schema에서 컬럼별 선언 타입을 읽는다."""
    raw = pd.read_excel(os.path.join(DS, schema_file), sheet_name="Sheet1_Schema", dtype=str)
    raw.columns = ["컬럼명", "PK", "타입", "한글명", "예시"]
    raw = raw[raw["컬럼명"] != "컬럼명"]
    return dict(zip(raw["컬럼명"].str.strip(), raw["타입"].str.strip()))


def common_clean(df, table):
    """R1~R3: 전 테이블 공통 — trim, 공백/빈문자열→NULL, 'NULL' 문자열→NULL"""
    for col in df.columns:
        if df[col].dtype != object:
            continue
        s = df[col]
        stripped = s.str.strip()
        n_trim = int((s.fillna("") != stripped.fillna("")).sum())
        if n_trim:
            log_rule(table, "R1", col, n_trim, "앞뒤 공백 제거", "고정폭 패딩 존재 컬럼")
        n_blank = int((stripped == "").sum())
        if n_blank:
            log_rule(table, "R2", col, n_blank, "공백/빈 문자열 → NULL")
        n_nullstr = int((stripped == "NULL").sum())
        if n_nullstr:
            log_rule(table, "R3", col, n_nullstr, "문자열 'NULL' → NULL")
        df[col] = stripped.replace({"": None, "NULL": None})
    return df


def cast_numeric(df, types, table):
    """R4: 스키마 선언 타입(numeric/double/bigint) 컬럼을 숫자로 캐스팅.
    문자열로 들어온 숫자('13675.00')를 계산 가능한 값으로 만든다."""
    for col, t in types.items():
        if col not in df.columns or not t:
            continue
        if any(k in t for k in ("numeric", "double", "bigint")):
            before = df[col].notna().sum()
            casted = pd.to_numeric(df[col], errors="coerce")
            n_fail = int(before - casted.notna().sum())
            if n_fail:
                log_rule(table, "R4", col, n_fail, "숫자 캐스팅 실패 → NULL", "원본이 비숫자 문자열")
            df[col] = casted
    return df


def yyyymmdd_to_iso(series, table, col, rule_id="R5"):
    """숫자/문자 YYYYMMDD → ISO 날짜 문자열. 0·비정상 → NULL."""
    def conv(v):
        if pd.isna(v):
            return None
        s = str(v).strip()
        if s.endswith(".0"):
            s = s[:-2]
        if not re.fullmatch(r"\d{8}", s) or s == "00000000":
            return "__BAD__"
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    # pandas 3의 문자열 dtype 추론은 None을 NaN으로 다시 승격할 수 있다.
    # 날짜 파생 컬럼은 이후 DuckDB NULL로 적재되어야 하므로 object dtype을
    # 명시해 Python None을 보존한다.
    out = pd.Series((conv(v) for v in series), index=series.index, dtype=object)
    n_bad = int((out == "__BAD__").sum())
    if n_bad:
        log_rule(table, rule_id, col, n_bad, "날짜 파싱 불가(0 등) → NULL")
    n_ok = int(out.notna().sum() - n_bad)
    log_rule(table, rule_id, col, n_ok, "YYYYMMDD → ISO 날짜(YYYY-MM-DD)")
    out.loc[out == "__BAD__"] = None
    return out


def drop_all_null_cols(df, cols, table, rule_id, note):
    """전량 무정보 컬럼 제거 — 반드시 런타임에 전량 결측을 재확인한 뒤 제거한다."""
    dropped = []
    for c in cols:
        if c in df.columns and df[c].isna().all():
            df = df.drop(columns=[c])
            dropped.append(c)
    if dropped:
        log_rule(table, rule_id, ",".join(dropped), len(df), "전량 결측 컬럼 제거", note)
    return df


# ──────────────────── 순수 판정 함수 (tests/test_preprocess.py 대상) ────────────────────
def norm_grd(v):
    """R9: 신용등급 끝자리 '0'(무부호·플랫 표기) 제거 — 'AA0'→'AA'.
    관측 분포와 신평 3사 공식 기호체계로 확인한 표기 정규화다."""
    if pd.isna(v):
        return None
    m = re.fullmatch(r"(AAA|AA|A|BBB|BB|B|CCC|CC|C|D)0", v)
    return m.group(1) if m else v


def evco(v):
    """R10: 평가사별 병기 등급('AAA, AA+') → (개수, 최저등급, 최저 rank).
    스플릿(평가사 불일치) 시 보수적으로 최저등급을 채택하는 실무 관행."""
    if pd.isna(v):
        return (None, None, None)
    parts = [norm_grd(p.strip()) for p in v.split(",") if p.strip()]
    ranks = [CRD_RANK[p] for p in parts if p in CRD_RANK]
    if not ranks:
        return (len(parts), None, None)
    worst = max(ranks)
    inv = {r: g for g, r in CRD_RANK.items()}
    return (len(parts), inv[worst], worst)


def maturity_status(mat_iso, as_of=AS_OF):
    """R8(8/5 정정): 만기 상태 4-상태 판정 — 날짜 파싱 성공 행만 만기 비교.

    - None/NaN(파싱 불가·결측)   → 'unknown'  (기존 버그: 이런 행이 만기도래로 오포함)
    - ISO 날짜 < AS_OF           → 'matured'
    - ISO 날짜 == AS_OF          → 'matures_on_snapshot'
    - ISO 날짜 > AS_OF           → 'active'   (영구채 센티널 9999-12-31 포함)
    ISO 문자열은 사전순 = 시간순이므로 문자열 비교로 안전하다
    (9999-12-31은 pandas Timestamp 범위 밖이라 오히려 문자열 비교가 안전).
    """
    if mat_iso is None or pd.isna(mat_iso):
        return "unknown"
    if mat_iso < as_of:
        return "matured"
    if mat_iso == as_of:
        return "matures_on_snapshot"
    return "active"


def kr_etp_corrupt_mask(df):
    """R24: 국내ETP 손상 행 도메인 규칙 탐지 (행 번호 하드코딩 금지 — 원본 갱신에도 동작).

    실측 근거(dev-kyung 교차검증): Excel 1,155행(단일 행)은 pd_itm_no='KR',
    pd_nm='.', 73컬럼 중 57개 공백인 손상 레코드이며, 같은 파일 Excel 299행
    (pd_itm_no='KR70193M0005')의 손상된 중복이므로 격리해도 상품 유실이 없다.
    탐지 규칙:
      - pd_itm_no 가 12자리 [A-Z0-9] 형식(표준 ISIN 형식)을 위반, 또는
      - 상품명 pd_nm 이 '.' (자리표시 오염)
    """
    bad_key = ~df["pd_itm_no"].fillna("").str.fullmatch(r"[A-Z0-9]{12}")
    bad_name = df["pd_nm"].fillna("") == "."
    return bad_key | bad_name


def kr_instrument_type(v):
    """R25(국내): pd_grp_no → 상품유형(ETF/ETN) 정규화.

    실측 근거: 원본 pd_grp_no 값 분포는 {'ETF': 1,202, 'ETN': 532} 두 값뿐이다
    (손상 행 격리 후 ETF 1,201 + ETN 532 = 1,733). dev-kyung prepare_data.py 도
    같은 컬럼을 instrument_type 으로 승격했다. 두 값 외 미지의 값은 오염으로
    간주해 NULL을 반환한다(조용히 통과시키지 않고 리포트에서 드러나게).
    """
    if pd.isna(v):
        return None
    u = str(v).strip().upper()
    return u if u in {"ETF", "ETN"} else None


def listing_status(pd_lste_dt, pd_tr_yn, as_of_compact=AS_OF_COMPACT):
    """R26(국내): 상장 상태 판정 — delisted / suspended / active.

    실측 근거:
      - pd_lste_dt(상장폐지/거래종료일)는 YYYYMMDD 문자열이며 분포는
        '99991231'(추출일 시점 미종료 센티널) 또는 과거 날짜뿐 — AS_OF 이후의
        미래 종료예정일은 0건이므로 이 값으로 만기 예정을 판단하면 안 된다.
      - pd_tr_yn 값 분포는 {'0': 1,661, '1': 72, NULL: 1}. 스키마 설명상
        "거래정지 여부"로 1=거래정지다. 1을 '거래 가능'으로 읽으면 의미가 뒤집힌다.
    판정 순서: 종료(delisted)가 정지(suspended)보다 우선 — 종료 상품 다수가
    정지 플래그도 1이므로, 종료를 먼저 확정해야 이중 계상이 없다.
    """
    if pd_lste_dt is not None and not pd.isna(pd_lste_dt):
        s = str(pd_lste_dt).strip()
        if s.endswith(".0"):          # 숫자 캐스팅 오염 방어 (원본은 text 선언)
            s = s[:-2]
        if re.fullmatch(r"\d{8}", s) and s not in ("00000000", "99991231") and s < as_of_compact:
            return "delisted"
    if pd_tr_yn is not None and not pd.isna(pd_tr_yn) and str(pd_tr_yn).strip() == "1":
        return "suspended"
    return "active"


def iso_lag_days(series, as_of=AS_OF, fmt="%Y-%m-%d"):
    """R30: 필드 신선도 — AS_OF 대비 기준일의 지연일수(Int64).

    파일 추출일(07-11)과 필드 실제 기준일이 다르므로(국내ETP 일간 ~06-15,
    해외 06-14~16, 채권 표준정보 중앙값 137일) 근거 표기는 필드별 기준일을
    써야 한다. fmt: ISO('%Y-%m-%d') 또는 compact YYYYMMDD('%Y%m%d').
    파싱 불가·결측은 NULL.
    """
    # pandas 3의 StringDtype과 pd.NA도 안전하게 다루도록 문자열 정규화를
    # dtype 분기 없이 수행한다. 결측은 그대로 NaT가 된다.
    cleaned = series.astype("string").str.strip()
    base = pd.to_datetime(cleaned, format=fmt, errors="coerce")
    return (pd.Timestamp(as_of) - base).dt.days.astype("Int64")


# ──────────────────────────────── 국내채권 ────────────────────────────────
def process_bond():
    tid, name = "PRBD01N001", "국내채권"
    df = pd.read_excel(os.path.join(DS, TABLES[tid][1]), dtype=str)
    types = load_schema_types(TABLES[tid][2])
    df = common_clean(df, name)

    # R5: double로 저장된 날짜 4종 → ISO 날짜 (숫자 캐스팅 전에 처리해야 안전)
    for col in ["ISU_DT", "MAT_DT", "CRD_GRD_DT", "PD_STD_INFO_UPDATE"]:
        df[col] = yyyymmdd_to_iso(df[col], name, col)
        types.pop(col, None)  # 날짜로 확정했으므로 숫자 캐스팅 대상에서 제외
    df = cast_numeric(df, types, name)

    # R6: 통화 센티널
    n = int((df["CURR_CD"] == "000").sum())
    if n:
        log_rule(name, "R6", "CURR_CD", n, "센티널 '000' → NULL")
        df["CURR_CD"] = df["CURR_CD"].replace({"000": None})

    # R7: 영구채 플래그 (MAT_DT=9999-12-31)
    df["drv_is_perpetual"] = (df["MAT_DT"] == "9999-12-31").map({True: "Y", False: "N"})
    log_rule(name, "R7", "drv_is_perpetual", int((df["drv_is_perpetual"] == "Y").sum()),
             "영구채 플래그 생성", "MAT_DT=99991231 센티널")

    # R8(8/5 정정): 만기 판정을 "날짜 파싱 성공 행"으로 제한하고 4-상태로 파생.
    #   기존 구현은 MAT_DT 파싱 불가(=0 등) 행 316건이 '만기도래'에 오포함되는
    #   버그가 있었다(만기도래 16,496 중 316건 — dev-kyung 교차검증으로 확인).
    #   파싱 실패·결측은 unknown 으로 분리하고, drv_is_matured 는 matured 만 Y.
    df["drv_maturity_status"] = df["MAT_DT"].map(maturity_status)
    st_cnt = df["drv_maturity_status"].value_counts()
    n_perp = int((df["drv_is_perpetual"] == "Y").sum())
    for st, desc in [("matured", "만기 도래(AS_OF 이전)"),
                     ("matures_on_snapshot", "AS_OF 당일 만기"),
                     ("active", f"잔존(AS_OF 이후) — 영구채 {n_perp}건 포함"
                                " (dev-kyung 집계 25,884는 영구채를 불명으로 분류해 4건 차이)"),
                     ("unknown", "만기 불명(파싱 불가·결측 — MAT_DT=0 316 + 공백 3)")]:
        log_rule(name, "R8", "drv_maturity_status", int(st_cnt.get(st, 0)),
                 f"만기 상태 '{st}'", desc)
    assert int(st_cnt.sum()) == len(df), "R8: 만기 상태 합계가 전체 행수와 불일치"
    df["drv_is_matured"] = (df["drv_maturity_status"] == "matured").map({True: "Y", False: "N"})
    log_rule(name, "R8", "drv_is_matured", int((df["drv_is_matured"] == "Y").sum()),
             "만기 도래 플래그 생성(정정)", f"MAT_DT < {AS_OF}, 파싱 성공 행 한정")
    df["drv_is_buyable"] = (pd.to_numeric(df["BUYABLE_QUANTITY"], errors="coerce") > 0).map({True: "Y", False: "N"})
    log_rule(name, "R8", "drv_is_buyable", int((df["drv_is_buyable"] == "Y").sum()),
             "매수가능 플래그 생성", "BUYABLE_QUANTITY > 0 (업무 규칙 확정 전 잠정)")

    # R9: 신용등급 정규화 — 'AA0' 등 끝자리 0(플랫) 제거 + 서열 rank
    df["drv_crd_grd_norm"] = df["CRD_GRD"].map(norm_grd)
    n0 = int((df["CRD_GRD"].notna() & (df["CRD_GRD"] != df["drv_crd_grd_norm"])).sum())
    log_rule(name, "R9", "drv_crd_grd_norm", n0, "끝자리 '0'(플랫 표기) 제거", "AA0→AA")
    df["drv_crd_grd_rank"] = df["drv_crd_grd_norm"].map(CRD_RANK).astype("Int64")
    log_rule(name, "R9", "drv_crd_grd_rank", int(df["drv_crd_grd_rank"].notna().sum()),
             "등급 서열 rank 부여", "AAA=1 ~ D=20, 'AA 이상'=rank<=3")

    # R10: 평가사별 등급(콤마 병기) → 개수·최저(보수적) 등급
    evco_res = df["PD_EVCO_CRD_GRD"].map(evco)
    df["drv_evco_grd_cnt"] = [x[0] for x in evco_res]
    df["drv_evco_grd_worst"] = [x[1] for x in evco_res]
    df["drv_evco_grd_worst_rank"] = pd.array([x[2] for x in evco_res], dtype="Int64")
    log_rule(name, "R10", "drv_evco_grd_*", int(df["drv_evco_grd_cnt"].notna().sum()),
             "평가사 병기 등급 분해", "개수·최저등급(스플릿 시 보수적 채택 관행)")

    # R11: 위험등급 표준화 (0=미분류 → NULL, 1~6 유지)
    grd = pd.to_numeric(df["PD_RISK_GCD"], errors="coerce")
    n_zero = int((grd == 0).sum())
    df["drv_risk_grade"] = grd.where(grd.between(1, 6)).astype("Int64")
    log_rule(name, "R11", "drv_risk_grade", n_zero, "위험등급 0(미분류) → NULL",
             "1=매우 높은 위험 ~ 6=매우 낮은 위험")

    # R13(8/5 확장): 무정보 의심 컬럼 경고 — AVG_ANNUAL_TAX_YIELD 비결측 881건 전부 0.
    #   실제 0인지 미수집 대체값인지 미확인이므로 제거하지 않고 리포트만 남긴다.
    vals = df["AVG_ANNUAL_TAX_YIELD"].dropna()
    if len(vals) and (vals == 0).all():
        log_rule(name, "R13", "AVG_ANNUAL_TAX_YIELD", len(vals), "유지(무정보 의심)",
                 "비결측값 전부 0 — 미수집 대체값 의심, 확인 전 순위·비교 사용 금지")

    # R30: 필드 신선도 — 표준정보 갱신일(PD_STD_INFO_UPDATE)의 AS_OF 대비 지연일수.
    #   원본 REMAINING_DAYS 등이 이 행별 갱신일 기준이므로(중앙값 137일 지연)
    #   근거 표기는 추출일 일괄이 아니라 필드별 기준일을 써야 한다.
    df["drv_std_info_lag_days"] = iso_lag_days(df["PD_STD_INFO_UPDATE"])
    med = df["drv_std_info_lag_days"].dropna().median()
    log_rule(name, "R30", "drv_std_info_lag_days", int(df["drv_std_info_lag_days"].notna().sum()),
             "표준정보 신선도(지연일) 파생", f"AS_OF−PD_STD_INFO_UPDATE, 중앙값 {int(med)}일")

    # R31: 비-KR ISIN 국제채권 태깅 — 국내채권 마스터의 데이터셋 범위 예외.
    #   ISIN 앞 2자 XS는 발행자 국적이 아니라 국제예탁(Euroclear/Clearstream) 범위
    #   표기이므로 국내발행채권 검색에서 제외할 근거를 명시적 플래그로 남긴다.
    intl = df["PD_NO"].notna() & ~df["PD_NO"].str.startswith("KR")
    df["drv_is_intl_bond"] = intl.map({True: "Y", False: "N"})
    log_rule(name, "R31", "drv_is_intl_bond", int(intl.sum()), "비-KR ISIN 국제채권 태깅",
             "PD_NO=" + ",".join(sorted(df.loc[intl, "PD_NO"])[:5]) + " — 국내발행채권 질의에서 제외 대상")

    assert len(df) == 42394, f"행수 불일치: {len(df)}"
    assert df["PD_NO"].is_unique, "PD_NO 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_{SLUG[name]}_processed.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장")


# ──────────────────────────────── 국내ETF ────────────────────────────────
def process_kr_etf():
    tid, name = "PREF01N001", "국내ETF"
    df = pd.read_excel(os.path.join(DS, TABLES[tid][1]), dtype=str)
    types = load_schema_types(TABLES[tid][2])
    df = common_clean(df, name)

    # R24: 손상 행 격리 — 도메인 규칙 기반 자동 탐지 (숫자 캐스팅 전에 수행해
    #   격리 파일에 원본 문자열이 그대로 남게 한다). 키 유일성 assertion은
    #   'KR'도 고유값이라 통과하므로 형식 규칙으로 잡아야 한다.
    bad = kr_etp_corrupt_mask(df)
    quarantined = df[bad]
    if len(quarantined):
        quarantined.to_csv(os.path.join(OUT, "quarantine_PREF01N001.csv"),
                           index=False, encoding="utf-8-sig")
        df = df[~bad].copy()
    log_rule(name, "R24", "(행 단위)", len(quarantined), "손상 행 격리",
             "pd_itm_no 12자리 형식 위반·상품명 '.' — 같은 파일 정상 행(KR70193M0005)의 손상된 중복, 상품 유실 없음")

    df = cast_numeric(df, types, name)

    # R12: 전량 결측 컬럼 제거 (100% 무정보 — 질의 불가 컬럼, 컬럼사전에 '미제공' 기록)
    df = drop_all_null_cols(
        df, ["nru_mkt_diff_rt", "nru_mkt_inav", "pd_dvid_cycl", "pd_sect_nm", "ru_mkt_price", "ru_mkt_volume"],
        name, "R12", "결측+공백 100% 실측 확인 후 제거")

    # R13: 무정보 의심 컬럼(비결측값 전부 0)은 제거하지 않고 리포트만 (실제 0인지 미수집인지 미확인)
    for col in ["du_chas_errt", "du_diff_rt", "pd_dvid_yield"]:
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(vals) and (vals == 0).all():
            log_rule(name, "R13", col, len(vals), "유지(무정보 의심)", "비결측값 전부 0 — 확인 전 순위·비교 사용 금지")

    # R14: 접두어 코드 정규화 파생
    df["drv_curr_cd"] = df["pd_curr_cd"].str.replace("CURR_CD_", "", regex=False).replace({"000": None})
    log_rule(name, "R14", "drv_curr_cd", int(df["drv_curr_cd"].notna().sum()),
             "통화코드 접두어 제거", "CURR_CD_KRW→KRW, CURR_CD_000→NULL")
    df["drv_risk_grade"] = pd.to_numeric(
        df["pd_risk_cd"].str.extract(r"PD_RISK_GCD_1([1-6])")[0], errors="coerce").astype("Int64")
    log_rule(name, "R14", "drv_risk_grade", int(df["drv_risk_grade"].notna().sum()),
             "위험등급 코드 → 1~6 정수", "PD_RISK_GCD_11→1(매우 높은 위험)")

    # R15: 날짜 정규화
    df["du_upt_dt"] = df["du_upt_dt"].str.slice(0, 10)
    log_rule(name, "R15", "du_upt_dt", int(df["du_upt_dt"].notna().sum()), "timestamp → ISO 날짜")

    # R25(국내): 상품유형(ETF/ETN) 파생 — "ETF 추천" 질의에 ETN 혼입 방지.
    #   ETN은 총보수·du_last_aum 등 필드 특성도 다르므로 유형 필터가 선행돼야 한다.
    df["drv_instrument_type"] = df["pd_grp_no"].map(kr_instrument_type)
    for t in ["ETF", "ETN"]:
        log_rule(name, "R25", "drv_instrument_type", int((df["drv_instrument_type"] == t).sum()),
                 f"상품유형 '{t}' 파생", "pd_grp_no 실측 분포 {ETF, ETN} 두 값뿐 — 격리 후 ETF 1,201 + ETN 532 기대")
    assert df["drv_instrument_type"].notna().all(), "R25: pd_grp_no에 미지의 값 존재 — 매핑 확인 필요"

    # R26(국내): 상장 상태 파생 — 종료 상품이 수익률·보수 랭킹에 섞이면 오답이므로
    #   기본 검색 대상(active)을 명시적 상태값으로 분리한다.
    df["drv_listing_status"] = [listing_status(l, t) for l, t in zip(df["pd_lste_dt"], df["pd_tr_yn"])]
    ls_cnt = df["drv_listing_status"].value_counts()
    for st, desc in [("delisted", f"거래종료(pd_lste_dt < {AS_OF})"),
                     ("suspended", "거래정지(pd_tr_yn=1, 1=정지 주의)"),
                     ("active", "기본 검색 대상")]:
        log_rule(name, "R26", "drv_listing_status", int(ls_cnt.get(st, 0)), f"상장 상태 '{st}'", desc)
    assert int(ls_cnt.sum()) == len(df), "R26: 상장 상태 합계가 전체 행수와 불일치"

    # R27(국내): 거래종료 상품의 -100 기간수익률 무효화 — 종료 상품은 종가(du_clpr)가
    #   0으로 남고, 기간수익률 -100은 실손실이 아니라 이 0 종가에서 계산된 산출물이다.
    #   실측: -100 보유 행은 전부 종가 0 종료 상품. processed 상에서 NULL 처리(원본은 datasets/ 보존).
    ended_zero = (df["drv_listing_status"] == "delisted") & (pd.to_numeric(df["du_clpr"], errors="coerce") == 0)
    er_cols = [c for c in KR_ETF_ER_COLS if c in df.columns]
    n_vals, n_rows = 0, 0
    hit_any = pd.Series(False, index=df.index)
    for c in er_cols:
        hit = ended_zero & (pd.to_numeric(df[c], errors="coerce") == -100)
        if hit.any():
            df.loc[hit, c] = None
            n_vals += int(hit.sum())
            hit_any |= hit
    n_rows = int(hit_any.sum())
    log_rule(name, "R27", ",".join(er_cols), n_vals, "종료 상품 -100 수익률 → NULL",
             f"종가 0 거래종료 {int(ended_zero.sum())}건 중 {n_rows}행 — -100은 0 종가 계산 산출물, 실손실 아님")

    # R30: 필드 신선도 — 일간 데이터 기준일(du_upt_dt)의 AS_OF 대비 지연일수.
    df["drv_daily_lag_days"] = iso_lag_days(df["du_upt_dt"])
    med = df["drv_daily_lag_days"].dropna().median()
    log_rule(name, "R30", "drv_daily_lag_days", int(df["drv_daily_lag_days"].notna().sum()),
             "일간 데이터 신선도(지연일) 파생", f"AS_OF−du_upt_dt, 중앙값 {int(med)}일")

    assert len(df) == 1733, f"행수 불일치: {len(df)} (격리 후 1,733 = ETF 1,201 + ETN 532)"
    assert len(df) + len(quarantined) == 1734, "행수 보존 위반 (원본 1,734)"
    assert df["pd_itm_no"].is_unique, "pd_itm_no 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_{SLUG[name]}_processed.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장 (+격리 {len(quarantined)}행)")


# ──────────────────────────────── 해외ETF ────────────────────────────────
def process_gl_etf():
    tid, name = "PREF02N001", "해외ETF"
    df = pd.read_excel(os.path.join(DS, TABLES[tid][1]), dtype=str)
    types = load_schema_types(TABLES[tid][2])
    df = common_clean(df, name)
    df = cast_numeric(df, types, name)

    # R16: 기초지수 센티널 → NULL (실질결측 ~48% — '기초지수 결측 0%'는 착시)
    n = int(df["cu_base_index"].isin(GL_INDEX_SENTINELS).sum())
    df["cu_base_index"] = df["cu_base_index"].where(~df["cu_base_index"].isin(GL_INDEX_SENTINELS))
    log_rule(name, "R16", "cu_base_index", n, "Lipper 센티널 문자열 → NULL",
             "'Index is not provided...' 등 2종")

    # R17: 전량 결측 컬럼 제거
    df = drop_all_null_cols(df, ["cu_lev_fector"], name, "R17", "배수 정보 100% 결측")
    # du_er_1d: 비결측값 전부 0 → 무정보 확정으로 제거
    vals = pd.to_numeric(df["du_er_1d"], errors="coerce").dropna()
    if len(vals) and (vals == 0).all():
        df = df.drop(columns=["du_er_1d"])
        log_rule(name, "R17", "du_er_1d", len(vals), "무정보 컬럼 제거", "비결측 5,388건 전부 0")

    # R18: Y-only 플래그 컬럼 → NULL=N 해석 파생 (원본 유지)
    for src, drv in [("cu_etn_yn", "drv_is_etn"), ("cu_inverse_short_yn", "drv_is_inverse")]:
        df[drv] = (df[src] == "Y").map({True: "Y", False: "N"})
        log_rule(name, "R18", drv, int((df[drv] == "Y").sum()),
                 "플래그 파생(NULL=N)", f"{src}: 값이 있으면 전부 Y인 플래그성 컬럼")

    # R19: 날짜 정규화
    df["du_nav_base_dt"] = df["du_nav_base_dt"].str.slice(0, 10)
    log_rule(name, "R19", "du_nav_base_dt", int(df["du_nav_base_dt"].notna().sum()), "timestamp → ISO 날짜")

    # R25(해외): 상품유형(ETF/ETN) 파생 — drv_is_etn(R18) 활용.
    #   실측 교차검증: pd_grp_no 분포 {ETF 5,587, ETN 59}와 cu_etn_yn 'Y' 59건이
    #   정확히 일치한다. 불일치가 생기면 리포트 비고로 드러나게 한다.
    df["drv_instrument_type"] = df["drv_is_etn"].map({"Y": "ETN", "N": "ETF"})
    n_mismatch = int((df["drv_instrument_type"] != df["pd_grp_no"].str.upper()).sum())
    for t in ["ETF", "ETN"]:
        log_rule(name, "R25", "drv_instrument_type", int((df["drv_instrument_type"] == t).sum()),
                 f"상품유형 '{t}' 파생", f"drv_is_etn 기반, pd_grp_no 교차검증 불일치 {n_mismatch}건")

    # R29(해외): 핵심 필드 대부분 결측 + 상장일 00000000 인 희소 행 태깅 (행 제거 아님).
    #   기본 랭킹·조건 검색에서 제외하되 티커 직접 조회는 허용해야 하므로 태그만 남긴다.
    sparse = df[GL_CORE_COLS].isna().sum(axis=1) >= 4
    df["drv_incomplete_core"] = sparse.map({True: "Y", False: "N"})
    n_zero_lstg = int((sparse & (df["pd_lstg_dt"] == "00000000")).sum())
    log_rule(name, "R29", "drv_incomplete_core", int(sparse.sum()), "희소 행 태깅(핵심 5필드 중 4+ 결측)",
             f"ISIN·운용사·자산군·지역·종가 기준, 상장일 00000000 동반 {n_zero_lstg}건 — 기본 랭킹 제외, 직접 조회 허용")

    # R30: 필드 신선도 — 일간 데이터 갱신일(du_upt_dt)의 AS_OF 대비 지연일수.
    #   명세 초안은 du_nav_base_dt 기준이었으나 실측 결과 du_nav_base_dt는 전 행
    #   상수(2026-06-14)라 행별 신선도 정보가 없다(지연 27일 고정, 30일 초과 0행).
    #   행별로 값이 다른(88개 고유값, 2025-07-29~2026-06-16) 일간 갱신일 du_upt_dt
    #   기준으로 파생한다 — dev-kyung market_data_lag_days(30일 초과 252행)와 동일 기준.
    df["drv_daily_lag_days"] = iso_lag_days(df["du_upt_dt"], fmt="%Y%m%d")
    n_stale = int((df["drv_daily_lag_days"] > 30).sum())
    log_rule(name, "R30", "drv_daily_lag_days", int(df["drv_daily_lag_days"].notna().sum()),
             "일간 데이터 신선도(지연일) 파생",
             f"AS_OF−du_upt_dt, 30일 초과 지연 {n_stale}행 — du_nav_base_dt는 전 행 상수(2026-06-14)라 기준 부적합")

    assert len(df) == 5646, f"행수 불일치: {len(df)}"
    assert df["pd_itm_no"].is_unique, "pd_itm_no 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_{SLUG[name]}_processed.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장")


# ──────────────────────────────── 공모펀드 ────────────────────────────────
def process_fund():
    tid, name = "PRFD01N001", "공모펀드"
    df = pd.read_excel(os.path.join(DS, TABLES[tid][1]), dtype=str)
    types = load_schema_types(TABLES[tid][2])
    df = common_clean(df, name)

    # R20: 도메인 위반(컬럼 밀림) 행 quarantine — 캐스팅 전에 탐지해야 흔적이 남는다
    bad = (
        ~df["itm_no"].fillna("").str.fullmatch(r"[A-Z0-9]{12}")
        | ~df["exchdg_yn"].isin(["Y", "N"]) & df["exchdg_yn"].notna()
        | ~df["zrin_fd_ivst_risk_gcd"].isin(["1", "2", "3", "4", "5", "6"]) & df["zrin_fd_ivst_risk_gcd"].notna()
    )
    quarantined = df[bad]
    if len(quarantined):
        quarantined.to_csv(os.path.join(OUT, "quarantine_PRFD01N001.csv"),
                           index=False, encoding="utf-8-sig")
        df = df[~bad].copy()
    log_rule(name, "R20", "(행 단위)", len(quarantined), "도메인 위반 행 격리",
             "컬럼 밀림 비정상 레코드 — 복구 전 적재 금지 (팀 가이드 정제규칙 9)")

    df = cast_numeric(df, types, name)

    # R21: kofia_fd_ccd 전체 0 센티널 → NULL
    n = int((df["kofia_fd_ccd"] == "0" * 20).sum())
    df["kofia_fd_ccd"] = df["kofia_fd_ccd"].replace({"0" * 20: None})
    log_rule(name, "R21", "kofia_fd_ccd", n, "'000...0'(20자리) 센티널 → NULL")

    # R22: or_attr_desc '06'은 결측으로 버리지 않고 보존 (파생형 코드 후보 — 팀 가이드)
    n06 = int((df["or_attr_desc"] == "06").sum())
    log_rule(name, "R22", "or_attr_desc", n06, "유지(미변환 코드 보존)",
             "'06' — 파생형 상품 코드 후보, 매핑 확정 전 NULL 변환 금지")

    # R23: 위험등급 정수 파생 + 등급명 표기 오염('높은위험' vs '높은 위험')은 코드 기준으로 해소
    df["drv_risk_grade"] = pd.to_numeric(df["zrin_fd_ivst_risk_gcd"], errors="coerce").astype("Int64")
    log_rule(name, "R23", "drv_risk_grade", int(df["drv_risk_grade"].notna().sum()),
             "위험등급 정수 파생", "1=매우 높은 위험 ~ 6=매우 낮은 위험. 등급명 문자열 대신 이 컬럼 사용")

    # R28: 물리적으로 불가능한 수익률(-100% 미만) → NULL — 단위·소수점 오류 후보.
    #   추정 복구(/100 등)는 하지 않고 품질 이력(log_rule)만 남긴다.
    ret_cols = [c for c in df.columns if re.fullmatch(r"fd_(wk|mm|yr)\d+_ern_r", c)]
    for c in ret_cols:
        col_num = pd.to_numeric(df[c], errors="coerce")
        imp = col_num < -100
        if imp.any():
            items = sorted(set(df.loc[imp, "itm_no"].dropna()))
            obs = [f"{v:.1f}" for v in col_num[imp]]
            df.loc[imp, c] = None
            log_rule(name, "R28", c, int(imp.sum()), "-100% 미만 수익률 → NULL",
                     f"itm_no={','.join(items)} 관측값 {','.join(obs)} — 단위·소수점 오류 후보, 추정 복구 금지")

    assert len(df) + len(quarantined) == 95619, "행수 보존 위반"
    assert not df.duplicated(subset=["itm_no", "prfd_attr_cd"]).any(), "(itm_no, prfd_attr_cd) 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_{SLUG[name]}_processed.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장 (+격리 {len(quarantined)}행)")


def main():
    """실행 경로 전용 — import 시에는 어떤 부작용도 없다 (테스트 import 안전성)."""
    # Windows 콘솔(cp949)에서 한글 출력 깨짐 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if not os.path.isdir(DS):
        sys.exit(
            f"원본 데이터 폴더를 찾을 수 없습니다: {DS}\n"
            "  · 대회에서 받은 xlsx 8개를 repo 루트의 datasets/ 에 넣거나,\n"
            "  · 환경변수 MIRAE_DATASETS 에 원본 폴더 경로를 지정하세요.\n"
            "  (원본은 참가자 전원이 보유하므로 저장소에 커밋하지 않습니다)"
        )
    os.makedirs(OUT, exist_ok=True)
    process_bond()
    process_kr_etf()
    process_gl_etf()
    process_fund()
    rep = pd.DataFrame(report)
    rep.to_csv(os.path.join(OUT, "preprocessing_report.csv"), index=False, encoding="utf-8-sig")
    print(f"\npreprocessing_report.csv: {len(rep)}건 규칙 기록")
    print("완료 →", OUT)


if __name__ == "__main__":
    main()
