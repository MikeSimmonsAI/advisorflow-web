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
# Single-company proof of concept: the owner cannot switch this on for a
# second organization without a code change (Issue #452, "do not expand the
# pilot prematurely").
PILOT_ORG_LIMIT = 1

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
# What the client sees: real interest and everything after it.
CLIENT_STAGES = (INTERESTED, BOOKED, HELD, NO_SHOW, QUOTED, WON, LOST)
# What the client may record herself, from which stages.
CLIENT_OUTCOMES = {
    HELD: (BOOKED, NO_SHOW),
    NO_SHOW: (BOOKED,),
    QUOTED: (BOOKED, HELD),
    WON: (HELD, QUOTED),
    LOST: (INTERESTED, BOOKED, HELD, NO_SHOW, QUOTED),
}

SEGMENTS: List[Dict[str, str]] = [
    {"key": "office", "label": "Office buildings & professional offices"},
    {"key": "medical_dental", "label": "Medical & dental offices"},
    {"key": "apartments", "label": "Apartment complexes"},
    {"key": "property_management", "label": "Property management companies"},
    {"key": "short_term_rental", "label": "Short-term rental managers"},
]
SEGMENT_KEYS = [s["key"] for s in SEGMENTS]
SEGMENT_LABEL = {s["key"]: s["label"] for s in SEGMENTS}

DEFAULT_SERVICE_AREA = ["Dallas", "Fort Worth", "Arlington", "Plano", "Irving", "Garland",
                        "Frisco", "McKinney", "Grand Prairie", "Denton", "Richardson", "Carrollton"]
DEFAULT_APPOINTMENT_MINUTES = 45
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
    """A capability never renames a business: only a cleaning company (or an
    org that records no industry) presents as the acquisition client."""
    from app.services.program_experience import identity_allows
    return identity_allows(org, IDENTITY)


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
