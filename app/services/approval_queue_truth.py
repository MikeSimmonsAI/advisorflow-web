"""MANAGER APPROVAL QUEUE TRUTH - one pure decision, integer cents, no database.

READ DECISION + MUTATION PLAN ONLY. Nothing here writes, sends, charges or
calls a provider. The caller gathers already brand-scoped facts (see
`approval_queue_gather`) and this module decides, per pending request:

    actionable   a manager of THIS brand may approve/deny it right now
    blocked      still pending, but cannot be answered, with ONE precise reason
                 (proposal locked/missing, deal gone, facts incomplete, ...)

Resolved (approved/denied), withdrawn and stale requests are never in the
pending list; they are returned separately as history so they cannot inflate
the "waiting on you" count. A fact that is unavailable is None - never 0.

VERSION. `pricing_approval_requests` has no version column (no migration is
added here), so the optimistic-concurrency token is a hash of the request's
frozen facts (intrinsic part) plus the live facts the decision depends on
(live part: proposal status/pricing, deal status). A decision sent with a
token that no longer matches is a conflict (409) and writes nothing.

MONEY: integer cents only; malformed, NaN/Infinity, negative or above
MAX_CENTS inputs become None (unavailable), not zero.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

MAX_CENTS = 10 ** 13

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
WITHDRAWN = "withdrawn"
STALE = "stale"
RESOLVED = (APPROVED, DENIED)

KIND_PROPOSAL = "proposal_adjustment"
KIND_CUSTOM = "custom_deal"

# Blockers (pending but not answerable).
B_PROPOSAL_MISSING = "proposal_missing"
B_PROPOSAL_LOCKED = "proposal_locked"
B_DEAL_MISSING = "deal_missing"
B_DEAL_CLOSED = "deal_closed"
B_FACTS_INCOMPLETE = "request_facts_incomplete"
B_NEGATIVE_TOTAL = "would_make_total_negative"
B_NOT_MANAGER = "viewer_not_manager"

BLOCKER_TEXT = {
    B_PROPOSAL_MISSING: "The proposal this request is about no longer exists.",
    B_PROPOSAL_LOCKED: "The proposal is no longer editable, so a price cannot be applied. Ask the rep to create a new version.",
    B_DEAL_MISSING: "The deal this request is about no longer exists.",
    B_DEAL_CLOSED: "The deal is no longer open, so pricing cannot be changed.",
    B_FACTS_INCOMPLETE: "The request is missing figures needed to answer it.",
    B_NEGATIVE_TOTAL: "Applying this adjustment would make the proposal total negative.",
    B_NOT_MANAGER: "Only a sales manager of this brand can answer this request.",
}

# Mutation outcomes.
O_APPLY = "apply"
O_REPLAY = "replay"
O_CONFLICT = "conflict"        # 409
O_REFUSED = "refused"          # 400
O_NOT_FOUND = "not_found"      # 404 (also cross-brand / non-manager)


class QueueInputError(ValueError):
    pass


def to_cents(v: Any) -> Optional[int]:
    """Decimal/int/numeric string dollars -> integer cents, or None.

    Sub-cent, NaN/Infinity, bool, malformed and out-of-range are None
    (unavailable), never coerced.
    """
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    c = d * 100
    if c != c.to_integral_value():
        return None
    c = int(c)
    if abs(c) > MAX_CENTS:
        return None
    return c


def money(cents: Optional[int]) -> Optional[Dict[str, Any]]:
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    return {"cents": cents, "display": "%s%s.%02d" % (sign, format(a // 100, ","), a % 100)}


def _h(parts: List[Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def intrinsic_version(r: Dict[str, Any]) -> str:
    """Hash of what the rep actually asked; never changes after creation."""
    return _h([r.get("id"), r.get("brand_sales_org_id"), r.get("opportunity_id"),
               r.get("proposal_id"), r.get("kind"), r.get("requested_by"),
               str(r.get("requested_at")), to_cents(r.get("base_amount")),
               to_cents(r.get("current_adjustment")),
               to_cents(r.get("requested_adjustment")),
               to_cents(r.get("requested_unit_price")), r.get("requested_min_units"),
               r.get("requested_term_months"),
               to_cents(r.get("requested_implementation_fee")), r.get("reason")])


def live_version(r: Dict[str, Any]) -> str:
    """Hash of the live facts an answer depends on."""
    p = r.get("proposal") or {}
    o = r.get("opportunity") or {}
    return _h([r.get("status"), p.get("exists"), p.get("sales_status"),
               to_cents(p.get("base_amount")), to_cents(p.get("adjustment")),
               o.get("exists"), o.get("status")])


def version_token(r: Dict[str, Any]) -> str:
    return "v1.%s.%s" % (intrinsic_version(r), live_version(r))


def _kind(r: Dict[str, Any]) -> str:
    return KIND_CUSTOM if r.get("kind") == "custom_deal" else KIND_PROPOSAL


def _policy(r: Dict[str, Any]) -> Dict[str, Any]:
    """Frozen floor/policy evidence. Unavailable is stated, not blanked."""
    raw = r.get("floor_breach_detail")
    if _kind(r) == KIND_PROPOSAL:
        return {"available": False,
                "note": "Proposal adjustments carry no pricing-floor rule; manager authority applies."}
    if not raw:
        return {"available": False, "note": "No floor/policy evidence was recorded with this request."}
    try:
        v = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return {"available": False, "note": "Recorded floor/policy evidence could not be read."}
    if not isinstance(v, dict) or not (v.get("summary") or v.get("breaches") or v.get("ceilings")):
        return {"available": False, "note": "No floor/policy evidence was recorded with this request."}
    return {"available": True, "summary": v.get("summary"),
            "breaches": v.get("breaches") or [], "ceilings": v.get("ceilings") or {}}


def _money_block(r: Dict[str, Any]) -> Dict[str, Any]:
    p = r.get("proposal") or {}
    if _kind(r) == KIND_PROPOSAL:
        base = to_cents(r.get("base_amount"))
        cur = to_cents(r.get("current_adjustment"))
        ask = to_cents(r.get("requested_adjustment"))
        live_base, live_adj = to_cents(p.get("base_amount")), to_cents(p.get("adjustment"))
        return {
            "base": money(base),
            "current_adjustment": money(cur),
            "requested_adjustment": money(ask),
            "current_total": money(base + cur) if base is not None and cur is not None else None,
            "requested_total": money(base + ask) if base is not None and ask is not None else None,
            "live_total": (money(live_base + live_adj)
                           if live_base is not None and live_adj is not None else None),
        }
    price = to_cents(r.get("requested_unit_price"))
    units = r.get("requested_min_units")
    units = units if isinstance(units, int) and not isinstance(units, bool) and units >= 0 else None
    o = r.get("opportunity") or {}
    cur_price, cur_units = to_cents(o.get("custom_unit_price")), o.get("custom_min_units")
    cur_units = cur_units if isinstance(cur_units, int) and not isinstance(cur_units, bool) else None
    return {
        "requested_unit_price": money(price),
        "requested_min_units": units,
        "requested_term_months": r.get("requested_term_months"),
        "requested_monthly": money(price * units) if price is not None and units is not None else None,
        "requested_implementation_fee": money(to_cents(r.get("requested_implementation_fee"))),
        "current_unit_price": money(cur_price),
        "current_monthly": (money(cur_price * cur_units)
                            if cur_price is not None and cur_units is not None else None),
    }


def blocker_for(r: Dict[str, Any], *, editable_statuses) -> Optional[str]:
    """First reason a PENDING request cannot be answered, else None."""
    o = r.get("opportunity") or {}
    if not o.get("exists"):
        return B_DEAL_MISSING
    if o.get("status") not in (None, "open"):
        return B_DEAL_CLOSED
    if _kind(r) == KIND_PROPOSAL:
        p = r.get("proposal") or {}
        if not p.get("exists"):
            return B_PROPOSAL_MISSING
        if p.get("sales_status") not in editable_statuses:
            return B_PROPOSAL_LOCKED
        base, ask = to_cents(r.get("base_amount")), to_cents(r.get("requested_adjustment"))
        if base is None or ask is None:
            return B_FACTS_INCOMPLETE
        if base + ask < 0:
            return B_NEGATIVE_TOTAL
    else:
        units, term = r.get("requested_min_units"), r.get("requested_term_months")
        if (to_cents(r.get("requested_unit_price")) is None
                or not isinstance(units, int) or isinstance(units, bool) or units < 1
                or not isinstance(term, int) or isinstance(term, bool) or term < 1):
            return B_FACTS_INCOMPLETE
    return None


def _sort_key(r: Dict[str, Any]):
    # Oldest first; id breaks ties so the order is total and repeatable.
    return (str(r.get("requested_at") or ""), str(r.get("id") or ""))


def item(r: Dict[str, Any], *, can_decide: bool, editable_statuses) -> Dict[str, Any]:
    o = r.get("opportunity") or {}
    blocker = blocker_for(r, editable_statuses=editable_statuses) if r.get("status") == PENDING else None
    if r.get("status") == PENDING and not can_decide:
        blocker = blocker or B_NOT_MANAGER
    pending = r.get("status") == PENDING
    return {
        "id": r.get("id"),
        "kind": _kind(r),
        "status": r.get("status"),
        "requested_by": r.get("requested_by"),
        "requested_by_name": r.get("requested_by_name"),
        "requested_at": r.get("requested_at"),
        "opportunity_id": r.get("opportunity_id"),
        "proposal_id": r.get("proposal_id"),
        "company_name": o.get("company_name"),
        "stage": o.get("stage"),
        "reason": r.get("reason"),
        "currency": r.get("currency") or "USD",
        "money": _money_block(r),
        "policy": _policy(r),
        "version": version_token(r),
        "actionable": bool(pending and blocker is None),
        "blocker": blocker,
        "blocker_text": BLOCKER_TEXT.get(blocker) if blocker else None,
        "decided_at": r.get("decided_at"),
        "decided_by_name": r.get("decided_by_name"),
        "decision_note": r.get("decision_note"),
    }


def decide_queue(*, brand_sales_org_id: str, requests: List[Dict[str, Any]],
                 viewer_is_manager: bool, editable_statuses,
                 history_limit: int = 10) -> Dict[str, Any]:
    """The one read decision. Rows from another brand are dropped, uncounted."""
    if not brand_sales_org_id:
        raise QueueInputError("brand_sales_org_id is required")
    scoped = [r for r in requests if r.get("brand_sales_org_id") == brand_sales_org_id]
    if not viewer_is_manager:
        # Defence in depth: the router already refuses non-managers. A caller
        # that gets here anyway sees nothing, not a degraded queue.
        return {"brand_sales_org_id": brand_sales_org_id, "authorized": False,
                "pending": [], "history": [], "pending_count": None,
                "actionable_count": None, "blocked_count": None}
    pend = sorted((r for r in scoped if r.get("status") == PENDING), key=_sort_key)
    items = [item(r, can_decide=True, editable_statuses=editable_statuses) for r in pend]
    hist = [r for r in scoped if r.get("status") in (APPROVED, DENIED, WITHDRAWN, STALE)]
    hist.sort(key=lambda r: (str(r.get("decided_at") or ""), str(r.get("id") or "")), reverse=True)
    history = [item(r, can_decide=True, editable_statuses=editable_statuses)
               for r in hist[:max(0, history_limit)]]
    n_act = sum(1 for i in items if i["actionable"])
    return {"brand_sales_org_id": brand_sales_org_id, "authorized": True,
            "pending": items, "history": history,
            "pending_count": len(items), "actionable_count": n_act,
            "blocked_count": len(items) - n_act}


def plan_decision(*, request: Optional[Dict[str, Any]], brand_sales_org_id: str,
                  actor_id: str, actor_is_manager: bool, approve: bool,
                  expected_version: Optional[str], editable_statuses) -> Dict[str, Any]:
    """What the mutation endpoint must do. Pure; the caller executes it.

    Order matters: authority and scope first (404, no existence leak), then
    version (409), then legality (400). Only O_APPLY may write.
    """
    if request is None or request.get("brand_sales_org_id") != brand_sales_org_id \
            or not actor_is_manager:
        return {"outcome": O_NOT_FOUND, "error": "Request not found"}
    if not expected_version:
        return {"outcome": O_REFUSED, "error": "expected_version is required."}

    want = DENIED if not approve else APPROVED
    status = request.get("status")
    intr = intrinsic_version(request)
    exp_parts = expected_version.split(".")
    exp_intr = exp_parts[1] if len(exp_parts) == 3 and exp_parts[0] == "v1" else None

    if status != PENDING:
        # Replay: the same manager already gave this very answer to this very
        # request. No write, same success; anything else is a conflict.
        if status == want and request.get("decided_by") == actor_id and exp_intr == intr:
            return {"outcome": O_REPLAY}
        return {"outcome": O_CONFLICT,
                "error": "That request is no longer pending (it is %s). Reload the queue." % status}
    if expected_version != version_token(request):
        return {"outcome": O_CONFLICT,
                "error": "That request changed since you loaded it. Reload the queue."}
    if approve:
        b = blocker_for(request, editable_statuses=editable_statuses)
        if b:
            return {"outcome": O_REFUSED, "error": BLOCKER_TEXT[b], "blocker": b}
    return {"outcome": O_APPLY}
