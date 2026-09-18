"""Process-wide runtime container.

Holds the settings, DB engine/session factory and a scheduler handle so that
handlers, services and scheduled jobs share one wiring. Built once at startup
and used directly for a two-user, single-process bot (kept out of the service
layer so services stay independently testable with a plain ``Session``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.database.session import create_engine_and_factory

if TYPE_CHECKING:
    from aiogram import Bot

    from app.scheduler.scheduler import ReflectionScheduler


@dataclass
class Runtime:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker
    scheduler: ReflectionScheduler | None = None
    bot: Bot | None = None


_runtime: Runtime | None = None


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()
    engine, session_factory = create_engine_and_factory(settings.database_url)
    return Runtime(settings=settings, engine=engine, session_factory=session_factory)


def init_runtime(runtime: Runtime) -> None:
    global _runtime
    _runtime = runtime


def get_runtime() -> Runtime:
    if _runtime is None:
        raise RuntimeError("Runtime not initialised. Call init_runtime() at startup.")
    return _runtime
