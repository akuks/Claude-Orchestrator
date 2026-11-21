"""policy: custom rules, org guard settings, grants, project trust/readonly,
MCP environment/default action, approval resume fields

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_rules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("match", sa.String(16), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "project_id", "slug", name="uq_action_rules_slug"),
    )
    op.create_index("ix_action_rules_project_id", "action_rules", ["project_id"])
    # NULL project_ids are distinct in the constraint above: org slugs get their own.
    op.create_index(
        "uq_action_rules_org_slug", "action_rules", ["scope", "slug"], unique=True,
        sqlite_where=sa.text("project_id IS NULL"), postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_table(
        "guard_org_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("disabled_rules", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("dev_db_patterns", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("updated_by", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "action_grants",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("root_id", sa.String(32), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=True),
        sa.Column("approval_id", sa.String(32), nullable=True),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("key_kind", sa.String(16), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("created_by", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(32), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_action_grants_root_id", "action_grants", ["root_id"])
    op.create_index("ix_action_grants_task_id", "action_grants", ["task_id"])
    op.create_index("ix_action_grants_approval_id", "action_grants", ["approval_id"])
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("trust", sa.String(16), nullable=False, server_default="own"))
        b.add_column(sa.Column("quarantine", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("on_no_answer", sa.String(20), nullable=False, server_default="deny"))
        b.add_column(sa.Column("readonly_paths", sa.JSON(), nullable=False, server_default="[]"))
        b.add_column(sa.Column("reference_project_ids", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("mcp_servers") as b:
        b.add_column(sa.Column("environment", sa.String(16), nullable=False, server_default="unknown"))
        # Existing servers keep today's behaviour; the ORM/API default for new ones is require_approval.
        b.add_column(sa.Column("default_action", sa.String(20), nullable=False, server_default="auto_approve"))
    with op.batch_alter_table("action_approvals") as b:
        b.add_column(sa.Column("grant_key", sa.String(64), nullable=False, server_default=""))
        b.add_column(sa.Column("resume_until", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("delivery", sa.String(16), nullable=False, server_default="inline"))
        b.add_column(sa.Column("scope", sa.String(8), nullable=True))
        b.add_column(sa.Column("followup_task_id", sa.String(32), nullable=True))
        b.add_column(sa.Column("grant_ids", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("action_approvals") as b:
        for col in ("grant_ids", "followup_task_id", "scope", "delivery", "resume_until", "grant_key"):
            b.drop_column(col)
    with op.batch_alter_table("mcp_servers") as b:
        b.drop_column("default_action")
        b.drop_column("environment")
    with op.batch_alter_table("projects") as b:
        for col in ("reference_project_ids", "readonly_paths", "on_no_answer", "quarantine", "trust"):
            b.drop_column(col)
    op.drop_index("ix_action_grants_approval_id", "action_grants")
    op.drop_index("ix_action_grants_task_id", "action_grants")
    op.drop_index("ix_action_grants_root_id", "action_grants")
    op.drop_table("action_grants")
    op.drop_table("guard_org_settings")
    op.drop_index("uq_action_rules_org_slug", "action_rules")
    op.drop_index("ix_action_rules_project_id", "action_rules")
    op.drop_table("action_rules")
