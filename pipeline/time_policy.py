from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


SERVICE_TIMEZONE = ZoneInfo("Asia/Seoul")


def service_today(now: datetime | None = None) -> date:
    """Return the request date in the service timezone.

    A supplied clock makes tests deterministic. Naive clocks are interpreted as
    already being in the service timezone.
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
    """Use an explicit user date, otherwise default to today's Seoul date."""
    return requested if requested is not None else service_today(now)


def remaining_days_as_of(maturity_date: date | None, as_of_date: date) -> int | None:
    """Calculate remaining calendar days without mutating stored product data."""
    if maturity_date is None:
        return None
    return max((maturity_date - as_of_date).days, 0)


def maturity_status_as_of(maturity_date: date | None, as_of_date: date) -> str:
    if maturity_date is None:
        return "unknown"
    if maturity_date < as_of_date:
        return "matured_before_as_of"
    if maturity_date == as_of_date:
        return "matures_on_as_of_date"
    return "active_after_as_of"

