"""Morning flow handler tests (v1.1, spec section 27 Morning handler group).

Reuses the duck-typed Telegram fakes from ``tests.test_handlers``: the point is
to catch handler-level bugs — the critical "main intention is committed to the
DB before the secondary step", callback identity via ``cb.from_user.id``,
stale-FSM clearing on explicit starts, HTML escaping and edit-flow semantics.
"""

from __future__ import annotations

import pytest
from aiogram.filters import Command
from sqlalchemy.orm import Session

from app.bot import texts
from app.bot.handlers import daily, morning
from app.bot.states import CheckinStates, MorningStates
from app.database.models import MorningIntent
from app.database.repositories import UserRepository
from app.database.session import session_scope
from app.services import morning_service
from app.services.auth_service import NotAuthorizedError
from app.services.time_service import reflection_day
from tests.test_handlers import (
    BOT_TG_ID,
    FakeState,
    app_runtime,  # noqa: F401  (pytest fixture, imported for reuse)
    bot_message,
    callback,
    user_message,
)


def _intent_for(session: Session, tg_id: int) -> MorningIntent | None:
    user = UserRepository(session).get_by_telegram_id(tg_id)
    assert user is not None
    return morning_service.get_intent(session, user)


# --------------------------------------------------------------------------
# Command / menu entry
# --------------------------------------------------------------------------
async def test_morning_command_filter_matches_only_slash_command() -> None:
    from datetime import UTC, datetime

    from aiogram.enums import ChatType
    from aiogram.types import Chat as TgChat
    from aiogram.types import Message as TgMessage
    from aiogram.types import User as TgUser

    def tg_msg(text: str) -> TgMessage:
        return TgMessage(
            message_id=1,
            date=datetime.now(UTC),
            chat=TgChat(id=111, type=ChatType.PRIVATE),
            from_user=TgUser(id=111, is_bot=False, first_name="T"),
            text=text,
        )

    f = Command("morning")
    assert await f(message=tg_msg("/morning"), bot=None)  # type: ignore[arg-type]
    assert not await f(message=tg_msg("☀️ Утренний фокус"), bot=None)  # type: ignore[arg-type]


async def test_morning_first_start_enters_flow(app_runtime) -> None:  # noqa: F811
    state = FakeState()
    msg = user_message(111, "/morning")
    await morning.cmd_morning(msg, state)
    assert texts.Q_MAIN_INTENTION in msg.answers
    assert state.state is MorningStates.waiting_main


async def test_scheduler_callback_button_starts_fsm(app_runtime) -> None:  # noqa: F811
    # The morning notification itself never touches the FSM; only this
    # callback does.
    state = FakeState()
    target = bot_message()
    await morning.cb_start_morning(callback("mrn:start", user_id=111, message=target), state)
    assert texts.Q_MAIN_INTENTION in target.answers
    assert state.state is MorningStates.waiting_main


async def test_morning_twice_shows_record_not_silent_overwrite(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "главное"), state)
    await morning.skip_secondary(callback("mrn:sec:skip", user_id=111, message=bot_message()), state)

    state2 = FakeState()
    msg = user_message(111, "/morning")
    await morning.cmd_morning(msg, state2)
    shown = msg.answers[-1]
    assert texts.MORNING_EMPTY_TODAY in shown
    assert "🎯 Главное: главное" in shown
    assert state2.state is None  # not re-entered; user must tap Изменить


# --------------------------------------------------------------------------
# CRITICAL persistence regression: main is committed before secondary exists
# --------------------------------------------------------------------------
async def test_main_saved_to_db_immediately_before_secondary(
    app_runtime, session_factory  # noqa: F811
) -> None:
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    second_msg = user_message(111, "  закончить разбор backup Flow  ")
    await morning.submit_main(second_msg, state)

    # After step 1 the row is ALREADY durably committed — a restart here must
    # not lose the main intention.
    with session_scope(session_factory) as s:
        intent = _intent_for(s, 111)
        assert intent is not None
        assert intent.main_intention == "закончить разбор backup Flow"  # trimmed in DB
        assert intent.secondary_intention is None

    assert texts.Q_SECONDARY_INTENTION in second_msg.answers
    assert state.state is MorningStates.waiting_secondary


async def test_secondary_text_updates_same_row(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "главное"), state)
    done = user_message(111, "сходить в зал")
    await morning.submit_secondary(done, state)

    with session_scope(session_factory) as s:
        intent = _intent_for(s, 111)
        assert intent.main_intention == "главное"
        assert intent.secondary_intention == "сходить в зал"
        assert s.query(MorningIntent).count() == 1  # never a second row
    assert state.state is None and state.data == {}  # cleared only after save
    assert texts.MORNING_DONE_HEADER in done.answers[-1]
    assert "○ Ещё: сходить в зал" in done.answers[-1]


async def test_secondary_skip_keeps_none_and_omits_line(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "главное"), state)
    target = bot_message()
    await morning.skip_secondary(callback("mrn:sec:skip", user_id=111, message=target), state)

    with session_scope(session_factory) as s:
        intent = _intent_for(s, 111)
        assert intent.secondary_intention is None
    # Callback flow edits the bot's own message; no empty secondary line.
    assert "○ Ещё" not in target.edits[-1]
    assert texts.MORNING_DONE_HEADER in target.edits[-1]
    assert state.state is None


async def test_empty_main_reasked_and_state_kept(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    empty = user_message(111, "   \n  ")
    await morning.submit_main(empty, state)
    assert texts.INTENTION_EMPTY in empty.answers
    assert state.state is MorningStates.waiting_main  # not advanced
    with session_scope(session_factory) as s:
        assert s.query(MorningIntent).count() == 0


async def test_too_long_text_rejected(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    long = user_message(111, "a" * (morning_service.MAX_INTENTION_LENGTH + 1))
    await morning.submit_main(long, state)
    assert texts.INTENTION_TOO_LONG in long.answers
    with session_scope(session_factory) as s:
        assert s.query(MorningIntent).count() == 0


# --------------------------------------------------------------------------
# Edit flow
# --------------------------------------------------------------------------
async def test_edit_flow_keeps_secondary_on_main_step_then_skip_clears_it(
    app_runtime, session_factory  # noqa: F811
) -> None:
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "старое главное"), state)
    await morning.submit_secondary(user_message(111, "старое ещё"), state)

    # ✏️ Изменить re-runs the flow.
    state2 = FakeState()
    await morning.cb_start_morning(callback("mrn:edit", user_id=111, message=bot_message()), state2)
    await morning.submit_main(user_message(111, "новое главное"), state2)
    with session_scope(session_factory) as s:
        intent = _intent_for(s, 111)
        assert intent.main_intention == "новое главное"
        assert intent.secondary_intention == "старое ещё"  # kept, not clobbered
        assert s.query(MorningIntent).count() == 1

    # Skip in the EDIT flow must clear the old secondary value.
    await morning.skip_secondary(callback("mrn:sec:skip", user_id=111, message=bot_message()), state2)
    with session_scope(session_factory) as s:
        assert _intent_for(s, 111).secondary_intention is None


# --------------------------------------------------------------------------
# Stale FSM clearing (spec section 14)
# --------------------------------------------------------------------------
async def test_explicit_evening_start_clears_stale_morning_fsm(app_runtime) -> None:  # noqa: F811
    state = FakeState({"main_draft": "x"})
    state.state = MorningStates.waiting_secondary
    await daily.cmd_checkin(user_message(111, "/checkin"), state)
    assert state.cleared >= 1
    assert state.state == CheckinStates.waiting_day  # the day question, not no flow
    # v1.1.1: only the frozen target Reflection Day survives the clear.
    assert state.data == {"target_date": reflection_day("Europe/Moscow").isoformat()}


async def test_explicit_morning_start_clears_stale_checkin_fsm(app_runtime) -> None:  # noqa: F811
    state = FakeState({"day_score": 4})
    state.state = MorningStates.waiting_main
    await morning.menu_morning(user_message(111, "☀️ Утренний фокус"), state)
    assert state.cleared >= 1
    assert state.state is MorningStates.waiting_main  # fresh flow, no old data
    # v1.1.1: only the frozen target Reflection Day survives the clear.
    assert state.data == {"target_date": reflection_day("Europe/Moscow").isoformat()}


# --------------------------------------------------------------------------
# Identity, escaping, privacy
# --------------------------------------------------------------------------
async def test_secondary_skip_saves_for_callback_user_not_bot(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Regression guard: identity must come from cb.from_user.id; cb.message
    # is authored by the bot (BOT_TG_ID) and must never be used.
    state = FakeState()
    await morning.cb_start_morning(callback("mrn:start", user_id=111, message=bot_message()), state)
    await morning.submit_main(user_message(111, "главное"), state)
    await morning.skip_secondary(
        callback("mrn:sec:skip", user_id=111, message=bot_message()), state
    )
    with session_scope(session_factory) as s:
        intent = _intent_for(s, 111)
        assert intent is not None
        assert UserRepository(s).get_by_telegram_id(BOT_TG_ID) is None


async def test_text_flow_replies_done_as_new_message_never_edits_user_text(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Regression: the ✅ confirmation must be a new message in the text flow —
    # Telegram forbids editing a user-authored message.
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "главное"), state)
    user_msg = user_message(111, "зал")
    await morning.submit_secondary(user_msg, state)
    assert user_msg.edits == []
    assert texts.MORNING_DONE_HEADER in user_msg.answers[-1]


async def test_user_text_is_html_escaped_in_every_render(app_runtime) -> None:  # noqa: F811
    state = FakeState()
    await morning.cmd_morning(user_message(111, "/morning"), state)
    await morning.submit_main(user_message(111, "<b>Важное</b> & дедлайн"), state)
    done = user_message(111, "<i>Ещё</i> & зал")
    await morning.submit_secondary(done, state)
    rendered = done.answers[-1]
    assert "&lt;b&gt;Важное&lt;/b&gt; &amp; дедлайн" in rendered
    assert "&lt;i&gt;Ещё&lt;/i&gt; &amp; зал" in rendered
    assert "<b>Важное</b>" not in rendered


async def test_unauthorized_user_cannot_write(app_runtime, session_factory) -> None:  # noqa: F811
    state = FakeState()
    with pytest.raises(NotAuthorizedError):
        await morning.submit_main(user_message(999, "злой инсайт"), state)
    with session_scope(session_factory) as s:
        assert s.query(MorningIntent).count() == 0
