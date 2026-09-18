"""FSM state definitions (baseline section 30).

Daily check-in:  IDLE -> WAITING_DAY_SCORE -> WAITING_MOOD_SCORE ->
WAITING_ENERGY_SCORE -> ASK_REFLECTION -> WAITING_REFLECTION_TEXT -> COMPLETE
Weekly:          WEEKLY_IDLE -> WAITING_BEST_EVENT -> WAITING_ENERGY_DRAINER ->
WAITING_WANT_MORE -> COMPLETE
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CheckinStates(StatesGroup):
    waiting_day = State()
    waiting_mood = State()
    waiting_energy = State()
    ask_reflection = State()
    waiting_reflection_text = State()


class WeeklyStates(StatesGroup):
    waiting_best_event = State()
    waiting_energy_drainer = State()
    waiting_want_more = State()


class SettingsStates(StatesGroup):
    waiting_checkin_time = State()
    waiting_reminder_time = State()
    waiting_timezone = State()


class ExportStates(StatesGroup):
    waiting_scope = State()
