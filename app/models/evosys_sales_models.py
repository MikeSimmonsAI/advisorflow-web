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

    evosys_campaigns          (PR #663, EvoSys Business Development) one row per
                              reach-out campaign: who it is for (an event, a
                              relationship, an industry or market), its own
                              messages, its own approval and its own switch.

A lead's follow-up row names ONE campaign, so a contact is never in two
sequences at once. Team-wide things (sender, mailing address, daily limit,
which markets may be reached) stay on evosys_outreach_settings.

Nothing here sends by itself: app/services/evosys_sales/outreach.run_pass does,
and only for a campaign that is approved (this exact version) AND switched on.
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
    # Which markets a live campaign may reach ("DFW", "Houston", ...). Empty
    # means not decided yet: campaigns can be planned, never switched on.
    eligible_markets = Column(JSON, nullable=True)
    sending_on = Column(Boolean, default=False, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)


class EvoSysOutreachState(Base):
    __tablename__ = "evosys_outreach_states"

    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, default="active", nullable=False)   # queued | active | stopped | finished
    # queued = in a campaign's audience, waiting for that campaign to be on.
    campaign_id = Column(String, ForeignKey("evosys_campaigns.id", ondelete="SET NULL"), nullable=True)
    joined_at = Column(DateTime, nullable=True)
    # Why this contact is in the audience but will not be emailed (no email,
    # unclear market, a fact the message needs is missing...). Shown to the
    # operator; cleared when the reason goes away.
    held_reason = Column(String, nullable=True)
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
    campaign_id = Column(String, nullable=True)
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


class EvoSysCampaign(Base):
    """One reach-out campaign. Starts Off. Its messages are approved as an
    exact version (`approved_hash`); any material change clears the approval
    and turns it off until it is approved and switched on again."""
    __tablename__ = "evosys_campaigns"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    # event | relationship | project | offer | industry | market | list
    kind = Column(String, default="list", nullable=False)
    # The event, referrer, project or offer the campaign is about, in Mike's words.
    context = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    market = Column(String, nullable=True)
    # warm = people Mike knows or was introduced to; researched = no relationship.
    tone = Column(String, default="researched", nullable=False)
    steps = Column(JSON, nullable=True)
    offer = Column(Text, nullable=True)
    booking_url = Column(String, nullable=True)
    sending_on = Column(Boolean, default=False, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_hash = Column(String(64), nullable=True)
    approved_version = Column(Integer, nullable=True)
    switched_at = Column(DateTime, nullable=True)
    switched_by = Column(String, ForeignKey("users.id"), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_evosys_campaigns_org", "brand_sales_org_id", "archived_at"),
    )
