# -*- coding: utf-8 -*-
"""
벽시계 상한이 걸린 HTTP 호출 — DNS 조회가 멈춰도 응답 시간 예산을 넘기지 않는다 (8/19).

무엇: httpx 의 timeout 은 연결·읽기·쓰기 단계에만 걸리고, 그 앞의 **이름 풀기(getaddrinfo)** 가
      멈추면 아무 제한 없이 기다린다. 8/19 실측: 실전 성적표 실행 중 HCX 호출 한 건이 368초 동안
      멈춘 뒤 'getaddrinfo failed' 로 끝났다(주최 하드 타임아웃 300초를 넘기는 사고).
어떻게: 실제 전송을 작업 스레드에서 돌리고 호출자는 (timeout + 여유) 초까지만 기다린다.
      넘기면 WallClockTimeout 을 던지고, 멈춘 스레드는 이름 풀기가 끝날 때 조용히 정리된다.
      호출부(chat/embed)는 예외를 받아 규칙 폴백·의미 검색 생략으로 강등한다.
"""
import concurrent.futures
import threading

WALL_MARGIN_SEC = 2.0
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="clova-http")
_LOCK = threading.Lock()


class WallClockTimeout(Exception):
    """timeout + 여유 안에 응답이 오지 않음(이름 풀기 정지 등) — 호출자는 강등 처리."""


def call_with_wall_clock(fn, timeout, *args, **kwargs):
    """fn(*args, **kwargs) 를 작업 스레드에서 실행하고 최대 timeout+여유 초만 기다린다."""
    future = _POOL.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=float(timeout) + WALL_MARGIN_SEC)
    except concurrent.futures.TimeoutError:
        raise WallClockTimeout(f"벽시계 상한 초과({float(timeout) + WALL_MARGIN_SEC:.0f}초) — "
                               "이름 풀기/연결이 멈춤(네트워크 사고 추정)")


def post_json(transport, timeout, url, headers, body):
    """httpx POST 를 벽시계 상한 안에서 — 응답 객체를 돌려준다(닫힌 클라이언트와 무관하게 읽을 수 있음)."""
    import httpx

    def _do():
        with httpx.Client(transport=transport, timeout=timeout) as http:
            resp = http.post(url, headers=headers, json=body)
            resp.read()                                   # 본문을 다 읽어 두어 클라이언트를 닫아도 안전
            return resp
    return call_with_wall_clock(_do, timeout)
