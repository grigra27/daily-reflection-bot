"""v1.2 morning immutability (spec 12-14): the plan is frozen once the evening starts.

The rule exists because an outcome the user already answered has the morning
text quoted inside it. Letting a stale callback rewrite that text would leave
the record saying something the user never planned. Both entry points are
covered here: every Telegram path (``/morning``, the reply-menu button,
``mrn:start``, ``mrn:edit``) and, separately, the service itself — because the
service is what a replayed or hand-crafted request ultimately reaches.
"""

from __future__ import annotations

import pytest

from app.bot import keyboards, texts
from app.bot.handlers import daily, morning
from app.bot.states import MorningStates
from app.database.models import DailyEntry, MorningIntent
from app.database.session import session_scope
from app.services import morning_service
from app.services.time_service import reflection_day
from tests.test_evening_outcomes import (  # noqa: F401
    ME,
    TZ,
    app_runtime,
    button_data,
    logged_bot_message,
    logged_user_message,
    plan,
    tap,
)
from tests.test_handlers import (
    FakeState,
    callback,
    user_message,
)

TODAY = reflection_day(TZ)


def _lock_view(tg_id: int = ME):
    """Re-open the morning view the way a user would: repeat /morning."""
    return logged_user_message(tg_id, "/morning")


async def show_morning(state: FakeState | None = None, tg_id: int = ME):
    view = _lock_view(tg_id)
    await morning.cmd_morning(view, state if state is not None else FakeState())
    return view


async def record_main_outcome() -> None:
    """Answer step A through the evening handlers, which locks the day."""
    await tap(f"ci:out:main:done:{TODAY.isoformat()}", FakeState())


async def _checkin(tg_id: int = ME):
    state = FakeState()
    msg = logged_user_message(tg_id, "/checkin")
    await daily.cmd_checkin(msg, state)
    return msg, state


# --------------------------------------------------------------------------
# Before the evening: the morning record stays editable
# --------------------------------------------------------------------------
async def test_morning_stays_editable_before_any_outcome(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan(main="главное", secondary="ещё")

    view = await show_morning()
    assert texts.Q_SECONDARY_INTENTION not in view.answers  # not an edit prompt
    assert button_data(view.markups[-1]) == ["mrn:edit"]
    assert texts.MORNING_LOCKED_OUTCOME not in view.answers[-1]

    # The edit button really does start the FSM.
    target = logged_bot_message()
    state = FakeState()
    await morning.cb_start_morning(callback("mrn:edit", user_id=ME, message=target), state)
    assert state.state == MorningStates.waiting_main
    assert target.answers[-1] == texts.Q_MAIN_INTENTION


async def test_editing_both_texts_still_works_before_the_evening(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan(main="старое", secondary="старое второе")
    state = FakeState()
    target = logged_bot_message()
    await morning.cb_start_morning(callback("mrn:edit", user_id=ME, message=target), state)
    await morning.submit_main(user_message(ME, "новое"), state)
    await morning.submit_secondary(user_message(ME, "новое второе"), state)
    with session_scope(session_factory) as s:
        row = s.query(MorningIntent).one()
        assert (row.main_intention, row.secondary_intention) == ("новое", "новое второе")
        assert (row.main_outcome, row.secondary_outcome) == (None, None)


# --------------------------------------------------------------------------
# After step A: the record is read-only everywhere
# --------------------------------------------------------------------------
async def test_morning_view_is_read_once_only_after_the_main_outcome(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan(main="главное", secondary="ещё")
    await record_main_outcome()

    state = FakeState()
    view = await show_morning(state)
    assert texts.MORNING_LOCKED_OUTCOME in view.answers[-1]
    assert "🎯 Главное: главное" in view.answers[-1]  # the record is still shown
    assert view.markups[-1] is None  # no ✏️ button at all
    # A locked view never enters the edit FSM.
    assert state.state is None
    assert state.data == {}


async def test_reply_menu_morning_button_is_read_only_too(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan()
    await record_main_outcome()
    state = FakeState()
    msg = logged_user_message(ME, keyboards.reply.BTN_MORNING)
    await morning.menu_morning(msg, state)
    assert texts.MORNING_LOCKED_OUTCOME in msg.answers[-1]
    assert state.state is None and state.data == {}


async def test_stale_edit_callback_cannot_start_an_overwrite(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The user tapped ✏️ Изменить before the evening started; the button is
    # still on screen afterwards and gets tapped again.
    await plan()
    await record_main_outcome()

    state = FakeState()
    target = logged_bot_message()
    await morning.cb_start_morning(callback("mrn:edit", user_id=ME, message=target), state)

    assert texts.MORNING_LOCKED_OUTCOME in target.answers
    assert texts.Q_MAIN_INTENTION not in target.answers  # the edit never opened
    assert state.state is None and state.data == {}
    with session_scope(session_factory) as s:
        assert s.query(MorningIntent).one().main_intention == "backup Flow"


async def test_stale_start_callback_is_also_refused(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan()
    await record_main_outcome()
    state = FakeState()
    target = logged_bot_message()
    await morning.cb_start_morning(callback("mrn:start", user_id=ME, message=target), state)
    assert texts.MORNING_LOCKED_OUTCOME in target.answers
    assert state.state is None


async def test_an_open_morning_fsm_is_refused_when_the_evening_lands_midway(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The edit FSM was entered legitimately; the user then answered the evening
    # prompt on another device, so the pending text write must be rejected.
    await plan(main="главное", secondary="ещё")
    state = FakeState()
    target = logged_bot_message()
    await morning.cb_start_morning(callback("mrn:edit", user_id=ME, message=target), state)
    assert state.state == MorningStates.waiting_main

    await record_main_outcome()  # the closure happens while the edit is open

    pending = logged_user_message(ME, "переписано")
    await morning.submit_main(pending, state)
    assert texts.MORNING_LOCKED_OUTCOME in pending.answers[-1]
    # The refused flow is closed, not left dangling in a half-edit state.
    assert state.state is None and state.data == {}
    with session_scope(session_factory) as s:
        row = s.query(MorningIntent).one()
        assert row.main_intention == "главное"
        assert (row.main_outcome, row.secondary_outcome) == ("done", None)


async def test_the_second_morning_step_is_refused_the_same_way(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Step 1 committed before the lock appeared (both checks ran then); the
    # closure lands before step 2, so that write must be refused too.
    await plan(main="главное", secondary="ещё")
    state = FakeState()
    await state.update_data(target_date=TODAY.isoformat())
    await state.set_state(MorningStates.waiting_secondary)
    await record_main_outcome()

    pending = logged_user_message(ME, "новое второе")
    await morning.submit_secondary(pending, state)
    assert texts.MORNING_LOCKED_OUTCOME in pending.answers[-1]
    assert state.state is None
    with session_scope(session_factory) as s:
        assert s.query(MorningIntent).one().secondary_intention == "ещё"


# --------------------------------------------------------------------------
# Historical data: a filled day without any outcome is locked as well
# --------------------------------------------------------------------------
async def test_historical_filled_day_is_locked_without_outcomes(
    app_runtime, session, user, session_factory  # noqa: F811
) -> None:
    # A pre-v1.2 row: intention + DailyEntry, both outcome columns NULL.
    session.add(
        MorningIntent(
            user_id=user.id, intention_date=TODAY, main_intention="из прошлого",
            secondary_intention="тоже",
        )
    )
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=TODAY, day_score=4, mood_score=4, energy_score=4
        )
    )
    session.commit()

    state = FakeState()
    view = await show_morning(state)
    assert texts.MORNING_LOCKED_FILLED in view.answers[-1]
    assert view.markups[-1] is None
    assert state.state is None

    target = logged_bot_message()
    await morning.cb_start_morning(callback("mrn:edit", user_id=ME, message=target), FakeState())
    assert texts.MORNING_LOCKED_FILLED in target.answers
    assert texts.Q_MAIN_INTENTION not in target.answers


async def test_the_evening_can_still_close_a_locked_day(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The lock covers the morning *texts*; the closure itself must keep going.
    await plan(main="главное", secondary="ещё")
    _, state = await _checkin()
    await tap(f"ci:out:main:done:{TODAY.isoformat()}", state)
    second, state2 = await _checkin()
    assert second.answers[-1] == texts.q_secondary_outcome("ещё")
    await tap(f"ci:out:secondary:partial:{TODAY.isoformat()}", state2)
    with session_scope(session_factory) as s:
        row = s.query(MorningIntent).one()
        assert (row.main_outcome, row.secondary_outcome) == ("done", "partial")
        assert (row.main_intention, row.secondary_intention) == ("главное", "ещё")


async def test_lock_message_is_neutral(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan()
    await record_main_outcome()
    view = await show_morning()
    for forbidden in ("ты забыл", "нельзя", "ошибка", "нечего"):
        assert forbidden not in view.answers[-1]


# --------------------------------------------------------------------------
# Review P1: a filled day cannot receive a morning plan after the fact
# --------------------------------------------------------------------------
async def test_a_filled_day_refuses_a_retroactive_morning_plan(
    app_runtime, session, user, session_factory  # noqa: F811
) -> None:
    # The evening was closed without any morning record (a pre-v1.2 shape, or
    # simply a day the user only rated). None of the four entry points may
    # invent a plan for it now.
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=TODAY, day_score=3, mood_score=3, energy_score=3
        )
    )
    session.commit()

    state = FakeState()
    view = await show_morning(state)
    assert texts.MORNING_LOCKED_DAY_OVER in view.answers[-1]
    assert texts.Q_MAIN_INTENTION not in view.answers  # no prompt to answer
    assert state.state is None and state.data == {}

    menu = logged_user_message(ME, keyboards.reply.BTN_MORNING)
    await morning.menu_morning(menu, FakeState())
    assert texts.MORNING_LOCKED_DAY_OVER in menu.answers[-1]

    target = logged_bot_message()
    start_state = FakeState()
    await morning.cb_start_morning(callback("mrn:start", user_id=ME, message=target), start_state)
    assert texts.MORNING_LOCKED_DAY_OVER in target.answers
    assert start_state.state is None

    # And the service itself refuses, so no path can talk it into a row.
    with pytest.raises(morning_service.MorningLockedError) as exc:
        morning_service.save_main_intention(session, user, "план на вечер")
    assert exc.value.lock is morning_service.MorningLock.DAY_FILLED
    assert exc.value.has_record is False  # the message must not claim otherwise
    with session_scope(session_factory) as s:
        assert s.query(MorningIntent).count() == 0
        assert s.query(DailyEntry).count() == 1


async def test_the_retroactive_refusal_stays_neutral(
    app_runtime, session, user  # noqa: F811
) -> None:
    # No record exists, so saying one "is already fixed" would be false; the
    # wording states the rule instead, without blaming anyone.
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=TODAY, day_score=3, mood_score=3, energy_score=3
        )
    )
    session.commit()
    view = await show_morning()
    assert texts.MORNING_LOCKED_DAY_OVER in view.answers[-1]
    assert "Утренний фокус уже зафиксирован" not in view.answers[-1]
    for forbidden in ("ты забыл", "нельзя", "ошибка", "нечего"):
        assert forbidden not in view.answers[-1]


# --------------------------------------------------------------------------
# Spec 14: the service refuses, whatever the Telegram layer thought
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda s, u: morning_service.save_main_intention(s, u, "новое"),
        lambda s, u: morning_service.set_secondary(s, u, "новое"),
    ],
    ids=["main", "secondary"],
)
def test_service_level_protection_after_an_outcome(session, user, call) -> None:
    morning_service.save_main_intention(session, user, "A")
    morning_service.set_secondary(session, user, "S")
    morning_service.record_outcome(session, user, outcome_field="main", outcome="partial")
    with pytest.raises(morning_service.MorningLockedError):
        call(session, user)
    row = morning_service.get_intent(session, user)
    assert (row.main_intention, row.secondary_intention) == ("A", "S")  # type: ignore[union-attr]
