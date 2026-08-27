# -*- coding: utf-8 -*-
"""
RDB 적재 — 전처리 CSV 4종 + 구성종목 수집분 → DuckDB 파일 1개 (S2 순서 ①).

무엇: storage/output/products.duckdb 에 6개 테이블을 만든다.
왜  : SQL 채널(필터·정렬·집계)의 저장소. DuckDB 는 표준 SQL 을 쓰는 관계형
      DB(RDB)이며 in-process(서버 데몬 0개·파일 1개·백업=복사)라 2vCPU/4GB
      단일 서버 무인 운영 제약에 맞는다 — S2_PLAN §1(8/13 승인).

테이블 (원시 보존 원칙 — 전 컬럼 VARCHAR, 선행 0·원문 보존 / 8/27: 주최 재배포본 전환):
  kr_bond         국내채권 20,497 (pd_no 유일 대표행 — R33 시장별 중복 대표화)
  kr_bond_alt     국내채권 시장별 중복 행 1,385 (장내 타 시장 시세 보존 — 기본 검색 제외)
  kr_etp          국내 ETF+ETN 1,779 (혼재 주의 — 검색은 drv_instrument_type 필터 필수)
  global_etf      해외ETF 6,037
  fund_master     공모펀드 상품 단위 23,622 (fss_itm_no 그룹 대표 + share_class_count)
  fund_class      공모펀드 판매 클래스 23,676 — itm_no 단독 키 (재배포본은 1행=1클래스)
  etf_constituent 구성종목 75,081 (기준일 2026-07-10 — 재수집 전까지 유지, 마스터 8/22 와 다름)

수치 정렬·비교 규약: 적재는 무손실 VARCHAR 로 하고, SQL 템플릿에서
  TRY_CAST(col AS DOUBLE) 를 쓴다(콤마 포함 컬럼은 replace 후 캐스트).
  파싱 실패 = NULL = "값 없음"과 동일 취급 — 결측 원칙과 일관.

실행 : python storage/load_duckdb.py          # 전체 재적재(멱등 — DROP 후 재생성)
검증 : 적재 후 기대 행수 대조 + 스팟 쿼리(TIGER 200·AUM 상위) 자동 실행, 불일치 시 종료 코드 1.
구조 주의: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # storage/
ROOT = os.path.dirname(HERE)
PROCESSED = os.path.join(ROOT, "preprocessing", "processed")
CONSTITUENTS_CSV = os.path.join(ROOT, "external_data", "constituents",
                                "constituents_20260710.csv")
OUT_DIR = os.path.join(HERE, "output")
DB_PATH = os.path.join(OUT_DIR, "products.duckdb")

CONSTITUENTS_AS_OF = "2026-07-10"   # 구성종목 조회 기준일 — 근거 표시용 컬럼으로 적재

# (테이블명, CSV 경로, 기대 행수) — 기대치는 8/27 재배포본 실측(어긋나면 적재 실패로 처리)
TABLES = [
    ("kr_bond",     os.path.join(PROCESSED, "PRBD01N001_kr_bond_processed.csv"),    20497),
    ("kr_bond_alt", os.path.join(PROCESSED, "PRBD01N001_kr_bond_alt_rows.csv"),      1385),
    ("kr_etp",      os.path.join(PROCESSED, "PREF01N001_kr_etf_processed.csv"),      1779),
    ("global_etf",  os.path.join(PROCESSED, "PREF02N001_global_etf_processed.csv"),  6037),
    ("fund_class",  os.path.join(PROCESSED, "PRFD01N001_public_fund_processed.csv"), 23676),
    ("etf_constituent", CONSTITUENTS_CSV,                                           75081),
]
FUND_MASTER_EXPECTED = 23622


def load_table(con, name, csv_path):
    """CSV 1개 → VARCHAR 전컬럼 테이블. 멱등(DROP 후 재생성)."""
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(
        f"CREATE TABLE {name} AS SELECT * FROM read_csv(?, all_varchar=true, header=true)",
        [csv_path])
    return con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]


def build_mgmt_resolved(con):
    """운용사 오염 복구 테이블(8/13) — kr_etp 전 행의 (원시값, 복구값, 방법).

    SQL 채널의 운용사 집계(M-09·H-29 유형)가 조인해 쓴다. 복구 규칙은
    pipeline/mgmt_resolution.py 단일 소스 — 여기서는 실행만 한다.
    """
    sys.path.insert(0, ROOT)
    from pipeline.mgmt_resolution import resolve_mgmt_co
    rows = con.execute("SELECT pd_itm_no, cu_fund_mgmt_co FROM kr_etp").fetchall()
    resolved = [(pid, raw, *resolve_mgmt_co(raw)) for pid, raw in rows]
    con.execute("DROP TABLE IF EXISTS mgmt_resolved")
    con.execute("""CREATE TABLE mgmt_resolved (
        pd_itm_no VARCHAR, raw VARCHAR, resolved VARCHAR, method VARCHAR)""")
    con.executemany("INSERT INTO mgmt_resolved VALUES (?, ?, ?, ?)", resolved)
    n_fixed = con.execute(
        "SELECT count(*) FROM mgmt_resolved WHERE method IN ('recovered','brand_split')"
    ).fetchone()[0]
    return len(resolved), n_fixed


def build_fund_master(con):
    """fund_class → 상품 단위 마스터 (8/27 재배포본 구조).

    재배포본은 1행=1클래스(itm_no 고유)이고 상품 묶음 키는 금감원 펀드코드
    fss_itm_no 다(전처리 R32에서 센티널 '000…0'→NULL). 코드가 없는 행은 행 자체가
    상품이다. 클래스 2개 이상인 그룹은 45개뿐 — 대표는 순자산(fd_nast_suma) 최대
    클래스, 동률이면 itm_no 사전순(결정적). share_class_count 로 클래스 수 보존."""
    con.execute("DROP TABLE IF EXISTS fund_master")
    con.execute("""
        CREATE TABLE fund_master AS
        SELECT * EXCLUDE (rn, grp_key)
        FROM (
            SELECT c.*,
                   coalesce(fss_itm_no, itm_no) AS grp_key,
                   row_number() OVER (
                       PARTITION BY coalesce(fss_itm_no, itm_no)
                       ORDER BY TRY_CAST(fd_nast_suma AS DOUBLE) DESC NULLS LAST, itm_no
                   ) AS rn,
                   count(*) OVER (PARTITION BY coalesce(fss_itm_no, itm_no)) AS share_class_count
            FROM fund_class c
        )
        WHERE rn = 1
    """)
    return con.execute("SELECT count(*) FROM fund_master").fetchone()[0]


def add_constituent_as_of(con):
    """구성종목 기준일 컬럼 — 마스터(7/11)와 다른 7/10 임을 행 단위로 보존(근거 표시)."""
    con.execute("ALTER TABLE etf_constituent ADD COLUMN as_of VARCHAR")
    con.execute("UPDATE etf_constituent SET as_of = ?", [CONSTITUENTS_AS_OF])


def spot_checks(con):
    """적재 직후 스팟 검증 — 실패 시 예외. SQL 채널의 수치 규약(TRY_CAST)도 함께 검증."""
    # ① TIGER 200 존재 + 운용사
    row = con.execute("""
        SELECT pd_abrv_nm, cu_fund_mgmt_co FROM kr_etp
        WHERE pd_itm_no = 'KR7102110004'
    """).fetchone()
    assert row and "TIGER" in (row[0] or ""), f"TIGER 200 조회 실패: {row}"
    # ② AUM 상위 5 (ETF만·active만 — ETN 혼재 함정 + TRY_CAST 규약 검증)
    top = con.execute("""
        SELECT pd_abrv_nm FROM kr_etp
        WHERE drv_instrument_type = 'ETF' AND drv_listing_status = 'active'
        ORDER BY TRY_CAST(pd_net_tamt AS DOUBLE) DESC NULLS LAST LIMIT 5
    """).fetchall()
    names = " / ".join(r[0] or "?" for r in top)
    assert any("200" in (r[0] or "") for r in top), f"AUM 상위에 200 계열 부재(의심): {names}"
    # ③ 구성종목 조인: 삼성전자 비중 30%+ ETF 존재(실측 TIGER 200 = 33.03)
    n = con.execute("""
        SELECT count(*) FROM etf_constituent
        WHERE COMPST_ISU_CD = '005930'
          AND TRY_CAST(replace(COMPST_RTO, ',', '') AS DOUBLE) > 30
    """).fetchone()[0]
    assert n >= 1, "삼성전자 비중 30%+ 행 부재 — COMPST_RTO 캐스트 규약 확인 필요"
    # ④ 펀드 마스터/클래스 관계
    m, c = (con.execute("SELECT count(*) FROM fund_master").fetchone()[0],
            con.execute("SELECT count(*) FROM fund_class").fetchone()[0])
    assert m < c, "마스터가 클래스보다 크다 — 그룹핑 오류"
    return names


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import duckdb
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)                     # 멱등 — 파일부터 재생성
    con = duckdb.connect(DB_PATH)
    failed = False
    for name, path, expected in TABLES:
        if not os.path.exists(path):
            print(f"[{name}] 입력 없음: {path}")
            failed = True
            continue
        n = load_table(con, name, path)
        ok = "OK" if n == expected else f"기대 {expected:,} 불일치!"
        if n != expected:
            failed = True
        print(f"[{name}] {n:,}행 — {ok}")
    n = build_fund_master(con)
    ok = "OK" if n == FUND_MASTER_EXPECTED else f"기대 {FUND_MASTER_EXPECTED:,} 불일치!"
    if n != FUND_MASTER_EXPECTED:
        failed = True
    print(f"[fund_master] {n:,}행 — {ok}")
    total, n_fixed = build_mgmt_resolved(con)
    print(f"[mgmt_resolved] {total:,}행 (복구 {n_fixed}건)")
    add_constituent_as_of(con)
    names = spot_checks(con)
    con.close()
    size_mb = os.path.getsize(DB_PATH) / 1e6
    print(f"스팟 검증 통과 — AUM 상위: {names}")
    print(f"적재 완료 → {DB_PATH} ({size_mb:.0f}MB)")
    if failed:
        sys.exit("행수 불일치 있음 — 위 로그 확인")


if __name__ == "__main__":
    main()
