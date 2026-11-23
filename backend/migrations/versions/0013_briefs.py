"""briefs: org model tiers, agent tier, task briefs (phase 4c)

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("guard_org_settings") as b:
        b.add_column(sa.Column("model_tiers", sa.JSON(), nullable=False, server_default="{}"))
    with op.batch_alter_table("agents") as b:
        b.add_column(sa.Column("tier", sa.String(16), nullable=True))
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("brief", sa.JSON(), nullable=True))
        b.add_column(sa.Column("brief_bounds", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.drop_column("brief_bounds")
        b.drop_column("brief")
    with op.batch_alter_table("agents") as b:
        b.drop_column("tier")
    with op.batch_alter_table("guard_org_settings") as b:
        b.drop_column("model_tiers")
