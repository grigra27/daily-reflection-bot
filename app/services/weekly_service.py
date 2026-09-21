"""Weekly reflection logic (baseline sections 25-26).

Secondary, non-blocking scenario: it must never interfere with the daily
check-in. Each of the three answers is optional.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database.models import User, WeeklyReflection
from app.database.repositories import WeeklyReflectionRepository
from app.services.checkin_service import _normalize_reflection
from app.services.time_service import is_week_end, reflection_day, week_start_date


def current_week_start(user: User, today: date | None = None) -> date:
    return week_start_date(today or reflection_day(user.timezone))


def save_weekly(
    session: Session,
    user: User,
    *,
    best_event: str | None,
    energy_drainer: str | None,
    want_more: str | None,
    week_start: date | None = None,
) -> WeeklyReflection:
    start = week_start or current_week_start(user)
    return WeeklyReflectionRepository(session).upsert(
        user_id=user.id,
        week_start_date=start,
        best_event=_normalize_reflection(best_event),
        energy_drainer=_normalize_reflection(energy_drainer),
        want_more=_normalize_reflection(want_more),
    )


def should_offer_weekly(
    session: Session, user: User, today: date | None = None
) -> bool:
    """Offer only on Sunday and only if this week is not filled yet.

    ``today`` defaults to the user's Reflection Day, so e.g. Monday 02:00
    local is still logically Sunday.
    """
    current = today or reflection_day(user.timezone)
    if not is_week_end(current):
        return False
    start = week_start_date(current)
    return WeeklyReflectionRepository(session).get(user.id, start) is None
