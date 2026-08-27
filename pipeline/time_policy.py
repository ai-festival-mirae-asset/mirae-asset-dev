"""시간 정책(time policy) 모듈 — dev-kyung 브랜치 pipeline/time_policy.py 이식.

이식 출처: dev-kyung 브랜치 (git show dev-kyung:pipeline/time_policy.py), 로직 동일 이식.
이식 이유(무엇/왜):
  - 채권 REMAINING_DAYS 저장값은 행별 PD_STD_INFO_UPDATE 시점 기준(중앙값 137일
    지연)이라 그대로 답변에 쓰면 틀린다.
  - 따라서 잔존만기·만기상태는 요청 시점 서울 날짜(as_of_date)로 매번 재계산하고,
    재현용 스냅샷 통계는 AS_OF=2026-08-22(8/26 재배포본) 로 고정한다 — 이 이원화가 본 모듈의
    핵심 계약이다.
  - DB 선택(duckdb 등)과 무관한 순수 함수 모듈이므로 papuagigi 브랜치에 그대로
    가져와 S2 도구 구현(잔존만기 재계산 도구)에서 사용할 예정이다.

주의: Windows 에서 ZoneInfo("Asia/Seoul") 는 tzdata 패키지가 필요하다
(현재 환경에 tzdata 설치 확인됨).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

# 서비스 기준 타임존: 모든 "오늘" 판정은 서울 날짜로 통일한다.
SERVICE_TIMEZONE = ZoneInfo("Asia/Seoul")


def service_today(now: datetime | None = None) -> date:
    """요청 시점의 서비스 타임존(서울) 기준 날짜를 반환한다.

    - now 를 주입할 수 있게 해 테스트를 결정적으로 만든다.
    - naive datetime 은 이미 서울 시각인 것으로 해석하고, aware datetime 은
      서울 시각으로 변환한다.
    """
    if now is None:
        now = datetime.now(SERVICE_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SERVICE_TIMEZONE)
    else:
        now = now.astimezone(SERVICE_TIMEZONE)
    return now.date()


def resolve_as_of_date(
    requested: date | None = None, *, now: datetime | None = None
) -> date:
    """사용자가 명시한 기준일이 있으면 우선하고, 없으면 오늘(서울 날짜)을 쓴다."""
    return requested if requested is not None else service_today(now)


def remaining_days_as_of(maturity_date: date | None, as_of_date: date) -> int | None:
    """기준일(as_of_date) 대비 잔존 달력일수를 재계산한다.

    - 저장된 상품 데이터(REMAINING_DAYS)를 변경하지 않고 요청마다 계산한다.
    - 만기 경과 시 음수 대신 0 을 반환한다(음수 방지).
    - 만기일 미상(None)이면 None 을 반환해 정책 계층이 "unknown" 처리하게 한다.
    """
    if maturity_date is None:
        return None
    return max((maturity_date - as_of_date).days, 0)


def maturity_status_as_of(maturity_date: date | None, as_of_date: date) -> str:
    """기준일 대비 만기 상태 4상태 중 하나를 반환한다.

    - "unknown": 만기일 미상(None)
    - "matured_before_as_of": 기준일 이전에 이미 만기 도래
    - "matures_on_as_of_date": 기준일 당일 만기
    - "active_after_as_of": 기준일 이후 만기(잔존)
    """
    if maturity_date is None:
        return "unknown"
    if maturity_date < as_of_date:
        return "matured_before_as_of"
    if maturity_date == as_of_date:
        return "matures_on_as_of_date"
    return "active_after_as_of"
