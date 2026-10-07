"""v1.3.2 vitamin acknowledgement: vitamin_logs

Creates one new table: a row means "this user confirmed the vitamins for this
Reflection Day". UNIQUE(user_id, vitamin_date) is what makes the ✅ tap
idempotent, so a double press and a replayed button can never stack history.

Additive only. The v1.3.1 settings columns on ``users`` are untouched and no
existing table, row or index is modified, so the downgrade drops exactly
``vitamin_logs``.

Revision ID: 0006_vitamin_log
Revises: 0005_vitamin_reminder
Create Date: 2026-10-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_vitamin_log"
down_revision = "0005_vitamin_reminder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vitamin_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vitamin_date", sa.Date(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "vitamin_date", name="uq_vitamin_log_user_date"),
    )
    op.create_index("ix_vitamin_logs_user_id", "vitamin_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_vitamin_logs_user_id", table_name="vitamin_logs")
    op.drop_table("vitamin_logs")
