"""Telephony: phone-number identity, inbound call log, voicemail, voicemail drops.

Stream XC, express overhaul 2026-09-28. NEW TABLES ONLY - `Base.metadata.create_all`
creates them; nothing here alters an existing table. (New columns on
`voice_calls` are declared on the VoiceCall model and listed in the snippet file.)

  phone_numbers          One provider number and what it may do. Assignment is a
                         SCOPE, most specific wins when resolving:
                             PLATFORM   organization_id NULL, platform_id NULL
                             BRAND      organization_id NULL, platform_id set
                             ORG        organization_id set,  workspace_id NULL
                             WORKSPACE  organization_id set,  workspace_id set
                         The legacy columns (organizations.org_twilio_phone_number,
                         users.twilio_phone_number) keep working as IMPLICIT
                         org / user records - see app/services/number_resolution.py.
                         No row here is ever created by a provider purchase call.

  telephony_user_settings  A person's own callback/forwarding phone. The human
                         dialer rings THIS phone first and bridges the lead; the
                         inbound route rings it before voicemail. Never the
                         directory `users.phone`, which is profile display only.
                         Usable ONLY once verified (a code delivered to that
                         phone and typed back): an unverified number is never
                         rung, so the org's caller ID cannot be pointed at a
                         stranger's phone.

  inbound_call_logs      Every inbound call that reached a tenant number, known
                         caller or not. `voice_calls.lead_id` is NOT NULL, so an
                         unknown caller cannot be a VoiceCall row; this is where
                         "unknown caller, logged inside the org" lives.

  voicemails             A recorded inbound message. `recording_url` is the
                         provider URL and is NEVER returned to a browser: playback
                         goes through GET /voicemails/{id}/audio, which checks the
                         caller's organization first.

  org_voicemail_drops    The message an outbound call may leave on an answering
                         machine. Only a row with status == "approved" is ever
                         played; nothing is dropped without one.

TENANT RULE: every tenant row carries organization_id and every query filters on
the acting workspace org first; another org's row is a 404.
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer, String,
                        Text)

from app.models.models import Base, gen_uuid

SCOPE_PLATFORM = "platform"
SCOPE_BRAND = "brand"
SCOPE_ORG = "organization"
SCOPE_WORKSPACE = "workspace"

VOICEMAIL_STATUSES = ("new", "reviewed")
DROP_STATUSES = ("draft", "approved", "revoked")


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"

    id = Column(String, primary_key=True, default=gen_uuid)
    e164 = Column(String, nullable=False, unique=True)            # +1XXXXXXXXXX
    provider = Column(String, nullable=False, default="twilio")
    provider_sid = Column(String, nullable=True)                  # PN... (informational)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"), nullable=True)
    workspace_id = Column(String, nullable=True)                  # locations.id, when location-scoped
    cap_sms = Column(Boolean, nullable=False, default=False)
    cap_voice_outbound = Column(Boolean, nullable=False, default=False)
    cap_voice_inbound = Column(Boolean, nullable=False, default=False)
    cap_voicemail = Column(Boolean, nullable=False, default=False)
    # JSON: {"mode": "ring_then_voicemail"|"voicemail_only"|"ai_agent",
    #        "ring_user_ids": [...], "timeout_seconds": 20, "voicemail": true,
    #        "greeting_text": "...", "greeting_recording_url": "https://..."}
    default_inbound_route = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    label = Column(String, nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_phone_numbers_org_ws", "organization_id", "workspace_id"),
    )


class TelephonyUserSetting(Base):
    __tablename__ = "telephony_user_settings"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, unique=True)
    callback_e164 = Column(String, nullable=True)                 # VERIFIED phone the bridge rings first
    verified_at = Column(DateTime, nullable=True)                 # when callback_e164 was proven
    ring_on_inbound = Column(Boolean, nullable=False, default=True)
    # Verification in flight. The code is never stored, only its salted hash;
    # it expires, attempts are capped, and resends are throttled.
    pending_e164 = Column(String, nullable=True)
    code_hash = Column(String, nullable=True)
    code_expires_at = Column(DateTime, nullable=True)
    code_attempts = Column(Integer, nullable=False, default=0)
    code_sent_at = Column(DateTime, nullable=True)
    pending_org_id = Column(String, nullable=True)                # workspace the number was checked in
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InboundCallLog(Base):
    __tablename__ = "inbound_call_logs"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    phone_number_id = Column(String, nullable=True)               # phone_numbers.id; NULL = legacy column
    to_e164 = Column(String, nullable=False)
    from_e164 = Column(String, nullable=True)                     # NULL when the caller id is withheld/unusable
    call_sid = Column(String, nullable=True, index=True)
    lead_id = Column(String, nullable=True)
    org_contact_id = Column(String, nullable=True)
    voice_call_id = Column(String, nullable=True)
    # known | unknown | dnc | suppressed
    caller_state = Column(String, nullable=False, default="unknown")
    # ringing | answered | voicemail | no_voicemail | ai_agent | completed
    status = Column(String, nullable=False, default="ringing")
    answered_by_user_id = Column(String, nullable=True)
    dial_status = Column(String, nullable=True)                   # Twilio DialCallStatus
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_inbound_calls_org_created", "organization_id", "created_at"),
    )


class Voicemail(Base):
    __tablename__ = "voicemails"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    phone_number_id = Column(String, nullable=True)
    inbound_call_id = Column(String, nullable=True)
    to_e164 = Column(String, nullable=True)
    from_e164 = Column(String, nullable=True)
    lead_id = Column(String, nullable=True, index=True)
    org_contact_id = Column(String, nullable=True)
    caller_state = Column(String, nullable=True)                  # known | unknown | dnc | suppressed
    call_sid = Column(String, nullable=True)
    recording_sid = Column(String, nullable=True, unique=True)
    recording_url = Column(String, nullable=True)                 # provider URL - never sent to a browser
    duration_seconds = Column(Integer, nullable=True)
    transcript = Column(Text, nullable=True)
    transcript_status = Column(String, nullable=True)             # not_enabled | pending | completed | failed
    status = Column(String, nullable=False, default="new")        # new | reviewed
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by_id = Column(String, nullable=True)
    task_id = Column(String, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_voicemails_org_received", "organization_id", "received_at"),
    )


class OrgVoicemailDrop(Base):
    __tablename__ = "org_voicemail_drops"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    phone_number_id = Column(String, nullable=True)
    message_text = Column(Text, nullable=True)
    recording_url = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft")      # draft | approved | revoked
    approved_by_id = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
