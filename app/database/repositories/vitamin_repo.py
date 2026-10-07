"""VitaminLog repository (v1.3.2).

Database-only, like the other repositories: ``mark_taken`` keeps at most one
row per user per day and is safe under a double tap — the UNIQUE constraint is
the authority, not a check-then-insert race. An existing row is returned
untouched, so the *first* confirmation stays the recorded time. Business rules
(which days a button may still write) live in ``vitamin_service``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import VitaminLog


class VitaminLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, vitamin_date: date) -> VitaminLog | None:
        stmt = select(VitaminLog).where(
            VitaminLog.user_id == user_id, VitaminLog.vitamin_date == vitamin_date
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def mark_taken(self, *, user_id: int, vitamin_date: date, taken_at: datetime) -> VitaminLog:
        existing = self.get(user_id, vitamin_date)
        if existing is not None:
            return existing
        log = VitaminLog(user_id=user_id, vitamin_date=vitamin_date, taken_at=taken_at)
        self._session.add(log)
        try:
            self._session.flush()
        except IntegrityError:
            # A concurrent tap created the row first: keep that one, with its
            # original taken_at.
            self._session.rollback()
            log = self.get(user_id, vitamin_date)
            assert log is not None
            return log
        self._session.commit()
        self._session.refresh(log)
        return log
