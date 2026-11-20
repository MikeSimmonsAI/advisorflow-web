"""CLIENT GROWTH - discovery questionnaires and proposal preparation.

A platform capability of EvoSys Pro (not insurance-only, not one prospect's
app): find out what a prospective client wants to grow, collect their answers
through a private link, and prepare a proposal from them.

THE PROSPECT IS NOT HERE. Prospects stay `opportunities` rows on the EvoSys Pro
sales team (the existing business-development records) and their activity stays
in `opportunity_events`. These tables add only what discovery needs:

    client_growth_templates    a versioned questionnaire definition. A new
                               version is a new row; a submission keeps the
                               id and version it was answered against.
    client_growth_invitations  one private link for one prospect and one
                               template. The link token is never stored in
                               the clear: its SHA-256 finds the row, and an
                               encrypted copy lets staff copy the link again.
                               Creating a link is not sending it.
    client_growth_submissions  the prospect's original answers, exactly as
                               received. One per invitation (unique), which
                               is what stops a double-click making two.
    client_growth_tasks        the proposal-preparation work a submission
                               creates, linked to the prospect and submission.

Nothing in these tables sends anything to anybody.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, JSON, UniqueConstraint

from app.models.models import Base, gen_uuid

INVITE_ACTIVE = "active"
INVITE_SUBMITTED = "submitted"
INVITE_REVOKED = "revoked"
INVITE_STATUSES = (INVITE_ACTIVE, INVITE_SUBMITTED, INVITE_REVOKED)   # "expired" is computed

SUB_SUBMITTED = "submitted"
SUB_UNDER_REVIEW = "under_review"
SUB_PROPOSAL_PREPARED = "proposal_prepared"
SUB_STATUSES = (SUB_SUBMITTED, SUB_UNDER_REVIEW, SUB_PROPOSAL_PREPARED)

TASK_OPEN = "open"
TASK_DONE = "done"


class ClientGrowthTemplate(Base):
    __tablename__ = "client_growth_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    key = Column(String(80), nullable=False)            # "agency_growth_insurance"
    version = Column(Integer, nullable=False, default=1)
    vertical = Column(String(40), nullable=True)        # "insurance"; null = any business
    name = Column(String, nullable=False)               # staff-facing
    definition = Column(JSON, nullable=False)           # sections, fields, public wording
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("key", "version", name="uq_client_growth_template_version"),)


class ClientGrowthInvitation(Base):
    __tablename__ = "client_growth_invitations"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(String, ForeignKey("client_growth_templates.id"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    token_enc = Column(Text, nullable=True)             # encrypted copy, for "Copy link" again
    # What the form is pre-filled with and whose name it is prepared for.
    # Editable by the prospect; never treated as their answer until submitted.
    prepared_for = Column(String, nullable=True)
    prefill = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default=INVITE_ACTIVE)
    expires_at = Column(DateTime, nullable=True)
    # Staff told us they sent it (and how). Delivery is never known here.
    sent_at = Column(DateTime, nullable=True)
    sent_by = Column(String, ForeignKey("users.id"), nullable=True)
    sent_note = Column(String, nullable=True)
    last_copied_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)         # first time the form was loaded
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String, ForeignKey("users.id"), nullable=True)
    revoke_reason = Column(Text, nullable=True)
    replaces_id = Column(String, ForeignKey("client_growth_invitations.id"), nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_client_growth_inv_opp", "opportunity_id"),
                      Index("ix_client_growth_inv_status", "brand_sales_org_id", "status"))


class ClientGrowthSubmission(Base):
    __tablename__ = "client_growth_submissions"

    id = Column(String, primary_key=True, default=gen_uuid)
    invitation_id = Column(String, ForeignKey("client_growth_invitations.id", ondelete="CASCADE"),
                           nullable=False, unique=True)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(String, ForeignKey("client_growth_templates.id"), nullable=False)
    template_key = Column(String(80), nullable=False)
    template_version = Column(Integer, nullable=False)
    answers = Column(JSON, nullable=False)              # original, authoritative
    status = Column(String(30), nullable=False, default=SUB_SUBMITTED)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    review_started_at = Column(DateTime, nullable=True)
    review_started_by = Column(String, ForeignKey("users.id"), nullable=True)
    proposal_prepared_at = Column(DateTime, nullable=True)
    proposal_prepared_by = Column(String, ForeignKey("users.id"), nullable=True)
    # Internal, AI-labelled reading of the answers. Never replaces them.
    ai_summary = Column(JSON, nullable=True)
    ai_summary_status = Column(String(20), nullable=True)   # done | failed
    ai_summary_error = Column(Text, nullable=True)
    ai_summary_at = Column(DateTime, nullable=True)
    ai_summary_by = Column(String, ForeignKey("users.id"), nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_client_growth_sub_status", "brand_sales_org_id", "status"),
                      Index("ix_client_growth_sub_opp", "opportunity_id"))


class ClientGrowthTask(Base):
    __tablename__ = "client_growth_tasks"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False)
    submission_id = Column(String, ForeignKey("client_growth_submissions.id", ondelete="CASCADE"),
                           nullable=False, unique=True)
    title = Column(String, nullable=False)
    status = Column(String(20), nullable=False, default=TASK_OPEN)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String, ForeignKey("users.id"), nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)


class ClientGrowthProposal(Base):
    __tablename__ = "client_growth_proposals"

    id = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    html = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
