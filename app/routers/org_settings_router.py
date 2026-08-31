"""
Org Settings Router — white labeling, tier config, industry settings.
Super admin can pass ?org_id= to manage any org's settings.
"""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

_SAFE_URL_SCHEMES = ("http://", "https://")


def _validate_url(url: Optional[str], field: str) -> Optional[str]:
    """Strip and validate that url uses http/https; raise 400 otherwise."""
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(_SAFE_URL_SCHEMES):
        raise HTTPException(status_code=400, detail=f"{field} must be an http or https URL.")
    return url

from app.deps import get_db, get_current_user, require_admin, load_org_in_scope
from app.models.models import Organization, User

router = APIRouter(prefix="/org-settings", tags=["org-settings"])

DEFAULT_TIERS = {
    "funeral": [
        {"value": "pre_need", "label": "Pre-Need", "color": "blue", "description": "Planning ahead"},
        {"value": "at_need", "label": "At-Need", "color": "red", "description": "Immediate need"},
        {"value": "imminent", "label": "Imminent", "color": "red", "description": "Within 90 days"},
        {"value": "contract_sold", "label": "Contract Sold", "color": "green", "description": "Closed"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
        {"value": "partial", "label": "Needs Review", "color": "amber", "description": "Incomplete info"},
    ],
    "roofing": [
        {"value": "estimate_requested", "label": "Estimate Requested", "color": "blue", "description": "New lead"},
        {"value": "estimate_given", "label": "Estimate Given", "color": "amber", "description": "Quote sent"},
        {"value": "follow_up", "label": "Follow Up", "color": "amber", "description": "Waiting on decision"},
        {"value": "contract_signed", "label": "Contract Signed", "color": "green", "description": "Closed"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "insurance": [
        {"value": "prospect", "label": "Prospect", "color": "blue", "description": "Initial contact"},
        {"value": "quoted", "label": "Quoted", "color": "amber", "description": "Quote sent"},
        {"value": "application", "label": "Application", "color": "amber", "description": "App in progress"},
        {"value": "policy_sold", "label": "Policy Sold", "color": "green", "description": "Closed"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "real_estate": [
        {"value": "buyer_lead", "label": "Buyer Lead", "color": "blue", "description": "Looking to buy"},
        {"value": "seller_lead", "label": "Seller Lead", "color": "amber", "description": "Looking to sell"},
        {"value": "showing_scheduled", "label": "Showing Scheduled", "color": "amber", "description": "Active"},
        {"value": "under_contract", "label": "Under Contract", "color": "green", "description": "Pending close"},
        {"value": "closed", "label": "Closed", "color": "green", "description": "Deal done"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "dental": [
        {"value": "new_patient", "label": "New Patient", "color": "blue", "description": "First contact"},
        {"value": "consultation", "label": "Consultation", "color": "amber", "description": "Consult booked"},
        {"value": "treatment_plan", "label": "Treatment Plan", "color": "amber", "description": "Plan presented"},
        {"value": "active_patient", "label": "Active Patient", "color": "green", "description": "Ongoing care"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "custom": [
        {"value": "tier_1", "label": "Tier 1", "color": "blue", "description": ""},
        {"value": "tier_2", "label": "Tier 2", "color": "amber", "description": ""},
        {"value": "tier_3", "label": "Tier 3", "color": "green", "description": ""},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "fiber": [
        {"value": "prospect", "label": "Prospect", "color": "blue", "description": "New inquiry, not yet contacted"},
        {"value": "quoted", "label": "Quoted", "color": "amber", "description": "Service options presented"},
        {"value": "scheduled_install", "label": "Scheduled Install", "color": "orange", "description": "Install date set"},
        {"value": "active_customer", "label": "Active Customer", "color": "green", "description": "Service live"},
        {"value": "churned", "label": "Churned", "color": "red", "description": "Cancelled or lost"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
}


def _resolve_org(current_user: User, org_id: Optional[str], db: Session) -> Organization:
    """
    Resolve which org to operate on.

    `?org_id=` IS SCOPED TO THE CALLER'S OWN PLATFORM.

    This used to load the org by id alone for anyone holding super_admin, with
    no platform comparison. `require_super_admin` proves the caller is *a*
    platform operator; it says nothing about *which* platform. So a super_admin
    on one brand could pass another brand's org id and reach all thirteen
    endpoints below - including PUT /org-settings/twilio, which writes
    `org_twilio_account_sid` and the encrypted auth token. One brand's operator
    could read or overwrite another brand's customer's Twilio credentials.

    `load_org_in_scope` is the guard that already exists for exactly this, and
    its own comment says every route taking an org_id must go through it. These
    routes took it as a QUERY parameter rather than a path parameter, which is
    how they were missed. Using the existing helper rather than a second
    authorization system is deliberate: one boundary, one place to audit.

    It refuses with 404 rather than 403 - a 403 on a record you may not touch
    confirms the record exists, which is how another brand's customer list gets
    enumerated one id at a time.

    god_admin still reaches every org on every platform; that is the owner
    control plane and it is unchanged. A customer org_admin never enters this
    branch at all and continues to get their own org.
    """
    if org_id and current_user.role in ("super_admin", "god_admin"):
        return load_org_in_scope(db, current_user, org_id)
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


class SocialLinksUpdate(BaseModel):
    facebook_url: Optional[str] = None
    google_review_url: Optional[str] = None
    instagram_url: Optional[str] = None
    linkedin_url: Optional[str] = None


class BrandingUpdate(BaseModel):
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_color_primary: Optional[str] = None
    brand_color_accent: Optional[str] = None
    member_label: Optional[str] = None   # singular e.g. "Agent"
    members_label: Optional[str] = None  # plural   e.g. "Agents"


class IndustryUpdate(BaseModel):
    industry: str


class TierConfigUpdate(BaseModel):
    tiers: list[dict]


@router.get("/")
def get_org_settings(
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = _resolve_org(current_user, org_id, db)

    tier_config = []
    if org.tier_config:
        try:
            tier_config = json.loads(org.tier_config)
        except Exception:
            pass
    if not tier_config:
        tier_config = DEFAULT_TIERS.get(org.industry or "funeral", DEFAULT_TIERS["funeral"])

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "plan": org.plan,
        "industry": org.industry or "funeral",
        "brand_name": org.brand_name,
        "brand_logo_url": org.brand_logo_url,
        "brand_color_primary": org.brand_color_primary,
        "brand_color_accent": org.brand_color_accent,
        "member_label": getattr(org, "member_label", None),
        "members_label": getattr(org, "members_label", None),
        "tier_config": tier_config,
        "facebook_url": getattr(org, "facebook_url", None),
        "google_review_url": getattr(org, "google_review_url", None),
        "instagram_url": getattr(org, "instagram_url", None),
        "linkedin_url": getattr(org, "linkedin_url", None),
        "enabled_features": json.loads(org.enabled_features) if getattr(org, "enabled_features", None) else None,
        # Org-level email sender — each brand sends from its own verified domain.
        "from_email": getattr(org, "from_email", None),
        # Never return the raw API key to the UI — only signal whether it's set.
        "resend_api_key_set": bool(getattr(org, "resend_api_key", None)),
        "reply_to_email": getattr(org, "reply_to_email", None),
        "cc_email": getattr(org, "cc_email", None),
        "calendar_provider": getattr(org, "calendar_provider", None),
        # Contact / booking page info
        "org_address": getattr(org, "org_address", None),
        "org_phone": getattr(org, "org_phone", None),
    }


@router.get("/default-tiers")
def get_default_tiers():
    return DEFAULT_TIERS


@router.patch("/branding")
def update_branding(
    req: BrandingUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = _resolve_org(current_user, org_id, db)
    if req.brand_name is not None: org.brand_name = req.brand_name
    if req.brand_logo_url is not None: org.brand_logo_url = req.brand_logo_url
    if req.brand_color_primary is not None: org.brand_color_primary = req.brand_color_primary
    if req.brand_color_accent is not None: org.brand_color_accent = req.brand_color_accent
    # Empty string = clear the override (fall back to industry default in the UI)
    if req.member_label is not None: org.member_label = req.member_label or None
    if req.members_label is not None: org.members_label = req.members_label or None
    db.commit()
    return {"updated": True}


class ContactInfoUpdate(BaseModel):
    name: Optional[str] = None
    org_address: Optional[str] = None
    org_phone: Optional[str] = None


@router.patch("/contact")
def update_contact_info(
    req: ContactInfoUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Update org name, address and phone — shown on the booking page header and confirmation emails."""
    org = _resolve_org(current_user, org_id, db)
    if req.name is not None and req.name.strip():
        org.name = req.name.strip()
    if req.org_address is not None:
        org.org_address = req.org_address.strip() or None
    if req.org_phone is not None:
        org.org_phone = req.org_phone.strip() or None
    db.commit()
    return {"updated": True, "name": org.name, "org_address": org.org_address, "org_phone": org.org_phone}


@router.patch("/industry")
def update_industry(
    req: IndustryUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = _resolve_org(current_user, org_id, db)
    org.industry = req.industry
    org.tier_config = json.dumps(DEFAULT_TIERS.get(req.industry, DEFAULT_TIERS["custom"]))
    db.commit()
    return {"updated": True, "tiers": json.loads(org.tier_config)}


@router.patch("/tiers")
def update_tier_config(
    req: TierConfigUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = _resolve_org(current_user, org_id, db)
    org.tier_config = json.dumps(req.tiers)
    db.commit()
    return {"updated": True, "tiers": req.tiers}


@router.patch("/social-links")
def update_social_links(
    req: SocialLinksUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Save organization-level social media / review page URLs."""
    org = _resolve_org(current_user, org_id, db)
    org.facebook_url = _validate_url(req.facebook_url, "facebook_url")
    org.google_review_url = _validate_url(req.google_review_url, "google_review_url")
    org.instagram_url = _validate_url(req.instagram_url, "instagram_url")
    org.linkedin_url = _validate_url(req.linkedin_url, "linkedin_url")
    db.commit()
    return {"updated": True}


class EmailSenderUpdate(BaseModel):
    from_email: Optional[str] = None
    resend_api_key: Optional[str] = None
    # Where replies land. Separate from the From address because the From must
    # sit on a domain verified with the sending provider and a Reply-To need
    # not - so a customer can send from a verified brand domain and still have
    # a human's answer arrive in their own inbox.
    reply_to_email: Optional[str] = None
    # Optional second recipient on appointment mail. Empty string clears it.
    # There is deliberately no default: nothing is copied anywhere unless an
    # admin puts an address here on purpose.
    cc_email: Optional[str] = None


@router.patch("/email-sender")
def update_email_sender(
    req: EmailSenderUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Saves the org-level Resend API key and from-address. The key is stored
    plaintext (it's an outbound service key, not a user secret — same
    threat model as an SMTP password stored in env vars). Only updates
    resend_api_key if a non-empty string is provided, so admins can update
    the from_email alone without having to re-enter the key.
    """
    org = _resolve_org(current_user, org_id, db)
    if req.from_email is not None:
        org.from_email = req.from_email or None  # empty string → clear
    if req.resend_api_key:  # only update when a non-empty value is explicitly provided
        org.resend_api_key = req.resend_api_key
    if req.reply_to_email is not None:
        org.reply_to_email = req.reply_to_email or None  # empty string → clear
    if req.cc_email is not None:
        org.cc_email = req.cc_email or None              # empty string → clear
    db.commit()
    return {"updated": True, "from_email": org.from_email,
            "reply_to_email": org.reply_to_email, "cc_email": org.cc_email,
            "resend_api_key_set": bool(org.resend_api_key)}


class CalendarProviderUpdate(BaseModel):
    # "google" | "microsoft" | "" to clear. Validated below rather than with an
    # Enum so a bad value returns a sentence naming the allowed ones.
    calendar_provider: Optional[str] = None


@router.patch("/calendar-provider")
def update_calendar_provider(
    req: CalendarProviderUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Which calendar this organization's scheduling runs on.

    Until this existed the answer came from the order of a tuple in
    `calendar_providers/__init__.py`: an advisor connected to both Microsoft
    and Google silently got Microsoft. That is not a decision anyone made
    about their business, and it is invisible in every screen.

    Set here, it is obeyed. A chosen provider that cannot be reached makes
    availability report `calendar_unavailable` rather than quietly resolving
    to the other one, because a booking written to a calendar the customer
    does not use is worse than a booking that refuses to be written.
    """
    allowed = ("google", "microsoft")
    value = (req.calendar_provider or "").strip().lower()
    if value and value not in allowed:
        raise HTTPException(
            status_code=400,
            detail="calendar_provider must be one of %s, or empty to clear."
                   % ", ".join(allowed))
    org = _resolve_org(current_user, org_id, db)
    org.calendar_provider = value or None
    db.commit()
    return {"updated": True, "calendar_provider": org.calendar_provider}


class FeaturesUpdate(BaseModel):
    enabled_features: list[str] | None = None  # None = all enabled; [] = none


@router.patch("/features")
def update_enabled_features(
    req: FeaturesUpdate,
    org_id: str = Query(..., description="Organization ID (required, super admin only)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Super admin only: set which admin features an org can access.
    Pass enabled_features=null to restore all-enabled state.
    Pass enabled_features=[] to disable all optional features.
    Pass enabled_features=["campaigns","reports",...] to restrict to a subset.
    """
    if current_user.role not in ("super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Super admin only")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if req.enabled_features is None:
        org.enabled_features = None
    else:
        org.enabled_features = json.dumps(req.enabled_features)
    db.commit()
    return {"updated": True, "enabled_features": req.enabled_features}


# ---------------------------------------------------------------------------
# Org-level shared Twilio configuration
# Supports toll-free (TFV approved) and 10DLC numbers as a shared sender
# fallback for advisors who don't have personal Twilio numbers configured.
# ---------------------------------------------------------------------------

class OrgTwilioRead(BaseModel):
    org_twilio_phone_number:   Optional[str] = None
    org_twilio_caller_id_name: Optional[str] = None
    org_twilio_number_type:    Optional[str] = None   # "toll_free" | "10dlc" | "short_code"
    org_twilio_configured:     bool = False
    org_twilio_account_sid_last4: Optional[str] = None


class OrgTwilioUpdate(BaseModel):
    org_twilio_account_sid:    str
    org_twilio_auth_token:     str                    # plaintext — encrypted before storage
    # OPTIONAL as of the org-credential model. The organization holds the Twilio
    # account and the A2P brand; the numbers underneath it are assigned to
    # individual advisors. A shared org-wide number is a deliberate extra, not a
    # prerequisite — requiring one here is what previously forced every customer
    # to nominate some number as "the org number" before anything would send.
    # Sending an empty string CLEARS it.
    org_twilio_phone_number:   Optional[str] = None   # E.164, e.g. "+18005550100"
    org_twilio_caller_id_name: Optional[str] = None
    org_twilio_number_type:    Optional[str] = "toll_free"


class OrgTwilioPhoneUpdate(BaseModel):
    """Lightweight update — change phone/caller-id without re-entering the auth token."""
    org_twilio_phone_number:   Optional[str] = None   # "" or null clears the shared number
    org_twilio_caller_id_name: Optional[str] = None
    org_twilio_number_type:    Optional[str] = None


# ── Sending-number assignment ────────────────────────────────────────────────
#
# A sending number identifies exactly one mailbox in the inbound webhook
# (app/routers/sms_router.py looks the inbound `To` up against
# users.twilio_phone_number, then organizations.org_twilio_phone_number). Two
# rows holding the same number would make that lookup pick whichever the
# database returned first, and a family's reply — a STOP included — would land
# in the wrong advisor's thread or the wrong tenant entirely. So assignment is
# checked for collisions across ALL users and ALL organizations, not just this
# one. That check is a correctness requirement of inbound routing, not a
# convenience.

_E164_HINT = "Use E.164 format, e.g. +12145550123."


def _normalize_e164(value: Optional[str], field: str) -> Optional[str]:
    """Return a validated E.164 string, or None for an empty/absent value."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    digits = value[1:] if value.startswith("+") else value
    if not value.startswith("+") or not digits.isdigit() or not (8 <= len(digits) <= 15):
        raise HTTPException(status_code=400, detail=f"{field} is not a valid phone number. {_E164_HINT}")
    return value


def _assert_number_unused(db: Session, number: str, *, allow_user_id: Optional[str] = None,
                          allow_org_id: Optional[str] = None) -> None:
    """Refuse a number already used as a sender anywhere on the platform."""
    clash = db.query(User).filter(User.twilio_phone_number == number)
    if allow_user_id:
        clash = clash.filter(User.id != allow_user_id)
    other = clash.first()
    if other is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{number} is already assigned as a sending number to another "
                f"user. A number can belong to only one sender, otherwise "
                f"inbound replies cannot be routed to the right person."
            ),
        )
    org_clash = db.query(Organization).filter(Organization.org_twilio_phone_number == number)
    if allow_org_id:
        org_clash = org_clash.filter(Organization.id != allow_org_id)
    if org_clash.first() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{number} is already in use as an organization's shared "
                f"sending number. A number can belong to only one sender."
            ),
        )


class AdvisorNumberRead(BaseModel):
    id:                Optional[str] = None
    full_name:         Optional[str] = None
    email:             Optional[str] = None
    role:              Optional[str] = None
    is_active:         Optional[bool] = None
    twilio_phone_number: Optional[str] = None
    twilio_caller_id_name: Optional[str] = None
    # True when this row carries its OWN Twilio account (the legacy
    # bring-your-own path). Under the org-credential model this is False for
    # everyone and the organization's credentials are used instead.
    has_own_twilio_account: bool = False


class AdvisorNumberUpdate(BaseModel):
    twilio_phone_number:   Optional[str] = None   # "" or null unassigns
    twilio_caller_id_name: Optional[str] = None


@router.get("/twilio", response_model=OrgTwilioRead)
def get_org_twilio(
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Return the org's shared Twilio config (auth token is never returned)."""
    org = _resolve_org(current_user, org_id, db)
    return OrgTwilioRead(
        org_twilio_phone_number=org.org_twilio_phone_number,
        org_twilio_caller_id_name=org.org_twilio_caller_id_name,
        org_twilio_number_type=org.org_twilio_number_type or "toll_free",
        org_twilio_configured=bool(
            org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted
        ),
        # Last 4 of THIS organization's own SID, or None. Never the platform's:
        # _resolve_org returns the impersonated tenant, so a god_admin viewing
        # a customer sees that customer's account or nothing at all.
        org_twilio_account_sid_last4=(org.org_twilio_account_sid or "")[-4:] or None,
    )


@router.put("/twilio")
def update_org_twilio(
    req: OrgTwilioUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Save org-level Twilio credentials (auth token is encrypted at rest).

    The shared number is optional: an organization may hold credentials and an
    A2P brand while every sending number belongs to an individual advisor.
    """
    from app.utils.crypto import encrypt_value
    org = _resolve_org(current_user, org_id, db)
    sid = (req.org_twilio_account_sid or "").strip()
    token = (req.org_twilio_auth_token or "").strip()
    if not sid or not token:
        raise HTTPException(
            status_code=400,
            detail="Both the Twilio Account SID and Auth Token are required.",
        )
    shared = _normalize_e164(req.org_twilio_phone_number, "Shared SMS number")
    if shared:
        _assert_number_unused(db, shared, allow_org_id=org.id)

    org.org_twilio_account_sid          = sid
    org.org_twilio_auth_token_encrypted = encrypt_value(token)
    org.org_twilio_phone_number         = shared
    org.org_twilio_caller_id_name       = req.org_twilio_caller_id_name
    org.org_twilio_number_type          = req.org_twilio_number_type or "toll_free"
    db.commit()
    return {"updated": True, "org_twilio_phone_number": org.org_twilio_phone_number}


@router.patch("/twilio/phone")
def update_org_twilio_phone(
    req: OrgTwilioPhoneUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Update the shared phone number / caller ID only — no auth token re-entry.

    An empty phone number CLEARS the shared sender, which is a supported state:
    the organization keeps its credentials and A2P registration, and every send
    resolves through an advisor's own assigned number.
    """
    org = _resolve_org(current_user, org_id, db)
    shared = _normalize_e164(req.org_twilio_phone_number, "Shared SMS number")
    if shared:
        _assert_number_unused(db, shared, allow_org_id=org.id)
    org.org_twilio_phone_number = shared
    if req.org_twilio_caller_id_name is not None:
        org.org_twilio_caller_id_name = req.org_twilio_caller_id_name
    if req.org_twilio_number_type is not None:
        org.org_twilio_number_type = req.org_twilio_number_type
    db.commit()
    return {"updated": True, "org_twilio_phone_number": org.org_twilio_phone_number}


# ---------------------------------------------------------------------------
# Per-advisor sending numbers — the org holds the credentials, each advisor
# holds only the local number assigned to them.
# ---------------------------------------------------------------------------

@router.get("/twilio/numbers", response_model=list[AdvisorNumberRead])
def list_org_sending_numbers(
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Who in this organization holds which sending number.

    god_admin rows are excluded even when they carry an organization_id through
    impersonation: the platform owner is not a member of the customer's staff
    and their number is the platform's, not the customer's.
    """
    org = _resolve_org(current_user, org_id, db)
    members = (
        db.query(User)
        .filter(User.organization_id == org.id, User.role != "god_admin")
        .order_by(User.full_name)
        .all()
    )
    return [
        AdvisorNumberRead(
            id=m.id,
            full_name=m.full_name,
            email=m.email,
            role=m.role,
            is_active=bool(m.is_active),
            twilio_phone_number=m.twilio_phone_number,
            twilio_caller_id_name=m.twilio_caller_id_name,
            has_own_twilio_account=bool(
                m.twilio_account_sid and m.twilio_auth_token_encrypted
            ),
        )
        for m in members
    ]


@router.put("/twilio/numbers/{user_id}")
def assign_org_sending_number(
    user_id: str,
    req: AdvisorNumberUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Assign (or clear) one advisor's sending number.

    This writes a NUMBER ONLY. It never writes an Account SID or Auth Token to
    a user row — the credentials stay on the organization, which is the whole
    point of the model: one Twilio account and one A2P registration per
    customer, with the numbers underneath it handed out to staff.

    The target user must belong to the organization being edited. That check is
    what keeps an org admin from assigning a number to somebody in another
    tenant, and it is enforced here rather than trusted from the request body.
    """
    org = _resolve_org(current_user, org_id, db)
    target = (
        db.query(User)
        .filter(User.id == user_id, User.organization_id == org.id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="User not found in this organization")
    if (target.role or "").lower() == "god_admin":
        raise HTTPException(
            status_code=400,
            detail="The platform owner's number is not a tenant sending number.",
        )

    number = _normalize_e164(req.twilio_phone_number, "Sending number")
    if number:
        _assert_number_unused(db, number, allow_user_id=target.id)

    target.twilio_phone_number = number
    if req.twilio_caller_id_name is not None:
        target.twilio_caller_id_name = req.twilio_caller_id_name.strip() or None
    db.commit()

    from app.services.sms_service import describe_sms_sender
    return {
        "updated": True,
        "user_id": target.id,
        "full_name": target.full_name,
        "twilio_phone_number": target.twilio_phone_number,
        # Echo the resolved sender so the UI reports exactly what a send would
        # do, rather than assuming the assignment is sufficient on its own —
        # it is not, if the organization has no credentials yet.
        "sender": describe_sms_sender(target, db),
    }
