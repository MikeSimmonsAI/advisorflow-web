"""Managed client acquisition (Issue #452) - shared vocabulary and access rules.

EvoSys Pro runs the acquisition engine for ONE client business (the Divine
Shine Elite Cleaning pilot): discover -> research -> qualify -> verified
contact -> outreach -> reply -> interested -> appointment -> held -> quote ->
won / lost. The client sees only what reached "interested" and later.

The engine is data: the client is a row (AcquisitionProfile on its own
organization), never a code path. It is switched on by an EXPLICIT feature
key on that organization, and only for a cleaning (or industry-neutral)
business, and only for PILOT_ORG_LIMIT organizations at once.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

FEATURE = "client_acquisition"
IDENTITY = "cleaning"
# Client-ready product template: each cleaning organization is explicitly
# enabled by God, starts PAUSED and keeps its own CRM, profile and portal.
# The cap prevents accidental unbounded provisioning; it is not a shared tenant.
PILOT_ORG_LIMIT = 25

# Practice rows are a controlled test cohort: they never reach the client,
# never count in client metrics, and are never eligible for outreach.
LEAD_SOURCE = "managed_acquisition"
PRACTICE_SOURCE = "managed_acquisition_practice"

# ── THE JOURNEY ────────────────────────────────────────────────────────────
DISCOVERED = "discovered"
RESEARCHED = "researched"
QUALIFIED = "qualified"
DISQUALIFIED = "disqualified"
CONTACT_VERIFIED = "contact_verified"
OUTREACH_READY = "outreach_ready"
CONTACTED = "contacted"
REPLIED = "replied"
INTERESTED = "interested"
BOOKED = "appointment_booked"
HELD = "appointment_held"
NO_SHOW = "no_show"
QUOTED = "quote_issued"
WON = "won"
LOST = "lost"

STAGES: List[Dict[str, str]] = [
    {"key": DISCOVERED, "label": "Discovered", "hint": "Found by research, nothing checked yet"},
    {"key": RESEARCHED, "label": "Researched", "hint": "Website and public facts reviewed, with sources"},
    {"key": QUALIFIED, "label": "Qualified", "hint": "Fits the client's ideal customer"},
    {"key": DISQUALIFIED, "label": "Not a fit", "hint": "Excluded, with the reason recorded"},
    {"key": CONTACT_VERIFIED, "label": "Contact verified", "hint": "A decision-maker contact passed verification"},
    {"key": OUTREACH_READY, "label": "Outreach ready", "hint": "A personal message is drafted and waiting"},
    {"key": CONTACTED, "label": "Contacted", "hint": "An authorized message went out"},
    {"key": REPLIED, "label": "Replied", "hint": "They wrote back"},
    {"key": INTERESTED, "label": "Interested", "hint": "Showed real interest in cleaning service"},
    {"key": BOOKED, "label": "Appointment booked", "hint": "A walkthrough or consultation is on the calendar"},
    {"key": HELD, "label": "Appointment held", "hint": "The client met with them"},
    {"key": NO_SHOW, "label": "No-show", "hint": "They missed the appointment"},
    {"key": QUOTED, "label": "Quote issued", "hint": "The client sent a quote"},
    {"key": WON, "label": "Won", "hint": "Contract signed"},
    {"key": LOST, "label": "Lost", "hint": "Closed without a contract"},
]
STAGE_KEYS = [s["key"] for s in STAGES]
STAGE_LABEL = {s["key"]: s["label"] for s in STAGES}
# How far along each stage is (lost/no-show/disqualified count as the furthest
# stage they must have passed), for "how many reached X" counts.
RANK = {DISCOVERED: 0, RESEARCHED: 1, DISQUALIFIED: 1, QUALIFIED: 2, CONTACT_VERIFIED: 3, OUTREACH_READY: 4,
        CONTACTED: 5, REPLIED: 6, INTERESTED: 7, LOST: 7, BOOKED: 8, NO_SHOW: 8, HELD: 9, QUOTED: 10, WON: 11}
# WHAT THE CLEANING CUSTOMER SEES (owner decision 2026-11-03, #452):
# EvoSys sells BOOKED APPOINTMENTS, not leads. A prospect EvoSys has allocated
# internally to a client is EvoSys's working record; the customer sees it only
# once a real appointment was booked for them (booked_at is set) and from then
# on through its outcome. Interest, research and conversations stay internal.
CLIENT_STAGES = (BOOKED, HELD, NO_SHOW, QUOTED, WON, LOST)
# What the client may record herself, from which stages.
CLIENT_OUTCOMES = {
    HELD: (BOOKED, NO_SHOW),
    NO_SHOW: (BOOKED,),
    QUOTED: (BOOKED, HELD),
    WON: (HELD, QUOTED),
    LOST: (BOOKED, HELD, NO_SHOW, QUOTED),
}

SEGMENTS: List[Dict[str, str]] = [
    {"key": "office", "label": "Office buildings & professional offices"},
    {"key": "medical_dental", "label": "Medical & dental offices"},
    {"key": "apartments", "label": "Apartment complexes"},
    {"key": "property_management", "label": "Property management companies"},
    {"key": "short_term_rental", "label": "Short-term rental managers"},
    # #452 multi-industry (2026-11-03): buyer types other service businesses sell to.
    {"key": "retail", "label": "Retail & storefronts"},
    {"key": "restaurants", "label": "Restaurants & hospitality"},
    {"key": "hoa", "label": "HOAs & communities"},
    {"key": "industrial", "label": "Warehouses & industrial"},
    {"key": "homeowners", "label": "Homeowners (residential)"},
]

# ── SERVICE CATEGORIES (#452, owner decision 2026-11-03) ─────────────────────
# Acquisition is a service-business appointment engine, not a cleaning app.
# A customer's category is its organization industry; cleaning was the first.
# Each category: (key, label, [(service key, service label, buyer segment)]).
SERVICE_CATEGORIES = [
    ("cleaning", "Commercial cleaning", [
        ("office", "Commercial office cleaning", "office"),
        ("medical", "Medical / dental cleaning", "medical_dental"),
        ("apartment_turnover", "Apartment turnover cleaning", "apartments"),
        ("property_management", "Property management cleaning", "property_management"),
        ("str_turnover", "Short-term rental turnover", "short_term_rental"),
        ("janitorial", "Janitorial contracts", "office"),
        ("move_in_out", "Move-in / move-out cleaning", "apartments")]),
    ("residential_cleaning", "Residential cleaning", [
        ("recurring_home", "Recurring home cleaning", "homeowners"),
        ("deep_clean", "Deep cleaning", "homeowners"),
        ("move_in_out", "Move-in / move-out cleaning", "apartments"),
        ("str_turnover", "Short-term rental turnover", "short_term_rental")]),
    ("window_washing", "Window washing", [
        ("storefront_windows", "Storefront windows", "retail"),
        ("office_windows", "Office building windows", "office"),
        ("high_rise", "High-rise / multi-story", "property_management"),
        ("residential_windows", "Residential windows", "homeowners")]),
    ("pest_control", "Pest control", [
        ("commercial_pest", "Commercial pest service", "office"),
        ("restaurant_pest", "Restaurant & food service", "restaurants"),
        ("multifamily_pest", "Apartment / multifamily", "apartments"),
        ("warehouse_pest", "Warehouse & industrial", "industrial"),
        ("residential_pest", "Residential pest service", "homeowners")]),
    ("landscaping", "Landscaping", [
        ("commercial_grounds", "Commercial grounds maintenance", "office"),
        ("hoa_grounds", "HOA & community grounds", "hoa"),
        ("multifamily_grounds", "Apartment grounds", "apartments"),
        ("residential_lawn", "Residential lawn care", "homeowners")]),
    ("pressure_washing", "Pressure washing", [
        ("storefront_wash", "Storefronts & sidewalks", "retail"),
        ("parking_wash", "Parking lots & garages", "property_management"),
        ("building_wash", "Building exteriors", "office"),
        ("residential_wash", "Residential exteriors", "homeowners")]),
    ("property_maintenance", "Property maintenance", [
        ("make_ready", "Make-ready / turnover", "apartments"),
        ("facility_repairs", "Facility repairs", "office"),
        ("hoa_maintenance", "HOA maintenance", "hoa")]),
    ("service_other", "Other service business", []),
]
SERVICE_CATEGORY_KEYS = [k for k, _, _ in SERVICE_CATEGORIES]
SERVICE_CATEGORY_LABEL = {k: l for k, l, _ in SERVICE_CATEGORIES}


def services_for(category: str):
    for k, _, svcs in SERVICE_CATEGORIES:
        if k == category:
            return svcs
    return []
SEGMENT_KEYS = [s["key"] for s in SEGMENTS]
SEGMENT_LABEL = {s["key"]: s["label"] for s in SEGMENTS}

DEFAULT_SERVICE_AREA = ["Dallas", "Fort Worth", "Arlington", "Plano", "Irving", "Garland",
                        "Frisco", "McKinney", "Grand Prairie", "Denton", "Richardson", "Carrollton"]
DEFAULT_APPOINTMENT_MINUTES = 45
DEFAULT_TIMEZONE = "America/Chicago"
RECURRING_PREFS = ("recurring", "one_time", "both")
# Two different offers, never mixed: Divine Shine's pilot is free; other
# cleaning businesses may be offered the paid Growth Pilot. Owner sets it.
FREE_PILOT = "free_pilot"
GROWTH_PILOT = "growth_pilot_250"
OFFER_LABEL = {FREE_PILOT: "Free 30-day pilot", GROWTH_PILOT: "$250 / 30-day Growth Pilot",
               "paid_trial": "Paid trial", "monthly_subscription": "Monthly subscription",
               "appointment_package": "Appointment-based package", "custom": "Custom offer"}
# Recorded terms only - choosing an offer never creates billing or a subscription.
APPOINTMENT_TYPES = ["Walkthrough", "Consultation"]

# Evidence kinds - every fact the engine states about a business carries one
# of these, a source URL and a confidence; nothing unsupported is shown as fact.
EVIDENCE_KINDS = ("listing", "website", "segment", "size", "decision_maker", "contact_email",
                  "contact_phone", "buying_signal", "exclusion", "note")

# The automated workers, stated truthfully. Nothing here runs yet; the owner
# screen shows exactly this rather than an invented health light.
WORKERS: List[Dict[str, str]] = [
    {"key": "discovery", "label": "Business discovery", "phase": "P2"},
    {"key": "research", "label": "Website research & evidence", "phase": "P2"},
    {"key": "contacts", "label": "Contact verification", "phase": "P2"},
    {"key": "outreach", "label": "Outreach drafting & sending", "phase": "P3"},
    {"key": "replies", "label": "Reply reading & classification", "phase": "P3"},
    {"key": "booking", "label": "Appointment booking", "phase": "P4"},
]


def jload(raw, default):
    if raw in (None, ""):
        return default
    try:
        v = json.loads(raw)
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


def jdump(v) -> str:
    return json.dumps(v)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat() + "Z"


def explicitly_enabled(org) -> bool:
    """Only an explicit key turns it on - a NULL legacy allow-list ("all
    features") is never an acquisition client."""
    if org is None or not getattr(org, "enabled_features", None):
        return False
    keys = jload(org.enabled_features, [])
    return isinstance(keys, list) and FEATURE in keys


def identity_ok(org) -> bool:
    """A capability never renames a business: only a SERVICE business (any
    supported service category) or an org that records no industry presents as
    an acquisition client. A funeral home, an energy company... never does."""
    from app.services.program_experience import NEUTRAL_INDUSTRIES, industry_of
    ind = industry_of(org)
    return ind in SERVICE_CATEGORY_KEYS or ind in NEUTRAL_INDUSTRIES


def acquisition_identity(org) -> bool:
    return org is not None and explicitly_enabled(org) and identity_ok(org)


class Ctx:
    """Who is asking, in which client organization, at what level."""

    def __init__(self, db, user, org, manager: bool, request):
        self.db = db
        self.user = user
        self.org = org
        self.org_id = org.id
        self.manager = manager
        self.request = request


def require_manager(ctx: Ctx):
    if not ctx.manager:
        raise HTTPException(403, "Owner access required")


def clean_list(values, allowed: Optional[List[str]] = None, limit: int = 60, width: int = 80) -> List[str]:
    out: List[str] = []
    for v in values or []:
        s = str(v or "").strip()[:width]
        if not s or s in out:
            continue
        if allowed is not None and s not in allowed:
            raise HTTPException(422, "Unknown value: %s" % s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def money(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        f = round(float(v), 2)
    except (TypeError, ValueError):
        raise HTTPException(422, "Amounts must be numbers")
    if f < 0 or f > 10_000_000:
        raise HTTPException(422, "Amount out of range")
    return f


# ── #452 Phase 5: delegated worker permissions (per client grant) ───────────
WORKER_PERMISSIONS = [
    ("view_queue", "See this client's work queue", "Prospects allocated to this client and their stages"),
    ("research", "Research & qualify", "Add prospects, record evidence and contacts, move research stages"),
    ("draft_outreach", "Draft outreach", "Preview emails"),
    ("send_outreach", "Send authorized outreach", "Send one approved email at a time (email only)"),
    ("review_replies", "Review replies", "Read responses and move prospects to Interested"),
    ("schedule", "Schedule appointments", "Book and reschedule on the client's real calendar"),
    ("followups", "Handle client follow-ups", "Work this client's follow-up requests"),
    ("view_results", "See client results", "Appointment outcomes, feedback and reports"),
    ("client_setup", "Client setup", "Edit the client's business profile and pause/resume (owner usually keeps this)"),
]
WORKER_PERMISSION_KEYS = [k for k, _, _ in WORKER_PERMISSIONS]
# Typical roles, offered as presets in the owner's UI. Nothing is granted by default.
WORKER_PRESETS = {
    "researcher": ["view_queue", "research"],
    "outreach": ["view_queue", "draft_outreach", "send_outreach"],
    "responder": ["view_queue", "review_replies", "followups"],
    "setter": ["view_queue", "review_replies", "schedule", "followups"],
    "general": ["view_queue", "research", "draft_outreach", "send_outreach", "review_replies", "schedule",
                "followups", "view_results"],
}
