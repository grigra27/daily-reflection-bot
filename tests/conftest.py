"""Shared pytest fixtures.

Tests use a throwaway SQLite file per test and never touch Telegram or the
network. No real bot token is required.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database.base import Base
from app.database.models import User
from app.database.session import create_engine_and_factory, session_scope


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="test-token",
        allowed_telegram_ids="111,222",
        default_timezone="Europe/Moscow",
        default_checkin_time="21:30",
        default_reminder_time="23:00",
        database_url="sqlite:///unused",
    )


@pytest.fixture
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    engine, factory = create_engine_and_factory(url)
    Base.metadata.create_all(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    with session_scope(session_factory) as s:
        yield s


@pytest.fixture
def user(session: Session) -> User:
    u = User(telegram_user_id=111, display_name="A", timezone="Europe/Moscow")
    session.add(u)
    session.commit()
    session.refresh(u)
    return u
