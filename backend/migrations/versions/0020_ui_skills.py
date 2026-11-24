"""projects: ui_skills (default on) and brand_skill (design skills for projects with a user interface)

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing projects read ui_skills = true; nothing is added unless a user interface is found.
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("ui_skills", sa.Boolean(), nullable=False, server_default=sa.true()))
        b.add_column(sa.Column("brand_skill", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.drop_column("brand_skill")
        b.drop_column("ui_skills")
