"""Shared vocabulary for the Gold & Jewelry program: segments, fields, context.

Everything gold-and-jewelry-specific that is DATA lives here, so a second
gold buyer, jewelry store or coin dealer is a row (JewelryProfile) and never a
code path. Nothing in this module names a client.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization, User

FEATURE = "jewelry_program"
INDUSTRY = "jewelry"

# ── CUSTOMER SEGMENTS (Lead.custom_fields["jw_segments"]) ──────────────────
# "inactive" is NOT stored: it is computed from the last recorded visit, so a
# customer can never be both "inactive" and seen last week. Everything else is
# chosen by staff (or mapped from an import column).
SEGMENTS: List[Dict[str, str]] = [
    {"key": "gold_seller", "label": "Gold Sellers"},
    {"key": "jewelry_buyer", "label": "Jewelry Buyers"},
    {"key": "coin_collector", "label": "Coin Collectors"},
    {"key": "previous", "label": "Previous Customers"},
    {"key": "vip", "label": "VIP Customers"},
    {"key": "inactive", "label": "Inactive Customers", "computed": "true"},
    {"key": "general", "label": "General Customers"},
]
SEGMENT_KEYS = [s["key"] for s in SEGMENTS]
SEGMENT_LABEL = {s["key"]: s["label"] for s in SEGMENTS}
ASSIGNABLE_SEGMENTS = [k for k in SEGMENT_KEYS if k != "inactive"]
INACTIVE_DAYS = 180     # no recorded visit or transaction for this long

FOLLOW_UP_STATUSES = ["new", "follow_up_due", "contacted", "appointment_set",
                      "not_interested", "do_not_contact"]
FOLLOW_UP_LABEL = {"new": "New", "follow_up_due": "Follow-up due", "contacted": "Contacted",
                   "appointment_set": "Appointment set", "not_interested": "Not interested",
                   "do_not_contact": "Do not contact"}

ITEM_CATEGORIES = ["gold", "silver", "jewelry", "coins", "collectibles", "other_precious_metals"]
ITEM_CATEGORY_LABEL = {"gold": "Gold", "silver": "Silver", "jewelry": "Jewelry", "coins": "Coins",
                       "collectibles": "Collectibles", "other_precious_metals": "Other Precious Metals"}
ITEM_STATUSES = ["available", "sold", "unavailable"]

DEFAULT_APPOINTMENT_TYPES = ["Gold Evaluation", "Jewelry Consultation", "Coin Collection Evaluation",
                             "General Store Visit", "Customer Follow-Up"]
APPOINTMENT_STATUSES = ["scheduled", "confirmed", "checked_in", "completed", "no_show", "cancelled"]
OPEN_APPOINTMENT = ("scheduled", "confirmed", "checked_in")

CAMPAIGN_KINDS = [
    {"key": "gold_buying", "label": "Gold Buying Weekend"},
    {"key": "reactivation", "label": "Previous Customer Reactivation"},
    {"key": "jewelry_specials", "label": "Jewelry Specials"},
    {"key": "coin_alerts", "label": "Coin Collector Alerts"},
    {"key": "seasonal", "label": "Seasonal Promotion"},
    {"key": "vip", "label": "VIP Customer Offer"},
    {"key": "custom", "label": "Custom"},
]
CAMPAIGN_KIND_KEYS = [k["key"] for k in CAMPAIGN_KINDS]

CF_SEGMENTS = "jw_segments"
CF_INTERESTS = "jw_interests"
CF_FOLLOW_UP = "jw_follow_up"
CF_FOLLOW_UP_AT = "jw_follow_up_at"
CF_LAST_VISIT = "jw_last_visit"

# Registered on Organization.crm_custom_fields at setup, which is what lets the
# platform's universal importer and lead screens recognise them.
FIELDS: List[Dict[str, Any]] = [
    {"key": CF_SEGMENTS, "label": "Customer segments", "type": "multi", "options": ASSIGNABLE_SEGMENTS},
    {"key": CF_INTERESTS, "label": "Customer interests", "type": "list"},
    {"key": CF_FOLLOW_UP, "label": "Follow-up status", "type": "choice", "options": FOLLOW_UP_STATUSES},
    {"key": CF_LAST_VISIT, "label": "Last store visit", "type": "date"},
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


def local_now(org: Optional[Organization]) -> datetime:
    """The store's wall-clock time (naive), from the organization's timezone."""
    tz = (getattr(org, "timezone", None) or "America/New_York").strip()
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)).replace(tzinfo=None, second=0, microsecond=0)
    except Exception:  # noqa: BLE001 - an unknown zone falls back to Eastern
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None, second=0, microsecond=0)


def custom(lead: Lead) -> Dict[str, Any]:
    cf = jload(lead.custom_fields, {})
    return cf if isinstance(cf, dict) else {}


def set_custom(lead: Lead, **values) -> None:
    cf = custom(lead)
    for k, v in values.items():
        if v is None:
            cf.pop(k, None)
        else:
            cf[k] = v
    lead.custom_fields = jdump(cf)


def lead_name(lead: Lead) -> str:
    n = " ".join(p for p in (lead.first_name, lead.last_name) if p).strip()
    return n or lead.phone or lead.email or "Customer"


def clean_list(values, limit: int = 30, size: int = 60) -> List[str]:
    out, seen = [], set()
    for v in values or []:
        s = str(v or "").strip()[:size]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:limit]


class Ctx:
    """Who is asking, in which gold & jewelry business, at what level.

    manager - owner/admin or manager of THIS workspace (lead_scope); sees every
              customer and may run campaigns, imports, exports and settings.
    staff   - any other member; sees the customers assigned to them (lead
              scope) and the appointments they are assigned to.
    owner   - org_admin / super_admin (or the platform owner): settings and team.
    """

    def __init__(self, db: Session, user: User, org: Organization, manager: bool, owner: bool, request):
        self.db = db
        self.user = user
        self.org = org
        self.org_id = org.id
        self.manager = manager
        self.owner = owner
        self.request = request


def explicitly_enabled(org: Optional[Organization]) -> bool:
    """The program is never implied: a NULL allow-list (legacy "everything")
    is not a gold & jewelry business. Only the explicit key turns it on."""
    if org is None or not org.enabled_features:
        return False
    keys = jload(org.enabled_features, [])
    return isinstance(keys, list) and FEATURE in keys


def require_manager(ctx: Ctx):
    if not ctx.manager:
        raise HTTPException(403, "Owner or manager access required")


def require_owner(ctx: Ctx):
    if not ctx.owner:
        raise HTTPException(403, "Owner or admin access required")
