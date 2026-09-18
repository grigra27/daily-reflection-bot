"""Statistics rendering (baseline sections 20-22)."""

from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts
from app.bot.deps import AuthorizedFilter
from app.database.repositories import DailyEntryRepository
from app.database.session import session_scope
from app.runtime import get_runtime
from app.services import stats_service
from app.services.auth_service import authorize
from app.services.time_service import user_today

router = Router(name="stats")
router.message.filter(AuthorizedFilter())
router.callback_query.filter(AuthorizedFilter())

_DEFAULT_PERIOD = 30


def _render(user, stats: stats_service.StatsResult) -> str:
    lines = [
        texts.stats_title(stats.period_days),
        "",
        f"Заполнено: <b>{stats.completed_days} из {stats.period_days}</b>",
        "",
    ]
    if stats.completed_days == 0:
        lines.append("За этот период ещё нет записей.")
        return "\n".join(lines)

    lines += [
        f"Средняя оценка дня: <b>{stats.avg_day}</b>",
        f"Среднее настроение: <b>{stats.avg_mood}</b>",
        f"Средняя энергия: <b>{stats.avg_energy}</b>",
        "",
    ]
    labels = {5: "😄 Отличных", 4: "🙂 Хороших", 3: "😐 Нормальных", 2: "🙁 Плохих", 1: "😞 Очень плохих"}
    for score in (5, 4, 3, 2, 1):
        lines.append(f"{labels[score]}: {stats.distribution.get(score, 0)}")
    lines += [
        "",
        f"Хороших и отличных дней: <b>{stats.good_day_ratio}%</b>",
    ]
    if stats.recent7_avg is not None:
        lines += ["", f"Последние 7 заполненных дней: <b>{stats.recent7_avg}</b>"]
    if stats.previous7_avg is not None:
        lines.append(f"Предыдущие 7 заполненных дней: <b>{stats.previous7_avg}</b>")
    return "\n".join(lines)


async def _build_stats(event: Message | CallbackQuery, period_days: int):
    runtime = get_runtime()
    with session_scope(runtime.session_factory) as session:
        user = authorize(session, runtime.settings, event.from_user.id)
        today = user_today(user.timezone)
        start = today - timedelta(days=period_days - 1)
        entries = DailyEntryRepository(session).list_in_range(user.id, start, today)
    stats = stats_service.compute_stats(entries, period_days=period_days, today=today)
    return _render(user, stats), keyboards.stats_keyboard(period_days)


@router.message(Command("stats"))
@router.message(F.text == keyboards.reply.BTN_STATS)
async def cmd_stats(message: Message) -> None:
    text, kb = await _build_stats(message, _DEFAULT_PERIOD)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("st:"))
async def cb_stats(cb: CallbackQuery) -> None:
    period = int(cb.data.split(":")[1])  # type: ignore[union-attr]
    text, kb = await _build_stats(cb, period)
    await cb.answer()
    if cb.message:
        await cb.message.edit_text(text, reply_markup=kb)
