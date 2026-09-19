"""Welcome, main menu, /help, and the private-bot notice for strangers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter, display_name, unauthorized_filter
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services.auth_service import authorize

router = Router(name="start")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())


async def send_welcome(message: Message) -> None:
    await message.answer(texts.START, reply_markup=keyboards.reply.main_menu())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    # Onboarding must be idempotent: create/refresh the user and (re)register
    # their scheduler jobs before welcoming, so a first-ever /start after a
    # cold scheduler sync still gets daily check-ins without an app restart.
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(
            session,
            runtime.settings,
            message.from_user.id,
            display_name=display_name(message),
        )
        user_pk = user.id
    if runtime.scheduler is not None:
        runtime.scheduler.reschedule_user(user_pk)
    await send_welcome(message)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


# --------------------------------------------------------------------------
# Unauthorized access: reply privately once and expose nothing.
# --------------------------------------------------------------------------
unauthorized_router = Router(name="unauthorized")
unauthorized_router.message.filter(unauthorized_filter())
unauthorized_router.callback_query.filter(unauthorized_filter())


@unauthorized_router.callback_query()
async def deny_callback(cb: CallbackQuery) -> None:
    await cb.answer(texts.NOT_PRIVATE, show_alert=True)


@unauthorized_router.message()
async def deny_message(message: Message) -> None:
    await message.answer(texts.NOT_PRIVATE)
