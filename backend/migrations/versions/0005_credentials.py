"""credentials: per-user credentials, org settings, project automation owner

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column(
            "user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("claude_kind", sa.String(16), nullable=True),
        sa.Column("claude_enc", sa.Text(), nullable=True),
        sa.Column("github_enc", sa.Text(), nullable=True),
        sa.Column("git_name", sa.String(200), nullable=True),
        sa.Column("git_email", sa.String(320), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "org_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value_enc", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(32), nullable=True),
    )
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("automation_owner_id", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.drop_column("automation_owner_id")
    op.drop_table("org_settings")
    op.drop_table("user_credentials")
