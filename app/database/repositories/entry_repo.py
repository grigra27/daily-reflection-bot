"""DailyEntry repository.

``upsert`` guarantees the "one entry per user per local date" rule (baseline
section 11) and safely handles re-submits / double taps: editing updates the
existing row instead of inserting a duplicate.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import QUESTIONNAIRE_VERSION, DailyEntry


class DailyEntryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, entry_date: date) -> DailyEntry | None:
        stmt = select(DailyEntry).where(
            DailyEntry.user_id == user_id, DailyEntry.entry_date == entry_date
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def upsert(
        self,
        *,
        user_id: int,
        entry_date: date,
        day_score: int,
        mood_score: int,
        energy_score: int,
        reflection_text: str | None,
    ) -> DailyEntry:
        entry = self.get(user_id, entry_date)
        if entry is None:
            entry = DailyEntry(
                user_id=user_id,
                entry_date=entry_date,
                day_score=day_score,
                mood_score=mood_score,
                energy_score=energy_score,
                reflection_text=reflection_text,
                questionnaire_version=QUESTIONNAIRE_VERSION,
            )
            self._session.add(entry)
        else:
            entry.day_score = day_score
            entry.mood_score = mood_score
            entry.energy_score = energy_score
            entry.reflection_text = reflection_text
        self._session.commit()
        self._session.refresh(entry)
        return entry

    def list_in_range(self, user_id: int, start: date, end: date) -> list[DailyEntry]:
        stmt = (
            select(DailyEntry)
            .where(
                DailyEntry.user_id == user_id,
                DailyEntry.entry_date >= start,
                DailyEntry.entry_date <= end,
            )
            .order_by(DailyEntry.entry_date)
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_all(self, user_id: int) -> list[DailyEntry]:
        stmt = (
            select(DailyEntry)
            .where(DailyEntry.user_id == user_id)
            .order_by(DailyEntry.entry_date)
        )
        return list(self._session.execute(stmt).scalars().all())
