"""Deal-linked quotes from the Salesperson Deal Builder.

SalesQuoteDraft     the builder's saved quote for ONE deal (latest revision),
                    so a rep can reload, reopen and revise it. Customer prices
                    only: no commission figure is ever stored here.
SalesQuoteSnapshot  the quote a particular proposal version quotes. Written
                    while that proposal is still editable; once it is sent it
                    is never touched again, so a customer's document never
                    moves underneath them.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text

from app.models.models import Base, gen_uuid


class SalesQuoteDraft(Base):
    __tablename__ = "sales_quote_drafts"

    id = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, unique=True, index=True)
    revision = Column(Integer, nullable=False, default=1)
    plan_key = Column(String, nullable=False)
    setup_total = Column(Numeric(12, 2), nullable=False)
    monthly_total = Column(Numeric(12, 2), nullable=False)
    addon_setup = Column(Numeric(12, 2), nullable=False, default=0)
    monthly_override = Column(String, nullable=True)       # "yes" when the SaaS monthly was typed
    data_json = Column(Text, nullable=False)                # builder state + customer lines
    rules_version = Column(String, nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesQuoteSnapshot(Base):
    __tablename__ = "sales_quote_snapshots"

    id = Column(String, primary_key=True, default=gen_uuid)
    proposal_id = Column(String, ForeignKey("proposals.id", ondelete="CASCADE"),
                         nullable=False, unique=True, index=True)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    draft_revision = Column(Integer, nullable=False)
    data_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
