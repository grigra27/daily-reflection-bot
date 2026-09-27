"""v1.3 food reflection: service rules, stateless handlers, scheduler jobs,
statistics, export and the migration.

Handlers are driven with the duck-typed fakes from ``tests.test_handlers``; the
scheduler jobs with the fake bot from ``tests.test_scheduler``.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.bot import texts
from app.bot.handlers import food
from app.bot.handlers import settings as settings_handlers
from app.bot.states import WeightStates
from app.database.models import DailyEntry, FoodDay, FoodRuleResult, User, WeightLog
from app.database.repositories import FoodDayRepository, UserRepository
from app.database.session import session_scope
from app.scheduler.scheduler import ReflectionScheduler
from app.services import export_service, food_service
from app.services.food_service import FoodDateError, FoodDayClosedError, FoodValidationError
from app.services.time_service import reflection_day, week_start_date
from tests.test_handlers import (
    FakeCallback,
    FakeState,
    app_runtime,  # noqa: F401  (pytest fixture, imported for reuse)
    bot_message,
    user_message,
)
from tests.test_migrations import _alembic, _columns, _tables
from tests.test_scheduler import FakeBot

TZ = "Europe/Moscow"


def _today() -> date:
    return reflection_day(TZ)


class AlertCallback(FakeCallback):
    """Keeps what ``cb.answer`` was called with, to assert on refusals."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.alerts: list[str] = []

    async def answer(self, text: str | None = None, **kwargs) -> None:
        self.answered += 1
        if text:
            self.alerts.append(text)


def cb(data: str, user_id: int = 111) -> AlertCallback:
    return AlertCallback(data, user_id, bot_message(user_id))


def _user(session: Session, tg_id: int = 111) -> User:
    u = User(telegram_user_id=tg_id, timezone=TZ)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _food_day(session_factory, tg_id: int, day: date) -> FoodDay | None:
    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(tg_id)
        assert user is not None
        found = FoodDayRepository(s).get(user.id, day)
        if found is not None:
            s.expunge(found)
        return found


# --------------------------------------------------------------------------
# Service: rule set
# --------------------------------------------------------------------------
def test_default_rules_are_the_chosen_ten(session: Session) -> None:
    u = _user(session)
    assert food_service.active_rules(u) == list(food_service.DEFAULT_RULES)
    assert len(food_service.DEFAULT_RULES) == 10
    assert set(food_service.RULE_CODES) == set(texts.FOOD_RULE_LABEL)
    assert set(food_service.TRIGGER_CODES) == set(texts.FOOD_TRIGGER_LABEL)


def test_rules_toggle_keeps_catalog_order_and_refuses_empty(session: Session) -> None:
    u = _user(session)
    food_service.set_rule_enabled(session, u, "steps", True)
    assert food_service.active_rules(u)[-1] == "steps"
    for code in list(food_service.active_rules(u))[:-1]:
        food_service.set_rule_enabled(session, u, code, False)
    assert food_service.active_rules(u) == ["steps"]
    with pytest.raises(FoodValidationError):
        food_service.set_rule_enabled(session, u, "steps", False)
    with pytest.raises(FoodValidationError):
        food_service.set_rule_enabled(session, u, "bogus", True)


# --------------------------------------------------------------------------
# Service: day flow
# --------------------------------------------------------------------------
def test_toggle_submit_records_every_active_rule(session: Session) -> None:
    u = _user(session)
    today = _today()
    food_service.toggle_violation(session, u, today, "no_sweets")
    food_service.toggle_violation(session, u, today, "no_chips")
    food_service.toggle_violation(session, u, today, "no_chips")  # back to kept
    day = food_service.get_day(session, u, today)
    assert food_service.violations(day) == ["no_sweets"]
    assert not food_service.is_completed(day)

    day = food_service.submit_day(session, u, today)
    assert food_service.is_completed(day)
    assert food_service.violations(day) == ["no_sweets"]
    assert len(day.results) == 10
    assert set(food_service.kept_rules(day)) == set(food_service.DEFAULT_RULES) - {"no_sweets"}


def test_submitted_day_is_read_only_until_reopened(session: Session) -> None:
    u = _user(session)
    today = _today()
    food_service.submit_day(session, u, today)
    with pytest.raises(FoodDayClosedError):
        food_service.toggle_violation(session, u, today, "no_sweets")
    with pytest.raises(FoodDayClosedError):
        food_service.set_focus(session, u, today, "no_sweets")
    # Double "Готово" is idempotent.
    first = food_service.get_day(session, u, today).completed_at
    assert food_service.submit_day(session, u, today).completed_at == first

    food_service.reopen_day(session, u, today)
    day = food_service.toggle_violation(session, u, today, "no_sweets")
    assert food_service.violations(day) == ["no_sweets"]


def test_dates_outside_window_are_refused(session: Session) -> None:
    u = _user(session)
    today = _today()
    with pytest.raises(FoodDateError):
        food_service.toggle_violation(session, u, today + timedelta(days=1), "no_sweets")
    with pytest.raises(FoodDateError):
        food_service.submit_day(session, u, today - timedelta(days=8))
    food_service.submit_day(session, u, today - timedelta(days=7))  # edge is allowed


def test_triggers_only_after_a_broken_submitted_day(session: Session) -> None:
    u = _user(session)
    today = _today()
    with pytest.raises(FoodValidationError):
        food_service.toggle_trigger(session, u, today, "stress")
    food_service.submit_day(session, u, today)
    with pytest.raises(FoodValidationError):  # clean day: nothing to explain
        food_service.toggle_trigger(session, u, today, "stress")

    food_service.reopen_day(session, u, today)
    food_service.toggle_violation(session, u, today, "no_sweets")
    food_service.submit_day(session, u, today)
    food_service.toggle_trigger(session, u, today, "tired")
    day = food_service.toggle_trigger(session, u, today, "stress")
    assert food_service.triggers_of(day) == ["stress", "tired"]  # catalog order
    day = food_service.toggle_trigger(session, u, today, "tired")
    assert food_service.triggers_of(day) == ["stress"]


def test_clean_resubmit_drops_old_triggers(session: Session) -> None:
    u = _user(session)
    today = _today()
    food_service.toggle_violation(session, u, today, "no_sweets")
    food_service.submit_day(session, u, today)
    food_service.toggle_trigger(session, u, today, "stress")
    food_service.reopen_day(session, u, today)
    food_service.toggle_violation(session, u, today, "no_sweets")
    day = food_service.submit_day(session, u, today)
    assert food_service.triggers_of(day) == []


def test_focus_must_be_an_active_rule(session: Session) -> None:
    u = _user(session)
    today = _today()
    with pytest.raises(FoodValidationError):
        food_service.set_focus(session, u, today, "steps")  # not enabled by default
    day = food_service.set_focus(session, u, today, None)
    assert food_service.focus_answered(day) and day.focus_rule is None
    day = food_service.set_focus(session, u, today, "no_sweets")
    assert day.focus_rule == "no_sweets"
    assert food_service.ordered_rules(u, "no_sweets")[0] == "no_sweets"


@pytest.mark.parametrize(
    ("raw", "value"),
    [("92.4", 92.4), ("92,4", 92.4), (" 101 кг ", 101.0), ("88.45", 88.5)],
)
def test_parse_weight(raw: str, value: float) -> None:
    assert food_service.parse_weight(raw) == value


@pytest.mark.parametrize("raw", ["abc", "", "5", "999", "-80"])
def test_parse_weight_rejects(raw: str) -> None:
    with pytest.raises(FoodValidationError):
        food_service.parse_weight(raw)


def test_weight_is_one_value_per_week(session: Session) -> None:
    u = _user(session)
    food_service.save_weight(session, u, 93.0)
    food_service.save_weight(session, u, 92.6)
    logs = session.query(WeightLog).all()
    assert len(logs) == 1 and logs[0].weight_kg == 92.6
    assert logs[0].week_start_date == week_start_date(_today())


# --------------------------------------------------------------------------
# Statistics (pure)
# --------------------------------------------------------------------------
def _stat_day(d: date, broken: list[str], *, rules=("a", "b"), focus=None, triggers=None):
    day = FoodDay(food_date=d, focus_rule=focus, triggers=triggers)
    day.completed_at = food_service._now()
    day.results = [FoodRuleResult(rule_code=r, kept=r not in broken) for r in rules]
    return day


def test_streak_counts_clean_days_and_tolerates_open_today() -> None:
    today = date(2026, 9, 27)
    days = [
        _stat_day(today - timedelta(days=4), ["a"]),
        _stat_day(today - timedelta(days=3), []),
        _stat_day(today - timedelta(days=2), []),
        _stat_day(today - timedelta(days=1), []),
    ]
    assert food_service.compute_streak(days, today) == 3  # today still open
    days.append(_stat_day(today, []))
    assert food_service.compute_streak(days, today) == 4
    days[-1] = _stat_day(today, ["b"])
    assert food_service.compute_streak(days, today) == 0


def test_food_stats_rates_focus_triggers_and_comparison() -> None:
    today = date(2026, 9, 27)
    rules = ("no_sweets", "water")
    days = []
    entries = []
    for i in range(6):
        d = today - timedelta(days=i)
        broken = ["no_sweets"] if i % 2 else []
        days.append(
            _stat_day(
                d, broken, rules=rules, focus="no_sweets",
                triggers="stress" if broken else None,
            )
        )
        energy = 2 if broken else 4
        entries.append(
            DailyEntry(entry_date=d, day_score=3, mood_score=energy, energy_score=energy)
        )
    weights = [
        WeightLog(week_start_date=date(2026, 9, 7), logged_date=date(2026, 9, 13), weight_kg=95.0),
        WeightLog(week_start_date=date(2026, 9, 14), logged_date=date(2026, 9, 20), weight_kg=94.2),
        WeightLog(week_start_date=date(2026, 9, 21), logged_date=date(2026, 9, 27), weight_kg=93.9),
    ]
    stats = food_service.compute_food_stats(days, entries, weights, period_days=7, today=today)
    assert stats.completed_days == 6
    assert stats.clean_days == 3
    assert [r.code for r in stats.rule_rates] == ["no_sweets", "water"]  # worst first
    assert stats.rule_rates[0].percent == 50
    assert (stats.focus_kept, stats.focus_total) == (3, 6)
    assert stats.triggers == [("stress", 3)]
    assert (stats.energy_clean, stats.energy_broken) == (4.0, 2.0)
    assert stats.last_weight == 93.9
    assert stats.weight_change_week == -0.3
    assert stats.weight_change_total == -1.1
    rendered = texts.food_stats_message(stats)
    assert "50% — Без сладкого (3/6)" in rendered
    assert "−1.1 кг" in rendered


def test_comparison_hidden_with_too_few_days() -> None:
    today = date(2026, 9, 27)
    days = [_stat_day(today, []), _stat_day(today - timedelta(days=1), ["a"])]
    entries = [
        DailyEntry(entry_date=d.food_date, day_score=3, mood_score=3, energy_score=3)
        for d in days
    ]
    stats = food_service.compute_food_stats(days, entries, [], period_days=7, today=today)
    assert stats.energy_clean is None and stats.mood_broken is None


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
async def test_focus_button_saves_and_edits_message(app_runtime, session_factory) -> None:  # noqa: F811
    today = _today()
    c = cb(f"fd:f:no_sweets:{today.isoformat()}")
    await food.cb_focus(c)
    assert c.message.edits == [texts.food_focus_saved("no_sweets")]
    assert _food_day(session_factory, 111, today).focus_rule == "no_sweets"


async def test_checklist_toggle_submit_and_triggers(app_runtime, session_factory) -> None:  # noqa: F811
    day = _today().isoformat()
    await food.cb_toggle(cb(f"fd:t:no_chips:{day}"))
    submit = cb(f"fd:ok:{day}")
    await food.cb_submit(submit)
    assert texts.FOOD_TRIGGERS_QUESTION in submit.message.edits[-1]
    assert "❌ Без чипсов" in submit.message.edits[-1]

    await food.cb_trigger(cb(f"fd:g:boredom:{day}"))
    done = cb(f"fd:gd:{day}")
    await food.cb_triggers_done(done)
    assert "Повлияло: Скука" in done.message.edits[-1]
    stored = _food_day(session_factory, 111, _today())
    assert stored.triggers == "boredom"


async def test_clean_submit_shows_streak(app_runtime, session_factory) -> None:  # noqa: F811
    submit = cb(f"fd:ok:{_today().isoformat()}")
    await food.cb_submit(submit)
    assert "Соблюдено: <b>10 из 10</b>" in submit.message.edits[-1]
    assert "Дней подряд без нарушений: <b>1</b>" in submit.message.edits[-1]


async def test_stale_toggle_after_submit_is_refused(app_runtime, session_factory) -> None:  # noqa: F811
    day = _today().isoformat()
    await food.cb_submit(cb(f"fd:ok:{day}"))
    stale = cb(f"fd:t:no_sweets:{day}")
    await food.cb_toggle(stale)
    assert stale.alerts == [texts.FOOD_DAY_CLOSED]
    assert stale.message.edits == []
    assert food_service.violations(_food_day(session_factory, 111, _today())) == []


async def test_old_and_malformed_buttons_are_refused(app_runtime) -> None:  # noqa: F811
    old = cb(f"fd:t:no_sweets:{(_today() - timedelta(days=30)).isoformat()}")
    await food.cb_toggle(old)
    assert old.alerts == [texts.FOOD_DATE_REFUSED]
    broken = cb("fd:t:no_sweets:not-a-date")
    await food.cb_toggle(broken)
    assert broken.alerts == [texts.FOOD_DATE_REFUSED]


async def test_open_reopens_submitted_day(app_runtime, session_factory) -> None:  # noqa: F811
    day = _today().isoformat()
    await food.cb_submit(cb(f"fd:ok:{day}"))
    opener = cb(f"fd:open:{day}")
    await food.cb_open(opener)
    assert texts.FOOD_EVENING_HINT in opener.message.answers[-1]
    assert not food_service.is_completed(_food_day(session_factory, 111, _today()))


async def test_food_command_renders_status_and_stats(app_runtime) -> None:  # noqa: F811
    await food.cb_submit(cb(f"fd:ok:{_today().isoformat()}"))
    msg = user_message(111, "/food")
    await food.cmd_food(msg, FakeState())
    assert "Итог: соблюдено 10 из 10" in msg.answers[-1]
    assert "Питание — последние 30 дней" in msg.answers[-1]


async def test_weight_via_command_and_bare_number(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    await food.cmd_weight(user_message(111, "/weight"), state)
    assert state.state is WeightStates.waiting_weight
    bad = user_message(111, "много")
    await food.submit_weight(bad, state)
    assert bad.answers == [texts.WEIGHT_INVALID]
    assert state.state is WeightStates.waiting_weight

    ok = user_message(111, "92,4")
    await food.submit_weight(ok, state)
    assert state.state is None
    assert "92.4 кг" in ok.answers[-1]

    again = user_message(111, "92.0")
    await food.bare_weight(again, FakeState())
    assert "92.0 кг" in again.answers[-1]
    with session_scope(session_factory) as s:
        assert [w.weight_kg for w in s.query(WeightLog).all()] == [92.0]


async def test_rule_toggle_in_settings(app_runtime, session_factory) -> None:  # noqa: F811
    c = cb("se:fr:steps")
    await settings_handlers.toggle_food_rule(c)
    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(111)
        assert "steps" in food_service.active_rules(user)
    assert c.message.edits == [texts.FOOD_RULES_SETTINGS]


async def test_food_callbacks_use_tapper_identity(app_runtime, session_factory) -> None:  # noqa: F811
    # The message was sent by the bot; the tap by user 222 must write for 222.
    await food.cb_submit(cb(f"fd:ok:{_today().isoformat()}", user_id=222))
    assert food_service.is_completed(_food_day(session_factory, 222, _today()))


# --------------------------------------------------------------------------
# Scheduler jobs
# --------------------------------------------------------------------------
def _sched_user(session: Session) -> User:
    return _user(session)


async def test_food_morning_sends_focus_then_skips_when_answered(session, session_factory) -> None:
    u = _sched_user(session)
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._food_morning_job(u.id)
    assert [t for _, t in bot.sent] == [texts.FOOD_MORNING_PROMPT]
    assert any(b.startswith("fd:f:none:") for b in bot.buttons)

    food_service.set_focus(session, u, _today(), None)
    bot2 = FakeBot()
    await ReflectionScheduler(session_factory, bot2)._food_morning_job(u.id)  # type: ignore[arg-type]
    assert bot2.sent == []


async def test_food_morning_nudges_open_yesterday_only_when_in_use(session, session_factory) -> None:
    u = _sched_user(session)
    today = _today()
    food_service.set_focus(session, u, today, None)  # isolate the nudge
    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._food_morning_job(u.id)  # type: ignore[arg-type]
    assert bot.sent == []  # first day of the feature: no complaint about yesterday

    food_service.submit_day(session, u, today - timedelta(days=2))
    bot2 = FakeBot()
    await ReflectionScheduler(session_factory, bot2)._food_morning_job(u.id)  # type: ignore[arg-type]
    assert len(bot2.sent) == 1
    assert "Вчерашний итог по питанию не заполнен." in bot2.sent[0][1]
    yesterday = (today - timedelta(days=1)).isoformat()
    assert f"fd:ok:{yesterday}" in bot2.buttons


async def test_food_evening_sends_checklist_until_submitted(session, session_factory) -> None:
    u = _sched_user(session)
    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._food_evening_job(u.id)  # type: ignore[arg-type]
    assert len(bot.sent) == 1
    assert f"fd:ok:{_today().isoformat()}" in bot.buttons
    assert len([b for b in bot.buttons if b.startswith("fd:t:")]) == 10

    food_service.submit_day(session, u, _today())
    bot2 = FakeBot()
    await ReflectionScheduler(session_factory, bot2)._food_evening_job(u.id)  # type: ignore[arg-type]
    assert bot2.sent == []


async def test_weight_job_skips_when_logged(session, session_factory) -> None:
    u = _sched_user(session)
    bot = FakeBot()
    await ReflectionScheduler(session_factory, bot)._weight_job(u.id)  # type: ignore[arg-type]
    assert [t for _, t in bot.sent] == [texts.WEIGHT_PROMPT]
    food_service.save_weight(session, u, 90.0)
    bot2 = FakeBot()
    await ReflectionScheduler(session_factory, bot2)._weight_job(u.id)  # type: ignore[arg-type]
    assert bot2.sent == []


async def test_weight_job_runs_on_sundays_only(session, session_factory) -> None:
    u = _sched_user(session)
    sched = ReflectionScheduler(session_factory, FakeBot())  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        trigger = str(sched.apscheduler.get_job(f"weight:{u.id}").trigger)
        assert "day_of_week='sun'" in trigger and "hour='9'" in trigger
    finally:
        sched.apscheduler.shutdown(wait=False)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def test_export_carries_food_and_weight(session: Session) -> None:
    u = _user(session)
    today = _today()
    food_service.set_focus(session, u, today, "no_sweets")
    food_service.toggle_violation(session, u, today, "no_sweets")
    food_service.submit_day(session, u, today)
    food_service.toggle_trigger(session, u, today, "stress")
    food_service.save_weight(session, u, 91.5)

    _, data = export_service.export_user_csv(session, u, scope="30d", today=today)
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert len(rows) == 1
    row = rows[0]
    assert row["food_focus"] == "no_sweets"
    assert row["food_completed"] == "yes"
    assert row["food_broken_rules"] == "no_sweets"
    assert "water" in row["food_kept_rules"].split(";")
    assert row["food_triggers"] == "stress"
    assert row["weight_kg"] == "91.5"
    assert row["day_score"] == ""  # evening not filled: empty, not zero


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------
def test_0004_upgrade_keeps_data_and_backfills_times(tmp_path: Path) -> None:
    db = tmp_path / "v12.db"
    _alembic(db, "upgrade", "0003_intention_outcomes")
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            INSERT INTO users (telegram_user_id, display_name, is_active, timezone,
                               checkin_time, reminder_time, created_at, updated_at,
                               morning_time)
            VALUES (111, 'A', 1, 'Europe/Moscow', '21:30', '23:00',
                    '2026-09-20 08:00:00', '2026-09-20 08:00:00', '08:30');
            INSERT INTO daily_entries (user_id, entry_date, day_score, mood_score,
                                       energy_score, reflection_text,
                                       questionnaire_version, created_at, updated_at)
            VALUES (1, '2026-09-26', 4, 3, 2, 'ok', 1,
                    '2026-09-26 21:40:00', '2026-09-26 21:40:00');
            INSERT INTO morning_intents (user_id, intention_date, main_intention,
                                         secondary_intention, created_at, updated_at,
                                         main_outcome, secondary_outcome)
            VALUES (1, '2026-09-26', 'план', NULL, '2026-09-26 08:31:00',
                    '2026-09-26 21:31:00', 'done', NULL);
            """
        )
        con.commit()
    finally:
        con.close()

    _alembic(db, "upgrade", "head")
    assert {"food_days", "food_rule_results", "weight_logs"} <= _tables(db)
    assert {"food_morning_time", "food_evening_time", "weight_time", "food_rules"} <= _columns(
        db, "users"
    )
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT food_morning_time, food_evening_time, weight_time, food_rules FROM users"
        ).fetchone() == ("08:45", "22:30", "09:00", None)
        assert con.execute("SELECT day_score, reflection_text FROM daily_entries").fetchone() == (
            4,
            "ok",
        )
        assert con.execute("SELECT main_outcome FROM morning_intents").fetchone() == ("done",)
    finally:
        con.close()

    _alembic(db, "downgrade", "0003_intention_outcomes")
    assert "food_days" not in _tables(db)
    assert "food_rules" not in _columns(db, "users")
    _alembic(db, "upgrade", "head")
    assert "weight_logs" in _tables(db)
