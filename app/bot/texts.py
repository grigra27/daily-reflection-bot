"""Telegram presentation texts and emoji/score formatting.

The baseline wording is the semantic reference. Emoji and labels belong here
only — scores are always persisted as integers 1..5 and outcomes as the
canonical ``done`` / ``partial`` / ``not_done`` strings (presentation is
separate from data). Tone: short, calm, neutral, no psychological
interpretation of what was or was not achieved.
"""

from __future__ import annotations

import html
from datetime import date

from app.database.models import MorningIntent
from app.services.morning_service import (
    OUTCOME_DONE,
    OUTCOME_NOT_DONE,
    OUTCOME_PARTIAL,
    MorningLock,
)

_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

DAY_EMOJI = {1: "😞", 2: "🙁", 3: "😐", 4: "🙂", 5: "😄"}
MOOD_EMOJI = {1: "😞", 2: "🙁", 3: "😐", 4: "🙂", 5: "😄"}
ENERGY_EMOJI = {1: "🪫", 2: "🔋", 3: "🔋", 4: "⚡", 5: "⚡"}

DAY_LABEL = {1: "Очень плохо", 2: "Плохо", 3: "Нормально", 4: "Хорошо", 5: "Отлично"}

WEEKDAY_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

NOT_PRIVATE = "Этот бот является приватным."
GENERAL_ERROR = "Что-то пошло не так. Попробуй ещё раз чуть позже."

START = (
    "👋 Добро пожаловать.\n\n"
    "Утром зафиксируем главный фокус дня, вечером — коротко подведём итог."
)

HELP = (
    "<b>Что умеет бот</b>\n\n"
    "/morning — записать или изменить фокус дня\n"
    "/checkin — заполнить сегодняшний итог\n"
    "/today — посмотреть сегодняшний снимок\n"
    "/stats — статистика\n"
    "/export — выгрузить данные\n"
    "/settings — настройки"
)

# --- Morning intention flow (v1.1) -------------------------------------------
MORNING_PROMPT = "☀️ <b>Доброе утро</b>\n\nЗафиксируем главный фокус дня?"
Q_MAIN_INTENTION = (
    "🎯 <b>Главное сегодня:</b>\n\nНапиши одно основное дело или фокус дня."
)
Q_SECONDARY_INTENTION = (
    "○ <b>Ещё хочу успеть:</b>\n\nМожно написать 1–2 дополнительных намерения."
)
MORNING_EMPTY_TODAY = "☀️ <b>Фокус на сегодня</b>"
MORNING_RECORDED_EMPTY = "☀️ Утренний фокус не записан."
INTENTION_TOO_LONG = "Слишком длинно. Напиши короче — максимум 1000 символов."
INTENTION_EMPTY = "Напиши хотя бы одно главное дело или фокус дня."

# v1.2: the morning record is frozen once its evening closure has started.
MORNING_LOCKED_OUTCOME = (
    "Утренний фокус уже зафиксирован — вечерний итог для этого дня начат."
)
MORNING_LOCKED_FILLED = "Утренний фокус уже зафиксирован — итог этого дня уже заполнен."


def morning_locked_message(lock: MorningLock | None) -> str:
    return MORNING_LOCKED_FILLED if lock is MorningLock.DAY_FILLED else MORNING_LOCKED_OUTCOME


# --- Evening outcomes (v1.2 "close the loop") --------------------------------
# Labels for the three canonical outcomes, in button order. ``partial`` cannot
# be expressed as a boolean, which is why the vocabulary has three members.
OUTCOME_LABEL: dict[str, str] = {
    OUTCOME_DONE: "✅ Да",
    OUTCOME_PARTIAL: "➗ Частично",
    OUTCOME_NOT_DONE: "❌ Нет",
}


def outcome_label(outcome: str | None) -> str | None:
    """Presented form of a stored outcome; None (not asked yet) renders nothing
    rather than an alarming 'NULL' / 'not filled' line."""
    return OUTCOME_LABEL[outcome] if outcome else None


def intention_lines(
    main: str | None,
    secondary: str | None,
    *,
    main_outcome: str | None = None,
    secondary_outcome: str | None = None,
) -> list[str]:
    """Shared morning block renderer — one implementation for /morning, the
    evening questions, /today and the completion message. User text is escaped
    because messages are sent with ParseMode.HTML; an absent secondary
    intention simply omits its line, and an outcome that has not been recorded
    yet omits its «Результат» line."""
    lines = []
    if main is not None:
        lines.append(f"🎯 Главное: {html.escape(main)}")
        if (label := outcome_label(main_outcome)) is not None:
            lines.append(f"Результат: {label}")
    if secondary:
        lines.append(f"○ Ещё: {html.escape(secondary)}")
        if (label := outcome_label(secondary_outcome)) is not None:
            lines.append(f"Результат: {label}")
    return lines


def _result_lines(intent: MorningIntent | None) -> list[str]:
    if intent is None:
        return []
    return intention_lines(
        intent.main_intention,
        intent.secondary_intention,
        main_outcome=intent.main_outcome,
        secondary_outcome=intent.secondary_outcome,
    )


def morning_record_message(intent: MorningIntent) -> str:
    return "\n".join([MORNING_EMPTY_TODAY, "", *_result_lines(intent)])


MORNING_DONE_HEADER = "✅ <b>Фокус дня сохранён</b>"


def morning_done_message(main: str, secondary: str | None) -> str:
    return "\n".join([MORNING_DONE_HEADER, "", *intention_lines(main, secondary)])


# --- Daily check-in flow -----------------------------------------------------
CHECKIN_HEADER = "🌙 <b>Итоги дня</b>\n\nКак в целом прошёл твой день?"
CHECKIN_SECTION = "🌙 <b>Итоги дня</b>"
EVENING_MISSING = "🌙 Итог дня пока не заполнен."
Q_DAY = "<b>Как в целом прошёл твой день?</b>"
Q_MOOD = "<b>Как ты себя эмоционально чувствовал большую часть дня?</b>"
Q_ENERGY = "<b>Сколько у тебя сегодня было энергии?</b>"
Q_REFLECTION_CHOICE = "<b>Хочешь оставить пару слов о сегодняшнем дне?</b>"
Q_REFLECTION_TEXT = (
    "<b>Что сегодня сильнее всего повлияло на твою оценку дня?</b>\n\n"
    "Напиши в свободной форме."
)

ALREADY_FILLED = "Ты уже заполнял сегодняшний итог."

# --- Evening questions (v1.2 loop closure, then the v1 ratings) --------------
Q_OUTCOME = "<b>Получилось?</b>"
NOW_ABOUT_DAY = "Теперь про день в целом."


def q_main_outcome(main_intention: str) -> str:
    """Step A — close the main intention. The text is quoted back so the user
    answers about what they actually planned this morning."""
    return "\n".join(
        [
            CHECKIN_SECTION,
            "",
            "🎯 <b>Главное сегодня:</b>",
            html.escape(main_intention),
            "",
            Q_OUTCOME,
        ]
    )


def q_secondary_outcome(secondary_intention: str) -> str:
    """Step B — close the secondary field as one whole. A user who wrote
    «зал, заказать страховку» answers once, with «Частично» when only one of
    the two happened; this is intentionally not a task list."""
    return "\n".join(
        [
            "○ <b>Ещё хотел успеть:</b>",
            html.escape(secondary_intention),
            "",
            Q_OUTCOME,
        ]
    )


def evening_day_prompt(intent: MorningIntent | None) -> str:
    """Step C — the day ratings. Without a morning intent this is exactly the
    v1 header (no negative reminder about not having written anything); with
    one it is the same question, reached after the outcomes were closed."""
    if intent is None:
        return CHECKIN_HEADER
    return f"{CHECKIN_SECTION}\n\n{NOW_ABOUT_DAY}\n\n{Q_DAY}"


def done_message(day: int, mood: int, energy: int) -> str:
    return (
        "✅ <b>Готово</b>\n\n"
        f"День: {DAY_EMOJI[day]} <b>{day}/5</b>\n"
        f"Настроение: {MOOD_EMOJI[mood]} <b>{mood}/5</b>\n"
        f"Энергия: {ENERGY_EMOJI[energy]} <b>{energy}/5</b>\n\n"
        "Запись сохранена."
    )


def ru_date(day: date) -> str:
    return f"{day.day} {_MONTHS_RU[day.month - 1]} {day.year}"


def today_message(
    entry_day: date,
    day: int | None,
    mood: int | None,
    energy: int | None,
    reflection: str | None,
    intent: MorningIntent | None = None,
) -> str:
    """Unified daily snapshot: morning block (with any evening outcomes
    already recorded against it) + evening block, each with a neutral empty
    state. Supports all four morning/evening existence combinations."""
    lines = [f"📅 <b>{ru_date(entry_day)}</b>", "", "☀️ <b>Утро</b>", ""]
    if intent is not None:
        lines += _result_lines(intent)
    else:
        lines.append(MORNING_RECORDED_EMPTY)
    lines += ["", "🌙 <b>Итоги</b>", ""]
    if day is None or mood is None or energy is None:
        lines.append(EVENING_MISSING)
        return "\n".join(lines)
    lines += [
        f"День: {DAY_EMOJI[day]} {day}/5",
        f"Настроение: {MOOD_EMOJI[mood]} {mood}/5",
        f"Энергия: {ENERGY_EMOJI[energy]} {energy}/5",
    ]
    if reflection:
        # User text is untrusted: messages are sent with ParseMode.HTML, so
        # escape it; the app's own markup above stays literal.
        lines += ["", f"💬 {html.escape(reflection)}"]
    return "\n".join(lines)


# --- Reminders ---------------------------------------------------------------
REMINDER = (
    "Кажется, сегодня мы ещё не подвели итог дня 🙂\n"
    "Займёт меньше минуты."
)

# --- Weekly ------------------------------------------------------------------
WEEKLY_OFFER = "📖 Хочешь коротко подвести итог недели?"
WEEKLY_Q1 = "<b>Что было лучшим событием этой недели?</b>"
WEEKLY_Q2 = "<b>Что больше всего отнимало силы?</b>"
WEEKLY_Q3 = "<b>Чего хотелось бы больше на следующей неделе?</b>"
WEEKLY_DONE = "✅ Итог недели сохранён."


# --- Stats -------------------------------------------------------------------
def stats_title(period_days: int) -> str:
    return f"📊 <b>Последние {period_days} дней</b>"


# --- Settings ----------------------------------------------------------------
def settings_message(morning_time: str, checkin_time: str, reminder_time: str, tz: str) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"☀️ Утренний фокус: {morning_time}\n"
        f"🌙 Итоги дня: {checkin_time}\n"
        f"🔔 Повторное напоминание: {reminder_time}\n"
        f"🌍 Часовой пояс: {tz}"
    )


ASK_MORNING_TIME = (
    "Во сколько утром напоминать про фокус дня? Отправь время в формате ЧЧ:ММ (например, 08:30)."
)
ASK_CHECKIN_TIME = "Во сколько задавать ежедневный вопрос? Отправь время в формате ЧЧ:ММ (например, 21:30)."
ASK_REMINDER_TIME = "Во сколько присылать повторное напоминание? Формат ЧЧ:ММ (например, 23:00)."
ASK_TIMEZONE = (
    "Укажи часовой пояс, например Europe/Moscow или Europe/Berlin."
)
INVALID_TIME = "Не понял время. Отправь в формате ЧЧ:ММ, например 21:30."
INVALID_TZ = "Такого часового пояса нет. Пример: Europe/Moscow."
