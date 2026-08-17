# -*- coding: utf-8 -*-
"""
벡터 채널 flat 스토어 — build_index.py 산출물(npz+meta)을 읽어 코사인 top-k 검색.

왜 flat(numpy brute force)인가: 코퍼스 5,566×1024 는 전수 내적이 ms 단위라
ANN 인덱스(FAISS 등)가 불필요하다 — 신규 의존성 없이 numpy 로 충분(NCP 4GB 제약에도 유리).
"""
import io
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NPZ_DEFAULT = os.path.join(HERE, "output", "index_global_etf.npz")
META_DEFAULT = os.path.join(HERE, "output", "index_meta_global_etf.json")


def normalize(matrix):
    """행 단위 L2 정규화 — 정규화 후 내적 = 코사인 유사도. 영벡터는 0 유지."""
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def top_k(query_vec, matrix_normed, k):
    """(정규화된) 행렬에서 코사인 상위 k 의 (index, score) 목록."""
    q = np.asarray(query_vec, dtype=np.float32)
    qn = np.linalg.norm(q)
    if qn == 0:
        return []
    scores = matrix_normed @ (q / qn)
    k = min(k, len(scores))
    idx = np.argpartition(-scores, range(k))[:k]
    return [(int(i), float(scores[i])) for i in idx]


class VectorStore:
    """npz(ids, matrix) + meta(mapping) 로드 — sha → 상품 목록 매핑 포함."""

    def __init__(self, ids, matrix, meta):
        self.ids = list(ids)
        self.matrix = normalize(matrix.astype(np.float32))
        self.meta = meta
        self.mapping = meta.get("mapping", {})

    @classmethod
    def load(cls, npz_path=NPZ_DEFAULT, meta_path=META_DEFAULT):
        if not os.path.exists(npz_path):
            raise FileNotFoundError(f"인덱스가 없다: {npz_path} — 먼저 python vector/build_index.py --all")
        data = np.load(npz_path, allow_pickle=False)
        with io.open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        return cls(data["ids"], data["matrix"], meta)

    def search(self, query_vec, k=5):
        """쿼리 벡터 → [(sha, score, [상품{pd_itm_no, pd_nm}])] 상위 k."""
        out = []
        for i, score in top_k(query_vec, self.matrix, k):
            sha = str(self.ids[i])
            out.append((sha, score, self.mapping.get(sha, [])))
        return out
