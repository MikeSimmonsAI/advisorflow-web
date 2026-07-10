"""
SMS Service - Twilio integration
Each advisor (User) has their own Twilio Account SID, Auth Token, and
phone number stored on their profile. This service sends FROM that
advisor's number, optionally with a Caller ID Name configured
(e.g. "Restland Cemetery" instead of just digits showing up).

Per Mike's walkthrough:
  - Lead receives SMS from advisor's Twilio number (with caller ID name if set)
  - Message includes a booking link (stateless token, matches the existing
    advisorflow-booking.vercel.app pattern)
  - Lead can reply to that same Twilio number -> captured as a Reply
  - Message can also mention the advisor's personal cell as an alternate
    contact method, written directly into the template text
"""

import os
from datetime import datetime
from twilio.rest import Client
from sqlalchemy.orm import Session
from app.models.models import User, Lead, Message, BookingLink
from app.utils.crypto import decrypt_value

BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", "https://advisorflow-booking.vercel.app")


def get_twilio_client(advisor: User) -> Client:
    if not advisor.twilio_account_sid or not advisor.twilio_auth_token_encrypted:
        raise ValueError(f"Advisor {advisor.full_name} has no Twilio credentials configured.")
    auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
    return Client(advisor.twilio_account_sid, auth_token)


BOOKING_SECRET = os.environ.get("BOOKING_SECRET", "advisorflow2026restland")


def _encode_booking_token(lead: Lead, advisor: User) -> str:
    """
    Generate a base64 self-contained token compatible with the Vercel booking app.
    Format: base64(json({lead, appt_type, expires_at}))~sha256sig
    """
    import base64
    import hashlib
    import json as _json
    from datetime import timedelta

    expires = (datetime.utcnow() + timedelta(days=14)).isoformat()
    data = {
        "lead": {
            "First Name": lead.first_name or "",
            "Last Name": lead.last_name or "",
            "Phone": lead.phone or "",
        },
        "appt_type": "file_review",
        "expires": expires,
    }
    payload = base64.urlsafe_b64encode(_json.dumps(data).encode()).decode().rstrip("=")
    sig = hashlib.sha256(f"{BOOKING_SECRET}:{payload}".encode()).hexdigest()[:16]
    return f"{payload}~{sig}"


def create_booking_link(db: Session, lead: Lead, advisor: User) -> BookingLink:
    token = _encode_booking_token(lead, advisor)
    booking = BookingLink(lead_id=lead.id, user_id=advisor.id, status="pending", token=token)
    db.add(booking)
    db.commit()
    return booking


def render_template(template: str, lead: Lead, advisor: User, booking_url: str) -> str:
    """Simple variable substitution for message templates."""
    return (
        template
        .replace("{first_name}", lead.first_name or "there")
        .replace("{advisor_name}", advisor.full_name)
        .replace("{booking_link}", booking_url)
        .replace("{advisor_cell}", advisor.twilio_phone_number or "")
    )


def send_sms(
    db: Session,
    advisor: User,
    lead: Lead,
    template: str,
    include_booking_link: bool = True,
) -> Message:
    """
    Sends a single SMS from advisor -> lead.
    Caller ID name (if configured on the advisor's Twilio number) is set
    at the Twilio phone number / messaging service level, not per-message -
    that's configured once via configure_caller_id_name() below.
    """
    if lead.status == "dnc":
        raise ValueError(f"Lead {lead.id} is marked DNC (likely a duplicate) - blocked from sending.")

    # Independent suppression-list check, not a substitute for the
    # Lead.status check above but an additional, direct guard. REAL GAP
    # THIS CLOSES: a number could exist in the Compliance Center's
    # suppression list while its matching Lead.status was never updated
    # to DNC (confirmed via testing - this was especially likely before
    # the phone-format bug in compliance_router.py was also fixed,
    # since the two systems' normalized phone formats didn't even match
    # each other). Every real send path must check this directly.
    from app.services.compliance_service import is_phone_suppressed
    if is_phone_suppressed(db, lead.organization_id, lead.phone):
        raise ValueError(f"Lead {lead.id}'s phone number is on the suppression list - blocked from sending.")

    booking_url = ""
    booking_link = None
    if include_booking_link:
        booking_link = create_booking_link(db, lead, advisor)
        booking_url = f"{BOOKING_BASE_URL}/book/{booking_link.token}"

    body = render_template(template, lead, advisor, booking_url)

    client = get_twilio_client(advisor)
    twilio_msg = client.messages.create(
        body=body,
        from_=advisor.twilio_phone_number,
        to=lead.phone,
    )


def send_mms(
    db: Session,
    advisor: User,
    lead: Lead,
    template: str,
    media_url: str,
    include_booking_link: bool = False,
) -> Message:
    """
    Sends an MMS (text + image/flyer) from advisor -> lead.
    media_url must be a publicly accessible URL (e.g. uploaded to S3 or Cloudinary).
    Twilio A2P 10DLC approval is required for MMS just like SMS.
    """
    if lead.status == "dnc":
        raise ValueError(f"Lead {lead.id} is marked DNC - blocked from sending.")

    from app.services.compliance_service import is_phone_suppressed
    if is_phone_suppressed(db, lead.organization_id, lead.phone):
        raise ValueError(f"Lead {lead.id}'s phone is on the suppression list - blocked.")

    booking_url = ""
    booking_link = None
    if include_booking_link:
        booking_link = create_booking_link(db, lead, advisor)
        booking_url = f"{BOOKING_BASE_URL}/book/{booking_link.token}"

    body = render_template(template, lead, advisor, booking_url)

    client = get_twilio_client(advisor)
    twilio_msg = client.messages.create(
        body=body,
        from_=advisor.twilio_phone_number,
        to=lead.phone,
        media_url=[media_url],
    )

    message = Message(
        lead_id=lead.id,
        sender_id=advisor.id,
        body=f"[MMS] {body}",
        twilio_sid=twilio_msg.sid,
        twilio_status=twilio_msg.status,
        booking_link_id=booking_link.id if booking_link else None,
    )
    db.add(message)
    lead.status = "sent"
    db.commit()
    return message

    message = Message(
        lead_id=lead.id,
        sender_id=advisor.id,
        body=body,
        twilio_sid=twilio_msg.sid,
        twilio_status=twilio_msg.status,
        booking_link_id=booking_link.id if booking_link else None,
    )
    db.add(message)

    lead.status = "sent"
    db.commit()
    return message


def configure_caller_id_name(advisor: User) -> None:
    """
    Sets the Caller ID Name (a.k.a. CNAM) on the advisor's Twilio number.
    Not all carriers display this, but most major US carriers do.
    Must be called once per number, not per message.
    """
    if not advisor.twilio_caller_id_name:
        return
    client = get_twilio_client(advisor)
    # Twilio CNAM registration is account-level via Messaging Service or
    # via the Trust Hub for A2P 10DLC - actual API call depends on which
    # Twilio product is in use. Placeholder for the real call:
    #
    # client.trusthub.v1.customer_profiles... (A2P 10DLC brand/campaign)
    #
    # For now we store the desired name on the User record and surface it
    # in the dashboard so Mike can complete this in the Twilio console,
    # since CNAM setup typically requires identity verification.
    pass


def send_batch(
    db: Session,
    advisor: User,
    leads: list[Lead],
    template: str,
    include_booking_link: bool = True,
) -> dict:
    """Sends to multiple leads, skipping any that are DNC/duplicate."""
    sent = []
    skipped = []
    for lead in leads:
        if lead.is_duplicate or lead.status == "dnc":
            skipped.append(lead.id)
            continue
        try:
            msg = send_sms(db, advisor, lead, template, include_booking_link)
            sent.append(msg.id)
        except Exception as e:
            skipped.append(lead.id)
    return {"sent_count": len(sent), "skipped_count": len(skipped), "sent_ids": sent, "skipped_ids": skipped}
