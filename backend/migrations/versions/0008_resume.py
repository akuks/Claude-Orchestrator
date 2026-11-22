"""resume: task origin, notes, heartbeat and recovery fields, thread handoffs,
org ground rules

Backfill: co-guard follow-ups (by title) get origin guard; dispatched children
(parent and agent set) get origin agent; every other row keeps user.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TASK_COLS = ("head_at_start", "credential_ref", "recovery", "recoveries", "run_proc_start", "run_pgid",
              "heartbeat_at", "notes_updated_at", "notes", "origin")


def upgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("origin", sa.String(16), nullable=False, server_default="user"))
        b.add_column(sa.Column("notes", sa.Text(), nullable=True))
        b.add_column(sa.Column("notes_updated_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("run_pgid", sa.Integer(), nullable=True))
        b.add_column(sa.Column("run_proc_start", sa.String(64), nullable=True))
        b.add_column(sa.Column("recoveries", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("recovery", sa.JSON(), nullable=True))
        b.add_column(sa.Column("credential_ref", sa.String(32), nullable=True))
        b.add_column(sa.Column("head_at_start", sa.String(64), nullable=True))
    op.execute("UPDATE tasks SET origin='guard' WHERE title LIKE 'co-guard follow-up:%'")
    op.execute("UPDATE tasks SET origin='agent' WHERE parent_task_id IS NOT NULL AND agent_id IS NOT NULL")
    op.create_table(
        "task_handoffs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("root_id", sa.String(32), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("reason", sa.String(16), nullable=False, server_default=""),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("prev_content", sa.JSON(), nullable=True),
        sa.Column("prev_text", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(32), nullable=True),
    )
    op.create_index("ix_task_handoffs_root_id", "task_handoffs", ["root_id"], unique=True)
    with op.batch_alter_table("guard_org_settings") as b:
        b.add_column(sa.Column("ground_rules", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("guard_org_settings") as b:
        b.drop_column("ground_rules")
    op.drop_index("ix_task_handoffs_root_id", "task_handoffs")
    op.drop_table("task_handoffs")
    with op.batch_alter_table("tasks") as b:
        for col in _TASK_COLS:
            b.drop_column(col)
