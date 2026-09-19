"""Authorization service.

Only Telegram IDs present in ``ALLOWED_TELEGRAM_IDS`` may use the bot. The
check is applied to every action, not just ``/start`` (see the auth filter in
the bot layer). Unknown users get no data access, no writes, no export and no
settings changes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import User
from app.database.repositories import UserRepository


class NotAuthorizedError(Exception):
    """Raised when a Telegram user is not on the allow-list."""


def authorize(
    session: Session,
    settings: Settings,
    telegram_user_id: int,
    *,
    display_name: str | None = None,
) -> User:
    if telegram_user_id not in settings.allowed_telegram_ids:
        raise NotAuthorizedError(str(telegram_user_id))
    user = UserRepository(session).get_or_create(
        telegram_user_id,
        display_name=display_name,
        timezone=settings.default_timezone,
        checkin_time=settings.default_checkin_time,
        reminder_time=settings.default_reminder_time,
        morning_time=settings.default_morning_time,
    )
    if not user.is_active:
        raise NotAuthorizedError(str(telegram_user_id))
    return user


def is_allowed(settings: Settings, telegram_user_id: int) -> bool:
    return telegram_user_id in settings.allowed_telegram_ids
