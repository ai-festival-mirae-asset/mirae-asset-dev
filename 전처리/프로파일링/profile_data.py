# -*- coding: utf-8 -*-
"""4종 마스터 데이터 프로파일링 (Sprint 0 항목) — 2026-08-04
결측률·공백문자열·고유값·타입 트랩을 실측한다. 재실행 가능(멱등).

산출물 (이 스크립트와 같은 폴더):
  - 프로파일링_국내채권.csv / 국내ETF / 해외ETF / 공모펀드  (컬럼별 상세)
  - 프로파일링_요약.md  (팀 공유용 요약 + 트랩 검증 결과)

실행: python profile_data.py
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent          # 전처리/프로파일링/
DATA = HERE.parent.parent / "datasets"          # repo 루트의 원본 xlsx

TABLES = {
    "국내채권": ("PRBD01N001", "PRBD01N001_국내채권마스터_20260711_datarows.xlsx", 42394),
    "국내ETF": ("PREF01N001", "PREF01N001_국내ETF마스터_20260711_datarows.xlsx", 1734),
    "해외ETF": ("PREF02N001", "PREF02N001_해외ETF마스터_20260711_datarows.xlsx", 5646),
    "공모펀드": ("PRFD01N001", "PRFD01N001_공모펀드마스터_20260711_datarows.xlsx", 95619),
}


def col(df: pd.DataFrame, name: str):
    """대소문자 무시 컬럼 탐색 (채권만 대문자 명명)."""
    return next((c for c in df.columns if c.lower() == name.lower()), None)


def profile_table(df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    rows = []
    for c in df.columns:
        s = df[c]
        n_null = int(s.isna().sum())
        if s.dtype == object:
            stripped = s.dropna().astype(str).str.strip()
            n_blank = int(stripped.eq("").sum())
            vals = stripped[stripped != ""]
        else:
            n_blank = 0
            vals = s.dropna()
        eff_missing = n_null + n_blank
        top3 = "; ".join(
            f"{str(k)[:24]} ({v})" for k, v in vals.value_counts().head(3).items()
        )
        num = pd.to_numeric(vals, errors="coerce") if len(vals) else pd.Series(dtype=float)
        n_numlike = int(num.notna().sum())
        looks_numeric_str = s.dtype == object and len(vals) > 0 and n_numlike / len(vals) >= 0.95
        rows.append({
            "컬럼": c,
            "dtype": str(s.dtype),
            "결측수": n_null,
            "공백문자열수": n_blank,
            "실질결측률%": round(100 * eff_missing / n, 2),
            "고유값수": int(vals.nunique()),
            "숫자형문자열": "Y" if looks_numeric_str else "",
            "min": round(float(num.min()), 4) if n_numlike else "",
            "max": round(float(num.max()), 4) if n_numlike else "",
            "상위값3": top3,
        })
    return pd.DataFrame(rows)


def main() -> None:
    md = []
    md.append("# 4종 마스터 데이터 프로파일링 요약")
    md.append("")
    md.append("> 실행일 2026-08-04 · 데이터 기준일 2026-07-11 스냅샷 · 스크립트 `profile_data.py` (재실행 가능)")
    md.append("> 컬럼별 상세는 같은 폴더의 `프로파일링_<상품군>.csv` 참조 (Excel에서 바로 열림)")
    md.append("")

    overview = []
    traps = []
    dfs = {}

    for name, (tid, fname, expected) in TABLES.items():
        path = DATA / fname
        print(f"[로드] {name} ← {fname}", flush=True)
        df = pd.read_excel(path, engine="calamine")
        dfs[name] = df
        prof = profile_table(df)
        out_csv = HERE / f"프로파일링_{name}.csv"
        prof.to_csv(out_csv, index=False, encoding="utf-8-sig")

        n, ncol = len(df), df.shape[1]
        match = "일치" if n == expected else f"**불일치(예상 {expected:,})**"
        n_missing_cols = int((prof["실질결측률%"] >= 50).sum())
        overview.append(f"| {name} | {tid} | {n:,} | {ncol} | {match} | {n_missing_cols} |")

        top_missing = prof[prof["실질결측률%"] > 0].nlargest(10, "실질결측률%")
        md.append(f"## {name} ({tid}) — {n:,}건 × {ncol}컬럼")
        md.append("")
        if top_missing.empty:
            md.append("- 결측·공백 없음")
        else:
            md.append("**실질결측률 상위 10 (결측 + 공백문자열):**")
            md.append("")
            md.append("| 컬럼 | 실질결측률% | 결측수 | 공백문자열수 | 고유값수 |")
            md.append("|---|---|---|---|---|")
            for _, r in top_missing.iterrows():
                md.append(f"| `{r['컬럼']}` | {r['실질결측률%']} | {r['결측수']:,} | {r['공백문자열수']:,} | {r['고유값수']:,} |")
        num_str_cols = prof[prof["숫자형문자열"] == "Y"]["컬럼"].tolist()
        if num_str_cols:
            md.append("")
            md.append(f"**숫자형 문자열 컬럼 (적재 시 캐스팅 필요, {len(num_str_cols)}개):** "
                      + ", ".join(f"`{c}`" for c in num_str_cols[:15])
                      + (" …" if len(num_str_cols) > 15 else ""))
        md.append("")

    # ---------- 트랩 검증 ----------
    bond, ketf, getf, fund = dfs["국내채권"], dfs["국내ETF"], dfs["해외ETF"], dfs["공모펀드"]

    # 1) 채권: 날짜가 숫자형인지
    for cname in ("ISU_DT", "MAT_DT", "CRD_GRD_DT"):
        c = col(bond, cname)
        if c is not None:
            s = pd.to_numeric(bond[c], errors="coerce").dropna()
            rng = f"{int(s.min())}~{int(s.max())}" if len(s) else "값 없음"
            traps.append(f"- [채권] `{c}` dtype=`{bond[c].dtype}`, 범위 {rng} → "
                         + ("**숫자로 저장된 날짜 확인 — 적재 시 date 캐스팅 필수**" if bond[c].dtype != object else "문자열 — 파싱 규칙 확인"))
    c = col(bond, "REMAINING_DAYS")
    if c is not None:
        s = pd.to_numeric(bond[c], errors="coerce")
        traps.append(f"- [채권] `{c}` 존재, 결측 {int(s.isna().sum()):,}건 → 잔존만기 연산에 우선 활용 가능")
    yield_cols = [c for c in bond.columns if "YIELD" in c.upper()]
    if yield_cols:
        parts = []
        for c in yield_cols:
            s = bond[c]
            miss = int(s.isna().sum())
            if s.dtype == object:
                miss += int(s.dropna().astype(str).str.strip().eq("").sum())
            parts.append(f"`{c}` {100*miss/len(bond):.0f}%")
        traps.append(f"- [채권] 수익률 컬럼 {len(yield_cols)}종 결측률: " + ", ".join(parts) + " → NULL 처리 규칙 필요")

    # 2) 국내ETF: 공백 40칸 문자열, 숫자형 문자열
    c = col(ketf, "cu_base_index")
    if c is not None:
        raw = ketf[c]
        blank = int(raw.dropna().astype(str).str.strip().eq("").sum())
        traps.append(f"- [국내ETF] `{c}`: NULL {int(raw.isna().sum()):,}건 + **공백문자열 {blank:,}건** / {len(ketf):,}건 → `IS NULL` 필터 불가 확인, TRIM→NULL 정규화 필수")
    c = col(ketf, "cu_charge_rt")
    if c is not None:
        raw = ketf[c]
        miss = int(raw.isna().sum()) + int(raw.dropna().astype(str).str.strip().eq("").sum())
        traps.append(f"- [국내ETF] `{c}`(총보수) dtype=`{raw.dtype}`, 실질결측 {miss:,}/{len(ketf):,}건 ({100*miss/len(ketf):.0f}%) → '일부 종목만 수록' 실측")

    # 3) 해외ETF: cu_strtegy 채움률, 정량 컬럼 채움률
    c = col(getf, "cu_strtegy")
    if c is not None:
        vals = getf[c].dropna().astype(str).str.strip()
        vals = vals[vals != ""]
        traps.append(f"- [해외ETF] `{c}`(운용전략 영문): 채움 {len(vals):,}/{len(getf):,}건 ({100*len(vals)/len(getf):.0f}%), 평균 {vals.str.len().mean():.0f}자 → 벡터 검색 대상 확정")
    for cname in ("cu_charge_rt", "du_last_aum", "cu_base_index"):
        c = col(getf, cname)
        if c is not None:
            raw = getf[c]
            miss = int(raw.isna().sum())
            if raw.dtype == object:
                miss += int(raw.dropna().astype(str).str.strip().eq("").sum())
            traps.append(f"- [해외ETF] `{c}` 실질결측 {miss:,}건 ({100*miss/len(getf):.0f}%) → 정량 비교 질의 적합성 실측")

    # 4) 공모펀드: 복합키·클래스 중복
    k1, k2, k3 = col(fund, "itm_no"), col(fund, "prfd_attr_cd"), col(fund, "zrin_fd_ivst_risk_gcd")
    if k1 is not None:
        nu = fund[k1].nunique()
        traps.append(f"- [공모펀드] `{k1}` 고유값 {nu:,} vs 전체 {len(fund):,}행 → **중복 계상 위험 실측** (행/고유 비율 {len(fund)/nu:.2f}배)")
        if k2 is not None and k3 is not None:
            dup = len(fund) - len(fund.drop_duplicates(subset=[k1, k2, k3]))
            traps.append(f"- [공모펀드] 복합키(`{k1}`,`{k2}`,`{k3}`) 중복 {dup:,}건 → {'PK 성립' if dup == 0 else '**PK 불성립 — 추가 키 필요**'}")
    c = col(fund, "rptt_ksd_itm_no")
    if c is not None:
        filled = int(fund[c].notna().sum())
        traps.append(f"- [공모펀드] `{c}`(대표종목 후보) 채움 {filled:,}/{len(fund):,}건 → 클래스 대표 선정 규칙에 활용 검토")

    header = [
        "## 전체 개요", "",
        "| 상품군 | 테이블 | 건수 | 컬럼수 | 로드맵 건수 대비 | 실질결측률 50%↑ 컬럼수 |",
        "|---|---|---|---|---|---|",
        *overview, "",
        "## 트랩 검증 결과 (로드맵 1장 주장 실측)", "",
        *traps, "",
        "## 적재 파이프라인(S1) TODO로 직결되는 것", "",
        "1. 공백문자열 → NULL 정규화 (전 테이블, 특히 국내ETF `cu_base_index`)",
        "2. 숫자형 문자열 컬럼 일괄 캐스팅 (각 CSV의 `숫자형문자열=Y` 컬럼 목록 참조)",
        "3. 채권 날짜 숫자(`ISU_DT`·`MAT_DT` 등) → date 캐스팅",
        "4. 공모펀드 클래스 대표 종목 규칙 확정 후 집계용 뷰 분리",
        "5. 수익률 컬럼 NULL 처리 규칙 문서화 (채권 7종 수익률·ETF 기간 체계 차이 포함)", "",
    ]
    final_md = md[:5] + header + md[5:]
    (HERE / "프로파일링_요약.md").write_text("\n".join(final_md), encoding="utf-8")
    print("[완료] 프로파일링_요약.md + CSV 4종 생성", flush=True)


if __name__ == "__main__":
    sys.exit(main())
