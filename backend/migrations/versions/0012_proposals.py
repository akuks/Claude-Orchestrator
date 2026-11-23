"""proposals: decision_proposals and decision stale flags (phase 4b)

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project_decisions") as b:
        b.add_column(sa.Column("stale_flagged_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("stale_reason", sa.String(300), nullable=True))
    op.create_table(
        "decision_proposals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=True),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="decision"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("touches", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("evidence", sa.String(300), nullable=False, server_default=""),
        sa.Column("dedupe_key", sa.String(80), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("proposed_by", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_by", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "dedupe_key", name="uq_decision_proposals_project_dedupe"),
    )
    op.create_index("ix_decision_proposals_project_id", "decision_proposals", ["project_id"])
    op.create_index("ix_decision_proposals_project_id_status", "decision_proposals", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_decision_proposals_project_id_status", "decision_proposals")
    op.drop_index("ix_decision_proposals_project_id", "decision_proposals")
    op.drop_table("decision_proposals")
    with op.batch_alter_table("project_decisions") as b:
        b.drop_column("stale_reason")
        b.drop_column("stale_flagged_at")
