"""Inline keyboards for the bot.

Emoji/labels live here (presentation). Callback data is intentionally compact
and encodes only the integer score, never a display value.
"""

from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts

SCORES = (1, 2, 3, 4, 5)


def _score_row(field: str, emoji: dict[int, str]) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text=f"{emoji[v]} {v}", callback_data=f"ci:{field}:{v}")
        for v in SCORES
    ]


def day_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[_score_row("day", texts.DAY_EMOJI)]
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


def today_actions_keyboard(*, has_morning: bool, has_evening: bool) -> InlineKeyboardMarkup:
    """Contextual /today actions — morning and evening, current date only."""
    morning = (
        InlineKeyboardButton(text="✏️ Изменить утро", callback_data="mrn:edit")
        if has_morning
        else InlineKeyboardButton(text="☀️ Записать утро", callback_data="mrn:start")
    )
    evening = (
        InlineKeyboardButton(text="✏️ Изменить итог", callback_data="act:edit")
        if has_evening
        else InlineKeyboardButton(text="🌙 Заполнить итог", callback_data="act:checkin")
    )
    return InlineKeyboardMarkup(inline_keyboard=[[morning, evening]])


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
