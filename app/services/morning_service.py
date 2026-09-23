"""Morning intention business logic.

Validation lives here (never in the Telegram layer): texts are trimmed,
required/optional normalised and length-capped, and since v1.2 the evening
outcome vocabulary is defined and enforced here too — handlers only ever pass
strings that came out of callback data. User-visible text is never logged.
Dates are always the user's Reflection Day via ``reflection_day`` (rolls over
at 05:00 user-local).
"""

from __future__ import annotations

from datetime import date
from enum import Enum, auto

from sqlalchemy.orm import Session

from app.database.models import DailyEntry, MorningIntent, User
from app.database.repositories import DailyEntryRepository, MorningIntentRepository
from app.services.time_service import reflection_day

MAX_INTENTION_LENGTH = 1000

# --- Evening outcomes (v1.2 "close the loop") --------------------------------
# The single canonical vocabulary. A boolean is not enough because "partly
# done" is a real answer, and strings (not numeric status codes) are what the
# rows, the CSV export and the logs have to carry.
OUTCOME_DONE = "done"
OUTCOME_PARTIAL = "partial"
OUTCOME_NOT_DONE = "not_done"

#: Display order of the evening buttons.
OUTCOMES: tuple[str, ...] = (OUTCOME_DONE, OUTCOME_PARTIAL, OUTCOME_NOT_DONE)
#: The two morning fields that can be closed.
OUTCOME_FIELD_MAIN = "main"
OUTCOME_FIELD_SECONDARY = "secondary"
OUTCOME_FIELDS: tuple[str, ...] = (OUTCOME_FIELD_MAIN, OUTCOME_FIELD_SECONDARY)


class MorningValidationError(ValueError):
    pass


class MorningLock(Enum):
    """Why the morning texts of a Reflection Day can no longer be edited."""

    OUTCOME_RECORDED = auto()  # the evening closure has already started
    DAY_FILLED = auto()  # the DailyEntry for that day already exists


class MorningLockedError(ValueError):
    """Refusal to overwrite a locked morning record; carries the reason so the
    presentation layer can pick a neutral message."""

    def __init__(self, lock: MorningLock) -> None:
        super().__init__(lock.name)
        self.lock = lock


MISSING_INTENT = "morning intent row is missing"
NO_SECONDARY_INTENTION = "this day has no secondary intention to close"


def _clean_main(text: str | None) -> str:
    if text is None or not text.strip():
        raise MorningValidationError("main intention is required")
    return text.strip()[:MAX_INTENTION_LENGTH]


def _clean_secondary(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return text[:MAX_INTENTION_LENGTH]


def lock_for(intent: MorningIntent | None, entry: DailyEntry | None) -> MorningLock | None:
    """Pure form of the lock rule, so both callers and tests can reason about
    historical data: pre-v1.2 days can have an entry while both outcomes are
    still NULL, and those texts are locked too. The filled-day reason wins when
    both apply — it is the more useful message for the user.

    A day with no MorningIntent row cannot be locked: the rule protects an
    existing plan from being rewritten after its evening was closed, and there
    is nothing to rewrite yet.
    """
    if intent is None:
        return None
    if entry is not None:
        return MorningLock.DAY_FILLED
    if intent.main_outcome or intent.secondary_outcome:
        return MorningLock.OUTCOME_RECORDED
    return None


def get_lock(
    session: Session, user: User, *, intention_date: date | None = None
) -> MorningLock | None:
    target_date = intention_date or reflection_day(user.timezone)
    return lock_for(
        MorningIntentRepository(session).get(user.id, target_date),
        DailyEntryRepository(session).get(user.id, target_date),
    )


def _refuse_if_locked(session: Session, user: User, target_date: date) -> None:
    """Service-level protection (spec 14): no Telegram UI state is trusted here,
    so a stale callback or a direct call cannot rewrite a closed morning."""
    lock = get_lock(session, user, intention_date=target_date)
    if lock is not None:
        raise MorningLockedError(lock)


def save_main_intention(
    session: Session, user: User, text: str, *, intention_date: date | None = None
) -> MorningIntent:
    """Step 1: persist immediately, keeping any existing secondary value."""
    target_date = intention_date or reflection_day(user.timezone)
    _refuse_if_locked(session, user, target_date)
    return MorningIntentRepository(session).upsert(
        user_id=user.id,
        intention_date=target_date,
        main_intention=_clean_main(text),
    )


def set_secondary(
    session: Session, user: User, text: str | None, *, intention_date: date | None = None
) -> MorningIntent:
    """Step 2: update the same row. Raises MorningValidationError(MISSING_INTENT)
    if step 1 never committed."""
    target_date = intention_date or reflection_day(user.timezone)
    repo = MorningIntentRepository(session)
    if repo.get(user.id, target_date) is None:
        raise MorningValidationError(MISSING_INTENT)
    _refuse_if_locked(session, user, target_date)
    return repo.upsert(
        user_id=user.id,
        intention_date=target_date,
        secondary_intention=_clean_secondary(text),
    )


def record_outcome(
    session: Session,
    user: User,
    *,
    outcome_field: str,
    outcome: str,
    intention_date: date | None = None,
) -> MorningIntent:
    """Persist one evening outcome the moment it is tapped, before the rest of
    the check-in exists.

    Deliberately independent of ``DailyEntry``: outcomes and day scores are
    separate facts and neither is derived from the other. Idempotent — a
    double tap rewrites the same column of the same row, never creating a
    MorningIntent or touching either intention text.
    """
    if outcome not in OUTCOMES:
        raise MorningValidationError(f"unknown outcome: {outcome!r}")
    if outcome_field not in OUTCOME_FIELDS:
        raise MorningValidationError(f"unknown outcome field: {outcome_field!r}")
    target_date = intention_date or reflection_day(user.timezone)
    repo = MorningIntentRepository(session)
    intent = repo.get(user.id, target_date)
    if intent is None:
        raise MorningValidationError(MISSING_INTENT)
    if outcome_field == OUTCOME_FIELD_SECONDARY and intent.secondary_intention is None:
        raise MorningValidationError(NO_SECONDARY_INTENTION)
    if outcome_field == OUTCOME_FIELD_MAIN:
        updated = repo.set_outcome(
            user_id=user.id, intention_date=target_date, main_outcome=outcome
        )
    else:
        updated = repo.set_outcome(
            user_id=user.id, intention_date=target_date, secondary_outcome=outcome
        )
    if updated is None:
        raise MorningValidationError(MISSING_INTENT)
    return updated


class EveningStep(Enum):
    """What the evening still owes this Reflection Day (v1.2)."""

    MAIN_OUTCOME = auto()
    SECONDARY_OUTCOME = auto()
    DAY_SCORE = auto()


def next_evening_step(intent: MorningIntent | None, *, restart: bool = False) -> EveningStep:
    """The one canonical resume rule, shared by /checkin, an outcome tap and
    the scheduled evening prompt: ask for the first thing that is still open,
    never something already answered.

    ``restart`` is what ``act:edit`` passes — a filled day is re-walked from
    the main outcome so a mistapped outcome can be corrected. Old outcomes are
    NOT cleared first: they are simply overwritten by the next tap, so an edit
    the user abandons leaves the existing data intact.
    """
    if intent is None:
        return EveningStep.DAY_SCORE
    if restart or intent.main_outcome is None:
        return EveningStep.MAIN_OUTCOME
    if intent.secondary_intention and intent.secondary_outcome is None:
        return EveningStep.SECONDARY_OUTCOME
    return EveningStep.DAY_SCORE


def get_intent(session: Session, user: User, intention_date: date | None = None) -> MorningIntent | None:
    target_date = intention_date or reflection_day(user.timezone)
    return MorningIntentRepository(session).get(user.id, target_date)


def has_intent_today(session: Session, user: User) -> bool:
    return get_intent(session, user) is not None
