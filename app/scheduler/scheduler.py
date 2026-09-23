"""Scheduler for morning intentions, daily check-ins and reminders.

Uses a repeating cron trigger **per user and per job kind** (never a
manually-enumerated job per calendar day — baseline section 38) evaluated in
that user's own timezone. Jobs are (re)built from the database on startup, so
the schedule automatically recovers after a restart. Business decisions (does
an entry or intent already exist?) are made through the service layer; this
module only decides *whether* to hand off to the notifier and never computes
statistics or writes entries.
"""

from __future__ import annotations

import logging
from datetime import time

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.bot import notifications
from app.database.models import User
from app.database.repositories import UserRepository
from app.database.session import session_scope
from app.services.checkin_service import has_entry_today
from app.services.morning_service import get_intent, has_intent_today
from app.services.time_service import reflection_day

logger = logging.getLogger("app.scheduler")


class ReflectionScheduler:
    def __init__(self, session_factory: sessionmaker[Session], bot: Bot) -> None:
        self._session_factory = session_factory
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    # -- job bodies ---------------------------------------------------------
    async def _morning_job(self, user_pk: int) -> None:
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is None or not user.is_active:
                return
            already_done = has_intent_today(session, user)
            chat_id = user.telegram_user_id
        if already_done:
            logger.info("Morning prompt skipped for user %s: intent exists today", user_pk)
            return
        await notifications.send_morning_prompt(self._bot, chat_id)

    async def _daily_job(self, user_pk: int) -> None:
        """The evening prompt asks for the first step the day still owes:
        outcomes are already partly saved whenever the user tapped and stopped,
        so the notifier (``evening_flow``) picks the right question. The job
        never creates FSM state — the buttons carry the target date instead."""
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is None or not user.is_active:
                return
            already_done = has_entry_today(session, user)
            chat_id = user.telegram_user_id
            target_date = reflection_day(user.timezone)
            intent = None if already_done else get_intent(session, user, target_date)
        if already_done:
            logger.info("Check-in skipped for user %s: entry exists today", user_pk)
            return
        await notifications.send_checkin_prompt(
            self._bot, chat_id, intent=intent, target_date=target_date
        )

    async def _reminder_job(self, user_pk: int) -> None:
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is None or not user.is_active:
                return
            already_done = has_entry_today(session, user)
            chat_id = user.telegram_user_id
        if already_done:
            logger.info("Reminder skipped for user %s: entry exists today", user_pk)
            return
        await notifications.send_reminder(self._bot, chat_id)

    # -- job management -----------------------------------------------------
    def _add_job(self, user: User, job_id: str, func, value: str) -> None:
        hour, minute = _parse_hhmm(value)
        trigger = CronTrigger(hour=hour, minute=minute, timezone=user.timezone)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            kwargs={"user_pk": user.id},
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )

    def sync_user(self, user: User) -> None:
        """(Re)register this user's three repeating jobs. Safe to call repeatedly."""
        self._add_job(user, f"morning:{user.id}", self._morning_job, user.morning_time)
        self._add_job(user, f"checkin:{user.id}", self._daily_job, user.checkin_time)
        self._add_job(user, f"reminder:{user.id}", self._reminder_job, user.reminder_time)
        logger.info(
            "Scheduled user %s: morning %s, check-in %s, reminder %s (%s)",
            user.id,
            user.morning_time,
            user.checkin_time,
            user.reminder_time,
            user.timezone,
        )

    def reschedule_user(self, user_pk: int) -> None:
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is not None:
                self.sync_user(user)

    def sync_all(self) -> None:
        with session_scope(self._session_factory) as session:
            for user in UserRepository(session).list_all():
                self.sync_user(user)

    def start(self) -> None:
        self.sync_all()
        self._scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    # exposed for tests
    @property
    def apscheduler(self) -> AsyncIOScheduler:
        return self._scheduler


def _parse_hhmm(value: str) -> tuple[int, int]:
    # Validate via the canonical parser, then coerce through datetime.time.
    hour, minute = [int(p) for p in value.split(":")]
    time(hour, minute)  # raises ValueError on out-of-range
    return hour, minute
