"""stage outputs, budget cap, launch raise_risk: tasks.outputs, workflows/workflow_runs.budget_usd,
project_launches.raise_risk (phase 5c)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("outputs", sa.JSON(none_as_null=True), nullable=True))
    with op.batch_alter_table("workflows") as b:
        b.add_column(sa.Column("budget_usd", sa.Float(), nullable=True))
    with op.batch_alter_table("workflow_runs") as b:
        b.add_column(sa.Column("budget_usd", sa.Float(), nullable=True))
    with op.batch_alter_table("project_launches") as b:
        b.add_column(sa.Column("raise_risk", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("project_launches") as b:
        b.drop_column("raise_risk")
    with op.batch_alter_table("workflow_runs") as b:
        b.drop_column("budget_usd")
    with op.batch_alter_table("workflows") as b:
        b.drop_column("budget_usd")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("outputs")
