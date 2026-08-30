"""Read-only forensics for one lead's outbound SMS. God-only.

Why this exists.

When a text does not arrive, the only place the reason lives is Twilio's
Message resource: the SID, the exact From and To the API was called with, the
final status, and the ErrorCode that explains it. Until now, answering "why
didn't this send?" meant opening the Twilio console and reading it by hand,
which is both slow and something a support conversation cannot do at all.

This endpoint asks the SAME Twilio account the message was sent from - resolved
through the ordinary credential ladder, never a platform fallback - for its own
record of a message we already have the SID for.

It is strictly read-only:
  * it makes no `messages.create` call and can never send anything
  * it changes no Twilio configuration, no number, no messaging service, no
    A2P registration - it performs a GET on a message resource and nothing else
  * the only rows it writes are backfills of `error_code`/`error_message`/
    `send_state` onto OUR message row, copying the provider's answer into the
    columns that should have captured it at receipt time
  * it never returns an auth token, and never logs one
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Lead, Message, User
from app.services.message_state import (
    describe as describe_delivery,
    normalize_provider_status,
)

router = APIRouter(prefix="/god/sms-trace", tags=["god-diagnostics"])

log = logging.getLogger(__name__)


def _mask(sid: Optional[str]) -> Optional[str]:
    """An account SID is an identifier, not a secret - but there is no reason
    to hand out the whole thing, so only the last four are ever returned."""
    s = str(sid or "")
    return ("…" + s[-4:]) if len(s) >= 4 else None


@router.get("/{lead_id}")
def trace_lead_sms(
    lead_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    ask_provider: bool = Query(
        default=True,
        description="Fetch each message's record from Twilio (read-only GET).",
    ),
    db: Session = Depends(get_db),
    _god: User = Depends(require_god),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "No such lead.")

    rows = (db.query(Message)
            .filter(Message.lead_id == lead_id)
            .order_by(Message.sent_at.desc())
            .limit(limit).all())

    # The advisor who actually sent each message is the one whose credentials
    # can read it back. A message sent by one advisor must not be looked up
    # with another's account - Twilio would simply 404 and we would report
    # "not found" for a message that exists.
    out = []
    for m in rows:
        entry = {
            "message_id": m.id,
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "body_preview": (m.body or "")[:160],
            "twilio_sid": m.twilio_sid,
            "stored": {
                "twilio_status": m.twilio_status,
                "delivery_status": m.delivery_status,
                "delivery_status_at": (m.delivery_status_at.isoformat()
                                       if m.delivery_status_at else None),
                "send_state": getattr(m, "send_state", None),
                "error_code": getattr(m, "error_code", None),
                "error_message": getattr(m, "error_message", None),
            },
            "delivery": describe_delivery(m),
            "provider": None,
        }

        # No SID means no provider request was ever made. There is nothing to
        # ask Twilio about, and saying so is the answer.
        if not m.twilio_sid:
            entry["provider"] = {
                "queried": False,
                "reason": "No provider SID on this row — the message was never "
                          "submitted to Twilio.",
            }
            out.append(entry)
            continue

        if not ask_provider:
            out.append(entry)
            continue

        sender = db.query(User).filter(User.id == m.sender_id).first()
        if sender is None:
            entry["provider"] = {"queried": False,
                                 "reason": "Sending user no longer exists."}
            out.append(entry)
            continue

        try:
            from app.services.sms_service import _resolve_twilio_creds
            client, _from, _cid = _resolve_twilio_creds(sender, db)
        except Exception as exc:                      # noqa: BLE001
            entry["provider"] = {"queried": False,
                                 "reason": f"Credentials unavailable: {exc}"}
            out.append(entry)
            continue

        try:
            rec = client.messages(m.twilio_sid).fetch()
        except Exception as exc:                      # noqa: BLE001
            entry["provider"] = {"queried": True, "ok": False,
                                 "error": str(exc)[:400]}
            out.append(entry)
            continue

        provider = {
            "queried": True,
            "ok": True,
            "sid": rec.sid,
            "account_sid_last4": _mask(getattr(rec, "account_sid", None)),
            "messaging_service_sid": getattr(rec, "messaging_service_sid", None),
            "from": getattr(rec, "from_", None),
            "to": getattr(rec, "to", None),
            "status": getattr(rec, "status", None),
            "error_code": (str(rec.error_code)
                           if getattr(rec, "error_code", None) else None),
            "error_message": getattr(rec, "error_message", None),
            "num_segments": getattr(rec, "num_segments", None),
            "direction": getattr(rec, "direction", None),
            "date_sent": (rec.date_sent.isoformat()
                          if getattr(rec, "date_sent", None) else None),
        }
        entry["provider"] = provider

        # Backfill what the receipt should have captured. This is the only
        # write here, it copies the provider's own answer onto our row, and it
        # never overwrites a code we already hold.
        changed = False
        if provider["error_code"] and not getattr(m, "error_code", None):
            m.error_code = provider["error_code"][:32]
            changed = True
        if provider["error_message"] and not getattr(m, "error_message", None):
            m.error_message = str(provider["error_message"])[:500]
            changed = True
        if provider["status"]:
            state = normalize_provider_status(provider["status"])
            if getattr(m, "send_state", None) != state:
                m.send_state = state
                changed = True
            if (m.delivery_status or "") != provider["status"]:
                m.delivery_status = provider["status"]
                m.twilio_status = provider["status"]
                changed = True
        if changed:
            entry["backfilled"] = True
        out.append(entry)

    db.commit()

    return {
        "lead": {
            "id": lead.id,
            "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            "phone": lead.phone,
            "status": lead.status,
            "is_duplicate": bool(lead.is_duplicate),
            "duplicate_reason": getattr(lead, "duplicate_reason", None),
            "duplicate_of_lead_id": getattr(lead, "duplicate_of_lead_id", None),
            "manual_flag": getattr(lead, "manual_flag", None),
            "assigned_to_id": lead.assigned_to_id,
            "organization_id": lead.organization_id,
        },
        "message_count": len(out),
        "messages": out,
    }


# ---------------------------------------------------------------------------
# WHAT THE CAMPAIGN WAS ACTUALLY APPROVED TO SEND
#
# `GET /10dlc/status` reports what OUR database holds, and for a campaign
# registered by hand in the Twilio console it holds nothing - every A2P column
# is null. That is a gap in our records, not evidence about the campaign.
#
# The registration itself is readable over the API: a Messaging Service carries
# its US A2P campaign, and that campaign carries the fields the carrier
# actually evaluates a message against - the use case, the sample messages
# submitted for approval, and the declared behaviour flags `has_embedded_links`
# and `has_embedded_phone`.
#
# `has_embedded_links` is the one that matters when messages containing a URL
# are filtered and an otherwise identical message without one is delivered. A
# campaign registered as not sending links, that then sends links, is a
# declaration mismatch - and it is invisible from our side until someone reads
# the registration.
#
# STRICTLY READ-ONLY. This performs GETs. It does not register, update or
# delete a brand, a campaign, a messaging service or a phone number, and it
# never returns an auth token.
# ---------------------------------------------------------------------------
@router.get("/campaign/{organization_id}")
def read_registered_campaign(
    organization_id: str,
    db: Session = Depends(get_db),
    _god: User = Depends(require_god),
):
    from app.models.models import Organization
    from app.services.sms_service import _resolve_twilio_creds

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise HTTPException(404, "No such organization.")

    # Credentials come from whoever in this org can actually send. That may be
    # the organization's own Twilio account or, for a bring-your-own advisor,
    # theirs - the ladder in sms_service decides, exactly as it does on a real
    # send, so this reads the same account the messages went out on.
    client = None
    used = None
    candidates = (db.query(User)
                  .filter(User.organization_id == organization_id)
                  .all())
    errors = []
    for who in candidates:
        try:
            client, _from, _cid = _resolve_twilio_creds(who, db)
            used = {"user_id": who.id, "from_number": _from}
            break
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"{who.id}: {str(exc)[:160]}")
            continue

    if client is None:
        return {"organization": org.name, "resolved": False,
                "reason": "No user in this organization resolves Twilio credentials.",
                "attempts": errors[:10]}

    out = []
    try:
        services = client.messaging.v1.services.list(limit=20)
    except Exception as exc:                              # noqa: BLE001
        return {"organization": org.name, "resolved": True, "credentials": used,
                "error": f"Could not list messaging services: {str(exc)[:300]}"}

    for svc in services:
        entry = {
            "messaging_service_sid": svc.sid,
            "friendly_name": svc.friendly_name,
            "use_inbound_webhook_on_number": getattr(
                svc, "use_inbound_webhook_on_number", None),
            "sender_pool": [],
            "campaigns": [],
        }

        try:
            for pn in client.messaging.v1.services(svc.sid).phone_numbers.list(limit=50):
                entry["sender_pool"].append(getattr(pn, "phone_number", None))
        except Exception as exc:                          # noqa: BLE001
            entry["sender_pool_error"] = str(exc)[:200]

        try:
            for c in client.messaging.v1.services(svc.sid).us_app_to_person.list(limit=10):
                entry["campaigns"].append({
                    "sid": c.sid,
                    "campaign_id": c.campaign_id,
                    "campaign_status": c.campaign_status,
                    "use_case": c.us_app_to_person_usecase,
                    "description": c.description,
                    # The approved wording, verbatim. This is what the failed
                    # message has to be compared against.
                    "message_samples": c.message_samples,
                    "message_flow": c.message_flow,
                    # The declared behaviour flags the carrier evaluates.
                    "has_embedded_links": c.has_embedded_links,
                    "has_embedded_phone": c.has_embedded_phone,
                    "subscriber_opt_in": c.subscriber_opt_in,
                    "age_gated": c.age_gated,
                    "direct_lending": c.direct_lending,
                    "opt_in_message": c.opt_in_message,
                    "opt_out_message": c.opt_out_message,
                    "help_message": c.help_message,
                    "opt_out_keywords": c.opt_out_keywords,
                    "rate_limits": c.rate_limits,
                    "is_externally_registered": c.is_externally_registered,
                    "errors": c.errors,
                })
        except Exception as exc:                          # noqa: BLE001
            entry["campaign_error"] = str(exc)[:300]

        out.append(entry)

    return {
        "organization": org.name,
        "resolved": True,
        "credentials": used,
        # What our own database claims, side by side with what Twilio holds.
        # A divergence here is a records gap on our side, never a statement
        # about the campaign's real status.
        "our_stored_a2p": {
            "brand_sid": getattr(org, "twilio_a2p_brand_sid", None),
            "campaign_sid": getattr(org, "twilio_a2p_campaign_sid", None),
            "campaign_status": getattr(org, "twilio_a2p_campaign_status", None),
            "campaign_use_case": getattr(org, "twilio_a2p_campaign_use_case", None),
            "messaging_service_sid": getattr(org, "twilio_messaging_service_sid", None),
        },
        "messaging_services": out,
    }


# ---------------------------------------------------------------------------
# WHY A REPLY NEVER ARRIVED. READ-ONLY.
#
# Outbound delivery is proven. The inbound leg has three segments and our
# database can only see the last one:
#
#     phone -> Twilio        (did the carrier hand Twilio the message?)
#     Twilio -> our webhook  (is a webhook URL even configured on the number?)
#     webhook -> EvoSys      (did guard_inbound accept it?)
#
# When no Reply row exists, all three look identical from inside the product,
# and the honest answer is "I cannot tell" - which is why this exists. It reads
# Twilio's own inbound message log and the IncomingPhoneNumber resource, so the
# broken segment is identified rather than guessed at.
#
# Two details that decide the answer and are easy to miss:
#   * `sms_application_sid`, when set, OVERRIDES `sms_url` entirely. A number
#     can show a perfectly correct sms_url and still route somewhere else.
#   * A messaging service with `use_inbound_webhook_on_number: true` - which is
#     how MG37d057... is configured - means the NUMBER's webhook governs, not
#     the service's. So the number-level fields below are the ones that matter.
#
# STRICTLY READ-ONLY. It lists and fetches. It cannot send a message, and it
# cannot modify a number, a webhook, a messaging service or a campaign.
# ---------------------------------------------------------------------------
@router.get("/inbound/{organization_id}")
def trace_inbound(
    organization_id: str,
    to_number: str = Query(..., description="The Twilio number that should have received it."),
    from_number: Optional[str] = Query(None, description="The handset that replied."),
    hours: int = Query(default=12, ge=1, le=168),
    db: Session = Depends(get_db),
    _god: User = Depends(require_god),
):
    import datetime as _dt

    from app.models.models import Organization
    from app.services.sms_service import _resolve_twilio_creds

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if not org:
        raise HTTPException(404, "No such organization.")

    client = None
    attempts = []
    for who in db.query(User).filter(User.organization_id == organization_id).all():
        try:
            client, _from, _cid = _resolve_twilio_creds(who, db)
            break
        except Exception as exc:                            # noqa: BLE001
            attempts.append(f"{who.id}: {str(exc)[:120]}")
            continue
    if client is None:
        return {"resolved": False,
                "reason": "No user in this organization resolves Twilio credentials.",
                "attempts": attempts[:10]}

    since = _dt.datetime.utcnow() - _dt.timedelta(hours=hours)

    # ── 1. Did Twilio receive anything inbound on this number? ──────────────
    inbound = {"queried": True, "messages": []}
    try:
        kwargs = {"to": to_number, "date_sent_after": since, "limit": 50}
        if from_number:
            kwargs["from_"] = from_number
        for m in client.messages.list(**kwargs):
            inbound["messages"].append({
                "sid": m.sid,
                "direction": m.direction,
                "from": m.from_,
                "to": m.to,
                "body": m.body,
                "status": m.status,
                "error_code": str(m.error_code) if m.error_code else None,
                "date_sent": m.date_sent.isoformat() if m.date_sent else None,
            })
    except Exception as exc:                                # noqa: BLE001
        inbound = {"queried": True, "error": str(exc)[:300], "messages": []}

    inbound["inbound_count"] = sum(
        1 for m in inbound["messages"] if (m.get("direction") or "").startswith("inbound")
    )

    # ── 2. What is actually configured on the number? ───────────────────────
    number = {"found": False}
    try:
        matches = client.incoming_phone_numbers.list(phone_number=to_number, limit=5)
        if matches:
            n = matches[0]
            number = {
                "found": True,
                "sid": n.sid,
                "phone_number": n.phone_number,
                "friendly_name": n.friendly_name,
                "sms_url": n.sms_url,
                "sms_method": n.sms_method,
                "sms_fallback_url": n.sms_fallback_url,
                "sms_fallback_method": n.sms_fallback_method,
                # If this is set, it WINS over sms_url. A correct-looking
                # sms_url beside a populated application sid is a trap.
                "sms_application_sid": n.sms_application_sid,
                "status_callback": n.status_callback,
            }
    except Exception as exc:                                # noqa: BLE001
        number = {"found": False, "error": str(exc)[:300]}

    expected = "https://advisorflow-backend.onrender.com/sms/webhook/inbound"
    configured = (number.get("sms_url") or "").strip()
    number["expected_sms_url"] = expected
    number["matches_expected"] = (configured == expected)
    number["overridden_by_application_sid"] = bool(number.get("sms_application_sid"))

    # ── Which messaging services hold this number, and how they route ───────
    services = []
    try:
        for svc in client.messaging.v1.services.list(limit=20):
            try:
                nums = client.messaging.v1.services(svc.sid).phone_numbers.list(limit=50)
            except Exception:                               # noqa: BLE001
                continue
            if any(getattr(pn, "phone_number", None) == to_number for pn in nums):
                services.append({
                    "sid": svc.sid,
                    "friendly_name": svc.friendly_name,
                    "inbound_request_url": getattr(svc, "inbound_request_url", None),
                    "inbound_method": getattr(svc, "inbound_method", None),
                    "fallback_url": getattr(svc, "fallback_url", None),
                    # True means the NUMBER's webhook governs inbound, not the
                    # service's - so `number.sms_url` above is the live setting.
                    "use_inbound_webhook_on_number": getattr(
                        svc, "use_inbound_webhook_on_number", None),
                })
    except Exception as exc:                                # noqa: BLE001
        services = [{"error": str(exc)[:200]}]

    return {
        "organization": org.name,
        "window_hours": hours,
        "twilio_inbound": inbound,
        "number": number,
        "messaging_services_holding_this_number": services,
    }
