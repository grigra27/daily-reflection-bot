"""SQLAlchemy ORM models for the Daily Reflection Bot.

Data models follow baseline sections 33-36. All timestamps are stored as
timezone-aware UTC values; user-local "today" is derived separately from each
user's timezone (see ``app.services.time_service``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

QUESTIONNAIRE_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    checkin_time: Mapped[str] = mapped_column(String(5), default="21:30", nullable=False)
    reminder_time: Mapped[str] = mapped_column(String(5), default="23:00", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    daily_entries: Mapped[list[DailyEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    weekly_reflections: Mapped[list[WeeklyReflection]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class DailyEntry(Base):
    __tablename__ = "daily_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "entry_date", name="uq_daily_entry_user_date"),
        CheckConstraint("day_score BETWEEN 1 AND 5", name="ck_day_score"),
        CheckConstraint("mood_score BETWEEN 1 AND 5", name="ck_mood_score"),
        CheckConstraint("energy_score BETWEEN 1 AND 5", name="ck_energy_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    day_score: Mapped[int] = mapped_column(Integer, nullable=False)
    mood_score: Mapped[int] = mapped_column(Integer, nullable=False)
    energy_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reflection_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    questionnaire_version: Mapped[int] = mapped_column(
        Integer, default=QUESTIONNAIRE_VERSION, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="daily_entries")


class WeeklyReflection(Base):
    __tablename__ = "weekly_reflections"
    __table_args__ = (UniqueConstraint("user_id", "week_start_date", name="uq_weekly_user_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    best_event: Mapped[str | None] = mapped_column(Text, nullable=True)
    energy_drainer: Mapped[str | None] = mapped_column(Text, nullable=True)
    want_more: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="weekly_reflections")
