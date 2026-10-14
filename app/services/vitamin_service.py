"""Vitamin acknowledgement business logic (v1.3.2 generic, v1.3.3 granular).

v1.3.3 asks one evening question with three separate buttons — 💊 Что из
витаминов ты сегодня принял? — and each tap is its own acknowledgement, stored
immediately and independently. One tap never closes the day: a day with two
confirmed items is a valid partial state that survives restarts, and the absence
of a row means only "no confirmation recorded", never "not taken". This module
keeps no "not taken" value, no streak and no statistics (deliberate scope limit).

The day a button belongs to is baked into its callback data when the reminder is
sent, so a tap hours later — or after a restart — still lands on the right day.
Because a forgotten old message must not write history months later, a button is
only accepted for the current or the previous Reflection Day: much stricter than
the food edit window, since there is no historical editing to support here.

``vitamin_logs`` (v1.3.2) stays as historical data with its old meaning, "the
user answered that day's generic question". It is never reinterpreted as three
item confirmations, and no backfill exists on purpose: a generic tap said
nothing about which supplement was taken.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.database.models import User, VitaminIntake, VitaminLog
from app.database.repositories import VitaminIntakeRepository, VitaminLogRepository
from app.services.time_service import reflection_day

#: A confirmation may cover today's or yesterday's Reflection Day only.
ACK_WINDOW_DAYS = 1

#: The catalog of what the user actually takes, in the order it is displayed.
#: Callback data, database rows and every check use the stable *code*; the label
#: exists only for rendering, so wording can change without touching history.
VITAMIN_ITEMS: tuple[tuple[str, str], ...] = (
    ("vitamin_d", "Витамин D"),
    ("omega_3", "Омега-3"),
    ("opti_men", "Opti-Men"),
)

_ITEM_LABELS = dict(VITAMIN_ITEMS)

#: How many separate confirmations make a full vitamin day.
ITEM_COUNT = len(VITAMIN_ITEMS)


class VitaminDateError(ValueError):
    """The button belongs to a future or stale Reflection Day."""


class VitaminItemError(ValueError):
    """The button names an item that is not in the catalog."""


def item_label(code: str) -> str:
    return _ITEM_LABELS[code]


def validate_item(code: str) -> bool:
    return code in _ITEM_LABELS


# ---------------------------------------------------------------------------
# Granular confirmations (v1.3.3)
# ---------------------------------------------------------------------------
def get_taken_items(session: Session, user: User, target_date: date) -> list[str]:
    """Codes confirmed for ``target_date``, in catalog order.

    The order is a property of the catalog, not of the database: tapping Opti-Men
    first must not move it to the top of tomorrow's checklist.
    """
    rows = VitaminIntakeRepository(session).list_for_day(user.id, target_date)
    taken = {row.item_code for row in rows}
    return [code for code, _label in VITAMIN_ITEMS if code in taken]


def is_item_taken(session: Session, user: User, target_date: date, item_code: str) -> bool:
    return VitaminIntakeRepository(session).get(user.id, target_date, item_code) is not None


def all_items_taken(session: Session, user: User, target_date: date) -> bool:
    return len(get_taken_items(session, user, target_date)) == ITEM_COUNT


def mark_item_taken(
    session: Session,
    user: User,
    target_date: date,
    item_code: str,
    *,
    now: datetime | None = None,
) -> VitaminIntake:
    """Record one item's tap for ``target_date``, idempotently and at once.

    ``now`` is the injectable real time of the tap: it also fixes the reference
    Reflection Day, so a tap at 01:30 still counts for the previous evening.
    """
    if not validate_item(item_code):
        raise VitaminItemError(f"unknown vitamin item: {item_code}")
    current = reflection_day(user.timezone, now)
    check_date(user, target_date, today=current)
    taken_at = now or datetime.now(UTC)
    return VitaminIntakeRepository(session).mark_taken(
        user_id=user.id, vitamin_date=target_date, item_code=item_code, taken_at=taken_at
    )


# ---------------------------------------------------------------------------
# Legacy generic acknowledgement (v1.3.2, kept for old buttons and history)
# ---------------------------------------------------------------------------
def get_log(session: Session, user: User, target_date: date) -> VitaminLog | None:
    return VitaminLogRepository(session).get(user.id, target_date)


def is_taken(session: Session, user: User, target_date: date) -> bool:
    """Whether the *generic* v1.3.2 question was answered for this day.

    A legacy row counts as a finished day for the scheduler: the user already
    answered that evening in production, and asking again would be noise. It does
    not imply anything about the three individual items.
    """
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
    """Record the legacy generic tap for ``target_date``, idempotently."""
    current = reflection_day(user.timezone, now)
    check_date(user, target_date, today=current)
    taken_at = now or datetime.now(UTC)
    return VitaminLogRepository(session).mark_taken(
        user_id=user.id, vitamin_date=target_date, taken_at=taken_at
    )
