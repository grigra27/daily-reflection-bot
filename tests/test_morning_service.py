"""MorningIntent repository + service tests (v1.1, spec section 27)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import MorningIntent, User
from app.database.repositories import MorningIntentRepository
from app.services import morning_service
from app.services.morning_service import MorningValidationError
from app.services.time_service import user_today


def _mk_user(session: Session, tg_id: int = 111) -> User:
    u = User(telegram_user_id=tg_id, timezone="UTC")
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


# --------------------------------------------------------------------------
# Model / repository
# --------------------------------------------------------------------------
def test_unique_user_date(session: Session, user: User) -> None:
    session.add(
        MorningIntent(
            user_id=user.id, intention_date=date(2026, 9, 19), main_intention="A"
        )
    )
    session.commit()
    session.add(
        MorningIntent(
            user_id=user.id, intention_date=date(2026, 9, 19), main_intention="B"
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_upsert_creates_then_edits_same_row(session: Session, user: User) -> None:
    repo = MorningIntentRepository(session)
    first = repo.upsert(
        user_id=user.id, intention_date=date(2026, 9, 19), main_intention="A"
    )
    second = repo.upsert(
        user_id=user.id,
        intention_date=date(2026, 9, 19),
        main_intention="B",
        secondary_intention="C",
    )
    assert first.id == second.id
    assert second.main_intention == "B"
    assert second.secondary_intention == "C"
    assert session.query(MorningIntent).count() == 1


def test_partial_upsert_keeps_untouched_field(session: Session, user: User) -> None:
    repo = MorningIntentRepository(session)
    repo.upsert(
        user_id=user.id,
        intention_date=date(2026, 9, 19),
        main_intention="A",
        secondary_intention="S",
    )
    # Step 1 of a re-run saves main only; secondary must survive untouched.
    updated = repo.upsert(
        user_id=user.id, intention_date=date(2026, 9, 19), main_intention="A2"
    )
    assert updated.main_intention == "A2"
    assert updated.secondary_intention == "S"
    # An explicit None clears it (skip in the edit flow).
    cleared = repo.upsert(
        user_id=user.id,
        intention_date=date(2026, 9, 19),
        secondary_intention=None,
    )
    assert cleared.secondary_intention is None


def test_upsert_survives_concurrent_insert(session: Session, user: User, monkeypatch) -> None:
    # Double-tap race: INSERT hits UNIQUE(user_id, intention_date); the repo
    # must roll back and update the row that actually exists.
    repo = MorningIntentRepository(session)
    winner = repo.upsert(
        user_id=user.id, intention_date=date(2026, 9, 19), main_intention="A"
    )
    original_get = MorningIntentRepository.get
    calls = {"n": 0}

    def racy_get(self, user_id, intention_date):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original_get(self, user_id, intention_date)

    monkeypatch.setattr(MorningIntentRepository, "get", racy_get)
    loser = repo.upsert(
        user_id=user.id, intention_date=date(2026, 9, 19), main_intention="B"
    )
    assert loser.id == winner.id
    assert loser.main_intention == "B"
    assert calls["n"] >= 2


def test_creating_without_main_is_rejected(session: Session, user: User) -> None:
    repo = MorningIntentRepository(session)
    with pytest.raises(ValueError):
        repo.upsert(user_id=user.id, intention_date=date(2026, 9, 19))
    session.rollback()


def test_delete_user_cascades_morning_intents(session: Session) -> None:
    u = _mk_user(session)
    session.add(
        MorningIntent(user_id=u.id, intention_date=date(2026, 9, 19), main_intention="A")
    )
    session.commit()
    session.delete(u)
    session.commit()
    assert session.query(MorningIntent).count() == 0


# --------------------------------------------------------------------------
# Service: validation + user-local date
# --------------------------------------------------------------------------
def test_main_validation(session: Session, user: User) -> None:
    with pytest.raises(MorningValidationError):
        morning_service.save_main_intention(session, user, "   ")
    with pytest.raises(MorningValidationError):
        morning_service.save_main_intention(session, user, "")
    intent = morning_service.save_main_intention(session, user, "  закончить отчёт  ")
    assert intent.main_intention == "закончить отчёт"  # trimmed, not mutated further


def test_length_cap(session: Session, user: User) -> None:
    intent = morning_service.save_main_intention(
        session, user, "x" * (morning_service.MAX_INTENTION_LENGTH + 50)
    )
    assert len(intent.main_intention) == morning_service.MAX_INTENTION_LENGTH
    updated = morning_service.set_secondary(
        session, user, "y" * (morning_service.MAX_INTENTION_LENGTH + 50)
    )
    assert len(updated.secondary_intention or "") == morning_service.MAX_INTENTION_LENGTH


def test_secondary_trims_empty_to_none(session: Session, user: User) -> None:
    morning_service.save_main_intention(session, user, "A")
    intent = morning_service.set_secondary(session, user, "   ")
    assert intent.secondary_intention is None
    intent = morning_service.set_secondary(session, user, " в зал ")
    assert intent.secondary_intention == "в зал"
    intent = morning_service.set_secondary(session, user, None)  # skip
    assert intent.secondary_intention is None


def test_set_secondary_requires_existing_row(session: Session, user: User) -> None:
    with pytest.raises(MorningValidationError):
        morning_service.set_secondary(session, user, "orphan")


def test_dates_use_user_local_today(session: Session) -> None:
    u = _mk_user(session)
    intent = morning_service.save_main_intention(session, u, "A")
    assert intent.intention_date == user_today("UTC")
    assert morning_service.has_intent_today(session, u)
    assert morning_service.get_intent(session, u) is not None
