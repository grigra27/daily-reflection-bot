"""Scheduler-logic tests (baseline section 48, Scheduler logic group).

The job bodies are exercised directly with a fake bot, so no real Telegram
token or network access is needed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.database.models import DailyEntry, User
from app.database.session import session_scope
from app.scheduler.scheduler import ReflectionScheduler
from app.services.time_service import user_today


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))


def _mk_user(session: Session, tg_id: int = 111) -> User:
    u = User(telegram_user_id=tg_id, timezone="UTC", checkin_time="21:30", reminder_time="23:00")
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _entry_exists(session_factory, user_pk: int) -> None:
    with session_scope(session_factory) as s:
        s.add(
            DailyEntry(
                user_id=user_pk,
                entry_date=user_today("UTC"),
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


async def test_two_repeating_jobs_per_user_not_per_day(session, session_factory, fake_bot) -> None:
    u = _mk_user(session)
    sched = ReflectionScheduler(session_factory, fake_bot)  # type: ignore[arg-type]
    sched.apscheduler.start()
    try:
        sched.sync_user(u)
        jobs = {j.id for j in sched.apscheduler.get_jobs()}
        assert jobs == {f"checkin:{u.id}", f"reminder:{u.id}"}
        # Re-syncing (settings change) must replace, not accumulate.
        sched.sync_user(u)
        assert len(sched.apscheduler.get_jobs()) == 2
    finally:
        sched.apscheduler.shutdown(wait=False)
