"""Pricing approval requests — the asking half of an authority that already existed.

Checkpoint 5.

WHY THIS EXISTS. A rep has never been able to discount a proposal; a manager
always has. That rule is correct and is unchanged here. What was missing was any
record of a rep ASKING. The refusal string said "Ask your manager to apply the
adjustment", which is an instruction to leave the product and use Slack — so the
manager had no queue, no history, and no way to know who was blocked on them.

WHAT THIS IS NOT. It is not a second pricing system. An approval does not write
a price; it calls the same `proposal_service.apply_pricing()` a manager would
have called by hand, with the manager as the actor. The proposal's own
price_override_by/_at/_reason columns and the opportunity timeline remain the
only record of what the price is and who set it. A request row records only the
question and the answer. Two records of the same fact eventually disagree, and
the one nobody looks at is the one that goes stale.

A MANAGER CAN STILL ACT DIRECTLY. Nothing here makes a request mandatory. A
manager who wants to adjust a price opens the proposal and does it, exactly as
in Checkpoint 4. This path is for the person who cannot.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import Proposal, PROPOSAL_EDITABLE_STATUSES
from app.models.sales_models import (
    Opportunity, PricingApprovalRequest,
    APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_DENIED, APPROVAL_WITHDRAWN,
    APPROVAL_STALE, APPROVAL_LABELS,
)
from app.services import proposal_service as ps

log = logging.getLogger(__name__)


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _f(v):
    """Decimal -> float for JSON, preserving None."""
    return None if v is None else float(v)


def open_request_for(db: Session, proposal_id: str) -> Optional[PricingApprovalRequest]:
    """The single live request on a proposal, if any."""
    return (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.proposal_id == proposal_id,
                    PricingApprovalRequest.status == APPROVAL_PENDING)
            .order_by(PricingApprovalRequest.requested_at.desc())
            .first())


def request_out(db: Session, req: PricingApprovalRequest, include_names: bool = True) -> dict:
    """Serializer. Carries no pricing authority of its own — read only."""
    base = _dec(req.base_amount) or Decimal("0")
    asked = _dec(req.requested_adjustment) or Decimal("0")
    out = {
        "id": req.id,
        "opportunity_id": req.opportunity_id,
        "proposal_id": req.proposal_id,
        "status": req.status,
        "status_label": APPROVAL_LABELS.get(req.status, req.status),
        "currency": req.currency or "USD",
        "base_amount": _f(req.base_amount),
        "current_adjustment": _f(req.current_adjustment),
        "requested_adjustment": _f(req.requested_adjustment),
        "requested_total": _f(base + asked),
        "reason": req.reason,
        "requested_at": req.requested_at,
        "decided_at": req.decided_at,
        "decision_note": req.decision_note,
    }
    if include_names:
        from app.routers.sales_router import _user_name
        out["requested_by_name"] = _user_name(db, req.requested_by)
        out["decided_by_name"] = _user_name(db, req.decided_by) if req.decided_by else None
    return out


def create_request(db: Session, prop: Proposal, user, requested_adjustment,
                   reason: str, now=None) -> dict:
    """A rep asks for an adjustment they cannot apply.

    Returns {"ok", "error", "request"}. Never raises — the caller turns a
    failure into a 400 with the message shown to the rep verbatim.
    """
    now = now or datetime.utcnow()

    adj = _dec(requested_adjustment)
    if adj is None:
        return {"ok": False, "error": "That adjustment is not a valid amount.", "request": None}
    if adj == 0:
        return {"ok": False,
                "error": "That is the current price. Ask for the amount you want instead.",
                "request": None}

    reason = (reason or "").strip()
    if not reason:
        return {"ok": False,
                "error": "Say why you need this price. Your manager only sees what you write here.",
                "request": None}

    if prop.sales_status not in PROPOSAL_EDITABLE_STATUSES:
        return {"ok": False,
                "error": "This proposal is locked. Create a new version, then ask on that one.",
                "request": None}

    base = _dec(prop.base_amount) or Decimal("0")
    if base + adj < 0:
        return {"ok": False,
                "error": "That would make the total negative.", "request": None}

    existing = open_request_for(db, prop.id)
    if existing is not None:
        # Replacing rather than stacking: two live asks on one proposal is a
        # queue a manager cannot answer, because approving one silently
        # contradicts the other.
        existing.status = APPROVAL_WITHDRAWN
        existing.decided_at = now
        existing.decision_note = "Replaced by a newer request from the same person."

    req = PricingApprovalRequest(
        brand_sales_org_id=prop.brand_sales_org_id,
        opportunity_id=prop.opportunity_id,
        proposal_id=prop.id,
        requested_by=user.id,
        requested_at=now,
        base_amount=prop.base_amount,
        current_adjustment=prop.adjustment,
        requested_adjustment=adj,
        currency=prop.currency or "USD",
        reason=reason,
        status=APPROVAL_PENDING,
        created_at=now,
    )
    db.add(req)
    db.flush()

    ps._event(db, prop.opportunity_id, "pricing_approval_requested",
              "Price approval requested",
              "%s %s requested — %s" % (req.currency, adj, reason),
              user.id, now)
    return {"ok": True, "error": None, "request": req}


def withdraw_request(db: Session, req: PricingApprovalRequest, user, now=None) -> dict:
    """The rep changes their mind. Only the person who asked may withdraw."""
    now = now or datetime.utcnow()
    if req.status != APPROVAL_PENDING:
        return {"ok": False, "error": "That request has already been decided."}
    if req.requested_by != user.id:
        return {"ok": False, "error": "Only the person who asked can withdraw it."}
    req.status = APPROVAL_WITHDRAWN
    req.decided_at = now
    req.decided_by = user.id
    ps._event(db, req.opportunity_id, "pricing_approval_withdrawn",
              "Price approval withdrawn", None, user.id, now)
    return {"ok": True, "error": None}


def decide(db: Session, req: PricingApprovalRequest, manager, approve: bool,
           note: str = None, now=None) -> dict:
    """A manager answers. Approval APPLIES the pricing as the manager.

    The manager is recorded as the price override actor because the manager is
    who authorised it — attributing it to the rep would say a rep changed a
    price, which is the exact thing the authority model forbids. The rep's own
    words are carried into the override reason so the audit trail says who asked
    and why, not just who clicked.
    """
    now = now or datetime.utcnow()
    note = (note or "").strip() or None

    if req.status != APPROVAL_PENDING:
        return {"ok": False, "error": "That request has already been decided.",
                "applied": False}

    prop = (db.query(Proposal)
            .filter(Proposal.id == req.proposal_id,
                    Proposal.deleted_at.is_(None)).first())
    if prop is None:
        req.status = APPROVAL_STALE
        req.decided_at = now
        req.decision_note = "The proposal no longer exists."
        return {"ok": False, "error": "That proposal no longer exists.", "applied": False}

    if not approve:
        req.status = APPROVAL_DENIED
        req.decided_by = manager.id
        req.decided_at = now
        req.decision_note = note
        ps._event(db, req.opportunity_id, "pricing_approval_denied",
                  "Price approval denied", note, manager.id, now)
        return {"ok": True, "error": None, "applied": False}

    # Approving something that can no longer be applied must not report success.
    if prop.sales_status not in PROPOSAL_EDITABLE_STATUSES:
        req.status = APPROVAL_STALE
        req.decided_by = manager.id
        req.decided_at = now
        req.decision_note = ("The proposal moved on before this was answered "
                             "(it is now %s)." % (prop.sales_status or "locked"))
        return {"ok": False,
                "error": "This proposal is no longer editable, so the price was not "
                         "changed. Ask the rep to create a new version.",
                "applied": False}

    from app.routers.sales_router import _user_name
    asked_by = _user_name(db, req.requested_by) or "the representative"
    reason = "%s (requested by %s)" % (req.reason, asked_by)
    if note:
        reason = "%s — %s" % (reason, note)

    res = ps.apply_pricing(db, prop, manager,
                           adjustment=req.requested_adjustment,
                           reason=reason, now=now)
    if not res.get("ok"):
        # apply_pricing refused (negative total, cross-brand package, bad amount).
        # The request stays pending: the manager still owes an answer, and a
        # silently-swallowed refusal would look like an approval that did nothing.
        return {"ok": False, "error": res.get("error"), "applied": False}

    req.status = APPROVAL_APPROVED
    req.decided_by = manager.id
    req.decided_at = now
    req.decision_note = note
    ps._event(db, req.opportunity_id, "pricing_approval_approved",
              "Price approval granted", note, manager.id, now)
    return {"ok": True, "error": None, "applied": True}


def sweep_stale(db: Session, brand_sales_org_id: str, now=None) -> int:
    """Close requests whose proposal has moved beyond changing.

    Read-time housekeeping rather than a background job: the manager queue is
    the only place these are read, so cleaning them as it loads keeps the queue
    truthful without adding a scheduler that writes while nobody is watching.
    """
    now = now or datetime.utcnow()
    rows = (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.brand_sales_org_id == brand_sales_org_id,
                    PricingApprovalRequest.status == APPROVAL_PENDING)
            .all())
    if not rows:
        return 0

    prop_ids = [r.proposal_id for r in rows]
    props = {p.id: p for p in db.query(Proposal)
             .filter(Proposal.id.in_(prop_ids)).all()}

    closed = 0
    for r in rows:
        p = props.get(r.proposal_id)
        if p is None:
            r.status = APPROVAL_STALE
            r.decided_at = now
            r.decision_note = "The proposal no longer exists."
            closed += 1
        elif p.deleted_at is not None or p.sales_status not in PROPOSAL_EDITABLE_STATUSES:
            r.status = APPROVAL_STALE
            r.decided_at = now
            r.decision_note = ("The proposal moved on before this was answered "
                               "(it is now %s)." % (p.sales_status or "locked"))
            closed += 1
    return closed


def pending_for_brand(db: Session, brand_sales_org_id: str) -> List[PricingApprovalRequest]:
    """Oldest first — the person who has waited longest is answered first."""
    return (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.brand_sales_org_id == brand_sales_org_id,
                    PricingApprovalRequest.status == APPROVAL_PENDING)
            .order_by(PricingApprovalRequest.requested_at.asc())
            .all())


def recent_decided_for_brand(db: Session, brand_sales_org_id: str,
                             limit: int = 10) -> List[PricingApprovalRequest]:
    """What was decided lately — so a manager can see their own recent calls."""
    return (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.brand_sales_org_id == brand_sales_org_id,
                    PricingApprovalRequest.status.in_(
                        (APPROVAL_APPROVED, APPROVAL_DENIED)))
            .order_by(PricingApprovalRequest.decided_at.desc())
            .limit(limit).all())
