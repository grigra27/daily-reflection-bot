"""Weekly reflection flow (baseline sections 25-26).

Secondary scenario: it never blocks the daily check-in. Every answer is
optional and can be skipped.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.states import WeeklyStates
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import weekly_service
from app.services.auth_service import authorize

router = Router(name="weekly")
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())
router.message.filter(AuthorizedFilter(), PrivateChatFilter())

# (state, question text, next state, answer key)
_STEPS = [
    (WeeklyStates.waiting_best_event, texts.WEEKLY_Q1, WeeklyStates.waiting_energy_drainer, "best_event", "q1"),
    (
        WeeklyStates.waiting_energy_drainer,
        texts.WEEKLY_Q2,
        WeeklyStates.waiting_want_more,
        "energy_drainer",
        "q2",
    ),
    (
        WeeklyStates.waiting_want_more,
        texts.WEEKLY_Q3,
        None,
        "want_more",
        "q3",
    ),
]


async def _ask(message: Message, state: FSMContext, index: int) -> None:
    st, q, _next, _key, code = _STEPS[index]
    await state.set_state(st)
    await state.update_data(weekly_step=index)
    await message.answer(q, reply_markup=keyboards.skip_keyboard(f"wk:{code}"))


@router.callback_query(F.data == "wk:start")
async def start_weekly(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.message:
        await _ask(cb.message, state, 0)


@router.callback_query(F.data == "wk:no")
async def decline_weekly(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    if cb.message:
        await cb.message.edit_text("Хорошо, вернёмся в другой раз.")


async def _advance(
    message: Message, state: FSMContext, key: str, value: str | None, telegram_user_id: int
) -> None:
    data = await state.get_data()
    index = int(data.get("weekly_step", 0))
    await state.update_data(**{key: value})
    if index + 1 < len(_STEPS):
        await _ask(message, state, index + 1)
        return
    await _save(message, state, telegram_user_id)


async def _save(message: Message, state: FSMContext, telegram_user_id: int) -> None:
    data = await state.get_data()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        weekly_service.save_weekly(
            session,
            user,
            best_event=data.get("best_event"),
            energy_drainer=data.get("energy_drainer"),
            want_more=data.get("want_more"),
        )
    # Only drop the answers once the reflection is durably saved.
    await state.clear()
    await message.answer(texts.WEEKLY_DONE)


# Text answers
@router.message(WeeklyStates.waiting_best_event, F.text & ~F.text.startswith("/"))
async def ans_q1(message: Message, state: FSMContext) -> None:
    await _advance(message, state, "best_event", message.text, message.from_user.id)


@router.message(WeeklyStates.waiting_energy_drainer, F.text & ~F.text.startswith("/"))
async def ans_q2(message: Message, state: FSMContext) -> None:
    await _advance(message, state, "energy_drainer", message.text, message.from_user.id)


@router.message(WeeklyStates.waiting_want_more, F.text & ~F.text.startswith("/"))
async def ans_q3(message: Message, state: FSMContext) -> None:
    await _advance(message, state, "want_more", message.text, message.from_user.id)


# Skip answers. Identity is always cb.from_user — cb.message is the bot's own
# message, so cb.message.from_user would authorise the bot itself.
@router.callback_query(WeeklyStates.waiting_best_event, F.data == "wk:q1:skip")
async def skip_q1(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.message:
        await _advance(cb.message, state, "best_event", None, cb.from_user.id)


@router.callback_query(WeeklyStates.waiting_energy_drainer, F.data == "wk:q2:skip")
async def skip_q2(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.message:
        await _advance(cb.message, state, "energy_drainer", None, cb.from_user.id)


@router.callback_query(WeeklyStates.waiting_want_more, F.data == "wk:q3:skip")
async def skip_q3(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.message:
        await _advance(cb.message, state, "want_more", None, cb.from_user.id)
