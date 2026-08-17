# -*- coding: utf-8 -*-
"""vector/ (벡터 채널) · agent/clova_embedding.py 테스트.

무엇: ① 코퍼스 구성·재개 순수 함수 ② flat 코사인 검색 ③ 임베딩 클라이언트의
      경로 후보 순회·서비스 앱 부재(40100) 안내 — 전부 mock, 실 API 불필요.
왜  : 임베딩 서비스 앱 승인(사용자 액션) 전에 코드 경로를 검증해 두기 위함.
      승인 후에는 `python vector/build_index.py --probe 5` 로 실측한다.
"""
import io
import json
import os

import httpx
import numpy as np
import pytest

from agent.clova_embedding import (ClovaEmbeddingClient, ClovaEmbeddingError,
                                   EMBEDDING_DIM)
from vector.build_index import build_corpus, load_done_shas, text_sha
from vector.vector_store import VectorStore, normalize, top_k


# ---------------------------------------------------------------------------
# 1. 코퍼스·재개 순수 함수
# ---------------------------------------------------------------------------

def test_text_sha_strip_invariant():
    assert text_sha(" abc \n") == text_sha("abc")
    assert len(text_sha("abc")) == 16


def test_build_corpus_dedupes_and_maps_all_products():
    rows = [("A1", "ETF 하나", "Same strategy."),
            ("A2", "ETF 둘", " Same strategy.  "),      # 공백 변형 — 같은 텍스트
            ("A3", "ETF 셋", "Other strategy."),
            ("A4", "서술 없음", None), ("A5", "빈 서술", "  ")]
    texts, mapping = build_corpus(rows)
    assert len(texts) == 2
    sha = text_sha("Same strategy.")
    assert [p["pd_itm_no"] for p in mapping[sha]] == ["A1", "A2"]


def test_load_done_shas_roundtrip(tmp_path):
    j = tmp_path / "journal.jsonl"
    j.write_text(json.dumps({"sha": "aa", "vector": [0.0]}) + "\n"
                 + json.dumps({"sha": "bb", "vector": [0.0]}) + "\n", encoding="utf-8")
    assert load_done_shas(str(j)) == {"aa", "bb"}
    assert load_done_shas(str(tmp_path / "none.jsonl")) == set()


# ---------------------------------------------------------------------------
# 2. flat 코사인 검색
# ---------------------------------------------------------------------------

def test_top_k_cosine_order():
    m = normalize(np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype=np.float32))
    hits = top_k([1.0, 0.1], m, 2)
    assert [i for i, _ in hits] == [0, 2]          # 동일 방향 > 45도 > 직교
    assert hits[0][1] > hits[1][1]
    assert top_k([0.0, 0.0], m, 2) == []           # 영벡터 질의 방어


def test_vector_store_search_maps_products():
    ids = [text_sha("t1"), text_sha("t2")]
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    meta = {"dim": 2, "model": "test", "as_of": "2026-07-11",
            "mapping": {ids[0]: [{"pd_itm_no": "A1", "pd_nm": "ETF 하나"}],
                        ids[1]: [{"pd_itm_no": "A2", "pd_nm": "ETF 둘"}]}}
    store = VectorStore(ids, matrix, meta)
    hits = store.search([1.0, 0.0], k=1)
    assert hits[0][0] == ids[0]
    assert hits[0][2][0]["pd_nm"] == "ETF 하나"


# ---------------------------------------------------------------------------
# 3. 임베딩 클라이언트 (mock transport)
# ---------------------------------------------------------------------------

def _client(handler, tmp_path):
    return ClovaEmbeddingClient(api_key="TEST-KEY",
                                transport=httpx.MockTransport(handler),
                                audit_path=tmp_path / "audit.jsonl")


def test_embed_endpoint_candidate_fallback(tmp_path):
    """첫 경로 404 → 다음 후보에서 성공 → 이후 호출은 그 경로로 고정."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/api-tools/embedding/v2":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={
            "status": {"code": "20000"},
            "result": {"embedding": [0.1] * EMBEDDING_DIM, "inputTokens": 7}})

    c = _client(handler, tmp_path)
    vec, tokens = c.embed("hello")
    assert len(vec) == EMBEDDING_DIM and tokens == 7
    c.embed("again")
    assert calls[-1] == calls[1]                   # 고정된 경로만 재사용(재순회 없음)


def test_embed_no_service_app_message(tmp_path):
    """전 경로 40100 이면 '서비스 앱 신청' 안내를 담아 실패한다 (8/13 실측 재현)."""
    def handler(request):
        return httpx.Response(401, json={
            "status": {"code": "40100",
                       "message": "Unauthorized: No Service App. Request for a Service App first."},
            "result": None})

    with pytest.raises(ClovaEmbeddingError, match="서비스 앱"):
        _client(handler, tmp_path).embed("hello")


def test_embed_rejects_wrong_dimension(tmp_path):
    def handler(request):
        return httpx.Response(200, json={
            "status": {"code": "20000"},
            "result": {"embedding": [0.1] * 8, "inputTokens": 1}})

    with pytest.raises(ClovaEmbeddingError, match="차원"):
        _client(handler, tmp_path).embed("hello")


def test_client_rejects_non_clovastudio_endpoint(tmp_path):
    with pytest.raises(ClovaEmbeddingError, match="허용되지 않은"):
        ClovaEmbeddingClient(base_url="https://api.openai.com", api_key="K",
                             audit_path=tmp_path / "a.jsonl")
