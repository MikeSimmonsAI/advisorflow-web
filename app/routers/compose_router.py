"""What the composer is allowed to do with one lead, answered before Send.

Three live defects share one cause - the page guessed at things only the
backend knows, and the advisor found out by pressing a button:

  * "Lead has no email address" appeared when choosing SMS for a lead that has
    a phone and no email. Missing email is a reason not to offer EMAIL. It was
    never a reason to refuse a text message.

  * "Include booking link" was checked and the message preview showed no link,
    because the link was substituted at send time - and only if the template
    happened to contain a {booking_link} placeholder, which a hand-typed or
    AI-drafted message never does.

  * "No Twilio credentials configured for advisor ... or their organization"
    arrived as a failed send rather than as a disabled button with a reason.

So this endpoint reports capability, sender readiness and the exact resolved
booking URL together, in one read, using the SAME functions the send path uses.
Nothing here sends anything, and nothing here is a second opinion: channel
rules come from the lead's own fields, the sender from
`sms_service.describe_sms_sender`, voice from `voice_orchestrator`, and the URL
from `public_identity.booking_url`.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_user
from app.models.models import Lead, User

router = APIRouter(prefix="/compose", tags=["compose"])

log = logging.getLogger(__name__)


def _fmt_phone(value: Optional[str]) -> Optional[str]:
    """+14695537417 -> +1 (469) 553-7417. Display only; storage is untouched."""
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        return "+1 (%s) %s-%s" % (digits[1:4], digits[4:7], digits[7:])
    if len(digits) == 10:
        return "(%s) %s-%s" % (digits[0:3], digits[3:6], digits[6:])
    return str(value)


def _lead_or_404(db: Session, lead_id: str, user: User) -> Lead:
    lead = (db.query(Lead)
            .filter(Lead.id == lead_id,
                    Lead.organization_id == user.organization_id)
            .first())
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/{lead_id}/context")
def compose_context(lead_id: str,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    lead = _lead_or_404(db, lead_id, user)

    has_phone = bool((lead.phone or "").strip())
    has_email = bool((getattr(lead, "email", None) or "").strip())
    is_dnc = (lead.status or "").lower() == "dnc"

    # ── the sender ───────────────────────────────────────────────────────────
    from app.services.sms_service import describe_sms_sender
    try:
        sender = describe_sms_sender(user, db)
    except Exception:
        log.exception("compose: could not describe SMS sender for user %s", user.id)
        sender = {"ready": False, "source": None, "from_number": None,
                  "account_sid_last4": None,
                  "reason": "The messaging sender could not be read."}

    # ── voice ────────────────────────────────────────────────────────────────
    voice_ready, voice_reason = False, "Voice is not configured."
    if has_phone:
        try:
            from app.services.voice_orchestrator import check_call_eligibility
            elig = check_call_eligibility(db, lead, user.organization_id)
            voice_ready, voice_reason = bool(elig.ok), elig.reason
        except Exception as e:
            log.exception("compose: voice eligibility failed for lead %s", lead_id)
            voice_reason = "Voice configuration could not be read: %s" % e
    else:
        voice_reason = "This lead has no phone number."

    # ── the channel matrix ───────────────────────────────────────────────────
    #
    # One rule per channel, each depending only on what that channel needs.
    # Email's absence is invisible to SMS and to voice, which is the whole fix.
    def cap(available: bool, reason: Optional[str]) -> dict:
        return {"available": bool(available), "reason": (None if available else reason)}

    if is_dnc:
        blocked = "This lead is marked do-not-contact."
        channels = {
            "sms": cap(False, blocked),
            "email": cap(False, blocked),
            "voice": cap(False, blocked),
            "both": cap(False, blocked),
        }
    else:
        sms_ok = has_phone and bool(sender.get("ready"))
        sms_reason = ("This lead has no phone number." if not has_phone
                      else sender.get("reason"))
        email_ok = has_email
        channels = {
            "sms": cap(sms_ok, sms_reason),
            "email": cap(email_ok, "This lead has no email address."),
            "voice": cap(voice_ready, voice_reason),
            "both": cap(sms_ok and email_ok,
                        "Sending on both channels needs a phone number with a "
                        "configured sender and an email address."),
        }

    # ── the booking link, exactly as it will be sent ─────────────────────────
    booking = {"url": None, "booking_link_id": None, "reason": None}
    try:
        from app.services.sms_service import get_or_create_booking_link
        from app.services.public_identity import booking_url as public_booking_url
        link = get_or_create_booking_link(db, lead, user)
        url = public_booking_url(db, lead.organization_id, link.token)
        booking = {
            "url": url or None,
            "booking_link_id": link.id,
            "reason": (None if url else
                       "No branded public address is configured for this "
                       "organization, so a booking link cannot be built."),
        }
    except Exception as e:
        log.exception("compose: could not build booking link for lead %s", lead_id)
        booking["reason"] = "The booking link could not be built: %s" % e

    return {
        "lead": {
            "id": lead.id,
            "first_name": lead.first_name,
            "phone": lead.phone,
            "phone_display": _fmt_phone(lead.phone),
            "email": getattr(lead, "email", None),
            "has_phone": has_phone,
            "has_email": has_email,
            "is_dnc": is_dnc,
        },
        "channels": channels,
        "sms_sender": sender,
        "booking": booking,
    }


class PreviewRequest(BaseModel):
    template: str = ""
    include_booking_link: bool = True


@router.post("/{lead_id}/preview")
def compose_preview(lead_id: str, req: PreviewRequest,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    """The EXACT text that pressing Send would transmit. Sends nothing.

    Calls `sms_service.compose_body` with the same arguments the send path
    uses, against the same booking link the send path would reuse. If this and
    the delivered message ever differ, one of them is a bug - there is no third
    place where the body is assembled.
    """
    lead = _lead_or_404(db, lead_id, user)

    from app.services.sms_service import compose_body, get_or_create_booking_link
    from app.services.public_identity import booking_url as public_booking_url

    url = ""
    link_id = None
    if req.include_booking_link:
        link = get_or_create_booking_link(db, lead, user)
        link_id = link.id
        url = public_booking_url(db, lead.organization_id, link.token) or ""

    body = compose_body(req.template or "", lead, user, url)
    # GSM-7 vs UCS-2 changes the segment size; the emoji case is the one that
    # surprises people, so it is worth being honest about here.
    unicode_body = any(ord(c) > 127 for c in body)
    per_segment = 70 if unicode_body else 160
    segments = 1 if len(body) <= per_segment else -(-len(body) // (67 if unicode_body else 153))

    return {
        "body": body,
        "booking_url": url or None,
        "booking_link_id": link_id,
        "characters": len(body),
        "segments": segments,
        "unicode": unicode_body,
    }
