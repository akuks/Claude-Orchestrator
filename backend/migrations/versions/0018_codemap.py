"""code map: projects.code_map and the project_indexes table (phase 6b)

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("code_map", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "project_indexes",
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sha", sa.String(64), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="never"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_try_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("project_indexes")
    with op.batch_alter_table("projects") as b:
        b.drop_column("code_map")
