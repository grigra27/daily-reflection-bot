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
    Float,
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
    morning_time: Mapped[str] = mapped_column(String(5), default="08:30", nullable=False)
    # v1.3 food reflection: three separate prompts and the user's own rule set.
    # ``food_rules`` is a comma-separated list of rule codes from the catalog in
    # ``food_service``; NULL means "the default set", so the catalog can evolve
    # without a data migration for users who never customised it.
    food_morning_time: Mapped[str] = mapped_column(String(5), default="08:45", nullable=False)
    food_evening_time: Mapped[str] = mapped_column(String(5), default="22:30", nullable=False)
    weight_time: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    food_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v1.3.1: plain wall-clock vitamin reminder — a text message only. There is
    # deliberately no acknowledgement or history (see the feature spec).
    vitamin_reminder_time: Mapped[str] = mapped_column(
        String(5), default="22:00", nullable=False
    )
    vitamin_reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    morning_intents: Mapped[list[MorningIntent]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    food_days: Mapped[list[FoodDay]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    weight_logs: Mapped[list[WeightLog]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class MorningIntent(Base):
    """One morning intention per user per local date.

    Deliberately has no FK to ``daily_entries`` — the link to the evening
    check-in is purely logical (same user, same user-local calendar date),
    and either record may exist without the other.
    """

    __tablename__ = "morning_intents"
    __table_args__ = (
        UniqueConstraint("user_id", "intention_date", name="uq_morning_intent_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    intention_date: Mapped[date] = mapped_column(Date, nullable=False)
    main_intention: Mapped[str] = mapped_column(Text, nullable=False)
    secondary_intention: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v1.2 evening closure of the morning intentions: NULL means "not asked
    # yet", so historical (pre-v1.2) rows and partially completed evenings are
    # both representable. Values are the application-level outcome vocabulary.
    main_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    secondary_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="morning_intents")


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


class FoodDay(Base):
    """One food reflection per user per Reflection Day (v1.3).

    The morning focus and the evening checklist share this row. A row may
    exist with only a focus (morning answered, evening not yet), with only
    draft rule results (checklist tapped but not submitted), or complete.
    ``completed_at`` is what marks the evening as submitted; until then the
    rule results are a draft that survives restarts.
    """

    __tablename__ = "food_days"
    __table_args__ = (UniqueConstraint("user_id", "food_date", name="uq_food_day_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    food_date: Mapped[date] = mapped_column(Date, nullable=False)
    # NULL with ``focus_set_at`` present means "answered: no focus today".
    focus_rule: Mapped[str | None] = mapped_column(String(32), nullable=True)
    focus_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Comma-separated trigger codes, only meaningful when a rule was broken.
    triggers: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="food_days")
    results: Mapped[list[FoodRuleResult]] = relationship(
        back_populates="food_day", cascade="all, delete-orphan", order_by="FoodRuleResult.id"
    )


class FoodRuleResult(Base):
    """Whether one rule was kept on one day. Keyed by the rule *code*, so
    disabling a rule later never rewrites or orphans its history."""

    __tablename__ = "food_rule_results"
    __table_args__ = (
        UniqueConstraint("food_day_id", "rule_code", name="uq_food_rule_result_day_rule"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    food_day_id: Mapped[int] = mapped_column(
        ForeignKey("food_days.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False)
    kept: Mapped[bool] = mapped_column(Boolean, nullable=False)

    food_day: Mapped[FoodDay] = relationship(back_populates="results")


class WeightLog(Base):
    """One weight value per user per ISO week (v1.3)."""

    __tablename__ = "weight_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "week_start_date", name="uq_weight_user_week"),
        CheckConstraint("weight_kg > 0", name="ck_weight_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    # The Reflection Day the value was entered on (for the CSV export).
    logged_date: Mapped[date] = mapped_column(Date, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="weight_logs")
