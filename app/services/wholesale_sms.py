"""The Wholesale seller SMS program: consent, opt-out, and THE send gate.

WHAT THIS MODULE GUARANTEES
---------------------------
No SMS reaches a Wholesale seller unless ALL of these hold, checked on the
server immediately before the provider call, in this order:

    PROGRAM_DISABLED                  kill switch (env), program switch (org), EvoSense SMS pause
    PROGRAM_MISMATCH                  wrong program, or the lead is not in this organization
    INVALID_NUMBER                    not a usable US mobile number
    NO_SMS_CONSENT                    no program consent of record for this number in this org
    OPTED_OUT                         the latest consent has been withdrawn (STOP etc.)
    SUPPRESSED                        on this organization's suppression list
    DNC                               a lead with this number in this org is marked DNC
    QUIET_HOURS                       outside 09:00-20:00 in the recipient's own zone
    MESSAGING_SERVICE_NOT_CONFIGURED  no Messaging Service SID / org Twilio account

Every failing check is reported, not just the first, as stable machine-readable
codes. There is no override parameter, no "force", and nothing an AI or a
strategy can set to skip a check: the gate reads only the database and the
clock. A refused send raises `WholesaleSmsBlocked` (a ValueError, so every
existing caller that treats ValueError as "blocked, move on" does exactly that)
and is logged with its codes. It never fails silently.

PHONE KNOWN IS NOT PERMISSION
-----------------------------
Owner identified, phone found, phone validated, mobile likely - none of those
is consent, and nothing here reads them. Contact Confidence, Seller Intent and
Property Opportunity scores are EvoSense's business and are never consulted.
The only thing that makes a number eligible is a row in `sms_consent_records`,
and only `record_consent` writes one - from a person ticking an unticked box.

WHICH LEADS ARE "PROGRAM" LEADS
-------------------------------
A Wholesale seller: a lead whose source is a Wholesale path, or that has a
Wholesale seller profile, or that EvoSense is working. For those leads the gate
applies on EVERY send path - manual composer, cadence, AI replies, workforce,
EvoSense - because they all funnel through `sms_service.send_sms/send_mms`,
and the few paths that call Twilio directly call `refusal_for_phone` first.
A Wholesale seller's SMS goes out ONLY through the program's Messaging Service,
never an advisor's or the organization's general number.

The module names no brand and no organization. EVO Integrated Solutions LLC is
configuration (its WholesaleSettings row), not code.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.models.sms_consent_models import (METHOD_WEB_CHECKBOX, STATUS_OPTED_IN,
                                           STATUS_OPTED_OUT, SmsConsentRecord)

log = logging.getLogger(__name__)

# Stable, brand-neutral program identifier. Consent is scoped to it.
PROGRAM = "wholesale_seller_sms"
FORM_ID = "wholesale_seller_inquiry"
SOURCE_CATEGORY = "wholesale_seller_inquiry"
PROGRAM_SOURCE_CATEGORIES = ("wholesale", "evosense", SOURCE_CATEGORY)

# Kill switch that needs no deploy and no database: set on the service.
KILL_SWITCH_ENV = "WHOLESALE_SMS_KILL_SWITCH"

# Reason codes. Stable strings - dashboards and tests group by them.
PROGRAM_DISABLED = "PROGRAM_DISABLED"
PROGRAM_MISMATCH = "PROGRAM_MISMATCH"
INVALID_NUMBER = "INVALID_NUMBER"
NO_SMS_CONSENT = "NO_SMS_CONSENT"
OPTED_OUT = "OPTED_OUT"
SUPPRESSED = "SUPPRESSED"
DNC = "DNC"
QUIET_HOURS = "QUIET_HOURS"
MESSAGING_SERVICE_NOT_CONFIGURED = "MESSAGING_SERVICE_NOT_CONFIGURED"
REASON_CODES = (PROGRAM_DISABLED, PROGRAM_MISMATCH, INVALID_NUMBER, NO_SMS_CONSENT,
                OPTED_OUT, SUPPRESSED, DNC, QUIET_HOURS,
                MESSAGING_SERVICE_NOT_CONFIGURED)

# Message copy registered with the campaign. {brand} is the organization's
# program display name, e.g. "EvoSys Wholesale"; {email} its support address.
OPT_IN_CONFIRMATION = ("{brand}: You're subscribed to messages about your property "
                       "inquiry. Msg frequency varies. Msg & data rates may apply. "
                       "Reply STOP to opt out, HELP for help.")
HELP_REPLY = ("{brand} Support: For help email {email} or reply STOP to cancel. "
              "Msg & data rates may apply.")

_TRUE = {"1", "true", "yes", "y", "on", "checked"}


class WholesaleSmsBlocked(ValueError):
    """A program send the gate refused. `.reasons` holds the codes."""

    def __init__(self, reasons: List[str], detail: str = ""):
        self.reasons = list(reasons)
        super().__init__("WHOLESALE_SMS_BLOCKED: %s%s" % (
            ",".join(self.reasons), (" - " + detail) if detail else ""))


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return value is not None and str(value).strip().lower() in _TRUE


def normalize_e164(raw: Optional[str]) -> Optional[str]:
    """+1XXXXXXXXXX for a usable US number, else None. Never guesses."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        return None
    return "+1" + digits


def _phone_forms(e164: str) -> List[str]:
    d = e164[2:]
    return [e164, "1" + d, d]


def settings_row(db, org_id: Optional[str]):
    """This org's WholesaleSettings, or None. Read-only - never creates one."""
    if not org_id:
        return None
    from app.models.wholesale_models import WholesaleSettings
    return (db.query(WholesaleSettings)
            .filter(WholesaleSettings.organization_id == org_id).first())


# ── Configuration ───────────────────────────────────────────────────────────

def kill_switch_on() -> bool:
    return truthy(os.environ.get(KILL_SWITCH_ENV))


def _org_twilio(db, org_id: str):
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or not org.org_twilio_account_sid or not org.org_twilio_auth_token_encrypted:
        return None, None
    return org, org.org_twilio_account_sid


def messaging_configured(db, org_id: str, settings=None) -> bool:
    s = settings if settings is not None else settings_row(db, org_id)
    sid = (getattr(s, "sms_messaging_service_sid", None) or "").strip()
    if not (sid.startswith("MG") and len(sid) == 34):
        return False
    org, _ = _org_twilio(db, org_id)
    return org is not None


def program_status(db, org_id: str) -> Dict[str, Any]:
    """What an operator needs to know about the program, WITHOUT secrets."""
    s = settings_row(db, org_id)
    ready = messaging_configured(db, org_id, s)
    return {
        "program": PROGRAM,
        "enabled": bool(getattr(s, "sms_program_enabled", False)),
        "kill_switch": kill_switch_on(),
        "messaging_service_configured": ready,
        "messaging_service_sid": getattr(s, "sms_messaging_service_sid", None),
        "campaign_sid": getattr(s, "sms_campaign_sid", None),
        "brand_sid": getattr(s, "sms_brand_sid", None),
        "sender_number": getattr(s, "sms_sender_number", None),
        "public_intake_enabled": bool(getattr(s, "public_intake_key", None)),
        "can_send": bool(getattr(s, "sms_program_enabled", False)) and ready
                    and not kill_switch_on(),
    }


# ── Program membership ──────────────────────────────────────────────────────

def is_program_lead(db, lead) -> bool:
    """True for a Wholesale seller - see the module docstring."""
    if lead is None:
        return False
    if (getattr(lead, "source_category", None) or "") in PROGRAM_SOURCE_CATEGORIES:
        return True
    lead_id = getattr(lead, "id", None)
    if not lead_id:
        return False
    from app.models.wholesale_models import WholesaleSellerProfile
    if db.query(WholesaleSellerProfile.id).filter(
            WholesaleSellerProfile.lead_id == lead_id).first() is not None:
        return True
    from app.models.evosense_models import EvoSenseEngagement
    return db.query(EvoSenseEngagement.id).filter(
        EvoSenseEngagement.lead_id == lead_id).first() is not None


def program_lead_for_phone(db, org_id: str, phone: Optional[str]):
    """The program lead in this org holding this number, if any."""
    e164 = normalize_e164(phone)
    if not e164 or not org_id:
        return None
    from app.models.models import Lead
    for lead in (db.query(Lead)
                 .filter(Lead.organization_id == org_id,
                         Lead.phone.in_(_phone_forms(e164))).all()):
        if is_program_lead(db, lead):
            return lead
    return None


# ── Consent of record ───────────────────────────────────────────────────────

def latest_consent(db, org_id: str, e164: str, program: str = PROGRAM):
    return (db.query(SmsConsentRecord)
            .filter(SmsConsentRecord.organization_id == org_id,
                    SmsConsentRecord.program == program,
                    SmsConsentRecord.phone_normalized == e164)
            .order_by(SmsConsentRecord.consented_at.desc(),
                      SmsConsentRecord.created_at.desc())
            .first())


def record_consent(db, org_id: str, *, phone_raw: str, disclosure_text: str,
                   disclosure_version: Optional[str], form_version: Optional[str],
                   source_url: Optional[str], ip: Optional[str], user_agent: Optional[str],
                   lead=None, profile_id: Optional[str] = None,
                   property_id: Optional[str] = None, deal_id: Optional[str] = None,
                   program: str = PROGRAM) -> SmsConsentRecord:
    """Write one consent record. The ONLY writer. Caller commits.

    Called only after a person ticked the unticked box. The timestamp is the
    server's; nothing the caller passes can set it."""
    e164 = normalize_e164(phone_raw)
    if not e164:
        raise ValueError("Consent requires a usable US mobile number.")
    s = settings_row(db, org_id)
    now = datetime.utcnow()
    rec = SmsConsentRecord(
        organization_id=org_id, program=program,
        phone_raw=(phone_raw or "")[:40], phone_normalized=e164,
        status=STATUS_OPTED_IN, consent_given=True, consented_at=now,
        consent_method=METHOD_WEB_CHECKBOX,
        source_url=(source_url or "")[:500] or None, form_id=FORM_ID,
        form_version=(form_version or "")[:64] or None,
        disclosure_version=(disclosure_version or "")[:64] or None,
        disclosure_text=(disclosure_text or "")[:4000] or None,
        ip_address=(ip or "")[:64] or None, user_agent=(user_agent or "")[:400] or None,
        lead_id=getattr(lead, "id", None), seller_profile_id=profile_id,
        property_id=property_id, deal_id=deal_id,
        campaign_sid=getattr(s, "sms_campaign_sid", None),
        messaging_service_sid=getattr(s, "sms_messaging_service_sid", None),
        created_at=now, updated_at=now)
    db.add(rec)
    if lead is not None:
        # Mirror onto the lead's existing consent columns so every existing
        # screen says the same thing. Server time, verbatim wording.
        lead.sms_consent = True
        lead.sms_consent_timestamp = now
        lead.sms_consent_ip = rec.ip_address
        lead.sms_consent_text = rec.disclosure_text
        lead.sms_consent_source = " · ".join(b for b in (
            rec.source_url, rec.disclosure_version and "v" + rec.disclosure_version,
            PROGRAM) if b)[:400]
    db.flush()
    return rec


def record_opt_out(db, org_id: str, phone: Optional[str], *, keyword: Optional[str] = None,
                   reason: Optional[str] = None, source: str = "reply_stop",
                   program: Optional[str] = None) -> int:
    """Withdraw every active consent for this number in this org. Caller commits.

    Scoped to the organization the opt-out arrived for; `program=None` means
    every program in that organization, which is what a STOP means."""
    e164 = normalize_e164(phone)
    if not e164 or not org_id:
        return 0
    q = (db.query(SmsConsentRecord)
         .filter(SmsConsentRecord.organization_id == org_id,
                 SmsConsentRecord.phone_normalized == e164,
                 SmsConsentRecord.status == STATUS_OPTED_IN))
    if program:
        q = q.filter(SmsConsentRecord.program == program)
    now = datetime.utcnow()
    n = 0
    for rec in q.all():
        rec.status = STATUS_OPTED_OUT
        rec.opted_out_at = now
        rec.opt_out_keyword = (keyword or "")[:40] or None
        rec.opt_out_reason = (reason or "")[:255] or None
        rec.opt_out_source = source
        rec.updated_at = now
        n += 1
    if n:
        db.flush()
        log.info("wholesale_sms: opt-out recorded org=%s records=%d source=%s", org_id, n, source)
    return n


def consent_json(rec: Optional[SmsConsentRecord]) -> Optional[Dict[str, Any]]:
    if rec is None:
        return None
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {
        "id": rec.id, "program": rec.program, "status": rec.status,
        "phone": rec.phone_normalized, "consented_at": iso(rec.consented_at),
        "consent_method": rec.consent_method, "source_url": rec.source_url,
        "form_id": rec.form_id, "form_version": rec.form_version,
        "disclosure_version": rec.disclosure_version,
        "disclosure_text": rec.disclosure_text,
        "ip_address": rec.ip_address, "user_agent": rec.user_agent,
        "lead_id": rec.lead_id, "property_id": rec.property_id, "deal_id": rec.deal_id,
        "campaign_sid": rec.campaign_sid, "messaging_service_sid": rec.messaging_service_sid,
        "confirmation_status": rec.confirmation_status,
        "opted_out_at": iso(rec.opted_out_at), "opt_out_keyword": rec.opt_out_keyword,
        "opt_out_reason": rec.opt_out_reason, "opt_out_source": rec.opt_out_source,
    }


# ── THE GATE ────────────────────────────────────────────────────────────────

def check_eligibility(db, org_id: str, phone: Optional[str], *, program: str = PROGRAM,
                      lead=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Deterministic. Reads the database and the clock, nothing else.

    Returns {"eligible", "reasons", "primary", "program", "organization_id",
    "phone", "consent_id", "checks"}. Every failing check is listed."""
    from app.services import compliance_service, contact_hours
    from app.models.models import Lead

    now = now or datetime.utcnow()
    reasons: List[str] = []
    checks: Dict[str, Any] = {}
    s = settings_row(db, org_id)
    e164 = normalize_e164(phone)

    # 1. Switches.
    enabled = bool(getattr(s, "sms_program_enabled", False))
    paused = False
    try:
        from app.models.evosense_models import EvoSenseControl
        ctl = (db.query(EvoSenseControl)
               .filter(EvoSenseControl.organization_id == org_id).first())
        paused = bool(ctl and (ctl.paused_all or ctl.paused_sms))
    except Exception:                                       # noqa: BLE001
        paused = True                                       # unknown means stopped
    checks.update(kill_switch=kill_switch_on(), program_enabled=enabled, evosense_sms_paused=paused)
    if kill_switch_on() or not enabled or paused:
        reasons.append(PROGRAM_DISABLED)

    # 2. Right program, right organization.
    lead_org = getattr(lead, "organization_id", None) if lead is not None else None
    if program != PROGRAM or not org_id or (lead is not None and lead_org != org_id):
        reasons.append(PROGRAM_MISMATCH)

    # 3. A number we can match against consent and suppression at all.
    if not e164:
        reasons.append(INVALID_NUMBER)

    consent = None
    if e164 and org_id:
        consent = latest_consent(db, org_id, e164, program)
        if consent is None:
            reasons.append(NO_SMS_CONSENT)
        elif consent.status != STATUS_OPTED_IN or consent.opted_out_at is not None:
            reasons.append(OPTED_OUT)
        if compliance_service.is_phone_suppressed(db, org_id, e164):
            reasons.append(SUPPRESSED)
        if (db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.status == "dnc",
                                     Lead.phone.in_(_phone_forms(e164))).first() is not None):
            reasons.append(DNC)
    elif not e164:
        reasons.append(NO_SMS_CONSENT)

    # 4. The recipient's clock.
    subject = lead
    if subject is None and consent is not None and consent.lead_id:
        subject = db.query(Lead).filter(Lead.id == consent.lead_id,
                                        Lead.organization_id == org_id).first()
    if subject is None:
        subject = SimpleNamespace(state=None, zip_code=None, phone=e164)
    hours = contact_hours.check(subject, now)
    checks["contact_hours"] = {"permitted": hours.get("permitted"), "code": hours.get("code")}
    if not hours.get("permitted"):
        reasons.append(QUIET_HOURS)

    # 5. The registered sender.
    if not messaging_configured(db, org_id, s):
        reasons.append(MESSAGING_SERVICE_NOT_CONFIGURED)

    reasons = [r for r in REASON_CODES if r in reasons]      # stable order, no dupes
    return {"eligible": not reasons, "reasons": reasons,
            "primary": reasons[0] if reasons else None, "program": program,
            "organization_id": org_id, "phone": e164,
            "consent_id": getattr(consent, "id", None), "checks": checks}


def _log_block(org_id, lead_id, result, path) -> None:
    log.warning("wholesale_sms BLOCKED %s", json.dumps({
        "path": path, "organization_id": org_id, "lead_id": lead_id,
        "reasons": result["reasons"], "program": result["program"]}))


def enforce_for_lead(db, lead, *, path: str = "send_sms",
                     now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """For a program lead: the gate, or raise. For anyone else: None.

    Called from inside `sms_service.send_sms/send_mms`, so no caller - human,
    cadence, AI, workforce or EvoSense - reaches a Wholesale seller any other
    way. Returns the sender to use when permitted."""
    if not is_program_lead(db, lead):
        return None
    org_id = getattr(lead, "organization_id", None)
    result = check_eligibility(db, org_id, getattr(lead, "phone", None), lead=lead, now=now)
    if not result["eligible"]:
        _log_block(org_id, getattr(lead, "id", None), result, path)
        raise WholesaleSmsBlocked(result["reasons"])
    return program_sender(db, org_id)


def refusal_for_phone(db, org_id: str, phone: Optional[str], *, path: str) -> Optional[List[str]]:
    """For the few paths that call Twilio directly (reminders, confirmations).

    None means "not a Wholesale seller, carry on as before". A list means a
    Wholesale seller the gate refused - the caller must not send. Even a
    PERMITTED program send is refused here, because those paths would send
    from an advisor or organization number rather than the program's
    Messaging Service; a Wholesale seller is only ever texted by the program."""
    lead = program_lead_for_phone(db, org_id, phone)
    if lead is None:
        return None
    result = check_eligibility(db, org_id, phone, lead=lead)
    reasons = result["reasons"] or ["PROGRAM_SENDER_REQUIRED"]
    _log_block(org_id, lead.id, dict(result, reasons=reasons), path)
    return reasons


def program_sender(db, org_id: str):
    """(twilio_client, messaging_service_sid) for the program. Raises if absent."""
    s = settings_row(db, org_id)
    if not messaging_configured(db, org_id, s):
        raise WholesaleSmsBlocked([MESSAGING_SERVICE_NOT_CONFIGURED])
    from twilio.rest import Client
    from app.utils.crypto import decrypt_value
    org, account_sid = _org_twilio(db, org_id)
    client = Client(account_sid, decrypt_value(org.org_twilio_auth_token_encrypted))
    return client, s.sms_messaging_service_sid.strip()


def program_brand(db, org_id: str) -> Dict[str, Optional[str]]:
    """The names the registered copy uses, from configuration only."""
    name = None
    try:
        from app.models.models import Organization, Platform
        from app.services import brand_config
        org = db.query(Organization).filter(Organization.id == org_id).first()
        plat = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if org is not None and getattr(org, "platform_id", None) else None)
        name = brand_config.product_name(getattr(plat, "slug", None), "wholesale")
    except Exception:                                       # noqa: BLE001
        log.exception("wholesale_sms: product name lookup failed for org %s", org_id)
    s = settings_row(db, org_id)
    return {"brand": name or "Wholesale",
            "email": getattr(s, "public_contact_email", None)}


def attribution_user_id(db, lead) -> Optional[str]:
    """Who a program message is attributed to: the seller's assigned operator,
    else this organization's longest-standing active administrator. Never a
    user of another organization, never the platform owner."""
    if getattr(lead, "assigned_to_id", None):
        return lead.assigned_to_id
    from app.models.models import User
    row = (db.query(User.id)
           .filter(User.organization_id == lead.organization_id,
                   User.role == "org_admin", User.is_active.isnot(False))
           .order_by(User.created_at.asc()).first())
    return row[0] if row else None


def send_program_sms(db, lead, body: str, *, sender_user_id: Optional[str],
                     send_source: str = "wholesale_program") -> Dict[str, Any]:
    """Send one program SMS through the gate. Never raises for a refusal:
    returns {"sent": False, "reasons": [...]} so the caller can record it."""
    from app.models.models import Message
    from app.services.twilio_callbacks import apply_status_callback
    if not is_program_lead(db, lead):
        # Not a program lead: this function is for program sends only.
        return {"sent": False, "reasons": [PROGRAM_MISMATCH]}
    org_id = getattr(lead, "organization_id", None)
    result = check_eligibility(db, org_id, lead.phone, lead=lead)
    if not result["eligible"]:
        _log_block(org_id, lead.id, result, send_source)
        return {"sent": False, "reasons": result["reasons"]}
    if not sender_user_id:
        # messages.sender_id is NOT NULL: a program message is attributed to
        # the operator who owns the seller. None assigned -> not sent, and said.
        return {"sent": False, "reasons": ["NO_SENDER_USER"]}
    client, mg_sid = program_sender(db, org_id)
    kwargs = apply_status_callback(dict(body=body, messaging_service_sid=mg_sid, to=lead.phone))
    msg = client.messages.create(**kwargs)
    db.add(Message(lead_id=lead.id, sender_id=sender_user_id, body=body,
                   twilio_sid=msg.sid, twilio_status=msg.status, delivery_status="pending",
                   send_source=send_source))
    db.flush()
    return {"sent": True, "sid": msg.sid}
