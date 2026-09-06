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

    # ONE QUEUE, ONE DECIDE BUTTON. A custom-deal request is answered by the
    # same manager action on the same screen; only what gets written differs,
    # because a negotiated rate lands on the DEAL and an adjustment lands on a
    # DOCUMENT. Branching here rather than at the endpoint keeps a manager from
    # ever having to know which kind they are looking at.
    if req.request_kind == "custom_deal":
        if approve:
            res = approve_custom_deal(db, req, manager, decision_note=note or "",
                                      now=now)
        else:
            res = deny_custom_deal(db, req, manager, decision_note=note or "",
                                   now=now)
        res["applied"] = bool(approve and res.get("ok"))
        return res

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


# ═══════════════════════════════════════════════════════════════════════════
# CUSTOM-DEAL REQUESTS
#
# The same queue, a different question. A proposal-adjustment request asks
# "may I take X off this document?"; a custom-deal request asks "may I sell it
# on these terms?" - a rate, a minimum and a term that were negotiated together
# and only mean anything approved together.
#
# THE PROPOSED PRICING LIVES ON THE REQUEST, NOT ON THE DEAL. Writing it to the
# opportunity and flagging it "pending" would mean the pipeline, the proposal
# and the compensation projection all describe a deal nobody has agreed to. The
# opportunity keeps saying what is actually true until a manager decides.
# ═══════════════════════════════════════════════════════════════════════════

import json as _json

from app.models.sales_models import Opportunity as _Opportunity


def open_request_for_opportunity(db: Session, opportunity_id: str):
    """The single live request on a deal, whatever kind it is."""
    return (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.opportunity_id == opportunity_id,
                    PricingApprovalRequest.status == APPROVAL_PENDING)
            .order_by(PricingApprovalRequest.requested_at.desc())
            .first())


def request_custom_deal(db: Session, opp: _Opportunity, user, *,
                        proposed_unit_price=None, proposed_unit_label=None,
                        proposed_min_units=None, proposed_term_months=None,
                        proposed_billing_option=None,
                        proposed_implementation_fee=None,
                        reason: str = "", verdict: Optional[dict] = None,
                        now=None) -> dict:
    """Capture a below-floor negotiation as a question for a manager.

    Returns a serialisable dict rather than the row, because the caller raises
    it straight back to the browser inside a 409.

    Replaces any existing open request on the deal rather than stacking:
    approving one of two live asks silently contradicts the other.
    """
    now = now or datetime.utcnow()

    existing = open_request_for_opportunity(db, opp.id)
    if existing is not None:
        existing.status = APPROVAL_WITHDRAWN
        existing.decided_at = now
        existing.decision_note = "Replaced by a newer request from the same person."

    req = PricingApprovalRequest(
        brand_sales_org_id=opp.brand_sales_org_id,
        opportunity_id=opp.id,
        proposal_id=None,
        requested_by=user.id,
        requested_at=now,
        currency="USD",
        reason=(reason or "").strip() or "Negotiated pricing below the approved floor.",
        status=APPROVAL_PENDING,
        request_kind="custom_deal",
        requested_implementation_fee=_dec(proposed_implementation_fee),
        requested_unit_price=_dec(proposed_unit_price),
        requested_unit_label=(proposed_unit_label or None),
        requested_min_units=proposed_min_units,
        requested_term_months=proposed_term_months,
        requested_billing_option=proposed_billing_option,
        # Frozen at request time. Recomputing the breach when the manager opens
        # it would answer against whatever the catalogue says THEN, so a
        # catalogue edit in between would change what the rep appears to have
        # asked for.
        floor_breach_detail=_json.dumps(verdict or {}, default=str),
        created_at=now,
    )
    db.add(req)
    db.flush()

    ps._event(db, opp.id, "pricing_approval_requested",
              "Custom pricing approval requested",
              (verdict or {}).get("summary") or req.reason,
              user.id, now)
    db.commit()
    return {
        "id": req.id,
        "status": req.status,
        "requested_unit_price": _f(req.requested_unit_price),
        "requested_min_units": req.requested_min_units,
        "requested_term_months": req.requested_term_months,
        "reason": req.reason,
    }


def approve_custom_deal(db: Session, req: PricingApprovalRequest, manager,
                        decision_note: str = "", now=None) -> dict:
    """A manager agrees, and the agreed economics are written to the deal.

    THE MANAGER IS THE ACTOR. The write happens here with the manager's
    authority, not the rep's, which is what makes an approved below-floor price
    legitimate rather than a rep's write that somebody waved through afterwards.
    """
    now = now or datetime.utcnow()
    if req.status != APPROVAL_PENDING:
        return {"ok": False, "error": "That request has already been decided."}
    if req.request_kind != "custom_deal":
        return {"ok": False, "error": "That is not a custom-deal request."}

    opp = (db.query(_Opportunity)
           .filter(_Opportunity.id == req.opportunity_id).first())
    if opp is None:
        req.status = APPROVAL_STALE
        req.decided_at = now
        req.decision_note = "The deal no longer exists."
        db.commit()
        return {"ok": False, "error": "That deal no longer exists."}

    # Captured BEFORE the write, so the timeline row can say what it was.
    before_note = "%s x %s, %s months" % (
        opp.custom_unit_price, opp.custom_min_units, opp.custom_term_months)

    opp.custom_unit_price = req.requested_unit_price
    opp.custom_unit_label = req.requested_unit_label
    opp.custom_min_units = req.requested_min_units
    opp.custom_term_months = req.requested_term_months
    if req.requested_billing_option:
        opp.billing_option = req.requested_billing_option
    if req.requested_implementation_fee is not None:
        opp.implementation_fee = req.requested_implementation_fee

    req.status = APPROVAL_APPROVED
    req.decided_by = manager.id
    req.decided_at = now
    req.decision_note = (decision_note or "").strip() or None

    ps._event(db, opp.id, "pricing_approved",
              "Custom pricing approved",
              "Approved by manager. Was: %s. Now: %s x %s, %s months.%s" % (
                  before_note, req.requested_unit_price, req.requested_min_units,
                  req.requested_term_months,
                  (" Note: " + req.decision_note) if req.decision_note else ""),
              manager.id, now)
    db.commit()
    return {"ok": True, "error": None, "request": req}


def deny_custom_deal(db: Session, req: PricingApprovalRequest, manager,
                     decision_note: str = "", now=None) -> dict:
    """A manager refuses. NOTHING is written to the deal - it never was."""
    now = now or datetime.utcnow()
    if req.status != APPROVAL_PENDING:
        return {"ok": False, "error": "That request has already been decided."}
    req.status = APPROVAL_DENIED
    req.decided_by = manager.id
    req.decided_at = now
    req.decision_note = (decision_note or "").strip() or None
    ps._event(db, req.opportunity_id, "pricing_denied",
              "Custom pricing denied",
              req.decision_note or "No note given.", manager.id, now)
    db.commit()
    return {"ok": True, "error": None, "request": req}
