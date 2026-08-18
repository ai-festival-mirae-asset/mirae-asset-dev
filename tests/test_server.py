# -*- coding: utf-8 -*-
"""구현 순서 ⑥ 테스트 — API 서버: 공식 규격 응답·전역 방어·캐시 규칙.

핵심 검사: ① 어떤 입력(정상·함정·빈 질문·내부 오류)에도 HTTP 200 + 5필드
문자열 JSON ② 같은 질문은 캐시 즉답 ③ 실패·강등 응답은 캐시에 남지 않는다.
서버 테스트는 가벼운 구성(DB+이름 사전만, 그래프·벡터·HCX 없음)으로 돈다.
"""
import os

import pytest

DB_EXISTS = os.path.exists(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "output", "products.duckdb"))
needs_db = pytest.mark.skipif(not DB_EXISTS, reason="products.duckdb 미생성 — load_duckdb.py 선행")

FIVE_FIELDS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}
REFUSE_HEAD = "요청하신 내용은 보유 데이터 기준으로 확인할 수 없습니다"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    if not DB_EXISTS:
        pytest.skip("products.duckdb 미생성")
    import duckdb
    from fastapi.testclient import TestClient
    from engine.channels import RuntimeContext
    from pipeline.entity_index import DB_PATH_DEFAULT, build_entity_index
    from server.app import create_app

    con = duckdb.connect(DB_PATH_DEFAULT, read_only=True)
    ctx = RuntimeContext(con=con, index=build_entity_index(con))
    cache_path = str(tmp_path_factory.mktemp("srv") / "cache.jsonl")
    app = create_app(ctx, cache_path=cache_path)
    with TestClient(app) as tc:
        yield tc
    con.close()


@needs_db
def test_answer_returns_five_string_fields(client):
    r = client.get("/answer", params={"question_id": "L-11",
                                      "question": "순자산총액 기준으로 국내 ETF 상위 5개 알려줘"})
    assert r.status_code == 200
    out = r.json()
    assert set(out) == FIVE_FIELDS
    assert all(isinstance(v, str) for v in out.values())
    assert "KODEX 200" in out["answer"]
    assert out["question_id"] == "L-11"


@needs_db
def test_trap_question_refused_via_server(client):
    r = client.get("/answer", params={"question": "kimi 관련 투자 상품 있어?"})
    assert r.status_code == 200
    assert r.json()["answer"].startswith(REFUSE_HEAD)


@needs_db
def test_empty_question_still_valid_json(client):
    r = client.get("/answer")
    assert r.status_code == 200
    out = r.json()
    assert set(out) == FIVE_FIELDS and "질문이 비어" in out["answer"]


@needs_db
def test_undefined_params_and_missing_id_never_500(client):
    """공식 규격(과제설명 PDF p.11): 미정의 파라미터가 와도 500 없이 처리, 응답은
    application/json; charset=utf-8. question_id 가 빠져도 5필드 200 을 유지한다."""
    q = "신용등급 AAAA인 채권 찾아줘"                     # 이 모듈의 다른 테스트와 겹치지 않는 질문
    r = client.get("/answer", params={"question": q, "foo": "bar", "debug": "1",
                                      "question_id ": "x"})   # 오타 키(공백 포함)도 미정의 파라미터
    assert r.status_code == 200
    out = r.json()
    assert set(out) == FIVE_FIELDS
    assert all(isinstance(v, str) for v in out.values())
    assert out["question_id"] == ""                       # 누락 → 빈 문자열(오류 아님)
    assert out["question"] == q                           # 요청값 그대로 반환
    ctype = r.headers["content-type"].lower()
    assert ctype.startswith("application/json") and "charset=utf-8" in ctype
    # 빈 질문 경로도 같은 헤더
    assert "charset=utf-8" in client.get("/answer", params={"x": "y"}).headers["content-type"].lower()


@needs_db
def test_cache_hits_on_second_call(client):
    q = {"question": "공모펀드는 총 몇 개야?"}
    first = client.get("/answer", params=q).json()
    assert "(캐시 응답)" not in first["think_trace"]
    second = client.get("/answer", params=q).json()
    assert "(캐시 응답)" in second["think_trace"]          # 정상 완결 응답은 캐시된다


@needs_db
def test_degraded_answer_not_cached(client):
    """폴백(강등)이 낀 응답은 캐시 금지 — 재시도가 새로 계산되게 한다."""
    q = {"question": "삼성전자랑 SK하이닉스를 둘 다 담고 있는 ETF 중에서 총보수가 제일 낮은 건 뭐야?"}
    first = client.get("/answer", params=q).json()
    assert "폴백" in first["think_trace"]                  # HCX 없는 구성 → 규칙 폴백 경로
    second = client.get("/answer", params=q).json()
    assert "(캐시 응답)" not in second["think_trace"]      # 캐시되지 않았다


@needs_db
def test_internal_error_still_returns_five_fields(client, monkeypatch):
    import server.app as srv
    def boom(*a, **k):
        raise RuntimeError("의도된 테스트 오류")
    monkeypatch.setattr(srv, "answer_question", boom)
    r = client.get("/answer", params={"question": "아무 질문"})
    assert r.status_code == 200
    out = r.json()
    assert set(out) == FIVE_FIELDS
    assert "전역 오류" in out["think_trace"] and "다시 시도" in out["answer"]


@needs_db
def test_health_and_test_page(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["db"] and h["index_entries"] > 0
    page = client.get("/")
    assert page.status_code == 200 and "질문 시험대" in page.text


def test_is_cacheable_predicate():
    from server.app import is_cacheable
    ok = {"think_trace": "stage=rule intent=x behavior=answer\n검문[value] pass"}
    assert is_cacheable(ok)
    for bad_marker in ("생성 호출 실패 — 규칙 요약으로 폴백", "강등", "오류 sql.x: boom",
                       "시간 예산 초과 — 생략", "전역 오류: RuntimeError"):
        assert not is_cacheable({"think_trace": f"...\n{bad_marker}"}), bad_marker
