"""The wholesale pipeline — stages, and the rules about moving between them.

STAGES ARE DATA, NOT CODE. The default list below is what a new organization
gets; `WholesaleSettings.pipeline_stages` overrides it per customer. A deal
stores a stage KEY, so renaming a label never moves a deal and removing a stage
from the configured list never orphans one — `resolve_stages` always returns the
configured list, and `stage_label` falls back to the key for anything a deal is
sitting in that the list no longer names.

WHAT THIS MODULE REFUSES TO DO
------------------------------
It does not enforce a linear march. Real deals go backwards: a seller who went
quiet comes back, a contract falls through, a buyer walks. A state machine that
forbids that is a state machine people work around by editing the database.

What it DOES enforce is the small set of transitions with money or legal weight
behind them, and only those:

    offer_sent          needs an approved offer when the offer gate is on
    under_contract      needs an approved contract when the contract gate is on
    assignment_pending  needs an approved assignment when that gate is on

Those three checks live in `guard_transition` and nowhere else. Everything else
is a free move, audited like everything else.
"""

import json
from typing import Any, Dict, List, Optional


# ── The default stage list ──────────────────────────────────────────────────
#
# Keys are stable identifiers and are never shown to a user; labels are. Order
# is the order a board renders left to right. `terminal` marks the two states a
# deal stops in, which is what the dashboard's "open pipeline" counts exclude.
DEFAULT_STAGES: List[Dict[str, Any]] = [
    {"key": "new_property",       "label": "New Property",       "order": 10,  "terminal": False},
    {"key": "owner_identified",   "label": "Owner Identified",   "order": 20,  "terminal": False},
    {"key": "enrichment_needed",  "label": "Enrichment Needed",  "order": 30,  "terminal": False},
    {"key": "ready_for_outreach", "label": "Ready for Outreach", "order": 40,  "terminal": False},
    {"key": "outreach_active",    "label": "Outreach Active",    "order": 50,  "terminal": False},
    {"key": "seller_engaged",     "label": "Seller Engaged",     "order": 60,  "terminal": False},
    {"key": "qualifying",         "label": "Qualifying",         "order": 70,  "terminal": False},
    {"key": "qualified",          "label": "Qualified",          "order": 80,  "terminal": False},
    {"key": "analysis",           "label": "Analysis",           "order": 90,  "terminal": False},
    {"key": "offer_review",       "label": "Offer Review",       "order": 100, "terminal": False},
    {"key": "offer_sent",         "label": "Offer Sent",         "order": 110, "terminal": False},
    {"key": "negotiating",        "label": "Negotiating",        "order": 120, "terminal": False},
    {"key": "under_contract",     "label": "Under Contract",     "order": 130, "terminal": False},
    {"key": "disposition",        "label": "Disposition",        "order": 140, "terminal": False},
    {"key": "buyer_identified",   "label": "Buyer Identified",   "order": 150, "terminal": False},
    {"key": "assignment_pending", "label": "Assignment Pending", "order": 160, "terminal": False},
    {"key": "title_closing",      "label": "Title / Closing",    "order": 170, "terminal": False},
    {"key": "closed",             "label": "Closed",             "order": 180, "terminal": True},
    {"key": "dead",               "label": "Dead / Lost",        "order": 190, "terminal": True},
]

DEFAULT_STAGE_KEYS = tuple(s["key"] for s in DEFAULT_STAGES)

# The first stage a property enters. Named rather than "the one with the lowest
# order", because a customer who reorders their board must not thereby change
# where new properties land.
INITIAL_STAGE = "new_property"

# Stages that mean "this deal is dead" and "this deal closed". The dashboard and
# the fee report both need to know which is which, and inferring it from the
# label is how a renamed stage breaks a metric.
STAGE_CLOSED = "closed"
STAGE_DEAD = "dead"

# ── The three gated transitions ─────────────────────────────────────────────
# stage key -> (settings flag that turns the gate on, approval kind required)
GATED_TRANSITIONS: Dict[str, tuple] = {
    "offer_sent":         ("require_offer_approval", "offer"),
    "under_contract":     ("require_contract_approval", "contract"),
    "assignment_pending": ("require_assignment_approval", "assignment"),
}


def resolve_stages(settings: Any) -> List[Dict[str, Any]]:
    """The stage list for this organization. Never raises, never returns empty.

    A settings row with corrupt JSON in `pipeline_stages` falls back to the
    defaults rather than leaving a customer with no board at all — the same
    reading `entitlements.enabled_for` applies to a corrupt allow-list, in the
    safe direction for this question.
    """
    raw = getattr(settings, "pipeline_stages", None) if settings is not None else None
    if not raw:
        return [dict(s) for s in DEFAULT_STAGES]
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return [dict(s) for s in DEFAULT_STAGES]
    if not isinstance(parsed, list) or not parsed:
        return [dict(s) for s in DEFAULT_STAGES]
    out = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        key = (item.get("key") or "").strip()
        if not key:
            continue
        out.append({
            "key": key,
            "label": item.get("label") or key.replace("_", " ").title(),
            "order": item.get("order", (i + 1) * 10),
            "terminal": bool(item.get("terminal", key in (STAGE_CLOSED, STAGE_DEAD))),
        })
    if not out:
        return [dict(s) for s in DEFAULT_STAGES]
    out.sort(key=lambda s: s["order"])
    return out


def stage_keys(settings: Any) -> List[str]:
    return [s["key"] for s in resolve_stages(settings)]


def stage_label(settings: Any, key: Optional[str]) -> str:
    """A readable name for a stage key, including one no longer in the list."""
    if not key:
        return ""
    for s in resolve_stages(settings):
        if s["key"] == key:
            return s["label"]
    return key.replace("_", " ").title()


def is_terminal(settings: Any, key: Optional[str]) -> bool:
    for s in resolve_stages(settings):
        if s["key"] == key:
            return bool(s["terminal"])
    return key in (STAGE_CLOSED, STAGE_DEAD)


def validate_stage(settings: Any, key: str) -> str:
    """Return `key` if this organization has that stage, else raise ValueError.

    Callers turn the ValueError into a 400. Accepting an unknown stage would put
    a deal somewhere no board renders it, which is how a deal disappears.
    """
    keys = stage_keys(settings)
    if key not in keys:
        raise ValueError(
            "unknown stage %r for this organization. Configured stages: %s"
            % (key, ", ".join(keys)))
    return key


def guard_transition(settings: Any, deal: Any, to_stage: str,
                     approvals: List[Any]) -> Optional[str]:
    """Why this move is refused, or None when it is allowed.

    `approvals` is the deal's approval rows. An APPROVED approval of the right
    kind, created no earlier than the current analysis, opens the gate. The
    caller passes the rows rather than a session so this stays a pure function
    and the tests that matter most here need no database.

    Returns a sentence a person can act on, never a code.
    """
    gate = GATED_TRANSITIONS.get(to_stage)
    if gate is None:
        return None
    flag_name, kind = gate
    if not getattr(settings, flag_name, True):
        return None          # this organization switched the gate off, on purpose
    for a in approvals or []:
        if getattr(a, "kind", None) == kind and getattr(a, "status", None) == "approved":
            return None
    return (
        "This move needs an approved %s. Nothing is blocked permanently — open "
        "the Approval Center on this deal, review the numbers and approve or "
        "reject it, and the move goes through." % kind
    )
