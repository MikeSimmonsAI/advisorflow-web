"""add local invoice draft tables

Revision ID: 6c8e0a2d4f1b
Revises: 5b7d9f1a3c2e
Create Date: 2026-10-08 00:00:00.000000

Local invoice drafts (draft / approval_ready / void). Separate from the read-only
Stripe mirror billing_invoices. New tables only; downgrade drops them (and their data).
Not executed against any shared or production database by the change that added it.
"""

from alembic import op
import sqlalchemy as sa

revision = "6c8e0a2d4f1b"
down_revision = "5b7d9f1a3c2e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoice_drafts",
        sa.Column("id",              sa.String(36),  primary_key=True),
        sa.Column("organization_id", sa.String(),    sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state",           sa.String(16),  nullable=False),
        sa.Column("version",         sa.Integer(),   nullable=False),
        sa.Column("currency",        sa.String(3),   nullable=False),
        sa.Column("customer_name",   sa.String(200), nullable=False),
        sa.Column("memo",            sa.Text(),      nullable=False),
        sa.Column("discount_cents",  sa.BigInteger(), nullable=False),
        sa.Column("tax_cents",       sa.BigInteger(), nullable=False),
        sa.Column("next_line_no",    sa.Integer(),   nullable=False),
        sa.Column("created_by",      sa.String(200), nullable=False),
        sa.Column("created_at",      sa.DateTime(),  server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_drafts_org", "invoice_drafts", ["organization_id", "created_at"])
    op.create_table(
        "invoice_draft_lines",
        sa.Column("draft_id",         sa.String(36),  sa.ForeignKey("invoice_drafts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("line_no",          sa.Integer(),   primary_key=True),
        sa.Column("description",      sa.String(200), nullable=False),
        sa.Column("quantity",         sa.Integer(),   nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "invoice_draft_events",
        sa.Column("draft_id",        sa.String(36),  sa.ForeignKey("invoice_drafts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seq",             sa.Integer(),   primary_key=True),
        sa.Column("organization_id", sa.String(),    nullable=False),
        sa.Column("at",              sa.String(40),  nullable=False),
        sa.Column("actor",           sa.String(200), nullable=False),
        sa.Column("action",          sa.String(40),  nullable=False),
        sa.Column("detail",          sa.Text(),      nullable=False),
        sa.Column("request_key",     sa.String(120), nullable=True),
        sa.UniqueConstraint("organization_id", "request_key", name="uq_invoice_draft_events_request_key"),
    )


def downgrade():
    op.drop_table("invoice_draft_events")
    op.drop_table("invoice_draft_lines")
    op.drop_index("ix_invoice_drafts_org", table_name="invoice_drafts")
    op.drop_table("invoice_drafts")
