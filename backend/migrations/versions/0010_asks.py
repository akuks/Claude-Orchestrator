"""asks: run_asks (private questions about a run, phase 3c)

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_asks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_asks_task_id", "run_asks", ["task_id"])
    op.create_index("ix_run_asks_user_id", "run_asks", ["user_id"])
    op.create_index("ix_run_asks_user_id_created_at", "run_asks", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_run_asks_user_id_created_at", "run_asks")
    op.drop_index("ix_run_asks_user_id", "run_asks")
    op.drop_index("ix_run_asks_task_id", "run_asks")
    op.drop_table("run_asks")
