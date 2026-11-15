"""Contractor onboarding, the parts that are not the application itself:
invite sends, document review, and the in-app agreement signing.

Statuses are kept apart on purpose - they are different facts:
  invite email   SalesInviteSend.email_ok   the provider accepted the message
  account        User.must_change_password  the person opened the emailed link
                 and set a password (proves control of that email address)
  document       SalesOnboardingReview      an owner looked at THIS file and
                 accepted it or asked for a correction (a manual review, not an
                 identity-verification service)
  agreement      SalesAgreementEnvelope     sent -> signed (-> countersigned)
  approval       SalesOnboarding.status     the owner's final yes
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text

from app.models.models import Base, gen_uuid

REVIEW_ACCEPTED = "accepted"
REVIEW_NEEDS_CORRECTION = "needs_correction"

TPL_DRAFT = "draft"
TPL_APPROVED = "approved"
TPL_RETIRED = "retired"

ENV_SENT = "sent"
ENV_AWAITING_COUNTERSIGN = "awaiting_countersign"
ENV_COMPLETED = "completed"
ENV_CORRECTION = "correction_needed"
ENV_VOID = "void"
ENV_LABELS = {ENV_SENT: "Awaiting signature", ENV_AWAITING_COUNTERSIGN: "Signed — awaiting countersignature",
              ENV_COMPLETED: "Signed", ENV_CORRECTION: "Correction needed", ENV_VOID: "Withdrawn"}


class SalesInviteSend(Base):
    __tablename__ = "sales_invite_sends"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sent_by = Column(String, ForeignKey("users.id"), nullable=True)
    kind = Column(String, nullable=False, default="invite")      # invite | resend | corrections
    link_issued = Column(Boolean, nullable=False, default=False)  # a new set-password link was issued
    email_ok = Column(Boolean, nullable=False, default=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesOnboardingReview(Base):
    __tablename__ = "sales_onboarding_reviews"

    id = Column(String, primary_key=True, default=gen_uuid)
    onboarding_id = Column(String, ForeignKey("sales_onboardings.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(String, ForeignKey("sales_onboarding_documents.id", ondelete="CASCADE"),
                         nullable=False, unique=True, index=True)
    status = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    reviewed_by = Column(String, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesAgreementTemplate(Base):
    __tablename__ = "sales_agreement_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    version_label = Column(String, nullable=False)
    exhibit_version = Column(String, nullable=False)
    body_html = Column(Text, nullable=False)
    status = Column(String, nullable=False, default=TPL_DRAFT)
    requires_countersign = Column(Boolean, nullable=False, default=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)


class SalesAgreementEnvelope(Base):
    __tablename__ = "sales_agreement_envelopes"

    id = Column(String, primary_key=True, default=gen_uuid)
    onboarding_id = Column(String, ForeignKey("sales_onboardings.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = Column(String, ForeignKey("sales_agreement_templates.id"), nullable=False)
    status = Column(String, nullable=False, default=ENV_SENT)
    title = Column(String, nullable=False)
    version_label = Column(String, nullable=False)
    exhibit_version = Column(String, nullable=False)
    document_html = Column(Text, nullable=False)
    document_sha256 = Column(String, nullable=False)
    sent_by = Column(String, ForeignKey("users.id"), nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    viewed_at = Column(DateTime, nullable=True)
    consent_at = Column(DateTime, nullable=True)
    signer_typed_name = Column(String, nullable=True)
    signed_at = Column(DateTime, nullable=True)
    sign_ip = Column(String, nullable=True)
    sign_user_agent = Column(String, nullable=True)
    countersigner_id = Column(String, ForeignKey("users.id"), nullable=True)
    countersign_typed_name = Column(String, nullable=True)
    countersigned_at = Column(DateTime, nullable=True)
    countersign_ip = Column(String, nullable=True)
    final_document_id = Column(String, ForeignKey("sales_onboarding_documents.id"), nullable=True)
    final_sha256 = Column(String, nullable=True)
    note = Column(Text, nullable=True)
