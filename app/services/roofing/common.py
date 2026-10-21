"""Shared vocabulary for the roofing program: stages, fields, context, helpers.

Everything roofing-specific that is DATA lives here, so a second roofing
company is a row (RoofingProfile) and never a code path.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization, User

FEATURE = "roofing_program"

# ── THE JOURNEY ────────────────────────────────────────────────────────────
# Ordered. `rank` is how far along a homeowner is; LOST sits outside the
# order (a homeowner can be lost from any stage) and WON is the end.
IMPORTED = "imported"
QUALIFIED = "qualified"
CONTACTED = "contacted"
ENGAGED = "engaged"
OFFERED = "inspection_offered"
BOOKED = "inspection_booked"
INSPECTED = "inspected"
ESTIMATE_SENT = "estimate_sent"
WON = "won"
LOST = "lost"

STAGES: List[Dict[str, str]] = [
    {"key": IMPORTED, "label": "Imported", "hint": "In the system, not yet checked for outreach"},
    {"key": QUALIFIED, "label": "Qualified", "hint": "Passed the send checks on at least one channel"},
    {"key": CONTACTED, "label": "Contacted", "hint": "At least one text or email went out"},
    {"key": ENGAGED, "label": "Engaged", "hint": "The homeowner replied (not an opt-out)"},
    {"key": OFFERED, "label": "Inspection Offered", "hint": "Inspection times were offered"},
    {"key": BOOKED, "label": "Inspection Booked", "hint": "An inspection is on the calendar"},
    {"key": INSPECTED, "label": "Inspected", "hint": "The roof was inspected"},
    {"key": ESTIMATE_SENT, "label": "Estimate Sent", "hint": "The estimate is with the homeowner"},
    {"key": WON, "label": "Won", "hint": "Job sold"},
    {"key": LOST, "label": "Lost", "hint": "Closed without a job"},
]
STAGE_KEYS = [s["key"] for s in STAGES]
STAGE_LABEL = {s["key"]: s["label"] for s in STAGES}
ORDER = [IMPORTED, QUALIFIED, CONTACTED, ENGAGED, OFFERED, BOOKED, INSPECTED, ESTIMATE_SENT, WON]
RANK = {k: i for i, k in enumerate(ORDER)}
# Stages only a person can set - activity never moves a homeowner into or out
# of them (an inspection happening, an estimate going out, a sale).
MANUAL_STAGES = (INSPECTED, ESTIMATE_SENT, WON, LOST)

# ── ROOFING FIELDS (stored in Lead.custom_fields) ──────────────────────────
# Registered on the org's crm_custom_fields at setup, which is what makes the
# universal importer auto-map a "Roof Issue" column to custom:roof_issue and
# lets qualification rules test `custom_fields.urgency`. Property address is
# NOT here - it is Lead.street_address/city/state/zip_code; lead source is
# Lead.source / source_detail.
FIELDS: List[Dict[str, Any]] = [
    {"key": "roof_issue", "label": "Roof issue", "type": "text",
     "help": "What the homeowner reported (leak, hail damage, missing shingles...)"},
    {"key": "service_type", "label": "Repair or replacement", "type": "choice",
     "options": ["repair", "replacement", "not_sure"]},
    {"key": "inspection_interest", "label": "Inspection interest", "type": "choice",
     "options": ["yes", "maybe", "no", "unknown"]},
    {"key": "urgency", "label": "Urgency", "type": "choice",
     "options": ["emergency", "high", "normal", "low"]},
    {"key": "roof_age", "label": "Roof age (years)", "type": "number"},
    {"key": "insurance_claim", "label": "Insurance claim", "type": "text"},
]
FIELD_KEYS = [f["key"] for f in FIELDS]
CHOICES = {f["key"]: f["options"] for f in FIELDS if f.get("options")}

DEFAULT_INSPECTION_TYPES = ["Roof Inspection", "Storm Damage Assessment", "Insurance Claim Review",
                            "Estimate Appointment"]
DEFAULT_DURATION = 60

# The roofing follow-up sequence. Placeholders are filled by the EXISTING
# cadence renderer from the real lead, the real salesperson and the real
# organization - there is no fictional sender anywhere in it.
SEQUENCE_NAME = "Roofing Inspection Follow-Up"
SEQUENCE_TOUCHES = [
    {"touch_number": 1, "day_offset": 0, "send_hour": 10, "channel": "sms",
     "message_template": "Hi {first_name}, this is {advisor_name} with {org_name}. I'm following up "
                         "about your roofing needs. Are you still interested in having someone take a "
                         "look at your roof?"},
    {"touch_number": 2, "day_offset": 2, "send_hour": 11, "channel": "sms",
     "message_template": "Hi {first_name}, {advisor_name} again with {org_name}. A free inspection "
                         "takes about an hour and you'll know exactly where your roof stands. Want me "
                         "to find a time?"},
    {"touch_number": 3, "day_offset": 5, "send_hour": 10, "channel": "email",
     "subject_template": "Your roof inspection, {first_name}",
     "message_template": "Hi {first_name},\n\nI wanted to follow up about your roof. We can have "
                         "someone out for a no-cost inspection and walk you through what we find - "
                         "repair or replacement, no pressure either way.\n\nJust reply with a day "
                         "that works.\n\n{advisor_name}\n{org_name}"},
    {"touch_number": 4, "day_offset": 9, "send_hour": 15, "channel": "sms",
     "message_template": "Hi {first_name}, still happy to get your roof looked at. Reply YES and "
                         "I'll send a few open times."},
    {"touch_number": 5, "day_offset": 20, "send_hour": 10, "channel": "sms",
     "message_template": "Hi {first_name}, this is my last follow-up for now from {org_name}. If "
                         "anything changes with your roof, just reply here."},
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
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat() + "Z"


def lead_name(lead: Lead) -> str:
    n = " ".join(p for p in (lead.first_name, lead.last_name) if p).strip()
    return n or lead.phone or lead.email or "Homeowner"


def lead_fields(lead: Lead) -> Dict[str, Any]:
    cf = jload(lead.custom_fields, {})
    if not isinstance(cf, dict):
        cf = {}
    return {k: cf.get(k) for k in FIELD_KEYS}


def address(lead: Lead) -> Optional[str]:
    line2 = " ".join(p for p in (lead.state, lead.zip_code) if p)
    parts = [p for p in (lead.street_address, lead.city, line2) if p]
    return ", ".join(parts) or None


def user_names(db: Session, org_id: str) -> Dict[str, str]:
    return {u.id: (u.full_name or u.email) for u in
            db.query(User).filter(User.organization_id == org_id).all()}


class Ctx:
    """Who is asking, in which roofing company, at what level."""

    def __init__(self, db: Session, user: User, org: Organization, manager: bool, request):
        self.db = db
        self.user = user
        self.org = org
        self.org_id = org.id
        self.manager = manager
        self.request = request


def explicitly_enabled(org: Optional[Organization]) -> bool:
    """Roofing is never implied. A legacy organization with enabled_features =
    NULL ("everything") is NOT a roofing company - a NULL allow-list means
    "not configured", and treating it as roofing would hand every old
    workspace a new product. Only an explicit key turns it on."""
    if org is None or not org.enabled_features:
        return False
    keys = jload(org.enabled_features, [])
    return isinstance(keys, list) and FEATURE in keys


def require_manager(ctx: Ctx):
    if not ctx.manager:
        raise HTTPException(403, "Owner or manager access required")
