"""Handler-level regression tests.

These exercise the Telegram handler functions directly with fake
Message/CallbackQuery/FSMContext objects. The point is to catch bugs that
service/repository tests structurally cannot see — in particular the
``callback.from_user`` vs ``callback.message.from_user`` identity confusion,
FSM-clear ordering, onboarding side effects and private-chat restriction.
No network, no real bot token.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.types import Chat as TgChat
from aiogram.types import Message as TgMessage
from aiogram.types import User as TgUser

import app.runtime as runtime_module
from app.bot import texts
from app.bot.deps import PrivateChatFilter
from app.bot.handlers import daily, start, weekly
from app.bot.keyboards.reply import BTN_TODAY
from app.config import Settings
from app.database.models import DailyEntry, User, WeeklyReflection
from app.database.repositories import UserRepository
from app.database.session import session_scope
from app.runtime import Runtime, init_runtime
from app.scheduler.scheduler import ReflectionScheduler
from app.services.auth_service import NotAuthorizedError

BOT_TG_ID = 777  # id of the bot that *sent* the message being interacted with


# --------------------------------------------------------------------------
# Telegram-object fakes (duck-typed; handlers only use the methods below)
# --------------------------------------------------------------------------
class FakeUser:
    def __init__(self, id: int, full_name: str = "Test User") -> None:
        self.id = id
        self.full_name = full_name
        self.username = None


class FakeChat:
    def __init__(self, id: int, type: str = "private") -> None:
        self.id = id
        self.type = type


class FakeMessage:
    def __init__(self, chat: FakeChat, from_user: FakeUser, text: str | None = None) -> None:
        self.chat = chat
        self.from_user = from_user
        self.text = text
        self.answers: list[str] = []
        self.edits: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs) -> None:
        self.answers.append(text)

    async def edit_text(self, text: str, reply_markup=None, **kwargs) -> None:
        # Telegram forbids editing messages authored by the user; treat any
        # such attempt in handlers as a test failure.
        assert self.from_user.id == BOT_TG_ID, "bot tried to edit a user-authored message"
        self.edits.append(text)


class FakeCallback:
    def __init__(self, data: str, user_id: int, message: FakeMessage) -> None:
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = message
        self.answered = 0

    async def answer(self, *args, **kwargs) -> None:
        self.answered += 1


class FakeState:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.state = None
        self.cleared = 0

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def set_state(self, state) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data = {}
        self.state = None
        self.cleared += 1


class AnyBot:
    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        pass  # scheduler job bodies are never executed here


def user_message(tg_id: int = 111, text: str | None = None) -> FakeMessage:
    """A message typed by a user in their private chat."""
    return FakeMessage(FakeChat(tg_id), FakeUser(tg_id), text)


def bot_message(chat_id: int = 111) -> FakeMessage:
    """A message the *bot* sent into the user's chat.

    ``from_user`` is the bot — handlers must never read identity from it.
    """
    return FakeMessage(FakeChat(chat_id), FakeUser(BOT_TG_ID))


def callback(data: str, user_id: int = 111, message: FakeMessage | None = None) -> FakeCallback:
    return FakeCallback(data, user_id, message or bot_message())


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def app_runtime(settings: Settings, session_factory) -> Iterator[Runtime]:
    runtime = Runtime(
        settings=settings,
        engine=session_factory.kw["bind"],
        session_factory=session_factory,
        scheduler=ReflectionScheduler(session_factory, AnyBot()),  # type: ignore[arg-type]
        bot=AnyBot(),  # type: ignore[arg-type]
    )
    init_runtime(runtime)
    yield runtime
    runtime_module._runtime = None


# --------------------------------------------------------------------------
# 1. /start onboarding: user creation + scheduler registration (item 1)
# --------------------------------------------------------------------------
async def test_start_creates_user_and_registers_scheduler_jobs(app_runtime, session_factory) -> None:
    app_runtime.scheduler.apscheduler.start()
    try:
        msg = user_message(111, "/start")
        await start.cmd_start(msg, FakeState())

        with session_scope(session_factory) as s:
            user = UserRepository(s).get_by_telegram_id(111)
            assert user is not None
            assert user.display_name == "Test User"
            pk = user.id

        jobs = [j.id for j in app_runtime.scheduler.apscheduler.get_jobs()]
        assert sorted(jobs) == sorted(
            [f"morning:{pk}", f"checkin:{pk}", f"reminder:{pk}"]
        )
        assert texts.START in msg.answers  # welcome comes after onboarding

        # Repeated /start is idempotent: no second user, no extra jobs.
        await start.cmd_start(user_message(111, "/start"), FakeState())
        jobs2 = [j.id for j in app_runtime.scheduler.apscheduler.get_jobs()]
        assert sorted(jobs2) == sorted(
            [f"morning:{pk}", f"checkin:{pk}", f"reminder:{pk}"]
        )
        with session_scope(session_factory) as s:
            assert len(UserRepository(s).list_all()) == 1
    finally:
        app_runtime.scheduler.apscheduler.shutdown(wait=False)


# --------------------------------------------------------------------------
# 2. Daily FSM: callback identity + FSM-clear ordering (items 2, 3)
# --------------------------------------------------------------------------
async def _run_score_steps(state: FakeState) -> None:
    await daily.step_day(callback("ci:day:4", message=bot_message()), state)
    await daily.step_mood(callback("ci:mood:3", message=bot_message()), state)
    await daily.step_energy(callback("ci:energy:2", message=bot_message()), state)


async def test_daily_skip_button_saves_for_the_tapping_user(app_runtime, session_factory) -> None:
    state = FakeState()
    await _run_score_steps(state)
    done_target = bot_message()  # the bot's own message with the buttons
    await daily.skip_reflection(callback("ci:ref:no", user_id=111, message=done_target), state)

    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(111)  # NOT the bot's id 777
        assert user is not None
        entry = s.query(DailyEntry).filter_by(user_id=user.id).one()
        assert (entry.day_score, entry.mood_score, entry.energy_score) == (4, 3, 2)
        assert entry.reflection_text is None
        assert UserRepository(s).get_by_telegram_id(BOT_TG_ID) is None
    assert state.cleared == 1
    assert texts.done_message(4, 3, 2) in done_target.edits


async def test_daily_text_reflection_flow_saves(app_runtime, session_factory) -> None:
    state = FakeState()
    await _run_score_steps(state)
    await daily.ask_reflection_text(callback("ci:ref:yes", message=bot_message()), state)
    assert state.state is not None
    text_msg = user_message(111, "Сегодня <нормально> & спокойно")
    await daily.submit_reflection_text(text_msg, state)

    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(111)
        entry = s.query(DailyEntry).filter_by(user_id=user.id).one()
        # The DB keeps the raw text; escaping happens only at render time.
        assert entry.reflection_text == "Сегодня <нормально> & спокойно"
    assert state.cleared == 1


async def test_daily_skip_after_text_step_saves(app_runtime, session_factory) -> None:
    state = FakeState()
    await _run_score_steps(state)
    await daily.ask_reflection_text(callback("ci:ref:yes", message=bot_message()), state)
    await daily.skip_reflection_text(callback("ci:reftext:skip", message=bot_message()), state)

    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(111)
        entry = s.query(DailyEntry).filter_by(user_id=user.id).one()
        assert entry.reflection_text is None


async def test_text_flow_replies_done_as_new_message_never_edits_user_text(
    app_runtime, session_factory
) -> None:
    # Regression: _finalize must not edit_text() the user's own message —
    # Telegram forbids it. The ✅ Готово confirmation goes out as a new message.
    state = FakeState()
    await _run_score_steps(state)
    await daily.ask_reflection_text(callback("ci:ref:yes", message=bot_message()), state)
    user_msg = user_message(111, "Мысли вслух")
    await daily.submit_reflection_text(user_msg, state)

    assert user_msg.edits == []  # user-authored FakeMessage was not edited
    assert texts.done_message(4, 3, 2) in user_msg.answers
    assert state.cleared == 1


async def test_finalize_keeps_fsm_state_when_save_fails(app_runtime) -> None:
    # 999 is not on the allow-list: authorisation fails inside _finalize.
    state = FakeState({"day_score": 4, "mood_score": 3, "energy_score": 2})
    with pytest.raises(NotAuthorizedError):
        await daily.skip_reflection(callback("ci:ref:no", user_id=999, message=bot_message()), state)
    assert state.cleared == 0  # answers are NOT lost on a failed save
    assert state.data == {"day_score": 4, "mood_score": 3, "energy_score": 2}


# --------------------------------------------------------------------------
# 3. /today: Command filter + HTML escaping (items 5, 6)
# --------------------------------------------------------------------------
async def test_today_command_filter_matches_only_slash_command() -> None:
    def tg_msg(text: str) -> TgMessage:
        return TgMessage(
            message_id=1,
            date=datetime.now(UTC),
            chat=TgChat(id=111, type=ChatType.PRIVATE),
            from_user=TgUser(id=111, is_bot=False, first_name="T"),
            text=text,
        )

    f = Command("today")
    assert await f(message=tg_msg("/today"), bot=None)  # type: ignore[arg-type]
    assert not await f(message=tg_msg(BTN_TODAY), bot=None)  # type: ignore[arg-type]


async def test_today_shows_empty_snapshot_then_saved_entry_with_escaped_text(
    app_runtime, session_factory
) -> None:
    msg = user_message(111, "/today")
    await daily.cmd_today(msg)
    # Unified v1.1 snapshot: both neutral empty states, no error.
    assert texts.MORNING_RECORDED_EMPTY in msg.answers[-1]
    assert texts.EVENING_MISSING in msg.answers[-1]

    # Fill via the full flow (skip button), then re-check.
    state = FakeState()
    await _run_score_steps(state)
    await daily.ask_reflection_text(callback("ci:ref:yes", message=bot_message()), state)
    await daily.submit_reflection_text(user_message(111, "Сегодня <нормально> & спокойно"), state)

    msg2 = user_message(111, "/today")
    await daily.cmd_today(msg2)
    rendered = msg2.answers[-1]
    assert "Сегодня &lt;нормально&gt; &amp; спокойно" in rendered
    assert "<нормально>" not in rendered


# --------------------------------------------------------------------------
# 4. Weekly flow: callback identity (item 4)
# --------------------------------------------------------------------------
async def test_weekly_flow_with_final_skip_saves_for_callback_user(
    app_runtime, session_factory
) -> None:
    state = FakeState()
    await weekly.start_weekly(callback("wk:start", message=bot_message()), state)
    await weekly.skip_q1(callback("wk:q1:skip", message=bot_message()), state)
    await weekly.ans_q2(user_message(111, "Созвон"), state)
    done_target = bot_message()
    await weekly.skip_q3(callback("wk:q3:skip", user_id=111, message=done_target), state)

    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(111)  # not the bot
        assert user is not None
        wr = s.query(WeeklyReflection).filter_by(user_id=user.id).one()
        assert wr.best_event is None
        assert wr.energy_drainer == "Созвон"
        assert wr.want_more is None
        assert s.query(User).filter_by(telegram_user_id=BOT_TG_ID).one_or_none() is None
    assert state.cleared == 1
    assert texts.WEEKLY_DONE in done_target.answers


async def test_weekly_flow_with_final_text_answer_saves(app_runtime, session_factory) -> None:
    state = FakeState()
    await weekly.start_weekly(callback("wk:start", message=bot_message()), state)
    await weekly.ans_q1(user_message(111, "Прогулка"), state)
    await weekly.ans_q2(user_message(111, "Митинги"), state)
    last = user_message(111, "Сна")
    await weekly.ans_q3(last, state)

    with session_scope(session_factory) as s:
        user = UserRepository(s).get_by_telegram_id(111)
        wr = s.query(WeeklyReflection).filter_by(user_id=user.id).one()
        assert (wr.best_event, wr.energy_drainer, wr.want_more) == ("Прогулка", "Митинги", "Сна")
    assert texts.WEEKLY_DONE in last.answers


# --------------------------------------------------------------------------
# 5. Private-chat restriction (item 7)
# --------------------------------------------------------------------------
def _tg_message(tg_id: int, chat_type: ChatType, bot: bool = False) -> TgMessage:
    return TgMessage(
        message_id=1,
        date=datetime.now(UTC),
        chat=TgChat(id=-100 if chat_type != ChatType.PRIVATE else tg_id, type=chat_type),
        from_user=TgUser(id=tg_id, is_bot=bot, first_name="T"),
        text="/today",
    )


async def test_private_chat_filter_blocks_group_message_and_callback() -> None:
    f = PrivateChatFilter()
    assert await f(event=_tg_message(111, ChatType.PRIVATE))
    assert not await f(event=_tg_message(111, ChatType.GROUP))
    assert not await f(event=_tg_message(111, ChatType.SUPERGROUP))

    group = _tg_message(111, ChatType.GROUP)
    cb = CallbackQuery(id="1", from_user=group.from_user, message=group, chat_instance="ci")
    assert not await f(event=cb)
    private = _tg_message(111, ChatType.PRIVATE)
    cb_ok = CallbackQuery(id="2", from_user=private.from_user, message=private, chat_instance="ci")
    assert await f(event=cb_ok)
