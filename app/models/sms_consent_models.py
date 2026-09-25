"""SMS consent of record, per PROGRAM, as auditable evidence.

WHY A TABLE AND NOT THE LEAD COLUMNS
------------------------------------
`Lead.sms_consent*` answers "did this lead ever tick a box somewhere". A
carrier audit of an A2P 10DLC campaign asks a narrower question: did THIS
number agree to THIS program's messages, on which page, under which wording,
when (by our clock, not the browser's), and has it since opted out? The lead
columns cannot say which program, cannot hold a disclosure version, and have
nowhere to put an opt-out. This table can. The lead columns are still written
(mirrored) so existing screens keep telling the truth; this table is the one
the send gate reads.

PHONE KNOWN IS NOT PERMISSION
-----------------------------
A row exists here ONLY when a person actively gave consent. A number found in
public records, by skip tracing, enrichment, an import or a purchased list has
no row here and is therefore never eligible for program SMS. There is no code
path that writes a row from a discovered number, and none may be added.

APPEND-ONLY IN SPIRIT
---------------------
A consent is never edited into something else. Opting out stamps the opt-out
columns on every active row for that (organization, program, number) and flips
status to `opted_out`; the original evidence stays exactly as captured. A
later fresh opt-in is a NEW row, so the history reads in order.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text

from app.models.models import Base, gen_uuid

STATUS_OPTED_IN = "opted_in"
STATUS_OPTED_OUT = "opted_out"
STATUSES = (STATUS_OPTED_IN, STATUS_OPTED_OUT)

METHOD_WEB_CHECKBOX = "web_form_checkbox"


class SmsConsentRecord(Base):
    __tablename__ = "sms_consent_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    # Stable program identifier, e.g. "evosys_wholesale". Consent is scoped to
    # (organization, program): agreeing to Wholesale property messages is not
    # agreeing to anything else the same organization might send.
    program = Column(String, nullable=False)

    phone_raw = Column(String, nullable=True)            # as typed, for the audit
    phone_normalized = Column(String, nullable=False)    # E.164, +1XXXXXXXXXX

    status = Column(String, nullable=False, default=STATUS_OPTED_IN)
    consent_given = Column(Boolean, nullable=False, default=True)
    # SERVER time, UTC. Never a timestamp the browser or the website supplied.
    consented_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    consent_method = Column(String, nullable=False, default=METHOD_WEB_CHECKBOX)

    source_url = Column(String, nullable=True)
    form_id = Column(String, nullable=True)
    form_version = Column(String, nullable=True)
    disclosure_version = Column(String, nullable=True)
    disclosure_text = Column(Text, nullable=True)        # verbatim, as shown

    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    seller_profile_id = Column(String, nullable=True)
    property_id = Column(String, nullable=True)
    deal_id = Column(String, nullable=True)

    # The Twilio registration this consent was collected under, when one is
    # configured at the time of capture. NULL is honest: not configured yet.
    campaign_sid = Column(String, nullable=True)
    messaging_service_sid = Column(String, nullable=True)

    # Opt-in confirmation SMS outcome: "sent" or a gate reason code.
    confirmation_status = Column(String, nullable=True)

    opted_out_at = Column(DateTime, nullable=True)
    opt_out_keyword = Column(String, nullable=True)
    opt_out_reason = Column(String, nullable=True)
    opt_out_source = Column(String, nullable=True)       # reply_stop | manual | suppression

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_smsconsent_org_program_phone", "organization_id", "program",
              "phone_normalized"),
        Index("ix_smsconsent_org_lead", "organization_id", "lead_id"),
    )
