"""guard: co-guard action approvals, project guard settings

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_approvals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=True),
        sa.Column("requested_by", sa.String(32), nullable=True),
        sa.Column("rule_ids", sa.JSON(), nullable=False),
        sa.Column("tool", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("cwd", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("command_hash", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(32), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_action_approvals_task_id", "action_approvals", ["task_id"])
    op.create_index("ix_action_approvals_project_id", "action_approvals", ["project_id"])
    op.create_index("ix_action_approvals_status", "action_approvals", ["status"])
    op.create_index(
        "uq_action_approvals_pending_key", "action_approvals", ["task_id", "idempotency_key"], unique=True,
        sqlite_where=sa.text("status = 'pending'"), postgresql_where=sa.text("status = 'pending'"),
    )
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("guard_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("approval_wait_seconds", sa.Integer(), nullable=False, server_default="300"))


def downgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.drop_column("approval_wait_seconds")
        b.drop_column("guard_enabled")
    op.drop_index("uq_action_approvals_pending_key", "action_approvals")
    op.drop_index("ix_action_approvals_status", "action_approvals")
    op.drop_index("ix_action_approvals_project_id", "action_approvals")
    op.drop_index("ix_action_approvals_task_id", "action_approvals")
    op.drop_table("action_approvals")
