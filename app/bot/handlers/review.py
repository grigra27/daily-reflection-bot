"""Read-only day/week browsing and explicit, on-demand AI reviews."""

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.deps import AuthorizedFilter, PrivateChatFilter
from app.bot.keyboards.reply import BTN_HISTORY, BTN_WEEK
from app.bot.review_views import chunks, date_label, day_text, keyboard, week_keyboard, week_text
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import ai_review_service, review_service
from app.services.auth_service import authorize
from app.services.time_service import reflection_day, week_start_date

router = Router(name="review")
router.message.filter(AuthorizedFilter(), PrivateChatFilter())
router.callback_query.filter(AuthorizedFilter(), PrivateChatFilter())
INVALID_DATE = "Эта дата недоступна. Открой /history или /week. Дату можно указать как 2026-11-02."


async def _deliver(message: Message, text: str, markup, *, edit: bool = False) -> None:
    parts = chunks(text)
    for i, part in enumerate(parts):
        kwargs = {"reply_markup": markup if i == len(parts) - 1 else None, "parse_mode": None}
        if edit and len(parts) == 1:
            try:
                await message.edit_text(part, **kwargs)
            except TelegramBadRequest as exc:
                if "message is not modified" not in exc.message.lower():
                    raise
        else:
            await message.answer(part, **kwargs)


def _view(telegram_id: int, kind: str, value: str | None):
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, telegram_id)
        today = reflection_day(user.timezone)
        first = review_service.earliest_date(session, user, today)
        if kind == "w":
            requested = date.fromisoformat(value) if value else today
            # Commands may name any day in a week, but never a future day.
            if requested > today:
                raise ValueError("future date")
            w = review_service.week(session, user, week_start_date(requested), today=today)
            text = week_text(w)
            if not ai_review_service.enabled(runtime.settings):
                text += "\nИИ пока не подключён."
            elif w["summary"]["completed"] < 3:
                text += "\nДля ИИ-обзора нужны хотя бы три дневных итога."
            return text, week_keyboard(w, ai_enabled=ai_review_service.enabled(runtime.settings))
        if kind == "d":
            d = date.fromisoformat(value) if value else today
            # Empty days at the start of the earliest week are still browsable.
            review_service.validate_date(d, today, week_start_date(first))
            item = review_service.days(session, user, d, d)[0]
            nav = []
            if d > week_start_date(first):
                nav.append(("← День", f"rv:d:{d - timedelta(days=1)}"))
            if d < today:
                nav.append(("День →", f"rv:d:{d + timedelta(days=1)}"))
            return day_text(item), keyboard(
                [
                    nav,
                    [
                        ("📊 Эта неделя", f"rv:w:{week_start_date(d)}"),
                        ("📅 История дней", "rv:days:0"),
                    ],
                ]
            )
        if kind not in ("days", "weeks"):
            raise ValueError("unknown view")
        page = int(value or "0")
        span = 7 if kind == "days" else 56
        last = today if kind == "days" else week_start_date(today)
        first = first if kind == "days" else week_start_date(first)
        max_page = (last - first).days // span
        if not 0 <= page <= max_page:
            raise ValueError("invalid page")
        end = last - timedelta(days=page * span)
        rows = []
        if kind == "days":
            items = review_service.days(session, user, max(first, end - timedelta(days=6)), end)
            for d in reversed(items):
                status = (
                    f"день {d['scores']['day']}/5"
                    if d["scores"]
                    else ("есть записи" if review_service.has_data(d) else "нет записей")
                )
                rows.append([(f"{date_label(d['date'])} · {status}", f"rv:d:{d['date']}")])
        else:
            for offset in range(8):
                start = end - timedelta(weeks=offset)
                if start < first:
                    break
                w = review_service.week(session, user, start, today=today)
                label = f"{date_label(start)} · {w['summary']['completed']}/7 дней"
                if w["reflection"]:
                    label += " · ✍️"
                rows.append([(label, f"rv:w:{start}")])
        nav = []
        if page < max_page:
            nav.append(("← Раньше", f"rv:{kind}:{page + 1}"))
        if page > 0:
            nav.append(("Позже →", f"rv:{kind}:{page - 1}"))
        rows += [nav, [("📅 Дни", "rv:days:0"), ("📖 Недели", "rv:weeks:0")]]
        heading = "📅 История дней" if kind == "days" else "📖 История недель"
        return f"{heading} · {page + 1}/{max_page + 1}\nВыбери запись.", keyboard(rows)


async def _show(message: Message, telegram_id: int, kind: str, value=None, *, edit=False):
    try:
        text, markup = _view(telegram_id, kind, value)
    except (ValueError, OverflowError):
        await message.answer(INVALID_DATE, parse_mode=None)
        return
    await _deliver(message, text, markup, edit=edit)


@router.message(Command("week"))
async def cmd_week(message: Message, command: CommandObject) -> None:
    await _show(message, message.from_user.id, "w", command.args)


@router.message(Command("day"))
async def cmd_day(message: Message, command: CommandObject) -> None:
    await _show(message, message.from_user.id, "d", command.args)


@router.message(Command("history"))
@router.message(F.text == BTN_HISTORY)
async def cmd_history(message: Message) -> None:
    await _show(message, message.from_user.id, "days")


@router.message(F.text == BTN_WEEK)
async def menu_week(message: Message) -> None:
    await _show(message, message.from_user.id, "w")


@router.callback_query(F.data.startswith("rv:"))
async def browse(cb: CallbackQuery) -> None:
    if not cb.message:
        await cb.answer()
        return
    parts = (cb.data or "").split(":")
    if len(parts) != 3:
        await cb.answer("Открой /history заново.")
        return
    _, kind, value = parts
    if kind != "a":
        await cb.answer()
        await _show(cb.message, cb.from_user.id, kind, value, edit=True)
        return
    await cb.answer("Готовлю обзор — обычно это занимает несколько секунд.")
    runtime = get_runtime()
    try:
        start = date.fromisoformat(value)
        result, cached = await ai_review_service.analyze(
            runtime.settings, runtime.session_factory, cb.from_user.id, start
        )
    except (ValueError, OverflowError):
        await cb.message.answer(INVALID_DATE, parse_mode=None)
        return
    except ai_review_service.AnalysisUnavailable as exc:
        await cb.message.answer(str(exc), parse_mode=None)
        return
    lines = [
        f"🤖 ИИ-обзор недели с {date_label(start)}",
        "Интерпретация Groq — проверь её по своим записям.",
    ]
    if cached:
        lines.append("Сохранённый обзор; нового запроса к модели не было.")
    for item in result.observations:
        lines += ["", "• " + item.text, "Записи: " + ", ".join(date_label(d) for d in item.dates)]
    lines += [
        "",
        "Небольшой эксперимент",
        result.experiment.text,
        "Записи: " + ", ".join(date_label(d) for d in result.experiment.dates),
        "",
        result.question,
    ]
    dates = sorted({d for item in [*result.observations, result.experiment] for d in item.dates})
    rows = [
        [(date_label(d), f"rv:d:{d}") for d in dates[i : i + 3]] for i in range(0, len(dates), 3)
    ]
    rows.append([("📊 К неделе", f"rv:w:{start}")])
    await _deliver(cb.message, "\n".join(lines), keyboard(rows))
