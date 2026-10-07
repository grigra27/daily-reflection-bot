"""Vitamin acknowledgement handler (v1.3.2).

A tap on the daily question's ✅ Да is fully stateless: the Reflection Day is
read from the button itself, never recomputed from wall-clock time, so the
confirmation lands on the evening the question was asked for — hours later,
after midnight, or after a restart. No FSM and no database row exist for a day
the user never confirmed.
"""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot import texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import vitamin_service
from app.services.auth_service import authorize
from app.services.vitamin_service import VitaminDateError

router = Router(name="vitamins")
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())


@router.callback_query(F.data.startswith("vit:yes:"))
async def cb_confirm(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    target_date: date | None = None
    if len(parts) == 3:
        try:
            target_date = date.fromisoformat(parts[2])
        except ValueError:
            target_date = None
    if target_date is None:
        await cb.answer(texts.VITAMIN_STALE, show_alert=True)
        return
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            # Identity from cb.from_user: the reminder message was sent by the bot.
            user = authorize(session, runtime.settings, cb.from_user.id)
            vitamin_service.mark_taken(session, user, target_date)
    except VitaminDateError:
        await cb.answer(texts.VITAMIN_STALE, show_alert=True)
        return
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_text(texts.VITAMIN_TAKEN, reply_markup=None)
