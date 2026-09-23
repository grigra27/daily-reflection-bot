"""Morning intention FSM (v1.1), read-only once the evening closure starts (v1.2).

Two steps: main intention, then an optional secondary one. The main intention
is committed to the DB immediately after step 1 — if the process restarts
before secondary is answered, main is already persisted. Handlers stay thin:
validation and persistence live in ``morning_service``. Since v1.1.1 the
target Reflection Day is frozen into FSM data at flow start, so a flow that
crosses the 05:00 rollover mid-way still writes both steps to one row.

Since v1.2 the texts of a day whose evening closure has begun (or whose entry
is already filled) can no longer be changed: that would quietly rewrite
history under an outcome the user already answered. The lock is enforced in
the service, so blocking here is only about asking nicely.
"""

from __future__ import annotations

from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.states import MorningStates
from app.database.models import MorningIntent
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import morning_service
from app.services.auth_service import authorize
from app.services.morning_service import (
    MorningLock,
    MorningLockedError,
    MorningValidationError,
)
from app.services.time_service import reflection_day

router = Router(name="morning")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())


def _target_date_from_state(data: dict, user) -> date:
    """The Reflection Day frozen at flow start (v1.1.1). Falls back to the
    current reflection day for flows started before the upgrade."""
    frozen = data.get("target_date")
    if frozen:
        return date.fromisoformat(frozen)
    return reflection_day(user.timezone)


async def _ask_main(message: Message, state: FSMContext) -> None:
    await state.set_state(MorningStates.waiting_main)
    await message.answer(texts.Q_MAIN_INTENTION)


async def _ask_secondary(message: Message, state: FSMContext) -> None:
    await state.set_state(MorningStates.waiting_secondary)
    await message.answer(
        texts.Q_SECONDARY_INTENTION, reply_markup=keyboards.skip_keyboard("mrn:sec")
    )


async def _show_morning(message: Message, intent: MorningIntent, lock: MorningLock | None) -> None:
    """Repeat /morning shows the existing record instead of silently
    overwriting it. A locked (v1.2) record is shown without the ✏️ Изменить
    button, so the edit FSM is never entered and no new text can be offered."""
    record = texts.morning_record_message(intent)
    if lock is None:
        await message.answer(record, reply_markup=keyboards.morning_edit_keyboard())
    else:
        await message.answer(f"{record}\n\n{texts.morning_locked_message(lock)}")


async def _handle_morning_request(message: Message, state: FSMContext) -> None:
    """/morning and the menu button: explicit start clears any stale FSM flow
    first (already-saved DB data is never touched). The target Reflection Day
    is computed once (v1.1.1): the existing-record check and the flow both
    use that same logical date, and it is frozen into FSM data."""
    await state.clear()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, message.from_user.id)
        target_date = reflection_day(user.timezone)
        intent = morning_service.get_intent(session, user, intention_date=target_date)
        lock = morning_service.get_lock(session, user, intention_date=target_date)
    if intent is not None:
        await _show_morning(message, intent, lock)
    else:
        await state.update_data(target_date=target_date.isoformat())
        await _ask_main(message, state)


@router.message(Command("morning"))
async def cmd_morning(message: Message, state: FSMContext) -> None:
    await _handle_morning_request(message, state)


@router.message(F.text == keyboards.reply.BTN_MORNING)
async def menu_morning(message: Message, state: FSMContext) -> None:
    await _handle_morning_request(message, state)


@router.callback_query(F.data == "mrn:start")
@router.callback_query(F.data == "mrn:edit")
async def cb_start_morning(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    # Identity from cb.from_user: cb.message was sent by the bot.
    await state.clear()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        target_date = reflection_day(user.timezone)
        lock = morning_service.get_lock(session, user, intention_date=target_date)
    if lock is not None:
        # v1.2: even a stale or hand-replayed ✏️ Изменить tap cannot start an
        # edit for a day whose evening closure has already begun.
        if cb.message is not None:
            await cb.message.answer(texts.morning_locked_message(lock))
        return
    # Freeze the date before step 1 (v1.1.1): even if the flow crosses 05:00,
    # both steps land on the same MorningIntent row.
    await state.update_data(target_date=target_date.isoformat())
    if cb.message is not None:
        await _ask_main(cb.message, state)


@router.message(MorningStates.waiting_main, F.text & ~F.text.startswith("/"))
async def submit_main(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    if len(text) > morning_service.MAX_INTENTION_LENGTH:
        await message.answer(texts.INTENTION_TOO_LONG)
        return
    runtime = get_runtime()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, message.from_user.id)
            data = await state.get_data()
            morning_service.save_main_intention(
                session, user, text, intention_date=_target_date_from_state(data, user)
            )
    except MorningLockedError as exc:
        # The row was closed from the evening side while this flow was open.
        await state.clear()
        await message.answer(texts.morning_locked_message(exc.lock))
        return
    except MorningValidationError:
        await message.answer(texts.INTENTION_EMPTY)
        return
    # Only advance after the commit succeeded.
    await _ask_secondary(message, state)


@router.message(MorningStates.waiting_secondary, F.text & ~F.text.startswith("/"))
async def submit_secondary(message: Message, state: FSMContext) -> None:
    text = message.text or ""
    if len(text) > morning_service.MAX_INTENTION_LENGTH:
        await message.answer(texts.INTENTION_TOO_LONG)
        return
    if not text.strip():
        await _ask_secondary(message, state)
        return
    await _finish_secondary(message, state, text, telegram_user_id=message.from_user.id)


@router.callback_query(MorningStates.waiting_secondary, F.data == "mrn:sec:skip")
async def skip_secondary(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    # Skip means "no secondary" — in the edit flow this also clears the old value.
    if cb.message is None:
        await state.clear()
        return
    # Identity from cb.from_user: cb.message was sent by the bot.
    await _finish_secondary(
        cb.message, state, None, telegram_user_id=cb.from_user.id, edit_target=True
    )


async def _finish_secondary(
    target: Message,
    state: FSMContext,
    text: str | None,
    *,
    telegram_user_id: int,
    edit_target: bool = False,
) -> None:
    runtime = get_runtime()
    data = await state.get_data()
    try:
        with session_scope(runtime.session_factory) as session:
            user = authorize(session, runtime.settings, telegram_user_id)
            # Same frozen date as step 1 — never re-derived from the clock.
            intent = morning_service.set_secondary(
                session, user, text, intention_date=_target_date_from_state(data, user)
            )
    except MorningLockedError as exc:
        await state.clear()
        await target.answer(texts.morning_locked_message(exc.lock))
        return
    except MorningValidationError:
        # Only reachable when the row vanished (e.g. it was deleted between
        # the two steps) — restart the flow cleanly.
        await state.set_state(MorningStates.waiting_main)
        await target.answer(texts.Q_MAIN_INTENTION)
        return

    # FSM is cleared only after the row is durably saved.
    await state.clear()
    done = texts.morning_done_message(intent.main_intention, intent.secondary_intention)
    if edit_target:
        # Callback flow: the target is the bot's own message and can be edited.
        await target.edit_text(done)
    else:
        # Text flow: the target is the user's message — Telegram forbids
        # editing it, so the confirmation goes out as a new message.
        await target.answer(done)
