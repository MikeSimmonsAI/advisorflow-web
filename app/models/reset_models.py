"""WORKSPACE DATA RESET - recorded, reversible clean-up of one workspace (Issue #507).

Built for Restland Cemetery and Funeral Home (one location, ~10.6k families
imported from several lists over the years) before its first program campaign.
Nothing here is Restland-specific in code; a run names its organization.

ADDITIVE ONLY. These are new tables; create_all() is the whole migration.

EVERY CHANGE IS AN ITEM. A run writes one WorkspaceResetItem per row it touches
(entity, before, after, why), and the rollback restores exactly those values.
A lead's ORIGINAL values are never overwritten: dispositions, normalized
phone/email and data notes sit beside the lead in LeadDisposition.

AUDIENCE IS A SNAPSHOT. CampaignAudienceMember records who a campaign WOULD
include or exclude and why, at a point in time. A member row never enrolls,
approves or sends anything; the program's own approval step does that.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.models.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class WorkspaceResetRun(Base):
    __tablename__ = "workspace_reset_runs"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False, index=True)
    run_key = Column(String, nullable=False)              # idempotency key, e.g. "restland-reset-1"
    mode = Column(String, nullable=False)                 # plan | apply | rollback
    status = Column(String, nullable=False, default="started")   # started | applied | planned | rolled_back | failed
    summary_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("organization_id", "run_key", "mode", name="uq_reset_run_key"),)


class WorkspaceResetItem(Base):
    __tablename__ = "workspace_reset_items"
    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, nullable=False, index=True)
    organization_id = Column(String, nullable=False)
    entity = Column(String, nullable=False)               # cadence_state | pipeline_conversation | cadence_template | campaign_family | ...
    entity_id = Column(String, nullable=False)
    action = Column(String, nullable=False)               # pause | create | set_default | ...
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    reverted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class LeadDisposition(Base):
    """One current clean-up decision per family. Original lead values untouched."""
    __tablename__ = "lead_dispositions"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    lead_id = Column(String, nullable=False)
    disposition = Column(String, nullable=False)    # retain | merged_duplicate | duplicate_review | manual_review | suppressed | test | email_only
    review_queue = Column(String, nullable=True)    # needs_classification | contact_data | duplicate | none
    phone_e164 = Column(String, nullable=True)      # normalized beside the original, never written onto the lead
    phone_issue = Column(String, nullable=True)     # missing | invalid | None
    email_issue = Column(String, nullable=True)     # missing | malformed | None
    name_issue = Column(String, nullable=True)      # incomplete | None
    canonical_lead_id = Column(String, nullable=True)
    shares_phone_with = Column(Integer, nullable=False, default=0)
    notes_json = Column(Text, nullable=True)
    run_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("organization_id", "lead_id", name="uq_lead_disposition"),
                      Index("ix_lead_disposition_org_disp", "organization_id", "disposition"))


class CampaignAudienceMember(Base):
    """Who a campaign would include or exclude, and why - a snapshot, not an enrollment."""
    __tablename__ = "campaign_audience_members"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    campaign_key = Column(String, nullable=False)
    lead_id = Column(String, nullable=False)
    status = Column(String, nullable=False)          # proposed | eligible | excluded
    segment = Column(String, nullable=False)         # ready | prior_conversation | past_appointment | earlier_sequence | ...
    reasons_json = Column(Text, nullable=True)
    batch = Column(Integer, nullable=True)           # 1 for the staged first batch
    destination = Column(String, nullable=True)      # E.164 the batch would text
    is_staged = Column(Boolean, nullable=False, default=False)
    run_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "campaign_key", "lead_id", name="uq_campaign_audience"),
                      Index("ix_campaign_audience_org_key_status", "organization_id", "campaign_key", "status"))


class CampaignCycleMember(Base):
    """One family's place in one round of a campaign (Issue #507 rotation).

    Auditable record of the rotation: when the family was staged, when its
    journey started, when and why it ended, the last real outreach, and the
    earliest date it may be REVIEWED for another round (last outreach + the
    rest period). Eligibility is permission to appear in a reviewed pool -
    never automatic enrollment. Destinations (phone/email) are stored so a
    rest or an active round blocks every record that shares them."""
    __tablename__ = "campaign_cycle_members"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    campaign_key = Column(String, nullable=False)
    cycle_key = Column(String, nullable=False)        # e.g. "ffr-batch-1"
    lead_id = Column(String, nullable=False)
    round_number = Column(Integer, nullable=False, default=1)   # this family's 1st, 2nd ... round
    phone_e164 = Column(String, nullable=True)
    email_norm = Column(String, nullable=True)
    status = Column(String, nullable=False, default="staged")   # staged | active | completed | stopped | withdrawn
    started_at = Column(DateTime, nullable=True)       # the family's journey start
    completed_at = Column(DateTime, nullable=True)
    completion_reason = Column(String, nullable=True)  # no_reply | undeliverable | replied | booked | opted_out | cancelled_appointment | unapproved
    last_outreach_at = Column(DateTime, nullable=True)
    next_eligible_at = Column(DateTime, nullable=True)
    notes_json = Column(Text, nullable=True)
    run_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "cycle_key", "lead_id", name="uq_campaign_cycle_member"),
                      Index("ix_cycle_members_org_status", "organization_id", "status"),
                      Index("ix_cycle_members_phone", "organization_id", "phone_e164"),
                      Index("ix_cycle_members_email", "organization_id", "email_norm"))



class AppointmentNotification(Base):
    """One appointment message for one channel (Issue #507): the booking
    confirmation, a reminder, the change/cancel acknowledgement or the survey
    invitation. Separate from campaign touches and from the rest period.

    `version` is the appointment time it was written for: a reschedule
    supersedes every row of the old version, so a stale reminder can never go.
    Unique (booking, purpose, channel, version) = one message per channel per
    appointment time, however many workers or webhook retries ask."""
    __tablename__ = "appointment_notifications"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    booking_id = Column(String, nullable=False)
    lead_id = Column(String, nullable=False)
    purpose = Column(String, nullable=False)        # confirmation | reminder_24h | reminder_2h | updated | cancelled | survey
    channel = Column(String, nullable=False)        # sms | email
    version = Column(String, nullable=False)        # booked time ISO (local) it was written for
    due_at = Column(DateTime, nullable=True)        # naive UTC
    status = Column(String, nullable=False, default="scheduled")
    # scheduled | claimed | sent | failed | retry | skipped | superseded | cancelled
    reason = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    attempted_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    message_id = Column(String, nullable=True)      # messages.id / email_messages.id
    provider_id = Column(String, nullable=True)
    delivery_state = Column(String, nullable=True)  # read from the message's own receipts
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("booking_id", "purpose", "channel", "version", name="uq_appt_notification"),
                      Index("ix_appt_notifications_due", "organization_id", "status", "due_at"))


class AppointmentSurvey(Base):
    """The post-visit survey for ONE completed appointment (Issue #507).
    One unguessable token; the text and the email both open it; one answer."""
    __tablename__ = "appointment_surveys"
    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    booking_id = Column(String, nullable=False, unique=True)
    lead_id = Column(String, nullable=False)
    token = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    rating = Column(Integer, nullable=True)          # 1-5
    helpful = Column(String, nullable=True)          # yes | somewhat | no
    comments = Column(Text, nullable=True)
    follow_up = Column(Boolean, nullable=True)
    follow_up_method = Column(String, nullable=True)  # phone | text | email
    submitted_at = Column(DateTime, nullable=True)
    task_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
