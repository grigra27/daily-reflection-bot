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
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.states import CheckinStates
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import checkin_service, morning_service, weekly_service
from app.services.auth_service import authorize
from app.services.time_service import user_today

router = Router(name="daily")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())


# --------------------------------------------------------------------------
# Starting the check-in
# --------------------------------------------------------------------------
def _evening_header(session, user) -> str:
    """First evening question with morning context — one renderer for the
    scheduled prompt (via notifications.send_checkin_prompt), /checkin, the
    main menu and editing (all go through ``texts.evening_header_for``)."""
    return texts.evening_header_for(morning_service.get_intent(session, user))


async def _handle_checkin_request(
    message: Message,
    state: FSMContext,
    *,
    telegram_user_id: int,
    allow_edit: bool = False,
) -> None:
    """/checkin, the menu button and inline check-in/edit callbacks: an
    explicit start clears any stale FSM flow first (saved DB data is never
    touched); if today is already filled, offer view/edit instead of silently
    restarting (baseline section 14). ``act:edit`` passes allow_edit — it *is*
    the edit choice from that menu.

    ``message`` is only the Telegram target to reply into; identity always
    comes from the caller: ``message.from_user.id`` for messages,
    ``cb.from_user.id`` for callbacks (a callback's message was authored by the
    bot, so its ``from_user`` is the bot, never the person tapping the button).
    """
    await state.clear()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        already_done = checkin_service.has_entry_today(session, user)
        header = None if already_done and not allow_edit else _evening_header(session, user)
    if header is None:
        await message.answer(texts.ALREADY_FILLED, reply_markup=keyboards.filled_choice_keyboard())
    else:
        await message.answer(header, reply_markup=keyboards.day_keyboard())


@router.message(F.text == keyboards.reply.BTN_CHECKIN)
async def menu_checkin(message: Message, state: FSMContext) -> None:
    await _handle_checkin_request(message, state, telegram_user_id=message.from_user.id)


@router.message(F.text == keyboards.reply.BTN_TODAY)
async def menu_today(message: Message) -> None:
    await _show_today(message, message.from_user.id)


@router.message(Command("checkin"))
async def cmd_checkin(message: Message, state: FSMContext) -> None:
    await _handle_checkin_request(message, state, telegram_user_id=message.from_user.id)


@router.callback_query(F.data == "act:checkin")
@router.callback_query(F.data == "act:edit")
async def cb_start_checkin(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.message:
        await _handle_checkin_request(
            cb.message,
            state,
            # Identity from cb.from_user: cb.message is bot-authored.
            telegram_user_id=cb.from_user.id,
            allow_edit=cb.data == "act:edit",
        )


@router.callback_query(F.data == "act:today")
async def cb_show_today(cb: CallbackQuery) -> None:
    await cb.answer()
    if cb.message:
        await _show_today(cb.message, cb.from_user.id)


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
    # Identity comes from cb.from_user: cb.message was sent by the bot, so its
    # from_user is the bot itself, never the person tapping the button.
    await _finalize(
        cb.message, state, reflection_text=None, telegram_user_id=cb.from_user.id, edit_target=True
    )


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
    await _finalize(
        cb.message, state, reflection_text=None, telegram_user_id=cb.from_user.id, edit_target=True
    )


@router.message(
    CheckinStates.waiting_reflection_text, F.text & ~F.text.startswith("/")
)
async def submit_reflection_text(message: Message, state: FSMContext) -> None:
    await _finalize(
        message, state, reflection_text=message.text, telegram_user_id=message.from_user.id,
        edit_target=False,
    )


# --------------------------------------------------------------------------
# Finalisation
# --------------------------------------------------------------------------
async def _finalize(
    target: Message | None,
    state: FSMContext,
    reflection_text: str | None,
    telegram_user_id: int,
    edit_target: bool,
) -> None:
    data = await state.get_data()
    day = data.get("day_score")
    mood = data.get("mood_score")
    energy = data.get("energy_score")
    if day is None or mood is None or energy is None:
        await state.clear()
        return

    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        entry = checkin_service.save_daily_entry(
            session,
            user,
            day_score=day,
            mood_score=mood,
            energy_score=energy,
            reflection_text=reflection_text,
        )
        offer_weekly = weekly_service.should_offer_weekly(session, user)

    # FSM is cleared only after the entry is durably saved; a DB/auth failure
    # leaves the answers in place instead of silently discarding them.
    await state.clear()
    if target is None:
        return
    done = texts.done_message(entry.day_score, entry.mood_score, entry.energy_score)
    if edit_target:
        # Callback flow: the target is the bot's own message and can be edited.
        await target.edit_text(done)
    else:
        # Text flow: the target is the user's message — the Telegram API
        # cannot edit it, so the confirmation goes out as a new message.
        await target.answer(done)
    if offer_weekly:
        await target.answer(texts.WEEKLY_OFFER, reply_markup=keyboards.weekly_offer_keyboard())


# --------------------------------------------------------------------------
# /today — unified morning + evening snapshot
# --------------------------------------------------------------------------
async def _show_today(message: Message, telegram_user_id: int) -> None:
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        entry = checkin_service.get_entry(session, user)
        intent = morning_service.get_intent(session, user)

    await message.answer(
        texts.today_message(
            user_today(user.timezone),
            entry.day_score if entry else None,
            entry.mood_score if entry else None,
            entry.energy_score if entry else None,
            entry.reflection_text if entry else None,
            morning_main=intent.main_intention if intent else None,
            morning_secondary=intent.secondary_intention if intent else None,
        ),
        reply_markup=keyboards.today_actions_keyboard(
            has_morning=intent is not None, has_evening=entry is not None
        ),
    )


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    await _show_today(message, message.from_user.id)
