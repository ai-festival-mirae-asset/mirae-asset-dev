# -*- coding: utf-8 -*-
"""
금융상품 4종 마스터 전처리 파이프라인 (1차)

입력 : datasets/*.xlsx (원본 datarows 4종 + schema 4종)
출력 : data/processed/<테이블ID>_<상품군>_전처리.csv   (전처리 완료 데이터)
       data/processed/quarantine_PRFD01N001_비정상행.csv (격리 행)
       data/processed/전처리_리포트.csv                  (규칙별 영향 행수)

원칙
  1. 원본 컬럼은 삭제·변형을 최소화하고(전량 무정보 컬럼 제외), 해석이 필요한
     정규화 값은 drv_* 파생 컬럼으로 추가한다. 원본 값 자체는 datasets/에 보존된다.
  2. 결측은 메꾸지 않고 "판별"한다. 센티널(무의미 값)을 NULL로 통일해
     IS NULL 필터가 작동하게 만드는 것이 목적이다.
  3. 모든 규칙은 규칙ID와 영향 행수를 리포트로 남긴다 (전처리_리포트.csv).
  4. 재실행 가능(멱등): 같은 입력이면 항상 같은 출력.

근거 문서 : 데이터_전처리_방법.md / 해석_메타데이터/수집_요약.md / PROJECT_GUIDE.md
실행      : python scripts/preprocess.py  (repo 루트에서)
"""
import os
import re
import sys
import io

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS = os.path.join(ROOT, "datasets")
OUT = os.path.join(ROOT, "data", "processed")
os.makedirs(OUT, exist_ok=True)

AS_OF = "2026-07-11"  # 데이터 스냅샷 기준일

TABLES = {
    "PRBD01N001": ("국내채권", "PRBD01N001_국내채권마스터_20260711_datarows.xlsx", "PRBD01N001_국내채권마스터_schema.xlsx"),
    "PREF01N001": ("국내ETF", "PREF01N001_국내ETF마스터_20260711_datarows.xlsx", "PREF01N001_국내ETF마스터_schema.xlsx"),
    "PREF02N001": ("해외ETF", "PREF02N001_해외ETF마스터_20260711_datarows.xlsx", "PREF02N001_해외ETF마스터_schema.xlsx"),
    "PRFD01N001": ("공모펀드", "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx", "PRFD01N001_공모펀드마스터_schema.xlsx"),
}

# 신용등급 서열 (해석_메타데이터/사전_신용등급.csv): AAA=1(최상) ~ D=20
CRD_RANK = {g: i + 1 for i, g in enumerate(
    ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
     "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"])}

# 해외ETF cu_base_index 센티널 (Lipper 원천, 실질결측 ~48%)
GL_INDEX_SENTINELS = {
    "Index is not provided by Management Company",
    "Index is not available on Lipper Database",
}

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
    out = series.map(conv)
    n_bad = int((out == "__BAD__").sum())
    if n_bad:
        log_rule(table, rule_id, col, n_bad, "날짜 파싱 불가(0 등) → NULL")
    n_ok = int(out.notna().sum() - n_bad)
    log_rule(table, rule_id, col, n_ok, "YYYYMMDD → ISO 날짜(YYYY-MM-DD)")
    return out.replace("__BAD__", None)


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

    # R8: 만기 도래 / 매수 가능 플래그 (잔존만기·검색 기본 필터용)
    df["drv_is_matured"] = ((df["MAT_DT"].notna()) & (df["MAT_DT"] < AS_OF)).map({True: "Y", False: "N"})
    log_rule(name, "R8", "drv_is_matured", int((df["drv_is_matured"] == "Y").sum()),
             "만기 도래 플래그 생성", f"MAT_DT < {AS_OF}")
    df["drv_is_buyable"] = (pd.to_numeric(df["BUYABLE_QUANTITY"], errors="coerce") > 0).map({True: "Y", False: "N"})
    log_rule(name, "R8", "drv_is_buyable", int((df["drv_is_buyable"] == "Y").sum()),
             "매수가능 플래그 생성", "BUYABLE_QUANTITY > 0 (업무 규칙 확정 전 잠정)")

    # R9: 신용등급 정규화 — 'AA0' 등 끝자리 0(플랫) 제거 + 서열 rank
    def norm_grd(v):
        if pd.isna(v):
            return None
        m = re.fullmatch(r"(AAA|AA|A|BBB|BB|B|CCC|CC|C|D)0", v)
        return m.group(1) if m else v
    df["drv_crd_grd_norm"] = df["CRD_GRD"].map(norm_grd)
    n0 = int((df["CRD_GRD"].notna() & (df["CRD_GRD"] != df["drv_crd_grd_norm"])).sum())
    log_rule(name, "R9", "drv_crd_grd_norm", n0, "끝자리 '0'(플랫 표기) 제거", "AA0→AA")
    df["drv_crd_grd_rank"] = df["drv_crd_grd_norm"].map(CRD_RANK).astype("Int64")
    log_rule(name, "R9", "drv_crd_grd_rank", int(df["drv_crd_grd_rank"].notna().sum()),
             "등급 서열 rank 부여", "AAA=1 ~ D=20, 'AA 이상'=rank<=3")

    # R10: 평가사별 등급(콤마 병기) → 개수·최저(보수적) 등급
    def evco(v):
        if pd.isna(v):
            return (None, None, None)
        parts = [norm_grd(p.strip()) for p in v.split(",") if p.strip()]
        ranks = [CRD_RANK[p] for p in parts if p in CRD_RANK]
        if not ranks:
            return (len(parts), None, None)
        worst = max(ranks)
        inv = {r: g for g, r in CRD_RANK.items()}
        return (len(parts), inv[worst], worst)
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

    assert len(df) == 42394, f"행수 불일치: {len(df)}"
    assert df["PD_NO"].is_unique, "PD_NO 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_국내채권_전처리.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장")


# ──────────────────────────────── 국내ETF ────────────────────────────────
def process_kr_etf():
    tid, name = "PREF01N001", "국내ETF"
    df = pd.read_excel(os.path.join(DS, TABLES[tid][1]), dtype=str)
    types = load_schema_types(TABLES[tid][2])
    df = common_clean(df, name)
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

    assert len(df) == 1734, f"행수 불일치: {len(df)}"
    assert df["pd_itm_no"].is_unique, "pd_itm_no 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_국내ETF_전처리.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장")


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

    assert len(df) == 5646, f"행수 불일치: {len(df)}"
    assert df["pd_itm_no"].is_unique, "pd_itm_no 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_해외ETF_전처리.csv"), index=False, encoding="utf-8-sig")
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
        quarantined.to_csv(os.path.join(OUT, "quarantine_PRFD01N001_비정상행.csv"),
                           index=False, encoding="utf-8-sig")
        df = df[~bad].copy()
    log_rule(name, "R20", "(행 단위)", len(quarantined), "도메인 위반 행 격리",
             "컬럼 밀림 비정상 레코드 — 복구 전 적재 금지 (PROJECT_GUIDE 정제규칙 9)")

    df = cast_numeric(df, types, name)

    # R21: kofia_fd_ccd 전체 0 센티널 → NULL
    n = int((df["kofia_fd_ccd"] == "0" * 20).sum())
    df["kofia_fd_ccd"] = df["kofia_fd_ccd"].replace({"0" * 20: None})
    log_rule(name, "R21", "kofia_fd_ccd", n, "'000...0'(20자리) 센티널 → NULL")

    # R22: or_attr_desc '06'은 결측으로 버리지 않고 보존 (파생형 코드 후보 — PROJECT_GUIDE)
    n06 = int((df["or_attr_desc"] == "06").sum())
    log_rule(name, "R22", "or_attr_desc", n06, "유지(미변환 코드 보존)",
             "'06' — 파생형 상품 코드 후보, 매핑 확정 전 NULL 변환 금지")

    # R23: 위험등급 정수 파생 + 등급명 표기 오염('높은위험' vs '높은 위험')은 코드 기준으로 해소
    df["drv_risk_grade"] = pd.to_numeric(df["zrin_fd_ivst_risk_gcd"], errors="coerce").astype("Int64")
    log_rule(name, "R23", "drv_risk_grade", int(df["drv_risk_grade"].notna().sum()),
             "위험등급 정수 파생", "1=매우 높은 위험 ~ 6=매우 낮은 위험. 등급명 문자열 대신 이 컬럼 사용")

    assert len(df) + len(quarantined) == 95619, "행수 보존 위반"
    assert not df.duplicated(subset=["itm_no", "prfd_attr_cd"]).any(), "(itm_no, prfd_attr_cd) 유일성 위반"
    df.to_csv(os.path.join(OUT, f"{tid}_공모펀드_전처리.csv"), index=False, encoding="utf-8-sig")
    print(f"{name}: {len(df)}행 × {len(df.columns)}컬럼 저장 (+격리 {len(quarantined)}행)")


if __name__ == "__main__":
    process_bond()
    process_kr_etf()
    process_gl_etf()
    process_fund()
    rep = pd.DataFrame(report)
    rep.to_csv(os.path.join(OUT, "전처리_리포트.csv"), index=False, encoding="utf-8-sig")
    print(f"\n전처리_리포트.csv: {len(rep)}건 규칙 기록")
    print("완료 →", OUT)
