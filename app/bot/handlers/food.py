"""Food reflection handlers (v1.3): morning focus, evening checklist, triggers,
/food status + statistics, and the weekly weight.

The day flow is stateless: every button carries its Reflection Day and every
tap is persisted immediately, so a restart or an old message never loses or
misroutes an answer. Only the weight entry after /weight uses the FSM; a bare
number sent outside any flow is also accepted as this week's weight, which is
what the Sunday prompt asks for.
"""

from __future__ import annotations

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import food_flow, keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.states import WeightStates
from app.database.models import User
from app.database.repositories import (
    DailyEntryRepository,
    FoodDayRepository,
    WeightLogRepository,
)
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import food_service
from app.services.auth_service import authorize
from app.services.food_service import (
    FoodDateError,
    FoodDayClosedError,
    FoodValidationError,
)
from app.services.time_service import reflection_day

router = Router(name="food")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())

_DEFAULT_PERIOD = 30
#: How far back the streak looks; far beyond any realistic streak break.
_STREAK_LOOKBACK_DAYS = 400
_WEIGHT_PATTERN = r"^\s*\d{2,3}([.,]\d{1,2})?\s*(кг|kg)?\s*$"


def _parse(cb: CallbackQuery) -> tuple[list[str], date | None]:
    """Split ``fd:<action>[:<code>]:<date>``; a malformed date reads as None."""
    parts = (cb.data or "").split(":")
    try:
        return parts, date.fromisoformat(parts[-1])
    except ValueError:
        return parts, None


def _streak(session, user: User, today: date) -> int:
    days = FoodDayRepository(session).list_in_range(
        user.id, today - timedelta(days=_STREAK_LOOKBACK_DAYS), today
    )
    return food_service.compute_streak(days, today)


async def _refuse(cb: CallbackQuery, exc: Exception) -> None:
    if isinstance(exc, FoodDayClosedError):
        text = texts.FOOD_DAY_CLOSED
    elif isinstance(exc, FoodDateError):
        text = texts.FOOD_DATE_REFUSED
    else:
        text = texts.FOOD_RULE_DISABLED
    await cb.answer(text, show_alert=True)


# --------------------------------------------------------------------------
# Morning focus
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("fd:f:"))
async def cb_focus(cb: CallbackQuery) -> None:
    parts, target_date = _parse(cb)
    if target_date is None or len(parts) != 4:
        await cb.answer(texts.FOOD_DATE_REFUSED, show_alert=True)
        return
    code = None if parts[2] == "none" else parts[2]
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            # Identity from cb.from_user: cb.message was sent by the bot.
            user = authorize(session, runtime.settings, cb.from_user.id)
            day = food_service.set_focus(session, user, target_date, code)
            focus = day.focus_rule
    except (FoodDateError, FoodDayClosedError, FoodValidationError) as exc:
        await _refuse(cb, exc)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_text(texts.food_focus_saved(focus))


@router.callback_query(F.data.startswith("fd:focus:"))
async def cb_focus_prompt(cb: CallbackQuery) -> None:
    _parts, target_date = _parse(cb)
    await cb.answer()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        screen = food_flow.focus_prompt(user, target_date or reflection_day(user.timezone))
    if cb.message is not None:
        await cb.message.answer(screen.text, reply_markup=screen.markup)


# --------------------------------------------------------------------------
# Evening checklist
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("fd:t:"))
async def cb_toggle(cb: CallbackQuery) -> None:
    parts, target_date = _parse(cb)
    if target_date is None or len(parts) != 4:
        await cb.answer(texts.FOOD_DATE_REFUSED, show_alert=True)
        return
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, cb.from_user.id)
            day = food_service.toggle_violation(session, user, target_date, parts[2])
            screen = food_flow.checklist(user, day, target_date, reflection_day(user.timezone))
    except (FoodDateError, FoodDayClosedError, FoodValidationError) as exc:
        await _refuse(cb, exc)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_text(screen.text, reply_markup=screen.markup)


@router.callback_query(F.data.startswith("fd:ok:"))
async def cb_submit(cb: CallbackQuery) -> None:
    _parts, target_date = _parse(cb)
    if target_date is None:
        await cb.answer(texts.FOOD_DATE_REFUSED, show_alert=True)
        return
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, cb.from_user.id)
            day = food_service.submit_day(session, user, target_date)
            today = reflection_day(user.timezone)
            if food_service.violations(day):
                screen = food_flow.triggers(day, target_date, today)
            else:
                screen = food_flow.summary(
                    day, target_date, today, streak=_streak(session, user, today)
                )
    except (FoodDateError, FoodValidationError) as exc:
        await _refuse(cb, exc)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_text(screen.text, reply_markup=screen.markup)


@router.callback_query(F.data.startswith("fd:g:"))
async def cb_trigger(cb: CallbackQuery) -> None:
    parts, target_date = _parse(cb)
    if target_date is None or len(parts) != 4:
        await cb.answer(texts.FOOD_DATE_REFUSED, show_alert=True)
        return
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, cb.from_user.id)
            day = food_service.toggle_trigger(session, user, target_date, parts[2])
            screen = food_flow.triggers(day, target_date, reflection_day(user.timezone))
    except (FoodDateError, FoodValidationError) as exc:
        await _refuse(cb, exc)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_text(screen.text, reply_markup=screen.markup)


@router.callback_query(F.data.startswith("fd:gd:"))
async def cb_triggers_done(cb: CallbackQuery) -> None:
    _parts, target_date = _parse(cb)
    await cb.answer()
    if target_date is None:
        return
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        day = food_service.get_day(session, user, target_date)
        if day is None or not food_service.is_completed(day):
            return
        screen = food_flow.summary(day, target_date, reflection_day(user.timezone))
    if cb.message is not None:
        await cb.message.edit_text(screen.text)


@router.callback_query(F.data.startswith("fd:open:"))
async def cb_open(cb: CallbackQuery) -> None:
    """/food → fill or edit: an explicit edit reopens a submitted day."""
    _parts, target_date = _parse(cb)
    if target_date is None:
        await cb.answer(texts.FOOD_DATE_REFUSED, show_alert=True)
        return
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, cb.from_user.id)
            food_service.check_date(user, target_date)
            day = food_service.get_day(session, user, target_date)
            if food_service.is_completed(day):
                day = food_service.reopen_day(session, user, target_date)
            screen = food_flow.checklist(user, day, target_date, reflection_day(user.timezone))
    except FoodDateError as exc:
        await _refuse(cb, exc)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.answer(screen.text, reply_markup=screen.markup)


# --------------------------------------------------------------------------
# /food: today's status + statistics
# --------------------------------------------------------------------------
def _build_food_view(telegram_user_id: int, period_days: int):
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        today = reflection_day(user.timezone)
        days = FoodDayRepository(session).list_in_range(
            user.id, today - timedelta(days=_STREAK_LOOKBACK_DAYS), today
        )
        entries = DailyEntryRepository(session).list_in_range(
            user.id, today - timedelta(days=period_days - 1), today
        )
        weights = WeightLogRepository(session).list_all(user.id)
        stats = food_service.compute_food_stats(
            days, entries, weights, period_days=period_days, today=today
        )
        day = next((d for d in days if d.food_date == today), None)
        completed = food_service.is_completed(day)
        status = texts.food_status_block(
            today,
            day.focus_rule if day else None,
            food_service.focus_answered(day),
            completed,
            len(food_service.violations(day)),
            len(day.results) if completed and day else 0,
        )
        markup = keyboards.food_status_keyboard(
            today, completed=completed, can_set_focus=not completed, active=period_days
        )
    return f"{status}\n\n{texts.food_stats_message(stats)}", markup


@router.message(Command("food"))
@router.message(F.text == keyboards.reply.BTN_FOOD)
async def cmd_food(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = _build_food_view(message.from_user.id, _DEFAULT_PERIOD)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("fd:st:"))
async def cb_food_stats(cb: CallbackQuery) -> None:
    try:
        period = int((cb.data or "").split(":")[2])
    except (IndexError, ValueError):
        period = _DEFAULT_PERIOD
    period = period if period in (7, 30, 90) else _DEFAULT_PERIOD
    text, markup = _build_food_view(cb.from_user.id, period)
    await cb.answer()
    if cb.message is not None:
        try:
            await cb.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            # Re-tapping the active period leaves the message unchanged, which
            # Telegram reports as an error; there is nothing to update.
            pass


# --------------------------------------------------------------------------
# Weight
# --------------------------------------------------------------------------
async def _save_weight(message: Message, state: FSMContext) -> None:
    runtime = get_runtime()
    try:
        value = food_service.parse_weight(message.text or "")
    except FoodValidationError:
        await message.answer(texts.WEIGHT_INVALID)
        return
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, message.from_user.id)
        log = food_service.save_weight(session, user, value)
        logs = WeightLogRepository(session).list_all(user.id)
        prev = food_service.previous_weight(logs, log.week_start_date)
        change = round(value - prev.weight_kg, 1) if prev else None
    await state.clear()
    await message.answer(texts.weight_saved_message(value, change))


@router.message(Command("weight"))
async def cmd_weight(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(WeightStates.waiting_weight)
    await message.answer(texts.WEIGHT_ASK)


@router.message(WeightStates.waiting_weight, F.text & ~F.text.startswith("/"))
async def submit_weight(message: Message, state: FSMContext) -> None:
    await _save_weight(message, state)


@router.message(StateFilter(None), F.text.regexp(_WEIGHT_PATTERN))
async def bare_weight(message: Message, state: FSMContext) -> None:
    """The Sunday prompt asks for "just a number": outside any flow, a message
    that looks like a weight is recorded as this week's weight."""
    await _save_weight(message, state)
