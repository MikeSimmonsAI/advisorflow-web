"""Telephony workflows: inbound tenant routing, inbound voicemail, outbound
answering-machine handling and the human click-to-call bridge.

Routers call these after the webhook signature has been verified
(telephony_webhook_guard) or the user authenticated. Everything here is scoped
to ONE organization, resolved from data we hold:

    inbound   -> the CALLED number (number_resolution.resolve_owner_by_called_number)
    outbound  -> the lead's own organization

and never from a value that made a round trip through the provider.

WHAT NOTHING HERE DOES
  * No call is placed without voice_bulk_gate.call_refusal returning None.
  * No voicemail is dropped without an APPROVED OrgVoicemailDrop row, and never
    to a DNC / suppressed / paused lead.
  * No lead is created for an unknown caller. The call is logged inside the
    called organization as "unknown" and a review task is created.
  * No transcription is requested: the current stack does not use Twilio
    transcription, so `transcript` stays null with transcript_status
    "not_enabled" rather than a made-up text.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

from app.models.models import Lead, Organization, User, VoiceCall
from app.models.telephony_models import (InboundCallLog, OrgVoicemailDrop, PhoneNumber,
                                         TelephonyUserSetting, Voicemail)
from app.services import number_resolution as NR
from app.services import telephony_twilio as TT

log = logging.getLogger(__name__)

TRANSCRIPT_NOT_ENABLED = "not_enabled"
TRANSCRIPT_REASON = ("Transcription is not enabled: the current voice stack does not use a "
                     "transcription provider for voicemail.")


def backend_base() -> str:
    """Absolute origin Twilio must call back. '' when it cannot be determined."""
    try:
        from app.services.twilio_callbacks import public_api_base
        base = public_api_base()
    except Exception:                                        # noqa: BLE001
        base = ""
    return (base or os.environ.get("BACKEND_URL") or "").rstrip("/")


def org_display_name(org: Optional[Organization]) -> str:
    if org is None:
        return "our office"
    return ((getattr(org, "brand_name", None) or org.name or "our office").strip())


def lead_label(lead: Optional[Lead]) -> str:
    if lead is None:
        return ""
    return ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip()


# ── caller lookup, ONLY inside one organization ─────────────────────────────

def find_caller(db, org_id: str, from_raw) -> Tuple[Optional[Lead], Optional[object], List[str]]:
    """(lead, org_contact, phone_forms) for this caller inside `org_id` only."""
    forms = NR.phone_forms(from_raw)
    if not forms:
        return None, None, []
    lead = (db.query(Lead)
            .filter(Lead.organization_id == org_id, Lead.phone.in_(forms))
            .order_by(Lead.updated_at.desc()).first())
    contact = None
    if lead is None:
        try:
            from app.models.intake_models import OrgContact
            contact = (db.query(OrgContact)
                       .filter(OrgContact.organization_id == org_id,
                               (OrgContact.phone.in_(forms)) | (OrgContact.mobile_phone.in_(forms)))
                       .first())
        except Exception:                                    # noqa: BLE001
            log.exception("telephony: contact lookup failed for org %s", org_id)
            contact = None
    return lead, contact, forms


def caller_state(db, org_id: str, lead: Optional[Lead], contact, from_raw) -> str:
    """known | unknown | dnc | suppressed - within this organization only."""
    from app.services import voice_bulk_gate
    from app.services.compliance_service import is_phone_suppressed
    if lead is not None and (getattr(lead.status, "value", lead.status) or "").lower() == "dnc":
        return "dnc"
    if from_raw and voice_bulk_gate._dnc_by_number(db, org_id, from_raw):
        return "dnc"
    try:
        if from_raw and is_phone_suppressed(db, org_id, from_raw):
            return "suppressed"
    except Exception:                                        # noqa: BLE001
        log.exception("telephony: suppression lookup failed")
        return "suppressed"                                  # fail closed: no follow-up
    return "known" if (lead is not None or contact is not None) else "unknown"


def _org_member_ids(db, org_id: str) -> set:
    ids = {u for (u,) in db.query(User.id).filter(User.organization_id == org_id,
                                                   User.is_active.is_(True)).all()}
    try:
        from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
        ids |= {u for (u,) in db.query(Membership.user_id).filter(
            Membership.scope_type == SCOPE_CUSTOMER_ORG, Membership.scope_id == org_id,
            Membership.is_active.is_(True)).all()}
    except Exception:                                        # noqa: BLE001
        pass
    return ids


def is_org_member(db, org_id: str, user_id: Optional[str]) -> bool:
    return bool(user_id) and user_id in _org_member_ids(db, org_id)


def user_callback_phone(db, user_id: Optional[str]) -> Optional[str]:
    """The user's VERIFIED callback phone, or None. An unverified number is
    never returned, so it is never rung."""
    if not user_id:
        return None
    row = db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == user_id).first()
    if row is None or not row.callback_e164 or not row.verified_at:
        return None
    return NR.normalize_e164(row.callback_e164)


# ── callback phone: verification and abuse limits ───────────────────────────

CODE_TTL_MINUTES = 10
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_SECONDS = 60
HUMAN_CALLS_PER_MINUTE = 5
HUMAN_CALLS_PER_DAY = 100


class RateLimited(Exception):
    pass


def callback_phone_problem(db, org_id: Optional[str], e164: Optional[str]) -> Optional[str]:
    """Why this number may NOT be a staff callback phone in this org, or None.

    The bridge rings this number from the organization's caller ID, so it must
    not be a customer's number (lead or contact in this org), and must not be
    on this org's Do Not Contact or suppression lists."""
    from app.services import voice_bulk_gate
    from app.services.compliance_service import is_phone_suppressed
    if not e164:
        return "Enter a valid US phone number (10 digits)."
    if not org_id:
        return "An active workspace is required."
    if voice_bulk_gate._dnc_by_number(db, org_id, e164):
        return "That number is on this organization's Do Not Contact list."
    try:
        if is_phone_suppressed(db, org_id, e164):
            return "That number is on this organization's suppression list."
    except Exception:                                        # noqa: BLE001
        return "The suppression list could not be checked."
    forms = NR.phone_forms(e164)
    if db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.phone.in_(forms)).first():
        return "That number belongs to a lead in this organization - use your own phone."
    try:
        from app.models.intake_models import OrgContact
        if db.query(OrgContact.id).filter(
                OrgContact.organization_id == org_id,
                (OrgContact.phone.in_(forms)) | (OrgContact.mobile_phone.in_(forms))).first():
            return "That number belongs to a contact in this organization - use your own phone."
    except Exception:                                        # noqa: BLE001
        return "The contact list could not be checked."
    return None


def _hash_code(user_id: str, code: str) -> str:
    import hashlib
    import hmac
    key = (os.environ.get("JWT_SECRET") or "telephony-verify").encode()
    return hmac.new(key, ("%s:%s" % (user_id, code)).encode(), hashlib.sha256).hexdigest()


def deliver_verification_code(db, org_id: str, e164: str, code: str) -> None:
    """Deliver the code by a short voice call from the organization's number
    (the existing voice path). Raises PermissionError when it cannot be sent."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    resolved = NR.resolve_voice_number(db, org, purpose=NR.PURPOSE_OUTBOUND)
    if not resolved.ok:
        raise PermissionError("Provider/config required: " + (resolved.reason or "no voice number."))
    creds = NR.twilio_credentials(db, org, resolved)
    if not creds:
        raise PermissionError("Provider/config required: no Twilio account is stored for this "
                              "organization, so a verification code cannot be delivered.")
    TT.create_call(creds, to=e164, from_=resolved.e164,
                   twiml=TT.twiml_say_code(code, org_display_name(org)), timeout=30)


def start_callback_verification(db, user: User, org_id: str, raw_phone) -> TelephonyUserSetting:
    import secrets
    e164 = NR.normalize_e164(raw_phone)
    why = callback_phone_problem(db, org_id, e164)
    if why:
        raise ValueError(why)
    row = db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == user.id).first()
    if row is None:
        row = TelephonyUserSetting(user_id=user.id, code_attempts=0)
        db.add(row)
        db.flush()
    now = datetime.utcnow()
    if row.code_sent_at and (now - row.code_sent_at).total_seconds() < CODE_RESEND_SECONDS:
        raise RateLimited("A code was just sent. Wait a minute before asking for another.")
    code = "%06d" % secrets.randbelow(1000000)
    deliver_verification_code(db, org_id, e164, code)        # raises before anything is stored
    from datetime import timedelta
    row.pending_e164 = e164
    row.pending_org_id = org_id
    row.code_hash = _hash_code(user.id, code)
    row.code_expires_at = now + timedelta(minutes=CODE_TTL_MINUTES)
    row.code_attempts = 0
    row.code_sent_at = now
    db.commit()
    return row


def confirm_callback_verification(db, user: User, code: str) -> TelephonyUserSetting:
    import hmac
    row = db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == user.id).first()
    if row is None or not row.pending_e164 or not row.code_hash:
        raise ValueError("There is no verification in progress. Save the number first.")
    now = datetime.utcnow()
    if not row.code_expires_at or now > row.code_expires_at:
        row.code_hash = None
        db.commit()
        raise ValueError("That code has expired. Ask for a new one.")
    if (row.code_attempts or 0) >= CODE_MAX_ATTEMPTS:
        row.code_hash = None
        db.commit()
        raise ValueError("Too many wrong codes. Ask for a new one.")
    row.code_attempts = (row.code_attempts or 0) + 1
    if not hmac.compare_digest(row.code_hash, _hash_code(user.id, (code or "").strip())):
        db.commit()
        raise ValueError("That code is not correct.")
    # Re-check at the moment it becomes usable.
    why = callback_phone_problem(db, row.pending_org_id, row.pending_e164)
    if why:
        db.commit()
        raise ValueError(why)
    row.callback_e164 = row.pending_e164
    row.verified_at = now
    row.pending_e164 = row.code_hash = row.code_expires_at = row.pending_org_id = None
    row.code_attempts = 0
    db.commit()
    return row


def enforce_human_call_rate(db, user_id: str) -> None:
    from datetime import timedelta
    now = datetime.utcnow()
    base = db.query(VoiceCall).filter(VoiceCall.advisor_id == user_id,
                                      VoiceCall.is_human_call.is_(True))
    if base.filter(VoiceCall.created_at >= now - timedelta(minutes=1)).count() >= HUMAN_CALLS_PER_MINUTE:
        raise RateLimited("Too many calls in the last minute. Wait a moment and try again.")
    if base.filter(VoiceCall.created_at >= now - timedelta(days=1)).count() >= HUMAN_CALLS_PER_DAY:
        raise RateLimited("Daily call limit reached for your account.")


def ring_numbers_for(db, org_id: str, route: dict) -> List[str]:
    members = _org_member_ids(db, org_id)
    out = []
    for uid in route.get("ring_user_ids") or []:
        if uid not in members:
            continue
        row = db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == uid).first()
        if row is None or not row.ring_on_inbound or not row.verified_at:
            continue                                         # unverified phones are never rung
        num = NR.normalize_e164(row.callback_e164)
        if num and num not in out:
            out.append(num)
    return out


def _route_for(owner) -> Tuple[dict, bool, bool]:
    """(route, inbound_capable, voicemail_capable) for the called number.
    Legacy org/user columns are implicit inbound + voicemail numbers."""
    rec = getattr(owner, "record", None)
    if rec is None:
        return NR.parse_route(None), True, True
    return (NR.parse_route(rec.default_inbound_route), bool(rec.cap_voice_inbound),
            bool(rec.cap_voicemail))


def greeting_for(db, org_id: str, route: dict) -> Tuple[Optional[str], Optional[str]]:
    if route.get("greeting_recording_url"):
        return None, route["greeting_recording_url"]
    if route.get("greeting_text"):
        return route["greeting_text"], None
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return ("You have reached %s. No one is available to take your call. Please leave "
            "your name, number and a short message after the tone." % org_display_name(org)), None


def _advisor_for_lead(db, lead: Lead, route: dict) -> Optional[User]:
    if lead.assigned_to_id:
        u = db.query(User).filter(User.id == lead.assigned_to_id).first()
        if u is not None:
            return u
    for uid in route.get("ring_user_ids") or []:
        u = db.query(User).filter(User.id == uid, User.organization_id == lead.organization_id).first()
        if u is not None:
            return u
    return (db.query(User).filter(User.organization_id == lead.organization_id,
                                  User.role.in_(["advisor", "org_admin", "super_admin"]))
            .order_by(User.created_at.asc()).first())


# ── inbound ─────────────────────────────────────────────────────────────────

def voicemail_twiml(db, log_row: InboundCallLog, route: dict, voicemail_capable: bool) -> str:
    if not (route.get("voicemail") and voicemail_capable):
        log_row.status = "no_voicemail"
        return TT.twiml_hangup("Sorry, no one is available to take your call. Please try again later.")
    text, rec_url = greeting_for(db, log_row.organization_id, route)
    log_row.status = "voicemail"
    return TT.twiml_record_voicemail(
        greeting_text=text, greeting_recording_url=rec_url,
        recording_callback_url="%s/voice/inbound/voicemail-recording?log_id=%s" % (
            backend_base(), log_row.id),
        finish_url="/voice/inbound/voicemail-done?log_id=%s" % log_row.id)


def handle_inbound(db, verified) -> str:
    """The whole inbound decision. Returns TwiML."""
    from app.services.telephony_webhook_guard import assert_org_matches
    to_raw = verified.get("To")
    from_raw = verified.get("From")
    call_sid = verified.get("CallSid")
    owner = NR.resolve_owner_by_called_number(db, to_raw)
    if owner is None:
        # Nobody owns this number: no lookup of any caller anywhere.
        log.warning("telephony: inbound call to unowned number %s", to_raw)
        return TT.twiml_generic_unknown_number()
    assert_org_matches(verified, owner.organization_id)
    org_id = owner.organization_id
    route, inbound_ok, vm_ok = _route_for(owner)

    row = None
    if call_sid:
        row = (db.query(InboundCallLog)
               .filter(InboundCallLog.organization_id == org_id,
                       InboundCallLog.call_sid == call_sid).first())
    lead, contact, _forms = find_caller(db, org_id, from_raw)
    state = caller_state(db, org_id, lead, contact, from_raw)
    if row is None:
        row = InboundCallLog(organization_id=org_id, phone_number_id=owner.number_id,
                             to_e164=owner.e164, from_e164=NR.normalize_e164(from_raw),
                             call_sid=call_sid or None, lead_id=getattr(lead, "id", None),
                             org_contact_id=getattr(contact, "id", None), caller_state=state,
                             status="ringing")
        db.add(row)
        db.flush()
        if lead is not None:
            adv = _advisor_for_lead(db, lead, route)
            if adv is not None:
                vc = VoiceCall(lead_id=lead.id, advisor_id=adv.id, organization_id=org_id,
                               call_sid=call_sid or None, to_phone=owner.e164,
                               from_phone=row.from_e164 or (from_raw or "")[:40],
                               call_number=1, status="ringing", provider="twilio",
                               direction="inbound", phone_number_id=owner.number_id,
                               created_at=datetime.utcnow())
                db.add(vc)
                db.flush()
                row.voice_call_id = vc.id

    if not inbound_ok:
        row.status = "rejected"
        db.commit()
        return TT.twiml_hangup("Thank you for calling. This number does not accept calls. Goodbye.")

    if (route["mode"] == "ai_agent" and lead is not None and state == "known"
            and row.voice_call_id):
        twiml = _ai_agent_twiml(db, row, lead)
        if twiml:
            row.status = "ai_agent"
            db.commit()
            return twiml

    ring = ring_numbers_for(db, org_id, route) if route["mode"] == "ring_then_voicemail" else []
    if ring:
        row.status = "ringing"
        db.commit()
        return TT.twiml_inbound(ring_numbers=ring, timeout=route["timeout_seconds"],
                                dial_action_url="/voice/inbound/dial-status?log_id=%s" % row.id,
                                caller_id=row.from_e164 or None)
    twiml = voicemail_twiml(db, row, route, vm_ok)
    db.commit()
    return twiml


def _ai_agent_twiml(db, row: InboundCallLog, lead: Lead) -> Optional[str]:
    """The pre-existing inbound AI stream, reachable only when a number's route
    explicitly says mode == ai_agent and the caller is a known, callable lead."""
    base = backend_base()
    vc = db.query(VoiceCall).filter(VoiceCall.id == row.voice_call_id).first()
    if not base or vc is None:
        return None
    host = base.replace("https://", "").replace("http://", "")
    from twilio.twiml.voice_response import Connect, VoiceResponse
    r = VoiceResponse()
    c = Connect()
    s = c.stream(url="wss://%s/voice/stream?call_id=%s&lead_id=%s&advisor_id=%s&direction=inbound"
                 % (host, vc.id, lead.id, vc.advisor_id))
    s.parameter(name="direction", value="inbound")
    r.append(c)
    return str(r)


def inbound_row(db, log_id: str) -> Optional[InboundCallLog]:
    return db.query(InboundCallLog).filter(InboundCallLog.id == log_id).first() if log_id else None


def _owner_route(db, row: InboundCallLog) -> Tuple[dict, bool]:
    rec = (db.query(PhoneNumber).filter(PhoneNumber.id == row.phone_number_id).first()
           if row.phone_number_id else None)
    if rec is None:
        return NR.parse_route(None), True
    return NR.parse_route(rec.default_inbound_route), bool(rec.cap_voicemail)


def handle_dial_status(db, row: InboundCallLog, dial_status: str) -> str:
    row.dial_status = (dial_status or "")[:40] or None
    if dial_status in ("completed", "answered"):
        row.status = "answered"
        db.commit()
        return TT.twiml_hangup()
    route, vm_ok = _owner_route(db, row)
    twiml = voicemail_twiml(db, row, route, vm_ok)
    db.commit()
    return twiml


def store_voicemail(db, row: InboundCallLog, *, recording_sid: str, recording_url: str,
                    duration, call_sid: Optional[str]) -> Voicemail:
    """Idempotent on RecordingSid. Creates the review task once. A RecordingSid
    that is not a Twilio RecordingSid is ignored (nothing could be played)."""
    if not TT.valid_recording_sid(recording_sid):
        log.warning("telephony: ignoring voicemail callback with invalid RecordingSid")
        return None
    existing = (db.query(Voicemail).filter(Voicemail.recording_sid == recording_sid).first()
                if recording_sid else None)
    if existing is not None:
        return existing
    try:
        dur = int(duration) if duration not in (None, "") else None
    except (TypeError, ValueError):
        dur = None
    vm = Voicemail(organization_id=row.organization_id, phone_number_id=row.phone_number_id,
                   inbound_call_id=row.id, to_e164=row.to_e164, from_e164=row.from_e164,
                   lead_id=row.lead_id, org_contact_id=row.org_contact_id,
                   caller_state=row.caller_state, call_sid=call_sid or row.call_sid,
                   recording_sid=recording_sid or None, recording_url=recording_url or None,
                   duration_seconds=dur, transcript=None,
                   transcript_status=TRANSCRIPT_NOT_ENABLED, status="new",
                   received_at=datetime.utcnow())
    db.add(vm)
    db.flush()
    row.status = "voicemail"

    lead = (db.query(Lead).filter(Lead.id == row.lead_id,
                                  Lead.organization_id == row.organization_id).first()
            if row.lead_id else None)
    who = lead_label(lead) or row.from_e164 or "an unknown caller"
    no_callback = row.caller_state in ("dnc", "suppressed")
    title = ("Review voicemail from %s (Do Not Contact - no call back)" % who if no_callback
             else "Voicemail from %s" % who)
    details = "Inbound voicemail%s received on %s." % (
        (" (%ds)" % dur) if dur else "", row.to_e164)
    if row.caller_state == "unknown":
        details += " The caller matched no lead or contact in this organization."
    route, _ = _owner_route(db, row)
    assignee = getattr(lead, "assigned_to_id", None)
    if not assignee:
        members = _org_member_ids(db, row.organization_id)
        assignee = next((u for u in route.get("ring_user_ids") or [] if u in members), None)
    from app.models.work_models import LeadTask
    task = LeadTask(organization_id=row.organization_id, lead_id=getattr(lead, "id", None),
                    title=title[:300], details=details, status="open",
                    assigned_to_id=assignee, source="voicemail")
    db.add(task)
    db.flush()
    vm.task_id = task.id

    if row.voice_call_id:
        vc = db.query(VoiceCall).filter(VoiceCall.id == row.voice_call_id).first()
        if vc is not None:
            vc.status = "completed"
            vc.outcome = "voicemail_received"
            vc.recording_sid = recording_sid or vc.recording_sid
            vc.duration_seconds = dur if dur is not None else vc.duration_seconds
            vc.ended_at = datetime.utcnow()
    db.commit()
    return vm


# ── outbound: approved voicemail drop and AMD ───────────────────────────────

def approved_drop(db, org_id: str) -> Optional[OrgVoicemailDrop]:
    row = (db.query(OrgVoicemailDrop)
           .filter(OrgVoicemailDrop.organization_id == org_id,
                   OrgVoicemailDrop.status == "approved")
           .order_by(OrgVoicemailDrop.approved_at.desc()).first())
    if row is None or not row.approved_at or not (row.message_text or row.recording_url):
        return None
    return row


def _answered_by(amd: str) -> str:
    amd = (amd or "").lower()
    if amd == "human":
        return "human"
    if amd.startswith("machine"):
        return "voicemail"
    if amd == "fax":
        return "failed"
    return "unknown"


def record_amd(call: VoiceCall, amd: str) -> None:
    call.amd_status = (amd or "")[:40] or None
    call.answered_by = _answered_by(amd)
    call.is_live_conversation = call.answered_by == "human"


def machine_twiml(db, call: VoiceCall, amd: str) -> str:
    """What an AUTOMATED call does on reaching a machine: the approved drop when
    every gate still allows it, otherwise hang up without a word."""
    from app.services import voice_bulk_gate
    record_amd(call, amd)
    if (amd or "").lower() not in TT.AMD_MACHINE_END:
        # Greeting still playing (machine_start) or fax: never speak into it.
        call.outcome = call.outcome or "machine_no_drop"
        db.commit()
        return TT.twiml_hangup()
    lead = db.query(Lead).filter(Lead.id == call.lead_id,
                                 Lead.organization_id == call.organization_id).first()
    drop = approved_drop(db, call.organization_id)
    refusal = voice_bulk_gate.call_refusal(db, lead, call.organization_id) if lead else "Lead not found."
    if drop is None or refusal or call.is_human_call:
        call.outcome = "machine_no_drop"
        call.error_message = (refusal or ("No approved voicemail message." if drop is None else None))
        db.commit()
        return TT.twiml_hangup()
    call.voicemail_left = True
    call.voicemail_drop_id = drop.id
    call.outcome = "voicemail_dropped"
    db.commit()
    return TT.twiml_voicemail_drop(text=drop.message_text, recording_url=drop.recording_url)


def handle_async_amd(db, call: VoiceCall, amd: str, call_sid: str) -> str:
    """Async AMD callback. A human is left on the AI stream; a machine that has
    finished its greeting gets the approved drop (or a hang-up) by redirecting
    the live call through the provider. Only OUR stored CallSid is ever
    redirected; a callback naming a different CallSid is refused outright."""
    if not call.call_sid or (call_sid or "") != call.call_sid:
        log.warning("telephony: AMD callback CallSid mismatch for call %s", call.id)
        return "sid_mismatch"
    if _answered_by(amd) == "human" or (amd or "").lower() == "unknown":
        record_amd(call, amd)
        db.commit()
        return "human"
    if (amd or "").lower() == "machine_start":
        record_amd(call, amd)
        db.commit()
        return "waiting"
    twiml = machine_twiml(db, call, amd)
    org = db.query(Organization).filter(Organization.id == call.organization_id).first()
    rec = (db.query(PhoneNumber).filter(PhoneNumber.id == call.phone_number_id).first()
           if call.phone_number_id else None)
    level = "organization" if rec is None or rec.organization_id else (
        "brand" if rec.platform_id else "platform")
    creds = NR.twilio_credentials(db, org, NR.ResolvedNumber(ok=True, level=level))
    if not creds or not call.call_sid:
        log.error("telephony: cannot redirect call %s after AMD - no credentials/sid", call.id)
        return "no_credentials"
    try:
        TT.update_call(creds, call.call_sid, twiml)
    except Exception as exc:                                 # noqa: BLE001
        log.error("telephony: AMD redirect failed for call %s: %s", call.id, exc)
        call.error_message = ("AMD redirect failed: %s" % exc)[:480]
        db.commit()
        return "redirect_failed"
    return "dropped" if call.voicemail_left else "hung_up"


# ── human dialer ────────────────────────────────────────────────────────────

def human_call_readiness(db, lead: Lead, user: User) -> dict:
    """Every precondition of the human bridge, each with what to do about it."""
    from app.services import voice_bulk_gate
    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    checks = []
    refusal = voice_bulk_gate.call_refusal(db, lead, lead.organization_id, human=True)
    checks.append({"key": "compliance", "ok": refusal is None,
                   "label": "Compliance (DNC, suppression, call permission)",
                   "detail": refusal})
    resolved = NR.resolve_voice_number(db, org, purpose=NR.PURPOSE_OUTBOUND)
    checks.append({"key": "org_number", "ok": resolved.ok,
                   "label": "Organization voice number",
                   "detail": (None if resolved.ok else resolved.reason),
                   "fix": (None if resolved.ok else
                           "A platform administrator assigns a voice-capable number to this "
                           "organization (Organization Control Center > Operations).")})
    cb = user_callback_phone(db, user.id)
    cb_problem = callback_phone_problem(db, lead.organization_id, cb) if cb else None
    setting = db.query(TelephonyUserSetting).filter(TelephonyUserSetting.user_id == user.id).first()
    pending = setting.pending_e164 if setting is not None and setting.code_hash else None
    if cb and not cb_problem:
        cb_detail, cb_fix = None, None
    elif cb_problem:
        cb_detail, cb_fix = cb_problem, "Save and verify a different phone of your own."
    elif pending:
        cb_detail, cb_fix = "Verify your callback phone.", "Enter the code we called %s with." % pending
    else:
        cb_detail, cb_fix = ("Verify your callback phone.",
                             "Save the phone we should ring first (your cell or desk); we call it with a code.")
    checks.append({"key": "callback_phone", "ok": bool(cb) and not cb_problem,
                   "label": "Your callback phone", "detail": cb_detail, "fix": cb_fix})
    creds = NR.twilio_credentials(db, org, resolved) if resolved.ok else None
    checks.append({"key": "provider", "ok": bool(creds),
                   "label": "Voice provider account",
                   "detail": None if creds else "No Twilio account credentials are stored for this number.",
                   "fix": None if creds else "Store the organization's Twilio account before calls can be "
                                             "placed."})
    base_ok = bool(backend_base())
    checks.append({"key": "callback_url", "ok": base_ok, "label": "Public callback URL",
                   "detail": None if base_ok else "API_BASE_URL / BACKEND_URL is not configured.",
                   "fix": None if base_ok else "Set API_BASE_URL on the backend service."})
    config_missing = [c["key"] for c in checks
                      if not c["ok"] and c["key"] in ("org_number", "provider", "callback_url")]
    return {"ready": all(c["ok"] for c in checks), "checks": checks,
            "provider_config_required": config_missing,
            "from_number": resolved.e164 if resolved.ok else None,
            "from_level": resolved.level if resolved.ok else None,
            "callback_phone": cb, "callback_pending": pending, "mode": "twilio_bridge",
            "browser_calling": False,
            "note": ("Click-to-call bridge: Twilio rings your phone first, then connects the "
                     "customer, who sees the organization's number. Browser (WebRTC) calling "
                     "is not available in this stack.")}


def start_human_call(db, lead: Lead, user: User) -> VoiceCall:
    """Place leg 1 of the bridge. Raises PermissionError(reason) on any refusal
    (no row written) and RuntimeError on a provider failure (row marked failed)."""
    enforce_human_call_rate(db, user.id)
    ready = human_call_readiness(db, lead, user)
    if not ready["ready"]:
        first = next(c for c in ready["checks"] if not c["ok"])
        raise PermissionError("%s: %s" % (first["label"], first.get("detail") or "not ready"))
    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    resolved = NR.resolve_voice_number(db, org, purpose=NR.PURPOSE_OUTBOUND)
    creds = NR.twilio_credentials(db, org, resolved)
    lead_e164 = NR.normalize_e164(lead.phone)
    if not lead_e164:
        raise PermissionError("The lead's phone number is not a usable US number.")
    prior = db.query(VoiceCall).filter(VoiceCall.lead_id == lead.id).count()
    call = VoiceCall(lead_id=lead.id, advisor_id=user.id, organization_id=lead.organization_id,
                     to_phone=lead_e164, from_phone=resolved.e164, call_number=prior + 1,
                     status="initiating", provider="twilio", direction="outbound",
                     is_human_call=True, phone_number_id=resolved.number_id,
                     created_at=datetime.utcnow())
    db.add(call)
    db.commit()
    db.refresh(call)
    base = backend_base()
    params = TT.human_bridge_params(
        user_phone=ready["callback_phone"], from_=resolved.e164,
        url="%s/voice/human/bridge?call_id=%s" % (base, call.id),
        status_callback="%s/voice/human/status?call_id=%s" % (base, call.id))
    try:
        sid = TT.create_call(creds, **params)
    except Exception as exc:                                 # noqa: BLE001
        call.status = "failed"
        call.outcome = "failed"
        call.error_message = ("provider: %s" % exc)[:480]
        call.ended_at = datetime.utcnow()
        db.commit()
        raise RuntimeError(str(exc))
    call.call_sid = sid
    call.status = "ringing_user"
    db.commit()
    db.refresh(call)
    return call


def bridge_twiml(db, call: VoiceCall) -> str:
    """Leg 1 answered by the user: re-check the gates, then dial the lead."""
    from app.services import voice_bulk_gate
    lead = db.query(Lead).filter(Lead.id == call.lead_id,
                                 Lead.organization_id == call.organization_id).first()
    refusal = voice_bulk_gate.call_refusal(db, lead, call.organization_id, human=True) \
        if lead else "Lead not found."
    if refusal:
        call.status = "failed"
        call.outcome = "refused"
        call.error_message = refusal[:480]
        db.commit()
        return TT.twiml_hangup("This call can no longer be placed. %s" % refusal)
    call.status = "in_progress"
    call.started_at = datetime.utcnow()
    db.commit()
    return TT.twiml_human_bridge(lead_e164=call.to_phone, caller_id=call.from_phone,
                                 action_url="/voice/human/dial-status?call_id=%s" % call.id,
                                 lead_label=(lead.first_name or None))


_DIAL_OUTCOME = {"completed": "connected", "answered": "connected", "no-answer": "no_answer",
                 "busy": "busy", "failed": "failed", "canceled": "canceled"}


def human_dial_status(db, call: VoiceCall, dial_status: str, duration) -> str:
    call.twilio_status = (dial_status or "")[:40] or None
    call.outcome = _DIAL_OUTCOME.get(dial_status or "", call.outcome or "unknown")
    if call.outcome == "connected":
        call.answered_by = "human"
        call.is_live_conversation = True
        call.answered_at = call.answered_at or datetime.utcnow()
    try:
        if duration not in (None, ""):
            call.duration_seconds = int(duration)
    except (TypeError, ValueError):
        pass
    call.status = "completed"
    call.ended_at = datetime.utcnow()
    db.commit()
    return TT.twiml_hangup()


def human_leg_status(db, call: VoiceCall, status: str) -> None:
    status = status or ""
    call.twilio_status = status[:40] or call.twilio_status
    if status in ("no-answer", "busy", "failed", "canceled") and call.status in (
            "initiating", "ringing_user"):
        call.status = "failed"
        call.outcome = "user_" + status.replace("-", "_")
        call.ended_at = datetime.utcnow()
    elif status == "completed" and call.status not in ("completed", "failed"):
        call.status = "completed"
        call.ended_at = call.ended_at or datetime.utcnow()
    db.commit()


DISPOSITIONS = ("connected", "no_answer", "left_voicemail", "busy", "wrong_number",
                "not_interested", "interested", "callback_requested", "appointment_set", "other")


def call_json(c: VoiceCall) -> dict:
    iso = (lambda d: d.isoformat() if d else None)
    return {"id": c.id, "lead_id": c.lead_id, "status": c.status, "outcome": c.outcome,
            "direction": c.direction, "is_human_call": bool(c.is_human_call),
            "from_phone": c.from_phone, "to_phone": c.to_phone,
            "answered_by": c.answered_by, "duration_seconds": c.duration_seconds,
            "disposition": c.disposition, "disposition_notes": c.disposition_notes,
            "disposition_at": iso(c.disposition_at), "error": c.error_message,
            "created_at": iso(c.created_at), "ended_at": iso(c.ended_at),
            "advisor_id": c.advisor_id, "disposition_by_id": c.disposition_by_id,
            "provider": c.provider, "voicemail_state": voicemail_state(c),
            "answered_at": iso(c.answered_at), "started_at": iso(c.started_at)}


# ══ human dialer: state, history, next-call queue (2026-10-01) ═══════════════

def voicemail_state(c: VoiceCall) -> Optional[str]:
    """What we KNOW about a voicemail on this call, from recorded facts only:
    left (a drop played / the caller recorded it), machine (AMD heard one,
    nothing recorded as left), or None."""
    if c.voicemail_left or c.voicemail_drop_id or c.disposition == "left_voicemail":
        return "left"
    if c.answered_by == "voicemail" or (c.amd_status or "").startswith("machine"):
        return "machine"
    return None


# Dispositions that take a number out of the human queue until someone acts.
QUEUE_EXCLUDE_DISPOSITIONS = ("wrong_number", "not_interested", "appointment_set")
MANUAL_PROVIDER = "manual"


def start_manual_call_log(db, lead: Lead, user: User) -> VoiceCall:
    """The provider is not configured, so the user dials from their own phone
    (tel: link). This records THAT a call was made from the user's device so
    notes + disposition have a home. No provider call, no caller-ID claim:
    from_phone stays NULL because the number shown was the user's own."""
    from app.services import voice_bulk_gate
    refusal = voice_bulk_gate.call_refusal(db, lead, lead.organization_id, human=True)
    if refusal:
        raise PermissionError(refusal)
    prior = db.query(VoiceCall).filter(VoiceCall.lead_id == lead.id).count()
    now = datetime.utcnow()
    call = VoiceCall(lead_id=lead.id, advisor_id=user.id, organization_id=lead.organization_id,
                     to_phone=NR.normalize_e164(lead.phone) or (lead.phone or "")[:40],
                     from_phone=None, call_number=prior + 1, status="completed",
                     provider=MANUAL_PROVIDER, direction="outbound", is_human_call=True,
                     started_at=now, ended_at=now, created_at=now)
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def lead_call_history(db, lead: Lead, limit: int = 50) -> dict:
    """Calls (both directions) and voicemails for one lead, newest first.
    The caller has already authorized the lead; every query is also pinned to
    the lead's organization."""
    org_id = lead.organization_id
    calls = (db.query(VoiceCall)
             .filter(VoiceCall.organization_id == org_id, VoiceCall.lead_id == lead.id)
             .order_by(VoiceCall.created_at.desc()).limit(limit).all())
    vms = (db.query(Voicemail)
           .filter(Voicemail.organization_id == org_id, Voicemail.lead_id == lead.id)
           .order_by(Voicemail.received_at.desc()).limit(limit).all())
    iso = (lambda d: d.isoformat() if d else None)
    return {
        "lead_id": lead.id,
        "calls": [call_json(c) for c in calls],
        "voicemails": [{"id": v.id, "status": v.status, "from_phone": v.from_e164,
                        "duration_seconds": v.duration_seconds,
                        "received_at": iso(v.received_at),
                        "audio_url": "/voicemails/%s/audio" % v.id} for v in vms],
        "unreviewed_voicemails": sum(1 for v in vms if v.status == "new"),
    }


def dialer_queue(db, user: User, org_id: str, *, limit: int = 25, everyone: bool = False,
                 scan: int = 300) -> dict:
    """The user's callable leads, best next call first.

    Every candidate passes the SAME gate as the dial button
    (voice_bulk_gate.call_refusal, human=True): DNC by number, suppression,
    consent/compliance preflight, test records, manual remove_all,
    allow_voice False, wholesale seller voice permission, org pause-all.
    Excluded leads are counted by reason, never silently dropped.

    Order: an open callback/follow-up task that is due -> never called ->
    longest since last call."""
    from sqlalchemy import func
    from app.models.work_models import LeadTask
    from app.services import lead_scope, voice_bulk_gate
    q = (lead_scope.authorized_lead_query(db, user)
         .filter(Lead.organization_id == org_id, Lead.phone.isnot(None), Lead.phone != ""))
    if not (everyone and lead_scope.is_manager_here(user, db)):
        q = q.filter(Lead.assigned_to_id == user.id)
    leads = q.order_by(Lead.created_at.asc()).limit(scan).all()
    ids = [l.id for l in leads]
    last_call, last_dispo = {}, {}
    due = {}
    if ids:
        for lid, ts in (db.query(VoiceCall.lead_id, func.max(VoiceCall.created_at))
                        .filter(VoiceCall.organization_id == org_id, VoiceCall.lead_id.in_(ids))
                        .group_by(VoiceCall.lead_id).all()):
            last_call[lid] = ts
        for c in (db.query(VoiceCall.lead_id, VoiceCall.disposition, VoiceCall.created_at)
                  .filter(VoiceCall.organization_id == org_id, VoiceCall.lead_id.in_(ids),
                          VoiceCall.disposition.isnot(None))
                  .order_by(VoiceCall.created_at.asc()).all()):
            last_dispo[c.lead_id] = c.disposition
        now = datetime.utcnow()
        for t in (db.query(LeadTask.lead_id, func.min(LeadTask.due_at))
                  .filter(LeadTask.organization_id == org_id, LeadTask.lead_id.in_(ids),
                          LeadTask.status == "open", LeadTask.due_at.isnot(None),
                          LeadTask.due_at <= now)
                  .group_by(LeadTask.lead_id).all()):
            due[t[0]] = t[1]
    items, excluded = [], {}
    for l in leads:
        why = voice_bulk_gate.call_refusal(db, l, org_id, human=True)
        if not why and last_dispo.get(l.id) in QUEUE_EXCLUDE_DISPOSITIONS:
            why = "Last call outcome: %s" % last_dispo[l.id].replace("_", " ")
        if why:
            excluded[why] = excluded.get(why, 0) + 1
            continue
        items.append({"lead_id": l.id, "name": lead_label(l) or None, "phone": l.phone,
                      "status": l.status, "last_called_at": (last_call[l.id].isoformat()
                                                             if l.id in last_call else None),
                      "last_disposition": last_dispo.get(l.id),
                      "follow_up_due_at": due[l.id].isoformat() if l.id in due else None,
                      "reason": ("Follow-up due" if l.id in due else
                                 "Never called" if l.id not in last_call else "Oldest last call")})
    items.sort(key=lambda i: (0 if i["follow_up_due_at"] else 1 if not i["last_called_at"] else 2,
                              i["follow_up_due_at"] or i["last_called_at"] or ""))
    return {"items": items[:limit], "total_callable": len(items),
            "excluded": [{"reason": k, "count": v} for k, v in sorted(excluded.items())],
            "scanned": len(leads), "scope": "team" if (everyone and lead_scope.is_manager_here(user, db)) else "mine"}
