"""v1.2 close the loop: evening outcomes on morning_intents

Adds two nullable text columns, ``morning_intents.main_outcome`` and
``morning_intents.secondary_outcome``. Existing rows keep NULL (no backfill:
an outcome is only ever what the user tapped in the evening). No other table,
row or existing column is touched, so historical users, daily entries, weekly
reflections and morning texts survive the upgrade byte-for-byte.

Two plain nullable ADD COLUMNs rather than a batch table rebuild with a CHECK
constraint: the outcome vocabulary is enforced in the business layer, and this
keeps the migration from rewriting (and therefore risking) production rows.

Revision ID: 0003_intention_outcomes
Revises: 0002_morning_intents
Create Date: 2026-09-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_intention_outcomes"
down_revision = "0002_morning_intents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "morning_intents",
        sa.Column("main_outcome", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "morning_intents",
        sa.Column("secondary_outcome", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("morning_intents", "secondary_outcome")
    op.drop_column("morning_intents", "main_outcome")
