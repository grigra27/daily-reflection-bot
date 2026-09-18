"""Database-layer tests (baseline section 48, Database group)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import DailyEntry, User
from app.database.repositories import DailyEntryRepository, UserRepository


def test_create_user(session: Session) -> None:
    repo = UserRepository(session)
    u = repo.get_or_create(42, display_name="X")
    assert u.id is not None
    assert u.telegram_user_id == 42
    assert u.checkin_time == "21:30"
    assert u.reminder_time == "23:00"


def test_get_or_create_is_idempotent(session: Session) -> None:
    repo = UserRepository(session)
    a = repo.get_or_create(42)
    b = repo.get_or_create(42)
    assert a.id == b.id


def test_telegram_id_unique(session: Session) -> None:
    session.add(User(telegram_user_id=7, timezone="UTC"))
    session.commit()
    session.add(User(telegram_user_id=7, timezone="UTC"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_create_daily_entry(session: Session, user: User) -> None:
    repo = DailyEntryRepository(session)
    entry = repo.upsert(
        user_id=user.id,
        entry_date=date(2026, 9, 18),
        day_score=4,
        mood_score=3,
        energy_score=2,
        reflection_text="ok",
    )
    assert entry.id is not None
    assert entry.questionnaire_version == 1


def test_one_entry_per_day(session: Session, user: User) -> None:
    # A second explicit row for the same user+date must violate UNIQUE.
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=date(2026, 9, 1),
            day_score=1, mood_score=1, energy_score=1,
        )
    )
    session.commit()
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=date(2026, 9, 1),
            day_score=5, mood_score=5, energy_score=5,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_upsert_updates_existing(session: Session, user: User) -> None:
    repo = DailyEntryRepository(session)
    first = repo.upsert(
        user_id=user.id, entry_date=date(2026, 9, 2),
        day_score=1, mood_score=1, energy_score=1, reflection_text=None,
    )
    second = repo.upsert(
        user_id=user.id, entry_date=date(2026, 9, 2),
        day_score=5, mood_score=5, energy_score=5, reflection_text="edited",
    )
    assert first.id == second.id  # no duplicate row created
    assert second.day_score == 5
    assert second.reflection_text == "edited"


def test_score_check_constraint(session: Session, user: User) -> None:
    session.add(
        DailyEntry(
            user_id=user.id, entry_date=date(2026, 9, 3),
            day_score=6, mood_score=1, energy_score=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
