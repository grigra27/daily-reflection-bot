"""Repositories: thin, database-only data-access objects.

No business rules live here — repositories only translate between the ORM and
the service layer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        stmt = select(User).where(User.telegram_user_id == telegram_user_id)
        return self._session.execute(stmt).scalar_one_or_none()

    def get_or_create(
        self,
        telegram_user_id: int,
        *,
        display_name: str | None = None,
        timezone: str = "Europe/Moscow",
        checkin_time: str = "21:30",
        reminder_time: str = "23:00",
        morning_time: str = "08:30",
    ) -> User:
        user = self.get_by_telegram_id(telegram_user_id)
        if user is not None:
            if display_name and not user.display_name:
                user.display_name = display_name
            return user
        user = User(
            telegram_user_id=telegram_user_id,
            display_name=display_name,
            timezone=timezone,
            checkin_time=checkin_time,
            reminder_time=reminder_time,
            morning_time=morning_time,
        )
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        return user

    def list_all(self) -> list[User]:
        return list(self._session.execute(select(User).order_by(User.id)).scalars().all())

    def save(self, user: User) -> User:
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        return user
