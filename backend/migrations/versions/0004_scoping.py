"""scoping: teams, team members, project members, workflow run project ids

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)
    op.create_table(
        "team_members",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_member"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])
    op.create_table(
        "project_members",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("subject_type", sa.String(8), nullable=False),
        sa.Column("subject_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.UniqueConstraint("project_id", "subject_type", "subject_id", name="uq_project_member"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_subject_id", "project_members", ["subject_id"])
    with op.batch_alter_table("workflow_runs") as b:
        b.add_column(sa.Column("project_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as b:
        b.drop_column("project_ids")
    op.drop_table("project_members")
    op.drop_table("team_members")
    op.drop_table("teams")
