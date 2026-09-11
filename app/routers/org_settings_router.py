"""
Org Settings Router â€” white labeling, tier config, industry settings.
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
from app.models.models import Organization, Platform, User
# The SAME writer the god-side Features screen uses. Importing it rather than
# reimplementing it is the point of this import: one column, one set of rules.
from app.services.entitlements import set_features
# GATE 1 + GATE 2 for the Twilio routes at the bottom of this file. `/branding`
# is a setting; `/twilio` is the account that bills and sends. They are not the
# same kind of thing and no longer sit behind the same guard.
from app.services.capabilities import require_capability

router = APIRouter(prefix="/org-settings", tags=["org-settings"])

# THE INDUSTRY TAXONOMY LIVES IN ONE PLACE NOW.
#
# This dict used to be declared here, a second copy lived in settings_router
# for appointment types, and a third in tier_config_service for the
# TierDefinition rows. Three copies is why a new customer in an unrelated
# industry still ended up with funeral vocabulary: whichever map did not
# recognise the industry fell back to the one it knew.
#
# The name is kept so nothing that imports it has to change; the data now has
# exactly one home, and an unknown industry resolves to a neutral
# service-business set rather than to any vertical.
from app.services import industry_migration, industry_templates
from app.services.industry_templates import DEFAULT_TIERS  # noqa: F401  (re-export)

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


# ── who this organization belongs to ────────────────────────────────────────
#
# THREE THINGS, AND THEY ARE NOT THE SAME THING:
#
#   THE ENGINE      AdvisorFlow. The platform underneath everything. A customer
#                   never sees it and it is not a brand anyone is sold.
#   THE BRAND       the white-label product this customer bought — resolved
#                   from the organization's OWN platform row, never from a
#                   constant. This is what the customer sees in the shell, in
#                   their emails and on their support page.
#   THE ORGANIZATION the customer's own company. It owns its name, its logo,
#                   its public contact details and its booking-page identity —
#                   and it does not own, replace or redefine the brand above it.
#
# Those were conflated: the settings page told every customer that its brand
# name "replaces BookaBoost in the sidebar and emails", which is one brand's
# name shown to another brand's customer as if it were the platform itself.

ENGINE_NAME = "AdvisorFlow"


def _platform_identity(db: Session, org: Organization) -> dict:
    """The brand that owns this organization, from its own platform row."""
    from app.services import brand_config

    platform = None
    if getattr(org, "platform_id", None):
        platform = (db.query(Platform)
                    .filter(Platform.id == org.platform_id).first())

    cfg = brand_config.config_for_slug(db, getattr(platform, "slug", None))
    brand_display = (getattr(platform, "name", None)
                     or cfg.get("display_name")
                     or None)

    return {
        "engine": ENGINE_NAME,
        "platform_id": getattr(platform, "id", None),
        "slug": getattr(platform, "slug", None),
        # None rather than a guess. A screen that has no brand name shows the
        # organization's own name, which is always true, instead of a brand
        # that may belong to somebody else.
        "brand_name": brand_display,
        "brand_known": bool(platform is not None),
        "support_email": (getattr(platform, "support_email", None)
                          or cfg.get("support_email")),
        "website_url": cfg.get("website_url"),
        "app_base_url": cfg.get("app_base_url"),
        "logo_url": cfg.get("logo_url"),
        "accent_color": cfg.get("accent_color"),
        "hierarchy": [
            {"level": "engine", "label": ENGINE_NAME, "customer_visible": False},
            {"level": "brand", "label": brand_display, "customer_visible": True},
            {"level": "organization", "label": org.name, "customer_visible": True},
        ],
    }


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
        # No funeral fallback. An org whose industry this platform does not
        # recognise is shown a neutral set, flagged as unmatched, rather than
        # another vertical's vocabulary presented as its own.
        tier_config = industry_templates.lead_tiers(org.industry)

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "plan": org.plan,
        "industry": industry_templates.normalize(org.industry),
        "industry_raw": org.industry,
        "industry_label": industry_templates.resolve(org.industry)["label"],
        "industry_matched": industry_templates.is_known(org.industry),
        # The parent white-label brand, resolved from this org's own platform.
        # Present so no screen has to guess, and so none of them can fall back
        # to a brand name typed into a JSX file.
        "platform": _platform_identity(db, org),
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
        # Org-level email sender â€” each brand sends from its own verified domain.
        "from_email": getattr(org, "from_email", None),
        # Never return the raw API key to the UI â€” only signal whether it's set.
        "resend_api_key_set": bool(getattr(org, "resend_api_key", None)),
        "reply_to_email": getattr(org, "reply_to_email", None),
        "cc_email": getattr(org, "cc_email", None),
        "calendar_provider": getattr(org, "calendar_provider", None),
        # Contact / booking page info
        "org_address": getattr(org, "org_address", None),
        "org_phone": getattr(org, "org_phone", None),
    }


@router.get("/platform-identity")
def platform_identity(org_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> dict:
    """Engine, brand and organization, each named from its own row."""
    org = _resolve_org(current_user, org_id, db)
    return _platform_identity(db, org)


# ── ownership of every setting on this page ─────────────────────────────────
#
# The settings screen had grown into one undifferentiated list in which an
# organization's social links sat next to the Twilio account that bills and
# sends. Those are not the same kind of thing and they do not belong to the
# same people.
#
# This classification is DESCRIPTIVE, not a second permission system. Each
# section names the guard that already enforces it, and `can_edit` is computed
# from the caller's existing role and capabilities — the endpoints below are
# still the things that refuse. A screen reads this to decide what to show and
# what to mark read-only; it is not what makes a write safe.

OWNER_CUSTOMER = "customer"
OWNER_BRAND    = "brand"
OWNER_PLATFORM = "platform"

_SECTIONS = [
    {
        "key": "organization_profile",
        "label": "Organization profile",
        "description": "Who this company is, as its own customers see it.",
        "owner": OWNER_CUSTOMER,
        "risk": "low",
        "fields": ["name", "org_address", "org_phone", "brand_logo_url",
                   "member_label", "members_label", "facebook_url",
                   "google_review_url", "instagram_url", "linkedin_url"],
        "endpoints": ["PATCH /org-settings/contact",
                      "PATCH /org-settings/social-links",
                      "PATCH /org-settings/branding"],
        "guard": "require_admin",
    },
    {
        "key": "business_configuration",
        "label": "Business configuration",
        "description": "What kind of business this is, and the vocabulary that "
                       "follows from it.",
        "owner": OWNER_CUSTOMER,
        "risk": "medium",
        "fields": ["industry", "tier_config", "appointment_types",
                   "crm_stages", "custom_fields"],
        "endpoints": ["PATCH /org-settings/industry",
                      "POST /org-settings/industry/preview",
                      "POST /org-settings/industry/apply",
                      "PATCH /org-settings/tiers",
                      "PUT /settings/appointment-types"],
        "guard": "require_admin",
        "note": "Changing the industry replaces inherited defaults only. "
                "Anything this organization customized is preserved.",
    },
    {
        "key": "communications",
        "label": "Communications and integrations",
        "description": "How this organization sends, and what it is connected "
                       "to.",
        "owner": OWNER_CUSTOMER,
        "risk": "high",
        "fields": ["from_email", "reply_to_email", "cc_email",
                   "resend_api_key", "calendar_provider"],
        "endpoints": ["PATCH /org-settings/email-sender",
                      "PATCH /org-settings/calendar-provider"],
        "guard": "require_admin",
    },
    {
        "key": "platform_brand",
        "label": "Platform and brand",
        "description": "The white-label brand this customer belongs to. Owned "
                       "by the brand, never by the customer.",
        "owner": OWNER_PLATFORM,
        "risk": "high",
        "fields": ["platform.brand_name", "platform.support_email",
                   "platform.logo_url", "platform.accent_color",
                   "platform.app_base_url"],
        "endpoints": ["God Mode brand configuration"],
        "guard": "god_admin",
        "note": "An organization's own name and logo do not replace the brand "
                "above it.",
    },
    {
        "key": "advanced",
        "label": "Advanced and sensitive operations",
        "description": "Sending infrastructure, entitlements, demo data and "
                       "anything destructive.",
        "owner": OWNER_BRAND,
        "risk": "critical",
        "fields": ["twilio_account_sid", "twilio_auth_token", "twilio_phone",
                   "enabled_features", "demo_data", "reset_to_defaults"],
        "endpoints": ["PUT /org-settings/twilio",
                      "PATCH /org-settings/features",
                      "POST /org-settings/industry/apply (replace_customized)"],
        "guard": "capability: twilio / god_admin",
    },
]


@router.get("/sections")
def settings_sections(org_id: Optional[str] = Query(None),
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)) -> dict:
    """Every settings surface, who owns it, and whether THIS caller may edit it."""
    org = _resolve_org(current_user, org_id, db)
    role = getattr(current_user, "role", None)
    is_god = role == "god_admin"
    is_operator = role in ("super_admin", "god_admin")
    is_org_admin = role in ("org_admin", "admin", "owner") or is_operator

    def editable(section) -> bool:
        if section["owner"] == OWNER_PLATFORM:
            return is_god
        if section["risk"] == "critical":
            return is_operator
        return is_org_admin

    return {
        "organization_id": org.id,
        "platform": _platform_identity(db, org),
        "sections": [{**section, "can_edit": editable(section)}
                     for section in _SECTIONS],
        "note": "Ownership is descriptive. Each endpoint enforces its own "
                "guard; this is what a screen reads to decide what to show.",
    }


@router.get("/default-tiers")
def get_default_tiers():
    return DEFAULT_TIERS


@router.get("/industries")
def list_industries() -> dict:
    """Every business type a customer can be, and what each one starts with.

    The settings screen and the customer-creation screen both read this, so
    neither of them carries its own list — which is how the lists drifted apart
    in the first place.
    """
    return {"industries": industry_templates.choices(),
            "generic_key": industry_templates.GENERIC_KEY}


@router.get("/industry-template")
def get_industry_template(industry: Optional[str] = Query(None),
                          org_id: Optional[str] = Query(None),
                          db: Session = Depends(get_db),
                          current_user: User = Depends(require_admin)) -> dict:
    """What a given industry configures, without applying anything.

    `matched: false` means the industry string did not resolve to a template
    and the generic set is being shown — which the screen says out loud rather
    than presenting neutral defaults as somebody's decision.
    """
    if industry is None:
        org = _resolve_org(current_user, org_id, db)
        industry = getattr(org, "industry", None)
    return industry_templates.summary(industry)


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
    """Update org name, address and phone â€” shown on the booking page header and confirmation emails."""
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
    """Set the organization's business type.

    THIS USED TO OVERWRITE THE TIER CONFIGURATION UNCONDITIONALLY. Changing an
    industry on an organization that had renamed its own tiers silently
    destroyed that work, which is why the change now runs through
    `industry_migration`: inherited defaults are replaced, anything a person
    actually customized is preserved and reported back.

    Replacing customized configuration is still possible and is a different
    request — POST /org-settings/industry/apply with replace_customized, which
    demands a reason and writes an audit entry naming what it overwrote.
    """
    org = _resolve_org(current_user, org_id, db)
    result = industry_migration.apply(
        db, org, current_user, req.industry,
        reason="Industry set from organization settings.",
        replace_customized=False)
    db.commit()
    return {"updated": True,
            "industry": result["industry"],
            "industry_label": result["industry_label"],
            "tiers": industry_templates.lead_tiers(result["industry"]),
            "replaced": result["applied"],
            "preserved": result["preserved"]}


class IndustryMigrationRequest(BaseModel):
    industry: str
    reason: Optional[str] = None
    replace_customized: bool = False


@router.post("/industry/preview")
def preview_industry_change(
    req: IndustryMigrationRequest,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """What changing this organization's industry would do. Writes nothing."""
    org = _resolve_org(current_user, org_id, db)
    return industry_migration.preview(db, org, req.industry,
                                      replace_customized=req.replace_customized)


@router.post("/industry/apply")
def apply_industry_change(
    req: IndustryMigrationRequest,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Apply an industry template to an existing organization.

    The repair path for a customer that was provisioned with the wrong
    business type. Inherited defaults are replaced; customized configuration is
    preserved unless `replace_customized` explicitly says otherwise, and either
    way the reason and the exact surfaces touched are audited.
    """
    org = _resolve_org(current_user, org_id, db)
    if req.replace_customized and current_user.role != "god_admin":
        raise HTTPException(
            status_code=403,
            detail="Overwriting configuration this organization customized "
                   "requires platform-owner authority.")
    result = industry_migration.apply(
        db, org, current_user, req.industry,
        reason=(req.reason or ""),
        replace_customized=req.replace_customized)
    db.commit()
    return result


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
    plaintext (it's an outbound service key, not a user secret â€” same
    threat model as an SMTP password stored in env vars). Only updates
    resend_api_key if a non-empty string is provided, so admins can update
    the from_email alone without having to re-enter the key.
    """
    org = _resolve_org(current_user, org_id, db)
    if req.from_email is not None:
        org.from_email = req.from_email or None  # empty string â†’ clear
    if req.resend_api_key:  # only update when a non-empty value is explicitly provided
        org.resend_api_key = req.resend_api_key
    if req.reply_to_email is not None:
        org.reply_to_email = req.reply_to_email or None  # empty string â†’ clear
    if req.cc_email is not None:
        org.cc_email = req.cc_email or None              # empty string â†’ clear
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
    """Super admin only: set which features an org is entitled to USE.

    TWO THINGS WERE WRONG HERE, AND THEY WERE THE SAME MISTAKE TWICE: this route
    carried its own version of work that already had one correct implementation.

    1. NO PLATFORM BOUNDARY. Every other endpoint in this file resolves its org
       through `_resolve_org` -> `load_org_in_scope`. This one loaded the
       Organization by the raw query-parameter id after checking only that the
       caller held super_admin. `require_super_admin` proves the caller is *a*
       platform operator and says nothing about *which* platform, so a
       super_admin on one brand could rewrite another brand's customer's
       entitlements - switching a competitor's customer's features off, or
       switching paid features on. It is the identical hole closed across the
       other thirteen endpoints here; it survived that pass precisely because it
       does not call `_resolve_org`, so a search for that helper never found it.

    2. NO VALIDATION AND NO AUDIT. It wrote `json.dumps(req.enabled_features)`
       straight into the column. The god-side writer for the SAME column,
       `PUT /god/customers/{org_id}/features`, goes through
       `entitlements.set_features` -> `normalize_keys`, which rejects an
       unregistered key with 400 and writes a `customer.features_set` audit row.
       Two writers, one column, different rules: this path is how unregistered
       keys such as `a2p_10dlc` and `master_dashboard` were persisted, and the
       God Features screen - which renders from the registry - then reported
       them as unknown. That warning was this endpoint.

    Both are fixed by deleting the local implementations and calling the shared
    ones. `_resolve_org` refuses with 404 rather than 403 for the reason
    `load_org_in_scope` documents: a 403 on a record you may not touch confirms
    it exists, which is how another brand's customer list gets enumerated one id
    at a time.

    Pass enabled_features=null to restore the legacy all-enabled state.
    Pass enabled_features=[] to disable every optional feature.
    Pass enabled_features=["campaigns","reports",...] to restrict to a subset.
    """
    if current_user.role not in ("super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Super admin only")
    org = _resolve_org(current_user, org_id, db)
    effective = set_features(db, org, current_user, req.enabled_features)
    db.commit()
    return {"updated": True, "enabled_features": req.enabled_features,
            "effective": effective}


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
    org_twilio_auth_token:     str                    # plaintext â€” encrypted before storage
    # OPTIONAL as of the org-credential model. The organization holds the Twilio
    # account and the A2P brand; the numbers underneath it are assigned to
    # individual advisors. A shared org-wide number is a deliberate extra, not a
    # prerequisite â€” requiring one here is what previously forced every customer
    # to nominate some number as "the org number" before anything would send.
    # Sending an empty string CLEARS it.
    org_twilio_phone_number:   Optional[str] = None   # E.164, e.g. "+18005550100"
    org_twilio_caller_id_name: Optional[str] = None
    org_twilio_number_type:    Optional[str] = "toll_free"


class OrgTwilioPhoneUpdate(BaseModel):
    """Lightweight update â€” change phone/caller-id without re-entering the auth token."""
    org_twilio_phone_number:   Optional[str] = None   # "" or null clears the shared number
    org_twilio_caller_id_name: Optional[str] = None
    org_twilio_number_type:    Optional[str] = None


# â”€â”€ Sending-number assignment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# A sending number identifies exactly one mailbox in the inbound webhook
# (app/routers/sms_router.py looks the inbound `To` up against
# users.twilio_phone_number, then organizations.org_twilio_phone_number). Two
# rows holding the same number would make that lookup pick whichever the
# database returned first, and a family's reply â€” a STOP included â€” would land
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


# THE FIVE ROUTES BELOW ARE INFRASTRUCTURE, NOT SETTINGS.
#
# They read and write the Twilio account that bills and sends, and decide which
# number each person sends from. They sat behind `require_admin` like
# `/branding` and `/contact` above them, so holding org_admin meant holding the
# customer's Twilio credentials - and USING a service and CONFIGURING THE
# INFRASTRUCTURE FOR IT are different permissions.
#
# `require_capability` is additive here: `require_admin` proved a role,
# `_resolve_org` proves the platform boundary, and this proves the two
# delegation gates. None of the three replaces another, and all three still run.
@router.get("/twilio", response_model=OrgTwilioRead)
def get_org_twilio(
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    _cap: User = Depends(require_capability("twilio_credentials")),
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
    _cap: User = Depends(require_capability("twilio_credentials")),
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
    _cap: User = Depends(require_capability("twilio_numbers")),
):
    """Update the shared phone number / caller ID only â€” no auth token re-entry.

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
# Per-advisor sending numbers â€” the org holds the credentials, each advisor
# holds only the local number assigned to them.
# ---------------------------------------------------------------------------

@router.get("/twilio/numbers", response_model=list[AdvisorNumberRead])
def list_org_sending_numbers(
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    _cap: User = Depends(require_capability("twilio_numbers")),
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
    _cap: User = Depends(require_capability("twilio_numbers")),
):
    """Assign (or clear) one advisor's sending number.

    This writes a NUMBER ONLY. It never writes an Account SID or Auth Token to
    a user row â€” the credentials stay on the organization, which is the whole
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
        # do, rather than assuming the assignment is sufficient on its own â€”
        # it is not, if the organization has no credentials yet.
        "sender": describe_sms_sender(target, db),
    }
