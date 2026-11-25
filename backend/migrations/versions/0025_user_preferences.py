"""Personal preferences (phase 8b): user_preferences, one row per person, the short list they add to every task
they start.

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "user_preferences" in _tables():
        return
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("items", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    if "user_preferences" not in _tables():
        return
    op.drop_table("user_preferences")
