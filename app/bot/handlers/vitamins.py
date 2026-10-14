"""Vitamin handlers (v1.3.2 generic ack, v1.3.3 granular checklist).

A tap on the evening checklist is fully stateless: the Reflection Day is read from
the button itself, never recomputed from wall-clock time, so each confirmation
lands on the evening the question was asked for — hours later, after midnight, or
after a restart. Every item is written the moment it is tapped, so a partial
evening survives a restart, and no FSM state exists for a day the user never
confirmed.

The legacy ``vit:yes:<date>`` buttons that v1.3.2 already placed in users' chats
keep working: such a tap writes the old generic VitaminLog and never invents three
granular records out of a detail the user never stated.
"""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import vitamin_service
from app.services.auth_service import authorize
from app.services.vitamin_service import VitaminDateError, VitaminItemError

router = Router(name="vitamins")
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())

#: Telegram's own words for "this edit would not change the message".
_NOT_MODIFIED = "message is not modified"


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


async def _render(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """Re-render the reminder message, tolerating only Telegram's no-op complaint.

    Re-tapping an item that is already ✅ produces exactly the message already on
    screen, and Telegram reports that as "message is not modified" — the wanted
    state is on screen, so there is nothing left to do. Every other Telegram error
    still propagates.
    """
    if cb.message is None:
        return
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if _NOT_MODIFIED not in str(error):
            raise


@router.callback_query(F.data.startswith("vit:item:"))
async def cb_confirm_item(cb: CallbackQuery) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        await cb.answer(texts.VITAMIN_INVALID, show_alert=True)
        return
    item_code, target_date = parts[2], _parse_date(parts[3])
    if target_date is None:
        await cb.answer(texts.VITAMIN_INVALID, show_alert=True)
        return

    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            # Identity from cb.from_user: the reminder message was sent by the bot.
            user = authorize(session, runtime.settings, cb.from_user.id)
            # This item is stored at once; the checklist is an acknowledgement, not
            # a draft, so there is no "submit" and nothing to undo.
            vitamin_service.mark_item_taken(session, user, target_date, item_code)
            taken = vitamin_service.get_taken_items(session, user, target_date)
            complete = vitamin_service.all_items_taken(session, user, target_date)
    except VitaminDateError:
        await cb.answer(texts.VITAMIN_STALE, show_alert=True)
        return
    except VitaminItemError:
        await cb.answer(texts.VITAMIN_INVALID, show_alert=True)
        return

    await cb.answer()
    if complete:
        await _render(cb, texts.VITAMIN_ALL_TAKEN, None)
    else:
        markup = keyboards.vitamin_reminder_keyboard(target_date, taken)
        await _render(cb, texts.VITAMIN_REMINDER, markup)


@router.callback_query(F.data.startswith("vit:yes:"))
async def cb_confirm(cb: CallbackQuery) -> None:
    """Legacy v1.3.2 generic ✅ Да: one VitaminLog row, never three item rows."""
    parts = (cb.data or "").split(":")
    target_date: date | None = _parse_date(parts[2]) if len(parts) == 3 else None
    if target_date is None:
        await cb.answer(texts.VITAMIN_STALE, show_alert=True)
        return

    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, cb.from_user.id)
            vitamin_service.mark_taken(session, user, target_date)
    except VitaminDateError:
        await cb.answer(texts.VITAMIN_STALE, show_alert=True)
        return

    await cb.answer()
    await _render(cb, texts.VITAMIN_TAKEN, None)
