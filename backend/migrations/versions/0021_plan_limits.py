"""Claude plan limits: plan_limit_status (latest reading per credential owner) and tasks.not_before /
tasks.wait_reason (a task waiting for the plan's reset time)

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_limit_status",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("credential_owner", sa.String(32), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resets_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("utilization", sa.Float(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("api_key", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("credential_ref", sa.String(32), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=True),
    )
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("not_before", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("wait_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.drop_column("wait_reason")
        b.drop_column("not_before")
    op.drop_table("plan_limit_status")
