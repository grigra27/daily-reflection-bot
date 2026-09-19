"""Application entrypoint: wires the bot, dispatcher, scheduler and storage."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from app.bot import texts
from app.bot.handlers import build_root_router
from app.config import get_settings
from app.logging_conf import configure_logging
from app.runtime import build_runtime, get_runtime, init_runtime
from app.scheduler.scheduler import ReflectionScheduler

logger = logging.getLogger("app.main")

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="morning", description="Фокус дня"),
    BotCommand(command="checkin", description="Итоги дня"),
    BotCommand(command="today", description="Сегодняшний снимок"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="export", description="Экспорт CSV"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Справка"),
]


async def _on_startup(bot: Bot) -> None:
    runtime = get_runtime()
    await bot.set_my_commands(COMMANDS)
    assert runtime.scheduler is not None
    runtime.scheduler.start()
    logger.info("Daily Reflection Bot started (allowed users: %d)", len(runtime.settings.allowed_telegram_ids))


async def _on_shutdown(bot: Bot) -> None:
    runtime = get_runtime()
    if runtime.scheduler is not None:
        runtime.scheduler.shutdown()
    logger.info("Daily Reflection Bot stopped")


async def _on_error(event: ErrorEvent) -> None:
    logger.exception("Unhandled error", exc_info=event.exception)
    if event.update.callback_query and event.update.callback_query.message:
        try:
            await event.update.callback_query.message.answer(texts.GENERAL_ERROR)
        except TelegramAPIError:
            pass
    elif event.update.message:
        try:
            await event.update.message.answer(texts.GENERAL_ERROR)
        except TelegramAPIError:
            pass


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not settings.allowed_telegram_ids:
        raise RuntimeError("ALLOWED_TELEGRAM_IDS is empty. Add at least one Telegram user id.")

    runtime = build_runtime(settings)
    init_runtime(runtime)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_root_router())
    dp.errors.register(_on_error)

    runtime.bot = bot
    runtime.scheduler = ReflectionScheduler(runtime.session_factory, bot)
    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
