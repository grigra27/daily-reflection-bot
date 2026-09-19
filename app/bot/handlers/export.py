"""CSV export handler (baseline sections 23-24)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot import keyboards
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import export_service
from app.services.auth_service import authorize
from app.services.time_service import user_today

router = Router(name="export")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())


@router.message(Command("export"))
@router.message(F.text == keyboards.reply.BTN_EXPORT)
async def cmd_export(message: Message) -> None:
    await message.answer("За какой период выгрузить данные?", reply_markup=keyboards.export_keyboard())


@router.callback_query(F.data.startswith("ex:"))
async def cb_export(cb: CallbackQuery) -> None:
    scope = cb.data.split(":")[1]  # type: ignore[union-attr]
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        today = user_today(user.timezone)
        filename, data = export_service.export_user_csv(session, user, scope=scope, today=today)

    await cb.answer()
    if cb.message is None:
        return
    await cb.message.answer("Готовлю файл…")
    await cb.message.answer_document(
        document=BufferedInputFile(data, filename=filename),
        caption="Твои данные в CSV (UTF-8).",
    )
