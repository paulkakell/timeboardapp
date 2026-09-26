"""Naive UTC for the existing SQLite schema, without deprecated utcnow()."""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp(value: float) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)
