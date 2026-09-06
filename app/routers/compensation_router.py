"""COMPENSATION COMMAND CENTER — see what the rules produced, and settle it.

TWO SURFACES, ONE ENGINE, AND THIS IS NOT THE CONFIGURATION SCREEN.

    /god/pricing                 DEFINES the rules — plans, rates, caps, holdbacks
    /sales/compensation/*        SHOWS what those rules produced, and pays it

Nothing in this router decides what anybody earns. It reads
`CompensationEntry` rows and calls the existing engine; a second place that
computed a commission would eventually disagree with the first, and the one
question a compensation system must never fumble is which number is right.

THREE LEVELS OF AUTHORITY, DELIBERATELY DIFFERENT
-------------------------------------------------
  MY COMPENSATION      any sales member, scoped to themselves. A rep reading
                       their own deal is not a management report.
  TEAM / COMMAND       sales manager or god. Their brands only, never all.
  RECORDING & PAYING   god only, for now.

That last line is a DELIBERATE, TEMPORARY narrowing and it is worth naming.
"Can see the team's numbers" and "can move money" are genuinely different
authorities, and the capability registry already has `sales_comp_view` and
`sales_comp_manage` to express it — but `UserCapabilityGrant.organization_id`
is NOT NULL, and a brand-sales user has `organization_id = NULL`, so those
grants cannot currently be made to the very people who would hold them. Rather
than invent a parallel permission system, payment authority is god-only until
that table is scoped to a brand, and this comment is here so the next reader
knows it was a decision rather than an oversight.

BRAND ISOLATION IS IN THE QUERY. Every read starts from
`visible_brand_ids(db, user)`; a caller who holds no brand gets an empty
ledger, not an unfiltered one.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.compensation_models import (COMP_PAID, PAYEE_OVERRIDE,
                                            PAYEE_SELLER, CompensationEntry)
from app.models.models import User
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.routers.audit_log_router import log_action
from app.services import compensation as comp
from app.services import compensation_ledger as ledger
from app.services.sales_access import (is_god, is_sales_manager,
                                       require_sales_member)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales/compensation", tags=["compensation"])


def _require_manager_scope(db: Session, user: User,
                           brand_sales_org_id: Optional[str]) -> List[str]:
    """The brands whose TEAM compensation this caller may read.

    A manager runs a team inside one brand. Asking for a brand they do not hold
    is a 403, not an empty list — an empty result reads as "your team has no
    compensation", which is a different and much worse answer.
    """
    if not (is_god(user) or is_sales_manager(user, db)):
        raise HTTPException(
            status_code=403,
            detail="Sales manager access is required to view team compensation.")
    visible = ledger.visible_brand_ids(db, user)
    if brand_sales_org_id:
        if brand_sales_org_id not in visible or not is_sales_manager(
                db=db, user=user, brand_sales_org_id=brand_sales_org_id):
            raise HTTPException(
                status_code=403,
                detail="Sales manager access required for this brand.")
        return [brand_sales_org_id]
    return visible


# ═══════════════════════════════════════════════════════════════════════════
# MY COMPENSATION — a salesperson's own numbers, and nobody else's
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/me")
def my_compensation(db: Session = Depends(get_db),
                    user: User = Depends(require_sales_member)):
    """What I have projected, earned, on hold, payable and been paid.

    `payee_user_id` is taken from the TOKEN and is not a parameter. There is
    deliberately no way to ask this endpoint about somebody else: a rep reading
    another rep's commission is the disclosure this whole surface is arranged
    to prevent, and a filter that could be overridden by a query string is not
    a boundary.
    """
    return {
        "payee_user_id": user.id,
        "summary": ledger.summary(db, user, payee_user_id=user.id),
        "projected": ledger.projected(db, user, payee_user_id=user.id),
        "entries": ledger.entries(db, user, payee_user_id=user.id, limit=500),
        "scope": "self",
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE COMMAND CENTER — management
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/overview")
def overview(brand_sales_org_id: Optional[str] = Query(None),
             db: Session = Depends(get_db),
             user: User = Depends(require_sales_member)):
    """Totals, upcoming liability, and who is owed what.

    Every headline here is the sum of rows `/ledger` returns under the same
    filters. If they could diverge, the screen would be untrustworthy the first
    time somebody checked — so both come from one scoped query.
    """
    _require_manager_scope(db, user, brand_sales_org_id)
    brands = [{"id": b.id, "name": b.name} for b in
              db.query(BrandSalesOrg)
              .filter(BrandSalesOrg.id.in_(ledger.visible_brand_ids(db, user)))
              .order_by(BrandSalesOrg.name.asc()).all()] \
        if ledger.visible_brand_ids(db, user) else []

    return {
        "summary": ledger.summary(db, user, brand_sales_org_id=brand_sales_org_id),
        # SEPARATE FROM EVERYTHING ELSE, ON PURPOSE. A projection is a forecast
        # from open deals; nothing in it is owed to anybody.
        "projected": ledger.projected(db, user, brand_sales_org_id=brand_sales_org_id),
        "payable_by_payee": ledger.by_payee(db, user, view=ledger.VIEW_PAYABLE_NOW,
                                            brand_sales_org_id=brand_sales_org_id),
        "brands": brands,
        "can_process_payments": is_god(user),
        "vocabulary": {
            "views": [{"key": k, "label": v} for k, v in ledger.VIEW_LABELS.items()],
            "types": [{"key": PAYEE_SELLER, "label": "Direct seller commission"},
                      {"key": PAYEE_OVERRIDE, "label": "Manager / upline override"}],
        },
    }


@router.get("/ledger")
def compensation_ledger(brand_sales_org_id: Optional[str] = Query(None),
                        payee_user_id: Optional[str] = Query(None),
                        view: str = Query(ledger.VIEW_ALL),
                        compensation_kind: Optional[str] = Query(None),
                        limit: int = Query(500, ge=1, le=2000),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_sales_member)):
    """Every entry behind the totals, traceable to what produced it."""
    _require_manager_scope(db, user, brand_sales_org_id)
    if view not in ledger.VIEW_LABELS:
        raise HTTPException(status_code=400,
                            detail="Unknown view %r." % view)
    rows = ledger.entries(db, user, brand_sales_org_id=brand_sales_org_id,
                          payee_user_id=payee_user_id, view=view,
                          compensation_kind=compensation_kind, limit=limit)
    return {
        "entries": rows,
        # The total OF THIS EXACT SET, so a screen can prove its own arithmetic
        # without re-summing on the client and drifting.
        "total": round(sum((r["amount"] or 0) for r in rows), 2),
        "count": len(rows),
        "view": view,
    }


# ═══════════════════════════════════════════════════════════════════════════
# RECORDING A COLLECTED PAYMENT — the only thing that creates money owed
# ═══════════════════════════════════════════════════════════════════════════

class CollectionIn(BaseModel):
    collection_reference: str
    collected_amount: float
    collected_at: Optional[datetime] = None


@router.post("/opportunities/{opportunity_id}/record-collection")
def record_collection(opportunity_id: str, body: CollectionIn,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)):
    """A customer payment arrived. Turn the projection into money owed.

    THIS IS THE MISSING LINK, AND IT DELIBERATELY ADDS NO RULES. Everything
    that decides whether anything is earned — the deal must be Won, the amount
    must be positive, a plan must exist, a manager override only where an
    eligible manager does — already lives in `compensation.earn()`, which this
    calls. All that was missing was a way for a human to tell the system that
    funds actually landed.

    IDEMPOTENT BY CONSTRUCTION. `earn()` is keyed on
    (opportunity, payee, level, collection_reference), so recording the same
    payment twice returns the same rows rather than paying anybody twice. That
    makes a retried request, a double-click and two operators doing the same
    thing all safe.
    """
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    before = (db.query(CompensationEntry)
              .filter(CompensationEntry.opportunity_id == opp.id).count())
    try:
        made = comp.earn(db, opp,
                         collection_reference=body.collection_reference,
                         collected_amount=body.collected_amount,
                         collected_at=body.collected_at, commit=False)
    except comp.NotEarnable as e:
        # A refusal, not a failure. The caller asked for something the business
        # rules forbid and is owed the reason in words.
        raise HTTPException(status_code=409, detail=str(e))

    log_action(db, None, user.id, action="compensation.collection_recorded",
               target_type="opportunity", target_id=opp.id,
               before={"entry_count": before},
               after={"entry_count": len(made),
                      "collection_reference": body.collection_reference,
                      "collected_amount": body.collected_amount},
               note="Compensation earned from a collected customer payment.",
               commit=False)
    db.commit()

    return {
        "opportunity_id": opp.id,
        "entries_for_this_collection": len(made),
        "entries": ledger.entries(db, user, limit=200),
    }


@router.post("/promote-due")
def promote_due(db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Move EARNED rows whose holdback has elapsed to PAYABLE.

    The ledger already REPORTS such a row as payable — see the note in
    `compensation_ledger` — so this changes what is stored, not what is shown.
    It exists because a payment run should settle rows whose durable state says
    payable, rather than rows a screen decided were payable at read time.
    """
    moved = comp.promote_due_to_payable(db, commit=False)
    if moved:
        log_action(db, None, user.id, action="compensation.promoted_to_payable",
                   target_type="compensation", target_id="bulk",
                   before=None, after={"entries_promoted": moved},
                   note="Holdback elapsed.", commit=False)
    db.commit()
    return {"promoted": moved}


# ═══════════════════════════════════════════════════════════════════════════
# PAYING
# ═══════════════════════════════════════════════════════════════════════════

class PayIn(BaseModel):
    entry_ids: List[str]
    payment_reference: str
    payment_method: Optional[str] = None
    payment_note: Optional[str] = None
    # One reference across a week's entries, so a bank transfer can be
    # reconciled to the commissions it settled. See the column's comment.
    payment_batch_reference: Optional[str] = None
    paid_at: Optional[datetime] = None


@router.post("/pay")
def pay(body: PayIn, db: Session = Depends(get_db),
        user: User = Depends(require_god)):
    """Settle a set of payable entries, capturing what proves it happened.

    ALL OR NOTHING. Every entry is checked before any is written, and a single
    refusal aborts the whole call — a partial payment run where some rows moved
    and some did not is the worst possible outcome, because nobody can tell
    afterwards which cheques were actually sent.

    A row that is already PAID raises rather than being skipped. Silently
    ignoring it would let a second run report success over money that had
    already gone out.
    """
    if not body.entry_ids:
        raise HTTPException(status_code=400, detail="No entries selected.")
    if not (body.payment_reference or "").strip():
        raise HTTPException(status_code=400,
                            detail="A payment reference is required.")

    visible = ledger.visible_brand_ids(db, user)
    rows = (db.query(CompensationEntry)
            .filter(CompensationEntry.id.in_(body.entry_ids)).all())
    found = {r.id for r in rows}
    missing = [i for i in body.entry_ids if i not in found]
    if missing:
        raise HTTPException(status_code=404,
                            detail="No such compensation entry: %s" % ", ".join(missing))

    for r in rows:
        if r.brand_sales_org_id not in visible:
            # 404 rather than 403: confirming an entry exists in a brand this
            # caller may not see is itself a disclosure.
            raise HTTPException(status_code=404,
                                detail="No such compensation entry: %s" % r.id)
        if r.state == COMP_PAID:
            raise HTTPException(
                status_code=409,
                detail="Entry %s was already paid on %s under reference %r. "
                       "Nothing was paid." % (r.id, r.paid_at, r.payment_reference))

    paid_at = body.paid_at or datetime.utcnow()
    total = 0.0
    for r in rows:
        try:
            comp.mark_paid(db, r, payment_reference=body.payment_reference,
                           paid_at=paid_at, paid_by=user.id,
                           payment_method=body.payment_method,
                           payment_note=body.payment_note,
                           payment_batch_reference=body.payment_batch_reference,
                           commit=False)
        except comp.NotEarnable as e:
            db.rollback()
            raise HTTPException(status_code=409,
                                detail="%s Nothing was paid." % e)
        total += float(r.amount or 0)

    log_action(db, None, user.id, action="compensation.paid",
               target_type="compensation",
               target_id=(body.payment_batch_reference or body.payment_reference),
               before=None,
               after={"entry_ids": body.entry_ids, "total": round(total, 2),
                      "payment_reference": body.payment_reference,
                      "payment_method": body.payment_method,
                      "batch_reference": body.payment_batch_reference},
               note="Sales compensation settled.", commit=False)
    db.commit()

    return {"paid": len(rows), "total": round(total, 2),
            "payment_reference": body.payment_reference,
            "payment_batch_reference": body.payment_batch_reference}


# ═══════════════════════════════════════════════════════════════════════════
# WEEKLY PAYABLES — a view, not a batch product
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/payables")
def payables(brand_sales_org_id: Optional[str] = Query(None),
             db: Session = Depends(get_db),
             user: User = Depends(require_sales_member)):
    """What a payment run would consist of, right now.

    One line per person with their entry ids, so the pay call settles exactly
    the rows this screen showed. There is no batch OBJECT here on purpose: a
    batch with its own lifecycle, approvals and reversals is an accounting
    product, and what is needed is operational control. The batch REFERENCE
    written onto each paid entry is what a bank transfer reconciles against.
    """
    _require_manager_scope(db, user, brand_sales_org_id)
    lines = ledger.by_payee(db, user, view=ledger.VIEW_PAYABLE_NOW,
                            brand_sales_org_id=brand_sales_org_id)
    return {
        "lines": lines,
        "total": round(sum((l["amount"] or 0) for l in lines), 2),
        "entry_count": sum(l["count"] for l in lines),
        "can_process_payments": is_god(user),
    }
