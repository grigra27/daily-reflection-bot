"""v1.3.1 vitamins reminder: text notification, scheduler job and sync
semantics, settings service/validation/render, /settings handlers and the
0005 migration.

The reminder is deliberately *not* a reflection flow: exactly one plain text
message per day in the user's own timezone, no buttons, no tracking, and a
disabled reminder means the job is removed from the scheduler outright.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.bot import keyboards, notifications, texts
from app.bot.handlers import settings as settings_handlers
from app.bot.handlers import start as start_handlers
from app.bot.states import SettingsStates
from app.database.models import User
from app.database.repositories import UserRepository
from app.database.session import session_scope
from app.scheduler.scheduler import ReflectionScheduler
from app.services import settings_service
from app.services.settings_service import InvalidSettingError
from tests.test_handlers import (
    FakeState,
    app_runtime,  # noqa: F401  (pytest fixture, imported for reuse)
    callback,
    user_message,
)
from tests.test_migrations import _alembic, _columns
from tests.test_scheduler import FakeBot, _mk_user


# --------------------------------------------------------------------------
# 19. The notification itself
# --------------------------------------------------------------------------
async def test_send_vitamin_reminder_is_one_plain_text_message() -> None:
    bot = FakeBot()
    await notifications.send_vitamin_reminder(bot, 111)  # type: ignore[arg-type]
    assert bot.sent == [(111, texts.VITAMIN_REMINDER)]
    assert texts.VITAMIN_REMINDER == "💊 Время принять витамины."
    # No keyboard of any kind is ever attached.
    assert "reply_markup" not in bot.kwargs[0]


# --------------------------------------------------------------------------
# 20-22. Job body: enabled / disabled / inactive
# --------------------------------------------------------------------------
async def test_vitamin_job_sends_for_active_enabled_user(session, session_factory) -> None:
    u = _mk_user(session)
    bot = FakeBot()
    assert u.vitamin_reminder_enabled is True  # ORM default
    assert u.vitamin_reminder_time == "22:00"
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)
    assert bot.sent == [(u.telegram_user_id, texts.VITAMIN_REMINDER)]


async def test_vitamin_job_silent_when_disabled(session, session_factory) -> None:
    u = _mk_user(session)
    u.vitamin_reminder_enabled = False
    session.commit()
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)
    assert bot.sent == []


async def test_vitamin_job_silent_when_user_inactive(session, session_factory) -> None:
    u = _mk_user(session)
    u.is_active = False
    session.commit()
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)
    assert bot.sent == []


async def test_vitamin_job_sends_without_any_entries(session, session_factory) -> None:
    # Independent of DailyEntry / FoodDay / reflection logic: with zero data
    # the reminder still goes out (the other jobs would skip in this state).
    u = _mk_user(session)
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)
    assert len(bot.sent) == 1


# --------------------------------------------------------------------------
# 23-26. Scheduler registration, toggle, time and timezone
# --------------------------------------------------------------------------
async def test_sync_registers_seventh_vitamins_job(session, session_factory) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, FakeBot())  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        jobs = {j.id for j in sched.apscheduler.get_jobs()}
        assert jobs == {
            f"morning:{u.id}", f"checkin:{u.id}", f"reminder:{u.id}",
            f"food_morning:{u.id}", f"food_evening:{u.id}", f"weight:{u.id}",
            f"vitamins:{u.id}",
        }
        trigger = str(sched.apscheduler.get_job(f"vitamins:{u.id}").trigger)
        assert "hour='22', minute='0'" in trigger
    finally:
        sched.apscheduler.shutdown(wait=False)


async def test_toggle_removes_and_readds_job_without_duplicates(
    session, session_factory
) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, FakeBot())  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        assert len(sched.apscheduler.get_jobs()) == 7

        u.vitamin_reminder_enabled = False
        session.commit()
        sched.reschedule_user(u.id)
        assert sched.apscheduler.get_job(f"vitamins:{u.id}") is None
        assert len(sched.apscheduler.get_jobs()) == 6

        u.vitamin_reminder_enabled = True
        session.commit()
        sched.reschedule_user(u.id)
        assert sched.apscheduler.get_job(f"vitamins:{u.id}") is not None
        assert len(sched.apscheduler.get_jobs()) == 7

        # Repeated reschedules never accumulate duplicates.
        sched.reschedule_user(u.id)
        sched.reschedule_user(u.id)
        assert len(sched.apscheduler.get_jobs()) == 7
    finally:
        sched.apscheduler.shutdown(wait=False)


async def test_removing_vitamins_job_is_safe_when_it_never_existed(
    session, session_factory
) -> None:
    u = _mk_user(session)
    u.vitamin_reminder_enabled = False
    session.commit()
    sched = ReflectionScheduler(session_factory, FakeBot())  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)  # first sync of a disabled user: nothing to remove
        assert sched.apscheduler.get_job(f"vitamins:{u.id}") is None
        assert len(sched.apscheduler.get_jobs()) == 6
    finally:
        sched.apscheduler.shutdown(wait=False)


async def test_changing_vitamin_time_reschedules_only_that_job(
    session, session_factory
) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, FakeBot())  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        settings_service.set_vitamin_reminder_time(session, u, "21:45")
        sched.reschedule_user(u.id)
        assert "hour='21', minute='45'" in str(sched.apscheduler.get_job(f"vitamins:{u.id}").trigger)
        # The other jobs stay where they were.
        assert "hour='8', minute='30'" in str(sched.apscheduler.get_job(f"morning:{u.id}").trigger)
        assert "hour='21', minute='30'" in str(sched.apscheduler.get_job(f"checkin:{u.id}").trigger)
        assert len(sched.apscheduler.get_jobs()) == 7
    finally:
        sched.apscheduler.shutdown(wait=False)


async def test_vitamin_trigger_uses_the_users_timezone(session, session_factory) -> None:
    u = _mk_user(session)
    u.timezone = "Europe/Moscow"
    session.commit()
    sched = ReflectionScheduler(session_factory, FakeBot())  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        assert str(sched.apscheduler.get_job(f"vitamins:{u.id}").trigger.timezone) == (
            "Europe/Moscow"
        )
        # A timezone change rebuilds the job through the existing reschedule.
        settings_service.set_timezone(session, u, "Europe/Berlin")
        sched.reschedule_user(u.id)
        assert str(sched.apscheduler.get_job(f"vitamins:{u.id}").trigger.timezone) == (
            "Europe/Berlin"
        )
    finally:
        sched.apscheduler.shutdown(wait=False)


# --------------------------------------------------------------------------
# 27. Settings service validation (canonical format_hhmm)
# --------------------------------------------------------------------------
def test_vitamin_time_validation(session: Session, user: User) -> None:
    settings_service.set_vitamin_reminder_time(session, user, "22:00")
    assert user.vitamin_reminder_time == "22:00"
    settings_service.set_vitamin_reminder_time(session, user, "7:5")
    assert user.vitamin_reminder_time == "07:05"

    user.vitamin_reminder_time = "22:00"
    session.commit()
    for bad in ["25:00", "12:99", "abc", ""]:
        with pytest.raises(InvalidSettingError):
            settings_service.set_vitamin_reminder_time(session, user, bad)
        assert user.vitamin_reminder_time == "22:00"  # old value survives


def test_new_user_vitamin_defaults(session: Session) -> None:
    u = UserRepository(session).get_or_create(42, display_name="X")
    assert u.vitamin_reminder_time == "22:00"
    assert u.vitamin_reminder_enabled is True


# --------------------------------------------------------------------------
# 28. Settings screen rendering and buttons
# --------------------------------------------------------------------------
def test_vitamin_settings_block_renders_both_states() -> None:
    on = texts.vitamin_settings_block(True, "22:00")
    assert "💊 Витамины: включено" in on
    assert "💊 Время напоминания: 22:00" in on
    off = texts.vitamin_settings_block(False, "22:00")
    assert "💊 Витамины: выключено" in off
    assert "💊 Время напоминания: 22:00" in off


def test_settings_keyboard_vitamin_buttons() -> None:
    buttons = {
        b.callback_data: b.text
        for row in keyboards.settings_keyboard(True).inline_keyboard
        for b in row
    }
    assert buttons["se:vt"] == "💊 Время витаминов"
    assert buttons["se:vto"] == "💊 Витамины: вкл"
    off = {
        b.callback_data: b.text
        for row in keyboards.settings_keyboard(False).inline_keyboard
        for b in row
    }
    assert off["se:vto"] == "💊 Витамины: выкл"


# --------------------------------------------------------------------------
# Handler flows: toggle persistence + FSM time entry (specs 12-14)
# --------------------------------------------------------------------------
async def _start_user(session_factory, tg_id: int = 111) -> int:
    await start_handlers.cmd_start(user_message(tg_id, "/start"), FakeState())
    with session_scope(session_factory) as s:
        return UserRepository(s).get_by_telegram_id(tg_id).id


async def test_settings_toggle_persists_and_reschedules(app_runtime, session_factory) -> None:  # noqa: F811
    app_runtime.scheduler.apscheduler.start()
    try:
        pk = await _start_user(session_factory)
        assert app_runtime.scheduler.apscheduler.get_job(f"vitamins:{pk}") is not None

        c = callback("se:vto")  # tapped on the bot's own settings message
        await settings_handlers.toggle_vitamin_reminder(c)

        with session_scope(session_factory) as s:
            assert UserRepository(s).get_by_telegram_id(111).vitamin_reminder_enabled is False
        assert app_runtime.scheduler.apscheduler.get_job(f"vitamins:{pk}") is None
        assert "💊 Витамины: выключено" in c.message.edits[-1]
        assert c.answered == 1

        c2 = callback("se:vto")
        await settings_handlers.toggle_vitamin_reminder(c2)
        assert app_runtime.scheduler.apscheduler.get_job(f"vitamins:{pk}") is not None
        assert "💊 Витамины: включено" in c2.message.edits[-1]
    finally:
        app_runtime.scheduler.apscheduler.shutdown(wait=False)


async def test_vitamin_toggle_uses_tapper_identity(app_runtime, session_factory) -> None:  # noqa: F811
    # The settings message was sent by the bot; the tap by 222 must toggle 222.
    app_runtime.scheduler.apscheduler.start()
    try:
        pk111 = await _start_user(session_factory, 111)
        pk222 = await _start_user(session_factory, 222)
        c = callback("se:vto", user_id=222)
        await settings_handlers.toggle_vitamin_reminder(c)
        with session_scope(session_factory) as s:
            assert UserRepository(s).get_by_telegram_id(222).vitamin_reminder_enabled is False
            assert UserRepository(s).get_by_telegram_id(111).vitamin_reminder_enabled is True
        assert app_runtime.scheduler.apscheduler.get_job(f"vitamins:{pk222}") is None
        assert app_runtime.scheduler.apscheduler.get_job(f"vitamins:{pk111}") is not None
    finally:
        app_runtime.scheduler.apscheduler.shutdown(wait=False)


async def test_vitamin_time_entry_via_settings_handler(app_runtime, session_factory) -> None:  # noqa: F811
    app_runtime.scheduler.apscheduler.start()
    try:
        pk = await _start_user(session_factory)
        state = FakeState()
        state.state = SettingsStates.waiting_vitamin_time
        msg = user_message(111, "21:45")
        await settings_handlers.set_vitamin_time(msg, state)
        assert state.state is None
        with session_scope(session_factory) as s:
            assert UserRepository(s).get_by_telegram_id(111).vitamin_reminder_time == "21:45"
        trigger = str(app_runtime.scheduler.apscheduler.get_job(f"vitamins:{pk}").trigger)
        assert "hour='21', minute='45'" in trigger
        assert "💊 Время напоминания: 21:45" in msg.answers[-1]
    finally:
        app_runtime.scheduler.apscheduler.shutdown(wait=False)


async def test_invalid_vitamin_time_keeps_old_value(app_runtime, session_factory) -> None:  # noqa: F811
    await _start_user(session_factory)
    state = FakeState()
    state.state = SettingsStates.waiting_vitamin_time
    msg = user_message(111, "25:00")
    await settings_handlers.set_vitamin_time(msg, state)
    assert msg.answers == [texts.INVALID_TIME]
    with session_scope(session_factory) as s:
        assert UserRepository(s).get_by_telegram_id(111).vitamin_reminder_time == "22:00"


# --------------------------------------------------------------------------
# 29. Migration 0004 -> 0005 over a v1.3-shaped database
# --------------------------------------------------------------------------
_V13_COUNTS = {
    "users": 1,
    "daily_entries": 2,
    "morning_intents": 1,
    "weekly_reflections": 1,
    "food_days": 1,
    "food_rule_results": 2,
    "weight_logs": 1,
}


def _build_v13_db(db: Path) -> None:
    _alembic(db, "upgrade", "0004_food_reflection")
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            INSERT INTO users (telegram_user_id, display_name, is_active, timezone,
                               checkin_time, reminder_time, morning_time,
                               food_morning_time, food_evening_time, weight_time,
                               food_rules, created_at, updated_at)
            VALUES (111, 'A', 1, 'Europe/Moscow', '21:30', '23:00', '08:30',
                    '09:15', '22:45', '10:00', 'water,steps',
                    '2026-09-20 08:00:00', '2026-10-01 21:35:00');

            INSERT INTO daily_entries (user_id, entry_date, day_score, mood_score,
                                       energy_score, reflection_text,
                                       questionnaire_version, created_at, updated_at)
            VALUES (1, '2026-09-26', 4, 3, 2, 'ok', 1,
                    '2026-09-26 21:40:00', '2026-09-26 21:40:00'),
                   (1, '2026-09-27', 5, 5, 5, NULL, 1,
                    '2026-09-27 21:40:00', '2026-09-27 21:40:00');

            INSERT INTO morning_intents (user_id, intention_date, main_intention,
                                         secondary_intention, main_outcome,
                                         secondary_outcome, created_at, updated_at)
            VALUES (1, '2026-09-26', 'релиз', 'зал', 'done', NULL,
                    '2026-09-26 08:31:00', '2026-09-26 21:31:00');

            INSERT INTO weekly_reflections (user_id, week_start_date, best_event,
                                            energy_drainer, want_more,
                                            created_at, updated_at)
            VALUES (1, '2026-09-21', 'b', 'e', 'w',
                    '2026-09-27 22:00:00', '2026-09-27 22:00:00');

            INSERT INTO food_days (user_id, food_date, focus_rule, focus_set_at,
                                   triggers, completed_at, created_at, updated_at)
            VALUES (1, '2026-09-26', 'water', '2026-09-26 08:46:00',
                    'stress', '2026-09-26 22:35:00',
                    '2026-09-26 08:46:00', '2026-09-26 22:35:00');

            INSERT INTO food_rule_results (food_day_id, rule_code, kept)
            VALUES (1, 'water', 1), (1, 'steps', 0);

            INSERT INTO weight_logs (user_id, week_start_date, logged_date, weight_kg,
                                     created_at, updated_at)
            VALUES (1, '2026-09-21', '2026-09-27', 92.4,
                    '2026-09-27 09:05:00', '2026-09-27 09:05:00');
            """
        )
        con.commit()
    finally:
        con.close()


def _v13_snapshot(db: Path) -> dict:
    con = sqlite3.connect(db)
    try:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in _V13_COUNTS
        }
        user = con.execute(
            "SELECT telegram_user_id, timezone, checkin_time, reminder_time, "
            "morning_time, food_morning_time, food_evening_time, weight_time, "
            "food_rules FROM users"
        ).fetchone()
        rows = {
            table: con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in _V13_COUNTS
            if table != "users"
        }
        return {"counts": counts, "user": user, "rows": rows}
    finally:
        con.close()


def test_v13_database_upgrades_without_losing_any_row(tmp_path: Path) -> None:
    db = tmp_path / "v13.db"
    _build_v13_db(db)
    before = _v13_snapshot(db)
    assert before["counts"] == _V13_COUNTS

    _alembic(db, "upgrade", "head")

    assert {"vitamin_reminder_time", "vitamin_reminder_enabled"} <= _columns(db, "users")
    after = _v13_snapshot(db)
    # Every pre-existing row and setting survives untouched...
    assert after == before
    assert after["counts"] == _V13_COUNTS
    assert after["rows"] == before["rows"]
    # ...and the two new fields carry the desired defaults.
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT vitamin_reminder_time, vitamin_reminder_enabled FROM users"
        ).fetchone() == ("22:00", 1)
    finally:
        con.close()


def test_vitamin_columns_downgrade_and_reupgrade(tmp_path: Path) -> None:
    db = tmp_path / "v13.db"
    _build_v13_db(db)
    _alembic(db, "upgrade", "head")

    _alembic(db, "downgrade", "0004_food_reflection")
    assert {"vitamin_reminder_time", "vitamin_reminder_enabled"}.isdisjoint(_columns(db, "users"))
    # Only the two new columns went away; all v1.3 data is intact.
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS

    _alembic(db, "upgrade", "head")
    assert {"vitamin_reminder_time", "vitamin_reminder_enabled"} <= _columns(db, "users")
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS
