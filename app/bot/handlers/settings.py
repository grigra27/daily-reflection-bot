"""Settings management: prompt times, timezone (section 19) and, since v1.3, the
food prompt times, the Sunday weight time and the food rule set.

Values are validated in the service layer; an invalid time or unknown timezone
is never persisted. On a successful change the user's scheduler jobs are
resynced to the new local time.
"""

from __future__ import annotations

from collections.abc import Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.states import SettingsStates
from app.database.models import User
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import food_service, settings_service
from app.services.auth_service import authorize
from app.services.food_service import FoodValidationError
from app.services.settings_service import InvalidSettingError

router = Router(name="settings")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())


async def _render_settings(message: Message) -> None:
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, message.from_user.id)
        text = texts.settings_message(
            user.morning_time, user.checkin_time, user.reminder_time, user.timezone
        ) + texts.food_settings_block(
            user.food_morning_time,
            user.food_evening_time,
            user.weight_time,
            len(food_service.active_rules(user)),
        )
    await message.answer(text, reply_markup=keyboards.settings_keyboard())


@router.message(Command("settings"))
@router.message(F.text == keyboards.reply.BTN_SETTINGS)
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _render_settings(message)


@router.callback_query(F.data == "se:morning")
async def ask_morning(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_morning_time)
    await cb.message.answer(texts.ASK_MORNING_TIME)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:checkin")
async def ask_checkin(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_checkin_time)
    await cb.message.answer(texts.ASK_CHECKIN_TIME)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:reminder")
async def ask_reminder(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_reminder_time)
    await cb.message.answer(texts.ASK_REMINDER_TIME)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:tz")
async def ask_tz(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_timezone)
    await cb.message.answer(texts.ASK_TIMEZONE)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:fdm")
async def ask_food_morning(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_food_morning_time)
    await cb.message.answer(texts.ASK_FOOD_MORNING_TIME)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:fde")
async def ask_food_evening(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_food_evening_time)
    await cb.message.answer(texts.ASK_FOOD_EVENING_TIME)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:wt")
async def ask_weight_time(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(SettingsStates.waiting_weight_time)
    await cb.message.answer(texts.ASK_WEIGHT_TIME)  # type: ignore[union-attr]


@router.callback_query(F.data == "se:fr")
async def show_food_rules(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        markup = keyboards.food_rules_settings_keyboard(
            food_service.active_rules(user), food_service.RULE_CODES
        )
    await cb.message.answer(texts.FOOD_RULES_SETTINGS, reply_markup=markup)  # type: ignore[union-attr]


@router.callback_query(F.data.startswith("se:fr:"))
async def toggle_food_rule(cb: CallbackQuery) -> None:
    code = (cb.data or "").split(":", 2)[2]
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            # Identity from cb.from_user: cb.message was sent by the bot.
            user = authorize(session, runtime.settings, cb.from_user.id)
            enabled = code not in food_service.active_rules(user)
            active = food_service.set_rule_enabled(session, user, code, enabled)
    except FoodValidationError:
        await cb.answer(texts.FOOD_LAST_RULE, show_alert=True)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_text(
            texts.FOOD_RULES_SETTINGS,
            reply_markup=keyboards.food_rules_settings_keyboard(active, food_service.RULE_CODES),
        )


async def _apply_entry(
    message: Message,
    state: FSMContext,
    setter: Callable[[Session, User, str], User],
    invalid_text: str,
) -> None:
    runtime = get_runtime()
    value = (message.text or "").strip()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, message.from_user.id)
            setter(session, user, value)
            user_pk = user.id
    except InvalidSettingError:
        await state.clear()
        await message.answer(invalid_text)
        return

    await state.clear()
    if runtime.scheduler is not None:
        runtime.scheduler.reschedule_user(user_pk)
    await _render_settings(message)


@router.message(SettingsStates.waiting_morning_time, F.text)
async def set_morning(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_morning_time, texts.INVALID_TIME)


@router.message(SettingsStates.waiting_checkin_time, F.text)
async def set_checkin(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_checkin_time, texts.INVALID_TIME)


@router.message(SettingsStates.waiting_reminder_time, F.text)
async def set_reminder(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_reminder_time, texts.INVALID_TIME)


@router.message(SettingsStates.waiting_timezone, F.text)
async def set_tz(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_timezone, texts.INVALID_TZ)


@router.message(SettingsStates.waiting_food_morning_time, F.text)
async def set_food_morning(message: Message, state: FSMContext) -> None:
    await _apply_entry(
        message, state, settings_service.set_food_morning_time, texts.INVALID_TIME
    )


@router.message(SettingsStates.waiting_food_evening_time, F.text)
async def set_food_evening(message: Message, state: FSMContext) -> None:
    await _apply_entry(
        message, state, settings_service.set_food_evening_time, texts.INVALID_TIME
    )


@router.message(SettingsStates.waiting_weight_time, F.text)
async def set_weight_time(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_weight_time, texts.INVALID_TIME)
