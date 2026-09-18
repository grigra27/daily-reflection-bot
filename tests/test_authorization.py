"""Authorization tests (baseline section 3, handoff "Авторизация")."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.auth_service import NotAuthorizedError, authorize, is_allowed


def test_allowed_user_authorized(session: Session, settings: Settings) -> None:
    u = authorize(session, settings, 111, display_name="A")
    assert u.telegram_user_id == 111
    assert u.id is not None


def test_unknown_user_rejected(session: Session, settings: Settings) -> None:
    with pytest.raises(NotAuthorizedError):
        authorize(session, settings, 999)


def test_is_allowed() -> None:
    assert is_allowed(Settings(telegram_bot_token="t", allowed_telegram_ids="1,2"), 1)
    assert not is_allowed(Settings(telegram_bot_token="t", allowed_telegram_ids="1,2"), 3)


def test_inactive_user_rejected(session: Session, settings: Settings) -> None:
    u = authorize(session, settings, 111)
    u.is_active = False
    session.commit()
    with pytest.raises(NotAuthorizedError):
        authorize(session, settings, 111)
