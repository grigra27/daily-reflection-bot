"""Outbound Telegram messages initiated by the scheduler.

The scheduler decides *whether* to notify (via the service layer); this module
only formats and sends. Messages go to the user's private chat, whose chat id
equals their Telegram user id.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot import keyboards, texts

logger = logging.getLogger("app.notifications")


async def send_checkin_prompt(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_message(chat_id, texts.CHECKIN_HEADER, reply_markup=keyboards.day_keyboard())
        logger.info("Sent daily check-in prompt to chat %s", chat_id)
    except TelegramAPIError:
        logger.exception("Failed to send check-in prompt to chat %s", chat_id)


async def send_reminder(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_message(chat_id, texts.REMINDER, reply_markup=keyboards.reminder_keyboard())
        logger.info("Sent reminder to chat %s", chat_id)
    except TelegramAPIError:
        logger.exception("Failed to send reminder to chat %s", chat_id)
