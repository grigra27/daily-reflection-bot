"""v1.3.1 vitamins reminder: two columns on ``users``

Adds ``vitamin_reminder_time`` and ``vitamin_reminder_enabled`` with server
defaults, so every existing user is backfilled to "enabled at 22:00" in the
same ADD COLUMN and no other table is touched. Additive only: the downgrade
drops exactly these two columns.

Revision ID: 0005_vitamin_reminder
Revises: 0004_food_reflection
Create Date: 2026-10-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_vitamin_reminder"
down_revision = "0004_food_reflection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("vitamin_reminder_time", sa.String(length=5), nullable=False,
                  server_default="22:00"),
    )
    op.add_column(
        "users",
        sa.Column("vitamin_reminder_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "vitamin_reminder_enabled")
    op.drop_column("users", "vitamin_reminder_time")
