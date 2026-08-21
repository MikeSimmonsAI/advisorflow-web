"""add phone and job_title to users

Revision ID: 7d4e8b2f1a9c
Revises: 3c9f2a1b7d8e
Create Date: 2026-08-21

Personal contact phone (not Twilio) and job title for profile completion.
"""
from alembic import op
import sqlalchemy as sa

revision = '7d4e8b2f1a9c'
down_revision = '3c9f2a1b7d8e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('job_title', sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('job_title')
        batch_op.drop_column('phone')
