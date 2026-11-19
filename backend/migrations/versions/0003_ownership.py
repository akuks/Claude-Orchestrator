"""ownership: created_by on user-owned tables, decided_by on tasks

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_OWNED = (
    "tasks",
    "schedules",
    "agents",
    "workflows",
    "workflow_runs",
    "projects",
    "templates",
    "context_docs",
)


def upgrade() -> None:
    for table in _OWNED:
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column("created_by", sa.String(32), nullable=True))
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("decided_by", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.drop_column("decided_by")
    for table in reversed(_OWNED):
        with op.batch_alter_table(table) as b:
            b.drop_column("created_by")
