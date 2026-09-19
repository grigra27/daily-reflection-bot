"""Morning intention business logic.

Validation lives here (never in the Telegram layer): texts are trimmed,
required/optional normalised and length-capped. User-visible text is never
logged. Dates are always the user's local date via ``user_today``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.database.models import MorningIntent, User
from app.database.repositories import MorningIntentRepository
from app.services.time_service import user_today

MAX_INTENTION_LENGTH = 1000


class MorningValidationError(ValueError):
    pass


MISSING_INTENT = "morning intent row is missing"


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


def save_main_intention(
    session: Session, user: User, text: str, *, intention_date: date | None = None
) -> MorningIntent:
    """Step 1: persist immediately, keeping any existing secondary value."""
    return MorningIntentRepository(session).upsert(
        user_id=user.id,
        intention_date=intention_date or user_today(user.timezone),
        main_intention=_clean_main(text),
    )


def set_secondary(
    session: Session, user: User, text: str | None, *, intention_date: date | None = None
) -> MorningIntent:
    """Step 2: update the same row. Raises MorningValidationError(MISSING_INTENT)
    if step 1 never committed."""
    target_date = intention_date or user_today(user.timezone)
    repo = MorningIntentRepository(session)
    if repo.get(user.id, target_date) is None:
        raise MorningValidationError(MISSING_INTENT)
    return repo.upsert(
        user_id=user.id,
        intention_date=target_date,
        secondary_intention=_clean_secondary(text),
    )


def get_intent(session: Session, user: User, intention_date: date | None = None) -> MorningIntent | None:
    target_date = intention_date or user_today(user.timezone)
    return MorningIntentRepository(session).get(user.id, target_date)


def has_intent_today(session: Session, user: User) -> bool:
    return get_intent(session, user) is not None
