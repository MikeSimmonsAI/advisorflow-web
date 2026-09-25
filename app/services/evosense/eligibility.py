"""May EvoSense contact this person, through this channel, right now?

Every check is SERVER-SIDE and runs immediately before a message would be
queued. The authorities are the platform's own, not EvoSense copies:

    Lead.status == "dnc"                    platform DNC (any lead with this number)
    suppression_entries                     compliance_service.is_phone_suppressed
    compliance_service.usable_us_phone      the send path's own number rule

On top of those, EvoSense's own rules:

    kill switches (EvoSense / SMS)          evosense_controls
    contact-point status                    wrong_party / opted_out / invalid never reused
    line type                               SMS to a landline is refused
    Contact Confidence vs strategy minimum
    ONE OWNER, ONE CONVERSATION             an owner of six properties is worked once,
                                            not six times by six strategies
    owner-level frequency cap               one touch per owner per N days
    already a Wholesale deal
    REAL cold SMS                           refused unless the strategy carries the
                                            organization's compliance confirmation

A strategy cannot override any of these. There is no "force" parameter.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from app.models.evosense_models import EvoSenseEngagement, EvoSenseOwner, EvoSenseProperty
from app.models.models import Lead
from app.services import compliance_service
from app.services.evosense import common as C
from app.services.evosense import strategy as ST

LIVE_ENGAGEMENT = ("active", "responded", "nurture", "handed_off")


def _check(code, ok, label, detail=None):
    return {"code": code, "ok": bool(ok), "label": label, "detail": detail}


def platform_dnc(db, org_id: str, phone: str) -> bool:
    digits = compliance_service.usable_us_phone(phone)
    if not digits:
        return False
    return (db.query(Lead.id)
            .filter(Lead.organization_id == org_id, Lead.status == "dnc",
                    Lead.phone.in_((digits, "+" + digits, digits[1:]))).first() is not None)


def check(db, prop: EvoSenseProperty, cp, strategy, *, channel: str = "sms",
          engagement_id: str = None, mark: bool = True) -> Dict[str, Any]:
    """Returns {"eligible", "mode", "blocks": [codes], "checks": [...], "summary"}.

    `mark=True` records what the platform said on the contact point, so a
    suppressed number found here is suppressed for every strategy after it."""
    db.flush()
    org_id = prop.organization_id
    ctl = C.controls(db, org_id)
    checks: List[Dict[str, Any]] = []
    checks.append(_check("EVOSENSE_PAUSED", not ctl.paused_all, "EvoSense is running",
                         "Paused by the kill switch" if ctl.paused_all else None))
    if channel == "sms":
        checks.append(_check("SMS_PAUSED", not ctl.paused_sms, "SMS outreach switched on",
                             "SMS is paused" if ctl.paused_sms else None))
    elif channel == "email":
        checks.append(_check("EMAIL_PAUSED", not ctl.paused_email, "Email switched on"))
    checks.append(_check("NO_CONTACT", cp is not None, "A contact point exists"))
    if cp is None:
        return _result(checks, prop)

    checks.append(_check("CONTACT_STATUS", cp.status == "active",
                         "Contact is usable",
                         None if cp.status == "active" else "%s — %s" % (
                             cp.status.replace("_", " ").upper(), cp.status_reason or "")))
    if cp.kind == "phone":
        usable = compliance_service.usable_us_phone(cp.value)
        checks.append(_check("NUMBER_UNUSABLE", usable is not None, "A usable US number"))
        suppressed = compliance_service.is_phone_suppressed(db, org_id, cp.value)
        checks.append(_check("SUPPRESSED", not suppressed, "Not on the suppression list",
                             "On this organization's suppression list" if suppressed else None))
        dnc = platform_dnc(db, org_id, cp.value)
        checks.append(_check("DNC", not dnc, "No platform DNC for this number",
                             "A lead with this number is marked DNC" if dnc else None))
        if mark and cp.status == "active" and (suppressed or dnc):
            cp.status = "suppressed"
            cp.status_reason = ("On the organization's suppression list" if suppressed
                                else "Platform DNC")
        if channel == "sms":
            checks.append(_check("LANDLINE", cp.line_type != "landline",
                                 "Can receive SMS", "Landline — SMS not possible"
                                 if cp.line_type == "landline" else None))
        checks.append(_check("INVALID", cp.validation != "invalid", "Passed validation"))
    elif channel == "sms":
        checks.append(_check("WRONG_CHANNEL", False, "A phone number for SMS"))

    from app.services.evosense import contacts as CT
    cc = CT.score_contact_point(db, prop, cp)["value"] or 0
    need = getattr(strategy, "min_contact_confidence", 60) if strategy else 60
    checks.append(_check("LOW_CONTACT_CONFIDENCE", cc >= need,
                         "Contact confidence %s (needs %s)" % (cc, need)))

    owner = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == cp.owner_id,
                                           EvoSenseOwner.organization_id == org_id).first()
    other = (db.query(EvoSenseEngagement)
             .filter(EvoSenseEngagement.organization_id == org_id,
                     EvoSenseEngagement.owner_id == cp.owner_id,
                     EvoSenseEngagement.status.in_(LIVE_ENGAGEMENT),
                     EvoSenseEngagement.id != (engagement_id or "-")).first())
    other_addr = None
    if other is not None:
        op = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == other.property_id).first()
        other_addr = op.street_address if op else None
    checks.append(_check("OWNER_ALREADY_IN_CONVERSATION", other is None,
                         "Owner not already being worked",
                         "Already in conversation about %s" % (other_addr or "another property")
                         if other is not None else None))
    cap_days = ctl.owner_touch_cap_days or 7
    recent = owner is not None and owner.last_touch_at and \
        owner.last_touch_at > C.now() - timedelta(days=cap_days)
    checks.append(_check("OWNER_CONTACTED_RECENTLY", not recent,
                         "Owner not contacted in the last %s days" % cap_days))
    checks.append(_check("ALREADY_A_DEAL", not prop.promoted_deal_id, "Not already a Wholesale deal"))
    if not (prop.is_test and cp.is_test):
        pol = ST.outreach_policy(strategy) if strategy else {}
        checks.append(_check("COMPLIANCE_NOT_CONFIRMED",
                             bool(pol.get("cold_outreach_compliance_confirmed")),
                             "Organization confirmed cold-outreach compliance for this strategy",
                             "Real cold SMS is refused until the strategy's outreach policy "
                             "records the organization's compliance confirmation."))
    return _result(checks, prop, cp)


LABELS = {
    "OWNER_ALREADY_IN_CONVERSATION": "OWNER ALREADY IN CONVERSATION",
    "OWNER_CONTACTED_RECENTLY": "OWNER CONTACTED RECENTLY",
}


def _result(checks, prop, cp=None) -> Dict[str, Any]:
    blocks = [c["code"] for c in checks if not c["ok"]]
    mode = "sandbox_simulated" if (prop.is_test and (cp is None or cp.is_test)) else "cadence"
    first = next((c for c in checks if not c["ok"]), None)
    return {"eligible": not blocks, "mode": mode, "blocks": blocks, "checks": checks,
            "primary_block": LABELS.get(blocks[0], blocks[0].replace("_", " ")) if blocks else None,
            "summary": ("Eligible — %s" % ("SANDBOX: simulated delivery, nothing is sent"
                                           if mode == "sandbox_simulated" else "platform cadence"))
            if not blocks else "BLOCKED — %s" % (first.get("detail") or first["label"])}
