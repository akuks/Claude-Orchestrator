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


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _create_index(name: str, column: str) -> None:
    """On Postgres built CONCURRENTLY (event writes go on while a large table is indexed), outside the
    migration's transaction; plain elsewhere."""
    if _postgres():
        with op.get_context().autocommit_block():
            op.create_index(name, "task_events", [column], postgresql_concurrently=True, if_not_exists=True)
    else:
        op.create_index(name, "task_events", [column])


def _drop_index(name: str) -> None:
    if _postgres():
        with op.get_context().autocommit_block():
            op.drop_index(name, table_name="task_events", postgresql_concurrently=True, if_exists=True)
    else:
        op.drop_index(name, table_name="task_events")


def upgrade() -> None:
    with op.batch_alter_table("guard_org_settings") as b:
        b.add_column(sa.Column("retention", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("root_commit", sa.String(40), nullable=True))
    have = _indexes()
    if "ix_task_events_created_at" not in have:
        _create_index("ix_task_events_created_at", "created_at")
    if "ix_task_events_task_id" not in have:  # the baseline has it; a repaired database might not
        _create_index("ix_task_events_task_id", "task_id")


def downgrade() -> None:
    # ix_task_events_task_id belongs to the baseline: it stays
    if "ix_task_events_created_at" in _indexes():
        _drop_index("ix_task_events_created_at")
    with op.batch_alter_table("projects") as b:
        b.drop_column("root_commit")
    with op.batch_alter_table("guard_org_settings") as b:
        b.drop_column("retention")
