"""LOCAL INVOICE DRAFTS - tenant-scoped, source-only, stops at approval_ready.

Deliberately separate from billing_models.BillingInvoice, which is a read-only
mirror of Stripe invoices. Nothing here is sent, finalized, charged or synced:
`provider_status` is always "not_started" and no code path reads these tables to
call a provider.

  invoice_drafts        one row per draft. Money is integer cents. `version` is the
                        optimistic-concurrency counter (UPDATE ... WHERE version = ?).
  invoice_draft_lines   editable only while state = 'draft'. PK (draft_id, line_no).
  invoice_draft_events  append-only audit. PK (draft_id, seq); (organization_id,
                        request_key) is unique so a replayed request is a no-op.
                        Application code never updates or deletes an event.
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func

from app.models.models import Base


class InvoiceDraft(Base):
    __tablename__ = "invoice_drafts"

    id = Column(String(36), primary_key=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    state = Column(String(16), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    currency = Column(String(3), nullable=False, default="usd")
    customer_name = Column(String(200), nullable=False)
    memo = Column(Text, nullable=False, default="")
    discount_cents = Column(BigInteger, nullable=False, default=0)
    tax_cents = Column(BigInteger, nullable=False, default=0)
    next_line_no = Column(Integer, nullable=False, default=1)
    created_by = Column(String(200), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_invoice_drafts_org", "organization_id", "created_at"),)


class InvoiceDraftLine(Base):
    __tablename__ = "invoice_draft_lines"

    draft_id = Column(String(36), ForeignKey("invoice_drafts.id", ondelete="CASCADE"), primary_key=True)
    line_no = Column(Integer, primary_key=True)
    description = Column(String(200), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price_cents = Column(BigInteger, nullable=False)


class InvoiceDraftEvent(Base):
    __tablename__ = "invoice_draft_events"

    draft_id = Column(String(36), ForeignKey("invoice_drafts.id", ondelete="CASCADE"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    organization_id = Column(String, nullable=False)
    at = Column(String(40), nullable=False)
    actor = Column(String(200), nullable=False)
    action = Column(String(40), nullable=False)
    detail = Column(Text, nullable=False)
    request_key = Column(String(120), nullable=True)

    __table_args__ = (UniqueConstraint("organization_id", "request_key", name="uq_invoice_draft_events_request_key"),)
