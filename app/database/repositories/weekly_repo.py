"""WeeklyReflection repository (baseline sections 25-26, 36).

One reflection per user per week, keyed by the ISO week start date (Monday).
Each of the three text answers may be left empty (skipped).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import WeeklyReflection


class WeeklyReflectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, week_start_date: date) -> WeeklyReflection | None:
        stmt = select(WeeklyReflection).where(
            WeeklyReflection.user_id == user_id,
            WeeklyReflection.week_start_date == week_start_date,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def upsert(
        self,
        *,
        user_id: int,
        week_start_date: date,
        best_event: str | None,
        energy_drainer: str | None,
        want_more: str | None,
    ) -> WeeklyReflection:
        row = self.get(user_id, week_start_date)
        if row is None:
            row = WeeklyReflection(
                user_id=user_id,
                week_start_date=week_start_date,
                best_event=best_event,
                energy_drainer=energy_drainer,
                want_more=want_more,
            )
            self._session.add(row)
        else:
            row.best_event = best_event
            row.energy_drainer = energy_drainer
            row.want_more = want_more
        self._session.commit()
        self._session.refresh(row)
        return row
