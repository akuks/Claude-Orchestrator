"""decisions: project_decisions and projects.decision_seq (phase 4a)

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("decision_seq", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "project_decisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="decision"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("see", sa.String(300), nullable=True),
        sa.Column("touches", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("decided_by", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="active"),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "number", name="uq_project_decisions_project_number"),
    )
    op.create_index("ix_project_decisions_project_id", "project_decisions", ["project_id"])
    op.create_index("ix_project_decisions_project_id_status", "project_decisions", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_project_decisions_project_id_status", "project_decisions")
    op.drop_index("ix_project_decisions_project_id", "project_decisions")
    op.drop_table("project_decisions")
    with op.batch_alter_table("projects") as b:
        b.drop_column("decision_seq")
