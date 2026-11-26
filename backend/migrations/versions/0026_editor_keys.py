"""Editor keys (phase 8c): editor_keys, a person's keys for their own editor to read decisions over POST /mcp.

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "editor_keys" in _tables():
        return
    op.create_table(
        "editor_keys",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(8), nullable=False),
        sa.Column("projects", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_editor_keys_key_hash"),
    )
    op.create_index("ix_editor_keys_user_id", "editor_keys", ["user_id"])


def downgrade() -> None:
    if "editor_keys" not in _tables():
        return
    op.drop_index("ix_editor_keys_user_id", table_name="editor_keys")
    op.drop_table("editor_keys")
