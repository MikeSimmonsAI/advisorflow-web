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
# Industry-specific tier sets. Each industry gets tiers that match its
# real sales/service workflow rather than defaulting to funeral terminology.
# ---------------------------------------------------------------------------

FIBER_DEFAULT_TIERS = [
    {
        "tier_key": "prospect", "tier_label": "Prospect", "sort_order": 0,
        "track_key": "fiber_prospect", "track_label": "Fiber Prospect Intro",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Prospect: a door-knocker just captured this lead as a verbal yes. "
            "Tone is warm and welcoming — confirm their interest, give a sense of "
            "next steps (scheduling the install), and keep it brief."
        ),
    },
    {
        "tier_key": "warm_lead", "tier_label": "Warm Lead", "sort_order": 1,
        "track_key": "fiber_warm", "track_label": "Fiber Warm Outreach",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Warm Lead: the customer has been contacted and showed interest. "
            "Move them toward scheduling an install appointment."
        ),
    },
    {
        "tier_key": "appt_set", "tier_label": "Appt Scheduled", "sort_order": 2,
        "track_key": "fiber_appt", "track_label": "Fiber Appt Reminder",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Appointment Scheduled: install date is confirmed. Tone is excited "
            "and reassuring — remind them of the date/time and what to expect."
        ),
    },
    {
        "tier_key": "installed", "tier_label": "Installed", "sort_order": 3,
        "track_key": "fiber_post_install", "track_label": "Post-Install Follow-up",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Installed: service is live. Tone is celebratory — check in on "
            "satisfaction, ask for a review if they are happy."
        ),
    },
    {
        "tier_key": "upsell", "tier_label": "Upsell / Referral", "sort_order": 4,
        "track_key": "fiber_upsell", "track_label": "Fiber Upsell",
        "is_manual_selectable": True,
        "ai_tone_context": (
            "Upsell: existing customer eligible for a speed upgrade or referral "
            "program. Tone is friendly and value-focused."
        ),
    },
    {
        "tier_key": "not_home", "tier_label": "Not Home / Revisit", "sort_order": 5,
        "track_key": "fiber_revisit", "track_label": "Fiber Re-knock",
        "is_manual_selectable": False,
        "ai_tone_context": (
            "Not Home: rep knocked but no answer. Brief, friendly message "
            "introducing Fiber Cartel and inviting them to reach out."
        ),
    },
]

SALES_DEFAULT_TIERS = [
    {
        "tier_key": "prospect", "tier_label": "Prospect", "sort_order": 0,
        "track_key": "sales_prospect", "track_label": "Initial Outreach",
        "ai_tone_context": "New prospect — warm, friendly intro. Focus on value and next step.",
    },
    {
        "tier_key": "warm", "tier_label": "Warm Lead", "sort_order": 1,
        "track_key": "sales_warm", "track_label": "Warm Follow-up",
        "ai_tone_context": "Engaged lead showing interest. Move toward scheduling a demo or call.",
    },
    {
        "tier_key": "hot", "tier_label": "Hot Lead", "sort_order": 2,
        "track_key": "sales_hot", "track_label": "Hot Close",
        "ai_tone_context": "High-intent lead ready to commit. Be direct, remove friction, guide to next step.",
    },
    {
        "tier_key": "customer", "tier_label": "Customer", "sort_order": 3,
        "track_key": "sales_upsell", "track_label": "Customer Upsell",
        "ai_tone_context": "Existing customer. Tone is warm and relationship-focused — upsell or referral ask.",
    },
    {
        "tier_key": "lost", "tier_label": "Lost / Not Now", "sort_order": 4,
        "track_key": "sales_nurture", "track_label": "Long-term Nurture",
        "is_manual_selectable": False,
        "ai_tone_context": "Passed for now — gentle long-cycle nurture to stay top of mind.",
    },
]

ROOFING_DEFAULT_TIERS = [
    {
        "tier_key": "new_lead", "tier_label": "New Lead", "sort_order": 0,
        "track_key": "roof_intro", "track_label": "Roofing Intro",
        "ai_tone_context": "New roofing inquiry. Friendly intro — offer a free inspection.",
    },
    {
        "tier_key": "inspection_set", "tier_label": "Inspection Scheduled", "sort_order": 1,
        "track_key": "roof_inspection", "track_label": "Inspection Reminder",
        "ai_tone_context": "Inspection is booked. Remind them of date/time and what to expect.",
    },
    {
        "tier_key": "estimate_sent", "tier_label": "Estimate Sent", "sort_order": 2,
        "track_key": "roof_estimate", "track_label": "Estimate Follow-up",
        "ai_tone_context": "Quote is out. Follow up warmly — address objections, keep urgency low.",
    },
    {
        "tier_key": "job_booked", "tier_label": "Job Booked", "sort_order": 3,
        "track_key": "roof_booked", "track_label": "Job Confirmation",
        "ai_tone_context": "Project accepted. Confirm start date, set expectations.",
    },
    {
        "tier_key": "job_complete", "tier_label": "Job Complete", "sort_order": 4,
        "track_key": "roof_complete", "track_label": "Post-Job Follow-up",
        "ai_tone_context": "Job done — check satisfaction, ask for review/referral.",
    },
]

INSURANCE_DEFAULT_TIERS = [
    {
        "tier_key": "prospect", "tier_label": "Prospect", "sort_order": 0,
        "track_key": "ins_intro", "track_label": "Insurance Intro",
        "ai_tone_context": "New insurance prospect. Warm intro — focus on peace of mind and protection.",
    },
    {
        "tier_key": "qualified", "tier_label": "Qualified", "sort_order": 1,
        "track_key": "ins_qualified", "track_label": "Qualified Follow-up",
        "ai_tone_context": "Lead is qualified. Move toward getting a quote on the books.",
    },
    {
        "tier_key": "quote_sent", "tier_label": "Quote Sent", "sort_order": 2,
        "track_key": "ins_quote", "track_label": "Quote Follow-up",
        "ai_tone_context": "Quote has been delivered. Follow up — answer questions, handle objections.",
    },
    {
        "tier_key": "app_in_progress", "tier_label": "App In Progress", "sort_order": 3,
        "track_key": "ins_app", "track_label": "Application Support",
        "ai_tone_context": "Application submitted or in progress. Support and keep moving forward.",
    },
    {
        "tier_key": "active", "tier_label": "Active Policy", "sort_order": 4,
        "track_key": "ins_active", "track_label": "Policy Holder Retention",
        "ai_tone_context": "Active policyholder. Renewal touch, referral ask, upsell opportunity.",
    },
]

HOME_SERVICES_DEFAULT_TIERS = [
    {
        "tier_key": "new_lead", "tier_label": "New Lead", "sort_order": 0,
        "track_key": "hs_intro", "track_label": "Home Services Intro",
        "ai_tone_context": "New home services inquiry. Friendly, helpful — book an estimate or consultation.",
    },
    {
        "tier_key": "estimate_scheduled", "tier_label": "Estimate Scheduled", "sort_order": 1,
        "track_key": "hs_estimate", "track_label": "Estimate Reminder",
        "ai_tone_context": "Estimate appointment is set. Remind them and build excitement.",
    },
    {
        "tier_key": "proposal_sent", "tier_label": "Proposal Sent", "sort_order": 2,
        "track_key": "hs_proposal", "track_label": "Proposal Follow-up",
        "ai_tone_context": "Proposal is out. Warm follow-up — answer questions and handle objections gently.",
    },
    {
        "tier_key": "job_won", "tier_label": "Job Won", "sort_order": 3,
        "track_key": "hs_job_won", "track_label": "Job Kickoff",
        "ai_tone_context": "Project awarded. Confirm details, set expectations, build confidence.",
    },
    {
        "tier_key": "complete", "tier_label": "Job Complete", "sort_order": 4,
        "track_key": "hs_complete", "track_label": "Post-Job Follow-up",
        "ai_tone_context": "Job done. Check satisfaction, ask for a review and referral.",
    },
]

REAL_ESTATE_DEFAULT_TIERS = [
    {
        "tier_key": "buyer_lead", "tier_label": "Buyer Lead", "sort_order": 0,
        "track_key": "re_buyer", "track_label": "Buyer Outreach",
        "ai_tone_context": "Prospective buyer. Friendly — understand their needs and offer to show listings.",
    },
    {
        "tier_key": "seller_lead", "tier_label": "Seller Lead", "sort_order": 1,
        "track_key": "re_seller", "track_label": "Seller Outreach",
        "ai_tone_context": "Homeowner interested in selling. Friendly — offer a CMA and guide next steps.",
    },
    {
        "tier_key": "showing_set", "tier_label": "Showing Scheduled", "sort_order": 2,
        "track_key": "re_showing", "track_label": "Showing Reminder",
        "ai_tone_context": "Showing is scheduled. Remind them and build excitement about the property.",
    },
    {
        "tier_key": "under_contract", "tier_label": "Under Contract", "sort_order": 3,
        "track_key": "re_contract", "track_label": "Contract Support",
        "ai_tone_context": "Under contract. Keep them informed and calm through the closing process.",
    },
    {
        "tier_key": "closed", "tier_label": "Closed", "sort_order": 4,
        "track_key": "re_closed", "track_label": "Post-Close Follow-up",
        "ai_tone_context": "Closed! Congratulate them and ask for reviews / referrals.",
    },
]

# Map industry values → their tier set. Fall back to SALES_DEFAULT_TIERS for
# any industry not explicitly listed here — it's generic enough to work.
INDUSTRY_TIER_SETS = {
    "fiber": FIBER_DEFAULT_TIERS,
    "fiber_internet": FIBER_DEFAULT_TIERS,
    "door_to_door": FIBER_DEFAULT_TIERS,
    "direct_sales": SALES_DEFAULT_TIERS,
    "solar": SALES_DEFAULT_TIERS,
    "telecom": SALES_DEFAULT_TIERS,
    "security": SALES_DEFAULT_TIERS,
    "roofing": ROOFING_DEFAULT_TIERS,
    "hvac": HOME_SERVICES_DEFAULT_TIERS,
    "plumbing": HOME_SERVICES_DEFAULT_TIERS,
    "electrical": HOME_SERVICES_DEFAULT_TIERS,
    "pest_control": HOME_SERVICES_DEFAULT_TIERS,
    "landscaping": HOME_SERVICES_DEFAULT_TIERS,
    "windows_doors": HOME_SERVICES_DEFAULT_TIERS,
    "painting": HOME_SERVICES_DEFAULT_TIERS,
    "flooring": HOME_SERVICES_DEFAULT_TIERS,
    "cleaning": HOME_SERVICES_DEFAULT_TIERS,
    "pool_spa": HOME_SERVICES_DEFAULT_TIERS,
    "tree_service": HOME_SERVICES_DEFAULT_TIERS,
    "water_treatment": HOME_SERVICES_DEFAULT_TIERS,
    "home_services": HOME_SERVICES_DEFAULT_TIERS,
    "insurance": INSURANCE_DEFAULT_TIERS,
    "life_insurance": INSURANCE_DEFAULT_TIERS,
    "health_insurance": INSURANCE_DEFAULT_TIERS,
    "medicare": INSURANCE_DEFAULT_TIERS,
    "annuities": INSURANCE_DEFAULT_TIERS,
    "real_estate": REAL_ESTATE_DEFAULT_TIERS,
    "mortgage": INSURANCE_DEFAULT_TIERS,  # quote-based flow similar to insurance
}


def get_tier_set_for_industry(industry: str) -> list:
    """Returns the appropriate default tier set for the given industry string."""
    if not industry:
        return RESTLAND_DEFAULT_TIERS
    key = industry.lower().strip().replace("-", "_").replace(" ", "_")
    if key in ("funeral", "cemetery", "funeral_cemetery"):
        return RESTLAND_DEFAULT_TIERS
    return INDUSTRY_TIER_SETS.get(key, SALES_DEFAULT_TIERS)


def clear_and_reseed_tier_definitions(db: Session, organization_id: str, industry: str) -> list[TierDefinition]:
    """
    Wipes ALL existing TierDefinition rows for this org and reseeds from
    the industry-appropriate defaults. Intentionally destructive — callers
    must confirm with the user before calling this.
    """
    db.query(TierDefinition).filter(TierDefinition.organization_id == organization_id).delete()
    db.commit()
    tier_set = get_tier_set_for_industry(industry)
    created = []
    for spec in tier_set:
        row = TierDefinition(organization_id=organization_id, **spec)
        db.add(row)
        created.append(row)
    db.commit()
    return created

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


def seed_default_tier_definitions(db: Session, organization_id: str, industry: str = "funeral") -> list[TierDefinition]:
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
    tier_set = get_tier_set_for_industry(industry)
    for spec in tier_set:
        definition = TierDefinition(organization_id=organization_id, **spec)
        db.add(definition)
        created.append(definition)
    db.commit()
    return created
