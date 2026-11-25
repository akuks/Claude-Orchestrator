"""Small gaps (phase 7e): mcp_servers.server_version (what the tool said it is, in its handshake)

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "server_version" not in _columns("mcp_servers"):
        with op.batch_alter_table("mcp_servers") as b:
            b.add_column(sa.Column("server_version", sa.String(64), nullable=True))


def downgrade() -> None:
    if "server_version" in _columns("mcp_servers"):
        with op.batch_alter_table("mcp_servers") as b:
            b.drop_column("server_version")
