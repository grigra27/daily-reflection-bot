"""Reply (main) keyboard shown after /start."""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_CHECKIN = "📝 Заполнить сегодня"
BTN_TODAY = "📅 Сегодня"
BTN_STATS = "📊 Статистика"
BTN_EXPORT = "📤 Экспорт"
BTN_SETTINGS = "⚙️ Настройки"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CHECKIN)],
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_STATS)],
            [KeyboardButton(text=BTN_EXPORT), KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )
