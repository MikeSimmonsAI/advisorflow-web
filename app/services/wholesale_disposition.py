"""Sending a deal sheet to a cash buyer — the real provider call, and its guards.

WHAT PHASE 1 DID AND WHY IT WAS NOT ENOUGH
-------------------------------------------
Phase 1 composed the deal sheet, refused the buyers it may not contact, and
wrote a `wholesale_buyer_outreach` row per buyer. What it did not do was hand
anything to a provider — so the screen said "prepared" and the buyer's inbox
said nothing, which is the shape of failure this codebase's own
`outbound_email_gate` docstring was written about ("the database has been
recording outbound activity that never happened").

This module closes that gap, and it does so through the platform's existing
machinery rather than beside it:

    outbound_email_gate.gate_transactional_email   the deployment switch
    demo_guard.block_if_demo                       the demonstration boundary
    email_service.send_email                       the brand-resolved provider call
    sms_service._resolve_twilio_creds              the org's own Twilio identity

There is no second credential resolver, no second suppression concept and no
second provider client anywhere in this file.

SIX REFUSALS, EACH BEFORE THE PROVIDER IS TOUCHED
--------------------------------------------------
In order, because the cheap and the categorical ones come first:

    1. sandbox     the deal or the buyer is a test record. Rehearsal must never
                   make a real inbox or phone light up.
    2. opted out   the buyer asked not to receive deals, or is inactive.
    3. no address  nothing to send to on the chosen channel.
    4. already sent a row that succeeded is not re-sent unless a person
                   explicitly retries it. A double-click must not mail twice.
    5. demo tenant `demo_guard`, the same call `sms_service.send_sms` makes.
    6. switch off  the deployment has not enabled this outbound path. The
                   refusal NAMES THE ENVIRONMENT VARIABLE, because an operator
                   reading a blocked row should not have to go and find out
                   which one governs it.

Only then does a provider get called, and whatever it answers — success or
failure — is written to the row verbatim. Nothing in this module can report a
send that did not happen.

NOTHING IS SENT AUTOMATICALLY. Every function here is reached from an explicit
human action on the deal room. There is no cron, no loop and no automation
hook, by design: "do not automatically blast every matched buyer" is a
requirement, and the simplest way to keep it is to have nowhere for an
automation to call in.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.models.wholesale_models import (
    ACTOR_USER, WholesaleBuyer, WholesaleBuyerOutreach, WholesaleDeal,
    WholesaleProperty,
)

log = logging.getLogger(__name__)

# ── The SMS switch ──────────────────────────────────────────────────────────
#
# Email goes through the platform's `outbound_email_gate`, which is where every
# gated email source is declared. SMS has no equivalent registry — the SMS paths
# that exist are already live and are governed per-organization by Twilio
# credentials — so this module declares its own switch, in the same shape and
# with the same default: OFF.
#
# Why a switch at all, when the org's own Twilio credentials already gate it:
# because a wholesaler's Twilio number is provisioned for seller conversations,
# and texting a list of cash buyers from it is a different use with a different
# carrier profile. That is a decision an operator should make once, deliberately,
# rather than discover.
SMS_ENV = "OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION"

_TRUE = ("1", "true", "yes", "on")


def sms_enabled() -> bool:
    return (os.environ.get(SMS_ENV) or "").strip().lower() in _TRUE


def channel_status() -> List[Dict[str, Any]]:
    """What the Settings screen shows: can this deployment actually send?

    Computed from the environment every time rather than stored, so it cannot
    go stale, and it names the variable so the answer is actionable.
    """
    from app.services import outbound_email_gate as gate

    email_on = gate.source_enabled(gate.WHOLESALE_BUYER_DISPOSITION)
    return [
        {"channel": "email",
         "enabled": email_on,
         "env": "OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION",
         "status": "ready" if email_on else "not_enabled",
         # `detail` is read by an acquisitions person on the deal screen.
         # `technical` is read by whoever runs the deployment, on Settings.
         # Same fact, two audiences — see this module's Phase 5 note.
         "detail": ("Buyer deal sheets can be emailed." if email_on else
                    "Email to buyers is turned off for this workspace. The "
                    "deal sheet is still written and saved — it just is not "
                    "sent. An administrator can turn it on."),
         "technical": (None if email_on else
                       "Set OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION=true "
                       "in the server environment.")},
        {"channel": "sms",
         "enabled": sms_enabled(),
         "env": SMS_ENV,
         "status": "ready" if sms_enabled() else "not_enabled",
         "detail": ("Buyer deal sheets can be texted from this organization's "
                    "own Twilio number." if sms_enabled() else
                    "Text messages to buyers are turned off for this "
                    "workspace. An administrator can turn them on."),
         "technical": (None if sms_enabled() else
                       "Set %s=true in the server environment." % SMS_ENV)},
    ]


class SendRefused(Exception):
    """A refusal, not a fault. Carries the reason a person needs to read."""

    def __init__(self, reason: str, code: str = "refused"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _org(db, org_id: str):
    from app.models.models import Organization
    return db.query(Organization).filter(Organization.id == org_id).first()


def preflight(db, org_id: str, deal: WholesaleDeal, buyer: WholesaleBuyer,
              row: Optional[WholesaleBuyerOutreach], channel: str,
              force_resend: bool = False) -> None:
    """Every refusal, in order. Raises SendRefused, or returns having done nothing.

    Separated from `send_to_buyer` so the preview endpoint can ask the same
    question without sending, and so a test can assert each refusal
    individually. There is no way to reach the provider that does not pass
    through here.
    """
    # 1. SANDBOX. First, because it is the one a person is most likely to be
    #    relying on while they learn the product.
    if getattr(deal, "is_test", False) or getattr(buyer, "is_test", False):
        raise SendRefused(
            "This is a sandbox record, so nothing was sent. Rehearse the whole "
            "workflow on it — matching, the preview, the response tracking — "
            "and do the real send from a live deal.", "sandbox")

    # 2. OPTED OUT.
    if getattr(buyer, "do_not_contact", False):
        why = getattr(buyer, "do_not_contact_reason", None)
        raise SendRefused(
            "This buyer asked not to receive deals%s." % ((" — " + why) if why else ""),
            "opted_out")
    if not getattr(buyer, "is_active", True):
        raise SendRefused("This buyer is marked inactive.", "inactive")

    # 3. SOMEWHERE TO SEND IT.
    if channel == "email" and not getattr(buyer, "email", None):
        raise SendRefused("No email address on file for this buyer.", "no_address")
    if channel == "sms" and not getattr(buyer, "phone", None):
        raise SendRefused("No phone number on file for this buyer.", "no_address")

    # 4. ALREADY SENT. The retry guard, and the reason a double-click is safe.
    if row is not None and not force_resend:
        if getattr(row, "sent_at", None) and getattr(row, "status", None) not in (
                "queued", "failed"):
            raise SendRefused(
                "This buyer was already sent this deal on %s. Use Resend if you "
                "meant to send it again."
                % row.sent_at.strftime("%d %b %Y at %H:%M"), "already_sent")

    # 5. DEMONSTRATION TENANT. The same guard sms_service makes, on the same
    #    object: the organization that owns the record being contacted.
    from app.services import demo_guard
    demo_guard.block_if_demo(_org(db, org_id), channel.upper())

    # 6. THE DEPLOYMENT SWITCH.
    if channel == "email":
        from app.services import outbound_email_gate as gate
        try:
            gate.gate_transactional_email(
                buyer.email, purpose="wholesale buyer deal sheet",
                source=gate.WHOLESALE_BUYER_DISPOSITION)
        except gate.EmailSendDisabled as exc:
            raise SendRefused(str(exc), "not_enabled")
    elif channel == "sms":
        if not sms_enabled():
            raise SendRefused(
                "Text messages to buyers are turned off for this workspace ""(%s). "
                "The deal sheet was composed and recorded; nothing was sent."
                % SMS_ENV, "not_enabled")
    else:
        raise SendRefused("Only email and SMS can be sent from here.", "bad_channel")


def send_to_buyer(db, org_id: str, deal: WholesaleDeal, buyer: WholesaleBuyer,
                  row: WholesaleBuyerOutreach, user, *,
                  force_resend: bool = False) -> Dict[str, Any]:
    """Actually send one deal sheet, and record exactly what happened.

    Returns {"sent", "status", "reason", "provider_message_id"}. It does not
    raise for a refusal or a provider failure — both are outcomes that belong on
    the row and in front of the person who pressed the button, not exceptions
    that lose the other nine buyers in the batch.
    """
    channel = row.channel or "email"
    row.attempts = (row.attempts or 0) + 1
    row.last_attempt_at = datetime.utcnow()

    try:
        preflight(db, org_id, deal, buyer, row, channel, force_resend=force_resend)
    except SendRefused as exc:
        row.status = "failed" if exc.code not in ("already_sent",) else row.status
        row.blocked_reason = exc.reason
        return {"sent": False, "status": row.status, "reason": exc.reason,
                "code": exc.code, "provider_message_id": None}
    except Exception as exc:                                    # noqa: BLE001
        # demo_guard raises a RuntimeError, deliberately not an HTTPException.
        row.status = "failed"
        row.blocked_reason = str(exc)[:400]
        return {"sent": False, "status": "failed", "reason": str(exc)[:400],
                "code": "blocked", "provider_message_id": None}

    if channel == "email":
        result = _send_email(db, org_id, buyer, row)
    else:
        result = _send_sms(db, org_id, buyer, row, user)

    row.provider_result = json.dumps(result, default=str)[:4000]
    if result.get("success"):
        row.status = "sent"
        row.sent_at = datetime.utcnow()
        row.provider_message_id = result.get("provider_message_id")
        row.provider_error = None
        row.blocked_reason = None
        return {"sent": True, "status": "sent", "reason": None, "code": "sent",
                "provider_message_id": row.provider_message_id}

    # A PROVIDER FAILURE IS A FAILURE. It is never rounded up to success, and
    # the row keeps the provider's own words so a retry can be judged.
    row.status = "failed"
    row.provider_error = str(result.get("error") or "the provider did not say")[:400]
    row.blocked_reason = "Send failed: %s" % row.provider_error
    return {"sent": False, "status": "failed", "reason": row.blocked_reason,
            "code": "provider_error", "provider_message_id": None}


def _send_email(db, org_id: str, buyer: WholesaleBuyer,
                row: WholesaleBuyerOutreach) -> Dict[str, Any]:
    """The brand-resolved transactional send the platform already has.

    `email_service.send_email` is the generic non-lead sender — its own
    docstring says "used by proposal_router and other non-lead-outreach
    surfaces" — and it looks the organization up so the mail leaves on the
    correct brand's verified domain rather than a shared default.
    """
    from app.services import email_service
    try:
        return email_service.send_email(
            db=db, org_id=org_id,
            to_email=buyer.email,
            to_name=(buyer.company_name or buyer.contact_name or ""),
            subject=row.subject or "Off-market opportunity",
            body=row.body or "")
    except Exception as exc:                                    # noqa: BLE001
        log.warning("wholesale buyer email failed: %s", exc)
        return {"success": False, "provider_message_id": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def _send_sms(db, org_id: str, buyer: WholesaleBuyer,
              row: WholesaleBuyerOutreach, user) -> Dict[str, Any]:
    """Text a buyer from the ORGANIZATION'S OWN Twilio identity.

    `sms_service.send_sms` cannot be used here and the reason is structural
    rather than stylistic: it takes a `Lead`, checks a family's DNC status,
    consults the suppression list and writes a `messages` row — four things that
    are about a customer's prospect and none of which describe a cash buyer. A
    counterparty run through a family's consent machinery is the category error
    `outbound_email_gate.gate_staff_email` explicitly refuses to make.

    So this reuses the part that IS shared — `_resolve_twilio_creds`, the
    organization's credentials and sending number — and nothing else. There is
    no second credential resolver here, and the buyer's own opt-out was checked
    in `preflight` before this function was reached.
    """
    try:
        from app.services.sms_service import _resolve_twilio_creds
        client, from_number, _messaging_sid = _resolve_twilio_creds(user, db)
        body = row.body or ""
        if row.subject:
            body = "%s\n\n%s" % (row.subject, body)
        message = client.messages.create(
            body=body[:1500], from_=from_number, to=buyer.phone)
        return {"success": True,
                "provider_message_id": getattr(message, "sid", None),
                "status": getattr(message, "status", None)}
    except Exception as exc:                                    # noqa: BLE001
        log.warning("wholesale buyer SMS failed: %s", exc)
        return {"success": False, "provider_message_id": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


# ── What a buyer may see ────────────────────────────────────────────────────
#
# THE PRIVACY GUARANTEE, STATED AS A LIST AND ENFORCED BY A TEST.
#
# `wholesale_service.build_buyer_message` composes from the property and the
# numbers and never reads a seller field. This constant is the same promise
# written down where a reviewer will find it, and
# `test_wholesale_disposition.py` asserts that none of these ever appears in a
# composed or stored buyer message.
SELLER_FIELDS_NEVER_SENT = (
    "seller name", "seller phone", "seller email", "seller mailing address",
    "motivation", "reason for selling", "asking price", "mortgage note",
    "decision makers", "qualification score", "qualification reasons",
    "AI summary", "seller conversation history", "internal notes",
)


def seller_leak_check(body: str, seller_values: List[Optional[str]]) -> List[str]:
    """Which seller values, if any, appear in this buyer-facing text.

    Used by the tests and by the preview endpoint. Returns the offending values
    so a failure names what leaked rather than just asserting a boolean — the
    difference between a test that tells you and one that only tells you off.
    """
    found = []
    haystack = (body or "").lower()
    for value in seller_values:
        text = (str(value) if value is not None else "").strip()
        # Two characters or fewer would match by accident; a real name, phone or
        # address is longer than that.
        if len(text) > 3 and text.lower() in haystack:
            found.append(text)
    return found
