# -*- coding: utf-8 -*-
"""
KRX ETF 구성종목(PDF) 수집기 — 2026-07-11 이전 스냅샷 확보용 (골격, 8/12)

무엇: KRX 정보데이터시스템의 ETF 구성종목(PDF) 통계를 과거 일자 기준으로 수집해
      external_data/constituents/ 에 원시 JSON + 통합 CSV 로 저장한다.
왜  : 출제 예고된 "구성 종목" 질의(예: 삼성전자 포함 ETF)의 답변 근거. 제공 RDB에 없다.
      소스 실사 결과(external_data/CONSTITUENTS_PLAN.md 1장) KRX가 유일한
      전 종목 × 과거 일자 소스다.

선행 조건 (CONSTITUENTS_PLAN.md 2~3장):
  1. KRX 무료 회원 가입 + 로그인 — 2026년 개편으로 로그인제. 비로그인 호출은 'LOGOUT' 반환.
  2. 브라우저 개발자 도구에서 로그인 세션의 Cookie 값을 복사해
     external_data/collectors/krx_cookie.txt 에 저장 (비밀값 — .gitignore 등록됨).

규정 게이트: --date 가 2026-07-11(데이터 기준일) 이후면 실행을 거부한다 —
  7/11 이후 생성 데이터 사용 금지(감점) 규정의 코드 강제. 기본 수집일은
  2026-07-10(금, 기준일 직전 거래일).

실행:
  python external_data/collectors/collect_krx_etf_constituents.py --probe            # 프로토콜 확인(1종목)
  python external_data/collectors/collect_krx_etf_constituents.py --isin KR7102110004 # 단일 종목
  python external_data/collectors/collect_krx_etf_constituents.py --all               # active ETF 1,139종목 (~25분)
  python external_data/collectors/collect_krx_etf_constituents.py --merge-only        # 원시 JSON → 통합 CSV 재생성

구조 주의: 테스트(tests/test_collect_krx.py)가 순수 함수를 import 한다 — import 부작용 금지.
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))                 # external_data/collectors/
ROOT = os.path.dirname(os.path.dirname(HERE))                     # repo 루트

# --- .env 파일 지원 -------------------------------------------------------
# 저장소 최상위의 .env 를 읽어 환경변수로 올린다(없으면 아무 일도 안 함).
# 운영체제 환경변수가 이미 있으면 그쪽이 우선이다.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from config.env_loader import load_env  # noqa: E402
load_env()
# --------------------------------------------------------------------------
OUT_BASE = os.path.join(os.path.dirname(HERE), "constituents")    # external_data/constituents/
KR_ETF_CSV = os.path.join(ROOT, "preprocessing", "processed", "PREF01N001_kr_etf_processed.csv")

BASELINE_COMPACT = "20260711"      # 데이터 기준일 — 이후 데이터 수집 금지(규정)
DEFAULT_DATE = "20260710"          # 기준일 직전 거래일(금)
PROBE_ISIN = "KR7102110004"        # TIGER 200 — 검증된 대형 ETF

URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
REFERER = "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030108"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
# 구성종목(PDF) 통계 화면의 bld 후보 — 개편으로 바뀌었을 수 있어 --probe 로 유효한 것을 자동 채택.
# 전부 실패하면 브라우저 Network 탭에서 실제 bld 를 확인해 --bld 로 지정한다(PLAN 3장).
BLD_CANDIDATES = [
    "dbms/MDC/STAT/standard/MDCSTAT05001",
    "dbms/MDC/STAT/standard/MDCSTAT04801",
]
COOKIE_FILE_DEFAULT = os.path.join(HERE, "krx_cookie.txt")


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------

def baseline_ok(date_compact, allow_post_baseline=False):
    """수집 기준일 게이트 — 7/11 이후면 False (allow 플래그로만 해제)."""
    if not (len(date_compact) == 8 and date_compact.isdigit()):
        return False
    return allow_post_baseline or date_compact <= BASELINE_COMPACT


def classify_response(text):
    """KRX 응답 분류 → (종류, 행 목록|None). 종류: 'logout' | 'rows' | 'empty' | 'error'."""
    body = (text or "").strip()
    if body.upper() == "LOGOUT":
        return "logout", None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return "error", None
    if isinstance(data, dict):
        for key in ("output", "OutBlock_1", "block1"):
            rows = data.get(key)
            if isinstance(rows, list):
                return ("rows", rows) if rows else ("empty", [])
    return "error", None


def merge_rows(raw_records):
    """[(isin, etf_name, rows)] → 통합 CSV 행 목록. 원시 컬럼명 그대로 보존 + 식별자 부착."""
    fieldnames, out = [], []
    for isin, name, rows in raw_records:
        for row in rows:
            for k in row:
                if k not in fieldnames:
                    fieldnames.append(k)
            merged = {"etf_isin": isin, "etf_name": name}
            merged.update(row)
            out.append(merged)
    return ["etf_isin", "etf_name"] + fieldnames, out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def read_cookie(path):
    if os.environ.get("KRX_COOKIE"):
        return os.environ["KRX_COOKIE"].strip()
    if not os.path.exists(path):
        sys.exit(f"쿠키 파일이 없다: {path}\n→ KRX 로그인 후 브라우저 Cookie 값을 저장할 것 (CONSTITUENTS_PLAN.md 3장)")
    with io.open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().strip()
    return raw[len("Cookie:"):].strip() if raw.lower().startswith("cookie:") else raw


def request_pdf(bld, isin, date_compact, cookie, timeout=30):
    """구성종목 1건 요청 → 응답 본문 텍스트."""
    payload = urllib.parse.urlencode({
        "bld": bld, "locale": "ko_KR", "trdDd": date_compact, "isuCd": isin,
        "share": "1", "money": "1", "csvxls_isNo": "false",
    }).encode()
    req = urllib.request.Request(URL, data=payload, headers={
        "User-Agent": UA, "Referer": REFERER, "Cookie": cookie,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def probe(cookie, isin, date_compact, bld_override=None):
    """bld 후보를 순회해 유효 응답을 주는 코드를 찾는다 → (bld, rows) 또는 종료."""
    candidates = [bld_override] if bld_override else BLD_CANDIDATES
    http_errors = 0
    for bld in candidates:
        try:
            text = request_pdf(bld, isin, date_compact, cookie)
        except urllib.error.HTTPError as e:
            # 8/13 실측: 대량 수집 중 776번째부터 전 요청 HTTP 400 — KRX 측 요청 제한(차단).
            # LOGOUT(쿠키 문제)과 구분되는 별도 상태다. 냉각 후 재시도해야 한다.
            http_errors += 1
            print(f"  [probe] bld={bld} → HTTP {e.code} ({e.reason})")
            time.sleep(1.0)
            continue
        kind, rows = classify_response(text)
        print(f"  [probe] bld={bld} → {kind}" + (f" ({len(rows)}행)" if rows else ""))
        if kind == "logout":
            sys.exit("응답 LOGOUT — 쿠키가 만료됐거나 로그인 세션이 아니다. 쿠키를 갱신할 것 (PLAN 3장)")
        if kind == "rows":
            return bld, rows
        time.sleep(1.0)
    if http_errors == len(candidates):
        sys.exit("전 후보가 HTTP 오류 — KRX 요청 제한(차단) 상태로 보인다. "
                 "30~60분 냉각 후 재시도하고, 재개 시 --sleep 2.0 이상을 권장 (PLAN 3장)")
    sys.exit("모든 bld 후보 실패 — 브라우저 Network 탭에서 실제 bld 를 확인해 --bld 로 지정할 것 (PLAN 3장)")


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------

def load_targets(limit=None):
    """전처리 국내ETF에서 수집 대상(active + ETF) 추출 — ETN 은 구성종목 개념이 달라 제외."""
    import pandas as pd
    df = pd.read_csv(KR_ETF_CSV, dtype=str)
    df = df[(df["drv_instrument_type"] == "ETF") & (df["drv_listing_status"] == "active")]
    targets = list(zip(df["pd_itm_no"], df["pd_nm"].str.strip()))
    return targets[:limit] if limit else targets


def collect(targets, bld, date_compact, cookie, sleep_s, max_consec_fail=10):
    raw_dir = os.path.join(OUT_BASE, "raw", date_compact)
    os.makedirs(raw_dir, exist_ok=True)
    done = skipped = failed = consec_fail = 0
    for i, (isin, name) in enumerate(targets, 1):
        path = os.path.join(raw_dir, f"{isin}.json")
        if os.path.exists(path):                       # 재개(resume): 이미 수집한 종목은 건너뜀
            skipped += 1
            continue
        try:
            text = request_pdf(bld, isin, date_compact, cookie)
        except (urllib.error.URLError, OSError) as e:
            print(f"  [{i}/{len(targets)}] {isin} 요청 실패({e}) — 5초 후 1회 재시도")
            time.sleep(5.0)
            try:
                text = request_pdf(bld, isin, date_compact, cookie)
            except (urllib.error.URLError, OSError):
                failed += 1
                consec_fail += 1
                # 8/13 실측: 776번째부터 전 요청 HTTP 400 — 개별 종목 문제가 아니라
                # KRX 측 요청 제한(차단)이다. 연속 실패가 임계를 넘으면 즉시 중단해
                # 무의미한 요청(차단 연장 위험)을 멈춘다. 재개는 같은 명령(resume).
                if consec_fail >= max_consec_fail:
                    print(f"\n연속 {consec_fail}회 요청 실패 — KRX 요청 제한(차단)으로 판단해 중단한다. "
                          f"({done}건 수집 후. 30~60분 냉각 후 같은 명령으로 재개, --sleep 2.0 이상 권장)")
                    break
                continue
        kind, rows = classify_response(text)
        if kind == "logout":
            print(f"\n세션 만료(LOGOUT) — {done}건 수집 후 중단. 쿠키 갱신 후 같은 명령으로 재개하면 이어서 수집한다.")
            break
        if kind == "error":
            failed += 1
            consec_fail += 1
            if consec_fail >= max_consec_fail:
                print(f"\n연속 {consec_fail}회 비정상 응답 — 차단 또는 프로토콜 변경으로 판단해 중단한다. "
                      f"({done}건 수집 후. 냉각 후 재개 또는 --probe 재확인)")
                break
        else:                                          # rows 또는 empty — empty 도 "해당일 구성 없음" 증거로 저장
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"etf_isin": isin, "etf_name": name, "trdDd": date_compact,
                           "bld": bld, "source": URL, "rows": rows}, fh, ensure_ascii=False)
            done += 1
            consec_fail = 0
        if i % 50 == 0:
            print(f"  진행 {i}/{len(targets)} (수집 {done} · 스킵 {skipped} · 실패 {failed})")
        time.sleep(sleep_s)
    print(f"수집 종료 — 신규 {done} · 스킵(기수집) {skipped} · 실패 {failed}")


def merge(date_compact):
    raw_dir = os.path.join(OUT_BASE, "raw", date_compact)
    if not os.path.isdir(raw_dir):
        sys.exit(f"원시 폴더가 없다: {raw_dir}")
    records = []
    for fname in sorted(os.listdir(raw_dir)):
        if fname.endswith(".json"):
            with io.open(os.path.join(raw_dir, fname), "r", encoding="utf-8") as fh:
                d = json.load(fh)
            records.append((d["etf_isin"], d.get("etf_name", ""), d.get("rows") or []))
    fieldnames, rows = merge_rows(records)
    out_csv = os.path.join(OUT_BASE, f"constituents_{date_compact}.csv")
    with io.open(out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    meta = {"source": "KRX 정보데이터시스템 (data.krx.co.kr)", "endpoint": URL,
            "trdDd": date_compact, "etf_count": len(records), "row_count": len(rows),
            "baseline_rule": f"데이터 기준일 {BASELINE_COMPACT} 이전만 수집(규정 게이트)"}
    with io.open(os.path.join(OUT_BASE, f"collection_meta_{date_compact}.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"통합 완료 → {out_csv} (ETF {len(records)}종목 · 구성 {len(rows)}행)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="KRX ETF 구성종목(PDF) 수집기")
    ap.add_argument("--date", default=DEFAULT_DATE, help=f"조회 기준일 YYYYMMDD (기본 {DEFAULT_DATE})")
    ap.add_argument("--probe", action="store_true", help="1종목으로 프로토콜(bld) 확인만")
    ap.add_argument("--isin", help="단일 종목 수집 (ISIN, 예: KR7102110004)")
    ap.add_argument("--all", action="store_true", help="active 국내ETF 전 종목 수집")
    ap.add_argument("--bld", help="bld 코드 수동 지정 (브라우저 Network 탭에서 확인)")
    ap.add_argument("--cookie-file", default=COOKIE_FILE_DEFAULT)
    ap.add_argument("--sleep", type=float, default=1.0, help="요청 간 대기 초 (기본 1.0 — 차단 후 재개는 2.0 이상 권장)")
    ap.add_argument("--max-consec-fail", type=int, default=10,
                    help="연속 실패 시 차단으로 판단해 중단하는 임계 (기본 10)")
    ap.add_argument("--limit", type=int, help="--all 대상 수 제한 (스모크용)")
    ap.add_argument("--merge-only", action="store_true", help="수집 없이 원시 JSON → CSV 통합만")
    ap.add_argument("--allow-post-baseline", action="store_true",
                    help="기준일(7/11) 이후 날짜 허용 — 평가 외 실험 전용, 산출물 혼입 금지")
    args = ap.parse_args(argv)

    if not baseline_ok(args.date, args.allow_post_baseline):
        sys.exit(f"기준일 게이트: {args.date} 는 데이터 기준일({BASELINE_COMPACT}) 이후이거나 형식 오류다. "
                 f"7/11 이후 데이터는 감점 리스크(규정) — 의도적 실험이면 --allow-post-baseline.")
    if args.merge_only:
        merge(args.date)
        return

    cookie = read_cookie(args.cookie_file)
    bld, rows = probe(cookie, args.isin or PROBE_ISIN, args.date, args.bld)
    print(f"프로토콜 확인 — bld={bld}, 표본 {len(rows)}행")
    if args.probe:
        print(json.dumps(rows[:3], ensure_ascii=False, indent=2))
        return

    if args.all:
        targets = load_targets(args.limit)
        print(f"수집 대상: active ETF {len(targets)}종목 · 기준일 {args.date} · 예상 {len(targets) * (args.sleep + 0.3) / 60:.0f}분")
    elif args.isin:
        targets = [(args.isin, "")]
    else:
        sys.exit("--probe / --isin / --all / --merge-only 중 하나를 지정할 것")
    collect(targets, bld, args.date, cookie, args.sleep, args.max_consec_fail)
    merge(args.date)


if __name__ == "__main__":
    main()
