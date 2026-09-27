"""Scheduler for morning intentions, daily check-ins, reminders and (v1.3) the
food focus, food checklist and Sunday weight prompts.

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
from datetime import time, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session, sessionmaker

from app.bot import food_flow, notifications
from app.database.models import User
from app.database.repositories import UserRepository
from app.database.session import session_scope
from app.services import food_service
from app.services.checkin_service import has_entry_today
from app.services.morning_service import get_intent, has_intent_today
from app.services.time_service import reflection_day, week_start_date

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

    # -- food reflection (v1.3) ---------------------------------------------
    async def _food_morning_job(self, user_pk: int) -> None:
        """Morning: first a nudge for yesterday's checklist if it was left open
        (only once the feature is in use, so the first day after the upgrade
        does not complain about a day that was never asked), then today's
        focus question unless it was already answered."""
        screens: list[tuple[food_flow.Screen, str]] = []
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is None or not user.is_active:
                return
            chat_id = user.telegram_user_id
            today = reflection_day(user.timezone)
            yesterday = today - timedelta(days=1)
            prev = food_service.get_day(session, user, yesterday)
            if not food_service.is_completed(prev) and (
                prev is not None or food_service.has_completed_before(session, user, yesterday)
            ):
                screens.append(
                    (food_flow.checklist(user, prev, yesterday, today, stale=True), "reminder")
                )
            if not food_service.focus_answered(food_service.get_day(session, user, today)):
                screens.append((food_flow.focus_prompt(user, today), "morning prompt"))
        if not screens:
            logger.info("Food morning skipped for user %s: nothing open", user_pk)
        for screen, kind in screens:
            await notifications.send_food_screen(self._bot, chat_id, screen, kind)

    async def _food_evening_job(self, user_pk: int) -> None:
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is None or not user.is_active:
                return
            chat_id = user.telegram_user_id
            today = reflection_day(user.timezone)
            day = food_service.get_day(session, user, today)
            screen = (
                None
                if food_service.is_completed(day)
                else food_flow.checklist(user, day, today, today)
            )
        if screen is None:
            logger.info("Food evening skipped for user %s: day already submitted", user_pk)
            return
        await notifications.send_food_screen(self._bot, chat_id, screen, "evening prompt")

    async def _weight_job(self, user_pk: int) -> None:
        with session_scope(self._session_factory) as session:
            user = session.get(User, user_pk)
            if user is None or not user.is_active:
                return
            chat_id = user.telegram_user_id
            week = week_start_date(reflection_day(user.timezone))
            already = food_service.get_weight(session, user, week) is not None
        if already:
            logger.info("Weight prompt skipped for user %s: logged this week", user_pk)
            return
        await notifications.send_weight_prompt(self._bot, chat_id)

    # -- job management -----------------------------------------------------
    def _add_job(
        self, user: User, job_id: str, func, value: str, *, day_of_week: str | None = None
    ) -> None:
        hour, minute = _parse_hhmm(value)
        trigger = CronTrigger(
            day_of_week=day_of_week, hour=hour, minute=minute, timezone=user.timezone
        )
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
        """(Re)register this user's repeating jobs. Safe to call repeatedly."""
        self._add_job(user, f"morning:{user.id}", self._morning_job, user.morning_time)
        self._add_job(user, f"checkin:{user.id}", self._daily_job, user.checkin_time)
        self._add_job(user, f"reminder:{user.id}", self._reminder_job, user.reminder_time)
        self._add_job(
            user, f"food_morning:{user.id}", self._food_morning_job, user.food_morning_time
        )
        self._add_job(
            user, f"food_evening:{user.id}", self._food_evening_job, user.food_evening_time
        )
        self._add_job(
            user, f"weight:{user.id}", self._weight_job, user.weight_time, day_of_week="sun"
        )
        logger.info(
            "Scheduled user %s: morning %s, check-in %s, reminder %s, "
            "food %s/%s, weight sun %s (%s)",
            user.id,
            user.morning_time,
            user.checkin_time,
            user.reminder_time,
            user.food_morning_time,
            user.food_evening_time,
            user.weight_time,
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
