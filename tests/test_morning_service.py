"""MorningIntent repository + service tests (v1.1, spec section 27; v1.2 outcomes)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import DailyEntry, MorningIntent, User
from app.database.repositories import MorningIntentRepository
from app.services import morning_service
from app.services.morning_service import MorningValidationError
from app.services.time_service import reflection_day


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


def test_literal_sentinel_text_is_stored_as_data(session: Session, user: User) -> None:
    # The "keep this field" marker is a private object, so a user who really
    # types the old magic string gets it stored instead of silently ignored.
    repo = MorningIntentRepository(session)
    repo.upsert(
        user_id=user.id,
        intention_date=date(2026, 9, 19),
        main_intention="A",
        secondary_intention="S",
    )
    updated = repo.upsert(
        user_id=user.id, intention_date=date(2026, 9, 19), main_intention="unchanged"
    )
    assert updated.main_intention == "unchanged"
    assert updated.secondary_intention == "S"


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


def test_dates_use_user_reflection_day(session: Session) -> None:
    u = _mk_user(session)
    intent = morning_service.save_main_intention(session, u, "A")
    # v1.1.1: default date is the logical Reflection Day, not calendar today.
    assert intent.intention_date == reflection_day("UTC")
    assert morning_service.has_intent_today(session, u)
    assert morning_service.get_intent(session, u) is not None


# --------------------------------------------------------------------------
# v1.2 outcome persistence: canonical vocabulary and narrow writes
# --------------------------------------------------------------------------
#: A fixed Reflection Day for the pure (no-database) rules below.
ANY_DAY = date(2026, 9, 19)


def test_outcome_vocabulary_is_exactly_three() -> None:
    assert morning_service.OUTCOMES == ("done", "partial", "not_done")
    assert morning_service.OUTCOME_FIELDS == ("main", "secondary")


def test_record_outcome_accepts_only_the_three_canonical_values(
    session: Session, user: User
) -> None:
    morning_service.save_main_intention(session, user, "A")
    for bad in ("перенёс", "carry_over", "yes", "DONE", "", "true", "4"):
        with pytest.raises(MorningValidationError):
            morning_service.record_outcome(
                session, user, outcome_field="main", outcome=bad
            )
    row = morning_service.get_intent(session, user)
    assert row is not None and row.main_outcome is None  # nothing half-written
    for good in morning_service.OUTCOMES:
        row = morning_service.record_outcome(
            session, user, outcome_field="main", outcome=good
        )
        assert row.main_outcome == good


def test_record_outcome_rejects_unknown_field(session: Session, user: User) -> None:
    morning_service.save_main_intention(session, user, "A")
    with pytest.raises(MorningValidationError):
        morning_service.record_outcome(
            session, user, outcome_field="third", outcome="done"
        )


def test_record_outcome_writes_only_its_own_column(session: Session, user: User) -> None:
    morning_service.save_main_intention(session, user, "A")
    morning_service.set_secondary(session, user, "S")
    before = morning_service.get_intent(session, user)
    assert before is not None
    before_created, before_id = before.created_at, before.id

    main = morning_service.record_outcome(
        session, user, outcome_field="main", outcome="partial"
    )
    assert (main.main_outcome, main.secondary_outcome) == ("partial", None)
    # The intention texts themselves are never touched by an outcome tap.
    assert (main.main_intention, main.secondary_intention) == ("A", "S")
    assert main.id == before_id  # the same row, not a new one

    both = morning_service.record_outcome(
        session, user, outcome_field="secondary", outcome="not_done"
    )
    assert (both.main_outcome, both.secondary_outcome) == ("partial", "not_done")
    assert both.created_at == before_created


def test_record_outcome_is_idempotent_and_creates_no_row(session: Session, user: User) -> None:
    morning_service.save_main_intention(session, user, "A")
    for _ in range(3):
        row = morning_service.record_outcome(
            session, user, outcome_field="main", outcome="done"
        )
        assert row.main_outcome == "done"
    assert session.query(MorningIntent).count() == 1


def test_record_outcome_requires_an_existing_morning_row(session: Session, user: User) -> None:
    with pytest.raises(MorningValidationError):
        morning_service.record_outcome(
            session, user, outcome_field="main", outcome="done"
        )
    assert session.query(MorningIntent).count() == 0


def test_secondary_outcome_needs_a_secondary_intention(session: Session, user: User) -> None:
    morning_service.save_main_intention(session, user, "A")
    with pytest.raises(MorningValidationError):
        morning_service.record_outcome(
            session, user, outcome_field="secondary", outcome="done"
        )
    row = morning_service.get_intent(session, user)
    assert row is not None and row.secondary_outcome is None


def test_record_outcome_is_independent_of_the_daily_entry(session: Session, user: User) -> None:
    # An outcome says nothing about the day scores: they are separate facts and
    # neither one is derived from the other.
    morning_service.save_main_intention(session, user, "A")
    morning_service.record_outcome(session, user, outcome_field="main", outcome="done")
    assert session.query(DailyEntry).count() == 0


# --------------------------------------------------------------------------
# v1.2 morning lock
# --------------------------------------------------------------------------
def _row(**kwargs) -> MorningIntent:
    """A detached row: ``lock_for`` and ``next_evening_step`` are pure rules."""
    return MorningIntent(user_id=1, intention_date=ANY_DAY, main_intention="A", **kwargs)


def test_lock_rule_matches_the_documented_states() -> None:
    Lock = morning_service.MorningLock
    entry = DailyEntry(
        user_id=1, entry_date=ANY_DAY, day_score=4, mood_score=4, energy_score=4
    )
    assert morning_service.lock_for(None, None) is None
    assert morning_service.lock_for(_row(), None) is None
    assert morning_service.lock_for(_row(main_outcome="partial"), None) is Lock.OUTCOME_RECORDED
    assert (
        morning_service.lock_for(_row(secondary_outcome="done"), None) is Lock.OUTCOME_RECORDED
    )
    # Historical pre-v1.2 shape: an entry exists while both outcomes are NULL.
    assert morning_service.lock_for(_row(), entry) is Lock.DAY_FILLED
    # The filled-day reason wins when both apply — it is the more useful message.
    assert morning_service.lock_for(_row(main_outcome="done"), entry) is Lock.DAY_FILLED
    # No intention at all: there is no plan that could be rewritten, so nothing
    # is locked (an evening-only day can still get a morning record).
    assert morning_service.lock_for(None, entry) is None


def test_service_refuses_morning_text_writes_once_an_outcome_exists(
    session: Session, user: User
) -> None:
    morning_service.save_main_intention(session, user, "A")
    morning_service.set_secondary(session, user, "S")
    morning_service.record_outcome(session, user, outcome_field="main", outcome="done")
    for call in (
        lambda: morning_service.save_main_intention(session, user, "переписано"),
        lambda: morning_service.set_secondary(session, user, "переписано"),
    ):
        with pytest.raises(morning_service.MorningLockedError) as exc:
            call()
        assert exc.value.lock is morning_service.MorningLock.OUTCOME_RECORDED
    row = morning_service.get_intent(session, user)
    assert row is not None
    assert (row.main_intention, row.secondary_intention) == ("A", "S")
    assert session.query(MorningIntent).count() == 1


def test_service_refuses_morning_text_writes_for_a_filled_historical_day(
    session: Session, user: User
) -> None:
    # A pre-v1.2 day: intention + entry, outcomes never introduced.
    morning_service.save_main_intention(session, user, "A")
    session.add(
        DailyEntry(
            user_id=user.id,
            entry_date=reflection_day(user.timezone),
            day_score=4,
            mood_score=4,
            energy_score=4,
        )
    )
    session.commit()
    with pytest.raises(morning_service.MorningLockedError) as exc:
        morning_service.save_main_intention(session, user, "переписано")
    assert exc.value.lock is morning_service.MorningLock.DAY_FILLED


def test_outcome_recording_is_still_allowed_while_locked(session: Session, user: User) -> None:
    # The lock covers the intention *texts*; the evening must still be able to
    # close the second field after the first one was answered.
    morning_service.save_main_intention(session, user, "A")
    morning_service.set_secondary(session, user, "S")
    morning_service.record_outcome(session, user, outcome_field="main", outcome="done")
    row = morning_service.record_outcome(
        session, user, outcome_field="secondary", outcome="partial"
    )
    assert row.secondary_outcome == "partial"


def test_get_lock_uses_the_reflection_day(session: Session) -> None:
    u = _mk_user(session)
    morning_service.save_main_intention(session, u, "A")
    morning_service.record_outcome(session, u, outcome_field="main", outcome="done")
    assert morning_service.get_lock(session, u) is morning_service.MorningLock.OUTCOME_RECORDED
    # A different Reflection Day is a different record and stays editable.
    assert morning_service.get_lock(session, u, intention_date=ANY_DAY) is None


# --------------------------------------------------------------------------
# v1.2: the shared evening step rule (spec 10, 20, 23)
# --------------------------------------------------------------------------
def test_next_evening_step_matrix() -> None:
    Step = morning_service.EveningStep
    assert morning_service.next_evening_step(None) is Step.DAY_SCORE
    assert morning_service.next_evening_step(_row()) is Step.MAIN_OUTCOME
    # No secondary intention -> nothing left to ask once main is answered.
    assert morning_service.next_evening_step(_row(main_outcome="done")) is Step.DAY_SCORE
    assert morning_service.next_evening_step(_row(secondary_intention="S")) is Step.MAIN_OUTCOME
    assert (
        morning_service.next_evening_step(_row(secondary_intention="S", main_outcome="partial"))
        is Step.SECONDARY_OUTCOME
    )
    assert (
        morning_service.next_evening_step(
            _row(secondary_intention="S", main_outcome="partial", secondary_outcome="done")
        )
        is Step.DAY_SCORE
    )


def test_next_evening_step_restart_rewinds_to_the_first_question() -> None:
    # act:edit on a filled day re-walks every step (spec 11)...
    Step = morning_service.EveningStep
    closed = _row(secondary_intention="S", main_outcome="done", secondary_outcome="done")
    assert morning_service.next_evening_step(closed) is Step.DAY_SCORE
    assert morning_service.next_evening_step(closed, restart=True) is Step.MAIN_OUTCOME
    # ...but a day with no morning intention has nothing to rewind to.
    assert morning_service.next_evening_step(None, restart=True) is Step.DAY_SCORE
