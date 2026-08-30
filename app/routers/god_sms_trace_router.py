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
