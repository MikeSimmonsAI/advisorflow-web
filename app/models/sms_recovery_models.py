"""SMS Delivery Recovery (#482) - channel-scoped SMS holds, their history,
and the carrier-error policy that decides what a failure does.

WHY THIS EXISTS
---------------
Until Nov 3 a single Twilio 30006 ("landline or unreachable carrier") ended a
family's text schedule for good (cadence status `stopped_bad_number`). That
read one provider receipt as proof the phone can never take a text, and it
left nobody a place to look at the evidence and decide. Of the 16 Central
Alabama families who got a 30006 between Oct 31 and Nov 3, none had a
landline on record (line types were "voip" or not looked up).

WHAT A HOLD IS - AND IS NOT
---------------------------
A hold stops automated texts to ONE family's phone until a person reviews it.
It is not DNC, not an opt-out, and not a line-type finding: those stay where
they live (Lead.status, SmsConsentRecord, the suppression list,
Lead.phone_line_type) and always win over anything here. Email is untouched.

One OPEN item per family (`open_lead_id` is unique and set only while open),
so two workers that see the same failure at once produce one hold. Every
change is an event row; `dedupe_key` makes a re-delivered Twilio callback or
a second worker a no-op instead of a second event.

POLICY IS VERSIONED CONFIG
--------------------------
`sms_recovery_policies` rows are never edited: a change is a new version, and
every hold records the version that opened it. No row = the built-in default
in app/services/sms_recovery_policy.py.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from app.models.models import Base, gen_uuid

# Channel states (#482 Phase B). sms_active and sms_opted_out are DERIVED
# (no open hold / the existing STOP-DNC-consent records); the three below are
# what an open item can hold.
SMS_ACTIVE = "sms_active"
SMS_TEMPORARY_HOLD = "sms_temporary_hold"
SMS_REVIEW_REQUIRED = "sms_review_required"
SMS_VERIFIED_UNREACHABLE = "sms_verified_unreachable"
SMS_OPTED_OUT = "sms_opted_out"
HELD_STATES = (SMS_TEMPORARY_HOLD, SMS_REVIEW_REQUIRED, SMS_VERIFIED_UNREACHABLE)

# Review progress on an open item.
REVIEW_OPEN = "open"
REVIEW_REQUESTED = "review_requested"
REVIEW_VERIFIED_CAPABLE = "verified_capable"
REVIEW_VERIFIED_NOT_CAPABLE = "verified_not_capable"

# How an item ended.
DISP_RELEASED = "released"                       # verified, released by a person
DISP_DELIVERED_AFTER_FAILURE = "delivered_after_failure"   # Twilio later reported delivered
DISP_TEMPORARY_EXPIRED = "temporary_hold_expired"
DISP_OPTED_OUT = "opted_out"                     # STOP/DNC arrived - the hold is moot


class SmsRecoveryItem(Base):
    __tablename__ = "sms_recovery_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, nullable=False, index=True)
    lead_id = Column(String, nullable=False, index=True)
    # Equal to lead_id while the item is open, NULL once closed. Unique, so
    # there is never more than one open hold per family, whoever opens it.
    open_lead_id = Column(String, nullable=True, unique=True)

    state = Column(String, nullable=False, default=SMS_REVIEW_REQUIRED)
    review_status = Column(String, nullable=False, default=REVIEW_OPEN)
    disposition = Column(String, nullable=True)

    # What opened it - the provider's own words, not an inference.
    error_code = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    provider_status = Column(String, nullable=True)
    trigger_message_id = Column(String, nullable=True, index=True)
    provider_sid = Column(String, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    policy_version = Column(String, nullable=True)
    policy_action = Column(String, nullable=True)
    opened_by = Column(String, nullable=True)        # callback / cadence / backfill
    hold_until = Column(DateTime, nullable=True)     # temporary holds only

    failures = Column(Integer, nullable=False, default=1)
    prevented = Column(Integer, nullable=False, default=0)   # scheduled texts held back

    reviewed_by_user_id = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    evidence = Column(Text, nullable=True)           # what the reviewer relied on
    email_fallback = Column(String, nullable=True)   # "routed" when a person confirmed it

    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_sms_recovery_org_state", "organization_id", "state"),
    )


class SmsRecoveryEvent(Base):
    __tablename__ = "sms_recovery_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, nullable=False, index=True)
    item_id = Column(String, nullable=True, index=True)
    lead_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)            # failure / opened / skip / review / release / ...
    message_id = Column(String, nullable=True)
    provider_sid = Column(String, nullable=True)
    error_code = Column(String, nullable=True)
    actor_user_id = Column(String, nullable=True)
    detail = Column(Text, nullable=True)             # JSON
    dedupe_key = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SmsRecoveryPolicy(Base):
    __tablename__ = "sms_recovery_policies"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, nullable=True, index=True)   # NULL = platform-wide
    version = Column(String, nullable=False)
    rules = Column(Text, nullable=False)             # JSON {code: {action, ...}}
    note = Column(Text, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_by_user_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
