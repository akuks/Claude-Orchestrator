"""licence hint and skill pack update check: projects.licence, skill_packs.latest_sha, skill_packs.checked_at
(phase 6a)

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("licence", sa.JSON(none_as_null=True), nullable=True))
    with op.batch_alter_table("skill_packs") as b:
        b.add_column(sa.Column("latest_sha", sa.String(64), nullable=True))
        b.add_column(sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("skill_packs") as b:
        b.drop_column("checked_at")
        b.drop_column("latest_sha")
    with op.batch_alter_table("projects") as b:
        b.drop_column("licence")
