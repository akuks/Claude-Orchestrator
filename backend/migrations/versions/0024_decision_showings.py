"""Decision track record (phase 8a): decision_showings, one row per task and decision number it was shown,
and what happened to that task afterwards.

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "decision_showings" in _tables():
        return
    op.create_table(
        "decision_showings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_number", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(12), nullable=True),
        sa.Column("retried", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", "decision_number", name="uq_decision_showings_task_number"),
    )
    op.create_index("ix_decision_showings_project_number", "decision_showings", ["project_id", "decision_number"])
    op.create_index("ix_decision_showings_project_shown", "decision_showings", ["project_id", "shown_at"])


def downgrade() -> None:
    if "decision_showings" not in _tables():
        return
    op.drop_index("ix_decision_showings_project_shown", table_name="decision_showings")
    op.drop_index("ix_decision_showings_project_number", table_name="decision_showings")
    op.drop_table("decision_showings")
