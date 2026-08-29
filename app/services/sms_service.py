"""
SMS Service - Twilio integration

Credential resolution order (per send):
  1. Advisor's own Twilio account SID + auth token + phone number (personal number)
  2. Org-level shared Twilio credentials (toll-free or 10DLC fallback)
     → all advisors in the org send FROM the shared number; advisor name appears
       in the message body so the lead knows who's reaching out.

This makes it trivial to:
  - Run a single shared toll-free number during demos / early launch
  - Move to per-advisor 10DLC numbers later without changing any send logic
  - Support large enterprise clients (e.g. SCI) with one 10DLC brand/campaign
    covering hundreds of advisors, each getting their own local number only when
    that scale makes sense

Number type (twilio_number_type / org_twilio_number_type):
  "toll_free"  → (8XX) numbers, TFV approval required (already done for 844-917-2171)
  "10dlc"      → local 10-digit numbers, A2P brand+campaign registration required
  "short_code" → 5-6 digit shared/dedicated short codes (high-volume only)
  All three are just phone numbers from Twilio's API perspective — the type field
  is informational only and used in dashboards/reporting, not in send logic.
"""

import os
from datetime import datetime
from twilio.rest import Client
from sqlalchemy.orm import Session
from app.models.models import User, Lead, Message, BookingLink, Organization
from app.utils.crypto import decrypt_value
from app.services.twilio_callbacks import apply_status_callback

# Kept as a name because several modules still import it, but the Vercel
# default is gone: one hostname for every brand is what put an infrastructure
# domain in front of a funeral home's families. Customer-facing links are
# built by app.services.public_identity, resolved per organization. An empty
# value here is deliberate - it makes a stray f-string produce a visibly
# broken link rather than a plausible wrong one.
BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", "")


def _resolve_twilio_creds(advisor: User, db: Session) -> tuple[Client, str, str | None]:
    """
    Returns (twilio_client, from_phone, caller_id_name).

    Resolution order:
      1. Advisor's personal Twilio credentials (their own number)
      2. Org-level shared credentials (toll-free / 10DLC fallback)

    Raises ValueError if neither is configured.
    """
    # --- 1. Advisor-level ---
    if advisor.twilio_account_sid and advisor.twilio_auth_token_encrypted:
        token = decrypt_value(advisor.twilio_auth_token_encrypted)
        client = Client(advisor.twilio_account_sid, token)
        return client, advisor.twilio_phone_number, advisor.twilio_caller_id_name

    # --- 2. Org-level fallback ---
    org: Organization | None = db.query(Organization).filter(
        Organization.id == advisor.organization_id
    ).first()
    if (
        org
        and org.org_twilio_account_sid
        and org.org_twilio_auth_token_encrypted
        and org.org_twilio_phone_number
    ):
        token = decrypt_value(org.org_twilio_auth_token_encrypted)
        client = Client(org.org_twilio_account_sid, token)
        return client, org.org_twilio_phone_number, org.org_twilio_caller_id_name

    raise ValueError(
        f"No Twilio credentials configured for advisor '{advisor.full_name}' "
        f"or their organization. Set a personal number in Settings → Twilio, "
        f"or ask an admin to configure a shared org number in Org Settings."
    )


# Legacy helper kept for any callers that only need the client object
def get_twilio_client(advisor: User, db: Session | None = None) -> Client:
    if db is not None:
        client, _, _ = _resolve_twilio_creds(advisor, db)
        return client
    # Backwards-compat path (no db): advisor must have personal creds
    if not advisor.twilio_account_sid or not advisor.twilio_auth_token_encrypted:
        raise ValueError(f"Advisor {advisor.full_name} has no Twilio credentials configured.")
    auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
    return Client(advisor.twilio_account_sid, auth_token)


BOOKING_SECRET = os.environ.get("BOOKING_SECRET", "advisorflow2026restland")
# IMPORTANT: Set BOOKING_SECRET env var in production to a strong random value.
# The fallback exists only for local dev/testing — any deployment without it
# uses a publicly-known default, making booking tokens trivially forgeable.


# Appointment type map — based on lead tier, message_track, or source
APPT_TYPE_MAP = {
    # Pre-Need / Planning
    "pre_need":              {"label": "Pre-Need Planning Consultation",     "duration": "45"},
    "pre-need":              {"label": "Pre-Need Planning Consultation",     "duration": "45"},
    "preneed":               {"label": "Pre-Need Planning Consultation",     "duration": "45"},
    "preplanning":           {"label": "Pre-Planning Consultation",          "duration": "45"},
    "pre_planning":          {"label": "Pre-Planning Consultation",          "duration": "45"},
    # At-Need
    "at_need":               {"label": "At-Need Arrangement Conference",     "duration": "60"},
    "at-need":               {"label": "At-Need Arrangement Conference",     "duration": "60"},
    "atneed":                {"label": "At-Need Arrangement Conference",     "duration": "60"},
    # Imminent
    "imminent":              {"label": "Immediate Need Consultation",        "duration": "30"},
    "urgent":                {"label": "Urgent Arrangement Consultation",    "duration": "30"},
    # File Check / Code
    "file_check":            {"label": "Family File Review",                 "duration": "20"},
    "file check":            {"label": "Family File Review",                 "duration": "20"},
    "code_lead":             {"label": "Family File Review",                 "duration": "20"},
    "code lead":             {"label": "Family File Review",                 "duration": "20"},
    "file_review":           {"label": "Family File Review",                 "duration": "20"},
    # Property / Cemetery
    "property":              {"label": "Property Ownership Review",          "duration": "30"},
    "property_transfer":     {"label": "Property Transfer Appointment",      "duration": "30"},
    "plot":                  {"label": "Cemetery Property Consultation",     "duration": "30"},
    "marker":                {"label": "Marker & Memorial Consultation",     "duration": "30"},
    "memorial":              {"label": "Memorial Planning Consultation",     "duration": "30"},
    "flower":                {"label": "Memorial Flower Review",             "duration": "20"},
    "flowers":               {"label": "Memorial Flower Review",             "duration": "20"},
    # Contract / Existing
    "contract":              {"label": "Contract Review Appointment",        "duration": "20"},
    "contract_sold":         {"label": "Contract Review Appointment",        "duration": "20"},
    "existing_customer":     {"label": "Family Services Appointment",        "duration": "20"},
    # Referral / Web
    "referral":              {"label": "Family Services Consultation",       "duration": "30"},
    "web_lead":              {"label": "General Consultation",               "duration": "20"},
    "web lead":              {"label": "General Consultation",               "duration": "20"},
    "new_inquiry":           {"label": "New Family Consultation",            "duration": "30"},
    "new inquiry":           {"label": "New Family Consultation",            "duration": "30"},
    # Insurance / Financial
    "insurance":             {"label": "Insurance & Benefits Review",        "duration": "30"},
    "benefits":              {"label": "Benefits & Coverage Consultation",   "duration": "30"},
    # Veteran
    "veteran":               {"label": "Veterans Benefits Consultation",     "duration": "30"},
    "veterans":              {"label": "Veterans Benefits Consultation",     "duration": "30"},
    # Default
    "general":               {"label": "Family Services Appointment",        "duration": "20"},
}

def _detect_appt_type(lead: Lead) -> dict:
    """Detect appointment type from lead tier, message_track, or source."""
    # Check multiple fields in priority order
    for field in [lead.message_track, lead.tier, lead.contact_channel]:
        if not field:
            continue
        key = str(field).lower().strip()
        if key in APPT_TYPE_MAP:
            return APPT_TYPE_MAP[key]
        # Partial match
        for map_key, appt in APPT_TYPE_MAP.items():
            if map_key in key or key in map_key:
                return appt
    return {"label": "Family Services Appointment", "duration": "20"}


def _encode_booking_token(lead: Lead, advisor: User) -> str:
    """
    Generate a base64 self-contained token compatible with the Vercel booking app.
    Format: base64(json({lead, appt_type, expires_at}))~sha256sig
    """
    import base64
    import hashlib
    import json as _json
    from datetime import timedelta

    appt = _detect_appt_type(lead)
    expires = (datetime.utcnow() + timedelta(days=14)).isoformat()
    data = {
        "lead": {
            "First Name": lead.first_name or "",
            "Last Name": lead.last_name or "",
            "Phone": lead.phone or "",
            "Tier": lead.tier or "",
            "Lead Type": lead.message_track or "",
        },
        "appt_type": appt["label"],
        "appt_label": appt["label"],
        "duration": appt["duration"],
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
        from app.services.public_identity import booking_url as public_booking_url
        booking_url = public_booking_url(db, lead.organization_id,
                                         booking_link.token)

    body = render_template(template, lead, advisor, booking_url)

    client, from_phone, _ = _resolve_twilio_creds(advisor, db)

    # StatusCallback: Twilio POSTs delivery receipts here.
    #
    # This used to read API_BASE_URL directly and silently skip the parameter
    # when it was empty. It was ALWAYS empty in production — the variable is not
    # declared for the backend service — so no receipt was ever requested and
    # every message stayed on 'pending' forever. Resolution now lives in
    # app/services/twilio_callbacks.py, which accepts the other spellings this
    # codebase already uses and logs an ERROR rather than failing quietly.
    create_kwargs = apply_status_callback(dict(
        body=body,
        from_=from_phone,
        to=lead.phone,
    ))

    twilio_msg = client.messages.create(**create_kwargs)

    # Log the send so the message history tab has data and health checks have failure data
    message = Message(
        lead_id=lead.id,
        sender_id=advisor.id,
        body=body,
        twilio_sid=twilio_msg.sid,
        twilio_status=twilio_msg.status,
        delivery_status="pending",
        booking_link_id=booking_link.id if booking_link else None,
    )
    db.add(message)
    lead.status = "sent"
    lead.last_messaged_at = datetime.utcnow()
    db.commit()
    return message


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
        from app.services.public_identity import booking_url as public_booking_url
        booking_url = public_booking_url(db, lead.organization_id,
                                         booking_link.token)

    body = render_template(template, lead, advisor, booking_url)

    client, from_phone, _ = _resolve_twilio_creds(advisor, db)
    # Same resolver as send_sms — see the note there and twilio_callbacks.py.
    mms_kwargs = apply_status_callback(dict(
        body=body,
        from_=from_phone,
        to=lead.phone,
        media_url=[media_url],
    ))

    twilio_msg = client.messages.create(**mms_kwargs)

    message = Message(
        lead_id=lead.id,
        sender_id=advisor.id,
        body=f"[MMS] {body}",
        twilio_sid=twilio_msg.sid,
        twilio_status=twilio_msg.status,
        delivery_status="pending",
        booking_link_id=booking_link.id if booking_link else None,
    )
    db.add(message)
    lead.status = "sent"
    lead.last_messaged_at = datetime.utcnow()
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
    get_twilio_client(advisor)
    # Twilio CNAM registration is account-level via Messaging Service or
    # via the Trust Hub for A2P 10DLC - actual API call depends on which
    # Twilio product is in use. Placeholder for the real call:
    #
    # client.trusthub.v1.customer_profiles... (A2P 10DLC brand/campaign)
    #
    # For now we store the desired name on the User record and surface it
    # in the dashboard so Mike can complete this in the Twilio console,
    # since CNAM setup typically requires identity verification.


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
        except Exception:
            skipped.append(lead.id)
    return {"sent_count": len(sent), "skipped_count": len(skipped), "sent_ids": sent, "skipped_ids": skipped}
