"""COMPENSATION COMMAND CENTER — see what the rules produced, and settle it.

TWO SURFACES, ONE ENGINE, AND THIS IS NOT THE CONFIGURATION SCREEN.

    /god/pricing                 DEFINES the rules — plans, rates, caps, holdbacks
    /sales/compensation/*        SHOWS what those rules produced, and pays it

Nothing in this router decides what anybody earns. It reads
`CompensationEntry` rows and calls the existing engine; a second place that
computed a commission would eventually disagree with the first, and the one
question a compensation system must never fumble is which number is right.

AUTHORITY IS A CAPABILITY AND A BRAND, NOT A ROLE NAME
------------------------------------------------------
  MY COMPENSATION      any sales member, scoped to THEMSELVES by their token.
                       A rep reading their own deal is not a management report.
  VIEWING              `sales_comp_view` over that brand, OR being a sales
                       manager of it. Either is enough; neither implies the
                       other.
  RECORDING & PAYING   `sales_comp_manage` over that brand. Being a manager is
                       deliberately NOT sufficient.

WHY MANAGER IS NOT ENOUGH TO PAY. Running a team and authorising money leaving
the business are different jobs, and collapsing them means the only way to let
a finance clerk process a commission run is to hand them a sales team. A
manager who should also settle is granted `sales_comp_manage` explicitly, which
is one audited row rather than a role change.

This replaced a god-only settlement gate that existed because
`UserCapabilityGrant` could not be scoped to a brand at all. It can now — see
`capabilities.BRAND_SCOPED_CAPABILITIES`.

god_admin still passes every gate, as it does everywhere else.

BRAND ISOLATION IS IN THE QUERY. Every read starts from
`visible_brand_ids(db, user)`; a caller who holds no brand gets an empty
ledger, not an unfiltered one.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.compensation_models import (COMP_PAID, PAYEE_OVERRIDE,
                                            PAYEE_SELLER, CompensationEntry)
from app.models.models import User
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.routers.audit_log_router import log_action
from app.services import capabilities as caps
from app.services import compensation as comp
from app.services import compensation_ledger as ledger
from app.services.sales_access import (is_god, is_sales_manager,
                                       require_sales_member)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales/compensation", tags=["compensation"])


def _may_view_brand(db: Session, user: User, brand_sales_org_id: str) -> bool:
    """TWO INDEPENDENT AUTHORITIES OVER ONE BRAND'S COMPENSATION.

      the sales hierarchy   a manager of THIS brand sees their team's numbers
                            as part of running the team,
      `sales_comp_view`     granted over THIS brand — a finance or ops person,
                            who is not a manager and never becomes one.

    Either is sufficient; neither implies the other; both are per-brand.
    """
    return (is_god(user)
            or caps.has_brand_capability(db, user, brand_sales_org_id,
                                         "sales_comp_view")
            or is_sales_manager(user, db, brand_sales_org_id))


def _may_settle_brand(db: Session, user: User, brand_sales_org_id: str) -> bool:
    """WHO MAY MOVE MONEY FOR THIS BRAND.

    `sales_comp_manage`, and nothing else. Being a sales manager is
    deliberately NOT sufficient: running a team and authorising payments are
    different jobs, and the whole reason this capability exists is so one can
    be given without the other. A manager who should also settle payments is
    granted the capability — explicitly, and visibly in the audit log.
    """
    return (is_god(user)
            or caps.has_brand_capability(db, user, brand_sales_org_id,
                                         "sales_comp_manage"))


def _require_view_scope(db: Session, user: User,
                        brand_sales_org_id: Optional[str]) -> List[str]:
    """The brands whose compensation this caller may read.

    Asking for a specific brand they do not hold is a 403, not an empty list —
    an empty result reads as "this brand has no compensation", which is a
    different and much worse answer than "you may not see it".
    """
    if brand_sales_org_id:
        if not _may_view_brand(db, user, brand_sales_org_id):
            raise HTTPException(
                status_code=403,
                detail="You do not have compensation visibility for this brand.")
        return [brand_sales_org_id]

    visible = [b for b in ledger.visible_brand_ids(db, user)
               if _may_view_brand(db, user, b)]
    if not visible:
        raise HTTPException(
            status_code=403,
            detail="You do not have compensation visibility for any brand.")
    return visible


def _require_settle_scope(db: Session, user: User,
                          brand_ids: Iterable[str]) -> None:
    """Every brand touched by a settlement must be one this caller may settle.

    Checked across the WHOLE set before anything is written, so a payment run
    spanning two brands cannot half-succeed because authority ran out partway
    through it.
    """
    for brand_id in sorted(set(brand_ids)):
        if not _may_settle_brand(db, user, brand_id):
            raise HTTPException(
                status_code=403,
                detail="You do not have compensation settlement authority for "
                       "this brand. Nothing was paid.")


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
             user: User = Depends(get_current_user)):
    """Totals, upcoming liability, and who is owed what.

    Every headline here is the sum of rows `/ledger` returns under the same
    filters. If they could diverge, the screen would be untrustworthy the first
    time somebody checked — so both come from one scoped query.
    """
    scope = _require_view_scope(db, user, brand_sales_org_id)
    brands = [{"id": b.id, "name": b.name} for b in
              db.query(BrandSalesOrg).filter(BrandSalesOrg.id.in_(scope))
              .order_by(BrandSalesOrg.name.asc()).all()] if scope else []

    return {
        "summary": ledger.summary(db, user, brand_sales_org_id=brand_sales_org_id),
        # SEPARATE FROM EVERYTHING ELSE, ON PURPOSE. A projection is a forecast
        # from open deals; nothing in it is owed to anybody.
        "projected": ledger.projected(db, user, brand_sales_org_id=brand_sales_org_id),
        "payable_by_payee": ledger.by_payee(db, user, view=ledger.VIEW_PAYABLE_NOW,
                                            brand_sales_org_id=brand_sales_org_id),
        "brands": brands,
        # PER BRAND, because settlement authority is per brand. A user may
        # legitimately settle for one brand and only read another, and a single
        # boolean would have to lie about one of them.
        "settlement_brands": [b for b in scope if _may_settle_brand(db, user, b)],
        "can_process_payments": any(_may_settle_brand(db, user, b) for b in scope),
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
                        user: User = Depends(get_current_user)):
    """Every entry behind the totals, traceable to what produced it."""
    _require_view_scope(db, user, brand_sales_org_id)
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
                      user: User = Depends(get_current_user)):
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
    # RECORDING A COLLECTION CREATES MONEY OWED, so it takes the same authority
    # as settling it.
    #
    # THE STATUS CODE DEPENDS ON WHAT THE CALLER ALREADY KNOWS. A manager of
    # this brand can see this deal on half a dozen screens; answering 404 would
    # be a lie that sends them hunting for a missing record instead of telling
    # them they need settlement authority. Somebody who cannot even see the
    # brand gets 404, because confirming the deal exists is itself a
    # disclosure.
    if not _may_settle_brand(db, user, opp.brand_sales_org_id):
        if _may_view_brand(db, user, opp.brand_sales_org_id):
            raise HTTPException(
                status_code=403,
                detail="Recording a collected payment creates money owed and "
                       "requires compensation settlement authority for this "
                       "brand.")
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
                user: User = Depends(get_current_user)):
    """Move EARNED rows whose holdback has elapsed to PAYABLE.

    The ledger already REPORTS such a row as payable — see the note in
    `compensation_ledger` — so this changes what is stored, not what is shown.
    It exists because a payment run should settle rows whose durable state says
    payable, rather than rows a screen decided were payable at read time.
    """
    # SCOPED, even though it is a bulk operation. A brand's finance user
    # releasing their own holdbacks must not move another brand's rows just
    # because the call has no id in it.
    scope = [b for b in ledger.visible_brand_ids(db, user)
             if _may_settle_brand(db, user, b)]
    if not scope:
        raise HTTPException(
            status_code=403,
            detail="You do not have compensation settlement authority for any "
                   "brand.")
    moved = comp.promote_due_to_payable(db, commit=False,
                                        brand_sales_org_ids=scope)
    if moved:
        log_action(db, None, user.id, action="compensation.promoted_to_payable",
                   target_type="compensation", target_id="bulk",
                   before=None, after={"entries_promoted": moved,
                                       "brand_sales_org_ids": scope},
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
        user: User = Depends(get_current_user)):
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

    # SETTLEMENT AUTHORITY IS CHECKED ACROSS THE WHOLE SET BEFORE ANY WRITE.
    # Seeing a brand's ledger is not authority to pay it, and a run spanning
    # two brands must not half-succeed because authority ran out partway.
    _require_settle_scope(db, user, [r.brand_sales_org_id for r in rows])

    for r in rows:
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
             user: User = Depends(get_current_user)):
    """What a payment run would consist of, right now.

    One line per person with their entry ids, so the pay call settles exactly
    the rows this screen showed. There is no batch OBJECT here on purpose: a
    batch with its own lifecycle, approvals and reversals is an accounting
    product, and what is needed is operational control. The batch REFERENCE
    written onto each paid entry is what a bank transfer reconciles against.
    """
    scope = _require_view_scope(db, user, brand_sales_org_id)
    lines = ledger.by_payee(db, user, view=ledger.VIEW_PAYABLE_NOW,
                            brand_sales_org_id=brand_sales_org_id)
    return {
        "lines": lines,
        "total": round(sum((l["amount"] or 0) for l in lines), 2),
        "entry_count": sum(l["count"] for l in lines),
        "can_process_payments": any(_may_settle_brand(db, user, b) for b in scope),
    }
