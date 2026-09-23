"""FSM state definitions (baseline section 30, v1.1 morning flow, v1.2 loop).

Daily check-in:  IDLE -> [WAITING_MAIN_OUTCOME -> WAITING_SECONDARY_OUTCOME] ->
WAITING_DAY -> WAITING_MOOD -> WAITING_ENERGY ->
ASK_REFLECTION -> WAITING_REFLECTION_TEXT -> COMPLETE
(the two outcome states only exist when a MorningIntent was written that day)
Morning:         IDLE -> WAITING_MAIN -> WAITING_SECONDARY -> COMPLETE
Weekly:          WEEKLY_IDLE -> WAITING_BEST_EVENT -> WAITING_ENERGY_DRAINER ->
WAITING_WANT_MORE -> COMPLETE
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CheckinStates(StatesGroup):
    # v1.2: closing the morning intentions, before the day ratings.
    waiting_main_outcome = State()
    waiting_secondary_outcome = State()
    waiting_day = State()
    waiting_mood = State()
    waiting_energy = State()
    ask_reflection = State()
    waiting_reflection_text = State()


class MorningStates(StatesGroup):
    waiting_main = State()
    waiting_secondary = State()


class WeeklyStates(StatesGroup):
    waiting_best_event = State()
    waiting_energy_drainer = State()
    waiting_want_more = State()


class SettingsStates(StatesGroup):
    waiting_morning_time = State()
    waiting_checkin_time = State()
    waiting_reminder_time = State()
    waiting_timezone = State()


class ExportStates(StatesGroup):
    waiting_scope = State()
