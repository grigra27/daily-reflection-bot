"""The evening loop's shared entry point (v1.2 "close the loop").

One implementation turns "what does this Reflection Day still owe?" into the
actual Telegram question, keyboard and FSM state, so the manual /checkin start,
a resume after a half-finished flow, the edit flow and the scheduled evening
prompt can never drift apart into three copies of the step logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aiogram.fsm.state import State
from aiogram.types import InlineKeyboardMarkup

from app.bot import keyboards, texts
from app.bot.states import CheckinStates
from app.database.models import MorningIntent
from app.services.morning_service import (
    OUTCOME_FIELD_MAIN,
    OUTCOME_FIELD_SECONDARY,
    EveningStep,
    next_evening_step,
)


@dataclass(frozen=True)
class EveningPrompt:
    step: EveningStep
    text: str
    markup: InlineKeyboardMarkup
    #: None for the day ratings, exactly as before v1.2: that step is reached
    # from a keyboard tap rather than a state-gated handler.
    state: State | None


def build_evening_prompt(
    intent: MorningIntent | None,
    target_date: date,
    *,
    edit_mode: bool = False,
    after_outcome_field: str | None = None,
) -> EveningPrompt:
    """The next evening question for ``target_date``, with the target date
    baked into every button so the flow survives the 05:00 rollover, a process
    restart and a lost MemoryStorage.

    ``edit_mode``/``after_outcome_field`` come straight out of the FSM and are
    handed to the one canonical step rule: a resumed evening asks what is
    still open, an explicit edit re-walks both questions.
    """
    step = next_evening_step(
        intent, edit_mode=edit_mode, after_outcome_field=after_outcome_field
    )
    if intent is not None and step is EveningStep.MAIN_OUTCOME:
        return EveningPrompt(
            step,
            texts.q_main_outcome(intent.main_intention),
            keyboards.outcome_keyboard(OUTCOME_FIELD_MAIN, target_date),
            CheckinStates.waiting_main_outcome,
        )
    if intent is not None and step is EveningStep.SECONDARY_OUTCOME:
        assert intent.secondary_intention is not None  # implied by the step
        return EveningPrompt(
            step,
            texts.q_secondary_outcome(intent.secondary_intention),
            keyboards.outcome_keyboard(OUTCOME_FIELD_SECONDARY, target_date),
            CheckinStates.waiting_secondary_outcome,
        )
    return EveningPrompt(
        step,
        texts.evening_day_prompt(intent),
        keyboards.day_keyboard(target_date),
        None,
    )


#: The morning field each open outcome state is waiting for, keyed by the raw
#: state string the FSM storage keeps (``aiogram`` stores ``State.state``).
_OUTCOME_FIELD_BY_STATE: dict[str | None, str] = {
    CheckinStates.waiting_main_outcome.state: OUTCOME_FIELD_MAIN,
    CheckinStates.waiting_secondary_outcome.state: OUTCOME_FIELD_SECONDARY,
}


def outcome_tap_is_open(state_value: str | None, outcome_field: str) -> bool:
    """Whether an outcome button may be answered by the flow that is open now.

    Three cases, with no fourth: no state at all is a scheduled prompt tapped
    after a restart wiped MemoryStorage, so the button itself decides; an
    outcome state is asking about exactly one morning field, so a button for
    the other one is stale; and any *other* active state — a later rating step,
    the morning, weekly or settings flow — belongs to a different question
    entirely, where answering an outcome would hijack that flow and move the
    user's screen on to something they never asked for.
    """
    if state_value is None:
        return True
    return _OUTCOME_FIELD_BY_STATE.get(state_value) == outcome_field
