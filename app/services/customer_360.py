"""EVERYTHING KNOWN ABOUT ONE CUSTOMER, AND NOTHING GUESSED.

WHY THE OLD SCREEN SHOWED DASHES
--------------------------------
`/god/customers` read the package off `Implementation.package_id` and the
implementation status off the same row, and reported SOURCE as
`implementation is not None`. So an organization with no implementation showed
"—", "—" and "created outside the pipeline" — three dashes with ONE cause,
which is why they always appeared together. Nothing was broken; the screen was
asking one question and printing three answers.

The fix is not to fill those columns in from somewhere. It is to read the
relationships that already exist, and to say plainly when there are none.

WHAT ALREADY EXISTS, AND IS SIMPLY NOT BEING READ
-------------------------------------------------
`Implementation` is the crossing between the sales tree and the customer tree,
and it has carried every durable ID since Checkpoint 6 — `opportunity_id` and
`organization_id` (both UNIQUE, so the link is a database fact rather than a
match), `platform_id`, `brand_sales_org_id`, `package_id`,
`accepted_proposal_id`, `accepted_proposal_version`, `sold_by_user_id`.

It also carries the PRICING SNAPSHOT taken at provisioning: `implementation_fee`,
`recurring_amount`, `billing_option`, `contract_term_months`. That snapshot is
the historical commercial truth and is what this module reads. Re-quoting the
catalogue would mean a price rise in September silently rewriting what a
customer agreed to in March.

WHAT THIS MODULE REFUSES TO DO
------------------------------
It does not price anything. RCV and TCV come from
`package_pricing.contract_values()` — the same function a live quote uses — so
a customer record and a proposal cannot state the arithmetic differently.

It does not reconstruct a relationship. An organization with no implementation
row has no provable originating deal, and matching on company name would invent
one. Those are reported CREATED OUTSIDE PIPELINE and, where no figures survive,
COMMERCIAL DATA INCOMPLETE.

It does not assume implementation is finished because a workspace exists. The
implementation's own canonical status is read, and its absence is reported as
NO IMPLEMENTATION RECORD rather than as a dash.

NOTHING HERE IS BRAND-SPECIFIC. Every relationship is resolved through
`platform_id` / `brand_sales_org_id` on rows that already carry them, so a
second white-label brand appears in this view with no code change.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.customer_lifecycle_models import (CUST_ACTIVE,
                                                  CUSTOMER_STATUS_LABELS,
                                                  CANCELLATION_REASON_LABELS,
                                                  CustomerLifecycleEvent)
from app.models.implementation_models import Implementation
from app.models.models import Lead, Organization, Platform, Proposal, User
from app.models.sales_models import (BrandPackage, BrandSalesOrg, Membership,
                                     Opportunity, SCOPE_BRAND_SALES_ORG)
from app.services import package_pricing as pp

# What a customer's commercial record can be.
COMMERCIAL_FROM_SALE = "sale_snapshot"
COMMERCIAL_INCOMPLETE = "incomplete"

SOURCE_PIPELINE = "pipeline"
SOURCE_OUTSIDE = "outside_pipeline"

SOURCE_LABELS = {
    SOURCE_PIPELINE: "From pipeline",
    SOURCE_OUTSIDE: "Created outside pipeline",
}

# The structure of what they pay, named the same way the pipeline names it.
STRUCTURE_TERM = "fixed_term"
STRUCTURE_M2M = "month_to_month"
STRUCTURE_ONE_TIME = "one_time_only"
STRUCTURE_UNKNOWN = "unknown"

NO_IMPLEMENTATION = "no_implementation_record"


def _dec(v) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _f(v) -> Optional[float]:
    d = _dec(v)
    return None if d is None else float(d)


def status_of(org: Organization) -> str:
    """The customer's lifecycle status, NULL-safe.

    Every organization that existed before the column did was a live customer,
    so NULL reads as `active`. The opposite reading would cancel the whole book
    on the first boot after deploy.
    """
    return getattr(org, "lifecycle_status", None) or CUST_ACTIVE


# ── the commercial record ───────────────────────────────────────────────────

def commercials(impl: Optional[Implementation]) -> Dict[str, Any]:
    """What this customer bought, FROM THE SNAPSHOT TAKEN WHEN THEY BOUGHT IT.

    Never re-quoted from today's catalogue. `provision_customer` copies the fee,
    the monthly rate, the billing option and the term onto the implementation at
    the moment of the crossing precisely so that this answer stops moving, and
    reading the catalogue here would undo that on every page load.
    """
    if impl is None:
        return {
            "basis": COMMERCIAL_INCOMPLETE,
            "structure": STRUCTURE_UNKNOWN,
            "complete": False,
            "incomplete_reason": (
                "This organization has no implementation record, so there is no "
                "originating deal and no pricing snapshot. Nothing about what "
                "they bought can be proven from the platform's own data."),
            "setup": None, "mrr": None, "term_months": None,
            "recurring_contract_value": None, "total_contract_value": None,
            "billing_option": None, "currency": None,
        }

    setup = _dec(impl.implementation_fee)
    mrr = _dec(impl.recurring_amount)
    term = impl.contract_term_months or None
    option = impl.billing_option

    # A term only counts where the deal was actually sold on one. A stray term
    # on a month-to-month row must not manufacture a contract value.
    effective_term = term if option == pp.BILLING_TERM_AGREEMENT else None
    rcv, tcv = pp.contract_values(setup, mrr, effective_term)

    if rcv is not None:
        structure = STRUCTURE_TERM
    elif mrr is not None:
        structure = STRUCTURE_M2M
    elif setup is not None:
        structure = STRUCTURE_ONE_TIME
    else:
        structure = STRUCTURE_UNKNOWN

    complete = structure != STRUCTURE_UNKNOWN
    return {
        "basis": COMMERCIAL_FROM_SALE if complete else COMMERCIAL_INCOMPLETE,
        "structure": structure,
        "complete": complete,
        "incomplete_reason": (
            None if complete else
            "This customer came through the pipeline, but the sale recorded "
            "neither an implementation fee nor a monthly rate. The figures "
            "cannot be stated without inventing them."),
        "setup": _f(setup),
        "mrr": _f(mrr),
        # MONTH-TO-MONTH IS SAID IN WORDS, not left as an empty cell that reads
        # as missing data. It is a known structure, not an unknown one.
        "term_months": effective_term,
        "term_label": ("%d months" % effective_term if effective_term
                       else "Month-to-month" if structure == STRUCTURE_M2M
                       else None),
        "recurring_contract_value": _f(rcv),
        "total_contract_value": _f(tcv),
        "billing_option": option,
        "currency": impl.currency or "USD",
        "billing_status": impl.billing_status,
    }


# ── one customer ────────────────────────────────────────────────────────────

def _user_brief(db: Session, user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return None
    return {"id": u.id, "name": u.full_name or u.email, "email": u.email}


def _sales_manager_for(db: Session, seller_id: Optional[str],
                       brand_sales_org_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The seller's manager AT THIS BRAND, from the org chart.

    `reports_to_user_id` is an org-chart fact and explicitly not an
    authorization input; it is read here only to answer "whose number was this",
    which is the question a platform owner is actually asking.
    """
    if not seller_id or not brand_sales_org_id:
        return None
    m = (db.query(Membership)
         .filter(Membership.user_id == seller_id,
                 Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                 Membership.scope_id == brand_sales_org_id,
                 Membership.is_active.is_(True)).first())
    if m is None or not m.reports_to_user_id:
        return None
    return _user_brief(db, m.reports_to_user_id)


def lifecycle_history(db: Session, org_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(CustomerLifecycleEvent)
            .filter(CustomerLifecycleEvent.organization_id == org_id)
            .order_by(CustomerLifecycleEvent.created_at.desc()).all())
    actors = {}
    ids = {r.actor_user_id for r in rows if r.actor_user_id}
    if ids:
        for u in db.query(User).filter(User.id.in_(ids)).all():
            actors[u.id] = u.full_name or u.email
    return [{
        "id": r.id,
        "event": r.event,
        "from_status": r.from_status,
        "to_status": r.to_status,
        "effective_at": r.effective_at,
        "reason": r.reason,
        "reason_label": CANCELLATION_REASON_LABELS.get(r.reason, r.reason),
        "note": r.note,
        "obligations_note": r.obligations_note,
        "actor_name": actors.get(r.actor_user_id) or "—",
        "created_at": r.created_at,
    } for r in rows]


def customer_360(db: Session, org: Organization, *,
                 include_compensation: bool = False) -> Dict[str, Any]:
    """The whole picture for one customer.

    `include_compensation` is decided by the CALLER from the caller's authority,
    never from anything on this row. Compensation is platform payroll: a
    customer administrator must never receive it, and defaulting it on here
    would make that a one-line mistake in some future route.
    """
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id).first())
    platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if org.platform_id else None)

    opp = None
    proposal = None
    brand_sales_org = None
    package = None
    if impl is not None:
        if impl.opportunity_id:
            opp = (db.query(Opportunity)
                   .filter(Opportunity.id == impl.opportunity_id).first())
        if impl.accepted_proposal_id:
            proposal = (db.query(Proposal)
                        .filter(Proposal.id == impl.accepted_proposal_id).first())
        if impl.brand_sales_org_id:
            brand_sales_org = (db.query(BrandSalesOrg)
                               .filter(BrandSalesOrg.id == impl.brand_sales_org_id).first())
        if impl.package_id:
            package = (db.query(BrandPackage)
                       .filter(BrandPackage.id == impl.package_id).first())

    seller = _user_brief(db, impl.sold_by_user_id) if impl else None
    manager = (_sales_manager_for(db, impl.sold_by_user_id, impl.brand_sales_org_id)
               if impl else None)

    status = status_of(org)
    out = {
        "organization_id": org.id,
        "name": org.name,
        "slug": org.slug,
        "industry": org.industry,
        "plan": org.plan,

        # WHICH BRAND OWNS THEM. Read from the row, never assumed.
        "platform": ({"id": platform.id, "name": platform.name,
                      "slug": getattr(platform, "slug", None)}
                     if platform else None),

        # ── lifecycle ──
        "lifecycle_status": status,
        "lifecycle_status_label": CUSTOMER_STATUS_LABELS.get(status, status),
        # SEPARATE FROM STATUS ON PURPOSE. `is_active` says whether anybody can
        # walk into the workspace right now; the status says what the commercial
        # relationship is. A cancelled customer served through a notice period
        # is both cancelled and open, and one field cannot say that.
        "workspace_active": bool(org.is_active),
        "cancellation_requested_at": org.cancellation_requested_at,
        "cancellation_effective_at": org.cancellation_effective_at,
        "cancellation_reason": org.cancellation_reason,
        "cancellation_reason_label": CANCELLATION_REASON_LABELS.get(
            org.cancellation_reason, org.cancellation_reason),
        "cancellation_note": org.cancellation_note,
        "cancellation_requested_by": _user_brief(db, org.cancellation_requested_by),
        "cancelled_at": org.cancelled_at,
        "archived_at": org.archived_at,
        "reactivated_at": org.reactivated_at,

        # ── where they came from ──
        "source": SOURCE_PIPELINE if impl is not None else SOURCE_OUTSIDE,
        "source_label": SOURCE_LABELS[SOURCE_PIPELINE if impl is not None
                                      else SOURCE_OUTSIDE],
        "opportunity": ({"id": opp.id, "company_name": opp.company_name,
                         "stage": opp.stage, "status": opp.status,
                         "won_at": getattr(opp, "won_at", None)}
                        if opp else None),
        "proposal": ({"id": proposal.id,
                      "number": proposal.proposal_number,
                      "version": proposal.version,
                      "status": proposal.sales_status,
                      "sent_at": proposal.sent_at,
                      "accepted_at": proposal.accepted_at}
                     if proposal else None),
        # The version as RECORDED ON THE CROSSING, which is the one that
        # governed the sale even if the proposal row has since been superseded.
        "accepted_proposal_version": impl.accepted_proposal_version if impl else None,
        "sales_organization": ({"id": brand_sales_org.id,
                                "name": brand_sales_org.name}
                               if brand_sales_org else None),
        "sold_by": seller,
        "sales_manager": manager,
        "package": ({"id": package.id, "key": package.key, "name": package.name}
                    if package else None),
        "customer_since": (impl.launched_at or impl.created_at) if impl else org.created_at,

        # ── what they pay ──
        "commercials": commercials(impl),

        # ── delivery ──
        "implementation": ({"id": impl.id,
                            "status": impl.status,
                            "is_live": impl.is_live(),
                            "owner": _user_brief(db, impl.owner_user_id),
                            "target_launch_date": impl.target_launch_date,
                            "kickoff_at": impl.kickoff_at,
                            "launched_at": impl.launched_at,
                            "blocker_note": impl.blocker_note}
                           if impl else None),
        # Explicit, not a dash. "We have no record" is information.
        "implementation_state": impl.status if impl else NO_IMPLEMENTATION,

        "user_count": int(db.query(User)
                          .filter(User.organization_id == org.id).count()),
        "lead_count": int(db.query(Lead)
                          .filter(Lead.organization_id == org.id).count()),

        "lifecycle_history": lifecycle_history(db, org.id),
    }

    if include_compensation:
        out["compensation"] = _compensation(db, opp)
    return out


def _compensation(db: Session, opp: Optional[Opportunity]) -> Dict[str, Any]:
    """What this deal has cost, or will cost, in sales compensation.

    Reads the EXISTING engine. There is no second calculation here, and there
    is deliberately no fallback that estimates something when the engine
    declines to: an unconfigured plan reports unconfigured.
    """
    from app.models.compensation_models import (COMP_EARNED, COMP_PAID,
                                                COMP_PAYABLE, CompensationEntry)
    from app.services import compensation as comp

    if opp is None:
        return {"available": False,
                "reason": "No originating opportunity, so no deal to compensate."}

    entries = (db.query(CompensationEntry)
               .filter(CompensationEntry.opportunity_id == opp.id).all())
    by_state = {}
    for e in entries:
        by_state[e.state] = by_state.get(e.state, Decimal("0")) + (e.amount or Decimal("0"))

    projected = comp.compute(db, opp)
    return {
        "available": True,
        # PROJECTED is what the deal WOULD pay on today's plan; the rest are
        # rows that actually exist. Keeping them apart is the whole reason the
        # compensation engine has states.
        "projected": (_f(projected.get("total"))
                      if projected.get("total") is not None else None),
        "projected_status": projected.get("status"),
        "earned": _f(by_state.get(COMP_EARNED)),
        "payable": _f(by_state.get(COMP_PAYABLE)),
        "paid": _f(by_state.get(COMP_PAID)),
        "entry_count": len(entries),
    }


# ── the list ────────────────────────────────────────────────────────────────

def customer_rows(db: Session, *, platform_id: Optional[str] = None,
                  statuses: Optional[List[str]] = None,
                  limit: int = 300) -> List[Dict[str, Any]]:
    """The customer list, with the commercial and lifecycle facts on the row.

    Batched the same way the screen it replaces was: one query per related
    table for the whole page, never one per customer.
    """
    from app.services.platform_owner import exclude_platform_org
    from sqlalchemy import func

    q = exclude_platform_org(db.query(Organization))
    if platform_id:
        q = q.filter(Organization.platform_id == platform_id)
    orgs = (q.order_by(Organization.created_at.desc())
            .limit(max(1, min(limit, 1000))).all())

    # Status filtering happens in Python because `lifecycle_status` is NULL for
    # every pre-existing customer and NULL means active — a SQL IN() would drop
    # exactly the rows that matter most.
    if statuses:
        wanted = set(statuses)
        orgs = [o for o in orgs if status_of(o) in wanted]

    org_ids = sorted({o.id for o in orgs})
    impls, users, leads, platforms, packages, sellers = {}, {}, {}, {}, {}, {}
    if org_ids:
        for im in (db.query(Implementation)
                   .filter(Implementation.organization_id.in_(org_ids)).all()):
            impls.setdefault(im.organization_id, im)
        users = {str(k): int(n) for k, n in
                 db.query(User.organization_id, func.count(User.id))
                   .filter(User.organization_id.in_(org_ids))
                   .group_by(User.organization_id).all()}
        leads = {str(k): int(n) for k, n in
                 db.query(Lead.organization_id, func.count(Lead.id))
                   .filter(Lead.organization_id.in_(org_ids))
                   .group_by(Lead.organization_id).all()}
        plat_ids = sorted({o.platform_id for o in orgs if o.platform_id})
        if plat_ids:
            platforms = {p.id: p for p in
                         db.query(Platform).filter(Platform.id.in_(plat_ids)).all()}
        pkg_ids = sorted({im.package_id for im in impls.values() if im.package_id})
        if pkg_ids:
            packages = {p.id: p for p in
                        db.query(BrandPackage).filter(BrandPackage.id.in_(pkg_ids)).all()}
        seller_ids = sorted({im.sold_by_user_id for im in impls.values()
                             if im.sold_by_user_id})
        if seller_ids:
            sellers = {u.id: (u.full_name or u.email) for u in
                       db.query(User).filter(User.id.in_(seller_ids)).all()}

    out = []
    for o in orgs:
        impl = impls.get(o.id)
        platform = platforms.get(o.platform_id) if o.platform_id else None
        pkg = packages.get(impl.package_id) if impl and impl.package_id else None
        status = status_of(o)
        out.append({
            "organization_id": o.id,
            "name": o.name,
            "slug": o.slug,
            "platform": ({"id": platform.id, "name": platform.name}
                         if platform else None),
            "lifecycle_status": status,
            "lifecycle_status_label": CUSTOMER_STATUS_LABELS.get(status, status),
            "workspace_active": bool(o.is_active),
            "cancellation_effective_at": o.cancellation_effective_at,
            "package": ({"key": pkg.key, "name": pkg.name} if pkg else None),
            "commercials": commercials(impl),
            "implementation": ({"id": impl.id, "status": impl.status,
                                "is_live": impl.is_live()} if impl else None),
            "implementation_state": impl.status if impl else NO_IMPLEMENTATION,
            "sold_by_name": sellers.get(impl.sold_by_user_id) if impl else None,
            "source": SOURCE_PIPELINE if impl is not None else SOURCE_OUTSIDE,
            "source_label": SOURCE_LABELS[SOURCE_PIPELINE if impl is not None
                                          else SOURCE_OUTSIDE],
            "opportunity_id": impl.opportunity_id if impl else None,
            "customer_since": (impl.launched_at or impl.created_at) if impl
                              else o.created_at,
            "user_count": users.get(str(o.id), 0),
            "lead_count": leads.get(str(o.id), 0),
        })
    return out
