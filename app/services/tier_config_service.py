"""
Tier Configuration Service

The real, per-organization lookup that replaces the old hardcoded
LeadTier enum + TIER_TO_TRACK dict combination. Every organization
(including Restland) owns a real set of TierDefinition rows - this
module is where any code that needs to validate a tier key, look up
its matching track, or get its AI tone-context goes, instead of
reaching for the old Python-level constants directly.
"""

from sqlalchemy.orm import Session
from app.models.models import TierDefinition


def get_tier_definition(db: Session, organization_id: str, tier_key: str):
    """Returns the TierDefinition row for this org+tier_key, or None if it doesn't exist/isn't active."""
    return (
        db.query(TierDefinition)
        .filter(
            TierDefinition.organization_id == organization_id,
            TierDefinition.tier_key == tier_key,
            TierDefinition.is_active == True,
        )
        .first()
    )


def get_tone_context_for_track(db: Session, organization_id: str, track_key: str) -> str:
    """
    Returns the ai_tone_context for this org+track_key. Looked up by
    TRACK, not tier - multiple tiers can share one track (e.g.
    Restland's "Partial Info" and "Address Only" tiers both map to the
    "needs_review" track), so this returns whichever TierDefinition row
    matches the track first; their tone context is meant to be
    identical for a shared track anyway. Falls back to "General
    outreach." if no row matches, same fallback the old hardcoded
    TRACK_CONTEXT.get(track, "General outreach.") used.
    """
    definition = (
        db.query(TierDefinition)
        .filter(
            TierDefinition.organization_id == organization_id,
            TierDefinition.track_key == track_key,
            TierDefinition.is_active == True,
        )
        .first()
    )
    return definition.ai_tone_context if definition else "General outreach."


def list_tier_definitions(db: Session, organization_id: str):
    """Every active tier definition for this org, in display order."""
    return (
        db.query(TierDefinition)
        .filter(TierDefinition.organization_id == organization_id, TierDefinition.is_active == True)
        .order_by(TierDefinition.sort_order.asc())
        .all()
    )


def validate_tier_key(db: Session, organization_id: str, tier_key: str):
    """
    Returns the matching TierDefinition row, or raises ValueError with a
    clear message listing the real, valid options for THIS org - not a
    generic Python enum error, since valid tiers now genuinely differ
    per organization.
    """
    definition = get_tier_definition(db, organization_id, tier_key)
    if not definition:
        valid_keys = [d.tier_key for d in list_tier_definitions(db, organization_id)]
        raise ValueError(f"'{tier_key}' is not a valid tier for this organization. Valid tiers: {', '.join(valid_keys)}")
    return definition


def validate_manually_selectable_tier_key(db: Session, organization_id: str, tier_key: str):
    """
    Same as validate_tier_key, but additionally requires
    is_manual_selectable=True - for the manual lead-entry and referral
    paths, where an advisor picks a tier by hand. Replaces the old
    hardcoded manual_entry_tiers Python set (which only ever worked for
    Restland's specific tier keys) with a real, per-org query against
    each tier's actual is_manual_selectable flag.
    """
    definition = get_tier_definition(db, organization_id, tier_key)
    if not definition or not definition.is_manual_selectable:
        valid_keys = [d.tier_key for d in list_tier_definitions(db, organization_id) if d.is_manual_selectable]
        raise ValueError(f"tier must be one of: {', '.join(sorted(valid_keys))}")
    return definition


# ---------------------------------------------------------------------------
# Restland's default tier set - exactly matching the original, hardcoded
# LeadTier + MessageTrack + TIER_TO_TRACK (import_service.py) +
# TRACK_CONTEXT (template_ai_service.py) values, byte-for-byte. Used to
# seed every new organization's default profile AND to backfill every
# existing organization (the real, one-time migration this whole
# system needs) - both call sites must produce identical rows, since
# an org created tomorrow and Restland's actual existing org must
# behave identically.
#
# NEW_INQUIRY_INTRO's ai_tone_context is "General outreach." -
# preserving a real, pre-existing gap: the old TRACK_CONTEXT dict never
# had an entry for this track either, and the AI prompt builder's
# TRACK_CONTEXT.get(track, "General outreach.") fell back to this exact
# string. Not a new gap introduced by this migration.
# ---------------------------------------------------------------------------
RESTLAND_DEFAULT_TIERS = [
    {
        "tier_key": "pre_need", "tier_label": "Pre-Need", "sort_order": 0,
        "track_key": "pre_need_lock_price", "track_label": "Pre-Need (Lock Price)",
        "ai_tone_context": (
            "Pre-Need: the lead is planning ahead for future cemetery/funeral "
            "arrangements, not facing an active loss. Tone should be helpful "
            "and focused on locking in today's pricing before it changes, not urgent or somber."
        ),
    },
    {
        "tier_key": "at_need", "tier_label": "At-Need", "sort_order": 1,
        "track_key": "at_need_support", "track_label": "At-Need Support",
        "ai_tone_context": (
            "At-Need: the lead's family is currently arranging services for a "
            "recent loss. Tone should be warm, supportive, and unhurried - never salesy."
        ),
    },
    {
        "tier_key": "imminent", "tier_label": "Imminent", "sort_order": 2,
        "track_key": "imminent_support", "track_label": "Imminent Support",
        "ai_tone_context": (
            "Imminent: a loss is expected very soon or has just occurred. Tone "
            "should be gentle and supportive, prioritizing a direct phone call "
            "over a booking link, since this family needs a human now."
        ),
    },
    {
        "tier_key": "contract_sold", "tier_label": "Contract Sold", "sort_order": 3,
        "track_key": "upsell_existing", "track_label": "Upsell (Existing Customer)",
        "ai_tone_context": (
            "Contract Sold / Upsell: the lead already has a contract with us. "
            "Message should introduce additional options (memorials, markers, "
            "additional plots/services) without sounding like a hard sell to "
            "someone who's already a customer."
        ),
    },
    {
        "tier_key": "email_only", "tier_label": "Email Only", "sort_order": 4,
        "track_key": "email_only_nurture", "track_label": "Email-Only Nurture",
        "is_manual_selectable": False,
        "ai_tone_context": (
            "Email-only nurture: the lead has no phone on file, only email. "
            "Tone should be informative and low-pressure, since this is a "
            "longer-cycle relationship-building track, not a quick-response one."
        ),
    },
    {
        "tier_key": "partial", "tier_label": "Partial Info", "sort_order": 5,
        "track_key": "needs_review", "track_label": "Needs Review",
        "is_manual_selectable": False,
        "ai_tone_context": (
            "Needs review (fallback): used only until an advisor manually "
            "assigns the correct tier. Keep this generic and warm - it should "
            "work reasonably for almost any situation."
        ),
    },
    {
        "tier_key": "addr_only", "tier_label": "Address Only", "sort_order": 6,
        "track_key": "needs_review", "track_label": "Needs Review",
        "is_manual_selectable": False,
        "ai_tone_context": (
            "Needs review (fallback): used only until an advisor manually "
            "assigns the correct tier. Keep this generic and warm - it should "
            "work reasonably for almost any situation."
        ),
    },
    {
        "tier_key": "new_inquiry", "tier_label": "New Inquiry", "sort_order": 7,
        "track_key": "new_inquiry_intro", "track_label": "New Inquiry Intro",
        "ai_tone_context": "General outreach.",
    },
]


def seed_default_tier_definitions(db: Session, organization_id: str) -> list[TierDefinition]:
    """
    Creates Restland's default 8 tier definitions for one organization.
    Idempotent - if this org already has any tier_definitions rows at
    all, does nothing and returns the empty list, so calling this
    defensively on every org-creation path is always safe and never
    creates duplicates.
    """
    existing_count = db.query(TierDefinition).filter(TierDefinition.organization_id == organization_id).count()
    if existing_count > 0:
        return []

    created = []
    for spec in RESTLAND_DEFAULT_TIERS:
        definition = TierDefinition(organization_id=organization_id, **spec)
        db.add(definition)
        created.append(definition)
    db.commit()
    return created
