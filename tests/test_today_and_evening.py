"""Unified /today snapshot + evening morning-context tests (v1.1, spec 27)."""

from __future__ import annotations

from app.bot import texts
from app.bot.handlers import daily, morning
from app.database.models import DailyEntry, User
from app.database.session import session_scope
from app.services import morning_service
from app.services.time_service import user_today
from tests.test_handlers import (  # noqa: F401
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
# Evening flow: morning context, existing semantics unchanged
# --------------------------------------------------------------------------
async def test_evening_header_shows_morning_intention(app_runtime) -> None:  # noqa: F811
    await _write_morning(secondary="зал")
    msg = user_message(111, "/checkin")
    await daily.cmd_checkin(msg, FakeState())
    header = msg.answers[-1]
    assert "Утром ты планировал:" in header
    assert "🎯 Главное: backup Flow" in header
    assert "○ Ещё: зал" in header
    assert "Как в целом прошёл твой день?" in header


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


async def test_morning_intent_is_stored_on_user_local_date(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await _write_morning()
    with session_scope(session_factory) as s:
        user = s.query(User).filter_by(telegram_user_id=111).one()
        intent = morning_service.get_intent(s, user)
        assert intent is not None
        assert intent.intention_date == user_today(user.timezone)
