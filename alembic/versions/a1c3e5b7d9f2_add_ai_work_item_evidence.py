"""add durable evidence + row_version to ai_work_items

Revision ID: a1c3e5b7d9f2
Revises: 6c8e0a2d4f1b
Create Date: 2026-10-08 00:00:00.000000

Adds NULLABLE columns only: row_version (durable optimistic version), the four
independent phase flags (source_complete, tests_complete, deployed,
live_verified) and the evidence detail/source/timestamp columns. No default,
no backfill, no table rewrite: legacy rows keep NULL, which readers treat as
"not recorded" (unavailable) and row_version NULL as 0.

Guarded like 4e6a8c0b2d1f: skips if the table is absent, adds only missing
columns; downgrade drops only columns that exist. Authored and contract-tested
only; NOT applied to any shared or production database.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c3e5b7d9f2"
down_revision = "6c8e0a2d4f1b"
branch_labels = None
depends_on = None

_TABLE = "ai_work_items"
_COLUMNS = (
    ("row_version", sa.Integer()),
    ("source_complete", sa.Boolean()),
    ("tests_complete", sa.Boolean()),
    ("deployed", sa.Boolean()),
    ("live_verified", sa.Boolean()),
    ("evidence_commit_sha", sa.String()),
    ("evidence_test_command", sa.String()),
    ("evidence_test_result", sa.String()),
    ("evidence_test_count", sa.Integer()),
    ("evidence_deploy_ref", sa.String()),
    ("evidence_deploy_status", sa.String()),
    ("evidence_live_ref", sa.String()),
    ("evidence_live_status", sa.String()),
    ("evidence_checkpoint_summary", sa.String()),
    ("evidence_source", sa.String()),
    ("evidence_recorded_at", sa.DateTime()),
)


def _existing_columns():
    insp = sa.inspect(op.get_bind())
    if _TABLE not in insp.get_table_names():
        return None
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    have = _existing_columns()
    if have is None:
        return
    for name, type_ in _COLUMNS:
        if name not in have:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    have = _existing_columns()
    if have is None:
        return
    for name, _ in reversed(_COLUMNS):
        if name in have:
            op.drop_column(_TABLE, name)
