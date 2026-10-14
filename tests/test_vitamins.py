"""v1.3.1 vitamins reminder + v1.3.2 generic acknowledgement + v1.3.3 granular
checklist: the daily question, its three dated item buttons, the stored
confirmations, scheduler job and sync semantics, settings service/validation/
render, /settings handlers and migrations 0005/0006/0007.

The reminder is deliberately *not* a reflection flow: exactly one question per
day in the user's own timezone, three independent confirmations, and a disabled
reminder means the job is removed from the scheduler outright. A tap records the
item on the day it was asked for and nothing else — there is no "not taken"
value, no streak and no stats.
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.orm import Session

import app.runtime as runtime_module
from app.bot import keyboards, notifications, texts
from app.bot.handlers import build_root_router
from app.bot.handlers import settings as settings_handlers
from app.bot.handlers import start as start_handlers
from app.bot.handlers import vitamins as vitamins_handlers
from app.bot.states import SettingsStates
from app.database.models import User, VitaminIntake, VitaminLog
from app.database.repositories import (
    UserRepository,
    VitaminIntakeRepository,
    VitaminLogRepository,
)
from app.database.session import session_scope
from app.runtime import Runtime, init_runtime
from app.scheduler.scheduler import ReflectionScheduler
from app.services import settings_service, vitamin_service
from app.services.settings_service import InvalidSettingError
from app.services.time_service import reflection_day
from app.services.vitamin_service import VitaminDateError
from tests.test_food import AlertCallback
from tests.test_handlers import (
    BOT_TG_ID,
    AnyBot,
    FakeChat,
    FakeMessage,
    FakeState,
    FakeUser,
    app_runtime,  # noqa: F401  (pytest fixture, imported for reuse)
    bot_message,
    callback,
    user_message,
)
from tests.test_migrations import _alembic, _columns, _tables
from tests.test_scheduler import FakeBot, _mk_user

#: The timezone new users get from the test settings (see the ``settings`` fixture).
_TZ = "Europe/Moscow"


def _today() -> date:
    return reflection_day(_TZ)


def _cb(data: str, user_id: int = 111) -> AlertCallback:
    """A tap on the bot's own reminder message, keeping the alert text."""
    return AlertCallback(data, user_id, bot_message(user_id))


def _log_dates(session_factory, telegram_user_id: int) -> list[date]:
    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(telegram_user_id)
        if user is None:
            return []
        return [
            log.vitamin_date
            for log in s.query(VitaminLog).filter_by(user_id=user.id).order_by(VitaminLog.id)
        ]


def _intakes(session_factory, telegram_user_id: int) -> list[tuple[date, str]]:
    """Confirmed items in tap order — the database keeps history, not layout."""
    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(telegram_user_id)
        if user is None:
            return []
        return [
            (row.vitamin_date, row.item_code)
            for row in s.query(VitaminIntake)
            .filter_by(user_id=user.id)
            .order_by(VitaminIntake.id)
        ]


def _buttons(markup) -> list[tuple[str, str]]:
    if markup is None:
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def _checklist(markup) -> list[str]:
    """Rendered labels of a checklist keyboard, [] once it is removed entirely."""
    return [text for text, _data in _buttons(markup)]


# --------------------------------------------------------------------------
# 19, 27. The notification itself (v1.3.3: a question with three item buttons)
# --------------------------------------------------------------------------
async def test_send_vitamin_reminder_asks_with_three_dated_buttons() -> None:
    bot = FakeBot()
    target = date(2026, 10, 14)
    await notifications.send_vitamin_reminder(bot, 111, target, [])  # type: ignore[arg-type]
    assert texts.VITAMIN_REMINDER == "💊 Что из витаминов ты сегодня принял?"
    assert bot.sent == [(111, texts.VITAMIN_REMINDER)]
    assert _buttons(bot.kwargs[0]["reply_markup"]) == [
        ("⬜ Витамин D", "vit:item:vitamin_d:2026-10-14"),
        ("⬜ Омега-3", "vit:item:omega_3:2026-10-14"),
        ("⬜ Opti-Men", "vit:item:opti_men:2026-10-14"),
    ]


async def test_send_vitamin_reminder_shows_the_already_confirmed_items() -> None:
    # A checklist sent after a partial evening must not ask for what is stored.
    bot = FakeBot()
    await notifications.send_vitamin_reminder(  # type: ignore[call-arg]
        bot, 111, date(2026, 10, 14), ["omega_3"]
    )
    assert _checklist(bot.kwargs[0]["reply_markup"]) == [
        "⬜ Витамин D",
        "✅ Омега-3",
        "⬜ Opti-Men",
    ]


def test_vitamin_reminder_keyboard_is_three_buttons_in_catalog_order() -> None:
    markup = keyboards.vitamin_reminder_keyboard(date(2026, 10, 14), ["opti_men"])
    rows = markup.inline_keyboard
    # One item per line: a single compact message, never three messages.
    assert [len(row) for row in rows] == [1, 1, 1]
    assert _checklist(markup) == ["⬜ Витамин D", "⬜ Омега-3", "✅ Opti-Men"]
    data = [b.callback_data for row in rows for b in row]
    assert data == [
        "vit:item:vitamin_d:2026-10-14",
        "vit:item:omega_3:2026-10-14",
        "vit:item:opti_men:2026-10-14",
    ]
    # Stable codes and dates only, stateless and well under Telegram's 64-byte cap.
    assert all(len(b.callback_data.encode()) <= 64 for row in rows for b in row)


def test_vitamin_catalog_is_exactly_three_stable_items() -> None:
    assert vitamin_service.VITAMIN_ITEMS == (
        ("vitamin_d", "Витамин D"),
        ("omega_3", "Омега-3"),
        ("opti_men", "Opti-Men"),
    )
    assert vitamin_service.ITEM_COUNT == 3
    assert vitamin_service.item_label("omega_3") == "Омега-3"
    assert vitamin_service.validate_item("vitamin_d") is True
    assert vitamin_service.validate_item("витамин_d") is False
    assert vitamin_service.validate_item("") is False


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


# ==========================================================================
# v1.3.2 — the acknowledgement: service, handlers, scheduler skip, 0006
# ==========================================================================

# --------------------------------------------------------------------------
# 3-9. The record itself: one row per user per day, idempotent, dated by the
#      button rather than by the moment of the tap
# --------------------------------------------------------------------------
def test_mark_taken_stores_one_log_for_the_day(session: Session, user: User) -> None:
    day = _today()
    log = vitamin_service.mark_taken(session, user, day)
    assert log.vitamin_date == day
    assert log.taken_at is not None
    assert log.id is not None
    assert vitamin_service.is_taken(session, user, day) is True
    assert vitamin_service.is_taken(session, user, day - timedelta(days=1)) is False


def test_double_confirmation_keeps_one_row_and_the_first_time(
    session: Session, user: User
) -> None:
    first_tap = datetime(2026, 10, 6, 19, 0, tzinfo=UTC)
    again_tap = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)
    day = reflection_day(user.timezone, first_tap)

    first = vitamin_service.mark_taken(session, user, day, now=first_tap)
    second = vitamin_service.mark_taken(session, user, day, now=again_tap)

    assert second.id == first.id
    # The first tap is the fact that gets remembered.
    assert second.taken_at == first.taken_at
    assert session.query(VitaminLog).count() == 1


def test_repository_writes_no_second_row_on_a_racing_tap(session: Session, user: User) -> None:
    repo = VitaminLogRepository(session)
    day = date(2026, 10, 5)
    moment = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    repo.mark_taken(user_id=user.id, vitamin_date=day, taken_at=moment)
    later = repo.mark_taken(
        user_id=user.id, vitamin_date=day, taken_at=moment + timedelta(minutes=1)
    )

    assert later.taken_at == moment.replace(tzinfo=None)
    assert session.query(VitaminLog).count() == 1


def test_midnight_tap_still_belongs_to_the_evening_it_was_asked(
    session: Session, user: User
) -> None:
    # 22:00 MSK on Oct 7 = 19:00 UTC; the tap at 01:30 MSK on Oct 8 is still
    # Reflection Day Oct 7, and the date travels with the button anyway.
    sent_at = datetime(2026, 10, 7, 19, 0, tzinfo=UTC)
    tapped_at = datetime(2026, 10, 7, 22, 30, tzinfo=UTC)
    target = reflection_day(user.timezone, sent_at)
    assert target == date(2026, 10, 7)
    assert reflection_day(user.timezone, tapped_at) == date(2026, 10, 7)

    log = vitamin_service.mark_taken(session, user, target, now=tapped_at)

    assert log.vitamin_date == date(2026, 10, 7)
    # taken_at is the actual confirmation moment, not the reminder's day.
    assert log.taken_at.replace(tzinfo=None) == tapped_at.replace(tzinfo=None)


@pytest.mark.parametrize(
    ("delta", "allowed"),
    [(0, True), (-1, True), (-2, False), (1, False)],
)
def test_ack_window_is_the_current_or_previous_reflection_day(
    session: Session, user: User, delta: int, allowed: bool
) -> None:
    # 09:00 MSK on Oct 8: the rollover already happened, so yesterday's button
    # may still confirm yesterday, but the day before may not.
    now = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
    assert reflection_day(user.timezone, now) == date(2026, 10, 8)
    target = date(2026, 10, 8) + timedelta(days=delta)

    if allowed:
        assert vitamin_service.mark_taken(session, user, target, now=now).vitamin_date == target
    else:
        with pytest.raises(VitaminDateError):
            vitamin_service.mark_taken(session, user, target, now=now)
        assert vitamin_service.get_log(session, user, target) is None
        assert texts.VITAMIN_STALE == "Эта кнопка уже устарела."


def test_future_or_stale_button_never_extends_history(session: Session, user: User) -> None:
    day = date(2026, 10, 8)
    with pytest.raises(VitaminDateError):
        vitamin_service.check_date(user, day + timedelta(days=30), today=day)
    with pytest.raises(VitaminDateError):
        vitamin_service.check_date(user, day - timedelta(days=40), today=day)
    assert session.query(VitaminLog).count() == 0


# --------------------------------------------------------------------------
# 16, 30-35. The handler: stateless, dated, tapper-authorized
# --------------------------------------------------------------------------
async def test_tap_saves_the_day_and_replaces_the_question(app_runtime) -> None:  # noqa: F811
    day = _today()
    cb = _cb(f"vit:yes:{day.isoformat()}")
    # No FSMContext parameter at all: a dated button survives a restart.
    assert "state" not in inspect.signature(vitamins_handlers.cb_confirm).parameters

    await vitamins_handlers.cb_confirm(cb)

    assert texts.VITAMIN_TAKEN == "✅ Витамины приняты."
    assert cb.alerts == []
    assert cb.answered == 1
    assert cb.message.edits == [texts.VITAMIN_TAKEN]
    assert cb.message.edit_markups == [None]  # the ✅ Да button is gone


async def test_yesterdays_button_still_confirms_yesterday(
    app_runtime, session_factory  # noqa: F811
) -> None:
    yesterday = _today() - timedelta(days=1)
    await vitamins_handlers.cb_confirm(_cb(f"vit:yes:{yesterday.isoformat()}"))
    assert _log_dates(session_factory, 111) == [yesterday]


async def test_double_tap_creates_exactly_one_record(app_runtime, session_factory) -> None:  # noqa: F811
    day = _today().isoformat()
    first = _cb(f"vit:yes:{day}")
    await vitamins_handlers.cb_confirm(first)
    second = _cb(f"vit:yes:{day}")
    await vitamins_handlers.cb_confirm(second)

    assert second.answered == 1
    assert second.message.edits == [texts.VITAMIN_TAKEN]
    assert _log_dates(session_factory, 111) == [_today()]


async def test_tap_records_the_tapper_not_the_message_author(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # cb.from_user is 222, while cb.message.from_user is the bot (777).
    day = _today()
    await vitamins_handlers.cb_confirm(_cb(f"vit:yes:{day.isoformat()}", user_id=222))

    assert _log_dates(session_factory, 222) == [day]
    assert _log_dates(session_factory, 111) == []
    with session_scope(session_factory) as s:
        assert UserRepository(s).get_by_telegram_id(BOT_TG_ID) is None


@pytest.mark.parametrize("delta", [-2, 1])
async def test_stale_or_future_tap_alerts_and_writes_nothing(
    app_runtime, session_factory, delta: int  # noqa: F811
) -> None:
    cb = _cb(f"vit:yes:{(_today() + timedelta(days=delta)).isoformat()}")
    await vitamins_handlers.cb_confirm(cb)

    assert cb.alerts == [texts.VITAMIN_STALE]
    assert cb.message.edits == []
    assert _log_dates(session_factory, 111) == []


@pytest.mark.parametrize("data", ["vit:yes:", "vit:yes:not-a-date", "vit:yes:2026-13-01"])
async def test_malformed_callback_is_refused(app_runtime, data: str) -> None:  # noqa: F811
    cb = _cb(data)
    await vitamins_handlers.cb_confirm(cb)
    assert cb.alerts == [texts.VITAMIN_STALE]
    assert cb.message.edits == []


def test_vitamins_router_is_registered_before_the_catch_all() -> None:
    names = [router.name for router in build_root_router().sub_routers]
    assert "vitamins" in names
    assert names.index("vitamins") < names.index("unauthorized")
    # Two callback routes: the granular item buttons and the legacy v1.3.2 one.
    handlers = [h.callback for h in vitamins_handlers.router.callback_query.handlers]
    assert handlers == [vitamins_handlers.cb_confirm_item, vitamins_handlers.cb_confirm]


# --------------------------------------------------------------------------
# 15, 36. The scheduler asks once and stops asking once confirmed
# --------------------------------------------------------------------------
async def test_vitamin_job_sends_the_question_with_the_dated_buttons(
    session, session_factory
) -> None:
    u = _mk_user(session)
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)

    day = reflection_day(u.timezone).isoformat()
    assert bot.sent == [(u.telegram_user_id, texts.VITAMIN_REMINDER)]
    assert bot.buttons == [
        f"vit:item:vitamin_d:{day}",
        f"vit:item:omega_3:{day}",
        f"vit:item:opti_men:{day}",
    ]
    assert _checklist(bot.kwargs[0]["reply_markup"]) == [
        "⬜ Витамин D",
        "⬜ Омега-3",
        "⬜ Opti-Men",
    ]


async def test_vitamin_job_skips_an_already_confirmed_day(
    session, session_factory
) -> None:
    u = _mk_user(session)
    vitamin_service.mark_taken(session, u, reflection_day(u.timezone))
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)
    assert bot.sent == []


async def test_confirmation_of_yesterday_does_not_silence_today(
    session, session_factory
) -> None:
    # Only the day the button was baked for is closed: yesterday's tap does not
    # suppress today's question.
    u = _mk_user(session)
    vitamin_service.mark_taken(session, u, reflection_day(u.timezone) - timedelta(days=1))
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._vitamin_reminder_job(u.id)
    assert len(bot.sent) == 1


async def test_a_confirmation_survives_a_restart_without_any_storage(
    session, session_factory
) -> None:
    u = _mk_user(session)
    day = reflection_day(u.timezone)
    vitamin_service.mark_taken(session, u, day)

    # A brand new scheduler (what a restart gives) still sees the record in the
    # database, so the question is not asked a second time.
    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._vitamin_reminder_job(u.id)
    assert bot.sent == []
    with session_scope(session_factory) as s:
        assert VitaminLogRepository(s).get(u.id, day) is not None


# --------------------------------------------------------------------------
# 38. Migration 0005 -> 0006 over a v1.3.1-shaped database
# --------------------------------------------------------------------------
def _build_v131_db(db: Path) -> None:
    _build_v13_db(db)
    _alembic(db, "upgrade", "0005_vitamin_reminder")
    assert {"vitamin_reminder_time", "vitamin_reminder_enabled"} <= _columns(db, "users")


def test_v131_database_upgrades_to_the_log_table_without_losing_anything(
    tmp_path: Path,
) -> None:
    db = tmp_path / "v131.db"
    _build_v131_db(db)
    before = _v13_snapshot(db)

    # Explicitly to 0006, the v1.3.2 head: the chain below it must stay intact.
    _alembic(db, "upgrade", "0006_vitamin_log")

    assert "vitamin_logs" in _tables(db)
    assert _v13_snapshot(db) == before
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS
    assert {"id", "user_id", "vitamin_date", "taken_at", "created_at"} == _columns(
        db, "vitamin_logs"
    )
    con = sqlite3.connect(db)
    try:
        # The new table starts empty and the v1.3.1 settings are untouched.
        assert con.execute("SELECT COUNT(*) FROM vitamin_logs").fetchone()[0] == 0
        assert con.execute(
            "SELECT vitamin_reminder_time, vitamin_reminder_enabled FROM users"
        ).fetchone() == ("22:00", 1)
        assert con.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0006_vitamin_log",)
    finally:
        con.close()


def test_unique_constraint_is_enforced_by_the_database(tmp_path: Path) -> None:
    db = tmp_path / "uniq.db"
    _alembic(db, "upgrade", "head")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute(
            "INSERT INTO users (telegram_user_id, display_name, is_active, timezone, "
            "checkin_time, reminder_time, morning_time, food_morning_time, "
            "food_evening_time, weight_time, vitamin_reminder_time, "
            "vitamin_reminder_enabled, created_at, updated_at) "
            "VALUES (111, 'A', 1, 'Europe/Moscow', '21:30', '23:00', '08:30', "
            "'08:45', '22:30', '09:00', '22:00', 1, "
            "'2026-10-07 08:00:00', '2026-10-07 08:00:00')"
        )
        con.execute(
            "INSERT INTO vitamin_logs (user_id, vitamin_date, taken_at, created_at) "
            "VALUES (1, '2026-10-07', '2026-10-07 19:00:00', '2026-10-07 19:00:00')"
        )
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO vitamin_logs (user_id, vitamin_date, taken_at, created_at) "
                "VALUES (1, '2026-10-07', '2026-10-07 21:00:00', '2026-10-07 21:00:00')"
            )
        # Another day for the same user, and the same day for another user, are fine.
        con.execute(
            "INSERT INTO vitamin_logs (user_id, vitamin_date, taken_at, created_at) "
            "VALUES (1, '2026-10-06', '2026-10-06 19:00:00', '2026-10-06 19:00:00')"
        )
        assert con.execute("SELECT COUNT(*) FROM vitamin_logs").fetchone()[0] == 2
    finally:
        con.close()


def test_vitamin_logs_downgrade_and_reupgrade(tmp_path: Path) -> None:
    db = tmp_path / "v131.db"
    _build_v131_db(db)
    _alembic(db, "upgrade", "head")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO vitamin_logs (user_id, vitamin_date, taken_at, created_at) "
            "VALUES (1, '2026-10-06', '2026-10-06 19:00:00', '2026-10-06 19:00:00')"
        )
        con.commit()
    finally:
        con.close()

    # Rolling back removes only vitamin_logs.
    _alembic(db, "downgrade", "0005_vitamin_reminder")
    assert "vitamin_logs" not in _tables(db)
    assert {"vitamin_reminder_time", "vitamin_reminder_enabled"} <= _columns(db, "users")
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS

    _alembic(db, "upgrade", "head")
    assert "vitamin_logs" in _tables(db)
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS


# ==========================================================================
# v1.3.3 — the granular checklist: three separate acknowledgements
# ==========================================================================

_ITEMS = [code for code, _label in vitamin_service.VITAMIN_ITEMS]

#: Telegram's exact words for an edit that would not change anything.
_NOT_MODIFIED = TelegramBadRequest(
    "editMessageText",
    "Bad Request: message is not modified: specified new message content and reply "
    "markup are exactly the same as a current content and reply markup of the message",
)


class _RejectingMessage(FakeMessage):
    """The bot's reminder message, answering every edit with one Telegram error."""

    def __init__(self, error: Exception) -> None:
        super().__init__(FakeChat(111), FakeUser(BOT_TG_ID))
        self._error = error

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> None:
        self.edits.append(text)
        self.edit_markups.append(reply_markup)
        raise self._error


# --------------------------------------------------------------------------
# 4, 9, 22-24. The service: one row per item, canonical order, no un-check
# --------------------------------------------------------------------------
def test_each_item_is_recorded_separately_and_read_in_catalog_order(
    session: Session, user: User
) -> None:
    day = _today()
    # Tapped in the reverse order: the catalog, not the database, decides how the
    # checklist renders.
    vitamin_service.mark_item_taken(session, user, day, "opti_men")
    vitamin_service.mark_item_taken(session, user, day, "vitamin_d")

    assert vitamin_service.get_taken_items(session, user, day) == ["vitamin_d", "opti_men"]
    assert vitamin_service.is_item_taken(session, user, day, "vitamin_d") is True
    assert vitamin_service.is_item_taken(session, user, day, "omega_3") is False
    # One tap is deliberately NOT a closed day.
    assert vitamin_service.all_items_taken(session, user, day) is False

    vitamin_service.mark_item_taken(session, user, day, "omega_3")
    assert vitamin_service.all_items_taken(session, user, day) is True
    assert session.query(VitaminIntake).count() == 3


def test_unknown_item_is_refused_without_writing(session: Session, user: User) -> None:
    with pytest.raises(vitamin_service.VitaminItemError):
        vitamin_service.mark_item_taken(session, user, _today(), "vitamin_c")
    assert session.query(VitaminIntake).count() == 0


def test_items_on_different_days_are_independent(session: Session, user: User) -> None:
    day = _today()
    vitamin_service.mark_item_taken(session, user, day, "vitamin_d")
    vitamin_service.mark_item_taken(session, user, day - timedelta(days=1), "omega_3")

    assert vitamin_service.get_taken_items(session, user, day) == ["vitamin_d"]
    assert vitamin_service.get_taken_items(session, user, day - timedelta(days=1)) == ["omega_3"]


def test_repository_writes_no_second_row_for_a_racing_item_tap(
    session: Session, user: User
) -> None:
    repo = VitaminIntakeRepository(session)
    day = date(2026, 10, 5)
    moment = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    repo.mark_taken(user_id=user.id, vitamin_date=day, item_code="omega_3", taken_at=moment)
    later = repo.mark_taken(
        user_id=user.id,
        vitamin_date=day,
        item_code="omega_3",
        taken_at=moment + timedelta(minutes=30),
    )

    # The first tap is the fact that gets remembered, per item.
    assert later.taken_at == moment.replace(tzinfo=None)
    assert session.query(VitaminIntake).count() == 1


def test_every_item_keeps_its_own_first_timestamp(session: Session, user: User) -> None:
    # The v1.3.2 midnight scenario: the question goes out at 22:00 MSK on Oct 14,
    # the bot restarts, and the remaining taps land at 00:40 and 01:15 MSK. The
    # day comes from the button, each taken_at from the moment of that tap.
    target = date(2026, 10, 14)
    taps = {
        "vitamin_d": datetime(2026, 10, 14, 19, 10, tzinfo=UTC),
        "omega_3": datetime(2026, 10, 14, 21, 40, tzinfo=UTC),
        "opti_men": datetime(2026, 10, 14, 22, 15, tzinfo=UTC),
    }
    for code, when in taps.items():
        assert reflection_day(user.timezone, when) == target
        row = vitamin_service.mark_item_taken(session, user, target, code, now=when)
        assert row.vitamin_date == target

    stored = {
        row.item_code: row.taken_at.replace(tzinfo=None)
        for row in session.query(VitaminIntake).order_by(VitaminIntake.id)
    }
    assert stored == {code: when.replace(tzinfo=None) for code, when in taps.items()}

    # A repeat tap on Omega an hour later changes nothing about its time.
    again = vitamin_service.mark_item_taken(
        session, user, target, "omega_3", now=datetime(2026, 10, 14, 22, 40, tzinfo=UTC)
    )
    assert again.taken_at.replace(tzinfo=None) == taps["omega_3"].replace(tzinfo=None)
    assert session.query(VitaminIntake).count() == 3


@pytest.mark.parametrize("item", _ITEMS)
@pytest.mark.parametrize(("delta", "allowed"), [(0, True), (-1, True), (-2, False), (1, False)])
def test_every_item_shares_the_acknowledgement_window(
    session: Session, user: User, item: str, delta: int, allowed: bool
) -> None:
    now = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
    assert reflection_day(user.timezone, now) == date(2026, 10, 8)
    target = date(2026, 10, 8) + timedelta(days=delta)

    if allowed:
        row = vitamin_service.mark_item_taken(session, user, target, item, now=now)
        assert (row.vitamin_date, row.item_code) == (target, item)
    else:
        with pytest.raises(VitaminDateError):
            vitamin_service.mark_item_taken(session, user, target, item, now=now)
        assert vitamin_service.get_taken_items(session, user, target) == []
        assert session.query(VitaminIntake).count() == 0


# --------------------------------------------------------------------------
# 28, 2, 24. The handler: the same message is re-rendered after every tap
# --------------------------------------------------------------------------
async def test_three_taps_walk_the_checklist_to_completion(
    app_runtime, session_factory  # noqa: F811
) -> None:
    day = _today()
    # One and the same Telegram message is updated in place, not re-sent.
    message = bot_message(111)

    await vitamins_handlers.cb_confirm_item(AlertCallback(f"vit:item:vitamin_d:{day}", 111, message))
    assert _checklist(message.edit_markups[-1]) == ["✅ Витамин D", "⬜ Омега-3", "⬜ Opti-Men"]
    assert _intakes(session_factory, 111) == [(day, "vitamin_d")]

    await vitamins_handlers.cb_confirm_item(AlertCallback(f"vit:item:omega_3:{day}", 111, message))
    assert _checklist(message.edit_markups[-1]) == ["✅ Витамин D", "✅ Омега-3", "⬜ Opti-Men"]
    assert _intakes(session_factory, 111) == [(day, "vitamin_d"), (day, "omega_3")]

    final = AlertCallback(f"vit:item:opti_men:{day}", 111, message)
    await vitamins_handlers.cb_confirm_item(final)

    assert texts.VITAMIN_ALL_TAKEN == "✅ Все витамины приняты."
    assert message.edits == [
        texts.VITAMIN_REMINDER,
        texts.VITAMIN_REMINDER,
        texts.VITAMIN_ALL_TAKEN,
    ]
    # The checklist was on screen for the first two taps and gone after the third.
    assert all(markup is not None for markup in message.edit_markups[:-1])
    assert message.edit_markups[-1] is None
    assert _checklist(message.edit_markups[-1]) == []
    assert final.message is message
    assert _intakes(session_factory, 111) == [
        (day, "vitamin_d"),
        (day, "omega_3"),
        (day, "opti_men"),
    ]


async def test_repeat_tap_is_idempotent_and_never_unchecks(
    app_runtime, session_factory  # noqa: F811
) -> None:
    day = _today()
    data = f"vit:item:vitamin_d:{day}"
    first = _cb(data)
    await vitamins_handlers.cb_confirm_item(first)

    again = _cb(data)
    await vitamins_handlers.cb_confirm_item(again)

    assert again.answered == 1
    assert again.alerts == []
    # Tapping an ✅ item again keeps it ✅: this is an acknowledgement, not a toggle.
    assert _checklist(again.message.edit_markups[-1]) == ["✅ Витамин D", "⬜ Омега-3", "⬜ Opti-Men"]
    assert _intakes(session_factory, 111) == [(day, "vitamin_d")]


async def test_yesterdays_item_button_still_confirms_yesterday(
    app_runtime, session_factory  # noqa: F811
) -> None:
    yesterday = _today() - timedelta(days=1)
    await vitamins_handlers.cb_confirm_item(_cb(f"vit:item:omega_3:{yesterday}"))
    assert _intakes(session_factory, 111) == [(yesterday, "omega_3")]


@pytest.mark.parametrize("item", _ITEMS)
@pytest.mark.parametrize("delta", [-2, 1])
async def test_stale_or_future_item_tap_alerts_and_writes_nothing(
    app_runtime, session_factory, item: str, delta: int  # noqa: F811
) -> None:
    cb = _cb(f"vit:item:{item}:{_today() + timedelta(days=delta)}")
    await vitamins_handlers.cb_confirm_item(cb)

    assert cb.alerts == [texts.VITAMIN_STALE]
    assert cb.message.edits == []
    assert _intakes(session_factory, 111) == []


@pytest.mark.parametrize("item", _ITEMS)
@pytest.mark.parametrize("delta", [0, -1])
async def test_current_or_previous_day_item_tap_is_recorded(
    app_runtime, session_factory, item: str, delta: int  # noqa: F811
) -> None:
    day = _today() + timedelta(days=delta)
    cb = _cb(f"vit:item:{item}:{day}")
    await vitamins_handlers.cb_confirm_item(cb)

    assert cb.alerts == []
    assert cb.answered == 1
    assert _intakes(session_factory, 111) == [(day, item)]


async def test_item_tap_records_the_tapper_not_the_message_author(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # cb.from_user is 222, while cb.message.from_user is the bot (777).
    day = _today()
    await vitamins_handlers.cb_confirm_item(_cb(f"vit:item:vitamin_d:{day}", user_id=222))

    assert _intakes(session_factory, 222) == [(day, "vitamin_d")]
    assert _intakes(session_factory, 111) == []
    with session_scope(session_factory) as s:
        assert UserRepository(s).get_by_telegram_id(BOT_TG_ID) is None


# --------------------------------------------------------------------------
# 21. Buttons that are not in the catalog
# --------------------------------------------------------------------------
async def test_item_outside_the_catalog_is_refused_neutrally(
    app_runtime, session_factory  # noqa: F811
) -> None:
    cb = _cb(f"vit:item:vitamin_c:{_today()}")
    await vitamins_handlers.cb_confirm_item(cb)

    assert texts.VITAMIN_INVALID == "Эта кнопка больше не актуальна."
    assert cb.alerts == [texts.VITAMIN_INVALID]
    assert cb.message.edits == []
    assert _intakes(session_factory, 111) == []


@pytest.mark.parametrize(
    "data",
    [
        "vit:item:vitamin_d",  # no date at all
        "vit:item:vitamin_d:not-a-date",
        "vit:item:vitamin_d:2026-13-01",
        "vit:item:vitamin_d:2026-10-14:surprise",  # wrong shape
        "vit:item::2026-10-14",  # no code
    ],
)
async def test_malformed_item_callback_is_refused(
    app_runtime, session_factory, data: str  # noqa: F811
) -> None:
    cb = _cb(data)
    await vitamins_handlers.cb_confirm_item(cb)
    assert cb.alerts == [texts.VITAMIN_INVALID]
    assert cb.message.edits == []
    assert _intakes(session_factory, 111) == []


# --------------------------------------------------------------------------
# 14, 31. The Telegram edge the v1.3.2 review left open
# --------------------------------------------------------------------------
async def test_repeat_tap_survives_message_is_not_modified(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Re-tapping an already-rendered state produces an identical message, which
    # Telegram reports as an error. The wanted state is already on screen.
    day = _today()
    data = f"vit:item:vitamin_d:{day}"
    await vitamins_handlers.cb_confirm_item(_cb(data))

    cb = AlertCallback(data, 111, _RejectingMessage(_NOT_MODIFIED))
    await vitamins_handlers.cb_confirm_item(cb)

    assert cb.answered == 1
    assert cb.message.edits == [texts.VITAMIN_REMINDER]
    assert _intakes(session_factory, 111) == [(day, "vitamin_d")]


async def test_completion_survives_message_is_not_modified(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The third tap of a checklist that was somehow already fully rendered.
    for item in _ITEMS[:-1]:
        await vitamins_handlers.cb_confirm_item(_cb(f"vit:item:{item}:{_today()}"))

    cb = AlertCallback(f"vit:item:{_ITEMS[-1]}:{_today()}", 111, _RejectingMessage(_NOT_MODIFIED))
    await vitamins_handlers.cb_confirm_item(cb)

    assert cb.message.edits == [texts.VITAMIN_ALL_TAKEN]
    assert len(_intakes(session_factory, 111)) == 3


async def test_any_other_telegram_edit_error_still_propagates(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Only the no-op complaint is tolerated; nothing else is swallowed.
    cb = AlertCallback(
        f"vit:item:vitamin_d:{_today()}",
        111,
        _RejectingMessage(TelegramBadRequest("editMessageText", "Bad Request: chat not found")),
    )
    with pytest.raises(TelegramBadRequest, match="chat not found"):
        await vitamins_handlers.cb_confirm_item(cb)
    # The tap itself was already persisted before the render failed.
    assert len(_intakes(session_factory, 111)) == 1


# --------------------------------------------------------------------------
# 12, 22, 29. A partial evening across a restart, with no FSM anywhere
# --------------------------------------------------------------------------
async def test_partial_evening_survives_a_restart(settings, session_factory) -> None:
    def fresh_runtime() -> Runtime:
        # What a restart gives: a brand new runtime, scheduler and storage over
        # the same database file.
        return Runtime(
            settings=settings,
            engine=session_factory.kw["bind"],
            session_factory=session_factory,
            scheduler=ReflectionScheduler(session_factory, AnyBot()),  # type: ignore[arg-type]
            bot=AnyBot(),  # type: ignore[arg-type]
        )

    day = _today()
    init_runtime(fresh_runtime())
    try:
        await vitamins_handlers.cb_confirm_item(_cb(f"vit:item:vitamin_d:{day}"))

        init_runtime(fresh_runtime())
        assert "state" not in inspect.signature(vitamins_handlers.cb_confirm_item).parameters

        cb = _cb(f"vit:item:omega_3:{day}")
        await vitamins_handlers.cb_confirm_item(cb)
    finally:
        runtime_module._runtime = None

    assert _intakes(session_factory, 111) == [(day, "vitamin_d"), (day, "omega_3")]
    assert _checklist(cb.message.edit_markups[-1]) == ["✅ Витамин D", "✅ Омега-3", "⬜ Opti-Men"]


# --------------------------------------------------------------------------
# 16, 17, 34. The legacy v1.3.2 generic button keeps working
# --------------------------------------------------------------------------
async def test_legacy_yes_button_confirms_generically_and_stays_legacy(
    app_runtime, session_factory  # noqa: F811
) -> None:
    day = _today()
    cb = _cb(f"vit:yes:{day}")
    await vitamins_handlers.cb_confirm(cb)

    assert cb.alerts == []
    assert cb.answered == 1
    assert cb.message.edits == [texts.VITAMIN_TAKEN]  # the old success text
    assert cb.message.edit_markups == [None]
    assert _log_dates(session_factory, 111) == [day]
    # A generic tap said nothing about which supplement: no items are invented.
    assert _intakes(session_factory, 111) == []


async def test_legacy_stale_button_still_refused(app_runtime, session_factory) -> None:  # noqa: F811
    cb = _cb(f"vit:yes:{_today() - timedelta(days=2)}")
    await vitamins_handlers.cb_confirm(cb)
    assert cb.alerts == [texts.VITAMIN_STALE]
    assert _log_dates(session_factory, 111) == []
    assert _intakes(session_factory, 111) == []


async def test_legacy_repeat_tap_survives_message_is_not_modified(
    app_runtime, session_factory  # noqa: F811
) -> None:
    day = _today()
    await vitamins_handlers.cb_confirm(_cb(f"vit:yes:{day}"))

    cb = AlertCallback(f"vit:yes:{day}", 111, _RejectingMessage(_NOT_MODIFIED))
    await vitamins_handlers.cb_confirm(cb)

    assert cb.message.edits == [texts.VITAMIN_TAKEN]
    assert _log_dates(session_factory, 111) == [day]


# --------------------------------------------------------------------------
# 18, 35. The scheduler asks the granular question at most once per day
# --------------------------------------------------------------------------
async def test_vitamin_job_marks_a_half_confirmed_day_in_the_checklist(
    session, session_factory
) -> None:
    u = _mk_user(session)
    vitamin_service.mark_item_taken(session, u, reflection_day(u.timezone), "vitamin_d")

    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._vitamin_reminder_job(u.id)  # type: ignore[arg-type]

    assert bot.sent == [(u.telegram_user_id, texts.VITAMIN_REMINDER)]
    assert _checklist(bot.kwargs[0]["reply_markup"]) == [
        "✅ Витамин D",
        "⬜ Омега-3",
        "⬜ Opti-Men",
    ]


async def test_vitamin_job_stops_asking_once_all_three_items_are_confirmed(
    session, session_factory
) -> None:
    u = _mk_user(session)
    day = reflection_day(u.timezone)
    for item in _ITEMS:
        vitamin_service.mark_item_taken(session, u, day, item)

    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._vitamin_reminder_job(u.id)  # type: ignore[arg-type]
    assert bot.sent == []


async def test_legacy_acknowledgement_closes_the_day_without_becoming_items(
    session, session_factory
) -> None:
    # Transition rule: this evening was already answered in production, so the
    # granular checklist is not sent for it — and the old row is not reinterpreted.
    u = _mk_user(session)
    vitamin_service.mark_taken(session, u, reflection_day(u.timezone))

    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._vitamin_reminder_job(u.id)  # type: ignore[arg-type]

    assert bot.sent == []
    with session_scope(session_factory) as s:
        assert s.query(VitaminIntake).count() == 0
        assert s.query(VitaminLog).count() == 1


# --------------------------------------------------------------------------
# 36. Migration 0006 -> 0007 over a production-shaped v1.3.2 database
# --------------------------------------------------------------------------
def _build_v132_db(db: Path) -> None:
    """A v1.3.2 database as production has it: all v1.3 data plus a legacy log."""
    _build_v131_db(db)
    _alembic(db, "upgrade", "0006_vitamin_log")
    assert "vitamin_logs" in _tables(db)
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            INSERT INTO vitamin_logs (user_id, vitamin_date, taken_at, created_at)
            VALUES (1, '2026-10-06', '2026-10-06 19:05:00', '2026-10-06 19:05:00'),
                   (1, '2026-10-07', '2026-10-07 22:03:00', '2026-10-07 22:03:00');
            """
        )
        con.commit()
    finally:
        con.close()


def _vitamin_logs(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT * FROM vitamin_logs ORDER BY id").fetchall()
    finally:
        con.close()


def test_v132_database_upgrades_to_granular_items_without_losing_anything(
    tmp_path: Path,
) -> None:
    db = tmp_path / "v132.db"
    _build_v132_db(db)
    before = _v13_snapshot(db)
    legacy_before = _vitamin_logs(db)
    assert len(legacy_before) == 2

    _alembic(db, "upgrade", "0007_vitamin_intakes")

    assert _v13_snapshot(db) == before
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS
    # The legacy generic acknowledgements stay exactly as they were.
    assert _vitamin_logs(db) == legacy_before
    assert {"id", "user_id", "vitamin_date", "item_code", "taken_at", "created_at"} == _columns(
        db, "vitamin_intakes"
    )
    con = sqlite3.connect(db)
    try:
        # The new table starts empty: nothing is backfilled from the old rows.
        assert con.execute("SELECT COUNT(*) FROM vitamin_intakes").fetchone()[0] == 0
        assert con.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0007_vitamin_intakes",
        )
        indexes = {row[1] for row in con.execute("PRAGMA index_list('vitamin_intakes')")}
        assert "ix_vitamin_intakes_user_id" in indexes
    finally:
        con.close()


def test_granular_unique_constraint_is_enforced_by_the_database(tmp_path: Path) -> None:
    db = tmp_path / "granular.db"
    _alembic(db, "upgrade", "head")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")

    def insert(user_id: int, day: str, code: str) -> None:
        con.execute(
            "INSERT INTO vitamin_intakes (user_id, vitamin_date, item_code, taken_at, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, day, code, f"{day} 19:00:00", f"{day} 19:00:00"),
        )

    try:
        con.executescript(
            "INSERT INTO users (telegram_user_id, display_name, is_active, timezone, "
            "checkin_time, reminder_time, morning_time, food_morning_time, "
            "food_evening_time, weight_time, vitamin_reminder_time, "
            "vitamin_reminder_enabled, created_at, updated_at) "
            "VALUES (111, 'A', 1, 'Europe/Moscow', '21:30', '23:00', '08:30', "
            "'08:45', '22:30', '09:00', '22:00', 1, "
            "'2026-10-14 08:00:00', '2026-10-14 08:00:00'), "
            "(222, 'B', 1, 'Europe/Moscow', '21:30', '23:00', '08:30', "
            "'08:45', '22:30', '09:00', '22:00', 1, "
            "'2026-10-14 08:00:00', '2026-10-14 08:00:00')"
        )
        insert(1, "2026-10-14", "vitamin_d")
        con.commit()

        # The same item twice for the same user and day is the database's business.
        with pytest.raises(sqlite3.IntegrityError):
            insert(1, "2026-10-14", "vitamin_d")
        # Anything else is a different acknowledgement and is allowed.
        insert(1, "2026-10-14", "omega_3")  # another item, same day
        insert(1, "2026-10-13", "vitamin_d")  # same item, another day
        insert(2, "2026-10-14", "vitamin_d")  # same day and item, another user
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM vitamin_intakes").fetchone()[0] == 4
    finally:
        con.close()


def test_vitamin_intakes_downgrade_and_reupgrade(tmp_path: Path) -> None:
    db = tmp_path / "v132.db"
    _build_v132_db(db)
    _alembic(db, "upgrade", "head")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO vitamin_intakes (user_id, vitamin_date, item_code, taken_at, "
            "created_at) VALUES (1, '2026-10-14', 'vitamin_d', "
            "'2026-10-14 19:10:00', '2026-10-14 19:10:00')"
        )
        con.commit()
    finally:
        con.close()

    # Rolling back removes exactly the new table.
    _alembic(db, "downgrade", "0006_vitamin_log")
    assert "vitamin_intakes" not in _tables(db)
    assert "vitamin_logs" in _tables(db)
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS
    assert len(_vitamin_logs(db)) == 2

    _alembic(db, "upgrade", "head")
    assert "vitamin_intakes" in _tables(db)
    assert _v13_snapshot(db)["counts"] == _V13_COUNTS
    assert len(_vitamin_logs(db)) == 2
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM vitamin_intakes").fetchone()[0] == 0
    finally:
        con.close()
