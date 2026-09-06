"""CUSTOMER 360 AND THE CUSTOMER LIFECYCLE — the platform owner's control plane.

EVERY ROUTE IS god_admin. `require_god` refuses everybody else with no
information, including super_admins and including a customer's own
administrators. That is not caution for its own sake: this surface carries one
customer's commercial terms, the identity of the person who sold them, and —
where asked for — platform payroll. A customer organization administrator
gaining any of that would be a cross-tenant disclosure, and there is deliberately
no route here that a tenant-scoped token can reach.

WHY A SEPARATE ROUTER FROM customers_router
-------------------------------------------
That router administers a customer: features, users, locations, activation. This
one answers questions ABOUT a customer and moves them through their life with
us. Keeping them apart means the lifecycle transitions — the only operations in
this system that can close a customer's doors — are in one file that can be read
end to end, rather than scattered among feature toggles.

CANCELLATION IS NOT DELETION, and permanent deletion lives at the bottom of this
file behind a refusal rather than a confirmation. See its docstring.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.customer_lifecycle_models import (
    CANCELLATION_REASON_LABELS, CANCELLATION_REASONS, CUSTOMER_OPEN_STATUSES,
    CUSTOMER_STATUS_LABELS, CUSTOMER_STATUSES, CustomerLifecycleEvent)
from app.models.implementation_models import Implementation
from app.models.models import Lead, Organization, Platform, User
from app.services import customer_360 as c360
from app.services import customer_lifecycle as lifecycle

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/customer-360", tags=["customer-360"])


def _org_or_404(db: Session, org_id: str) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return org


# ── reading ─────────────────────────────────────────────────────────────────

@router.get("/customers")
def list_customers(platform_id: Optional[str] = Query(None),
                   status: Optional[List[str]] = Query(None),
                   include_archived: bool = Query(False),
                   limit: int = Query(300, ge=1, le=1000),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_god)):
    """The customer list, status-first.

    ARCHIVED CUSTOMERS ARE EXCLUDED BY DEFAULT AND ARE NEVER UNREACHABLE. That
    is the whole meaning of archive here — filed out of the everyday view, one
    parameter away from being read. A cancelled customer, by contrast, is shown
    by default: they are recent history and hiding them is how churn becomes
    invisible.
    """
    statuses = list(status) if status else None
    if statuses is None and not include_archived:
        statuses = [s for s in CUSTOMER_STATUSES if s != "archived"]

    rows = c360.customer_rows(db, platform_id=platform_id, statuses=statuses,
                              limit=limit)

    platforms = [{"id": p.id, "name": p.name} for p in
                 db.query(Platform).order_by(Platform.name.asc()).all()]
    counts: Dict[str, int] = {}
    for r in c360.customer_rows(db, platform_id=platform_id, limit=1000):
        counts[r["lifecycle_status"]] = counts.get(r["lifecycle_status"], 0) + 1

    return {
        "customers": rows,
        "status_counts": counts,
        "platforms": platforms,
        "vocabulary": {
            "statuses": [{"key": s, "label": CUSTOMER_STATUS_LABELS[s]}
                         for s in CUSTOMER_STATUSES],
            "open_statuses": list(CUSTOMER_OPEN_STATUSES),
            "reasons": [{"key": r, "label": CANCELLATION_REASON_LABELS[r]}
                        for r in CANCELLATION_REASONS],
        },
    }


@router.get("/customers/{org_id}")
def customer_detail(org_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """One customer, whole.

    Compensation is included because this route is god-only and a platform
    owner asking what a customer is worth is asking what it cost to win them.
    It is passed as an explicit argument rather than defaulted inside the
    service, so that any future non-god caller has to decide deliberately.
    """
    org = _org_or_404(db, org_id)
    return c360.customer_360(db, org, include_compensation=True)


@router.get("/customers/{org_id}/offboarding-preview")
def offboarding_preview(org_id: str, db: Session = Depends(get_db),
                        user: User = Depends(require_god)):
    """What cancelling will and will not do, including what stays manual."""
    return lifecycle.offboarding_preview(db, _org_or_404(db, org_id))


# ── the transitions ─────────────────────────────────────────────────────────

class CancellationRequest(BaseModel):
    reason: Optional[str] = None
    effective_at: Optional[datetime] = None
    note: Optional[str] = None
    obligations_note: Optional[str] = None


@router.post("/customers/{org_id}/request-cancellation")
def request_cancellation(org_id: str, body: CancellationRequest,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_god)):
    """Record that a customer is leaving. Access is deliberately unchanged."""
    return lifecycle.request_cancellation(
        db, _org_or_404(db, org_id), user, reason=body.reason,
        effective_at=body.effective_at, note=body.note,
        obligations_note=body.obligations_note)


class NoteIn(BaseModel):
    note: Optional[str] = None


@router.post("/customers/{org_id}/start-offboarding")
def start_offboarding(org_id: str, body: NoteIn,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)):
    return lifecycle.start_offboarding(db, _org_or_404(db, org_id), user,
                                       note=body.note)


class CompleteIn(BaseModel):
    effective_at: Optional[datetime] = None
    note: Optional[str] = None


@router.post("/customers/{org_id}/complete-cancellation")
def complete_cancellation(org_id: str, body: CompleteIn,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_god)):
    """End the relationship and suspend the workspace. Deletes nothing."""
    return lifecycle.complete_cancellation(db, _org_or_404(db, org_id), user,
                                           effective_at=body.effective_at,
                                           note=body.note)


@router.post("/customers/{org_id}/archive")
def archive(org_id: str, body: NoteIn, db: Session = Depends(get_db),
            user: User = Depends(require_god)):
    return lifecycle.archive(db, _org_or_404(db, org_id), user, note=body.note)


@router.post("/customers/{org_id}/reactivate")
def reactivate(org_id: str, body: NoteIn, db: Session = Depends(get_db),
               user: User = Depends(require_god)):
    return lifecycle.reactivate(db, _org_or_404(db, org_id), user,
                                note=body.note)


# ═══════════════════════════════════════════════════════════════════════════
# PERMANENT DELETION
#
# THIS ROUTE MOSTLY EXISTS TO SAY NO, and that is the design rather than an
# apology for it.
#
# Cancelling a customer never needs deletion, and the ordinary way to remove a
# customer from view is ARCHIVE. What is left is the genuinely exceptional case:
# a test organization somebody created by accident, or a duplicate. Those have
# one thing in common — no commercial history — and that is exactly what this
# route checks.
#
# An organization with an implementation, an originating opportunity, a proposal
# or a single compensation entry is REFUSED, permanently and with no override
# flag. Deleting it would leave a commission row pointing at a deal whose
# customer no longer exists, or a proposal for nobody: orphaned financial
# records that no report can explain and no audit can reconstruct. The impact
# summary names what is blocking, and the honest alternative — archive — is
# offered in the same breath.
#
# There is deliberately no `force`. A flag that lets somebody past this check
# would be used, once, at the worst possible moment.
# ═══════════════════════════════════════════════════════════════════════════

def _deletion_impact(db: Session, org: Organization) -> Dict[str, Any]:
    from app.models.compensation_models import CompensationEntry
    from app.models.models import Proposal
    from app.models.sales_models import Opportunity

    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id).first())

    opp_id = impl.opportunity_id if impl else None
    proposals = (db.query(Proposal)
                 .filter(Proposal.organization_id == org.id).count())
    if opp_id:
        proposals += (db.query(Proposal)
                      .filter(Proposal.opportunity_id == opp_id).count())
    comp_entries = (db.query(CompensationEntry)
                    .filter(CompensationEntry.opportunity_id == opp_id).count()
                    if opp_id else 0)

    users = db.query(User).filter(User.organization_id == org.id).count()
    leads = db.query(Lead).filter(Lead.organization_id == org.id).count()
    events = (db.query(CustomerLifecycleEvent)
              .filter(CustomerLifecycleEvent.organization_id == org.id).count())

    # PROTECTED means "deleting this would orphan a financial or historical
    # record". User and lead counts are reported but do not block: they are the
    # customer's own operational data, and a genuine test organization has them
    # too.
    blockers: List[str] = []
    if impl is not None:
        blockers.append(
            "an implementation record, which carries the originating deal and "
            "the pricing agreed at the sale")
    if opp_id:
        blockers.append("an originating opportunity")
    if proposals:
        blockers.append("%d proposal%s" % (proposals, "" if proposals == 1 else "s"))
    if comp_entries:
        blockers.append("%d sales compensation record%s"
                        % (comp_entries, "" if comp_entries == 1 else "s"))

    return {
        "organization_id": org.id,
        "name": org.name,
        "lifecycle_status": c360.status_of(org),
        "counts": {
            "implementation": 1 if impl else 0,
            "opportunities": 1 if opp_id else 0,
            "proposals": proposals,
            "compensation_entries": comp_entries,
            "users": users,
            "leads": leads,
            "lifecycle_events": events,
        },
        "protected_relationships": blockers,
        "may_delete": not blockers,
        "refusal": (None if not blockers else
                    "This customer has %s. Deleting the organization would "
                    "leave those records pointing at a customer that no longer "
                    "exists. Archive preserves everything and removes them from "
                    "everyday views." % _join(blockers)),
        # Said plainly, because the operator's next question is always this one.
        "alternative": "Archive keeps every record and is reversible.",
    }


def _join(items: List[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


@router.get("/customers/{org_id}/deletion-impact")
def deletion_impact(org_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """Everything that points at this customer, and whether deletion is allowed."""
    return _deletion_impact(db, _org_or_404(db, org_id))


class PermanentDeleteIn(BaseModel):
    # The customer's own name, typed. Not a checkbox: the point is that the
    # operator has to look at which customer they are about to destroy.
    confirmation: str


@router.post("/customers/{org_id}/permanent-delete")
def permanent_delete(org_id: str, body: PermanentDeleteIn,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_god)):
    """Destroy an organization that has no commercial history. Rarely allowed.

    Refuses outright where any protected relationship exists — see the block
    comment above. There is no override.
    """
    org = _org_or_404(db, org_id)
    impact = _deletion_impact(db, org)

    if not impact["may_delete"]:
        raise HTTPException(status_code=409, detail=impact["refusal"])

    if (body.confirmation or "").strip() != (org.name or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Type the customer's name exactly (%s) to confirm." % org.name)

    from app.routers.audit_log_router import log_action

    # The audit row is written with the organization's id BEFORE the row goes,
    # and deliberately carries the whole impact summary: once the organization
    # is gone this entry is the only remaining evidence of what was destroyed.
    log_action(db, None, user.id, action="customer.permanently_deleted",
               target_type="organization", target_id=org.id,
               platform_id=org.platform_id,
               before={"name": org.name, "slug": org.slug,
                       "counts": impact["counts"]},
               after=None,
               note="No implementation, opportunity, proposal or compensation "
                    "record existed. Confirmed by name.",
               commit=False)

    # Children first, and only the ones this organization owns outright. A
    # protected relationship would have refused above, so nothing financial is
    # in scope here.
    db.query(CustomerLifecycleEvent).filter(
        CustomerLifecycleEvent.organization_id == org.id).delete(
            synchronize_session=False)
    db.query(Lead).filter(Lead.organization_id == org.id).delete(
        synchronize_session=False)
    db.query(User).filter(User.organization_id == org.id).delete(
        synchronize_session=False)
    db.delete(org)
    db.commit()

    return {"deleted": True, "organization_id": org_id,
            "destroyed": impact["counts"]}
