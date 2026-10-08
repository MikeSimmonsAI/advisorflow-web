"""add launch board tables

Revision ID: 5b7d9f1a3c2e
Revises: 4e6a8c0b2d1f
Create Date: 2026-10-08 00:00:00.000000

Control Room launch board (issue #21): durable project portfolio.

  launch_board_projects  project state + optimistic-concurrency `version`
  launch_board_events    append-only history, PK (project_id, seq), unique request_key

New tables only; downgrade drops them (and therefore the board's data).
Not executed against any shared or production database by the change that added it.
"""

from alembic import op
import sqlalchemy as sa

revision = "5b7d9f1a3c2e"
down_revision = "4e6a8c0b2d1f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "launch_board_projects",
        sa.Column("id",         sa.Integer(),   primary_key=True),
        sa.Column("name",       sa.String(200), nullable=False),
        sa.Column("name_key",   sa.String(200), nullable=False),
        sa.Column("lane",       sa.String(16),  nullable=False),
        sa.Column("priority",   sa.Integer(),   nullable=False),
        sa.Column("version",    sa.Integer(),   nullable=False),
        sa.Column("data",       sa.Text(),      nullable=False),
        sa.Column("created_at", sa.DateTime(),  server_default=sa.func.now()),
        sa.UniqueConstraint("name_key", name="uq_launch_board_projects_name_key"),
    )
    op.create_index("ix_launch_board_projects_order", "launch_board_projects", ["priority", "id"])
    op.create_table(
        "launch_board_events",
        sa.Column("project_id",  sa.Integer(),   sa.ForeignKey("launch_board_projects.id"), primary_key=True),
        sa.Column("seq",         sa.Integer(),   primary_key=True),
        sa.Column("at",          sa.String(40),  nullable=False),
        sa.Column("actor",       sa.String(200), nullable=False),
        sa.Column("action",      sa.String(40),  nullable=False),
        sa.Column("detail",      sa.Text(),      nullable=False),
        sa.Column("request_key", sa.String(120), nullable=True),
        sa.UniqueConstraint("request_key", name="uq_launch_board_events_request_key"),
    )


def downgrade():
    op.drop_table("launch_board_events")
    op.drop_index("ix_launch_board_projects_order", table_name="launch_board_projects")
    op.drop_table("launch_board_projects")
