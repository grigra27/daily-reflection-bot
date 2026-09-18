"""Settings validation + check-in service round-trip tests."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.database.models import User
from app.services import checkin_service, settings_service
from app.services.settings_service import InvalidSettingError
from app.services.time_service import (
    is_valid_timezone,
    parse_hhmm,
    week_start_date,
)


def test_valid_inputs() -> None:
    assert parse_hhmm("21:30") == (21, 30)
    assert is_valid_timezone("Europe/Moscow")
    assert not is_valid_timezone("Mars/Olympus")
    # Monday is the ISO week start.
    assert week_start_date(date(2026, 9, 18)).weekday() == 0


@pytest.mark.parametrize("bad", ["25:00", "12:99", "abc", "12", ""])
def test_invalid_time_not_saved(session: Session, user: User, bad: str) -> None:
    before = user.checkin_time
    with pytest.raises(InvalidSettingError):
        settings_service.set_checkin_time(session, user, bad)
    assert user.checkin_time == before


def test_invalid_timezone_not_saved(session: Session, user: User) -> None:
    before = user.timezone
    with pytest.raises(InvalidSettingError):
        settings_service.set_timezone(session, user, "Not/AZone")
    assert user.timezone == before


def test_valid_settings_persist(session: Session, user: User) -> None:
    settings_service.set_checkin_time(session, user, "7:5")
    settings_service.set_reminder_time(session, user, "23:45")
    settings_service.set_timezone(session, user, "Europe/Berlin")
    assert user.checkin_time == "07:05"
    assert user.reminder_time == "23:45"
    assert user.timezone == "Europe/Berlin"


def test_checkin_service_validates_and_saves(session: Session, user: User) -> None:
    with pytest.raises(checkin_service.ValidationError):
        checkin_service.save_daily_entry(
            session, user, day_score=9, mood_score=3, energy_score=3,
            reflection_text=None, entry_date=date(2026, 9, 18),
        )

    entry = checkin_service.save_daily_entry(
        session, user, day_score=4, mood_score=3, energy_score=5,
        reflection_text="   ", entry_date=date(2026, 9, 18),
    )
    assert entry.reflection_text is None  # whitespace normalised to NULL
    fetched = checkin_service.get_entry(session, user, date(2026, 9, 18))
    assert fetched is not None and fetched.id == entry.id
    # Editing updates the same row.
    updated = checkin_service.save_daily_entry(
        session, user, day_score=5, mood_score=5, energy_score=5,
        reflection_text="better", entry_date=date(2026, 9, 18),
    )
    assert updated.id == entry.id
    assert updated.day_score == 5
