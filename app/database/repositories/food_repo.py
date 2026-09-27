"""FoodDay / FoodRuleResult / WeightLog repositories (v1.3).

Database-only, like the other repositories: ``get_or_create`` keeps one
``FoodDay`` per user per date (race-safe against the UNIQUE constraint) and
``upsert`` keeps one ``WeightLog`` per user per week. Business rules — which
rules exist, when a day may still be edited — live in ``food_service``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database.models import FoodDay, FoodRuleResult, WeightLog


class FoodDayRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, food_date: date) -> FoodDay | None:
        stmt = (
            select(FoodDay)
            .options(selectinload(FoodDay.results))
            .where(FoodDay.user_id == user_id, FoodDay.food_date == food_date)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def get_or_create(self, user_id: int, food_date: date) -> FoodDay:
        day = self.get(user_id, food_date)
        if day is not None:
            return day
        day = FoodDay(user_id=user_id, food_date=food_date)
        self._session.add(day)
        try:
            self._session.flush()
        except IntegrityError:
            # A concurrent tap created the row first: use that one.
            self._session.rollback()
            day = self.get(user_id, food_date)
            assert day is not None
        return day

    def set_result(self, day: FoodDay, rule_code: str, kept: bool) -> None:
        for result in day.results:
            if result.rule_code == rule_code:
                result.kept = kept
                return
        day.results.append(FoodRuleResult(rule_code=rule_code, kept=kept))

    def remove_result(self, day: FoodDay, rule_code: str) -> None:
        day.results[:] = [r for r in day.results if r.rule_code != rule_code]

    def save(self, day: FoodDay) -> FoodDay:
        self._session.add(day)
        self._session.commit()
        self._session.refresh(day)
        return day

    def list_in_range(self, user_id: int, start: date, end: date) -> list[FoodDay]:
        stmt = (
            select(FoodDay)
            .options(selectinload(FoodDay.results))
            .where(FoodDay.user_id == user_id, FoodDay.food_date >= start, FoodDay.food_date <= end)
            .order_by(FoodDay.food_date)
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_all(self, user_id: int) -> list[FoodDay]:
        stmt = (
            select(FoodDay)
            .options(selectinload(FoodDay.results))
            .where(FoodDay.user_id == user_id)
            .order_by(FoodDay.food_date)
        )
        return list(self._session.execute(stmt).scalars().all())


class WeightLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: int, week_start_date: date) -> WeightLog | None:
        stmt = select(WeightLog).where(
            WeightLog.user_id == user_id, WeightLog.week_start_date == week_start_date
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def upsert(
        self, *, user_id: int, week_start_date: date, logged_date: date, weight_kg: float
    ) -> WeightLog:
        log = self.get(user_id, week_start_date)
        if log is None:
            log = WeightLog(
                user_id=user_id,
                week_start_date=week_start_date,
                logged_date=logged_date,
                weight_kg=weight_kg,
            )
            self._session.add(log)
            try:
                self._session.flush()
            except IntegrityError:
                self._session.rollback()
                log = self.get(user_id, week_start_date)
                assert log is not None
                log.logged_date = logged_date
                log.weight_kg = weight_kg
        else:
            log.logged_date = logged_date
            log.weight_kg = weight_kg
        self._session.commit()
        self._session.refresh(log)
        return log

    def list_all(self, user_id: int) -> list[WeightLog]:
        stmt = (
            select(WeightLog)
            .where(WeightLog.user_id == user_id)
            .order_by(WeightLog.week_start_date)
        )
        return list(self._session.execute(stmt).scalars().all())
