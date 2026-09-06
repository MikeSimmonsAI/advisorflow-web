"""Sales Manager command workspace — Checkpoint 5.

  /sales/manager/overview            the whole screen, one batched call
  /sales/manager/reps/{user_id}      one rep's book, for the drill-down
  /sales/manager/approvals/{id}/decide   approve or deny a price request

EVERY route here is gated by `require_sales_manager`. That dependency is the
boundary, not the nav item — hiding a link is presentation, and a rep who types
the URL must get a 403 rather than a screen.

MANAGER IS NOT GOD. A sales manager runs a team inside one brand. Nothing in
this router reaches platform data, other brands, customer tenants, leads, or
billing. `_resolve_context` refuses a brand the caller does not hold, and every
query underneath filters on that one brand's id explicitly — never on "every
brand this caller can see", which for a god_admin is all of them.

NO NEW AUTHORITY. The only state-changing route is the approval decision, and
approving calls the same `apply_pricing()` a manager could always call by hand.
Everything else is a read.
"""

from __future__ import annotations

import logging
from datetime import datetime, date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import User
from app.models.sales_models import PricingApprovalRequest
from app.services.sales_access import require_sales_manager, is_sales_manager, is_god
from app.services import manager_workspace as _mw
from app.services import pricing_approvals as _appr
from app.routers.sales_router import _resolve_context

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sales/manager", tags=["sales-manager"])


@router.get("/overview")
def manager_overview(brand_sales_org_id: str = Query(None),
                     day: date = Query(None,
                                       description="Local date for Team Today; "
                                                   "defaults to today in the brand's timezone"),
                     db: Session = Depends(get_db),
                     user: User = Depends(require_sales_manager)):
    """The command screen. One request, one spinner, one consistent moment.

    Splitting this into six endpoints would give six loading states and six
    slightly different "now" values, so a meeting could appear in Team Today and
    be missing from the rep rollup drawn a second later.
    """
    org = _resolve_context(user, db, brand_sales_org_id)
    # Belt and braces: _resolve_context proves membership of the brand, this
    # proves the membership is a MANAGER one. A god_admin passes both.
    if not is_sales_manager(user, db, org.id):
        raise HTTPException(status_code=403,
                            detail="Sales manager access required for this brand.")
    data = _mw.overview(db, org, day=day)
    db.commit()          # sweep_stale may have closed dead approval requests
    return data


@router.get("/reps/{user_id}")
def manager_rep_detail(user_id: str,
                       brand_sales_org_id: str = Query(None),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_sales_manager)):
    """One rep's open book. Rows link into the existing Opportunity Detail."""
    org = _resolve_context(user, db, brand_sales_org_id)
    if not is_sales_manager(user, db, org.id):
        raise HTTPException(status_code=403,
                            detail="Sales manager access required for this brand.")
    return _mw.rep_detail(db, org, user_id)


@router.get("/approvals")
def manager_approvals(brand_sales_org_id: str = Query(None),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_sales_manager)):
    """The approval queue on its own, for polling without redrawing everything."""
    org = _resolve_context(user, db, brand_sales_org_id)
    if not is_sales_manager(user, db, org.id):
        raise HTTPException(status_code=403,
                            detail="Sales manager access required for this brand.")
    _appr.sweep_stale(db, org.id)
    pending = _appr.pending_for_brand(db, org.id)
    recent = _appr.recent_decided_for_brand(db, org.id, limit=8)
    db.commit()
    return {
        "pending": [_appr.request_out(db, r) for r in pending],
        "pending_count": len(pending),
        "recent": [_appr.request_out(db, r) for r in recent],
    }


@router.post("/approvals/{request_id}/decide")
def decide_approval(request_id: str, body: dict,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_sales_manager)):
    """Approve or deny. Approving applies the price as this manager.

    Cross-brand requests return 404, not 403 — a manager of brand A must not be
    able to learn that a request id in brand B exists.
    """
    req = (db.query(PricingApprovalRequest)
           .filter(PricingApprovalRequest.id == request_id).first())
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if not (is_god(user) or is_sales_manager(user, db, req.brand_sales_org_id)):
        raise HTTPException(status_code=404, detail="Request not found")

    approve = bool(body.get("approve"))
    note = body.get("note")
    res = _appr.decide(db, req, user, approve=approve, note=note)
    if not res.get("ok"):
        db.commit()      # a stale request is still closed, even on refusal
        raise HTTPException(status_code=400, detail=res.get("error"))
    db.commit()
    return {"ok": True, "applied": res.get("applied", False),
            "request": _appr.request_out(db, req)}


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE FINANCIAL PROJECTION
#
# A MANAGER SCREEN, GATED TWICE. `require_sales_manager` proves the caller runs
# a team; `is_sales_manager(brand)` proves it is THIS team. Compensation spans a
# whole brand's payroll, so the brand check is not optional here even though the
# manager guard already passed - that pair is the same one the rest of this
# router uses, and a request for another brand returns 403 rather than an empty
# projection that reads as "your team has no pipeline".
#
# Everything returned is PROJECTED. Nothing here is earned, payable or owed, and
# the payload says so in a field rather than relying on the screen to remember.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/pipeline-projection")
def pipeline_projection(brand_sales_org_id: str = Query(None),
                        owner_user_id: str = Query(None),
                        stage: str = Query(None),
                        include_deals: bool = Query(False),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_sales_manager)):
    """What the open pipeline is worth, and what it would cost in sales comp."""
    from app.models.sales_models import Opportunity as _Opp
    from app.services import pipeline_projection as _proj

    org = _resolve_context(user, db, brand_sales_org_id)
    if not is_sales_manager(user, db, org.id):
        raise HTTPException(status_code=403,
                            detail="Sales manager access required for this brand.")

    # OPEN deals only. A won or lost deal is not a forecast, and including won
    # ones is how a projection quietly starts double-counting revenue that has
    # already been recognised.
    q = (db.query(_Opp)
         .filter(_Opp.brand_sales_org_id == org.id,
                 _Opp.status == "open"))
    if owner_user_id:
        q = q.filter(_Opp.owner_user_id == owner_user_id)
    if stage:
        q = q.filter(_Opp.stage == stage)

    result = _proj.project(db, q.all(), brand_sales_org_id=org.id,
                           include_deals=include_deals)
    result["brand_sales_org_id"] = org.id
    result["filters"] = {"owner_user_id": owner_user_id, "stage": stage}
    return result
