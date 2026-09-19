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


def test_settings_fixture_exposes_allowed_ids(settings: Settings) -> None:
    assert settings.allowed_telegram_ids == {111, 222}


def test_is_allowed() -> None:
    s = Settings(telegram_bot_token="t", allowed_telegram_ids_raw="1,2")
    assert is_allowed(s, 1)
    assert not is_allowed(s, 3)


def test_inactive_user_rejected(session: Session, settings: Settings) -> None:
    u = authorize(session, settings, 111)
    u.is_active = False
    session.commit()
    with pytest.raises(NotAuthorizedError):
        authorize(session, settings, 111)
