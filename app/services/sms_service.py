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


def _is_platform_owner(user: User) -> bool:
    """True for the AdvisorFlow platform account itself.

    A god_admin operating a customer tenant does so through X-Org-Override,
    which rewrites `organization_id` on the in-memory User to the tenant's id.
    Everything downstream then treats the platform owner as if they were a
    member of that customer's staff - including, until this guard existed, the
    Twilio resolution below, which read the OWNER's personal credentials and
    would have sent a customer's message from the platform's own number.

    That is not a cosmetic attribution problem. The family sees a number the
    funeral home does not own, replies land in the platform's inbox instead of
    the tenant's, and the customer's own 10DLC registration is bypassed.
    """
    return (getattr(user, "role", None) or "").lower() == "god_admin"


def _org_twilio_credentials(org: "Organization | None") -> tuple[str | None, str | None]:
    """(account_sid, plaintext_auth_token) for an organization, or (None, None).

    Credentials live on the ORGANIZATION. One Twilio account, one A2P brand,
    one campaign per customer - and every number underneath it belongs to that
    same account. This is the only place the org token is decrypted.
    """
    if org and org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted:
        return org.org_twilio_account_sid, decrypt_value(org.org_twilio_auth_token_encrypted)
    return None, None


def _resolve_twilio_creds(advisor: User, db: Session) -> tuple[Client, str, str | None]:
    """
    Returns (twilio_client, from_phone, caller_id_name).

    Resolution order:
      1. Advisor's OWN Twilio account (sid + token on their user row) - legacy,
         for advisors who brought their own Twilio before org credentials
         existed. Unchanged, so no existing configuration breaks.
      2. Advisor's ASSIGNED NUMBER + the ORGANIZATION's credentials.
         This is the model everything new uses: the organization holds one
         Twilio account and one A2P brand/campaign; each FSA row holds only
         the local number assigned to them. An advisor row never needs to
         carry a copy of the organization's auth token, and copying it onto
         every row - which the old ladder forced - is precisely the thing this
         branch exists to stop.
      3. The organization's optional SHARED number + the organization's
         credentials.
      4. Nothing. Raise.

    There is no platform fallback at any step. Steps 1 and 2 are SKIPPED for
    the platform owner. See `_is_platform_owner`: the owner's personal Twilio
    is the platform's, never a tenant's, so an impersonated send falls through
    to the organization's own sender and is refused outright when the
    organization has none. Refusing is the right outcome - a customer with no
    sender configured must find that out here, not by having the platform
    quietly send for them.
    """
    owner = _is_platform_owner(advisor)

    # --- 1. Advisor's own Twilio account (legacy / bring-your-own) ---
    if (not owner) and advisor.twilio_account_sid and advisor.twilio_auth_token_encrypted:
        token = decrypt_value(advisor.twilio_auth_token_encrypted)
        client = Client(advisor.twilio_account_sid, token)
        return client, advisor.twilio_phone_number, advisor.twilio_caller_id_name

    org: Organization | None = db.query(Organization).filter(
        Organization.id == advisor.organization_id
    ).first()
    org_sid, org_token = _org_twilio_credentials(org)

    # --- 2. Advisor's assigned number, organization's credentials ---
    if (not owner) and advisor.twilio_phone_number and org_sid and org_token:
        client = Client(org_sid, org_token)
        return (
            client,
            advisor.twilio_phone_number,
            advisor.twilio_caller_id_name or (org.org_twilio_caller_id_name if org else None),
        )

    # --- 3. Organization's optional shared number ---
    if org_sid and org_token and org and org.org_twilio_phone_number:
        client = Client(org_sid, org_token)
        return client, org.org_twilio_phone_number, org.org_twilio_caller_id_name

    # --- 4. Unavailable ---
    if advisor.twilio_phone_number and not owner:
        raise ValueError(
            f"'{advisor.full_name}' has a sending number assigned "
            f"({advisor.twilio_phone_number}) but this organization has no "
            f"Twilio credentials configured. An admin must add the "
            f"organization's Account SID and Auth Token in Org Settings -> Twilio."
        )
    raise ValueError(
        f"No Twilio sender is available for advisor '{advisor.full_name}'. "
        f"An admin must configure this organization's Twilio credentials in "
        f"Org Settings -> Twilio and assign this advisor a sending number."
    )


def describe_sms_sender(advisor: User, db: Session) -> dict:
    """Which number this advisor would send from, WITHOUT raising or sending.

    The composer used to discover that no Twilio credentials were configured by
    pressing Send and reading the exception text. The same four-step resolution
    order is used here as in `_resolve_twilio_creds` - the advisor's own Twilio
    account, then their assigned number on the organization's credentials, then
    the organization's shared number, then nothing - so what the page reports
    and what the send does can never disagree.

    `source` names WHOSE NUMBER is used ("advisor" | "organization").
    `credentials_source` names WHOSE TWILIO ACCOUNT pays for and owns it. They
    differ in the normal case now: an FSA's own local number sent under the
    organization's account and A2P registration.

    NO CROSS-TENANT FALLBACK, and no secret in the return value.

    The platform-owner skip mirrors `_resolve_twilio_creds` exactly, because
    the whole point of this function is that the page and the send agree. A
    god_admin inside a tenant used to be shown the PLATFORM's number here and
    a green "ready" beside it, which is the single most misleading thing this
    screen could say: it reports a customer as able to text when the customer
    has no sender at all.
    """
    owner = _is_platform_owner(advisor)

    # --- 1. Advisor's own Twilio account (legacy / bring-your-own) ---
    if (not owner) and advisor.twilio_account_sid and advisor.twilio_auth_token_encrypted:
        return {
            "ready": bool(advisor.twilio_phone_number),
            "source": "advisor",
            "credentials_source": "advisor",
            "from_number": advisor.twilio_phone_number,
            "account_sid_last4": (advisor.twilio_account_sid or "")[-4:],
            "reason": (None if advisor.twilio_phone_number else
                       "Your Twilio account is connected but no sending number "
                       "is set. Add one in Settings -> Twilio."),
        }

    org: Organization | None = db.query(Organization).filter(
        Organization.id == advisor.organization_id
    ).first()
    org_has_creds = bool(
        org and org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted
    )

    # --- 2. Advisor's assigned number on the organization's credentials ---
    if (not owner) and advisor.twilio_phone_number and org_has_creds:
        return {
            "ready": True,
            "source": "advisor",
            "credentials_source": "organization",
            "from_number": advisor.twilio_phone_number,
            "account_sid_last4": (org.org_twilio_account_sid or "")[-4:],
            "reason": None,
        }

    # --- 3. Organization's optional shared number ---
    if org_has_creds and org.org_twilio_phone_number:
        return {
            "ready": True,
            "source": "organization",
            "credentials_source": "organization",
            "from_number": org.org_twilio_phone_number,
            "account_sid_last4": (org.org_twilio_account_sid or "")[-4:],
            "reason": None,
        }

    # --- 4. Unavailable ---
    if (not owner) and advisor.twilio_phone_number and not org_has_creds:
        return {
            "ready": False,
            "source": "advisor",
            "credentials_source": None,
            "from_number": advisor.twilio_phone_number,
            "account_sid_last4": None,
            "reason": (
                "A sending number is assigned to you, but this organization "
                "has no Twilio credentials configured. An admin must add the "
                "organization's Account SID and Auth Token in Org Settings -> "
                "Twilio before this number can send."
            ),
        }

    return {
        "ready": False,
        "source": None,
        "credentials_source": None,
        "from_number": None,
        "account_sid_last4": None,
        "reason": (
            # Named for whoever is actually missing a sender. Under
            # impersonation the caller is the platform owner, and telling a
            # platform admin to "add a personal number in Settings" would have
            # them configure the PLATFORM's Twilio to fix a CUSTOMER's gap.
            "This organization has no Twilio sender configured. An admin of "
            "this organization must add the organization's Twilio credentials "
            "in Org Settings -> Twilio and assign the advisor a sending number."
            if owner else
            "No sending number is assigned to %s, and this organization has "
            "no shared number. Ask an admin to assign you a number in "
            "Org Settings -> Twilio."
            % (advisor.full_name or "this advisor")
        ),
    }


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


BOOKING_TOKEN_TTL_DAYS = 14


def _encode_booking_token(lead: Lead, advisor: User) -> str:
    """A SHORT, OPAQUE token. 22 characters, no payload, nothing to decode.

    This used to return `base64(json({lead, appt_type, duration, expires}))~sig`
    - 379 characters carrying the family's name, phone and tier in the URL. Two
    things were wrong with that, and the second one broke sending outright:

      1. It put personal data in a link that lands in message logs, carrier
         infrastructure and anyone's screenshot.
      2. It made a normal Restland SMS 602 characters / 4 segments, and
         carriers filtered it - Twilio error 30007, "Your message content was
         flagged as going against carrier guidelines", on EVERY multi-segment
         send from +14692241155 since July. A 1-segment message from the same
         number, on the same path, to the same handset, delivered.

    The token was never doing work as a payload: every lookup in this codebase
    is `BookingLink.token == token`, and lead and advisor are read from that
    row. So the token only ever needed to be an unguessable name for the row.

    `secrets.token_urlsafe(16)` gives 128 bits of entropy in 22 URL-safe
    characters. That is STRONGER than what it replaces - the old signature was
    a 16-hex-character (64-bit) truncated SHA-256 over a payload an attacker
    could read, and it depended on BOOKING_SECRET being set, which in a
    deployment without the env var fell back to a publicly known default.
    Randomness here needs no secret and cannot be forged by construction.

    Expiry moves to `BookingLink.expires_at`, which is enforced against the
    database rather than trusted from the URL - a self-describing token can
    always be re-read, but only the row can be revoked.
    """
    import secrets
    return secrets.token_urlsafe(16)


def create_booking_link(db: Session, lead: Lead, advisor: User) -> BookingLink:
    """Mint a booking link, with the appointment details ON THE ROW."""
    from datetime import timedelta

    appt = _detect_appt_type(lead)
    try:
        duration = int(appt["duration"])
    except (TypeError, ValueError):
        duration = 30

    booking = BookingLink(
        lead_id=lead.id,
        user_id=advisor.id,
        status="pending",
        token=_encode_booking_token(lead, advisor),
        appt_label=appt["label"],
        appt_duration=duration,
        expires_at=datetime.utcnow() + timedelta(days=BOOKING_TOKEN_TTL_DAYS),
    )
    db.add(booking)
    db.commit()
    return booking


def get_or_create_booking_link(db: Session, lead: Lead, advisor: User) -> BookingLink:
    """The link the composer previewed, so the send uses that exact URL.

    Previewing a message used to be impossible without minting a token, and
    minting one per keystroke would litter the table with links a family never
    saw. Reusing the newest still-pending link for this lead and advisor means
    the preview and the send agree on a single URL, and the Message row's
    `booking_link_id` still points at the link that was actually sent.
    """
    existing = (db.query(BookingLink)
                .filter(BookingLink.lead_id == lead.id,
                        BookingLink.user_id == advisor.id,
                        BookingLink.status == "pending")
                .order_by(BookingLink.created_at.desc())
                .first())
    if existing:
        return existing
    return create_booking_link(db, lead, advisor)


def render_template(template: str, lead: Lead, advisor: User, booking_url: str) -> str:
    """Simple variable substitution for message templates."""
    return (
        template
        .replace("{first_name}", lead.first_name or "there")
        .replace("{advisor_name}", advisor.full_name)
        .replace("{booking_link}", booking_url)
        .replace("{advisor_cell}", advisor.twilio_phone_number or "")
    )


BOOKING_LINK_PLACEHOLDER = "{booking_link}"


def compose_body(template: str, lead: Lead, advisor: User, booking_url: str) -> str:
    """The EXACT text that will be sent. One function, used by preview and send.

    `render_template` only substitutes a `{booking_link}` placeholder. A message
    typed by hand, or drafted by the AI (which strips URLs deliberately), has no
    placeholder - so ticking "Include booking link" minted a token, recorded it
    against the message, and sent a text with no link in it. The advisor saw a
    checked box and the family got nothing to click.

    When the placeholder is absent and there is a URL, it is appended. Preview
    and send call this with the same arguments and therefore produce the same
    string, which is the whole point: no hidden send-time URL.
    """
    body = render_template(template, lead, advisor, booking_url)
    if not booking_url:
        return body
    if BOOKING_LINK_PLACEHOLDER in (template or ""):
        return body
    if booking_url in body:
        return body                     # already typed in by hand
    return (body.rstrip() + "\n\n" + booking_url).strip()


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
        # The same link the composer previewed, not a fresh one.
        booking_link = get_or_create_booking_link(db, lead, advisor)
        from app.services.public_identity import booking_url as public_booking_url
        booking_url = public_booking_url(db, lead.organization_id,
                                         booking_link.token)

    body = compose_body(template, lead, advisor, booking_url)

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
        booking_link = get_or_create_booking_link(db, lead, advisor)
        from app.services.public_identity import booking_url as public_booking_url
        booking_url = public_booking_url(db, lead.organization_id,
                                         booking_link.token)

    body = compose_body(template, lead, advisor, booking_url)

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
