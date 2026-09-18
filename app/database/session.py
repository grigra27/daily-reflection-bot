"""Session scope helper."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.base import build_engine, make_session_factory


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a transactional session; commit on success, rollback on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_engine_and_factory(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = build_engine(database_url)
    return engine, make_session_factory(engine)
