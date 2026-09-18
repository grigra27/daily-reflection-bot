"""Settings management: check-in time, reminder time, timezone (section 19).

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
from app.bot.deps import AuthorizedFilter
from app.bot.states import SettingsStates
from app.database.models import User
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import settings_service
from app.services.auth_service import authorize
from app.services.settings_service import InvalidSettingError

router = Router(name="settings")
router.message.filter(AuthorizedFilter())
router.callback_query.filter(AuthorizedFilter())


async def _render_settings(message: Message) -> None:
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, message.from_user.id)
        text = texts.settings_message(user.checkin_time, user.reminder_time, user.timezone)
    await message.answer(text, reply_markup=keyboards.settings_keyboard())


@router.message(Command("settings"))
@router.message(F.text == keyboards.reply.BTN_SETTINGS)
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _render_settings(message)


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


@router.message(SettingsStates.waiting_checkin_time, F.text)
async def set_checkin(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_checkin_time, texts.INVALID_TIME)


@router.message(SettingsStates.waiting_reminder_time, F.text)
async def set_reminder(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_reminder_time, texts.INVALID_TIME)


@router.message(SettingsStates.waiting_timezone, F.text)
async def set_tz(message: Message, state: FSMContext) -> None:
    await _apply_entry(message, state, settings_service.set_timezone, texts.INVALID_TZ)
