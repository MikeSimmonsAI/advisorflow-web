"""EvoSys Sales program (owner request 2026-11-16).

EvoSys's own sales team signing up businesses (cleaning companies first). The
leads themselves stay `opportunities` rows on the EvoSys Pro brand sales team;
these tables add only what the automatic follow-up needs:

    evosys_outreach_settings  one row per brand sales team: the steps, the
                              offer, the sender, the mailing address, whether
                              Mike approved them and whether sending is on
    evosys_outreach_states    one row per lead in the follow-up: next step and
                              when it is due, or why it stopped
    evosys_outreach_messages  every message out (and every reply logged), so a
                              lead's page shows the whole conversation

Nothing here sends by itself: app/services/evosys_sales/outreach.run_pass does,
and only when the settings are approved AND switched on.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, JSON

from app.models.models import Base, gen_uuid


class EvoSysOutreachSettings(Base):
    __tablename__ = "evosys_outreach_settings"

    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                primary_key=True)
    steps = Column(JSON, nullable=True)            # [{"day":0,"channel":"email","subject":..,"body":..}]
    offer = Column(Text, nullable=True)
    booking_url = Column(String, nullable=True)
    sender_name = Column(String, nullable=True)
    from_email = Column(String, nullable=True)
    reply_to_email = Column(String, nullable=True)
    mailing_address = Column(Text, nullable=True)
    daily_limit = Column(Integer, default=60, nullable=False)
    sending_on = Column(Boolean, default=False, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)


class EvoSysOutreachState(Base):
    __tablename__ = "evosys_outreach_states"

    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, default="active", nullable=False)   # active | stopped | finished
    stop_reason = Column(String, nullable=True)                  # replied | booked | opted_out | no_email | stage | manual
    next_step = Column(Integer, default=0, nullable=False)       # index into settings.steps
    next_due_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    stopped_at = Column(DateTime, nullable=True)
    opted_out_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    # Short random code in this lead's Reply-To (info+evs-<code>@evosyspro.live):
    # a reply comes back addressed to it, which names the lead without guessing.
    reply_code = Column(String(16), nullable=True, unique=True)

    __table_args__ = (
        Index("ix_evosys_outreach_due", "brand_sales_org_id", "status", "next_due_at"),
    )


class EvoSysOutreachMessage(Base):
    __tablename__ = "evosys_outreach_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False)
    direction = Column(String, default="out", nullable=False)   # out | in
    channel = Column(String, default="email", nullable=False)   # email | text | call
    step_no = Column(Integer, nullable=True)
    to_address = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String, nullable=False)                      # sent | failed | held | logged
    error = Column(Text, nullable=True)
    provider_message_id = Column(String, nullable=True)   # out: Resend id; in: Internet Message-ID
    match_method = Column(String, nullable=True)          # in: reply_address | sender | manual
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_evosys_outreach_msg_opp", "opportunity_id", "created_at"),
        Index("ix_evosys_outreach_msg_org_time", "brand_sales_org_id", "created_at"),
    )
