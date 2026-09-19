"""v1.1 morning intentions: users.morning_time + morning_intents table

Adds ``users.morning_time`` (existing users get the default 08:30 via the
column's server default) and creates ``morning_intents`` with a
UNIQUE(user_id, intention_date) constraint and an ON DELETE CASCADE FK.
Existing rows in ``users``/``daily_entries``/``weekly_reflections`` are never
modified or removed.

Revision ID: 0002_morning_intents
Revises: 0001_initial
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_morning_intents"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A single ADD COLUMN with a server default: SQLite backfills every
    # existing user with '08:30' and enforces NOT NULL in one step, avoiding
    # the multi-phase ALTER (which SQLite does not support). No existing row
    # is otherwise modified.
    op.add_column(
        "users",
        sa.Column("morning_time", sa.String(length=5), nullable=False, server_default="08:30"),
    )

    op.create_table(
        "morning_intents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("intention_date", sa.Date(), nullable=False),
        sa.Column("main_intention", sa.Text(), nullable=False),
        sa.Column("secondary_intention", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "intention_date", name="uq_morning_intent_user_date"),
    )
    op.create_index("ix_morning_intents_user_id", "morning_intents", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_morning_intents_user_id", table_name="morning_intents")
    op.drop_table("morning_intents")
    op.drop_column("users", "morning_time")
