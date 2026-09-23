"""v1.2 "close the loop" evening flows, driven through the real handlers.

The product guarantee these tests exist for is ordering: an outcome is
committed the instant it is tapped, long before any ``DailyEntry`` exists
(spec 9), a half-finished evening resumes at its first unanswered step
(spec 10), and the Reflection Day baked into a button is what the tap uses
(spec 17-19). Everything here goes through the ``daily``/``morning`` handler
functions with the duck-typed Telegram fakes from ``test_handlers``, so
callback identity (``cb.from_user`` vs the bot-authored ``cb.message``) stays
under test too.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.bot import texts
from app.bot.handlers import daily, morning
from app.bot.states import CheckinStates
from app.database.models import DailyEntry, MorningIntent
from app.database.repositories import MorningIntentRepository, UserRepository
from app.database.session import session_scope
from app.services import morning_service
from app.services.time_service import reflection_day
from tests.test_handlers import (  # noqa: F401
    BOT_TG_ID,
    FakeChat,
    FakeMessage,
    FakeState,
    FakeUser,
    app_runtime,
    callback,
    user_message,
)

ME = 111
OTHER = 222  # also on the allow-list (tests/conftest.py)
TZ = "Europe/Moscow"  # the ``user`` fixture / default timezone


class _LoggedMessage(FakeMessage):
    """A fake Telegram message that also remembers each keyboard sent with it."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.markups: list = []
        self.edit_markups: list = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:  # type: ignore[override]
        await super().answer(text, reply_markup=reply_markup, **kwargs)
        self.markups.append(reply_markup)

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> None:  # type: ignore[override]
        await super().edit_text(text, reply_markup=reply_markup, **kwargs)
        self.edit_markups.append(reply_markup)


def logged_user_message(tg_id: int = ME, text: str | None = None) -> _LoggedMessage:
    return _LoggedMessage(FakeChat(tg_id), FakeUser(tg_id), text)


def logged_bot_message(chat_id: int = ME) -> _LoggedMessage:
    return _LoggedMessage(FakeChat(chat_id), FakeUser(BOT_TG_ID))


def button_data(markup) -> list[str]:
    assert markup is not None, "the step was sent without a keyboard"
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


def outcome_callbacks(field: str, target: date) -> list[str]:
    """The three closure buttons for one field, in display order. The vocabulary
    is closed by design (spec 2): no «перенёс», no «Пропустить», no fourth."""
    return [
        f"ci:out:{field}:{value}:{target.isoformat()}" for value in morning_service.OUTCOMES
    ]


# --------------------------------------------------------------------------
# Helpers that drive the real flows
# --------------------------------------------------------------------------
async def plan(
    main: str = "backup Flow",
    secondary: str | None = "зал, заказать страховку",
    *,
    tg_id: int = ME,
) -> None:
    """Write a morning record through the two-step morning FSM."""
    state = FakeState()
    await morning.cmd_morning(user_message(tg_id, "/morning"), state)
    await morning.submit_main(user_message(tg_id, main), state)
    if secondary is None:
        await morning.skip_secondary(
            callback("mrn:sec:skip", user_id=tg_id, message=logged_bot_message(tg_id)), state
        )
    else:
        await morning.submit_secondary(user_message(tg_id, secondary), state)


async def ask_evening(tg_id: int = ME) -> tuple[_LoggedMessage, FakeState]:
    """``/checkin`` as a fresh explicit start: (prompt message, FSM)."""
    state = FakeState()
    msg = logged_user_message(tg_id, "/checkin")
    await daily.cmd_checkin(msg, state)
    return msg, state


async def start_edit(tg_id: int = ME) -> tuple[_LoggedMessage, FakeState]:
    """``✏️ Изменить`` on a filled day: (prompt message, FSM).

    The FSM returned here is the one the edit flow itself created, so it carries
    the edit permission the outcome handler needs — a fresh ``FakeState`` would
    model a stale button instead of an edit."""
    state = FakeState()
    msg = logged_bot_message(tg_id)
    await daily.cb_start_checkin(callback("act:edit", user_id=tg_id, message=msg), state)
    return msg, state


async def tap(
    data: str,
    state: FakeState,
    *,
    message: _LoggedMessage | None = None,
    tg_id: int = ME,
) -> _LoggedMessage:
    """Tap an outcome button on the bot's prompt message; returns that message,
    whose ``edits`` now hold the next question."""
    target = message if message is not None else logged_bot_message(tg_id)
    await daily.step_outcome(callback(data, user_id=tg_id, message=target), state)
    return target


async def close_scores(state: FakeState, *, scores: tuple[int, int, int] = (4, 3, 2)) -> None:
    """The v1 rating steps, reached once the loop is closed."""
    await daily.step_day(callback(f"ci:day:{scores[0]}", user_id=ME), state)
    await daily.step_mood(callback(f"ci:mood:{scores[1]}", user_id=ME), state)
    await daily.step_energy(callback(f"ci:energy:{scores[2]}", user_id=ME), state)
    await daily.skip_reflection(callback("ci:ref:no", user_id=ME), state)


def fetch(session_factory, tg_id: int = ME) -> MorningIntent:
    """The user's single MorningIntent row, detached so attributes stay readable."""
    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(tg_id)
        assert user is not None
        row = MorningIntentRepository(s).get(user.id, reflection_day(user.timezone))
        assert row is not None
        s.expunge(row)
        return row


def outcomes(session_factory, tg_id: int = ME) -> tuple[str | None, str | None]:
    row = fetch(session_factory, tg_id)
    return (row.main_outcome, row.secondary_outcome)


def count(session_factory, model) -> int:  # type: ignore[no-untyped-def]
    with session_scope(session_factory) as s:
        return s.query(model).count()


# --------------------------------------------------------------------------
# No morning plan: the v1 evening is untouched
# --------------------------------------------------------------------------
async def test_evening_without_a_plan_is_the_unchanged_v1_flow(
    app_runtime, session_factory  # noqa: F811
) -> None:
    msg, state = await ask_evening()
    assert msg.answers[-1] == texts.CHECKIN_HEADER
    assert state.state is None
    assert not any(b.startswith("ci:out:") for b in button_data(msg.markups[-1]))

    await close_scores(state)
    assert count(session_factory, MorningIntent) == 0
    assert count(session_factory, DailyEntry) == 1


# --------------------------------------------------------------------------
# Steps A / B / C
# --------------------------------------------------------------------------
async def test_main_only_plan_asks_main_then_the_day(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ)
    await plan(secondary=None)

    msg, state = await ask_evening()
    assert msg.answers[-1] == texts.q_main_outcome("backup Flow")
    assert state.state == CheckinStates.waiting_main_outcome
    assert button_data(msg.markups[-1]) == outcome_callbacks("main", today)

    prompt = await tap(f"ci:out:main:done:{today.isoformat()}", state)
    # No secondary intention was written, so step B is simply absent.
    assert prompt.edits[-1] == texts.evening_day_prompt(fetch(session_factory))
    assert state.state is None
    assert button_data(prompt.edit_markups[-1]) == [
        f"ci:day:{v}:{today.isoformat()}" for v in (1, 2, 3, 4, 5)
    ]
    assert count(session_factory, DailyEntry) == 0
    assert outcomes(session_factory) == ("done", None)


async def test_main_and_secondary_plan_asks_both_in_order(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ)
    await plan(main="релиз", secondary="зал, страховка")

    msg, state = await ask_evening()
    assert msg.answers[-1] == texts.q_main_outcome("релиз")

    prompt = await tap(f"ci:out:main:done:{today.isoformat()}", state)
    assert prompt.edits[-1] == texts.q_secondary_outcome("зал, страховка")
    assert state.state == CheckinStates.waiting_secondary_outcome
    assert button_data(prompt.edit_markups[-1]) == outcome_callbacks("secondary", today)

    prompt2 = await tap(f"ci:out:secondary:partial:{today.isoformat()}", state)
    assert prompt2.edits[-1] == texts.evening_day_prompt(fetch(session_factory))
    assert state.state is None
    # The secondary field stays ONE question: two items inside it, one answer.
    assert outcomes(session_factory) == ("done", "partial")

    await close_scores(state)
    with session_scope(session_factory) as s:
        entry = s.query(DailyEntry).one()
        assert (entry.day_score, entry.mood_score, entry.energy_score) == (4, 3, 2)


async def test_secondary_is_answered_as_one_whole_even_when_only_one_item_happened(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Spec 15: «зал, заказать страховку» is not a task list; one item done is
    # expressed as a single "partial" on the whole field.
    today = reflection_day(TZ).isoformat()
    await plan(secondary="зал, заказать страховку")
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    await tap(f"ci:out:secondary:partial:{today}", state)
    assert outcomes(session_factory) == ("done", "partial")
    assert count(session_factory, MorningIntent) == 1
    assert fetch(session_factory).secondary_intention == "зал, заказать страховку"


# --------------------------------------------------------------------------
# Spec 9: the tap is the commit
# --------------------------------------------------------------------------
async def test_outcome_is_persisted_immediately_while_no_entry_exists(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(secondary=None)
    _, state = await ask_evening()

    # Tap «Частично» and never come back: the user closes Telegram here.
    await tap(f"ci:out:main:partial:{today}", state)

    assert fetch(session_factory).main_outcome == "partial"
    # The day entry does not exist yet — and a tap must never create one.
    assert count(session_factory, DailyEntry) == 0


# --------------------------------------------------------------------------
# Spec 10: resume / spec 11: edit
# --------------------------------------------------------------------------
async def test_resume_continues_at_the_first_unanswered_step(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)

    # A brand-new FSM (restart, lost MemoryStorage) must not re-ask main.
    again, state2 = await ask_evening()
    assert again.answers[-1] == texts.q_secondary_outcome("зал, заказать страховку")
    assert outcomes(session_factory) == ("done", None)

    await tap(f"ci:out:secondary:done:{today}", state2)
    third, state3 = await ask_evening()
    assert third.answers[-1] == texts.evening_day_prompt(fetch(session_factory))
    assert state3.state is None
    assert not any(b.startswith("ci:out:") for b in button_data(third.markups[-1]))


async def test_edit_rewalks_both_outcomes_without_clearing_answers(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Review P0: an evening that is already closed is re-walked question by
    # question — the stored secondary answer must not swallow step B.
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    await tap(f"ci:out:secondary:done:{today}", state)
    await close_scores(state)
    assert outcomes(session_factory) == ("done", "done")

    target, edit_state = await start_edit()
    assert target.answers[-1] == texts.q_main_outcome("backup Flow")
    assert edit_state.data["edit_mode"] is True
    # Walking back does not wipe what was already answered...
    assert outcomes(session_factory) == ("done", "done")
    assert count(session_factory, DailyEntry) == 1

    # ...and a new main tap is followed by the SECOND question, even though it
    # already has an answer, rather than jumping to the ratings.
    prompt = await tap(f"ci:out:main:not_done:{today}", edit_state, message=target)
    assert prompt.edits[-1] == texts.q_secondary_outcome("зал, заказать страховку")
    assert edit_state.state == CheckinStates.waiting_secondary_outcome
    assert outcomes(session_factory) == ("not_done", "done")

    prompt2 = await tap(f"ci:out:secondary:partial:{today}", edit_state, message=prompt)
    assert prompt2.edits[-1] == texts.evening_day_prompt(fetch(session_factory))
    assert outcomes(session_factory) == ("not_done", "partial")
    # Each tap replaces exactly one column, and the day entry stays as it was.
    assert count(session_factory, DailyEntry) == 1


async def test_abandoning_an_edit_after_the_first_answer_keeps_the_second(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    await tap(f"ci:out:secondary:not_done:{today}", state)
    await close_scores(state)

    target, edit_state = await start_edit()
    # The user corrects the first answer and stops there, before step B.
    await tap(f"ci:out:main:partial:{today}", edit_state, message=target)
    assert outcomes(session_factory) == ("partial", "not_done")
    with session_scope(session_factory) as s:
        entry = s.query(DailyEntry).one()
        assert (entry.day_score, entry.mood_score, entry.energy_score) == (4, 3, 2)
        assert s.query(MorningIntent).one().main_intention == "backup Flow"


async def test_nothing_changes_when_an_edit_is_not_finished(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    await tap(f"ci:out:main:partial:{today}", state)
    await tap(f"ci:out:secondary:not_done:{today}", state)
    await close_scores(state)

    await start_edit()
    # The user stops here: no further tap at all.
    assert outcomes(session_factory) == ("partial", "not_done")
    with session_scope(session_factory) as s:
        entry = s.query(DailyEntry).one()
        assert (entry.day_score, entry.mood_score, entry.energy_score) == (4, 3, 2)
        assert s.query(MorningIntent).one().main_intention == "backup Flow"


# --------------------------------------------------------------------------
# Review P0: a closed evening is only re-opened by an explicit edit
# --------------------------------------------------------------------------
async def test_a_stale_tap_cannot_rewrite_a_filled_day(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    await tap(f"ci:out:secondary:done:{today}", state)
    await close_scores(state)

    # The evening message from earlier is still on screen and gets tapped again
    # — with no FSM at all, which is exactly what a scheduled prompt leaves.
    stale = await tap(f"ci:out:main:not_done:{today}", FakeState())
    assert outcomes(session_factory) == ("done", "done")
    assert count(session_factory, MorningIntent) == 1
    assert count(session_factory, DailyEntry) == 1
    assert stale.edits == []  # the user's screen is not moved on either


async def test_the_same_button_tapped_through_an_edit_does_change_it(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The difference is not the button but the flow it came through.
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    await close_scores(state)

    target, edit_state = await start_edit()
    await tap(f"ci:out:main:not_done:{today}", edit_state, message=target)
    assert outcomes(session_factory) == ("not_done", None)
    assert count(session_factory, DailyEntry) == 1


# --------------------------------------------------------------------------
# Review P1: the open flow decides the day, and the field it is asking about
# --------------------------------------------------------------------------
async def test_the_frozen_flow_date_beats_a_stale_callback_date(
    app_runtime, session_factory  # noqa: F811
) -> None:
    yesterday = reflection_day(TZ) - timedelta(days=1)
    today = reflection_day(TZ)
    await plan(main="сегодня", secondary=None)
    with session_scope(session_factory) as s:
        u = UserRepository(s).get_by_telegram_id(ME)
        assert u is not None
        s.add(
            MorningIntent(
                user_id=u.id, intention_date=yesterday, main_intention="вчера"
            )
        )
        s.commit()

    _, state = await ask_evening()  # freezes today
    # A button belonging to yesterday's prompt, tapped inside today's flow.
    await tap(f"ci:out:main:done:{yesterday.isoformat()}", state)
    with session_scope(session_factory) as s:
        by_date = {r.intention_date: r.main_outcome for r in s.query(MorningIntent)}
        assert by_date == {yesterday: None, today: "done"}
    assert state.data["target_date"] == today.isoformat()


async def test_a_button_for_the_other_question_is_ignored_while_one_is_open(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan()
    _, state = await ask_evening()
    assert state.state == CheckinStates.waiting_main_outcome
    prompt = logged_bot_message()

    # Step B's button, tapped while step A is the open question.
    ignored = await tap(f"ci:out:secondary:done:{today}", state, message=prompt)
    assert outcomes(session_factory) == (None, None)
    assert ignored.edits == []
    assert state.state == CheckinStates.waiting_main_outcome
    # The flow still works when the right button is tapped.
    await tap(f"ci:out:main:done:{today}", state, message=prompt)
    assert outcomes(session_factory) == ("done", None)
    assert prompt.edits[-1] == texts.q_secondary_outcome("зал, заказать страховку")


# --------------------------------------------------------------------------
# Spec 33: double taps / spec 18: taps with no FSM / spec 34: isolation
# --------------------------------------------------------------------------
async def test_double_tap_writes_one_row_and_keeps_the_last_answer(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(secondary=None)
    _, state = await ask_evening()
    for _ in range(3):
        await tap(f"ci:out:main:done:{today}", state)
    assert outcomes(session_factory) == ("done", None)
    assert count(session_factory, MorningIntent) == 1
    assert count(session_factory, DailyEntry) == 0
    # A deliberate change of mind is allowed and stays on the same row.
    await tap(f"ci:out:main:not_done:{today}", state)
    assert outcomes(session_factory) == ("not_done", None)
    assert count(session_factory, MorningIntent) == 1


async def test_outcome_button_works_on_a_completely_empty_fsm(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # The scheduled evening prompt carries a keyboard but creates no FSM state,
    # and a restart can wipe MemoryStorage: the tap must be self-contained.
    today = reflection_day(TZ)
    await plan()
    state = FakeState()

    target = await tap(f"ci:out:main:done:{today.isoformat()}", state)
    assert state.data == {"target_date": today.isoformat()}
    assert state.state == CheckinStates.waiting_secondary_outcome
    assert outcomes(session_factory) == ("done", None)
    assert target.edits[-1] == texts.q_secondary_outcome("зал, заказать страховку")


@pytest.mark.parametrize(
    ("data", "description"),
    [
        ("ci:out:main", "truncated callback"),
        (f"ci:out:main:перенёс:{reflection_day(TZ).isoformat()}", "value outside the vocabulary"),
        (f"ci:out:third:done:{reflection_day(TZ).isoformat()}", "unknown field"),
    ],
)
async def test_handcrafted_outcome_callbacks_change_nothing(
    app_runtime, session_factory, data, description  # noqa: F811
) -> None:
    await plan()
    state = FakeState()
    target = logged_bot_message()
    await daily.step_outcome(callback(data, user_id=ME, message=target), state)
    assert outcomes(session_factory) == (None, None)
    assert state.data == {} and state.state is None
    assert target.edits == []  # the prompt the user is looking at is left alone


async def test_outcome_callback_without_a_morning_record_creates_nothing(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    state = FakeState()
    await daily.step_outcome(callback(f"ci:out:main:done:{today}", user_id=ME), state)
    assert count(session_factory, MorningIntent) == 0
    assert state.data == {}


async def test_secondary_outcome_is_refused_when_only_the_main_field_exists(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(secondary=None)
    state = FakeState()
    await daily.step_outcome(callback(f"ci:out:secondary:done:{today}", user_id=ME), state)
    assert outcomes(session_factory) == (None, None)


async def test_one_users_tap_never_closes_another_users_plan(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(main="мой план", secondary=None)
    await plan(main="чужой план", secondary=None, tg_id=OTHER)

    state = FakeState()
    await tap(f"ci:out:main:done:{today}", state, tg_id=OTHER)

    assert outcomes(session_factory, ME) == (None, None)
    assert outcomes(session_factory, OTHER) == ("done", None)
    with session_scope(session_factory) as s:
        rows = {r.main_intention: r.main_outcome for r in s.query(MorningIntent)}
        assert rows == {"мой план": None, "чужой план": "done"}
        # Identity never leaks to the bot account.
        assert UserRepository(s).get_by_telegram_id(BOT_TG_ID) is None


# --------------------------------------------------------------------------
# Spec 19: the day keyboard answers both callback shapes
# --------------------------------------------------------------------------
async def test_legacy_dateless_day_callback_still_works(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # A keyboard from before the upgrade is still tappable after it.
    today = reflection_day(TZ)
    state = FakeState()
    await daily.step_day(callback("ci:day:4", user_id=ME), state)
    assert state.data["target_date"] == today.isoformat()
    assert state.data["day_score"] == 4

    await daily.step_mood(callback("ci:mood:3", user_id=ME), state)
    await daily.step_energy(callback("ci:energy:2", user_id=ME), state)
    await daily.skip_reflection(callback("ci:ref:no", user_id=ME), state)
    with session_scope(session_factory) as s:
        assert s.query(DailyEntry).one().entry_date == today


async def test_dated_day_callback_freezes_the_baked_reflection_day(
    app_runtime, session_factory  # noqa: F811
) -> None:
    other = reflection_day(TZ) - timedelta(days=3)
    state = FakeState()
    await daily.step_day(callback(f"ci:day:5:{other.isoformat()}", user_id=ME), state)
    assert state.data["target_date"] == other.isoformat()


async def test_an_already_frozen_fsm_date_beats_the_callback_date(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Explicit starts freeze the date first (v1.1.1); a later button must not
    # silently move the flow to another day.
    frozen = reflection_day(TZ)
    state = FakeState({"target_date": frozen.isoformat()})
    await daily.step_day(callback(f"ci:day:4:{(frozen - timedelta(days=1)).isoformat()}", user_id=ME), state)
    assert state.data["target_date"] == frozen.isoformat()


# --------------------------------------------------------------------------
# Spec 24-26: rendering
# --------------------------------------------------------------------------
async def test_plan_text_is_escaped_in_every_evening_view(
    app_runtime, session_factory  # noqa: F811
) -> None:
    nasty = '<img src=x onerror="alert(1)"> & "кот"'
    safe = "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; &quot;кот&quot;"
    today = reflection_day(TZ).isoformat()
    await plan(main=nasty, secondary=nasty)

    msg, state = await ask_evening()
    assert safe in msg.answers[-1]
    prompt1 = await tap(f"ci:out:main:done:{today}", state)
    assert safe in prompt1.edits[-1]  # step B quotes the secondary text back
    prompt2 = await tap(f"ci:out:secondary:done:{today}", state)

    view = logged_user_message(ME, "/today")
    await daily.cmd_today(view)
    morning_view = logged_user_message(ME, "/morning")
    await morning.cmd_morning(morning_view, FakeState())

    rendered = "\n".join(
        [*msg.answers, *msg.edits, *prompt1.edits, *prompt2.edits, *view.answers,
         *morning_view.answers]
    )
    assert "<img" not in rendered
    assert safe in rendered
    assert rendered.count(safe) >= 4  # both closure questions + /today + /morning


# --------------------------------------------------------------------------
# Spec 25: /today shows a result line only for what was actually answered
# --------------------------------------------------------------------------
async def test_today_shows_a_result_line_per_recorded_outcome(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(main="главное", secondary="ещё")
    _, state = await ask_evening()

    # Nothing answered yet: /today must not print empty status lines at all.
    # (The evening block's own neutral "не заполнен" is a different thing — it
    # describes missing scores, not a missing outcome.)
    view = logged_user_message(ME, "/today")
    await daily.cmd_today(view)
    assert "Результат" not in view.answers[-1]
    assert "NULL" not in view.answers[-1] and "unknown" not in view.answers[-1]

    await tap(f"ci:out:main:partial:{today}", state)
    view = logged_user_message(ME, "/today")
    await daily.cmd_today(view)
    rendered = view.answers[-1]
    assert rendered.count("Результат") == 1
    assert "🎯 Главное: главное\nРезультат: ➗ Частично" in rendered
    assert "❌" not in rendered  # only the recorded answer is shown

    await tap(f"ci:out:secondary:not_done:{today}", state)
    view = logged_user_message(ME, "/today")
    await daily.cmd_today(view)
    assert "○ Ещё: ещё\nРезультат: ❌ Нет" in view.answers[-1]

    await tap(f"ci:out:main:done:{today}", state)
    view = logged_user_message(ME, "/today")
    await daily.cmd_today(view)
    assert "🎯 Главное: главное\nРезультат: ✅ Да" in view.answers[-1]


async def test_morning_view_shows_recorded_outcomes_and_no_empty_ones(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(main="главное", secondary="ещё")

    view = logged_user_message(ME, "/morning")
    await morning.cmd_morning(view, FakeState())
    assert "Результат" not in view.answers[-1]  # before the evening: no status lines

    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    view = logged_user_message(ME, "/morning")
    await morning.cmd_morning(view, FakeState())
    assert "Результат: ✅ Да" in view.answers[-1]
    # The still-unanswered secondary field shows no result line.
    assert view.answers[-1].count("Результат") == 1


async def test_the_closure_questions_never_interpret_the_answer(
    app_runtime, session_factory  # noqa: F811
) -> None:
    # Spec 29: outcomes are facts, presented neutrally.
    today = reflection_day(TZ).isoformat()
    await plan(secondary=None)
    msg, state = await ask_evening()
    prompt = await tap(f"ci:out:main:not_done:{today}", state)
    for text in [*msg.answers, *msg.edits, *prompt.edits]:
        for forbidden in ("плохо спланировал", "не справился", "стыдно", "надо было", "опять"):
            assert forbidden not in text


async def test_only_the_three_canonical_outcome_buttons_are_offered(
    app_runtime, session_factory  # noqa: F811
) -> None:
    await plan()
    msg, _ = await ask_evening()
    labels = [b.text for row in msg.markups[-1].inline_keyboard for b in row]
    assert labels == ["✅ Да", "➗ Частично", "❌ Нет"]


async def test_morning_texts_are_never_rewritten_by_an_outcome_tap(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(main="главное", secondary="ещё")
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    await tap(f"ci:out:secondary:done:{today}", state)
    row = fetch(session_factory)
    assert (row.main_intention, row.secondary_intention) == ("главное", "ещё")
    assert (row.main_outcome, row.secondary_outcome) == ("done", "done")


# --------------------------------------------------------------------------
# Review P2: /today never offers a morning action the service would refuse
# --------------------------------------------------------------------------
async def today_buttons(tg_id: int = ME) -> list[str]:
    view = logged_user_message(tg_id, "/today")
    await daily.cmd_today(view)
    return [b.text for row in view.markups[-1].inline_keyboard for b in row]


async def test_today_offers_the_morning_edit_only_until_an_outcome_exists(
    app_runtime, session_factory  # noqa: F811
) -> None:
    today = reflection_day(TZ).isoformat()
    await plan(main="главное", secondary=None)
    assert await today_buttons() == ["✏️ Изменить утро", "🌙 Заполнить итог"]

    # Closing the loop freezes the plan, so the button that would reopen it
    # disappears with it.
    _, state = await ask_evening()
    await tap(f"ci:out:main:done:{today}", state)
    assert await today_buttons() == ["🌙 Заполнить итог"]


async def test_today_never_offers_a_retroactive_morning_for_a_filled_day(
    app_runtime, session, user  # noqa: F811
) -> None:
    # An empty day really is still open for planning.
    assert await today_buttons() == ["☀️ Записать утро", "🌙 Заполнить итог"]

    session.add(
        DailyEntry(
            user_id=user.id, entry_date=reflection_day(TZ), day_score=3, mood_score=3,
            energy_score=3,
        )
    )
    session.commit()
    # A filled evening with no morning row: "записать утро" would promise a write
    # the service now refuses, so only the evening action is left.
    assert await today_buttons() == ["✏️ Изменить итог"]
