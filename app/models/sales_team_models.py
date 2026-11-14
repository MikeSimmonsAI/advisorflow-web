"""EVOSYS PRO SALES TEAM — onboarding, departures and per-deal team credit.

The team itself is the existing `memberships` row on EvoSys Pro Sales (role,
reports_to, is_active) plus the named brand grants. These tables hold only
what that row cannot:

  sales_onboardings            one per person per brand: the application and
                               where it stands (in progress → submitted →
                               approved). Approval is what turns selling on.
  sales_onboarding_documents   the checklist files. Bytes live in the
                               platform's private storage (wholesale_files);
                               this row only says where. Never logged, never
                               in a URL.
  sales_departures             the date someone left the company. From that
                               date their share of later collections is
                               unclaimed (EvoSys keeps it, reported by deal).
  sales_deal_credits           who was credited on a deal - rep, Area Sales
                               Manager, Sr EVP - and the rules version, frozen
                               when the customer accepted. Moving a rep later
                               never changes who is paid on an earlier deal.
  sales_unclaimed_shares       each role share that was not paid, by deal and
                               collection, with the amount, role and reason.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer,
                        Numeric, String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid

ONB_IN_PROGRESS = "in_progress"
ONB_SUBMITTED = "submitted"
ONB_APPROVED = "approved"
ONB_RETURNED = "returned"          # sent back for a fix
ONB_STATES = (ONB_IN_PROGRESS, ONB_SUBMITTED, ONB_APPROVED, ONB_RETURNED)

# The checklist. All required.
DOC_AGREEMENT = "contractor_agreement"
DOC_W9 = "w9"
DOC_ID_FRONT = "photo_id_front"
DOC_ID_BACK = "photo_id_back"
DOC_I9 = "i9"
DOC_KINDS = (DOC_AGREEMENT, DOC_W9, DOC_ID_FRONT, DOC_ID_BACK, DOC_I9)
DOC_LABELS = {
    DOC_AGREEMENT: "Signed independent contractor agreement",
    DOC_W9: "W-9",
    DOC_ID_FRONT: "Photo ID — front",
    DOC_ID_BACK: "Photo ID — back",
    DOC_I9: "I-9 paperwork",
}


class SalesOnboarding(Base):
    __tablename__ = "sales_onboardings"
    __table_args__ = (UniqueConstraint("brand_sales_org_id", "user_id",
                                       name="uq_sales_onboarding_person"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False, default=ONB_IN_PROGRESS)
    # The application. Plain contact facts only - no SSN here (that is on the
    # W-9 file, which only owners can open).
    legal_name = Column(String, nullable=True)
    home_address = Column(Text, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    returned_note = Column(Text, nullable=True)
    invited_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesOnboardingDocument(Base):
    __tablename__ = "sales_onboarding_documents"

    id = Column(String, primary_key=True, default=gen_uuid)
    onboarding_id = Column(String, ForeignKey("sales_onboardings.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    kind = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_current = Column(Boolean, nullable=False, default=True)
    storage_backend = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String, nullable=False)
    uploaded_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesDeparture(Base):
    __tablename__ = "sales_departures"
    __table_args__ = (UniqueConstraint("brand_sales_org_id", "user_id",
                                       name="uq_sales_departure_person"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    departed_at = Column(DateTime, nullable=False)
    recorded_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesDealCredit(Base):
    __tablename__ = "sales_deal_credits"

    id = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, unique=True, index=True)
    brand_sales_org_id = Column(String, nullable=False, index=True)
    rules_version = Column(String, nullable=False)
    rep_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    asm_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    sevp_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    accepted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesUnclaimedShare(Base):
    __tablename__ = "sales_unclaimed_shares"
    __table_args__ = (UniqueConstraint("opportunity_id", "collection_reference", "role",
                                       name="uq_sales_unclaimed_once"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, nullable=False, index=True)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    collection_reference = Column(String, nullable=False)
    collected_at = Column(DateTime, nullable=False)
    role = Column(String, nullable=False)              # rep | asm | sevp
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason = Column(String, nullable=False)            # seat empty | left the company
    amount = Column(Numeric(12, 2), nullable=False)
    rules_version = Column(String, nullable=False)
    reversed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesCollectionSplit(Base):
    """How one collected payment on an EvoSys Pro Sales deal was split between
    setup and monthly service. Written once per (deal, collection) and reused,
    so re-running the earn step can never classify the same money twice."""
    __tablename__ = "sales_collection_splits"
    __table_args__ = (UniqueConstraint("opportunity_id", "collection_reference",
                                       name="uq_sales_collection_split_once"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    collection_reference = Column(String, nullable=False)
    collected_amount = Column(Numeric(12, 2), nullable=False)
    setup_part = Column(Numeric(12, 2), nullable=False)
    monthly_part = Column(Numeric(12, 2), nullable=False)
    rules_version = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
