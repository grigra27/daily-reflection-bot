"""Daily check-in FSM, /today and /checkin (baseline sections 5-14, 30).

Handlers stay thin: they drive the FSM and Telegram UI, while persistence,
validation and "today" computation live in the service layer.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter
from app.bot.states import CheckinStates
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import checkin_service, weekly_service
from app.services.auth_service import authorize

router = Router(name="daily")
router.message.filter(AuthorizedFilter())
router.callback_query.filter(AuthorizedFilter())


# --------------------------------------------------------------------------
# Starting the check-in
# --------------------------------------------------------------------------
async def _begin_checkin(message: Message) -> None:
    await message.answer(texts.CHECKIN_HEADER, reply_markup=keyboards.day_keyboard())


async def _handle_checkin_request(message: Message) -> None:
    """/checkin and the menu button: if today is already filled, offer view/edit
    instead of silently restarting (baseline section 14)."""
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, message.from_user.id)
        already_done = checkin_service.has_entry_today(session, user)
    if already_done:
        await message.answer(texts.ALREADY_FILLED, reply_markup=keyboards.filled_choice_keyboard())
    else:
        await _begin_checkin(message)


@router.message(F.text == keyboards.reply.BTN_CHECKIN)
async def menu_checkin(message: Message) -> None:
    await _handle_checkin_request(message)


@router.message(F.text == keyboards.reply.BTN_TODAY)
async def menu_today(message: Message) -> None:
    await _show_today(message)


@router.message(Command("checkin"))
async def cmd_checkin(message: Message) -> None:
    await _handle_checkin_request(message)


@router.callback_query(F.data == "act:checkin")
@router.callback_query(F.data == "act:edit")
async def cb_start_checkin(cb: CallbackQuery) -> None:
    await cb.answer()
    if cb.message:
        await _begin_checkin(cb.message)


@router.callback_query(F.data == "act:today")
async def cb_show_today(cb: CallbackQuery) -> None:
    await cb.answer()
    if cb.message:
        await _show_today(cb.message)


# --------------------------------------------------------------------------
# Score steps (day -> mood -> energy -> reflection)
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("ci:day:"))
async def step_day(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    value = int(cb.data.split(":")[2])  # type: ignore[union-attr]
    await state.update_data(day_score=value)
    await state.set_state(CheckinStates.waiting_mood)
    if cb.message:
        await cb.message.edit_text(texts.Q_MOOD, reply_markup=keyboards.mood_keyboard())


@router.callback_query(CheckinStates.waiting_mood, F.data.startswith("ci:mood:"))
async def step_mood(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.update_data(mood_score=int(cb.data.split(":")[2]))  # type: ignore[union-attr]
    await state.set_state(CheckinStates.waiting_energy)
    if cb.message:
        await cb.message.edit_text(texts.Q_ENERGY, reply_markup=keyboards.energy_keyboard())


@router.callback_query(CheckinStates.waiting_energy, F.data.startswith("ci:energy:"))
async def step_energy(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.update_data(energy_score=int(cb.data.split(":")[2]))  # type: ignore[union-attr]
    await state.set_state(CheckinStates.ask_reflection)
    if cb.message:
        await cb.message.edit_text(
            texts.Q_REFLECTION_CHOICE, reply_markup=keyboards.reflection_choice_keyboard()
        )


@router.callback_query(CheckinStates.ask_reflection, F.data == "ci:ref:no")
async def skip_reflection(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _finalize(cb.message, state, reflection_text=None)


@router.callback_query(CheckinStates.ask_reflection, F.data == "ci:ref:yes")
async def ask_reflection_text(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(CheckinStates.waiting_reflection_text)
    if cb.message:
        await cb.message.edit_text(
            texts.Q_REFLECTION_TEXT, reply_markup=keyboards.skip_keyboard("ci:reftext")
        )


@router.callback_query(
    CheckinStates.waiting_reflection_text, F.data == "ci:reftext:skip"
)
async def skip_reflection_text(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _finalize(cb.message, state, reflection_text=None)


@router.message(
    CheckinStates.waiting_reflection_text, F.text & ~F.text.startswith("/")
)
async def submit_reflection_text(message: Message, state: FSMContext) -> None:
    await _finalize(message, state, reflection_text=message.text)


# --------------------------------------------------------------------------
# Finalisation
# --------------------------------------------------------------------------
async def _finalize(target: Message | None, state: FSMContext, reflection_text: str | None) -> None:
    data = await state.get_data()
    day = data.get("day_score")
    mood = data.get("mood_score")
    energy = data.get("energy_score")
    await state.clear()
    if target is None or day is None or mood is None or energy is None:
        return

    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, _author_id(target))
        entry = checkin_service.save_daily_entry(
            session,
            user,
            day_score=day,
            mood_score=mood,
            energy_score=energy,
            reflection_text=reflection_text,
        )
        offer_weekly = weekly_service.should_offer_weekly(session, user)

    await target.edit_text(texts.done_message(entry.day_score, entry.mood_score, entry.energy_score))
    if offer_weekly:
        await target.answer(texts.WEEKLY_OFFER, reply_markup=keyboards.weekly_offer_keyboard())


def _author_id(message: Message) -> int:
    assert message.from_user is not None
    return message.from_user.id


# --------------------------------------------------------------------------
# /today
# --------------------------------------------------------------------------
async def _show_today(message: Message) -> None:
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, message.from_user.id)
        entry = checkin_service.get_entry(session, user)

    if entry is None:
        await message.answer(texts.NO_ENTRY_TODAY, reply_markup=keyboards.today_fill_keyboard())
        return
    await message.answer(
        texts.today_message(entry.entry_date, entry.day_score, entry.mood_score,
                             entry.energy_score, entry.reflection_text),
        reply_markup=keyboards.today_edit_keyboard(),
    )


@router.message(F.command == "today")
async def cmd_today(message: Message) -> None:
    await _show_today(message)
