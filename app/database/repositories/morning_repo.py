"""MorningIntent repository.

Mirrors ``DailyEntryRepository``: ``upsert`` guarantees one row per user per
local date and is race-safe against the UNIQUE(user_id, intention_date)
constraint. ``_UNCHANGED`` lets callers update only one of the two intention
fields without clobbering the other (step 1 saves main while secondary stays
untouched; a later skip explicitly sets secondary to None). ``set_outcome`` is
the separate, narrow write used by the v1.2 evening closure: one outcome column
on an existing row, never the texts and never a new row.
"""

from __future__ import annotations

from datetime import date
from typing import final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import MorningIntent


@final
class _Unchanged:
    """Sentinel type: a private object, so no user-supplied text can ever
    collide with it the way a magic string could."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNCHANGED"


_UNCHANGED = _Unchanged()
type _OptIntention = str | None | _Unchanged
#: Outcome columns take the same sentinel, so closing one intention can never
#: blank out the other one's outcome.
type _OptOutcome = str | None | _Unchanged


class MorningIntentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, intention_date: date) -> MorningIntent | None:
        stmt = select(MorningIntent).where(
            MorningIntent.user_id == user_id,
            MorningIntent.intention_date == intention_date,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def upsert(
        self,
        *,
        user_id: int,
        intention_date: date,
        main_intention: _OptIntention = _UNCHANGED,
        secondary_intention: _OptIntention = _UNCHANGED,
    ) -> MorningIntent:
        intent = self.get(user_id, intention_date)
        if intent is not None:
            self._assign(intent, main_intention, secondary_intention)
        else:
            if main_intention is _UNCHANGED or main_intention is None:
                raise ValueError("main_intention is required when creating a MorningIntent")
            intent = MorningIntent(
                user_id=user_id,
                intention_date=intention_date,
                main_intention=main_intention,
                secondary_intention=None
                if secondary_intention is _UNCHANGED
                else secondary_intention,
            )
            self._session.add(intent)
            try:
                self._session.flush()
            except IntegrityError:
                # A concurrent request won the UNIQUE(user_id, intention_date)
                # race: discard our INSERT and update the row that exists.
                self._session.rollback()
                intent = self.get(user_id, intention_date)
                assert intent is not None
                self._assign(intent, main_intention, secondary_intention)
        self._session.commit()
        self._session.refresh(intent)
        return intent

    @staticmethod
    def _assign(
        intent: MorningIntent,
        main_intention: _OptIntention,
        secondary_intention: _OptIntention,
    ) -> None:
        if main_intention is not _UNCHANGED:
            if main_intention is None:
                raise ValueError("main_intention cannot be cleared")
            intent.main_intention = main_intention
        if secondary_intention is not _UNCHANGED:
            intent.secondary_intention = secondary_intention

    def set_outcome(
        self,
        *,
        user_id: int,
        intention_date: date,
        main_outcome: _OptOutcome = _UNCHANGED,
        secondary_outcome: _OptOutcome = _UNCHANGED,
    ) -> MorningIntent | None:
        """Evening closure write (v1.2): touch only the outcome column the
        caller passed — never an intention text, never a new row. Returns None
        when the row is missing so the service can report it. Re-writing the
        same value leaves the row untouched, which makes double taps
        idempotent."""
        intent = self.get(user_id, intention_date)
        if intent is None:
            return None
        self._assign_outcomes(intent, main_outcome, secondary_outcome)
        self._session.commit()
        self._session.refresh(intent)
        return intent

    @staticmethod
    def _assign_outcomes(
        intent: MorningIntent,
        main_outcome: _OptOutcome,
        secondary_outcome: _OptOutcome,
    ) -> None:
        if main_outcome is not _UNCHANGED:
            intent.main_outcome = main_outcome
        if secondary_outcome is not _UNCHANGED:
            intent.secondary_outcome = secondary_outcome

    def list_in_range(self, user_id: int, start: date, end: date) -> list[MorningIntent]:
        stmt = (
            select(MorningIntent)
            .where(
                MorningIntent.user_id == user_id,
                MorningIntent.intention_date >= start,
                MorningIntent.intention_date <= end,
            )
            .order_by(MorningIntent.intention_date)
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_all(self, user_id: int) -> list[MorningIntent]:
        stmt = (
            select(MorningIntent)
            .where(MorningIntent.user_id == user_id)
            .order_by(MorningIntent.intention_date)
        )
        return list(self._session.execute(stmt).scalars().all())
