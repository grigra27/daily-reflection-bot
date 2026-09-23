"""Unified /today snapshot + evening flow entry tests (v1.1, spec 27; v1.2 loop closure)."""

from __future__ import annotations

import pytest

from app.bot import texts
from app.bot.handlers import daily, morning
from app.bot.states import CheckinStates
from app.database.models import DailyEntry, User
from app.database.repositories import UserRepository
from app.database.session import session_scope
from app.services import morning_service
from app.services.time_service import reflection_day
from tests.test_handlers import (  # noqa: F401
    BOT_TG_ID,
    FakeState,
    app_runtime,
    bot_message,
    callback,
    user_message,
)


async def _write_morning(tg_id: int = 111, secondary: str | None = "зал") -> None:
    state = FakeState()
    await morning.cmd_morning(user_message(tg_id, "/morning"), state)
    await morning.submit_main(user_message(tg_id, "backup Flow"), state)
    if secondary is not None:
        await morning.submit_secondary(user_message(tg_id, secondary), state)
    else:
        await morning.skip_secondary(
            callback("mrn:sec:skip", user_id=tg_id, message=bot_message()), state
        )


async def _run_score_steps(state: FakeState) -> None:
    await daily.step_day(callback("ci:day:4", message=bot_message()), state)
    await daily.step_mood(callback("ci:mood:3", message=bot_message()), state)
    await daily.step_energy(callback("ci:energy:2", message=bot_message()), state)


async def _write_evening(tg_id: int = 111) -> None:
    state = FakeState()
    await _run_score_steps(state)
    await daily.skip_reflection(
        callback("ci:ref:no", user_id=tg_id, message=bot_message()), state
    )


# --------------------------------------------------------------------------
# /today — all four combinations
# --------------------------------------------------------------------------
async def test_today_both_morning_and_evening(app_runtime) -> None:  # noqa: F811
    await _write_morning()
    await _write_evening()
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    rendered = msg.answers[-1]
    assert "☀️ <b>Утро</b>" in rendered
    assert "🎯 Главное: backup Flow" in rendered
    assert "○ Ещё: зал" in rendered
    assert "🌙 <b>Итоги</b>" in rendered
    assert "День: 🙂 4/5" in rendered
    assert texts.MORNING_RECORDED_EMPTY not in rendered
    assert texts.EVENING_MISSING not in rendered


async def test_today_morning_only(app_runtime) -> None:  # noqa: F811
    await _write_morning()
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    rendered = msg.answers[-1]
    assert "🎯 Главное: backup Flow" in rendered
    assert texts.EVENING_MISSING in rendered


async def test_today_evening_only(app_runtime) -> None:  # noqa: F811
    await _write_evening()
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    rendered = msg.answers[-1]
    assert texts.MORNING_RECORDED_EMPTY in rendered
    assert "День: 🙂 4/5" in rendered


async def test_today_neither(app_runtime) -> None:  # noqa: F811
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    rendered = msg.answers[-1]
    assert texts.MORNING_RECORDED_EMPTY in rendered
    assert texts.EVENING_MISSING in rendered


# --------------------------------------------------------------------------
# Evening flow entry: step A replaces the v1.1 morning-context header (v1.2)
# --------------------------------------------------------------------------
async def test_evening_opens_with_the_main_outcome_question(app_runtime) -> None:  # noqa: F811
    # v1.2 step A: an intention exists, so the evening closes the loop before
    # asking for the day scores.
    await _write_morning(secondary="зал")
    msg = user_message(111, "/checkin")
    state = FakeState()
    await daily.cmd_checkin(msg, state)
    header = msg.answers[-1]
    assert "🎯 <b>Главное сегодня:</b>" in header
    assert "backup Flow" in header
    assert texts.Q_OUTCOME in header
    assert "Как в целом прошёл твой день?" not in header
    assert state.state == CheckinStates.waiting_main_outcome
    assert state.data == {"target_date": reflection_day("Europe/Moscow").isoformat()}


async def test_evening_header_without_morning_stays_v1_neutral(app_runtime) -> None:  # noqa: F811
    msg = user_message(111, "/checkin")
    await daily.cmd_checkin(msg, FakeState())
    assert msg.answers[-1] == texts.CHECKIN_HEADER
    assert "не записан" not in msg.answers[-1]  # no negative reminder


async def test_evening_header_escapes_morning_text(app_runtime) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "<b>релиз</b> & откат"), state)
    await morning.submit_secondary(user_message(111, "x"), state)

    msg = user_message(111, "/checkin")
    await daily.cmd_checkin(msg, FakeState())
    assert "&lt;b&gt;релиз&lt;/b&gt; &amp; откат" in msg.answers[-1]


async def test_existing_evening_score_flow_semantics_unchanged(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # day → mood → energy → skip reflection still writes exactly the v1 entry.
    state = FakeState()
    await _run_score_steps(state)
    target = bot_message()
    await daily.skip_reflection(callback("ci:ref:no", user_id=111, message=target), state)
    with session_scope(session_factory) as s:
        user = s.query(User).filter_by(telegram_user_id=111).one()
        entry = s.query(DailyEntry).filter_by(user_id=user.id).one()
        assert (entry.day_score, entry.mood_score, entry.energy_score) == (4, 3, 2)
        assert entry.reflection_text is None
    assert texts.done_message(4, 3, 2) in target.edits


async def test_morning_intent_is_stored_on_reflection_day(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await _write_morning()
    with session_scope(session_factory) as s:
        user = s.query(User).filter_by(telegram_user_id=111).one()
        intent = morning_service.get_intent(s, user)
        assert intent is not None
        # v1.1.1: the logical day, not the raw calendar date.
        assert intent.intention_date == reflection_day(user.timezone)


# --------------------------------------------------------------------------
# Callback identity (spec 18): act:checkin / act:edit must authorise the
# person who tapped the button. cb.message is authored by the bot, so reading
# identity from cb.message.from_user authorises BOT_TG_ID and raises
# NotAuthorizedError — the exact production bug these tests pin down. Every
# test here drives the real handler, not the score steps.
# --------------------------------------------------------------------------
def _assert_bot_never_becomes_a_user(session_factory) -> None:
    with session_scope(session_factory) as s:
        assert UserRepository(s).get_by_telegram_id(BOT_TG_ID) is None


async def test_act_checkin_callback_starts_evening_flow_for_tapper(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await _write_morning()
    target = bot_message()
    await daily.cb_start_checkin(callback("act:checkin", user_id=111, message=target), FakeState())
    header = target.answers[-1]
    assert "🎯 <b>Главное сегодня:</b>" in header
    assert "backup Flow" in header
    assert texts.Q_OUTCOME in header
    _assert_bot_never_becomes_a_user(session_factory)


async def test_act_edit_callback_starts_edit_flow_when_entry_exists(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await _write_morning()
    await _write_evening()
    target = bot_message()
    await daily.cb_start_checkin(callback("act:edit", user_id=111, message=target), FakeState())
    # Editing must reach the first question, not bounce back to "already filled".
    assert texts.ALREADY_FILLED not in target.answers
    header = target.answers[-1]
    assert "🎯 <b>Главное сегодня:</b>" in header
    assert texts.Q_OUTCOME in header
    _assert_bot_never_becomes_a_user(session_factory)


async def test_act_checkin_callback_with_existing_entry_still_offers_choice(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The already-filled guard keeps working under callback identity: a plain
    # re-start is not silently allowed to overwrite today's entry.
    await _write_evening()
    target = bot_message()
    await daily.cb_start_checkin(callback("act:checkin", user_id=111, message=target), FakeState())
    assert target.answers[-1] == texts.ALREADY_FILLED
    _assert_bot_never_becomes_a_user(session_factory)


async def test_act_checkin_callback_clears_stale_evening_fsm(
    app_runtime, session_factory  # noqa: F811
) -> None:
    state = FakeState({"day_score": 5})
    state.state = CheckinStates.waiting_mood
    await daily.cb_start_checkin(callback("act:checkin", user_id=111), state)
    assert state.state is None
    # v1.1.1: stale answers are gone; the fresh flow carries only the frozen
    # target Reflection Day.
    assert state.data == {"target_date": reflection_day("Europe/Moscow").isoformat()}
    assert state.cleared >= 1
    _assert_bot_never_becomes_a_user(session_factory)


async def test_act_edit_callback_clears_stale_fsm_before_restarting(
    app_runtime, session_factory  # noqa: F811
) -> None:
    state = FakeState({"main": "leftover"})
    state.state = CheckinStates.waiting_energy
    await daily.cb_start_checkin(callback("act:edit", user_id=111, message=bot_message()), state)
    assert state.state is None
    assert state.data == {"target_date": reflection_day("Europe/Moscow").isoformat()}
    _assert_bot_never_becomes_a_user(session_factory)


async def test_evening_start_callbacks_authorize_the_tapper_not_the_bot(
    app_runtime,  # noqa: F811
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Spying on authorize is what proves the identity source: the same call
    # site reading cb.message.from_user would report BOT_TG_ID instead.
    seen: list[int] = []
    real_authorize = daily.authorize

    def spy(session, settings, telegram_user_id: int):  # type: ignore[no-untyped-def]
        seen.append(telegram_user_id)
        return real_authorize(session, settings, telegram_user_id)

    monkeypatch.setattr(daily, "authorize", spy)
    await daily.cb_start_checkin(callback("act:checkin", user_id=111), FakeState())
    await daily.cb_start_checkin(callback("act:edit", user_id=111), FakeState())
    assert seen == [111, 111]
    assert BOT_TG_ID not in seen
