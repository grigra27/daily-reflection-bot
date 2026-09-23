"""Outbound Telegram messages initiated by the scheduler.

The scheduler decides *whether* to notify (via the service layer); this module
only formats and sends. Messages go to the user's private chat, whose chat id
equals their Telegram user id.

A notification never touches the FSM — it is a message sitting in a chat that
may be tapped hours later, after a restart. That is why every button it carries
is built with the target Reflection Day baked in (``evening_flow``).
"""

from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot import evening_flow, keyboards, texts
from app.database.models import MorningIntent

logger = logging.getLogger("app.notifications")


async def send_morning_prompt(bot: Bot, chat_id: int) -> None:
    """Sunrise prompt only — the FSM starts when the user taps the button,
    never from this notification. There is deliberately no morning reminder.
    """
    try:
        await bot.send_message(
            chat_id, texts.MORNING_PROMPT, reply_markup=keyboards.morning_prompt_keyboard()
        )
        logger.info("Sent morning prompt to chat %s", chat_id)
    except TelegramAPIError:
        logger.exception("Failed to send morning prompt to chat %s", chat_id)


async def send_checkin_prompt(
    bot: Bot, chat_id: int, *, intent: MorningIntent | None, target_date: date
) -> None:
    """The evening prompt: the first still-open step for that day — closing
    the main intention, then the secondary one, then the day ratings (or
    straight to the ratings when there is no morning intention at all)."""
    prompt = evening_flow.build_evening_prompt(intent, target_date)
    try:
        await bot.send_message(chat_id, prompt.text, reply_markup=prompt.markup)
        logger.info("Sent daily check-in prompt to chat %s", chat_id)
    except TelegramAPIError:
        logger.exception("Failed to send check-in prompt to chat %s", chat_id)


async def send_reminder(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_message(chat_id, texts.REMINDER, reply_markup=keyboards.reminder_keyboard())
        logger.info("Sent reminder to chat %s", chat_id)
    except TelegramAPIError:
        logger.exception("Failed to send reminder to chat %s", chat_id)
