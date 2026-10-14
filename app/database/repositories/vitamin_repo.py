"""Vitamin repositories.

``VitaminLogRepository`` (v1.3.2) stores the legacy generic acknowledgement: one
row per user per day, meaning "the user answered that day's vitamin question".
``VitaminIntakeRepository`` (v1.3.3) stores the granular records, one row per
tapped item, and never reinterprets a legacy row as item confirmations.

Both are database-only and both make the ✅ tap idempotent the same way:
``mark_taken`` keeps at most one row per key and is safe under a double tap —
the UNIQUE constraint is the authority, not a check-then-insert race. An existing
row is returned untouched, so the *first* confirmation stays the recorded time.
Business rules (which days a button may still write) live in
``vitamin_service``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import VitaminIntake, VitaminLog


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


class VitaminIntakeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, vitamin_date: date, item_code: str) -> VitaminIntake | None:
        stmt = select(VitaminIntake).where(
            VitaminIntake.user_id == user_id,
            VitaminIntake.vitamin_date == vitamin_date,
            VitaminIntake.item_code == item_code,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def list_for_day(self, user_id: int, vitamin_date: date) -> list[VitaminIntake]:
        """Confirmed items of one day, in the order they were first tapped.

        Callers that need the catalog order sort by code themselves; the database
        has no opinion about how the items are displayed.
        """
        stmt = (
            select(VitaminIntake)
            .where(VitaminIntake.user_id == user_id, VitaminIntake.vitamin_date == vitamin_date)
            .order_by(VitaminIntake.id)
        )
        return list(self._session.execute(stmt).scalars().all())

    def mark_taken(
        self, *, user_id: int, vitamin_date: date, item_code: str, taken_at: datetime
    ) -> VitaminIntake:
        existing = self.get(user_id, vitamin_date, item_code)
        if existing is not None:
            return existing
        intake = VitaminIntake(
            user_id=user_id, vitamin_date=vitamin_date, item_code=item_code, taken_at=taken_at
        )
        self._session.add(intake)
        try:
            self._session.flush()
        except IntegrityError:
            # This item was already confirmed, possibly by a concurrent tap:
            # keep that row, with its original taken_at.
            self._session.rollback()
            intake = self.get(user_id, vitamin_date, item_code)
            assert intake is not None
            return intake
        self._session.commit()
        self._session.refresh(intake)
        return intake
