"""Shared vocabulary for the EvoSense engine. No database access beyond the
small helpers at the bottom, no vertical-specific names, no vendor names."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

log = logging.getLogger("evosense")

# ── Capabilities a provider may implement ───────────────────────────────────
PROPERTY_SEARCH = "PROPERTY_SEARCH"
PARCEL = "PARCEL"
ASSESSOR = "ASSESSOR"
OWNERSHIP = "OWNERSHIP"
TAX = "TAX"
FORECLOSURE = "FORECLOSURE"
PROBATE = "PROBATE"
CODE_VIOLATION = "CODE_VIOLATION"
VACANCY = "VACANCY"
LISTING = "LISTING"
VALUATION = "VALUATION"
COMPS = "COMPS"
CONTACT_ENRICHMENT = "CONTACT_ENRICHMENT"
PHONE_VALIDATION = "PHONE_VALIDATION"
EMAIL_VALIDATION = "EMAIL_VALIDATION"
ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
GEOCODING = "GEOCODING"

CAPABILITIES = (PROPERTY_SEARCH, PARCEL, ASSESSOR, OWNERSHIP, TAX, FORECLOSURE,
                PROBATE, CODE_VIOLATION, VACANCY, LISTING, VALUATION, COMPS,
                CONTACT_ENRICHMENT, PHONE_VALIDATION, EMAIL_VALIDATION,
                ENTITY_RESOLUTION, GEOCODING)
DISCOVERY_CAPABILITIES = (PROPERTY_SEARCH, VACANCY, TAX, FORECLOSURE, PROBATE,
                          CODE_VIOLATION, LISTING)

# ── What kind of connector produced a piece of data ─────────────────────────
REAL = "real"
SANDBOX = "sandbox"
MANUAL = "manual"
IMPORT = "import"
INTERFACE_ONLY = "interface_only"
NOT_BUILT = "not_built"
CONNECTOR_LABELS = {REAL: "REAL / CONNECTED", SANDBOX: "SANDBOX", MANUAL: "MANUAL",
                    IMPORT: "IMPORT", INTERFACE_ONLY: "INTERFACE ONLY",
                    NOT_BUILT: "NOT BUILT"}

# ── Provider health ─────────────────────────────────────────────────────────
H_CONNECTED = "CONNECTED"
H_NOT_CONNECTED = "NOT_CONNECTED"
H_DEGRADED = "DEGRADED"
H_RATE_LIMITED = "RATE_LIMITED"
H_DISABLED = "DISABLED"
H_MISSING_CREDENTIALS = "MISSING_CREDENTIALS"
H_BLOCKED = "BLOCKED"            # platform-wide: the public source refused our servers

# ── Truth states — every material value says which of these it is ───────────
T_KNOWN = "known"
T_SELLER_STATED = "seller_stated"
T_PROVIDER = "provider"
T_ESTIMATE = "estimate"
T_INFERRED = "inferred"
T_CONFLICTING = "conflicting"
T_STALE = "stale"
T_MISSING = "missing"
T_INSUFFICIENT = "insufficient_evidence"
T_HUMAN = "human_entered"
TRUTH_LABELS = {T_KNOWN: "KNOWN", T_SELLER_STATED: "SELLER STATED",
                T_PROVIDER: "PROVIDER REPORTED", T_ESTIMATE: "SYSTEM ESTIMATE",
                T_INFERRED: "INFERRED", T_CONFLICTING: "CONFLICTING",
                T_STALE: "STALE", T_MISSING: "MISSING",
                T_INSUFFICIENT: "INSUFFICIENT EVIDENCE", T_HUMAN: "HUMAN ENTERED"}

# ── Discovery inbox buckets (EvoSenseProperty.status) ───────────────────────
S_NEW = "new"
S_LOW = "low_opportunity"
S_HIGH = "high_opportunity"
S_NEEDS_ENRICHMENT = "needs_enrichment"
S_BUDGET_BLOCKED = "budget_blocked"
S_WAITING_DATA = "waiting_for_data"
S_CONTACT_FOUND = "contact_found"
S_READY = "ready_for_outreach"
S_OUTREACH = "outreach_active"
S_RESPONDED = "responded"
S_NURTURE = "nurture"
S_SUPPRESSED = "suppressed"
S_NEEDS_REVIEW = "needs_review"
S_NEEDS_YOU = "needs_you"
S_PROMOTED = "promoted"
S_CLOSED_OUT = "closed_out"
BUCKETS = [
    (S_NEW, "New"), (S_HIGH, "High opportunity"),
    (S_NEEDS_ENRICHMENT, "Needs enrichment"), (S_BUDGET_BLOCKED, "Budget blocked"),
    (S_WAITING_DATA, "Waiting for data"), (S_CONTACT_FOUND, "Contact found"),
    (S_READY, "Ready for outreach"), (S_OUTREACH, "Outreach active"),
    (S_RESPONDED, "Responded"), (S_NEEDS_YOU, "Needs you"), (S_NURTURE, "Nurture"),
    (S_SUPPRESSED, "Suppressed"), (S_NEEDS_REVIEW, "Needs review"),
    (S_PROMOTED, "Promoted"), (S_LOW, "Below threshold"), (S_CLOSED_OUT, "Closed out"),
]

# ── Seller response outcomes ────────────────────────────────────────────────
O_HARD_NO = "HARD_NO"
O_DNC = "DO_NOT_CONTACT"
O_WRONG_PERSON = "WRONG_PERSON"
O_NOT_NOW = "NOT_NOW"
O_CALL_LATER = "CALL_LATER"
O_PRICE_TOO_HIGH = "PRICE_TOO_HIGH"
O_ALREADY_SOLD = "ALREADY_SOLD"
O_LISTED = "LISTED_WITH_AGENT"
O_NOT_OWNER = "NOT_OWNER"
O_FAMILY = "FAMILY_DECIDING"
O_TENANT = "TENANT_ISSUE"
O_MAYBE = "MAYBE"
O_INTERESTED = "INTERESTED"
O_WANTS_OFFER = "WANTS_OFFER"
O_APPOINTMENT = "APPOINTMENT"
O_ESTATE = "ESTATE_HANDLING"
O_UNKNOWN = "UNKNOWN"
OUTCOMES = (O_HARD_NO, O_DNC, O_WRONG_PERSON, O_NOT_NOW, O_CALL_LATER, O_PRICE_TOO_HIGH,
            O_ALREADY_SOLD, O_LISTED, O_NOT_OWNER, O_FAMILY, O_TENANT, O_MAYBE,
            O_INTERESTED, O_WANTS_OFFER, O_APPOINTMENT, O_ESTATE, O_UNKNOWN)

# ── Enrichment decisions ────────────────────────────────────────────────────
D_NO_LOOKUP = "NO_LOOKUP_NEEDED"
D_USE_EXISTING = "USE_EXISTING_DATA"
D_FREE = "USE_FREE_SOURCE"
D_PAID = "PAID_LOOKUP_APPROVED"
D_APPROVAL = "APPROVAL_REQUIRED"
D_BUDGET = "BUDGET_BLOCKED"
D_SUPPRESSED = "SUPPRESSED"
D_INSUFFICIENT = "INSUFFICIENT_OPPORTUNITY"
D_RETRY = "RETRY_LATER"
DECISIONS = (D_NO_LOOKUP, D_USE_EXISTING, D_FREE, D_PAID, D_APPROVAL, D_BUDGET,
             D_SUPPRESSED, D_INSUFFICIENT, D_RETRY)

# ── Cost-governor lookup decisions (public-record lookups, any capability) ──
# These are the spec's vocabulary; contact-enrichment decisions above map onto
# them for display via GOVERNOR_LABEL.
L_SKIP_LOW_SCORE = "SKIP_LOW_SCORE"
L_SKIP_ALREADY_KNOWN = "SKIP_ALREADY_KNOWN"
L_SKIP_FRESH_DATA = "SKIP_FRESH_DATA"
L_SKIP_DUPLICATE = "SKIP_DUPLICATE_LOOKUP"
L_SKIP_BUDGET = "SKIP_BUDGET"
L_SKIP_PROVIDER_DOWN = "SKIP_PROVIDER_DOWN"
L_QUEUE_FREE = "QUEUE_FREE_LOOKUP"
L_QUEUE_PAID = "QUEUE_PAID_LOOKUP"
L_MANUAL_REVIEW = "MANUAL_REVIEW"
LOOKUP_DECISIONS = (L_SKIP_LOW_SCORE, L_SKIP_ALREADY_KNOWN, L_SKIP_FRESH_DATA, L_SKIP_DUPLICATE,
                    L_SKIP_BUDGET, L_SKIP_PROVIDER_DOWN, L_QUEUE_FREE, L_QUEUE_PAID,
                    L_MANUAL_REVIEW)
GOVERNOR_LABEL = {
    D_NO_LOOKUP: L_SKIP_ALREADY_KNOWN, D_USE_EXISTING: L_SKIP_ALREADY_KNOWN,
    D_FREE: L_QUEUE_FREE, D_PAID: L_QUEUE_PAID, D_APPROVAL: L_MANUAL_REVIEW,
    D_BUDGET: L_SKIP_BUDGET, D_SUPPRESSED: L_SKIP_ALREADY_KNOWN,
    D_INSUFFICIENT: L_SKIP_LOW_SCORE, D_RETRY: L_SKIP_PROVIDER_DOWN,
}
GOVERNOR_LABEL.update({d: d for d in LOOKUP_DECISIONS})

# ── Operator feedback ───────────────────────────────────────────────────────
FEEDBACK_KINDS = ("GOOD_FIND", "BAD_FIT", "WRONG_OWNER", "BAD_CONTACT",
                  "NOT_ACTUALLY_DISTRESSED", "HIGH_PRIORITY", "IGNORE")

ACTOR_USER = "user"
ACTOR_SYSTEM = "system"
ACTOR_AI = "ai"
ACTOR_AUTOMATION = "automation"


def now() -> datetime:
    from app.models.evosense_models import _now
    return _now()


def jdump(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str)


def jload(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def day_key(when: Optional[datetime] = None) -> str:
    return "d:" + (when or now()).strftime("%Y-%m-%d")


def month_key(when: Optional[datetime] = None) -> str:
    return "m:" + (when or now()).strftime("%Y-%m")


def money(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    return "$%s" % format(cents / 100.0, ",.2f")


def controls(db, org_id: str):
    """The organization's kill switches, created on first read."""
    from app.models.evosense_models import EvoSenseControl
    row = db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org_id).first()
    if row is None:
        row = EvoSenseControl(organization_id=org_id)
        db.add(row)
        db.flush()
    return row


def log_event(db, org_id: str, action: str, *, summary: str = None,
              property_id: str = None, strategy_id: str = None, actor_type: str = ACTOR_SYSTEM,
              user=None, details: Dict[str, Any] = None, is_test: bool = False):
    """Write EvoSense's own audit row; mirror to the platform audit log when a
    real person acted (same two-table rule as `wholesale_service.log_event`)."""
    from app.models.evosense_models import EvoSenseEvent
    ev = EvoSenseEvent(organization_id=org_id, property_id=property_id,
                       strategy_id=strategy_id, action=action, actor_type=actor_type,
                       actor_user_id=getattr(user, "id", None), summary=(summary or "")[:500],
                       details=jdump(details), is_test=bool(is_test))
    db.add(ev)
    if user is not None and getattr(user, "id", None):
        try:
            from app.routers.audit_log_router import log_action
            log_action(db, org_id, user.id, "evosense." + action,
                       "evosense_property" if property_id else "evosense",
                       property_id or strategy_id or org_id,
                       details=details, note=summary, commit=False)
        except Exception:  # noqa: BLE001 - audit mirror must never sink the action
            log.exception("evosense platform audit mirror failed for %s", action)
    return ev
