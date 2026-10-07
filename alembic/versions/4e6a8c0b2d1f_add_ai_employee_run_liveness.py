"""add liveness columns to ai_employee_runs

Revision ID: 4e6a8c0b2d1f
Revises: 1b3d5f7a9c0e
Create Date: 2026-10-07 00:00:00.000000

Adds three NULLABLE columns (last_heartbeat_at, current_stage, lease_seconds).
No default, no backfill, no table rewrite: existing rows keep NULL, which the
run-evidence reader treats as "no heartbeat recorded" (legacy started_at
fallback, labelled as such).

The workforce tables are created by Base.metadata.create_all(), not by this
alembic chain, so the upgrade is guarded: it only alters the table if it
exists and only adds a column that is missing. The same columns are also
listed in app/auto_migrate.py COLUMNS_TO_ADD (the repository's runtime path).
NOT applied by this change to any shared or production database.
"""
from alembic import op
import sqlalchemy as sa

revision = "4e6a8c0b2d1f"
down_revision = "1b3d5f7a9c0e"
branch_labels = None
depends_on = None

_TABLE = "ai_employee_runs"
_COLUMNS = (
    ("last_heartbeat_at", sa.DateTime()),
    ("current_stage", sa.String()),
    ("lease_seconds", sa.Integer()),
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
