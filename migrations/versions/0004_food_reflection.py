"""v1.3 food reflection: food_days, food_rule_results, weight_logs

Adds four columns to ``users`` (three prompt times with server defaults, so
every existing user is backfilled in the same ADD COLUMN, and a nullable
``food_rules`` where NULL means "the default rule set") and creates three new
tables. No existing row or column is modified, so users, daily entries,
morning intentions and weekly reflections survive the upgrade byte-for-byte.

Revision ID: 0004_food_reflection
Revises: 0003_intention_outcomes
Create Date: 2026-09-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_food_reflection"
down_revision = "0003_intention_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "food_morning_time", sa.String(length=5), nullable=False, server_default="08:45"
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "food_evening_time", sa.String(length=5), nullable=False, server_default="22:30"
        ),
    )
    op.add_column(
        "users",
        sa.Column("weight_time", sa.String(length=5), nullable=False, server_default="09:00"),
    )
    op.add_column("users", sa.Column("food_rules", sa.Text(), nullable=True))

    op.create_table(
        "food_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("food_date", sa.Date(), nullable=False),
        sa.Column("focus_rule", sa.String(length=32), nullable=True),
        sa.Column("focus_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggers", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "food_date", name="uq_food_day_user_date"),
    )
    op.create_index("ix_food_days_user_id", "food_days", ["user_id"])

    op.create_table(
        "food_rule_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("food_day_id", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.String(length=32), nullable=False),
        sa.Column("kept", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["food_day_id"], ["food_days.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("food_day_id", "rule_code", name="uq_food_rule_result_day_rule"),
    )
    op.create_index("ix_food_rule_results_food_day_id", "food_rule_results", ["food_day_id"])

    op.create_table(
        "weight_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("logged_date", sa.Date(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "week_start_date", name="uq_weight_user_week"),
        sa.CheckConstraint("weight_kg > 0", name="ck_weight_positive"),
    )
    op.create_index("ix_weight_logs_user_id", "weight_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_weight_logs_user_id", table_name="weight_logs")
    op.drop_table("weight_logs")
    op.drop_index("ix_food_rule_results_food_day_id", table_name="food_rule_results")
    op.drop_table("food_rule_results")
    op.drop_index("ix_food_days_user_id", table_name="food_days")
    op.drop_table("food_days")
    op.drop_column("users", "food_rules")
    op.drop_column("users", "weight_time")
    op.drop_column("users", "food_evening_time")
    op.drop_column("users", "food_morning_time")
