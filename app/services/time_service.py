"""Time and timezone helpers.

Critical rule (baseline "Время и timezone"): "today" is defined per user in the
user's own timezone, never by server system time. All helpers here are pure and
timezone-aware; naive datetimes are not mixed in.

Two distinct notions of "today" exist (v1.1.1):
- ``user_today``: the plain calendar date in the user's timezone.
- ``reflection_day``: the Reflection Bot logical day, which rolls over at
  05:00 user-local, not at midnight.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

_VALID_TZ = available_timezones()

REFLECTION_DAY_ROLLOVER_HOUR = 5


def is_valid_timezone(name: str) -> bool:
    return bool(name) and (name == "UTC" or name in _VALID_TZ)


def parse_hhmm(value: str) -> tuple[int, int]:
    """Validate an ``HH:MM`` string.

    Raises ``ValueError`` with a user-neutral message on invalid input so the
    caller can refuse to persist a bad value.
    """
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("time must be in HH:MM format")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time out of range")
    return hour, minute


def format_hhmm(value: str) -> str:
    hour, minute = parse_hhmm(value)
    return f"{hour:02d}:{minute:02d}"


def _local_now(tz_name: str, now: datetime | None) -> datetime:
    """Resolve ``now`` (or real UTC now) into the user's local wall time."""
    zone = ZoneInfo(tz_name)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(zone)


def user_today(tz_name: str, now: datetime | None = None) -> date:
    """Return the current calendar date in the user's timezone."""
    return _local_now(tz_name, now).date()


def reflection_day(tz_name: str, now: datetime | None = None) -> date:
    """Return the user's logical Reflection Bot day.

    The day starts at ``REFLECTION_DAY_ROLLOVER_HOUR`` (05:00) *user-local*,
    not midnight: everything before it still belongs to the previous date
    (e.g. 02:15 on the 21st is reflection day 20).
    """
    local = _local_now(tz_name, now)
    if local.hour < REFLECTION_DAY_ROLLOVER_HOUR:
        return local.date() - timedelta(days=1)
    return local.date()


def week_start_date(day: date) -> date:
    """Monday of the ISO week containing ``day``."""
    return day - timedelta(days=day.weekday())


def is_week_end(day: date) -> bool:
    """Sunday is the day the weekly reflection is offered."""
    return day.weekday() == 6
