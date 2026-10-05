"""Configure an organization as a location outreach program. Idempotent.

Every function here can run twice and leave one copy of everything: the
overnight apply step for SCI is "run setup, check the dry-run, run nothing
else", so a second run after a partial failure must be harmless.
"""
import hashlib
import json
import secrets
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.location_models import Location
from app.models.models import Organization, User
from app.models.program_models import (
    CampaignFamily, LocationProfile, OutreachProgram, ProgramAsset,
)
from app.services.programs.normalize import location_key

REVIEW_BUCKET_NAME = "Unassigned / Location Review"

DEFAULT_REPLY_SMS = "Reply to this text and I'll get right back to you."
DEFAULT_REPLY_EMAIL = "Just reply to this email and I'll get right back to you."

# Campaign families. ONE definition each, rendered per location at send time:
#   {first_name} {location_name} {primary_contact_name} {reply_instructions}
#   {flyer_link} {location_website}
# Copy is a starting point for review, not approved creative, and every family
# is created INACTIVE: nothing is enrolled until a person switches it on.
# None of it asks the family to call.
DEFAULT_FAMILIES = [
    {
        "key": "veteran_planning_guide", "name": "Veteran Planning Guide",
        "patterns": ["Veteran Planning Guide"], "asset_category": "veteran_planning_guide",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}. "
                "You asked about the Veteran Planning Guide - I can send it over. "
                "{reply_instructions}"),
        "subject": "Your Veteran Planning Guide from {location_name}",
        "body": ("Hi {first_name},\n\nThank you for your interest in the Veteran Planning Guide. "
                 "{flyer_link}\n\nIf you have any questions about the benefits available to you "
                 "and your family, I'm glad to help. {reply_instructions}\n\n"
                 "{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "veteran_official", "name": "Veteran Official",
        "patterns": ["Veteran> Official"], "asset_category": "veteran_planning_guide",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}, following up "
                "on your veteran benefits request. {reply_instructions}"),
        "subject": "Your veteran benefits information - {location_name}",
        "body": ("Hi {first_name},\n\nI'm following up on your veteran benefits request. "
                 "{flyer_link}\n\n{reply_instructions}\n\n{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "veteran_spanish", "name": "Veteran - Spanish callouts",
        "patterns": ["Spanish Callouts"], "asset_category": "veteran_planning_guide",
        "language": "en-es",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name} about your "
                "veteran planning request. {reply_instructions}"),
        "subject": "Veteran planning information - {location_name}",
        "body": ("Hi {first_name},\n\n{flyer_link}\n\n{reply_instructions}\n\n"
                 "{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "general_survey", "name": "General Survey",
        "patterns": ["Survey> General"], "asset_category": "general_preplanning",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}. Thank you for "
                "returning our planning survey. {reply_instructions}"),
        "subject": "Thank you from {location_name}",
        "body": ("Hi {first_name},\n\nThank you for returning our planning survey. "
                 "{flyer_link}\n\n{reply_instructions}\n\n{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "life_story", "name": "Life Story",
        "patterns": ["Life Story"], "asset_category": "general_preplanning",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}, following up on "
                "your Life Story request. {reply_instructions}"),
        "subject": "Your Life Story request - {location_name}",
        "body": ("Hi {first_name},\n\n{flyer_link}\n\n{reply_instructions}\n\n"
                 "{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "cemetery_x_sell", "name": "Cemetery X-Sell",
        "patterns": ["Cemetery X-Sell"], "asset_category": "cemetery_planning",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}. I'd be glad to "
                "share cemetery planning options with you. {reply_instructions}"),
        "subject": "Cemetery planning options at {location_name}",
        "body": ("Hi {first_name},\n\n{flyer_link}\n\n{reply_instructions}\n\n"
                 "{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "cremation", "name": "Cremation",
        "patterns": ["Cremation"], "asset_category": "cremation_information",
        "sms": ("Hi {first_name}, this is {primary_contact_name} with {location_name}, following up on "
                "your cremation information request. {reply_instructions}"),
        "subject": "Cremation information from {location_name}",
        "body": ("Hi {first_name},\n\n{flyer_link}\n\n{reply_instructions}\n\n"
                 "{primary_contact_name}\n{location_name}"),
    },
    {
        "key": "re_engagement", "name": "Re-engagement / Follow-Up",
        "patterns": [], "asset_category": "re_engagement",
        "sms": ("Hi {first_name}, {primary_contact_name} with {location_name} checking in. "
                "{reply_instructions}"),
        "subject": "Checking in from {location_name}",
        "body": ("Hi {first_name},\n\n{flyer_link}\n\n{reply_instructions}\n\n"
                 "{primary_contact_name}\n{location_name}"),
    },
]

FLYER_CATEGORIES = {
    "veteran_planning_guide": "Veteran Planning Guide",
    "general_preplanning": "General Pre-Planning",
    "cemetery_planning": "Cemetery Planning",
    "cremation_information": "Cremation Information",
    "re_engagement": "Re-engagement / Follow-Up",
}

FLYER_DYNAMIC_FIELDS = [
    "location_name", "location_logo", "program_logo", "facility_image",
    "primary_contact_name", "facility_address", "location_website", "campaign",
    "cta", "reply_instructions",
]


def ensure_program(db: Session, org: Organization, *, name: str,
                   primary_contact_name: Optional[str] = None,
                   primary_contact_title: Optional[str] = None,
                   hero_title: Optional[str] = None,
                   hero_subtitle: Optional[str] = None,
                   hot_sla_minutes: int = 15) -> OutreachProgram:
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is None:
        prog = OutreachProgram(
            organization_id=org.id, name=name,
            customer_channels=json.dumps(["sms", "email"]),
            reply_instructions_sms=DEFAULT_REPLY_SMS,
            reply_instructions_email=DEFAULT_REPLY_EMAIL,
            hot_sla_minutes=hot_sla_minutes,
            require_location_to_send=True,
            staff_sms_alerts_enabled=False,
        )
        db.add(prog)
    prog.name = name
    if primary_contact_name is not None:
        prog.primary_contact_name = primary_contact_name
    if primary_contact_title is not None:
        prog.primary_contact_title = primary_contact_title
    if hero_title is not None:
        prog.hero_title = hero_title
    if hero_subtitle is not None:
        prog.hero_subtitle = hero_subtitle
    db.flush()
    return prog


def ensure_locations(db: Session, org: Organization, actor: User,
                     names: Iterable[str]) -> Dict[str, LocationProfile]:
    """One Location + LocationProfile per distinct source name, plus the review bucket.

    Only the NAME comes from the source. Address, phone, website and manager are
    left empty - nothing is invented; they are filled in on the profile screen.
    """
    from app.services import customer_provisioning as cp
    out: Dict[str, LocationProfile] = {}
    wanted = []
    for n in names:
        n = (n or "").strip()
        if n and location_key(n) not in {location_key(w) for w in wanted}:
            wanted.append(n)
    wanted.sort(key=str.lower)
    existing_profiles = {p.location_id: p for p in db.query(LocationProfile)
                         .filter(LocationProfile.organization_id == org.id).all()}
    locs = {location_key(l.name): l for l in db.query(Location)
            .filter(Location.organization_id == org.id).all()}

    def _profile(loc: Location, official: str, review: bool) -> LocationProfile:
        prof = existing_profiles.get(loc.id)
        if prof is None:
            prof = LocationProfile(organization_id=org.id, location_id=loc.id,
                                   official_name=official, is_review_bucket=review,
                                   source_names=json.dumps([] if review else [official]))
            db.add(prof)
            existing_profiles[loc.id] = prof
        return prof

    for name in wanted:
        loc = locs.get(location_key(name))
        if loc is None:
            loc = cp.create_location(db, org, actor, name=name)
            locs[location_key(name)] = loc
        out[location_key(name)] = _profile(loc, name, False)
    review = locs.get(location_key(REVIEW_BUCKET_NAME))
    if review is None:
        review = cp.create_location(db, org, actor, name=REVIEW_BUCKET_NAME,
                                    notes="Rows whose location could not be resolved. "
                                          "Nothing here is ever sent to.")
        locs[location_key(REVIEW_BUCKET_NAME)] = review
    out[location_key(REVIEW_BUCKET_NAME)] = _profile(review, REVIEW_BUCKET_NAME, True)
    db.flush()
    return out


def ensure_campaign_families(db: Session, org: Organization) -> List[CampaignFamily]:
    have = {f.key: f for f in db.query(CampaignFamily)
            .filter(CampaignFamily.organization_id == org.id).all()}
    out = []
    for spec in DEFAULT_FAMILIES:
        fam = have.get(spec["key"])
        if fam is None:
            fam = CampaignFamily(
                organization_id=org.id, key=spec["key"], name=spec["name"],
                source_campaign_patterns=json.dumps(spec["patterns"]),
                asset_category=spec["asset_category"],
                language=spec.get("language", "en"),
                first_touch_email_mode="hosted", followup_email_mode="attached",
                sms_template=spec["sms"], email_subject_template=spec["subject"],
                email_body_template=spec["body"], is_active=False,
            )
            db.add(fam)
        out.append(fam)
    db.flush()
    return out


def store_asset(db: Session, org: Organization, *, kind: str, title: str, data: bytes,
                content_type: str, filename: Optional[str] = None,
                category: Optional[str] = None, campaign_family: Optional[str] = None,
                location_id: Optional[str] = None, activate: bool = False,
                uploaded_by: Optional[str] = None) -> ProgramAsset:
    """A new VERSION of (org, kind, category, family, location, title). Old versions stay."""
    digest = hashlib.sha256(data).hexdigest()
    q = db.query(ProgramAsset).filter(
        ProgramAsset.organization_id == org.id, ProgramAsset.kind == kind,
        ProgramAsset.title == title, ProgramAsset.category == category,
        ProgramAsset.campaign_family == campaign_family,
        ProgramAsset.location_id == location_id)
    prior = q.order_by(ProgramAsset.version.desc()).all()
    if prior and prior[0].sha256 == digest:
        same = prior[0]
        if activate and not same.is_active:
            set_active(db, same, True)
        return same
    asset = ProgramAsset(
        organization_id=org.id, kind=kind, title=title, category=category,
        campaign_family=campaign_family, location_id=location_id,
        version=(prior[0].version + 1) if prior else 1, is_active=False,
        filename=filename, content_type=content_type, size_bytes=len(data),
        sha256=digest, data=data, public_token=secrets.token_urlsafe(24),
        dynamic_fields=json.dumps(FLYER_DYNAMIC_FIELDS) if kind == "flyer" else None,
        uploaded_by=uploaded_by,
    )
    db.add(asset)
    db.flush()
    if activate:
        set_active(db, asset, True)
    return asset


def set_active(db: Session, asset: ProgramAsset, active: bool) -> ProgramAsset:
    """Activating a version deactivates its siblings - one live version per slot."""
    if active:
        for sib in db.query(ProgramAsset).filter(
                ProgramAsset.organization_id == asset.organization_id,
                ProgramAsset.kind == asset.kind, ProgramAsset.title == asset.title,
                ProgramAsset.category == asset.category,
                ProgramAsset.campaign_family == asset.campaign_family,
                ProgramAsset.location_id == asset.location_id,
                ProgramAsset.id != asset.id).all():
            sib.is_active = False
    asset.is_active = active
    db.flush()
    return asset


def attach_primary_contact_user(db: Session, org: Organization, actor: User, *,
                                email: str, full_name: str,
                                title: Optional[str] = None) -> User:
    """Create (or reuse) the program's primary contact as an org_admin. NO INVITE.

    The account gets an unknowable password and no activation link is issued
    or sent. Access is granted later, deliberately, from the People screen.
    Requires a real email - the caller never makes one up.
    """
    from app.services import customer_provisioning as cp
    user, _ = cp.add_customer_user(db, org, actor, email=email, full_name=full_name,
                                   role="org_admin")
    if title:
        user.job_title = title
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is not None:
        prog.primary_contact_user_id = user.id
        prog.primary_contact_name = prog.primary_contact_name or full_name
    db.flush()
    return user
