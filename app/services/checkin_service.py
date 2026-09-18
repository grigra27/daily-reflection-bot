"""Daily check-in business logic.

Persists a completed check-in as a single DailyEntry for the user's current
local date. Scores are validated here (never in the Telegram layer); the
"one row per day / update on edit / double-tap safe" rule is delegated to the
repository's idempotent upsert.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database.models import DailyEntry, User
from app.database.repositories import DailyEntryRepository
from app.services.time_service import user_today

MAX_REFLECTION_LENGTH = 4000


class ValidationError(ValueError):
    pass


def _validate_score(name: str, value: int) -> int:
    if not isinstance(value, int) or not (1 <= value <= 5):
        raise ValidationError(f"{name} must be an integer between 1 and 5")
    return value


def _normalize_reflection(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return text[:MAX_REFLECTION_LENGTH]


def save_daily_entry(
    session: Session,
    user: User,
    *,
    day_score: int,
    mood_score: int,
    energy_score: int,
    reflection_text: str | None,
    entry_date: date | None = None,
) -> DailyEntry:
    _validate_score("day_score", day_score)
    _validate_score("mood_score", mood_score)
    _validate_score("energy_score", energy_score)
    target_date = entry_date or user_today(user.timezone)
    return DailyEntryRepository(session).upsert(
        user_id=user.id,
        entry_date=target_date,
        day_score=day_score,
        mood_score=mood_score,
        energy_score=energy_score,
        reflection_text=_normalize_reflection(reflection_text),
    )


def get_entry(session: Session, user: User, entry_date: date | None = None) -> DailyEntry | None:
    target_date = entry_date or user_today(user.timezone)
    return DailyEntryRepository(session).get(user.id, target_date)


def has_entry_today(session: Session, user: User) -> bool:
    return get_entry(session, user) is not None
