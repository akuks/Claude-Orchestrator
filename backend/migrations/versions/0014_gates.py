"""gates: run stage state and log, task stage attempt, gate run records (phase 5a)

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs") as b:
        b.add_column(sa.Column("stage_state", sa.JSON(), nullable=False, server_default="{}"))
        b.add_column(sa.Column("log", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("stage_attempt", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "workflow_gate_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("handout", sa.String(32), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("runner", sa.String(32), nullable=True),
        sa.Column("passed", sa.Integer(), nullable=True),
        sa.Column("failed", sa.Integer(), nullable=True),
        sa.Column("skipped", sa.Integer(), nullable=True),
        sa.Column("failing", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("flaky", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("code_state", sa.String(64), nullable=True),
        sa.Column("log_name", sa.String(200), nullable=True),
    )
    op.create_index("ix_workflow_gate_runs_run_id", "workflow_gate_runs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_gate_runs_run_id", "workflow_gate_runs")
    op.drop_table("workflow_gate_runs")
    with op.batch_alter_table("tasks") as b:
        b.drop_column("stage_attempt")
    with op.batch_alter_table("workflow_runs") as b:
        b.drop_column("log")
        b.drop_column("stage_state")
