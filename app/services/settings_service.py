"""User settings management (baseline section 19).

Validation lives here so an invalid time or unknown timezone is never persisted
to the database.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import User
from app.database.repositories import UserRepository
from app.services.time_service import format_hhmm, is_valid_timezone


class InvalidSettingError(ValueError):
    pass


def set_timezone(session: Session, user: User, tz_name: str) -> User:
    tz_name = tz_name.strip()
    if not is_valid_timezone(tz_name):
        raise InvalidSettingError("unknown timezone")
    user.timezone = tz_name
    return UserRepository(session).save(user)


def set_morning_time(session: Session, user: User, value: str) -> User:
    try:
        user.morning_time = format_hhmm(value)
    except ValueError as exc:
        raise InvalidSettingError(str(exc)) from exc
    return UserRepository(session).save(user)


def set_checkin_time(session: Session, user: User, value: str) -> User:
    try:
        user.checkin_time = format_hhmm(value)
    except ValueError as exc:
        raise InvalidSettingError(str(exc)) from exc
    return UserRepository(session).save(user)


def set_reminder_time(session: Session, user: User, value: str) -> User:
    try:
        user.reminder_time = format_hhmm(value)
    except ValueError as exc:
        raise InvalidSettingError(str(exc)) from exc
    return UserRepository(session).save(user)
