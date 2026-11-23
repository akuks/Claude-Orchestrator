"""launch list: project_launches, launch_items, projects.launch_seq, tasks.launch_item_id (phase 5b)

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("launch_seq", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "project_launches",
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("launch_date", sa.Date(), nullable=True),
        sa.Column("critical_paths", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "launch_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("project_id", sa.String(32), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("section", sa.String(8), nullable=False, server_default="before"),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_by", sa.String(32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("project_id", "number", name="uq_launch_items_project_number"),
    )
    op.create_index("ix_launch_items_project_id", "launch_items", ["project_id"])
    with op.batch_alter_table("tasks") as b:
        b.add_column(sa.Column("launch_item_id", sa.String(32), nullable=True))
        b.create_foreign_key("fk_tasks_launch_item_id", "launch_items", ["launch_item_id"], ["id"],
                             ondelete="SET NULL")
        b.create_index("ix_tasks_launch_item_id", ["launch_item_id"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as b:
        b.drop_index("ix_tasks_launch_item_id")
        b.drop_constraint("fk_tasks_launch_item_id", type_="foreignkey")
        b.drop_column("launch_item_id")
    op.drop_index("ix_launch_items_project_id", "launch_items")
    op.drop_table("launch_items")
    op.drop_table("project_launches")
    with op.batch_alter_table("projects") as b:
        b.drop_column("launch_seq")
