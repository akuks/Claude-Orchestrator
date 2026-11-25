"""Upkeep (phase 7d): the tidying switch (guard_org_settings.retention: enabled, days, last run),
projects.root_commit, and indexes on task_events.created_at (and task_id, when a database lacks it)

All in the migration's one transaction (migrations run at start, before the app serves): an interrupted start
leaves nothing half-done, and each step is skipped when it is already there.

Revision ID: 0022
Revises: 0021
"""
import logging

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)
INDEX_NOTE = "Building an index on the task history table; on a large history this can take a few minutes."


def _indexes() -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("task_events")}


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _indexes()
    if "ix_task_events_created_at" not in have:
        log.warning(INDEX_NOTE)
        op.create_index("ix_task_events_created_at", "task_events", ["created_at"], if_not_exists=True)
    if "ix_task_events_task_id" not in have:  # the baseline has it; a repaired database might not
        op.create_index("ix_task_events_task_id", "task_events", ["task_id"], if_not_exists=True)
    if "retention" not in _columns("guard_org_settings"):
        with op.batch_alter_table("guard_org_settings") as b:
            b.add_column(sa.Column("retention", sa.JSON(), nullable=False, server_default="{}"))
    if "root_commit" not in _columns("projects"):
        with op.batch_alter_table("projects") as b:
            b.add_column(sa.Column("root_commit", sa.String(40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.drop_column("root_commit")
    with op.batch_alter_table("guard_org_settings") as b:
        b.drop_column("retention")
    # ix_task_events_task_id belongs to the baseline: it stays
    if "ix_task_events_created_at" in _indexes():
        op.drop_index("ix_task_events_created_at", table_name="task_events")
