"""v1.1.1 boundary tests: the Reflection Day rolls over at 05:00 user-local.

Covers spec sections 11-17: pure ``reflection_day`` boundaries (explicit,
timezone-aware UTC instants — never the wall clock), the frozen-date FSM
guarantee across the 05:00 crossing, /today, evening↔morning pairing, weekly,
stats/export range ends and the scheduler's "already done" checks.

Handler-level tests patch only the *entry point* ``reflection_day`` of the
handler module under test, pinning it to an explicit UTC instant while the
real rollover arithmetic still runs.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime

import pytest

from app.bot import texts
from app.bot.handlers import daily, export, morning, stats, weekly
from app.bot.states import MorningStates
from app.database.models import DailyEntry, MorningIntent, WeeklyReflection
from app.database.session import session_scope
from app.scheduler import scheduler as scheduler_module
from app.scheduler.scheduler import ReflectionScheduler
from app.services import checkin_service, time_service, weekly_service
from tests.test_evening_outcomes import logged_user_message
from tests.test_handlers import (  # noqa: F401
    BOT_TG_ID,
    FakeChat,
    FakeMessage,
    FakeState,
    FakeUser,
    app_runtime,
    bot_message,
    callback,
    user_message,
)

# 2026-09-20 is a Sunday, 2026-09-21 a Monday. MSK = UTC+3, Berlin (September) = UTC+2.
PREV = date(2026, 9, 20)  # reflection day before the boundary
CUR = date(2026, 9, 21)  # calendar day the boundary falls inside


def _utc(hhmmss: tuple[int, int, int], day: date = CUR) -> datetime:
    return datetime(day.year, day.month, day.day, *hhmmss, tzinfo=UTC)


def _freeze(monkeypatch: pytest.MonkeyPatch, module, moment: datetime) -> None:
    """Pin ``module.reflection_day`` to ``moment`` without faking the maths."""
    monkeypatch.setattr(
        module,
        "reflection_day",
        lambda tz_name, now=None: time_service.reflection_day(tz_name, moment),
    )


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))


# --------------------------------------------------------------------------
# 11. Pure reflection_day boundaries
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (_utc((1, 59, 59)), PREV),  # 04:59:59 MSK -> previous day
        (_utc((2, 0, 0)), CUR),  # 05:00:00 MSK -> current day
        (_utc((21, 0, 0), PREV), PREV),  # 00:00 MSK -> previous day
        (_utc((23, 0, 0), PREV), PREV),  # 02:00 MSK -> previous day
        (_utc((20, 59, 0)), CUR),  # 23:59 MSK -> current day
    ],
)
def test_reflection_day_moscow_boundaries(moment: datetime, expected: date) -> None:
    assert time_service.reflection_day("Europe/Moscow", moment) == expected


def test_reflection_day_boundary_is_user_local_not_moscow() -> None:
    # Same UTC instant: in Moscow it is already 05:30 (new day), in Berlin
    # only 04:30 (still the previous day) — the rollover is user-local.
    moment = _utc((2, 30, 0))
    assert time_service.reflection_day("Europe/Berlin", moment) == PREV
    assert time_service.reflection_day("Europe/Moscow", moment) == CUR


def test_reflection_day_berlin_boundaries() -> None:
    assert time_service.reflection_day("Europe/Berlin", _utc((2, 59, 59))) == PREV
    assert time_service.reflection_day("Europe/Berlin", _utc((3, 0, 0))) == CUR


def test_user_today_stays_a_plain_calendar_date() -> None:
    moment = _utc((23, 30, 0), PREV)  # 02:30 MSK on the 21st
    assert time_service.user_today("Europe/Moscow", moment) == CUR
    assert time_service.reflection_day("Europe/Moscow", moment) == PREV


def test_reflection_day_treats_naive_datetime_as_utc() -> None:
    naive = datetime(2026, 9, 21, 1, 0)  # noqa: DTZ001 - deliberately naive, must be read as UTC
    assert time_service.reflection_day("Europe/Moscow", naive) == PREV


# --------------------------------------------------------------------------
# 12. Critical FSM freeze: crossing 05:00 mid-flow
# --------------------------------------------------------------------------
async def test_evening_flow_crossing_0500_saves_to_frozen_date(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    _freeze(monkeypatch, daily, _utc((1, 58, 0)))  # 04:58 MSK start
    state = FakeState()
    await daily.cmd_checkin(user_message(111, "/checkin"), state)
    assert state.data == {"target_date": PREV.isoformat()}

    await daily.step_day(callback("ci:day:4", message=bot_message()), state)
    await daily.step_mood(callback("ci:mood:3", message=bot_message()), state)
    await daily.step_energy(callback("ci:energy:2", message=bot_message()), state)

    _freeze(monkeypatch, daily, _utc((2, 1, 0)))  # clock crosses 05:00 MSK
    await daily.skip_reflection(
        callback("ci:ref:no", user_id=111, message=bot_message()), state
    )

    with session_scope(session_factory) as s:
        entries = s.query(DailyEntry).filter_by(user_id=user.id).all()
        assert len(entries) == 1  # no accidental row for the new calendar day
        assert entries[0].entry_date == PREV


async def test_morning_flow_crossing_0500_writes_one_frozen_row(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    _freeze(monkeypatch, morning, _utc((1, 59, 0)))  # 04:59 MSK start
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    assert state.data == {"target_date": PREV.isoformat()}

    await morning.submit_main(user_message(111, "главное"), state)
    _freeze(monkeypatch, morning, _utc((2, 1, 0)))  # 05:01 MSK for step 2
    await morning.submit_secondary(user_message(111, "второе"), state)

    with session_scope(session_factory) as s:
        rows = s.query(MorningIntent).filter_by(user_id=user.id).all()
        assert len(rows) == 1  # both steps on the SAME frozen row
        assert rows[0].intention_date == PREV
        assert (rows[0].main_intention, rows[0].secondary_intention) == ("главное", "второе")


# --------------------------------------------------------------------------
# 5. Existing-entry guard uses the same logical date
# --------------------------------------------------------------------------
async def test_checkin_guard_checks_reflection_day_not_calendar_day(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=PREV, day_score=4, mood_score=4, energy_score=4
        )
    )
    session.commit()
    _freeze(monkeypatch, daily, _utc((1, 59, 0)))  # 04:59 MSK on the 21st

    state = FakeState({"day_score": 9})
    msg = user_message(111, "/checkin")
    await daily.cmd_checkin(msg, state)
    assert msg.answers[-1] == texts.ALREADY_FILLED
    assert state.data == {}  # guard branch never starts (and never freezes) a flow


# --------------------------------------------------------------------------
# 13. /today boundary
# --------------------------------------------------------------------------
async def _seed_two_days(session, user) -> None:
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=PREV, day_score=4, mood_score=3,
            energy_score=2, reflection_text="запись 20",
        )
    )
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=CUR, day_score=5, mood_score=5,
            energy_score=5, reflection_text="запись 21",
        )
    )
    session.commit()


async def test_today_at_0459_reads_previous_day(app_runtime, session, user, monkeypatch) -> None:  # noqa: F811
    await _seed_two_days(session, user)
    _freeze(monkeypatch, daily, _utc((1, 59, 0)))  # 04:59 MSK
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    rendered = msg.answers[-1]
    assert texts.ru_date(PREV) in rendered
    assert "запись 20" in rendered  # the DB row for the reflection day
    assert "запись 21" not in rendered


async def test_today_at_0500_reads_current_day(app_runtime, session, user, monkeypatch) -> None:  # noqa: F811
    await _seed_two_days(session, user)
    _freeze(monkeypatch, daily, _utc((2, 0, 0)))  # 05:00 MSK
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    rendered = msg.answers[-1]
    assert texts.ru_date(CUR) in rendered
    assert "запись 21" in rendered
    assert "запись 20" not in rendered


# --------------------------------------------------------------------------
# 14. Evening header pairs with the morning intent of the SAME reflection day
# --------------------------------------------------------------------------
async def test_evening_header_shows_sep20_intent_at_0200_on_sep21(
    app_runtime, session, user, monkeypatch  # noqa: F811
) -> None:
    session.add(
        MorningIntent(user_id=user.id, intention_date=PREV, main_intention="вчерашний фокус")
    )
    session.add(
        MorningIntent(user_id=user.id, intention_date=CUR, main_intention="декой 21")
    )
    session.commit()
    _freeze(monkeypatch, daily, _utc((23, 0, 0), PREV))  # 02:00 MSK on the 21st

    msg = user_message(111, "/checkin")
    await daily.cmd_checkin(msg, FakeState())
    header = msg.answers[-1]
    assert "вчерашний фокус" in header
    assert "декой 21" not in header  # it must not look up the new calendar day


# --------------------------------------------------------------------------
# 15. Weekly offer boundaries
# --------------------------------------------------------------------------
def test_weekly_offer_boundaries(session, user, monkeypatch) -> None:
    # Monday 02:00 MSK -> reflection day Sunday: the Sunday offer still applies.
    _freeze(monkeypatch, weekly_service, _utc((23, 0, 0), PREV))
    assert weekly_service.should_offer_weekly(session, user) is True

    # Monday 05:00 MSK -> reflection day Monday: it no longer applies.
    _freeze(monkeypatch, weekly_service, _utc((2, 0, 0), CUR))
    assert weekly_service.should_offer_weekly(session, user) is False

    # Sunday 01:30 MSK -> reflection day Saturday: nothing to offer yet.
    _freeze(monkeypatch, weekly_service, _utc((22, 30, 0), date(2026, 9, 19)))
    assert weekly_service.should_offer_weekly(session, user) is False


# --------------------------------------------------------------------------
# 16. Stats / export period ends on the reflection day
# --------------------------------------------------------------------------
async def test_stats_period_ends_on_previous_reflection_day_before_0500(
    app_runtime, session, user, monkeypatch  # noqa: F811
) -> None:
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=PREV, day_score=4, mood_score=3, energy_score=2
        )
    )
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=CUR, day_score=5, mood_score=5, energy_score=5
        )
    )
    session.commit()
    _freeze(monkeypatch, stats, _utc((1, 0, 0)))  # 04:00 MSK on the 21st

    msg = user_message(111, "/stats")
    await stats.cmd_stats(msg)
    # Only the Sep 20 entry counts; the Sep 21 one is beyond the period end.
    assert "Заполнено: <b>1 из 30</b>" in msg.answers[-1]


async def test_export_before_0500_excludes_new_calendar_day(
    app_runtime, session, user, monkeypatch  # noqa: F811
) -> None:
    await _seed_two_days(session, user)
    _freeze(monkeypatch, export, _utc((1, 0, 0)))  # 04:00 MSK on the 21st

    target = bot_message()
    captured: list[tuple[str, bytes]] = []

    async def fake_document(document=None, caption=None, **kwargs) -> None:
        captured.append((document.filename, document.data))

    target.answer_document = fake_document  # type: ignore[attr-defined]
    await export.cb_export(callback("ex:all", user_id=111, message=target))

    assert "Готовлю файл…" in target.answers
    assert len(captured) == 1
    filename, data = captured[0]
    assert filename == "reflection_export_2026-09-20.csv"  # follows the reflection day
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    # The new calendar day (Sep 21) must not have entered the range yet.
    assert [r["date"] for r in rows] == ["2026-09-20"]


# --------------------------------------------------------------------------
# Review follow-up P0: the scheduled evening prompt carries the day keyboard
# directly — the FIRST score tap must freeze target_date, not _finalize.
# --------------------------------------------------------------------------
async def test_direct_score_click_freezes_reflection_day(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    # No /checkin and no _handle_checkin_request beforehand: FSM starts empty.
    _freeze(monkeypatch, daily, _utc((1, 58, 0)))  # 04:58 MSK
    state = FakeState()
    await daily.step_day(callback("ci:day:4", user_id=111, message=bot_message()), state)
    assert state.data["target_date"] == PREV.isoformat()

    await daily.step_mood(callback("ci:mood:3", user_id=111, message=bot_message()), state)
    await daily.step_energy(callback("ci:energy:2", user_id=111, message=bot_message()), state)

    _freeze(monkeypatch, daily, _utc((2, 1, 0)))  # 05:01 MSK at completion
    await daily.skip_reflection(
        callback("ci:ref:no", user_id=111, message=bot_message()), state
    )

    with session_scope(session_factory) as s:
        entries = s.query(DailyEntry).filter_by(user_id=user.id).all()
        assert len(entries) == 1
        assert entries[0].entry_date == PREV  # NOT the post-rollover calendar day


async def test_step_day_keeps_already_frozen_target_date(
    app_runtime, session, user, monkeypatch  # noqa: F811
) -> None:
    # Explicit-start flows already froze the date: step_day must not touch it.
    _freeze(monkeypatch, daily, _utc((2, 30, 0)))  # 05:30 MSK — a *different* day
    state = FakeState({"target_date": PREV.isoformat()})
    await daily.step_day(callback("ci:day:4", user_id=111, message=bot_message()), state)
    assert state.data["target_date"] == PREV.isoformat()
    assert state.data["day_score"] == 4


# --------------------------------------------------------------------------
# Review follow-up P1: weekly offer follows the frozen evening day
# --------------------------------------------------------------------------
class _MarkupRecorder(FakeMessage):
    """Bot-authored message that also records the reply markup of answers."""

    def __init__(self) -> None:
        super().__init__(FakeChat(111), FakeUser(BOT_TG_ID))
        self.markups: list[object] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:
        self.answers.append(text)
        self.markups.append(reply_markup)


async def test_weekly_offer_follows_frozen_entry_date(
    app_runtime, session, user, monkeypatch  # noqa: F811
) -> None:
    # Live clock is already Monday 05:01 (reflection day Monday): a time-based
    # offer check would say "no". The entry is frozen to Sunday -> offer yes,
    # and the offer button carries the Sunday's week start.
    _freeze(monkeypatch, weekly_service, _utc((2, 1, 0)))
    state = FakeState(
        {
            "day_score": 4,
            "mood_score": 3,
            "energy_score": 2,
            "target_date": PREV.isoformat(),  # Sunday 2026-09-20
        }
    )
    recorder = _MarkupRecorder()
    await daily.skip_reflection(callback("ci:ref:no", user_id=111, message=recorder), state)

    assert texts.WEEKLY_OFFER in recorder.answers
    keyboard = recorder.markups[-1]
    assert keyboard is not None
    assert keyboard.inline_keyboard[0][0].callback_data == "wk:start:2026-09-14"


# --------------------------------------------------------------------------
# Review follow-up P1: the weekly flow itself freezes its target week
# --------------------------------------------------------------------------
async def test_weekly_flow_from_dated_offer_saves_frozen_week(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    # Rollover already happened: a time-based week computation would now
    # produce the NEW week (2026-09-21). The offer's baked date must win.
    _freeze(monkeypatch, weekly_service, _utc((2, 1, 0)))
    state = FakeState()
    await weekly.start_weekly(
        callback("wk:start:2026-09-14", user_id=111, message=bot_message()), state
    )
    assert state.data["week_start"] == "2026-09-14"

    await weekly.ans_q1(user_message(111, "релиз"), state)
    await weekly.ans_q2(user_message(111, "созвон"), state)
    await weekly.ans_q3(user_message(111, "чтение"), state)

    with session_scope(session_factory) as s:
        rows = s.query(WeeklyReflection).filter_by(user_id=user.id).all()
        assert len(rows) == 1
        assert rows[0].week_start_date == date(2026, 9, 14)  # not the 09-21 week


# --------------------------------------------------------------------------
# 17. Scheduler "already done today" checks use the logical date
# --------------------------------------------------------------------------
async def test_has_entry_today_true_at_0200_for_previous_day_entry(
    session, user, monkeypatch
) -> None:
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=PREV, day_score=5, mood_score=4, energy_score=3
        )
    )
    session.commit()
    _freeze(monkeypatch, checkin_service, _utc((23, 0, 0), PREV))  # 02:00 MSK on the 21st
    assert checkin_service.has_entry_today(session, user) is True


async def test_daily_and_reminder_jobs_skip_at_0200_when_previous_day_filled(
    session, user, session_factory, monkeypatch
) -> None:
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=PREV, day_score=4, mood_score=4, energy_score=4
        )
    )
    session.commit()
    _freeze(monkeypatch, checkin_service, _utc((23, 0, 0), PREV))  # 02:00 MSK on the 21st
    bot = FakeBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._daily_job(user.id)
    await sched._reminder_job(user.id)
    assert bot.sent == []


# --------------------------------------------------------------------------
# v1.2: the evening closure obeys the same 05:00 contract
# --------------------------------------------------------------------------
def _seed_plan(session, user, day: date = PREV) -> None:
    session.add(
        MorningIntent(
            user_id=user.id, intention_date=day, main_intention="вечерний фокус",
            secondary_intention="второе",
        )
    )
    session.commit()


async def test_closure_across_0500_lands_entirely_on_the_frozen_day(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    # 04:58 MSK: the evening starts on the PREV reflection day and every button
    # it renders is baked with that date.
    _seed_plan(session, user)
    _freeze(monkeypatch, daily, _utc((1, 58, 0)))
    state = FakeState()
    msg = logged_user_message(111, "/checkin")
    await daily.cmd_checkin(msg, state)
    assert state.data == {"target_date": PREV.isoformat()}
    assert f"ci:out:main:done:{PREV.isoformat()}" in _buttons(msg)

    # The user takes two minutes: the clock crosses 05:00 mid-flow.
    _freeze(monkeypatch, daily, _utc((2, 1, 0)))
    await daily.step_outcome(
        callback(f"ci:out:main:partial:{PREV.isoformat()}", user_id=111, message=bot_message()),
        state,
    )
    await daily.step_outcome(
        callback(f"ci:out:secondary:done:{PREV.isoformat()}", user_id=111, message=bot_message()),
        state,
    )
    await daily.step_day(callback(f"ci:day:4:{PREV.isoformat()}", user_id=111), state)
    await daily.step_mood(callback("ci:mood:3", user_id=111), state)
    await daily.step_energy(callback("ci:energy:2", user_id=111), state)
    await daily.skip_reflection(callback("ci:ref:no", user_id=111, message=bot_message()), state)

    with session_scope(session_factory) as s:
        intents = s.query(MorningIntent).filter_by(user_id=user.id).all()
        assert len(intents) == 1  # no row invented for the new logical day
        assert intents[0].intention_date == PREV
        assert (intents[0].main_outcome, intents[0].secondary_outcome) == ("partial", "done")
        entries = s.query(DailyEntry).filter_by(user_id=user.id).all()
        assert len(entries) == 1
        assert entries[0].entry_date == PREV  # NOT the post-rollover day


async def test_baked_outcome_button_ignores_the_rolled_over_clock(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    # A prompt from before 05:00 is tapped after it, with no FSM at all
    # (scheduled message + restart). The button's own date must win.
    _seed_plan(session, user)
    _freeze(monkeypatch, daily, _utc((2, 5, 0)))  # 05:05 MSK: logical day is CUR
    await daily.step_outcome(
        callback(f"ci:out:main:done:{PREV.isoformat()}", user_id=111, message=bot_message()),
        FakeState(),
    )
    with session_scope(session_factory) as s:
        rows = s.query(MorningIntent).filter_by(user_id=user.id).all()
        assert [(r.intention_date, r.main_outcome) for r in rows] == [(PREV, "done")]


async def test_outcome_tap_for_todays_plan_uses_todays_row(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    # The mirror case: PREV already has a closed outcome. A tap baked with CUR
    # must touch only the CUR plan, never drift onto the neighbouring day.
    _seed_plan(session, user, day=PREV)
    _seed_plan(session, user, day=CUR)
    with session_scope(session_factory) as s:
        s.query(MorningIntent).filter_by(intention_date=PREV).one().main_outcome = "not_done"
        s.commit()
    _freeze(monkeypatch, daily, _utc((2, 0, 0)))  # 05:00 MSK
    state = FakeState()
    await daily.step_outcome(
        callback(f"ci:out:main:done:{CUR.isoformat()}", user_id=111, message=bot_message()), state
    )
    assert state.data == {"target_date": CUR.isoformat()}
    with session_scope(session_factory) as s:
        by_date = {r.intention_date: r.main_outcome for r in s.query(MorningIntent)}
        assert by_date == {PREV: "not_done", CUR: "done"}


async def test_morning_view_of_the_new_day_stays_editable_after_rollover(
    app_runtime, session, user, session_factory, monkeypatch  # noqa: F811
) -> None:
    # The lock is per Reflection Day: closing PREV's evening must not freeze
    # the brand-new day that starts at 05:00.
    _seed_plan(session, user)
    with session_scope(session_factory) as s:
        s.query(MorningIntent).filter_by(intention_date=PREV).one().main_outcome = "done"
        s.commit()

    _freeze(monkeypatch, morning, _utc((2, 1, 0)))  # 05:01 MSK -> CUR
    state = FakeState()
    msg = user_message(111, "/morning")
    await morning.cmd_morning(msg, state)
    assert state.state == MorningStates.waiting_main
    assert texts.Q_MAIN_INTENTION in msg.answers
    assert texts.MORNING_LOCKED_OUTCOME not in msg.answers

    # The previous day, viewed at 04:59, is read-only.
    _freeze(monkeypatch, morning, _utc((1, 59, 0)))
    locked = FakeState()
    msg2 = user_message(111, "/morning")
    await morning.cmd_morning(msg2, locked)
    assert texts.MORNING_LOCKED_OUTCOME in msg2.answers[-1]
    assert locked.state is None and locked.data == {}


async def test_scheduled_evening_prompt_at_0200_closes_yesterdays_plan(
    session, user, session_factory, monkeypatch
) -> None:
    # 02:00 MSK on the 21st is still the 20th for this user: the prompt must
    # offer yesterday's closure, and its buttons must carry yesterday's date.
    _seed_plan(session, user)
    _freeze(monkeypatch, scheduler_module, _utc((23, 0, 0), PREV))
    _freeze(monkeypatch, checkin_service, _utc((23, 0, 0), PREV))
    bot = _MarkupBot()
    sched = ReflectionScheduler(session_factory, bot)  # type: ignore[arg-type]
    await sched._daily_job(user.id)
    assert len(bot.sent) == 1
    assert "вечерний фокус" in bot.sent[0][1]
    assert f"ci:out:main:done:{PREV.isoformat()}" in bot.buttons
    # The reminder still fires while the day entry is missing, even though the
    # evening has already been partly answered (spec 21).
    await sched._reminder_job(user.id)
    assert len(bot.sent) == 2


def _buttons(message) -> list[str]:
    """Callback data of the keyboard the fake message was last answered with."""
    markup = getattr(message, "markups", [None])[-1]
    if markup is None:
        return []
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


class _MarkupBot:
    """Fake bot that keeps the keyboard of every scheduled message."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.buttons: list[str] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))
        self.buttons += _buttons_of(kwargs.get("reply_markup"))


def _buttons_of(markup) -> list[str]:
    if markup is None:
        return []
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]
