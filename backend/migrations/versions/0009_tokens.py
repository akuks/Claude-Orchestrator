"""tokens: per-step usage, the Questions inbox, tasks.auto_continued,
projects.fresh_session_tokens

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("auto_continued", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("fresh_session_tokens", sa.Integer(), nullable=True))
    op.create_table(
        "task_usage",
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("root_id", sa.String(32), nullable=False),
        sa.Column("context_tokens", sa.Integer(), nullable=True),
        sa.Column("peak_context", sa.Integer(), nullable=True),
        sa.Column("est_cost_usd", sa.Float(), nullable=True),
        sa.Column("breakdown", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("bands", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_usage_root_id", "task_usage", ["root_id"])
    op.create_table(
        "task_questions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("root_id", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("blocks", sa.Text(), nullable=True),
        sa.Column("reversible", sa.Boolean(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.String(32), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("followup_task_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_questions_task_id", "task_questions", ["task_id"])
    op.create_index("ix_task_questions_root_id", "task_questions", ["root_id"])


def downgrade() -> None:
    op.drop_index("ix_task_questions_root_id", "task_questions")
    op.drop_index("ix_task_questions_task_id", "task_questions")
    op.drop_table("task_questions")
    op.drop_index("ix_task_usage_root_id", "task_usage")
    op.drop_table("task_usage")
    with op.batch_alter_table("projects") as b:
        b.drop_column("fresh_session_tokens")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("auto_continued")
