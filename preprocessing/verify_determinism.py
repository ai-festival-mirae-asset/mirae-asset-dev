# -*- coding: utf-8 -*-
"""전처리 파이프라인 결정성(멱등) 검증 — N회 실행 SHA-256 비교 + 불일치 증거 보존.

무엇: preprocess.py 를 같은 입력으로 N회(기본 2회) 연속 실행하고, processed/ 의
      모든 CSV 산출물(processed 4종 + quarantine 2종 + preprocessing_report)의
      SHA-256 해시가 실행 간 완전히 동일한지 확인한다. 불일치가 나오면 양쪽
      실행의 실파일과 셀 단위 diff, 입력 xlsx 해시를 determinism_diag/ 에 보존한다.
왜: "같은 입력이면 항상 같은 출력"(PREPROCESSING_METHOD.md 원칙 4)은 지금까지
    선언뿐이었다. 8/5 교차검증에서 dev-kyung 의 검증 방식(결정성 확인을 재실행
    절차에 포함)을 채택했다 — 딕셔너리 순회·set 순서·타임스탬프 같은 비결정
    요소가 파이프라인에 스며들면 산출물 diff 로 즉시 드러난다.

증거 보존을 넣은 이유(8/7 결함 수정): 8/7 검증 세션에서 채권 산출물 해시가 1회
    불일치(run1 805b0caa... vs run2 0610d8e7...)했으나, 초판 스크립트는 해시만
    저장하고 run2 가 run1 파일을 덮어쓰는 구조여서 "어느 셀이 달랐는지"를 남기지
    못했다. 이후 9회 이상 재실행에서 미재현(간헐적) — 원인 규명에는 재발 시점의
    실파일 diff 가 필수이므로, 이 판은 불일치 순간의 증거를 자동 보존한다.
    입력 xlsx 해시를 매회 함께 채집하는 것은 "입력 자체가 변한 환경 간섭"(클라우드
    동기화·원본 교체)과 "파이프라인 비결정"을 구분하기 위해서다.

실행: python preprocessing/verify_determinism.py [--runs N]
      (원본 위치가 datasets/ 가 아니면 MIRAE_DATASETS 환경변수 지정 — preprocess.py 와 동일)
종료코드: 0=결정성 확인(증거 폴더 자동 삭제), 1=불일치(증거 보존) 또는 실행 실패.
"""
import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))   # preprocessing/
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "processed")
SCRIPT = os.path.join(HERE, "preprocess.py")
DS = os.environ.get("MIRAE_DATASETS") or os.path.join(ROOT, "datasets")  # preprocess.py 와 동일 규칙
DIAG = os.path.join(HERE, "determinism_diag")       # 불일치 증거 보존 폴더 (성공 시 자동 삭제)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_inputs():
    """원본 xlsx 해시 채집 — 실행 간 '입력 자체가 변한' 환경 간섭을 산출물 비결정과 구분한다."""
    if not os.path.isdir(DS):
        return {}
    return {f: sha256_of(os.path.join(DS, f))
            for f in sorted(os.listdir(DS)) if f.endswith(".xlsx")}


def run_pipeline(label):
    """파이프라인 1회 실행 후 {csv 파일명: sha256} 스냅샷을 반환한다."""
    print(f"[{label}] preprocess.py 실행 중 ...")
    proc = subprocess.run([sys.executable, SCRIPT], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        sys.exit(f"[{label}] 파이프라인 실행 실패 (exit {proc.returncode})")
    hashes = {f: sha256_of(os.path.join(OUT, f))
              for f in sorted(os.listdir(OUT)) if f.endswith(".csv")}
    if not hashes:
        sys.exit(f"[{label}] processed/ 에 CSV 산출물이 없다")
    print(f"[{label}] CSV {len(hashes)}개 해시 채집 완료")
    return hashes


def snapshot_outputs(dst_dir):
    """processed/ 의 CSV 전부를 dst_dir 로 복사 — 다음 실행이 덮어쓰기 전에 실파일을 남긴다."""
    os.makedirs(dst_dir, exist_ok=True)
    for f in sorted(os.listdir(OUT)):
        if f.endswith(".csv"):
            shutil.copy2(os.path.join(OUT, f), os.path.join(dst_dir, f))


def diff_cells(path_a, path_b, max_records=200):
    """두 CSV 의 셀 단위 diff 목록을 반환한다 — (레코드 리스트, 잘림 여부).

    레코드: {"row": 0-기반 데이터 행번호, "column": 컬럼명, "run_a": 값, "run_b": 값}.
    행수·컬럼 목록이 다르면 row=-1 특수 레코드로 먼저 기록한다.
    왜 셀 좌표인가: 해시 불일치만으로는 원인 규칙을 특정할 수 없다 — 어느 컬럼의
    어느 행이 달랐는지가 남아야 재발 시 해당 파생 규칙(R*)으로 역추적할 수 있다.
    keep_default_na=False + dtype=str 로 읽어 NA 해석 없이 원문 문자열을 그대로 비교한다.
    """
    a = pd.read_csv(path_a, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    b = pd.read_csv(path_b, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    records = []
    if list(a.columns) != list(b.columns):
        records.append({"row": -1, "column": "(컬럼 목록 불일치)",
                        "run_a": ";".join(a.columns), "run_b": ";".join(b.columns)})
    if len(a) != len(b):
        records.append({"row": -1, "column": "(행수 불일치)",
                        "run_a": str(len(a)), "run_b": str(len(b))})
    common = [c for c in a.columns if c in b.columns]
    n = min(len(a), len(b))
    for col in common:
        av = a[col].values[:n]
        bv = b[col].values[:n]
        for i in (av != bv).nonzero()[0]:
            if len(records) >= max_records:
                return records, True
            records.append({"row": int(i), "column": col,
                            "run_a": av[i], "run_b": bv[i]})
    return records, False


def preserve_evidence(run_no, diff_names, input_changed):
    """불일치 순간의 증거를 determinism_diag/ 에 보존한다 (git 추적 제외)."""
    os.makedirs(DIAG, exist_ok=True)
    with open(os.path.join(DIAG, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("*\n")  # 증거는 로컬 조사용 — 저장소에 커밋하지 않는다 (루트 .gitignore 불변)
    run_dir = os.path.join(DIAG, f"run{run_no}")
    snapshot_outputs(run_dir)
    for name in diff_names:
        recs, truncated = diff_cells(os.path.join(DIAG, "run1", name),
                                     os.path.join(run_dir, name))
        diff_path = os.path.join(DIAG, f"celldiff_run1_vs_run{run_no}__{name}")
        pd.DataFrame(recs).to_csv(diff_path, index=False, encoding="utf-8-sig")
        print(f"  셀 diff {len(recs)}건{' (상한 잘림)' if truncated else ''} → {diff_path}")
    meta = [
        f"python: {sys.version}",
        f"pandas: {pd.__version__}",
        f"PYTHONHASHSEED: {os.environ.get('PYTHONHASHSEED', '(미설정=랜덤)')}",
        f"불일치 파일: {', '.join(diff_names)}",
        f"입력 xlsx 해시 변화: {'있음 — 환경 간섭(동기화·원본 교체) 의심' if input_changed else '없음 — 파이프라인 비결정 의심'}",
    ]
    with open(os.path.join(DIAG, "meta.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(meta) + "\n")
    print(f"  실행 메타 → {os.path.join(DIAG, 'meta.txt')}")


def main():
    # Windows 콘솔(cp949) 한글 출력 깨짐 방지 (preprocess.py 와 동일 처리)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    parser = argparse.ArgumentParser(description="전처리 결정성 검증 (N회 실행 SHA-256 비교)")
    parser.add_argument("--runs", type=int, default=2,
                        help="파이프라인 실행 횟수 (기본 2, 간헐 불일치 추적 시 3 이상 권장)")
    args = parser.parse_args()
    if args.runs < 2:
        sys.exit("--runs 는 2 이상이어야 한다 (비교 대상이 필요)")

    if os.path.isdir(DIAG):
        print(f"이전 증거 폴더 {DIAG} 삭제 후 새로 검증한다")
        shutil.rmtree(DIAG)

    inputs_base = hash_inputs()
    base = run_pipeline("1회차")
    # run1 실파일을 즉시 보존 — 다음 실행이 덮어쓰면 불일치 시 비교 대상이 사라진다 (8/7 교훈)
    snapshot_outputs(os.path.join(DIAG, "run1"))

    ok = True
    for k in range(2, args.runs + 1):
        inputs_k = hash_inputs()
        input_changed = inputs_k != inputs_base
        if input_changed:
            print(f"[경고] {k}회차 실행 전 입력 xlsx 해시가 1회차와 다르다 — 환경 간섭(원본 변경) 의심")
        cur = run_pipeline(f"{k}회차")
        if set(base) != set(cur):
            ok = False
            print("산출물 파일 목록이 실행 간 다르다:", set(base) ^ set(cur))
        diff_names = []
        for name in sorted(set(base) & set(cur)):
            same = base[name] == cur[name]
            mark = "OK " if same else "DIFF"
            print(f"  [{mark}] {name}  {base[name][:16]}...")
            if not same:
                diff_names.append(name)
        if diff_names:
            ok = False
            print(f"\n[불일치] {k}회차에서 {len(diff_names)}개 파일이 1회차와 다르다 — 증거 보존:")
            preserve_evidence(k, diff_names, input_changed)

    if not ok:
        sys.exit(f"결정성 위반: 같은 입력에서 다른 산출물이 생성됐다. {DIAG} 의 셀 diff·메타로 원인을 조사하라.")
    shutil.rmtree(DIAG, ignore_errors=True)  # 성공 — run1 스냅샷 정리
    print(f"\n결정성 확인: {args.runs}회 실행 산출물 {len(base)}개 CSV SHA-256 전부 동일 (멱등)")


if __name__ == "__main__":
    main()
