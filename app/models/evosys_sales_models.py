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
    # Configurable groups (grouping.py has the defaults): [{"key","label","keywords":[..]}]
    industries = Column(JSON, nullable=True)
    # [{"key","label","state","cities":[..]}]  - US markets
    markets = Column(JSON, nullable=True)
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
    # The campaign this lead's follow-up belongs to (campaigns.py). Set once at
    # enrolment; existing enrolments are mapped to the default campaign with
    # their step and due time unchanged.
    campaign_id = Column(String, ForeignKey("evosys_campaigns.id", ondelete="SET NULL"), nullable=True)

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
    campaign_id = Column(String, ForeignKey("evosys_campaigns.id", ondelete="SET NULL"), nullable=True)
    # in: interested | pricing | question | not_interested | wrong_person | unsubscribe | unclear
    label = Column(String(30), nullable=True)
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_evosys_outreach_msg_opp", "opportunity_id", "created_at"),
        Index("ix_evosys_outreach_msg_org_time", "brand_sales_org_id", "created_at"),
    )


class EvoSysCampaign(Base):
    """One industry + market group's follow-up: its own audience, copy, offer,
    schedule and switch. Sender, From, Reply-To and mailing address stay on the
    team settings. A campaign sends only when sending_on AND the hash of what
    would be sent equals approved_hash (campaigns.content_hash): any change to
    the copy, offer, schedule or the team's send settings stops it until it is
    approved again."""
    __tablename__ = "evosys_campaigns"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    industry_key = Column(String(60), nullable=True)   # None = any industry
    market_key = Column(String(60), nullable=True)     # None = any market
    is_default = Column(Boolean, default=False, nullable=False)
    steps = Column(JSON, nullable=True)
    offer = Column(Text, nullable=True)
    booking_url = Column(String, nullable=True)
    daily_limit = Column(Integer, default=60, nullable=False)
    send_days = Column(String(20), default="0,1,2,3,4", nullable=False)   # Mon=0
    send_start_hour = Column(Integer, default=8, nullable=False)
    send_end_hour = Column(Integer, default=18, nullable=False)
    timezone = Column(String(60), default="America/Chicago", nullable=False)
    sending_on = Column(Boolean, default=False, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    approved_hash = Column(String(64), nullable=True)
    approved_version = Column(Integer, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    draft_origin = Column(String(20), nullable=True)   # manual | ai | ai_edited
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (Index("ix_evosys_campaign_brand", "brand_sales_org_id"),)


class EvoSysLead(Base):
    """EvoSys Sales facts about one opportunity: its industry + market group,
    where that came from, and whether a person needs to look at it."""
    __tablename__ = "evosys_leads"

    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    industry_key = Column(String(60), nullable=True)
    market_key = Column(String(60), nullable=True)
    city = Column(String(120), nullable=True)
    state = Column(String(20), nullable=True)
    group_source = Column(String(20), nullable=True)     # rule | ai_accepted | manual
    group_reason = Column(Text, nullable=True)
    source_channel = Column(String(40), nullable=True)   # facebook_ads | tiktok | google | ...
    # ok | unclear_market | possible_duplicate | held | excluded
    review_status = Column(String(30), default="ok", nullable=False)
    review_note = Column(Text, nullable=True)
    duplicate_of_id = Column(String, ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (Index("ix_evosys_leads_group", "brand_sales_org_id", "industry_key", "market_key"),
                      Index("ix_evosys_leads_review", "brand_sales_org_id", "review_status"))


class EvoSysAITask(Base):
    """Every AI request EvoSys Sales makes and what became of it: what it was
    asked, which facts it was given, what it suggested, whether a person
    accepted, edited or ignored it - or why it failed. A retry with the same
    inputs returns the saved result instead of asking again."""
    __tablename__ = "evosys_ai_tasks"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(30), nullable=False)          # group_lead | draft_campaign | reply_assist
    target_id = Column(String, nullable=False)         # opportunity / campaign / message id
    input_hash = Column(String(64), nullable=False)
    facts = Column(JSON, nullable=True)                # exactly what the AI was given
    output = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False)        # done | failed | needs_person
    error = Column(Text, nullable=True)
    outcome = Column(String(20), nullable=True)        # accepted | edited | dismissed
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_evosys_ai_target", "kind", "target_id", "input_hash"),)
