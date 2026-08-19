"""pipeline/time_policy.py 단위 테스트.

무엇: dev-kyung 에서 이식한 시간 정책 모듈의 계약을 검증한다.
왜: 잔존만기·만기상태는 요청 시점 서울 날짜(as_of_date)로 재계산한다는 것이
    핵심 계약이므로, 타임존 경계·사용자 명시 날짜 우선·음수 방지·None 처리·
    만기 4상태·영구채(9999-12-31) 케이스를 고정해 회귀를 막는다.

참고: dev-kyung 의 tests/test_prepare_data.py 내 TimePolicyUnitTests 를 참고해
      pytest 스타일로 새로 작성했다 (prepare_data 의존 없이 time_policy 만 검증).
"""

from datetime import date, datetime, timedelta, timezone

from pipeline.time_policy import (
    SERVICE_TIMEZONE,
    maturity_status_as_of,
    remaining_days_as_of,
    resolve_as_of_date,
    service_today,
)

# 프로젝트 데이터 스냅샷 기준일 (재현용 통계 고정 날짜).
AS_OF_SNAPSHOT = date(2026, 7, 11)

# 영구채 표기: 원본 데이터의 만기일 센티널 9999-12-31.
PERPETUAL_MATURITY = date(9999, 12, 31)


class TestServiceToday:
    """service_today: 요청 경계에서의 타임존 처리."""

    def test_aware_utc_clock_converts_to_seoul_date(self):
        # UTC 16:30 은 서울로는 다음날 01:30 — 서울 날짜가 하루 앞서야 한다.
        utc_clock = datetime(2026, 8, 3, 16, 30, tzinfo=timezone.utc)
        assert service_today(utc_clock) == date(2026, 8, 4)

    def test_aware_non_utc_clock_converts_to_seoul_date(self):
        # 뉴욕(UTC-4, 서머타임) 2026-08-03 20:00 → 서울 2026-08-04 09:00.
        ny = timezone(timedelta(hours=-4))
        assert service_today(datetime(2026, 8, 3, 20, 0, tzinfo=ny)) == date(2026, 8, 4)

    def test_naive_clock_is_interpreted_as_seoul_time(self):
        # naive datetime 은 이미 서울 시각으로 해석 — 날짜 변환 없음.
        naive_clock = datetime(2026, 8, 3, 23, 59)
        assert service_today(naive_clock) == date(2026, 8, 3)

    def test_none_clock_uses_current_seoul_date(self):
        # now=None 이면 실제 현재 서울 날짜를 반환해야 한다.
        expected = datetime.now(SERVICE_TIMEZONE).date()
        assert service_today(None) == expected

    def test_seoul_midnight_boundary(self):
        # UTC 14:59 = 서울 23:59(같은 날), UTC 15:00 = 서울 00:00(다음 날).
        assert service_today(datetime(2026, 7, 10, 14, 59, tzinfo=timezone.utc)) == date(2026, 7, 10)
        assert service_today(datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)) == date(2026, 7, 11)


class TestResolveAsOfDate:
    """resolve_as_of_date: 사용자 명시 날짜 우선 규칙."""

    def test_explicit_date_has_priority_over_clock(self):
        # 사용자가 스냅샷 기준일을 명시하면 현재 시각과 무관하게 그 날짜를 쓴다.
        clock = datetime(2026, 8, 6, 9, 0, tzinfo=SERVICE_TIMEZONE)
        assert resolve_as_of_date(AS_OF_SNAPSHOT, now=clock) == AS_OF_SNAPSHOT

    def test_defaults_to_seoul_today_when_not_specified(self):
        clock = datetime(2026, 8, 5, 16, 30, tzinfo=timezone.utc)  # 서울 8/6 01:30
        assert resolve_as_of_date(None, now=clock) == date(2026, 8, 6)


class TestRemainingDaysAsOf:
    """remaining_days_as_of: 음수 방지·None 처리·요청별 재계산."""

    def test_recalculates_per_request_date(self):
        # 같은 만기라도 기준일이 다르면 잔존일수가 달라진다 (저장값 미사용 계약).
        maturity = date(2055, 5, 14)
        assert remaining_days_as_of(maturity, date(2026, 7, 11)) == 10534
        assert remaining_days_as_of(maturity, date(2026, 8, 4)) == 10510

    def test_matured_bond_is_clamped_to_zero(self):
        # 만기 경과분은 음수가 아니라 0 으로 고정한다.
        assert remaining_days_as_of(date(2026, 7, 1), AS_OF_SNAPSHOT) == 0

    def test_matures_on_as_of_date_is_zero(self):
        assert remaining_days_as_of(AS_OF_SNAPSHOT, AS_OF_SNAPSHOT) == 0

    def test_unknown_maturity_returns_none(self):
        assert remaining_days_as_of(None, AS_OF_SNAPSHOT) is None

    def test_perpetual_sentinel_returns_huge_positive_days(self):
        # 영구채 센티널(9999-12-31)은 매우 큰 양수 — 상위 계층이 별도 표기 처리.
        result = remaining_days_as_of(PERPETUAL_MATURITY, AS_OF_SNAPSHOT)
        assert result is not None and result > 2_900_000


class TestMaturityStatusAsOf:
    """maturity_status_as_of: 만기 상태 4상태."""

    def test_unknown_when_maturity_is_none(self):
        assert maturity_status_as_of(None, AS_OF_SNAPSHOT) == "unknown"

    def test_matured_before_as_of(self):
        assert maturity_status_as_of(date(2026, 7, 10), AS_OF_SNAPSHOT) == "matured_before_as_of"

    def test_matures_on_as_of_date(self):
        assert maturity_status_as_of(AS_OF_SNAPSHOT, AS_OF_SNAPSHOT) == "matures_on_as_of_date"

    def test_active_after_as_of(self):
        assert maturity_status_as_of(date(2026, 7, 12), AS_OF_SNAPSHOT) == "active_after_as_of"

    def test_perpetual_sentinel_is_active(self):
        # 영구채 센티널은 항상 기준일 이후 → active 상태로 분류된다.
        assert maturity_status_as_of(PERPETUAL_MATURITY, AS_OF_SNAPSHOT) == "active_after_as_of"
