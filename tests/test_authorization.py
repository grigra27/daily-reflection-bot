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


def test_new_user_receives_all_four_schedule_settings(
    session: Session, settings: Settings
) -> None:
    # v1.1: authorize() must seed timezone, morning_time, checkin_time and
    # reminder_time from settings, not just the v1 three.
    u = authorize(session, settings, 111)
    assert u.timezone == settings.default_timezone
    assert u.morning_time == settings.default_morning_time == "08:30"
    assert u.checkin_time == settings.default_checkin_time
    assert u.reminder_time == settings.default_reminder_time


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
