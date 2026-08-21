# -*- coding: utf-8 -*-
"""
벡터 채널 인덱스 빌더 — 해외+국내 ETP 전략 서술(cu_strtegy) → CLOVA Embedding v2 → flat 인덱스.

무엇: 해외ETF 전략 서술(cu_strtegy 5,638건) + 국내ETP 합성 문장(1,733건 — 8/22 확장,
      KG_NEXT 2순위)을 임베딩해 vector/output/ 에 저장한다.
      검색은 vector_store.py(numpy flat 코사인).
왜  : 중-1("구조·전략 동향")·상-1("테마 연결") 유형의 의미 검색 기반. 국내 확장은
      테마 사전에 없는 새 주제가 실전에 나왔을 때 국내 상품 쪽 안전망이 된다.
      **국내 cu_strtegy 는 서술문이 아니라 분류값 4종(실물복제·합성복제·액티브·C)뿐
      (8/22 실측 — 해외와 컬럼명만 같고 내용이 다름).** 그래서 국내는 정식명·약칭·
      기초지수(있으면)로 합성한 문장을 쓰고, 분류값은 코퍼스에서 제외한다.
      FAISS 등 신규 의존성 없이 numpy 만 쓴다 — 7천×1024 도 brute force 로 충분(ms).

산출물 (vector/output/ — kg/output 과 같이 gitignore, 재생성 가능하나 API 비용 有):
  embeddings_corpus.jsonl  텍스트 해시별 벡터(재개용 append 저널)
  index_corpus.npz         ids(text_sha 배열) + matrix(float32 [N,1024])
  index_meta_corpus.json   모델·차원·건수·소스·텍스트해시→상품 매핑(table 포함)
  (8/22 이전 이름 embeddings_global_etf.jsonl 등은 첫 실행 때 자동 이어받는다 —
   기존 임베딩 5,566건을 다시 API 로 만들지 않는다)

실행:
  python vector/build_index.py --probe 5     # 레이트리밋·지연 실측(5건)만
  python vector/build_index.py --all         # 전체 (재개 가능 — 새 텍스트만 임베딩)
  python vector/build_index.py --finalize    # 저널 → npz/meta 재조립만

주의: 임베딩 모델은 질의 시점과 동일해야 한다(Embedding v2 고정, meta 에 기록).
구조: 테스트가 순수 함수를 import 한다 — import 부작용 금지.
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))            # vector/
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

GLOBAL_ETF_CSV = os.path.join(ROOT, "preprocessing", "processed",
                              "PREF02N001_global_etf_processed.csv")
KR_ETP_CSV = os.path.join(ROOT, "preprocessing", "processed",
                          "PREF01N001_kr_etf_processed.csv")
OUT_DIR = os.path.join(HERE, "output")
JOURNAL = os.path.join(OUT_DIR, "embeddings_corpus.jsonl")
NPZ = os.path.join(OUT_DIR, "index_corpus.npz")
META = os.path.join(OUT_DIR, "index_meta_corpus.json")
JOURNAL_LEGACY = os.path.join(OUT_DIR, "embeddings_global_etf.jsonl")   # 8/22 이전 이름

MODEL = "clova-embedding-v2"   # meta 기록용 표기 — 질의 시 동일 모델 필수 (1024차원)
DIM = 1024
AS_OF = "2026-07-11"


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------

def text_sha(text):
    """텍스트 정규화(策 없이 strip만) 후 SHA-256 16자리 — 저널·인덱스의 키."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def build_corpus(rows):
    """[(pd_itm_no, pd_nm, cu_strtegy[, table])] → (고유텍스트 {sha: text}, 매핑 {sha: [상품]}).

    같은 서술을 쓰는 상품이 여럿이면(시리즈 상품) 임베딩은 1회, 매핑에 전원 기록.
    4번째 값 table("kr_etp"/"global_etf")이 있으면 상품 항목에 담는다(8/22 국내 확장) —
    검색 채널이 근거의 출처 테이블·시장 표기를 상품별로 정확히 남기기 위해서다.
    """
    texts, mapping = {}, {}
    for row in rows:
        pid, name, strategy = row[0], row[1], row[2]
        table = row[3] if len(row) > 3 else None
        if not strategy or not str(strategy).strip():
            continue
        t = str(strategy).strip()
        sha = text_sha(t)
        texts.setdefault(sha, t)
        entry = {"pd_itm_no": pid, "pd_nm": name}
        if table:
            entry["table"] = table
        mapping.setdefault(sha, []).append(entry)
    return texts, mapping


def load_done_shas(journal_path):
    """저널에서 이미 임베딩된 sha 집합 — 재개(resume)의 근거."""
    done = set()
    if os.path.exists(journal_path):
        with io.open(journal_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    done.add(json.loads(line)["sha"])
    return done


# ---------------------------------------------------------------------------
# 임베딩 실행
# ---------------------------------------------------------------------------

INDEX_SENTINEL_SUBSTRINGS = ("Index is not provided", "Index is not available")


def _cell(v):
    """pandas 결측(float NaN) → 빈 문자열."""
    return v.strip() if isinstance(v, str) else ""


def kr_synth_text(name, abrv, idx):
    """국내 ETP 의미 검색용 합성 문장 — 정식명 · 약칭 · 기초지수(있으면).

    국내 cu_strtegy 는 분류값 4종뿐이라(8/22 실측) 서술문 대신 이름 재료로 문장을
    합성한다. 한↔영 교차 의미검색("미국 소형주"→Small Cap)이 실증된 임베딩이라
    이름 안의 테마 어휘도 뜻으로 검색된다.
    """
    name, abrv, idx = _cell(name), _cell(abrv), _cell(idx)
    if not (name or abrv):
        return ""
    parts = [name or abrv]
    if abrv and abrv != name:
        parts.append(f"약칭 {abrv}")
    if idx and not any(t in idx for t in INDEX_SENTINEL_SUBSTRINGS):
        parts.append(f"기초지수 {idx}")
    return " · ".join(parts)


def load_rows():
    import pandas as pd
    gl = pd.read_csv(GLOBAL_ETF_CSV, dtype=str)
    rows = list(zip(gl["pd_itm_no"], gl["pd_nm"], gl["cu_strtegy"], ["global_etf"] * len(gl)))
    kr = pd.read_csv(KR_ETP_CSV, dtype=str)
    for pid, nm, ab, idx in zip(kr["pd_itm_no"], kr["pd_nm"], kr["pd_abrv_nm"], kr["cu_base_index"]):
        rows.append((pid, _cell(nm), kr_synth_text(nm, ab, idx), "kr_etp"))
    return rows


def migrate_legacy_journal():
    """8/22 이전 저널(embeddings_global_etf.jsonl)을 새 이름으로 이어받는다 — API 재호출 방지."""
    if os.path.exists(JOURNAL) or not os.path.exists(JOURNAL_LEGACY):
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    with io.open(JOURNAL_LEGACY, "r", encoding="utf-8") as src, \
            io.open(JOURNAL, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line)
    print(f"저널 이어받기: {os.path.basename(JOURNAL_LEGACY)} → {os.path.basename(JOURNAL)}")
    return True


def run_embedding(texts, done, sleep_s, max_consec_fail=5, limit=None, log_every=100):
    """고유 텍스트를 순차 임베딩해 저널에 append. 지연 통계를 출력한다(레이트리밋 실측)."""
    from agent.clova_embedding import ClovaEmbeddingClient, ClovaEmbeddingError
    client = ClovaEmbeddingClient()
    todo = [(sha, t) for sha, t in sorted(texts.items()) if sha not in done]
    if limit:
        todo = todo[:limit]
    print(f"임베딩 대상 {len(todo):,}건 (기완료 {len(done):,} 스킵) · 간격 {sleep_s}s")
    os.makedirs(OUT_DIR, exist_ok=True)
    latencies, tokens_total, consec_fail = [], 0, 0
    with io.open(JOURNAL, "a", encoding="utf-8") as fh:
        for i, (sha, text) in enumerate(todo, 1):
            t0 = time.monotonic()
            try:
                vec, tokens = client.embed(text)
            except ClovaEmbeddingError as e:
                consec_fail += 1
                print(f"  [{i}/{len(todo)}] 실패({e}) — 5초 대기 후 계속")
                time.sleep(5.0)
                if consec_fail >= max_consec_fail:
                    print(f"연속 {consec_fail}회 실패 — 중단(재실행 시 이어서). 레이트리밋이면 간격을 늘릴 것")
                    break
                continue
            consec_fail = 0
            latencies.append(time.monotonic() - t0)
            tokens_total += tokens or 0
            fh.write(json.dumps({"sha": sha, "vector": vec, "tokens": tokens},
                                ensure_ascii=False) + "\n")
            if i % log_every == 0:
                import statistics
                print(f"  진행 {i:,}/{len(todo):,} · 지연 p50 {statistics.median(latencies):.2f}s "
                      f"· 누적 토큰 {tokens_total:,}")
            time.sleep(sleep_s)
    if latencies:
        import statistics
        print(f"완료분 {len(latencies):,}건 · 지연 p50 {statistics.median(latencies):.2f}s / "
              f"max {max(latencies):.2f}s · 토큰 {tokens_total:,}")


def finalize(texts, mapping):
    """저널 → npz(행렬) + meta(매핑) 조립. 저널에 없는 텍스트는 집계만 보고."""
    import numpy as np
    vectors, ids = [], []
    with io.open(JOURNAL, "r", encoding="utf-8") as fh:
        seen = set()
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d["sha"] in seen:          # 중복 append 방어 — 마지막이 아닌 첫 기록 유지
                continue
            if d["sha"] not in texts:     # 현재 코퍼스 밖(예: 8/22 제외한 국내 분류값) — 인덱스 미포함
                continue
            seen.add(d["sha"])
            ids.append(d["sha"])
            vectors.append(np.asarray(d["vector"], dtype=np.float32))
    matrix = np.vstack(vectors) if vectors else np.zeros((0, DIM), dtype=np.float32)
    np.savez_compressed(NPZ, ids=np.array(ids), matrix=matrix)
    missing = sorted(set(texts) - set(ids))
    by_table = {}
    for products in mapping.values():
        for p in products:
            t = p.get("table", "global_etf")
            by_table[t] = by_table.get(t, 0) + 1
    meta = {"model": MODEL, "dim": DIM, "as_of": AS_OF,
            "source": "PREF02N001 해외ETF + PREF01N001 국내ETP cu_strtegy (8/22 국내 확장)",
            "unique_texts": len(texts), "embedded": len(ids), "missing": len(missing),
            "products_by_table": by_table,
            "mapping": mapping}
    with io.open(META, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"인덱스 조립 → {NPZ} ({len(ids):,}×{DIM}) · 미임베딩 {len(missing)}건 · meta 저장")


def main(argv=None):
    ap = argparse.ArgumentParser(description="해외+국내 ETP 전략 서술 임베딩 인덱스 빌더")
    ap.add_argument("--probe", type=int, help="N건만 실측(지연·토큰)하고 종료")
    ap.add_argument("--all", action="store_true", help="전체 임베딩(재개 가능) + 조립")
    ap.add_argument("--finalize", action="store_true", help="저널 → npz/meta 조립만")
    ap.add_argument("--sleep", type=float, default=0.15, help="요청 간 대기 초 (기본 0.15)")
    args = ap.parse_args(argv)

    migrate_legacy_journal()
    rows = load_rows()
    texts, mapping = build_corpus(rows)
    print(f"코퍼스: 서술 보유 상품 {sum(len(v) for v in mapping.values()):,} · 고유 텍스트 {len(texts):,}")
    if args.finalize:
        finalize(texts, mapping)
        return
    done = load_done_shas(JOURNAL)
    if args.probe:
        run_embedding(texts, done, args.sleep, limit=args.probe, log_every=1)
        return
    if args.all:
        run_embedding(texts, done, args.sleep)
        finalize(texts, mapping)
        return
    ap.error("--probe N / --all / --finalize 중 하나를 지정할 것")


if __name__ == "__main__":
    main()
