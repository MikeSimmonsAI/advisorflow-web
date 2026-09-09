"""add job_runs table (GOD-10)

Revision ID: 1b3d5f7a9c0e
Revises: 9a2b4c6d8e0f
Create Date: 2026-09-09 00:00:00.000000

Creates the job_runs ledger — one row per background-loop invocation.
No tenant scope: these loops run for all orgs simultaneously.
No sensitive payloads: error_summary is capped at 512 chars (service
layer enforces this before writing).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "1b3d5f7a9c0e"
down_revision = "9a2b4c6d8e0f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("error_summary", sa.String(length=512), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_runs_job_name", "job_runs", ["job_name"])
    op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_started_at", table_name="job_runs")
    op.drop_index("ix_job_runs_job_name", table_name="job_runs")
    op.drop_table("job_runs")
