"""Inline keyboards for the bot.

Emoji/labels live here (presentation). Callback data is intentionally compact
and encodes only system-controlled values — an integer score or a canonical
outcome — never a display value or any user text.
"""

from __future__ import annotations

from datetime import date
from enum import Enum, auto

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts

SCORES = (1, 2, 3, 4, 5)


def _dated(callback_data: str, target_date: date | None) -> str:
    """Bake the target Reflection Day into a callback (v1.2).

    A scheduled Telegram message never creates FSM state, and MemoryStorage can
    be lost to a restart — the date carried by the button itself keeps the tap
    bound to the day it was written for, even after the 05:00 rollover. With no
    date the legacy form is produced, so messages sent before the upgrade stay
    tappable.
    """
    return callback_data if target_date is None else f"{callback_data}:{target_date.isoformat()}"


def _score_row(
    field: str, emoji: dict[int, str], target_date: date | None = None
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=f"{emoji[v]} {v}", callback_data=_dated(f"ci:{field}:{v}", target_date)
        )
        for v in SCORES
    ]


def day_keyboard(target_date: date | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_score_row("day", texts.DAY_EMOJI, target_date)]
    )


def outcome_keyboard(field: str, target_date: date | None = None) -> InlineKeyboardMarkup:
    """The three evening outcomes for one morning intention (``field`` is
    "main" or "secondary") — e.g. ``ci:out:main:partial:2026-09-23``."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label, callback_data=_dated(f"ci:out:{field}:{value}", target_date)
                )
                for value, label in texts.OUTCOME_LABEL.items()
            ]
        ]
    )


def mood_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_score_row("mood", texts.MOOD_EMOJI)]
    )


def energy_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_score_row("energy", texts.ENERGY_EMOJI)]
    )


def reflection_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✍️ Записать", callback_data="ci:ref:yes"),
                InlineKeyboardButton(text="Пропустить", callback_data="ci:ref:no"),
            ]
        ]
    )


def skip_keyboard(callback_prefix: str) -> InlineKeyboardMarkup:
    """A single 'skip' button used in text-entry steps (weekly/optional text)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data=f"{callback_prefix}:skip")]
        ]
    )


class MorningAction(Enum):
    """What /today may legitimately offer for the morning block (v1.2).

    Derived from the morning lock rather than from "does a row exist", so the
    keyboard can never offer an edit or a retroactive plan the service would
    refuse. ``None`` (no member) means neither button is shown.
    """

    EDIT = auto()
    CREATE = auto()


def today_actions_keyboard(
    *, morning_action: MorningAction | None, has_evening: bool
) -> InlineKeyboardMarkup:
    """Contextual /today actions — morning and evening, current date only."""
    row: list[InlineKeyboardButton] = []
    if morning_action is MorningAction.EDIT:
        row.append(InlineKeyboardButton(text="✏️ Изменить утро", callback_data="mrn:edit"))
    elif morning_action is MorningAction.CREATE:
        row.append(InlineKeyboardButton(text="☀️ Записать утро", callback_data="mrn:start"))
    evening = (
        InlineKeyboardButton(text="✏️ Изменить итог", callback_data="act:edit")
        if has_evening
        else InlineKeyboardButton(text="🌙 Заполнить итог", callback_data="act:checkin")
    )
    row.append(evening)
    return InlineKeyboardMarkup(inline_keyboard=[row])


def morning_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎯 Записать", callback_data="mrn:start")]]
    )


def morning_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✏️ Изменить", callback_data="mrn:edit")]]
    )


def filled_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👀 Посмотреть", callback_data="act:today"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="act:edit"),
            ]
        ]
    )


def reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📝 Заполнить", callback_data="act:checkin")]]
    )


def weekly_offer_keyboard(week_start: date | None = None) -> InlineKeyboardMarkup:
    """When ``week_start`` is given it is baked into the callback data
    (v1.1.1), so the offer stays bound to the right week even after the
    05:00 rollover or a bot restart — the answer is carried by the button
    itself, not by FSM state."""
    start_cb = f"wk:start:{week_start.isoformat()}" if week_start else "wk:start"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=start_cb),
                InlineKeyboardButton(text="Не сейчас", callback_data="wk:no"),
            ]
        ]
    )


def stats_keyboard(active: int) -> InlineKeyboardMarkup:
    def btn(days: int, label: str) -> InlineKeyboardButton:
        prefix = "▸ " if days == active else ""
        return InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"st:{days}")

    return InlineKeyboardMarkup(
        inline_keyboard=[[btn(7, "7 дней"), btn(30, "30 дней"), btn(90, "90 дней"), btn(365, "Год")]]
    )


def export_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Последние 30 дней", callback_data="ex:30d"),
                InlineKeyboardButton(text="Последние 90 дней", callback_data="ex:90d"),
            ],
            [
                InlineKeyboardButton(text="Текущий год", callback_data="ex:year"),
                InlineKeyboardButton(text="Всё время", callback_data="ex:all"),
            ],
        ]
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="☀️ Утренний фокус", callback_data="se:morning"),
                InlineKeyboardButton(text="🌙 Время вопроса", callback_data="se:checkin"),
            ],
            [
                InlineKeyboardButton(text="🔔 Напоминание", callback_data="se:reminder"),
                InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="se:tz"),
            ],
        ]
    )
