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
    "/food — питание: итог дня и статистика\n"
    "/weight — записать вес за неделю\n"
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
# A filled day that never had a morning plan: claiming one "is already fixed"
# would describe something that does not exist, so this says the real rule.
MORNING_LOCKED_DAY_OVER = (
    "Итог этого дня уже заполнен — утренний фокус для него уже не добавить."
)


def morning_locked_message(lock: MorningLock | None, *, has_record: bool = True) -> str:
    """Neutral explanation of why the morning cannot be written right now.
    ``has_record`` is the difference between freezing a plan the user made and
    refusing to invent one after their evening was closed."""
    if lock is MorningLock.DAY_FILLED:
        return MORNING_LOCKED_FILLED if has_record else MORNING_LOCKED_DAY_OVER
    return MORNING_LOCKED_OUTCOME


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

# --- Vitamins (v1.3.1 reminder, v1.3.2 acknowledgement) ------------------------
# The daily question and its single ✅ button. A tap replaces the message with
# VITAMIN_TAKEN; VITAMIN_STALE only refuses a button from an older day. There is
# deliberately no "No"/"Пропустить" answer: not tapping proves nothing.
VITAMIN_REMINDER = "💊 Ты принял витамины?"
VITAMIN_TAKEN = "✅ Витамины приняты."
VITAMIN_STALE = "Эта кнопка уже устарела."

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
ASK_VITAMIN_TIME = (
    "Во сколько напоминать принять витамины?\nОтправь время в формате ЧЧ:ММ, например 22:00."
)
ASK_TIMEZONE = (
    "Укажи часовой пояс, например Europe/Moscow или Europe/Berlin."
)
INVALID_TIME = "Не понял время. Отправь в формате ЧЧ:ММ, например 21:30."
INVALID_TZ = "Такого часового пояса нет. Пример: Europe/Moscow."


# --- Food reflection (v1.3) ----------------------------------------------------
# Labels for the persisted codes in ``food_service``. Tone stays neutral: facts
# and numbers, no praise, no motivational phrases.
FOOD_RULE_LABEL: dict[str, str] = {
    "no_late_eating": "Не ем за 3 ч до сна",
    "no_fastfood": "Без фастфуда и доставки",
    "no_sweets": "Без сладкого",
    "no_chips": "Без чипсов",
    "no_sugary_drinks": "Без сладких напитков",
    "no_alcohol": "Без алкоголя",
    "stop_when_full": "Стоп при сытости",
    "no_screen": "Ем без экрана",
    "no_packaging": "Не ем из упаковки",
    "water": "Вода 1,5–2 л",
    "no_after_20": "Не ем после 20:00",
    "no_snacks": "Без перекусов",
    "breakfast": "Нормальный завтрак",
    "vegetables": "Овощи в обед и ужин",
    "no_seconds": "Без добавки",
    "eat_slowly": "Ем медленно",
    "steps": "8 000+ шагов",
}

FOOD_TRIGGER_LABEL: dict[str, str] = {
    "stress": "Стресс",
    "tired": "Усталость",
    "boredom": "Скука",
    "company": "Компания / праздник",
    "hunger": "Сильный голод",
    "sleep": "Недосып",
    "no_food": "Не было нормальной еды",
}

FOOD_SECTION = "🍽 <b>Питание</b>"
FOOD_NO_FOCUS = "Без фокуса"
FOOD_MORNING_PROMPT = f"{FOOD_SECTION}\n\nНа чём по еде сосредоточишься сегодня?"
FOOD_EVENING_HINT = "Отметь, что нарушил. Нажатие переключает ✅ ↔ ❌."
FOOD_TRIGGERS_QUESTION = "<b>Что повлияло?</b> Можно выбрать несколько или пропустить."
FOOD_DAY_CLOSED = "Итог по питанию за этот день уже сохранён. Изменить: /food"
FOOD_DATE_REFUSED = "Эта кнопка устарела — день уже нельзя изменить."
FOOD_RULE_DISABLED = "Это правило сейчас выключено в настройках."
FOOD_LAST_RULE = "Хотя бы одно правило должно остаться включённым."
FOOD_NO_DATA = "За этот период ещё нет итогов по питанию."

WEIGHT_PROMPT = (
    "⚖️ <b>Вес</b>\n\nЗапиши вес на этой неделе — отправь одно число, например 92.4."
)
WEIGHT_ASK = "⚖️ Отправь вес одним числом, например 92.4."
WEIGHT_INVALID = "Не понял число. Отправь вес в килограммах, например 92.4."


def food_rule_label(code: str) -> str:
    return FOOD_RULE_LABEL.get(code, code)


def _food_day_title(target_date: date, today: date) -> str:
    if target_date == today:
        return f"{FOOD_SECTION} — итог дня"
    return f"{FOOD_SECTION} — итог за {target_date.day} {_MONTHS_RU[target_date.month - 1]}"


def food_focus_line(focus: str | None) -> str:
    return f"🎯 Фокус: {food_rule_label(focus) if focus else FOOD_NO_FOCUS.lower()}"


def food_focus_saved(focus: str | None) -> str:
    if focus is None:
        return f"{FOOD_SECTION}\n\nСегодня без фокуса."
    return f"{FOOD_SECTION}\n\n🎯 Фокус на сегодня: <b>{food_rule_label(focus)}</b>"


def food_checklist_message(
    target_date: date, today: date, focus: str | None, *, stale: bool = False
) -> str:
    lines = [_food_day_title(target_date, today), ""]
    if stale:
        lines += ["Вчерашний итог по питанию не заполнен.", ""]
    if focus:
        lines += [food_focus_line(focus), ""]
    lines.append(FOOD_EVENING_HINT)
    return "\n".join(lines)


def food_summary_message(
    target_date: date,
    today: date,
    *,
    rules: list[str],
    broken: list[str],
    focus: str | None,
    triggers: list[str],
    streak: int | None = None,
) -> str:
    lines = [_food_day_title(target_date, today), ""]
    lines.append(f"Соблюдено: <b>{len(rules) - len(broken)} из {len(rules)}</b>")
    if focus:
        mark = "❌" if focus in broken else "✅"
        lines.append(f"🎯 Фокус «{food_rule_label(focus)}»: {mark}")
    if broken:
        lines += ["", "Нарушено:"]
        lines += [f"❌ {food_rule_label(c)}" for c in broken]
    if triggers:
        lines += ["", "Повлияло: " + ", ".join(FOOD_TRIGGER_LABEL[c] for c in triggers)]
    if streak:
        lines += ["", f"Дней подряд без нарушений: <b>{streak}</b>"]
    return "\n".join(lines)


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace("-", "−")


def weight_saved_message(value: float, change: float | None) -> str:
    text = f"⚖️ Вес записан: <b>{value:.1f} кг</b>"
    if change is not None:
        text += f"\nК прошлой записи: {_signed(change)} кг"
    return text


def food_status_block(
    target_date: date, focus: str | None, focus_set: bool, completed: bool, broken: int, total: int
) -> str:
    lines = [f"{FOOD_SECTION} — {target_date.day} {_MONTHS_RU[target_date.month - 1]}", ""]
    lines.append(food_focus_line(focus) if focus_set else "🎯 Фокус не выбран")
    if completed:
        lines.append(f"Итог: соблюдено {total - broken} из {total}")
    else:
        lines.append("Итог дня пока не заполнен")
    return "\n".join(lines)


def food_stats_message(stats) -> str:
    lines = [f"🍽 <b>Питание — последние {stats.period_days} дней</b>", ""]
    lines.append(f"Заполнено: <b>{stats.completed_days} из {stats.period_days}</b>")
    if stats.completed_days == 0:
        lines += ["", FOOD_NO_DATA]
    else:
        percent = round(stats.clean_days / stats.completed_days * 100)
        lines.append(f"Дней без нарушений: <b>{stats.clean_days}</b> ({percent}%)")
        lines.append(f"Текущая серия: <b>{stats.streak}</b>")
        lines += ["", "<b>Правила</b> (от самого трудного):"]
        for rate in stats.rule_rates:
            lines.append(f"{rate.percent}% — {food_rule_label(rate.code)} ({rate.kept}/{rate.total})")
        if stats.focus_total:
            lines += [
                "",
                f"🎯 Фокус дня соблюдён: <b>{stats.focus_kept} из {stats.focus_total}</b>",
            ]
        if stats.triggers:
            lines += ["", "<b>Что чаще влияло:</b>"]
            lines += [f"{FOOD_TRIGGER_LABEL[c]} — {n}" for c, n in stats.triggers[:5]]
        if stats.energy_clean is not None:
            lines += [
                "",
                "<b>Дни без нарушений / с нарушениями:</b>",
                f"Энергия: {stats.energy_clean} / {stats.energy_broken}",
                f"Настроение: {stats.mood_clean} / {stats.mood_broken}",
            ]
    if stats.last_weight is not None:
        lines += ["", f"⚖️ Вес: <b>{stats.last_weight:.1f} кг</b>"]
        if stats.weight_change_week is not None:
            lines.append(f"За неделю: {_signed(stats.weight_change_week)} кг")
        if stats.weight_change_total is not None:
            lines.append(f"С первой записи: {_signed(stats.weight_change_total)} кг")
    return "\n".join(lines)


def food_settings_block(
    food_morning_time: str, food_evening_time: str, weight_time: str, rules_count: int
) -> str:
    return (
        f"\n\n🍽 Питание утром: {food_morning_time}\n"
        f"🍽 Питание вечером: {food_evening_time}\n"
        f"⚖️ Вес (воскресенье): {weight_time}\n"
        f"📋 Правил питания: {rules_count}"
    )


FOOD_RULES_SETTINGS = "📋 <b>Правила питания</b>\n\nНажми, чтобы включить или выключить правило."
ASK_FOOD_MORNING_TIME = "Во сколько спрашивать про фокус по еде? Формат ЧЧ:ММ (например, 08:45)."
ASK_FOOD_EVENING_TIME = "Во сколько подводить итог по питанию? Формат ЧЧ:ММ (например, 22:30)."
ASK_WEIGHT_TIME = "Во сколько по воскресеньям спрашивать вес? Формат ЧЧ:ММ (например, 09:00)."


def vitamin_settings_block(enabled: bool, reminder_time: str) -> str:
    status = "включено" if enabled else "выключено"
    return f"\n\n💊 Витамины: {status}\n💊 Время напоминания: {reminder_time}"
