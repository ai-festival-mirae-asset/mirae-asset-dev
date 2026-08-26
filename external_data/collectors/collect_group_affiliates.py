# -*- coding: utf-8 -*-
"""
공정거래위원회 대규모기업집단 소속회사(계열사) 수집기 — 자회사 질의의 실데이터화 (8/26 준비).

무엇: 공공데이터포털(data.go.kr)의 공정위 「지정된 대규모기업집단 소속회사 조회 서비스」
      (데이터셋 15091891)를 호출해 그룹명→소속회사 목록 CSV 를 만든다.
왜  : "LG의 자회사를 담은 ETF" 질의를 지금은 회사명 접두(이름이 LG로 시작) 근사로 답한다.
      공정위 소속회사(계열사) 명단이 있으면 '진짜 계열 관계'로 바꿀 수 있다(대회 규정상
      외부 데이터 수집 허용 — 8/18 공식 자료).
주의: '자회사'(지분 보유 종속회사)와 '계열사'(동일 기업집단 소속회사)는 법적으로 다르다.
      이 수집분은 계열사 명단이므로 답변에는 "공정위 기업집단 소속회사 기준"을 명시한다.

사용 준비(사용자 작업 — 무료, 계정 필요):
  1) data.go.kr 회원가입 → 데이터셋 15091891 「활용신청」 → 승인 후 일반 인증키(serviceKey) 발급
  2) 활용신청 화면의 스웨거(OpenAPI 명세)에서 소속회사 조회 요청 주소를 복사
  3) 환경변수 설정(키 값은 코드·문서·대화에 절대 적지 않는다):
       DATA_GO_KR_API_KEY=<인증키>
       FTC_AFFILIATES_ENDPOINT=<스웨거의 소속회사 조회 URL>
       FTC_PUBLICYM_ENDPOINT=<스웨거의 공개년월 목록 URL>   (선택)
실행:
  python external_data/collectors/collect_group_affiliates.py --probe          # 연결 확인
  python external_data/collectors/collect_group_affiliates.py --ym 202605      # 수집
출력: external_data/group_affiliates/group_affiliates_<ym>.csv + 수집 메타 json
"""
import argparse
import csv
import datetime
import io
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "group_affiliates"))

ENV_KEY = "DATA_GO_KR_API_KEY"
ENV_ENDPOINT = "FTC_AFFILIATES_ENDPOINT"
ENV_YM_ENDPOINT = "FTC_PUBLICYM_ENDPOINT"
PAGE_SIZE = 500


def parse_items(xml_text):
    """응답 XML → (행 dict 목록, 결과 코드, 전체 건수). 순수 함수 — 오프라인 테스트 대상.

    공공데이터포털 표준 응답(header/resultCode + body/items/item)을 우선 따르되,
    태그 이름은 스웨거 확정 전까지 미리 단정하지 않고 item 의 모든 자식 태그를 그대로 열로 쓴다.
    """
    root = ET.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or root.findtext(".//returnCode") or "").strip()
    total_text = (root.findtext(".//totalCount") or "").strip()
    total = int(total_text) if total_text.isdigit() else None
    rows = []
    for item in root.iter("item"):
        row = {}
        for child in item:
            row[child.tag] = (child.text or "").strip()
        if row:
            rows.append(row)
    return rows, code, total


def fetch(endpoint, params, timeout=30.0):
    import httpx
    key = os.environ.get(ENV_KEY, "")
    if not key:
        raise SystemExit(f"환경변수 {ENV_KEY} 가 없습니다 — data.go.kr 인증키를 설정하세요(값은 출력 금지).")
    q = dict(params)
    q["serviceKey"] = key
    r = httpx.get(endpoint, params=q, timeout=timeout)
    r.raise_for_status()
    return r.text


def collect(endpoint, ym):
    """공개년월 ym 의 소속회사 전 페이지 수집 → 행 목록."""
    rows, page = [], 1
    while True:
        text = fetch(endpoint, {"pageNo": page, "numOfRows": PAGE_SIZE, "publicYm": ym,
                                "resultType": "xml"})
        got, code, total = parse_items(text)
        if code and code not in ("00", "0", "NORMAL_CODE"):
            raise SystemExit(f"API 오류 코드 {code} — 스웨거의 요청 파라미터 이름(publicYm 등)을 확인하세요.")
        rows.extend(got)
        if not got or (total is not None and len(rows) >= total) or len(got) < PAGE_SIZE:
            return rows


def save(rows, ym):
    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, f"group_affiliates_{ym}.csv")
    headers = sorted({k for r in rows for k in r})
    with io.open(out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    meta = {"source": "data.go.kr 공정위 기업집단포털 소속회사 조회(15091891)",
            "public_ym": ym, "rows": len(rows), "columns": headers,
            "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "note": "serviceKey 는 환경변수로만 사용 — 파일·로그에 기록하지 않음"}
    with io.open(out_csv.replace(".csv", "_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return out_csv, len(rows)


def main():
    ap = argparse.ArgumentParser(description="공정위 대규모기업집단 소속회사 수집기")
    ap.add_argument("--ym", help="공개년월(YYYYMM) — 예: 202605")
    ap.add_argument("--probe", action="store_true", help="연결·인증만 확인(1페이지 1건)")
    args = ap.parse_args()

    endpoint = os.environ.get(ENV_ENDPOINT, "")
    if not endpoint:
        raise SystemExit(f"환경변수 {ENV_ENDPOINT} 가 없습니다 — 활용신청 승인 후 스웨거의 "
                         "소속회사 조회 URL 을 설정하세요(모듈 상단 '사용 준비' 참조).")
    if args.probe:
        ym = args.ym or f"{datetime.date.today().year}05"
        text = fetch(endpoint, {"pageNo": 1, "numOfRows": 1, "publicYm": ym, "resultType": "xml"})
        rows, code, total = parse_items(text)
        print(f"연결 확인 — 결과코드 {code or '(없음)'} · 표본 {len(rows)}건 · 전체 {total}건")
        if rows:
            print("표본 열:", sorted(rows[0]))
        return
    if not args.ym:
        raise SystemExit("--ym YYYYMM 이 필요합니다(예: 202605 — 매년 5/1 공개).")
    rows = collect(endpoint, args.ym)
    out_csv, n = save(rows, args.ym)
    print(f"수집 완료: {n}행 → {out_csv}")


if __name__ == "__main__":
    main()
