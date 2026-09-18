"""initial schema: users, daily_entries, weekly_reflections

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("checkin_time", sa.String(length=5), nullable=False, server_default="21:30"),
        sa.Column("reminder_time", sa.String(length=5), nullable=False, server_default="23:00"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)

    op.create_table(
        "daily_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("day_score", sa.Integer(), nullable=False),
        sa.Column("mood_score", sa.Integer(), nullable=False),
        sa.Column("energy_score", sa.Integer(), nullable=False),
        sa.Column("reflection_text", sa.Text(), nullable=True),
        sa.Column("questionnaire_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "entry_date", name="uq_daily_entry_user_date"),
        sa.CheckConstraint("day_score BETWEEN 1 AND 5", name="ck_day_score"),
        sa.CheckConstraint("mood_score BETWEEN 1 AND 5", name="ck_mood_score"),
        sa.CheckConstraint("energy_score BETWEEN 1 AND 5", name="ck_energy_score"),
    )
    op.create_index("ix_daily_entries_user_id", "daily_entries", ["user_id"])

    op.create_table(
        "weekly_reflections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("best_event", sa.Text(), nullable=True),
        sa.Column("energy_drainer", sa.Text(), nullable=True),
        sa.Column("want_more", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "week_start_date", name="uq_weekly_user_week"),
    )
    op.create_index("ix_weekly_reflections_user_id", "weekly_reflections", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_weekly_reflections_user_id", table_name="weekly_reflections")
    op.drop_table("weekly_reflections")
    op.drop_index("ix_daily_entries_user_id", table_name="daily_entries")
    op.drop_table("daily_entries")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
