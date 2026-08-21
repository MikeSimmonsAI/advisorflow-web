"""add proposal portal tables

Revision ID: 9a2b4c6d8e0f
Revises: 8f3a1c2d4e5b
Create Date: 2026-08-21 00:00:00.000000

Creates four new tables for the EvoSys Pro client proposal portal:

  proposals         — document metadata, status, branding overrides
  proposal_blocks   — ordered content blocks (text/image/pdf/video/cta/divider)
  proposal_tokens   — magic-link access tokens sent to clients
  proposal_views    — analytics: open/scroll/download events per portal session

All four tables are org-scoped and cascade-delete cleanly via FK relationships.
"""

from alembic import op
import sqlalchemy as sa

revision = "9a2b4c6d8e0f"
down_revision = "8f3a1c2d4e5b"
branch_labels = None
depends_on = None


def upgrade():
    # ── proposals ─────────────────────────────────────────────────────────────
    op.create_table(
        "proposals",
        sa.Column("id",                 sa.String(),   primary_key=True),
        sa.Column("organization_id",    sa.String(),   sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by_id",      sa.String(),   sa.ForeignKey("users.id"),         nullable=False),
        sa.Column("title",              sa.String(),   nullable=False),
        sa.Column("subtitle",           sa.String(),   nullable=True),
        sa.Column("client_name",        sa.String(),   nullable=True),
        sa.Column("client_email",       sa.String(),   nullable=True),
        sa.Column("client_company",     sa.String(),   nullable=True),
        sa.Column("status",             sa.String(),   nullable=False, server_default="draft"),
        sa.Column("branding_override",  sa.Text(),     nullable=True),
        sa.Column("expires_at",         sa.DateTime(), nullable=True),
        sa.Column("deleted_at",         sa.DateTime(), nullable=True),
        sa.Column("created_at",         sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at",         sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_proposals_organization_id", "proposals", ["organization_id"])

    # ── proposal_blocks ───────────────────────────────────────────────────────
    op.create_table(
        "proposal_blocks",
        sa.Column("id",          sa.String(),  primary_key=True),
        sa.Column("proposal_id", sa.String(),  sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("block_type",  sa.String(),  nullable=False),
        sa.Column("position",    sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content",     sa.Text(),    nullable=True),
        sa.Column("file_url",    sa.String(),  nullable=True),
        sa.Column("file_name",   sa.String(),  nullable=True),
        sa.Column("file_size",   sa.Integer(), nullable=True),
        sa.Column("created_at",  sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_proposal_blocks_proposal_id", "proposal_blocks", ["proposal_id"])

    # ── proposal_tokens ───────────────────────────────────────────────────────
    op.create_table(
        "proposal_tokens",
        sa.Column("id",                  sa.String(),   primary_key=True),
        sa.Column("proposal_id",         sa.String(),   sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token",               sa.String(),   unique=True, nullable=False),
        sa.Column("recipient_email",     sa.String(),   nullable=True),
        sa.Column("recipient_name",      sa.String(),   nullable=True),
        sa.Column("expires_at",          sa.DateTime(), nullable=True),
        sa.Column("first_redeemed_at",   sa.DateTime(), nullable=True),
        sa.Column("revoked_at",          sa.DateTime(), nullable=True),
        sa.Column("created_at",          sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_proposal_tokens_proposal_id", "proposal_tokens", ["proposal_id"])
    op.create_index("ix_proposal_tokens_token",       "proposal_tokens", ["token"])

    # ── proposal_views ────────────────────────────────────────────────────────
    op.create_table(
        "proposal_views",
        sa.Column("id",               sa.String(),   primary_key=True),
        sa.Column("proposal_id",      sa.String(),   sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_id",         sa.String(),   sa.ForeignKey("proposal_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("opened_at",        sa.DateTime(), server_default=sa.func.now()),
        sa.Column("closed_at",        sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(),  nullable=True),
        sa.Column("max_scroll_pct",   sa.Integer(),  server_default="0"),
        sa.Column("downloaded",       sa.Boolean(),  server_default="false"),
        sa.Column("viewer_city",      sa.String(),   nullable=True),
    )
    op.create_index("ix_proposal_views_proposal_id", "proposal_views", ["proposal_id"])
    op.create_index("ix_proposal_views_token_id",    "proposal_views", ["token_id"])


def downgrade():
    op.drop_table("proposal_views")
    op.drop_table("proposal_tokens")
    op.drop_table("proposal_blocks")
    op.drop_table("proposals")
