"""Vitamin acknowledgement business logic (v1.3.2).

The daily 💊 message asks a yes/no-free question with one button: the tap is
the whole feature. A confirmation is stored per Reflection Day, and the absence
of a row means only "no confirmation was recorded" — this module never models
"not taken", keeps no streak, and offers no statistics (deliberate scope limit).

The day a button belongs to is baked into its callback data when the reminder is
sent, so a tap hours later — or after a restart — still lands on the right day.
Because a forgotten old message must not write history months later, a button is
only accepted for the current or the previous Reflection Day: much stricter than
the food edit window, since there is no historical editing to support here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.database.models import User, VitaminLog
from app.database.repositories import VitaminLogRepository
from app.services.time_service import reflection_day

#: A confirmation may cover today's or yesterday's Reflection Day only.
ACK_WINDOW_DAYS = 1


class VitaminDateError(ValueError):
    """The button belongs to a future or stale Reflection Day."""


def get_log(session: Session, user: User, target_date: date) -> VitaminLog | None:
    return VitaminLogRepository(session).get(user.id, target_date)


def is_taken(session: Session, user: User, target_date: date) -> bool:
    return get_log(session, user, target_date) is not None


def check_date(user: User, target_date: date, *, today: date | None = None) -> None:
    current = today or reflection_day(user.timezone)
    if target_date > current:
        raise VitaminDateError("future date")
    if target_date < current - timedelta(days=ACK_WINDOW_DAYS):
        raise VitaminDateError("outside the acknowledgement window")


def mark_taken(
    session: Session, user: User, target_date: date, *, now: datetime | None = None
) -> VitaminLog:
    """Record the tap for ``target_date``, idempotently.

    ``now`` is the injectable real time of the tap: it also fixes the reference
    Reflection Day, so a tap at 01:30 still counts for the previous evening.
    """
    current = reflection_day(user.timezone, now)
    check_date(user, target_date, today=current)
    taken_at = now or datetime.now(UTC)
    return VitaminLogRepository(session).mark_taken(
        user_id=user.id, vitamin_date=target_date, taken_at=taken_at
    )
