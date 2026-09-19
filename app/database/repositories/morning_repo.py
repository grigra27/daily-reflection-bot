"""MorningIntent repository.

Mirrors ``DailyEntryRepository``: ``upsert`` guarantees one row per user per
local date and is race-safe against the UNIQUE(user_id, intention_date)
constraint. ``_UNCHANGED`` lets callers update only one of the two intention
fields without clobbering the other (step 1 saves main while secondary stays
untouched; a later skip explicitly sets secondary to None).
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
