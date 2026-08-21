"""add session_token to users

Revision ID: 8f3a1c2d4e5b
Revises: 02907fcdb80c
Create Date: 2026-08-21 00:00:00.000000

Adds a session_token column to the users table for single-session
enforcement. On every login a new UUID is generated and stored here
AND embedded as the JWT's `jti` claim. The auth dependency rejects
any token whose jti doesn't match this column, instantly invalidating
previous sessions (and force-logout / deactivation by admins).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '8f3a1c2d4e5b'
down_revision = '02907fcdb80c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('session_token', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'session_token')
