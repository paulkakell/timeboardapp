from __future__ import annotations

from ..clock import utc_now

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings


def get_app_tz() -> ZoneInfo:
    s = get_settings()
    try:
        return ZoneInfo(s.app.timezone)
    except Exception:
        return ZoneInfo("UTC")


def now_utc() -> datetime:
    # Stored timestamps are naive UTC.
    return utc_now().replace(tzinfo=None)


def as_aware_utc(dt_utc_naive: datetime) -> datetime:
    return dt_utc_naive.replace(tzinfo=timezone.utc)


def to_local(dt_utc_naive: datetime) -> datetime:
    tz = get_app_tz()
    return as_aware_utc(dt_utc_naive).astimezone(tz)


def from_local_to_utc_naive(dt_local_naive: datetime) -> datetime:
    tz = get_app_tz()
    aware_local = dt_local_naive.replace(tzinfo=tz)
    aware_utc = aware_local.astimezone(timezone.utc)
    return aware_utc.replace(tzinfo=None)


def iso_for_datetime_local_input(dt_utc_naive: datetime) -> str:
    # HTML datetime-local expects YYYY-MM-DDTHH:MM.
    local = to_local(dt_utc_naive)
    return local.strftime("%Y-%m-%dT%H:%M")


def format_dt_display(dt_utc_naive: datetime) -> str:
    local = to_local(dt_utc_naive)
    return local.strftime("%Y-%m-%d %H:%M")
