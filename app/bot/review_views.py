"""Plain-text Telegram views: diary/model text is never interpreted as HTML."""

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup

from app.bot import texts
from app.services import review_service, vitamin_service

OUTCOMES = {
    "done": "✅ выполнено",
    "partial": "◐ частично",
    "not_done": "○ не выполнено",
    None: "итог не отмечен",
}


def date_label(value: str | date) -> str:
    return (date.fromisoformat(value) if isinstance(value, str) else value).strftime("%d.%m.%Y")


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [Button(text=label, callback_data=data) for label, data in row] for row in rows if row
        ]
    )


def analysis_text(result, start: date, *, cached: bool) -> str:
    lines = [
        f"🤖 ИИ-обзор недели с {date_label(start)}",
        "Выводы Groq и исходные записи — интерпретацию можно оспорить.",
    ]
    if cached:
        lines.append("Сохранённый обзор; нового запроса к модели не было.")

    def evidence(item):
        return [
            ("Неделя с " if s.source_id.startswith("week:") else "Запись ")
            + f"{date_label(s.date)} · {s.label}: «{s.quote}»"
            for s in item.sources
        ]

    for item in result.observations:
        lines += ["", "• " + item.text, *evidence(item)]
    lines += [
        "",
        "Небольшой эксперимент",
        result.experiment.text,
        "Что отметить: " + result.experiment.check,
        *evidence(result.experiment),
        "",
        result.question.text,
        *evidence(result.question),
    ]
    return "\n".join(lines)


def week_text(w: dict) -> str:
    s = w["summary"]
    lines = [f"📊 Неделя {date_label(w['start'])} — {date_label(w['end'])}"]
    if w["ongoing"]:
        lines.append("Неделя ещё идёт — показаны записи по сегодняшний день.")
    lines += ["", f"Дневных итогов: {s['completed']} из {len(w['days'])}"]
    if s["averages"]:
        a = s["averages"]
        lines.append(
            f"Средние: день {a['day']}/5 · настроение {a['mood']}/5 · энергия {a['energy']}/5"
        )
        p = w["previous"]
        if p["averages"]:
            a = p["averages"]
            lines.append(
                f"Предыдущая полная неделя ({p['completed']} записей): день {a['day']} · настроение {a['mood']} · энергия {a['energy']}"
            )
        rated = [d for d in w["days"] if d["scores"]]
        high, low = max(d["scores"]["day"] for d in rated), min(d["scores"]["day"] for d in rated)
        for label, score in (("Самая высокая оценка", high), ("Самая низкая оценка", low)):
            dates = ", ".join(
                date_label(d["date"])[:5] for d in rated if d["scores"]["day"] == score
            )
            lines.append(f"{label}: {score}/5 — {dates}")
    o = s["outcomes"]
    if o:
        lines += [
            "",
            f"🎯 Намерения: ✅ {o.get('done', 0)} · ◐ {o.get('partial', 0)} · ○ {o.get('not_done', 0)} · без итога {o.get('unanswered', 0)}",
        ]
    lines += ["", f"🍽 Итоги питания: {s['food_completed']}"]
    for code, (kept, total) in sorted(s["food_rules"].items(), key=lambda p: p[1][0] / p[1][1])[:3]:
        lines.append(f"• {texts.food_rule_label(code)}: {kept}/{total}")
    lines.append(f"💊 Дней с отметкой: {s['vitamin_confirmed_days']} (нет отметки ≠ не принимал)")
    if w["reflection"]:
        lines += ["", "📖 Твой итог недели"]
        for key, label in (
            ("best_event", "Лучшее"),
            ("energy_drainer", "Отнимало силы"),
            ("want_more", "Хочу больше"),
        ):
            lines.append(f"{label}: {w['reflection'][key] or 'не записано'}")
    else:
        lines += ["", "📖 Итог недели ещё не записан."]
    lines += [
        "",
        "ИИ-обзор по кнопке: Groq получит оценки, намерения и записи этой недели. Тексты передаются как написаны; Telegram ID и имя профиля не добавляются.",
    ]
    return "\n".join(lines)


def week_keyboard(w: dict, *, ai_enabled: bool) -> InlineKeyboardMarkup:
    start = date.fromisoformat(w["start"])
    nav = []
    if start > date.fromisoformat(w["first"]):
        nav.append(("← Неделя", f"rv:w:{start - timedelta(days=7)}"))
    if start + timedelta(days=7) <= date.fromisoformat(w["today"]):
        nav.append(("Неделя →", f"rv:w:{start + timedelta(days=7)}"))
    rows = (
        [[("🤖 ИИ-обзор Groq", f"rv:a:{start}")]]
        if ai_enabled and w["summary"]["completed"] >= 3
        else []
    )
    rows += [[("✍️ Изменить итог" if w["reflection"] else "✍️ Подвести итог", f"wk:start:{start}")]]
    rows += [
        [
            (
                date_label(d["date"])[:5]
                + (f" · {d['scores']['day']}/5" if d["scores"] else " · —"),
                f"rv:d:{d['date']}",
            )
            for d in w["days"][i : i + 4]
        ]
        for i in range(0, len(w["days"]), 4)
    ]
    rows += [nav, [("📖 История недель", "rv:weeks:0"), ("📅 История дней", "rv:days:0")]]
    return keyboard(rows)


def day_text(d: dict) -> str:
    lines = [f"📅 {date_label(d['date'])}", ""]
    if not review_service.has_data(d):
        return "\n".join(lines + ["За этот день записей нет."])
    for i in d["intentions"]:
        lines.append(f"🎯 {i['text']}\n{OUTCOMES.get(i['outcome'], 'итог не отмечен')}")
    if d["scores"]:
        s = d["scores"]
        lines += ["", f"День {s['day']}/5 · настроение {s['mood']}/5 · энергия {s['energy']}/5"]
    if d["note"]:
        lines += ["", "💬 " + d["note"]]
    f = d["food"]
    if f:
        lines += ["", "🍽 Питание" + ("" if f["complete"] else " — черновик, итог не подтверждён")]
        if f["focus"]:
            lines.append("Фокус: " + texts.food_rule_label(f["focus"]))
        lines += [
            ("✅ " if kept else "○ ") + texts.food_rule_label(code)
            for code, kept in f["rules"].items()
        ]
        if f["triggers"]:
            lines.append(
                "Что влияло: "
                + ", ".join(texts.FOOD_TRIGGER_LABEL.get(c, c) for c in f["triggers"])
            )
    if d["vitamins"]:
        lines += [
            "",
            "💊 Отмечено: "
            + ", ".join(
                vitamin_service.item_label(c)
                for c in d["vitamins"]
                if vitamin_service.validate_item(c)
            ),
        ]
    elif d["legacy_vitamins"]:
        lines += ["", "💊 Старая общая отметка о витаминах (без детализации)."]
    if d["weight"] is not None:
        lines += ["", f"⚖️ Записан вес: {d['weight']:g} кг"]
    return "\n".join(lines)


def chunks(text: str, limit: int = 3500) -> list[str]:
    """Telegram counts UTF-16 units. Preserve full texts, including long notes."""
    result, part, size = [], [], 0
    for char in text:
        n = 2 if ord(char) > 0xFFFF else 1
        if size + n > limit:
            result.append("".join(part))
            part, size = [], 0
        part.append(char)
        size += n
    if part:
        result.append("".join(part))
    return result
