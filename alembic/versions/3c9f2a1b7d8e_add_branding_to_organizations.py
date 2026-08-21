"""add extended branding columns to organizations

Revision ID: 3c9f2a1b7d8e
Revises: 8f3a1c2d4e5b
Create Date: 2026-08-21

Adds the four branding fields not already present on the organizations table.
(brand_name, brand_logo_url, brand_color_primary, brand_color_accent already exist
from the initial schema.)
"""
from alembic import op
import sqlalchemy as sa

revision = '3c9f2a1b7d8e'
down_revision = '8f3a1c2d4e5b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations') as batch_op:
        batch_op.add_column(sa.Column('favicon_url', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('tagline', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('support_email', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('email_sender_name', sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table('organizations') as batch_op:
        batch_op.drop_column('email_sender_name')
        batch_op.drop_column('support_email')
        batch_op.drop_column('tagline')
        batch_op.drop_column('favicon_url')
