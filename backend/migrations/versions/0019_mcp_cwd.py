"""connected tools: mcp_servers.start_in_project (tools start from a private, empty folder)

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing tools read false: they start from their own empty folder on their next task.
    with op.batch_alter_table("mcp_servers") as b:
        b.add_column(sa.Column("start_in_project", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("mcp_servers") as b:
        b.drop_column("start_in_project")
