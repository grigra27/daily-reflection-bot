"""Database engine and session management (sync SQLAlchemy 2.x on SQLite).

The bot is a single-process, low-volume application for two users, so a simple
synchronous session is used everywhere (handlers and scheduler alike). This
keeps Alembic configuration trivial and avoids blocking concerns at this scale.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[2]


def _normalize_sqlite_url(url: str) -> str:
    """Resolve a relative SQLite path against the project root."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix):]
        if raw and not raw.startswith("/"):
            path = (BASE_DIR / raw).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"{prefix}{path}"
    return url


class Base(DeclarativeBase):
    pass


def build_engine(url: str) -> Engine:
    engine = create_engine(_normalize_sqlite_url(url), future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        # WAL improves concurrent read/write between bot and scheduler.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
