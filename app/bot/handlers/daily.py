"""Daily check-in FSM, /today and /checkin (baseline sections 5-14, 30).

Handlers stay thin: they drive the FSM and Telegram UI, while persistence,
validation and "today" computation live in the service layer. Since v1.2 the
evening opens by closing the morning intentions (``app.bot.evening_flow``
decides which question is first), and each outcome is saved the instant it is
tapped — long before the day entry itself exists.
"""

from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import evening_flow, keyboards, texts
from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.states import CheckinStates
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import checkin_service, morning_service, weekly_service
from app.services.auth_service import authorize
from app.services.time_service import reflection_day, week_start_date

router = Router(name="daily")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())

logger = logging.getLogger("app.daily")


# --------------------------------------------------------------------------
# Starting the check-in
# --------------------------------------------------------------------------
def _baked_date(parts: list[str], index: int) -> date | None:
    """Read the Reflection Day baked into callback data by the keyboard
    builder. Messages sent before v1.2 carry no date and return None, so the
    caller falls back to the v1.1.1 clock logic."""
    if len(parts) <= index:
        return None
    try:
        return date.fromisoformat(parts[index])
    except ValueError:
        logger.warning("Ignoring unparseable date in callback data")
        return None


def _target_date_from_state(data: dict, user) -> date:
    """The Reflection Day frozen at flow start (v1.1.1), so a flow crossing
    05:00 still saves to the day it began on. Falls back to the current
    reflection day for flows started before the upgrade (no frozen date in
    FSM data)."""
    frozen = data.get("target_date")
    if frozen:
        return date.fromisoformat(frozen)
    return reflection_day(user.timezone)


def _resolve_target_date(fsm_data: dict, parts: list[str], index: int, user) -> date:
    """The Reflection Day a button tap belongs to, used by every stateless tap
    (an outcome and the day score alike): the date frozen at flow start wins
    over the one baked into the button, because the button may be an old
    message from another day while the open flow is a conversation about
    exactly one day. Only when nothing is frozen (a scheduled prompt tapped
    after a restart wiped MemoryStorage) does the button itself decide, and
    with neither the v1.1.1 clock logic applies.
    """
    frozen = fsm_data.get("target_date")
    baked = _baked_date(parts, index)
    if frozen:
        target_date = date.fromisoformat(frozen)
        if baked is not None and baked != target_date:
            # Stale UI, not something to bother the user about; only the shape
            # of the problem is logged, never the callback itself.
            logger.info("Callback date disagrees with the frozen flow date; callback date ignored")
        return target_date
    return baked if baked is not None else reflection_day(user.timezone)


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

    The target Reflection Day is computed once here and frozen into FSM data
    (v1.1.1): the existing-entry check, the morning context and the final
    save all use that same date even if the clock crosses 05:00 mid-flow.

    Which question opens the evening is decided by the shared evening step
    rule (v1.2): outcomes still owed come first, and editing a filled day
    re-walks both of them from the main outcome so a mistapped answer can be
    corrected. That second behaviour is remembered in the FSM as ``edit_mode``:
    it is what lets a later tap overwrite an outcome of an already filled day,
    and it is deliberately *not* something a stale button can claim for itself.

    ``message`` is only the Telegram target to reply into; identity always
    comes from the caller: ``message.from_user.id`` for messages,
    ``cb.from_user.id`` for callbacks (a callback's message was authored by the
    bot, so its ``from_user`` is the bot, never the person tapping the button).
    """
    await state.clear()
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        target_date = reflection_day(user.timezone)
        already_done = (
            checkin_service.get_entry(session, user, entry_date=target_date) is not None
        )
        edit_mode = already_done and allow_edit
        prompt = (
            None
            if already_done and not allow_edit
            else evening_flow.build_evening_prompt(
                morning_service.get_intent(session, user, intention_date=target_date),
                target_date,
                edit_mode=edit_mode,
            )
        )
    if prompt is None:
        await message.answer(texts.ALREADY_FILLED, reply_markup=keyboards.filled_choice_keyboard())
    else:
        # Only freeze the date when the flow really starts.
        flow_data = {"target_date": target_date.isoformat()}
        if edit_mode:
            flow_data["edit_mode"] = True
        await state.update_data(**flow_data)
        await state.set_state(prompt.state)
        await message.answer(prompt.text, reply_markup=prompt.markup)


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
# Evening outcomes (v1.2): closing the morning intentions
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("ci:out:"))
async def step_outcome(cb: CallbackQuery, state: FSMContext) -> None:
    """``ci:out:<field>:<outcome>[:<date>]`` — save one morning intention's
    evening answer and move to whatever the day still owes.

    Three things make this handler unusual, and all three are product
    guarantees: the write is committed here, immediately, so a user who taps
    «Частично» and closes Telegram still has ``main_outcome = partial`` in the
    DB long before any DailyEntry exists; the target Reflection Day comes out
    of the callback when nothing froze it earlier, so the tap works with a
    completely empty FSM (a scheduled prompt that survived a restart); and
    identity is ``cb.from_user.id``, so one user's tap can never close another
    user's intention.

    Everything else about a tap is treated as suspicion, because inline buttons
    outlive the flow they belonged to: a day whose evening is already filled is
    only corrected by a flow that started as an edit, a tap the open flow never
    asked for is stale, and a date that disagrees with that flow loses to it.
    """
    await cb.answer()
    parts = (cb.data or "").split(":")
    if len(parts) < 4:
        logger.warning("Malformed outcome callback ignored")
        return
    outcome_field, outcome = parts[2], parts[3]
    data = await state.get_data()
    # Asked before the DB: answering an outcome the open flow never requested
    # would move the user's screen on to a question they never asked.
    if not evening_flow.outcome_tap_is_open(await state.get_state(), outcome_field):
        logger.info("Outcome callback the open flow did not ask for; ignored")
        return
    # Edit permission lives in the FSM, never in callback data: a button alone
    # cannot claim the right to rewrite a closed day.
    edit_mode = bool(data.get("edit_mode"))
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        target_date = _resolve_target_date(data, parts, 4, user)
        try:
            intent = morning_service.record_outcome(
                session,
                user,
                outcome_field=outcome_field,
                outcome=outcome,
                intention_date=target_date,
                allow_filled_day_edit=edit_mode,
            )
        except (
            morning_service.MorningValidationError,
            morning_service.MorningLockedError,
        ):
            # A stale or hand-crafted callback: an unknown value, a day with no
            # morning intention (or no secondary one) to close, or an evening
            # that is already filled and was not opened for editing. Nothing is
            # written and the ongoing flow is left alone.
            logger.info("Rejected outcome callback %s", cb.data)
            return
        prompt = evening_flow.build_evening_prompt(
            intent,
            target_date,
            edit_mode=edit_mode,
            after_outcome_field=outcome_field,
        )
    await state.update_data(target_date=target_date.isoformat())
    await state.set_state(prompt.state)
    if cb.message:
        await cb.message.edit_text(prompt.text, reply_markup=prompt.markup)


# --------------------------------------------------------------------------
# Score steps (day -> mood -> energy -> reflection)
# --------------------------------------------------------------------------
@router.callback_query(F.data.startswith("ci:day:"))
async def step_day(cb: CallbackQuery, state: FSMContext) -> None:
    """``ci:day:<score>[:<date>]`` — the first rating, and the only step that
    has no FSM state gating it, because the scheduled evening prompt offers it
    with an empty flow.

    Being stateless does not make it safe, though: this tap opens the part of
    the evening that ends in the day's ``DailyEntry``. A button left on screen
    from an evening that has since been closed elsewhere must not re-open it,
    so — exactly like an outcome tap — this one continues over a filled day only
    for a flow the user opened as an edit. Nothing is written here; the answers
    are only carried to finalisation, which is where the entry is saved.
    """
    await cb.answer()
    parts = (cb.data or "").split(":")
    value = int(parts[2])
    data = await state.get_data()
    edit_mode = bool(data.get("edit_mode"))
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, cb.from_user.id)
        target_date = _resolve_target_date(data, parts, 3, user)
        already_filled = checkin_service.get_entry(
            session, user, entry_date=target_date
        ) is not None
    if already_filled and not edit_mode:
        # The flow, its stored answers and the existing entry all stay exactly
        # as they are: the user has to choose to edit (spec 14).
        logger.info("Day tap on an already filled Reflection Day; ignored")
        return
    await state.update_data(target_date=target_date.isoformat(), day_score=value)
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
    """Save the completed evening and clear the flow.

    The last write of a flow is guarded the same way its first tap was, but
    checking only at the tap would not be enough: the day can be closed from
    another device, or by a scheduled prompt, while this flow sits between the
    day score and finalisation. So the target day is re-read here, immediately
    before the save, and a day that was filled in the meantime is left alone —
    these answers are the older, half-finished version of it, and overwriting
    would quietly discard whatever closed the day. Only a flow the user opened
    as an edit may rewrite a filled day."""
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
        entry_date = _target_date_from_state(data, user)
        existing = checkin_service.get_entry(session, user, entry_date=entry_date)
        if existing is not None and not data.get("edit_mode"):
            entry = None
        else:
            entry = checkin_service.save_daily_entry(
                session,
                user,
                day_score=day,
                mood_score=mood,
                energy_score=energy,
                reflection_text=reflection_text,
                entry_date=entry_date,
            )
            # Weekly semantics follow the day the entry was actually saved to
            # (v1.1.1): a Sunday flow finalized after the Monday 05:00 rollover
            # still gets the Sunday offer.
            offer_weekly = weekly_service.should_offer_weekly(
                session, user, today=entry.entry_date
            )
    if entry is None:
        # The day was closed while these answers were being collected, so the
        # flow is finished without writing: the user can still see the day and
        # choose to edit it deliberately.
        await state.clear()
        if target is not None:
            await target.answer(
                texts.ALREADY_FILLED, reply_markup=keyboards.filled_choice_keyboard()
            )
        return

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
        await target.answer(
            texts.WEEKLY_OFFER,
            reply_markup=keyboards.weekly_offer_keyboard(
                week_start_date(entry.entry_date)  # bound into the callback data
            ),
        )


# --------------------------------------------------------------------------
# /today — unified morning + evening snapshot
# --------------------------------------------------------------------------
async def _show_today(message: Message, telegram_user_id: int) -> None:
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_user_id)
        # One reflection day resolved once (v1.1.1): heading and DB rows can
        # never disagree even if the clock crosses 05:00 mid-render.
        day = reflection_day(user.timezone)
        entry = checkin_service.get_entry(session, user, entry_date=day)
        intent = morning_service.get_intent(session, user, intention_date=day)
        lock = morning_service.get_lock(session, user, intention_date=day)

    # The morning action follows the same rule that protects the record, so a
    # button here can never promise an edit the service would refuse: nothing
    # at all once the morning is locked or its day already has an evening.
    if lock is not None:
        morning_action: keyboards.MorningAction | None = None
    elif intent is not None:
        morning_action = keyboards.MorningAction.EDIT
    else:
        morning_action = keyboards.MorningAction.CREATE

    await message.answer(
        texts.today_message(
            day,
            entry.day_score if entry else None,
            entry.mood_score if entry else None,
            entry.energy_score if entry else None,
            entry.reflection_text if entry else None,
            intent,
        ),
        reply_markup=keyboards.today_actions_keyboard(
            morning_action=morning_action, has_evening=entry is not None
        ),
    )


@router.message(Command("today"))
async def cmd_today(message: Message) -> None:
    await _show_today(message, message.from_user.id)
