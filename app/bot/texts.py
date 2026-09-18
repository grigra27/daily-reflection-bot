"""Telegram presentation texts and emoji/score formatting.

The baseline wording is the semantic reference. Emoji and labels belong here
only — scores are always persisted as integers 1..5 (presentation is separate
from data). Tone: short, calm, neutral, no psychological interpretation.
"""

from __future__ import annotations

from datetime import date

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
    "Я буду раз в день помогать тебе коротко фиксировать, каким был твой день."
)

HELP = (
    "<b>Что умеет бот</b>\n\n"
    "/checkin — заполнить сегодняшний итог\n"
    "/today — посмотреть сегодняшнюю запись\n"
    "/stats — статистика\n"
    "/export — выгрузить данные\n"
    "/settings — настройки"
)

# --- Daily check-in flow -----------------------------------------------------
CHECKIN_HEADER = "🌙 <b>Итоги дня</b>\n\nКак в целом прошёл твой день?"
Q_DAY = "<b>Как в целом прошёл твой день?</b>"
Q_MOOD = "<b>Как ты себя эмоционально чувствовал большую часть дня?</b>"
Q_ENERGY = "<b>Сколько у тебя сегодня было энергии?</b>"
Q_REFLECTION_CHOICE = "<b>Хочешь оставить пару слов о сегодняшнем дне?</b>"
Q_REFLECTION_TEXT = (
    "<b>Что сегодня сильнее всего повлияло на твою оценку дня?</b>\n\n"
    "Напиши в свободной форме."
)

ALREADY_FILLED = "Ты уже заполнял сегодняшний итог."
NO_ENTRY_TODAY = "Сегодняшней записи пока нет."


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


def today_message(entry_day: date, day: int, mood: int, energy: int, reflection: str | None) -> str:
    lines = [
        f"📅 <b>{ru_date(entry_day)}</b>",
        "",
        f"День: {DAY_EMOJI[day]} {day}/5",
        f"Настроение: {MOOD_EMOJI[mood]} {mood}/5",
        f"Энергия: {ENERGY_EMOJI[energy]} {energy}/5",
    ]
    if reflection:
        lines += ["", f"💬 {reflection}"]
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
def settings_message(checkin_time: str, reminder_time: str, tz: str) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"Ежедневный вопрос: {checkin_time}\n"
        f"Повторное напоминание: {reminder_time}\n"
        f"Часовой пояс: {tz}"
    )


ASK_CHECKIN_TIME = "Во сколько задавать ежедневный вопрос? Отправь время в формате ЧЧ:ММ (например, 21:30)."
ASK_REMINDER_TIME = "Во сколько присылать повторное напоминание? Формат ЧЧ:ММ (например, 23:00)."
ASK_TIMEZONE = (
    "Укажи часовой пояс, например Europe/Moscow или Europe/Berlin."
)
INVALID_TIME = "Не понял время. Отправь в формате ЧЧ:ММ, например 21:30."
INVALID_TZ = "Такого часового пояса нет. Пример: Europe/Moscow."
