"""COMMUNICATION ELIGIBILITY - may this workspace reach this person on this
channel, and if not, exactly why not?

One deterministic answer per (person, channel), built from the platform's own
authorities - never a copy of them:

    SMS     wholesale_sms.check_eligibility   program consent of record, opt-out,
                                              suppression, DNC, program switch,
                                              registered sender, quiet hours
    email   compliance_service preflight      DNC, capacity hold, allow_email,
            rules                             bad_email / remove_all
            org_contacts.email_status         bounce, unsubscribe, suppressed
            + the permission basis below

Every check falls in one of four categories, because they mean different things
to the person reading the answer and must never be merged:

    permission   is there EVIDENCE we may use this channel for this person
    block        is there a deterministic reason we may NOT (opt-out, DNC...)
    operational  could the workspace actually send on this channel today
    timing       not now (quiet hours) - never a reason for "not eligible"

`eligible` is True only when permission holds, nothing blocks and the channel is
operational. Quiet hours are reported, not counted: the send path enforces the
recipient's clock on every message.

WHAT THIS IS NOT
  * Not outreach policy. Whether a strategy SHOULD contact an owner now (one
    conversation per owner, frequency caps, confidence minimums) is EvoSense's
    `eligibility.check`, which sits on top of this.
  * Not a sender. Nothing here sends, queues or schedules anything.
  * Never a grant. Finding a mobile number is not SMS consent, and a found
    email address is not permission to email. Only evidence is.

EMAIL PERMISSION BASIS (deterministic, most specific first)
  SELLER_INITIATED       the person contacted this workspace (a seller inquiry)
  EXPLICIT_PERMISSION    Lead.allow_email is True
  WORKSPACE_CONFIRMED    the workspace recorded that emailing owners it found is
                         lawful for it (WholesaleSettings.cold_seller_email_confirmed)
  none                   -> NO_EMAIL_PERMISSION
Email has no seller sending path yet, so it is reported NOT OPERATIONAL even when
permitted - the answer says "permitted, but nothing can send it", which is true.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

SMS = "sms"
EMAIL = "email"
VOICE = "voice"
CHANNELS = (SMS, EMAIL, VOICE)

PERMISSION, BLOCK, OPERATIONAL, TIMING = "permission", "block", "operational", "timing"

# SMS reason -> category. Everything wholesale_sms can say, and nothing else.
_SMS_CATEGORY = {
    "NO_SMS_CONSENT": PERMISSION,
    "INVALID_NUMBER": BLOCK, "OPTED_OUT": BLOCK, "SUPPRESSED": BLOCK, "DNC": BLOCK,
    "PROGRAM_MISMATCH": BLOCK,
    "PROGRAM_DISABLED": OPERATIONAL, "MESSAGING_SERVICE_NOT_CONFIGURED": OPERATIONAL,
    "QUIET_HOURS": TIMING,
}
_SMS_LABEL = {
    "NO_SMS_CONSENT": "No SMS consent of record for this number (a found number is never consent)",
    "INVALID_NUMBER": "Not a usable US number",
    "OPTED_OUT": "This number opted out",
    "SUPPRESSED": "On this workspace's suppression list",
    "DNC": "A record with this number is Do Not Contact",
    "PROGRAM_MISMATCH": "Wrong program or workspace for this number",
    "PROGRAM_DISABLED": "The seller SMS program is off",
    "MESSAGING_SERVICE_NOT_CONFIGURED": "No registered SMS sender is configured",
    "QUIET_HOURS": "Outside the recipient's contact hours right now",
}

# Seller-originated sources: the person reached out to this workspace.
SELLER_INITIATED_SOURCES = ("wholesale_seller_inquiry",)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_BAD_EMAIL_STATUS = {"invalid": "Address failed validation",
                     "hard_bounce": "Address hard-bounced",
                     "unsubscribed": "Unsubscribed",
                     "suppressed": "Email suppressed for this person"}


def _check(code, ok, category, label, detail=None) -> Dict[str, Any]:
    return {"code": code, "ok": bool(ok), "category": category, "label": label, "detail": detail}


def _verdict(channel: str, checks: List[Dict[str, Any]], *, basis: Optional[str] = None,
             value: Optional[str] = None) -> Dict[str, Any]:
    failing = [c for c in checks if not c["ok"]]
    blocks = [c for c in failing if c["category"] == BLOCK]
    missing = [c for c in failing if c["category"] == PERMISSION]
    down = [c for c in failing if c["category"] == OPERATIONAL]
    timing = [c for c in failing if c["category"] == TIMING]
    if blocks:
        state = "BLOCKED"
    elif missing:
        state = "NO_PERMISSION"
    elif down:
        state = "NOT_OPERATIONAL"
    else:
        state = "ELIGIBLE"
    first = (blocks or missing or down or [None])[0]
    return {
        "channel": channel,
        "value": value,
        "eligible": state == "ELIGIBLE",
        "state": state,
        "permitted": not blocks and not missing,       # evidence allows it, ignoring plumbing
        "operational": not down,
        "permission_basis": basis if not missing else None,
        "blocks": [c["code"] for c in blocks],
        "missing": [c["code"] for c in missing],
        "not_operational": [c["code"] for c in down],
        "timing": [c["code"] for c in timing],
        "reason": first["label"] if first else None,
        "checks": checks,
    }


def _lead_blocks(lead, channel: str) -> List[Dict[str, Any]]:
    """What the LEAD itself says, for any channel. Mirrors the platform's
    preflight + test_records rules without raising."""
    out: List[Dict[str, Any]] = []
    if lead is None:
        return out
    out.append(_check("DNC", (lead.status or "") != "dnc", BLOCK,
                      "This person is Do Not Contact"))
    out.append(_check("REMOVE_ALL", getattr(lead, "manual_flag", None) != "remove_all", BLOCK,
                      "Manually flagged: no outreach on any channel"))
    out.append(_check("TEST_RECORD", not getattr(lead, "is_test", False), BLOCK,
                      "Internal test record - excluded from real outreach"))
    try:
        from app.services import lead_capacity
        held = lead_capacity.is_held(lead)
    except Exception:  # noqa: BLE001
        held = False
    out.append(_check("CAPACITY_HELD", not held, BLOCK,
                      "Held: the workspace is at its lead limit"))
    if channel == SMS and getattr(lead, "allow_sms", None) is False:
        out.append(_check("SMS_DENIED", False, BLOCK, "The record says: no SMS"))
    if channel == EMAIL:
        if getattr(lead, "allow_email", None) is False:
            out.append(_check("EMAIL_DENIED", False, BLOCK, "The record says: no email"))
        if getattr(lead, "manual_flag", None) == "bad_email":
            out.append(_check("BAD_EMAIL", False, BLOCK, "Email flagged as bad"))
    return out


def sms(db, org_id: str, phone: Optional[str], *, lead=None, line_type: Optional[str] = None,
        contact_status: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """SMS eligibility for one number in one workspace. Reuses the seller SMS
    program gate unchanged; adds the lead's own flags and the line type."""
    from app.services import wholesale_sms as WS
    checks = _lead_blocks(lead, SMS)
    if contact_status and contact_status != "active":
        checks.append(_check("CONTACT_STATUS", False, BLOCK,
                             "Contact point is %s" % contact_status.replace("_", " ")))
    if line_type == "landline":
        checks.append(_check("LANDLINE", False, BLOCK, "Landline - cannot receive SMS"))
    gate = WS.check_eligibility(db, org_id, phone, lead=lead, now=now)
    seen = {c["code"] for c in checks}
    for code in gate["reasons"]:
        if code in seen:
            continue
        checks.append(_check(code, False, _SMS_CATEGORY.get(code, BLOCK),
                             _SMS_LABEL.get(code, code.replace("_", " ").title())))
    if "NO_SMS_CONSENT" not in gate["reasons"] and "OPTED_OUT" not in gate["reasons"] \
            and gate.get("consent_id"):
        checks.append(_check("SMS_CONSENT", True, PERMISSION, "SMS consent of record"))
    basis = "SMS_CONSENT_OF_RECORD" if gate.get("consent_id") else None
    return _verdict(SMS, checks, basis=basis, value=gate.get("phone") or phone)


def _email_status_for(db, org_id: str, address: str, contact=None) -> Optional[str]:
    if contact is not None:
        return getattr(contact, "email_status", None)
    try:
        from app.models.intake_models import OrgContact
        c = (db.query(OrgContact)
             .filter(OrgContact.organization_id == org_id, OrgContact.email == address,
                     OrgContact.archived_at.is_(None))
             .order_by(OrgContact.updated_at.desc()).first())
        return getattr(c, "email_status", None)
    except Exception:  # noqa: BLE001
        return None


def email(db, org_id: str, address: Optional[str], *, lead=None, contact=None,
          seller_initiated: Optional[bool] = None, contact_status: Optional[str] = None
          ) -> Dict[str, Any]:
    """Email eligibility for one address in one workspace - its own model,
    never inherited from SMS."""
    from app.models.models import Lead
    addr = (address or "").strip().lower() or None
    checks = _lead_blocks(lead, EMAIL)
    checks.append(_check("NO_EMAIL", bool(addr), BLOCK, "No email address"))
    if addr:
        checks.append(_check("INVALID_EMAIL", bool(_EMAIL_RE.match(addr)), BLOCK,
                             "Not a valid email address"))
        if contact_status and contact_status != "active":
            checks.append(_check("CONTACT_STATUS", False, BLOCK,
                                 "Contact point is %s" % contact_status.replace("_", " ")))
        status = _email_status_for(db, org_id, addr, contact)
        if status in _BAD_EMAIL_STATUS:
            checks.append(_check("EMAIL_" + status.upper(), False, BLOCK,
                                 _BAD_EMAIL_STATUS[status]))
        # Any record in this workspace with this address that is DNC.
        dnc = (db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.status == "dnc",
                                        Lead.email == addr).first() is not None)
        if dnc and not any(c["code"] == "DNC" and not c["ok"] for c in checks):
            checks.append(_check("DNC", False, BLOCK, "A record with this address is Do Not Contact"))

    if seller_initiated is None:
        seller_initiated = bool(lead is not None and
                                (getattr(lead, "source_category", None) in SELLER_INITIATED_SOURCES))
    basis = None
    if seller_initiated:
        basis = "SELLER_INITIATED"
    elif lead is not None and getattr(lead, "allow_email", None) is True:
        basis = "EXPLICIT_PERMISSION"
    else:
        from app.services import wholesale_sms as WS
        s = WS.settings_row(db, org_id)
        if s is not None and getattr(s, "cold_seller_email_confirmed", False):
            basis = "WORKSPACE_CONFIRMED"
    checks.append(_check("NO_EMAIL_PERMISSION", basis is not None, PERMISSION,
                         "No basis to email: they never wrote to us, never said yes, and the "
                         "workspace has not confirmed emailing owners it found"
                         if basis is None else "Permission basis: %s" % basis.replace("_", " ").lower()))
    try:
        from app.services.evosense import common as C
        paused = bool(C.controls(db, org_id).paused_email)
    except Exception:  # noqa: BLE001
        paused = False
    if paused:
        checks.append(_check("EMAIL_PAUSED", False, OPERATIONAL, "Email outreach is paused"))
    checks.append(_check("EMAIL_CHANNEL_NOT_BUILT", False, OPERATIONAL,
                         "There is no seller email sending path yet - nothing can send it"))
    return _verdict(EMAIL, checks, basis=basis, value=addr)


def voice(db, org_id: str, phone: Optional[str], *, lead=None,
          seller_initiated: Optional[bool] = None) -> Dict[str, Any]:
    """A phone call by a PERSON. The platform records whether anything forbids
    it and what the permission basis is; it never dials.

    Permission: the person contacted us (a seller inquiry) or the record says
    calls are allowed. Calling an owner who never contacted the workspace
    raises Do-Not-Call-registry questions that are the workspace's to answer,
    so it is NO_VOICE_PERMISSION here, never assumed."""
    from app.services import compliance_service
    checks = _lead_blocks(lead, VOICE)
    usable = compliance_service.usable_us_phone(phone) if phone else None
    checks.append(_check("NO_PHONE", bool(usable), BLOCK, "No usable phone number"))
    if usable:
        sup = compliance_service.is_phone_suppressed(db, org_id, usable)
        checks.append(_check("SUPPRESSED", not sup, BLOCK, "On this workspace's suppression list"))
    if lead is not None and getattr(lead, "allow_voice", None) is False:
        checks.append(_check("VOICE_DENIED", False, BLOCK, "The record says: no calls"))
    if seller_initiated is None:
        seller_initiated = bool(lead is not None and
                                getattr(lead, "source_category", None) in SELLER_INITIATED_SOURCES)
    basis = ("SELLER_INITIATED" if seller_initiated else
             "EXPLICIT_PERMISSION" if (lead is not None and getattr(lead, "allow_voice", None) is True)
             else None)
    checks.append(_check("NO_VOICE_PERMISSION", basis is not None, PERMISSION,
                         "They never contacted us and never agreed to calls" if basis is None
                         else "Permission basis: %s" % basis.replace("_", " ").lower()))
    # A person makes the call: "operational" means a person can, which is true.
    return _verdict(VOICE, checks, basis=basis, value=usable)


def summarize(verdicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Best channel first: eligible > permitted-but-not-operational > no permission."""
    rank = {"ELIGIBLE": 0, "NOT_OPERATIONAL": 1, "NO_PERMISSION": 2, "BLOCKED": 3}
    ordered = sorted(verdicts, key=lambda v: rank.get(v["state"], 9))
    return {"best": ordered[0] if ordered else None,
            "eligible_channels": [v["channel"] for v in ordered if v["eligible"]],
            "permitted_channels": [v["channel"] for v in ordered if v["permitted"]]}
