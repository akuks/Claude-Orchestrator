"""Upkeep (phase 7d): the tidying switch (guard_org_settings.retention: enabled, days, last run) and indexes on
task_events.created_at (and task_id, when a database lacks it)

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _indexes() -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("task_events")}


def upgrade() -> None:
    with op.batch_alter_table("guard_org_settings") as b:
        b.add_column(sa.Column("retention", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("root_commit", sa.String(40), nullable=True))
    have = _indexes()
    if "ix_task_events_created_at" not in have:
        op.create_index("ix_task_events_created_at", "task_events", ["created_at"])
    if "ix_task_events_task_id" not in have:  # the baseline has it; a repaired database might not
        op.create_index("ix_task_events_task_id", "task_events", ["task_id"])


def downgrade() -> None:
    # ix_task_events_task_id belongs to the baseline: it stays
    if "ix_task_events_created_at" in _indexes():
        op.drop_index("ix_task_events_created_at", table_name="task_events")
    with op.batch_alter_table("projects") as b:
        b.drop_column("root_commit")
    with op.batch_alter_table("guard_org_settings") as b:
        b.drop_column("retention")
