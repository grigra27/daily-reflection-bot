"""Settings validation + check-in service round-trip tests."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.database.models import User
from app.database.session import session_scope
from app.services import checkin_service, settings_service
from app.services.settings_service import InvalidSettingError
from app.services.time_service import (
    is_valid_timezone,
    parse_hhmm,
    week_start_date,
)
from tests.test_handlers import app_runtime  # noqa: F401  (fixture reuse)


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
    before_morning = user.morning_time
    with pytest.raises(InvalidSettingError):
        settings_service.set_morning_time(session, user, bad)
    assert user.morning_time == before_morning


def test_invalid_timezone_not_saved(session: Session, user: User) -> None:
    before = user.timezone
    with pytest.raises(InvalidSettingError):
        settings_service.set_timezone(session, user, "Not/AZone")
    assert user.timezone == before


def test_valid_settings_persist(session: Session, user: User) -> None:
    settings_service.set_morning_time(session, user, "7:5")
    settings_service.set_checkin_time(session, user, "21:30")
    settings_service.set_reminder_time(session, user, "23:45")
    settings_service.set_timezone(session, user, "Europe/Berlin")
    assert user.morning_time == "07:05"
    assert user.checkin_time == "21:30"
    assert user.reminder_time == "23:45"
    assert user.timezone == "Europe/Berlin"


def test_new_user_defaults_morning_time(session: Session) -> None:
    from app.database.repositories import UserRepository

    u = UserRepository(session).get_or_create(42, display_name="X")
    assert u.morning_time == "08:30"


def test_settings_render_includes_morning_line() -> None:
    from app.bot import texts

    rendered = texts.settings_message("08:30", "21:30", "23:00", "Europe/Moscow")
    assert "☀️ Утренний фокус: 08:30" in rendered
    assert "🌙 Итоги дня: 21:30" in rendered
    assert "🔔 Повторное напоминание: 23:00" in rendered
    assert "🌍 Часовой пояс: Europe/Moscow" in rendered


async def test_setting_morning_time_reschedules_all_three_jobs(
    app_runtime, session_factory  # noqa: F811
) -> None:
    """Changing any relevant time must rebuild morning+checkin+reminder."""
    from app.bot.handlers import settings as settings_handlers
    from app.bot.states import SettingsStates
    from app.database.repositories import UserRepository
    from tests.test_handlers import FakeState, user_message

    app_runtime.scheduler.apscheduler.start()
    try:
        msg = user_message(111, "/start")
        from app.bot.handlers import start as start_handlers

        await start_handlers.cmd_start(msg, FakeState())
        with session_scope(session_factory) as s:
            pk = UserRepository(s).get_by_telegram_id(111).id

        state = FakeState()
        state.state = SettingsStates.waiting_morning_time
        await settings_handlers.set_morning(user_message(111, "07:20"), state)

        with session_scope(session_factory) as s:
            assert UserRepository(s).get_by_telegram_id(111).morning_time == "07:20"
        jobs = {j.id: j for j in app_runtime.scheduler.apscheduler.get_jobs()}
        assert set(jobs) == {f"morning:{pk}", f"checkin:{pk}", f"reminder:{pk}"}
        assert "hour='7', minute='20'" in str(jobs[f"morning:{pk}"].trigger)
    finally:
        app_runtime.scheduler.apscheduler.shutdown(wait=False)


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
