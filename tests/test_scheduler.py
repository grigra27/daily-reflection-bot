"""Scheduler-logic tests (baseline section 48, Scheduler logic group).

The job bodies are exercised directly with a fake bot, so no real Telegram
token or network access is needed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.database.models import DailyEntry, MorningIntent, User
from app.database.session import session_scope
from app.scheduler.scheduler import ReflectionScheduler
from app.services.time_service import reflection_day


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))


def _mk_user(session: Session, tg_id: int = 111) -> User:
    u = User(
        telegram_user_id=tg_id,
        timezone="UTC",
        checkin_time="21:30",
        reminder_time="23:00",
        morning_time="08:30",
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _entry_exists(session_factory, user_pk: int) -> None:
    with session_scope(session_factory) as s:
        s.add(
            DailyEntry(
                user_id=user_pk,
                entry_date=reflection_day("UTC"),
                day_score=4, mood_score=4, energy_score=4,
            )
        )
        s.commit()


@pytest.fixture
def fake_bot() -> FakeBot:
    return FakeBot()


async def test_checkin_sent_when_no_entry(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._daily_job(u.id)
    assert len(fake_bot.sent) == 1
    assert fake_bot.sent[0][0] == u.telegram_user_id


async def test_checkin_skipped_when_entry_exists(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    _entry_exists(session_factory, u.id)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._daily_job(u.id)
    assert fake_bot.sent == []


async def test_reminder_only_when_missing(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._reminder_job(u.id)
    assert len(fake_bot.sent) == 1

    u2 = _mk_user(session, tg_id=222)
    _entry_exists(session_factory, u2.id)
    await sched._reminder_job(u2.id)
    assert len(fake_bot.sent) == 1  # still one — no reminder for u2


def _intent_exists(session_factory, user_pk: int) -> None:
    with session_scope(session_factory) as s:
        s.add(
            MorningIntent(
                user_id=user_pk,
                intention_date=reflection_day("UTC"),
                main_intention="focus",
            )
        )
        s.commit()


# --------------------------------------------------------------------------
# Morning prompt job (v1.1)
# --------------------------------------------------------------------------
async def test_morning_sent_when_no_intent(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._morning_job(u.id)
    assert len(fake_bot.sent) == 1
    assert fake_bot.sent[0][0] == u.telegram_user_id
    assert "Доброе утро" in fake_bot.sent[0][1]


async def test_morning_skipped_when_intent_exists(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    _intent_exists(session_factory, u.id)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._morning_job(u.id)
    assert fake_bot.sent == []


async def test_morning_job_uses_user_local_today(session, session_factory, fake_bot) -> None:
    # An intent written for the user's OWN today (Moscow) must suppress the
    # prompt even though the server clock could differ.
    u = _mk_user(session)
    u.timezone = "Europe/Moscow"
    session.commit()
    with session_scope(session_factory) as s:
        s.add(
            MorningIntent(
                user_id=u.id,
                intention_date=reflection_day("Europe/Moscow"),
                main_intention="focus",
            )
        )
        s.commit()
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._morning_job(u.id)
    assert fake_bot.sent == []


async def test_no_morning_reminder_job_exists(session, session_factory, fake_bot) -> None:
    # Spec: morning has NO second reminder — exactly three jobs, none named
    # morning-reminder.
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        ids = {j.id for j in sched.apscheduler.get_jobs()}
        assert not any("reminder" in i and "morning" in i for i in ids)
        assert {i for i in ids if i.startswith("morning")} == {f"morning:{u.id}"}
    finally:
        sched.apscheduler.shutdown(wait=False)


async def test_inactive_user_gets_no_morning_prompt(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    u.is_active = False
    session.commit()
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._morning_job(u.id)
    assert fake_bot.sent == []


async def test_evening_prompt_shows_morning_context(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    _intent_exists(session_factory, u.id)  # "focus" intention today
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._daily_job(u.id)
    assert len(fake_bot.sent) == 1
    assert "Утром ты планировал:" in fake_bot.sent[0][1]
    assert "🎯 Главное: focus" in fake_bot.sent[0][1]


async def test_evening_prompt_without_intent_is_v1_header(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    await sched._daily_job(u.id)
    assert len(fake_bot.sent) == 1
    assert "Утром ты планировал" not in fake_bot.sent[0][1]


async def test_reschedule_updates_morning_time(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        job = sched.apscheduler.get_job(f"morning:{u.id}")
        assert "hour='8', minute='30'" in str(job.trigger)
        u.morning_time = "07:15"
        session.commit()
        sched.reschedule_user(u.id)
        job2 = sched.apscheduler.get_job(f"morning:{u.id}")
        assert "hour='7', minute='15'" in str(job2.trigger)
        # Still exactly three jobs, no accumulation.
        assert len(sched.apscheduler.get_jobs()) == 3
    finally:
        sched.apscheduler.shutdown(wait=False)


async def test_three_repeating_jobs_per_user_not_per_day(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        jobs = {j.id for j in sched.apscheduler.get_jobs()}
        assert jobs == {f"morning:{u.id}", f"checkin:{u.id}", f"reminder:{u.id}"}
        # Re-syncing (settings change) must replace, not accumulate.
        sched.sync_user(u)
        assert len(sched.apscheduler.get_jobs()) == 3
    finally:
        sched.apscheduler.shutdown(wait=False)
