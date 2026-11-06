"""Cache requested weekly AI analyses; preserve all existing diary data."""

import sqlalchemy as sa
from alembic import op

revision = "0008_weekly_analyses"
down_revision = "0007_vitamin_intakes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "week_start", name="uq_analysis_user_week"),
    )
    op.create_index("ix_weekly_analyses_user_id", "weekly_analyses", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_weekly_analyses_user_id", table_name="weekly_analyses")
    op.drop_table("weekly_analyses")
