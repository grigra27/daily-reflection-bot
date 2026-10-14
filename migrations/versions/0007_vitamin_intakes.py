"""v1.3.3 granular vitamins: vitamin_intakes

Creates one new table: a row means "this user confirmed this vitamin item for
this Reflection Day". UNIQUE(user_id, vitamin_date, item_code) is what makes a
✅ tap idempotent per item, so a double press can never stack history while a
partially confirmed day stays stored as it was tapped.

Additive only. The legacy ``vitamin_logs`` table (v1.3.2 generic
acknowledgements) keeps its rows and its meaning — a legacy row is deliberately
not reinterpreted as three item confirmations — and no existing table, column or
index is touched, so the downgrade drops exactly ``vitamin_intakes``.

Revision ID: 0007_vitamin_intakes
Revises: 0006_vitamin_log
Create Date: 2026-10-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_vitamin_intakes"
down_revision = "0006_vitamin_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vitamin_intakes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vitamin_date", sa.Date(), nullable=False),
        sa.Column("item_code", sa.String(length=32), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "vitamin_date", "item_code", name="uq_vitamin_intake_user_date_item"
        ),
    )
    op.create_index("ix_vitamin_intakes_user_id", "vitamin_intakes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_vitamin_intakes_user_id", table_name="vitamin_intakes")
    op.drop_table("vitamin_intakes")
