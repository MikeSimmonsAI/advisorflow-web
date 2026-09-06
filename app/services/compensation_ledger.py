"""WHAT WE OWE, WHAT WE HAVE PAID, AND WHY — the reading side of compensation.

THIS MODULE COMPUTES NO COMPENSATION. Not one rate is applied here. Every
number it reports was already decided by `compensation.compute()` (projected) or
is already sitting in a `CompensationEntry` row (earned, payable, paid). A
second place that knew how to work out what somebody earns is exactly how a
dashboard and a payment run come to disagree with nobody able to say which is
right.

WHY "ON HOLD" IS NOT A NEW STATE
--------------------------------
The canonical states are earned / payable / paid / void. "On hold" is not a
fifth one — it is an EARNED row whose `payable_at` has not arrived. Adding a
state for it would mean two places could disagree about whether a holdback had
elapsed, and a row could sit in `on_hold` after its date passed because nothing
moved it. It is derived, every time, from data that cannot drift:

    on hold   state == earned AND payable_at  > now
    payable   state == payable, OR earned AND payable_at <= now

That second clause matters. `promote_due_to_payable()` flips rows in bulk, but
until it runs an earned row whose holdback has quietly elapsed IS payable in
every sense that matters to the business. Reporting it as still held would
understate what can be paid today, so the read reports the truth and the
promotion catches up.

RECONCILIATION IS THE POINT
---------------------------
Every headline figure is the sum of rows the ledger will return under the same
filters. `PAYABLE NOW = $8,450` has to be openable. Totals computed by one path
and rows by another is how a finance screen becomes untrustworthy, so `summary`
and `entries` share `_scoped_query` and nothing else computes a total.

BRAND ISOLATION IS APPLIED IN THE QUERY, NOT THE VIEW. Every read starts from
the set of brand sales orgs the caller may see, and a caller who may see none
gets an empty ledger rather than an unfiltered one.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.compensation_models import (BASIS_LABELS, COMP_EARNED,
                                            COMP_PAID, COMP_PAYABLE, COMP_VOID,
                                            COMP_STATE_LABELS, PAYEE_OVERRIDE,
                                            PAYEE_SELLER, CompensationEntry,
                                            CompensationPlan)
from app.models.implementation_models import Implementation
from app.models.models import Organization, User
from app.models.sales_models import (BrandPackage, BrandSalesOrg, Opportunity)

ZERO = Decimal("0")

# The buckets a screen actually asks for. `on_hold` and `payable_now` are
# DERIVED views of the durable states above, never stored.
VIEW_ON_HOLD = "on_hold"
VIEW_PAYABLE_NOW = "payable_now"
VIEW_PAID = "paid"
VIEW_VOID = "void"
VIEW_ALL = "all"

VIEW_LABELS = {
    VIEW_ON_HOLD: "On hold",
    VIEW_PAYABLE_NOW: "Payable now",
    VIEW_PAID: "Paid",
    VIEW_VOID: "Void",
    VIEW_ALL: "All",
}

TYPE_LABELS = {
    PAYEE_SELLER: "Direct seller commission",
    PAYEE_OVERRIDE: "Manager override",
}


def _f(v) -> Optional[float]:
    if v is None:
        return None
    return float(Decimal(str(v)).quantize(Decimal("0.01")))


def compensation_type(entry: CompensationEntry) -> str:
    """What KIND of compensation this is, in words.

    An override names its level, because "manager override" and "second-level
    override" are different expenses to a business even though the engine
    treats them identically.
    """
    if entry.payee_kind != PAYEE_OVERRIDE:
        return TYPE_LABELS[PAYEE_SELLER]
    level = entry.override_level or 1
    if level == 1:
        return "Manager override"
    return "Level-%d override" % level


# ── scoping ─────────────────────────────────────────────────────────────────

def visible_brand_ids(db: Session, user: User) -> List[str]:
    """Which brands' compensation this caller may see. Empty means none.

    Delegates to the existing sales-access model rather than inventing a second
    answer: a god_admin sees every brand (narrowed by a selected brand, never
    widened), and everybody else sees only brands they hold a membership in.
    """
    from app.services.sales_access import sales_org_ids
    return sales_org_ids(user, db)


def _scoped_query(db: Session, *, brand_ids: Iterable[str],
                  payee_user_id: Optional[str] = None,
                  brand_sales_org_id: Optional[str] = None,
                  view: str = VIEW_ALL,
                  now: Optional[datetime] = None):
    """The ONE query both the totals and the rows are built from."""
    now = now or datetime.utcnow()
    ids = list(brand_ids)
    q = db.query(CompensationEntry)
    if not ids:
        # No brand, no ledger. Deliberately an impossible filter rather than an
        # unfiltered query — a scoping bug must produce nothing, not everything.
        return q.filter(CompensationEntry.id.is_(None))
    q = q.filter(CompensationEntry.brand_sales_org_id.in_(ids))
    if brand_sales_org_id:
        if brand_sales_org_id not in ids:
            return q.filter(CompensationEntry.id.is_(None))
        q = q.filter(CompensationEntry.brand_sales_org_id == brand_sales_org_id)
    if payee_user_id:
        q = q.filter(CompensationEntry.payee_user_id == payee_user_id)

    if view == VIEW_ON_HOLD:
        q = q.filter(CompensationEntry.state == COMP_EARNED,
                     CompensationEntry.payable_at.isnot(None),
                     CompensationEntry.payable_at > now)
    elif view == VIEW_PAYABLE_NOW:
        # Both the promoted rows and the ones whose holdback has elapsed but
        # which nothing has promoted yet. See the module docstring.
        q = q.filter(
            (CompensationEntry.state == COMP_PAYABLE) |
            ((CompensationEntry.state == COMP_EARNED) &
             (CompensationEntry.payable_at.isnot(None)) &
             (CompensationEntry.payable_at <= now)))
    elif view == VIEW_PAID:
        q = q.filter(CompensationEntry.state == COMP_PAID)
    elif view == VIEW_VOID:
        q = q.filter(CompensationEntry.state == COMP_VOID)
    return q


def derived_view(entry: CompensationEntry, now: Optional[datetime] = None) -> str:
    """Which bucket ONE entry falls in. Same rule as the query, one row."""
    now = now or datetime.utcnow()
    if entry.state == COMP_PAID:
        return VIEW_PAID
    if entry.state == COMP_VOID:
        return VIEW_VOID
    if entry.state == COMP_PAYABLE:
        return VIEW_PAYABLE_NOW
    if entry.payable_at is not None and entry.payable_at <= now:
        return VIEW_PAYABLE_NOW
    return VIEW_ON_HOLD


# ── the rows ────────────────────────────────────────────────────────────────

def _decorate(db: Session, rows: List[CompensationEntry],
              now: datetime) -> List[Dict[str, Any]]:
    """Attach the human context. Batched — one query per related table."""
    if not rows:
        return []
    opp_ids = sorted({r.opportunity_id for r in rows})
    user_ids = sorted({r.payee_user_id for r in rows if r.payee_user_id})
    plan_ids = sorted({r.plan_id for r in rows if r.plan_id})
    brand_ids = sorted({r.brand_sales_org_id for r in rows if r.brand_sales_org_id})

    opps = {o.id: o for o in db.query(Opportunity)
            .filter(Opportunity.id.in_(opp_ids)).all()} if opp_ids else {}
    users = {u.id: (u.full_name or u.email) for u in db.query(User)
             .filter(User.id.in_(user_ids)).all()} if user_ids else {}
    plans = {p.id: p.name for p in db.query(CompensationPlan)
             .filter(CompensationPlan.id.in_(plan_ids)).all()} if plan_ids else {}
    brands = {b.id: b.name for b in db.query(BrandSalesOrg)
              .filter(BrandSalesOrg.id.in_(brand_ids)).all()} if brand_ids else {}

    # The CUSTOMER this commission came from, through the implementation — the
    # same crossing Customer 360 reads. Not every deal has one: a commission can
    # be earned before provisioning, and saying so beats inventing a customer.
    impls = {i.opportunity_id: i for i in db.query(Implementation)
             .filter(Implementation.opportunity_id.in_(opp_ids)).all()} if opp_ids else {}
    org_ids = sorted({i.organization_id for i in impls.values()})
    orgs = {o.id: o.name for o in db.query(Organization)
            .filter(Organization.id.in_(org_ids)).all()} if org_ids else {}
    pkg_ids = sorted({i.package_id for i in impls.values() if i.package_id})
    pkgs = {p.id: p.name for p in db.query(BrandPackage)
            .filter(BrandPackage.id.in_(pkg_ids)).all()} if pkg_ids else {}

    out = []
    for r in rows:
        opp = opps.get(r.opportunity_id)
        impl = impls.get(r.opportunity_id)
        out.append({
            "id": r.id,
            "view": derived_view(r, now),
            "state": r.state,
            "state_label": COMP_STATE_LABELS.get(r.state, r.state),

            "payee_user_id": r.payee_user_id,
            "payee_name": users.get(r.payee_user_id) or "—",
            "payee_kind": r.payee_kind,
            "override_level": r.override_level,
            "compensation_type": compensation_type(r),

            "brand_sales_org_id": r.brand_sales_org_id,
            "brand_sales_org_name": brands.get(r.brand_sales_org_id),

            "opportunity_id": r.opportunity_id,
            "deal_name": opp.company_name if opp else None,
            "customer_organization_id": impl.organization_id if impl else None,
            "customer_name": orgs.get(impl.organization_id) if impl else None,
            "package_name": pkgs.get(impl.package_id) if impl and impl.package_id else None,

            # WHY THIS IS MONEY. The qualifying collection, named.
            "collection_reference": r.collection_reference,
            "collected_at": r.collected_at,
            "collected_amount": _f(r.basis_collected_amount),

            "amount": _f(r.amount),
            "currency": r.currency or "USD",
            "capped_from_amount": _f(r.capped_from_amount),

            "earned_at": r.earned_at,
            "payable_at": r.payable_at,
            "paid_at": r.paid_at,
            "payment_reference": r.payment_reference,
            "days_until_payable": (
                max(0, (r.payable_at - now).days)
                if r.payable_at and r.payable_at > now else 0),

            # THE SNAPSHOT — what the plan said AT THE TIME, not what it says
            # now. This is what answers "why was this person paid this amount"
            # years later, and it is why a plan edit cannot rewrite history.
            "plan_id": r.plan_id,
            "plan_name": plans.get(r.plan_id),
            "rule_id": r.rule_id,
            "basis": r.basis,
            "basis_label": BASIS_LABELS.get(r.basis, r.basis),
            "rate_percent": _f(r.rate_percent),
            "rate_amount": _f(r.rate_amount),
            "basis_implementation_fee": _f(r.basis_implementation_fee),
            "basis_mrr": _f(r.basis_mrr),
            "basis_term_months": r.basis_term_months,
            "basis_tcv": _f(r.basis_tcv),

            "note": r.note,
        })
    return out


def entries(db: Session, user: User, *,
            brand_sales_org_id: Optional[str] = None,
            payee_user_id: Optional[str] = None,
            view: str = VIEW_ALL,
            compensation_kind: Optional[str] = None,
            limit: int = 500,
            now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.utcnow()
    q = _scoped_query(db, brand_ids=visible_brand_ids(db, user),
                      payee_user_id=payee_user_id,
                      brand_sales_org_id=brand_sales_org_id, view=view, now=now)
    if compensation_kind in (PAYEE_SELLER, PAYEE_OVERRIDE):
        q = q.filter(CompensationEntry.payee_kind == compensation_kind)
    rows = (q.order_by(CompensationEntry.earned_at.desc())
            .limit(max(1, min(limit, 2000))).all())
    return _decorate(db, rows, now)


# ── the totals ──────────────────────────────────────────────────────────────

def summary(db: Session, user: User, *,
            brand_sales_org_id: Optional[str] = None,
            payee_user_id: Optional[str] = None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """The headline figures, each one openable in the ledger.

    Built from the SAME `_scoped_query` the rows come from, filtered the same
    way, so a total can never describe a set the ledger will not show.
    """
    now = now or datetime.utcnow()
    brand_ids = visible_brand_ids(db, user)

    def bucket(view: str):
        rows = _scoped_query(db, brand_ids=brand_ids, payee_user_id=payee_user_id,
                             brand_sales_org_id=brand_sales_org_id, view=view,
                             now=now).all()
        total = sum((r.amount or ZERO) for r in rows) if rows else ZERO
        return {"amount": _f(total), "count": len(rows)}

    on_hold = bucket(VIEW_ON_HOLD)
    payable = bucket(VIEW_PAYABLE_NOW)
    paid = bucket(VIEW_PAID)

    # EARNED is every row that has become money, whether or not it has been
    # paid — the lifetime figure. On hold and payable are subsets of what is
    # not yet paid, which is why they are reported beside it rather than
    # inside it.
    earned_rows = _scoped_query(db, brand_ids=brand_ids, payee_user_id=payee_user_id,
                                brand_sales_org_id=brand_sales_org_id,
                                view=VIEW_ALL, now=now).filter(
        CompensationEntry.state != COMP_VOID).all()
    earned_total = sum((r.amount or ZERO) for r in earned_rows) if earned_rows else ZERO

    return {
        "as_of": now,
        "earned_total": _f(earned_total),
        "earned_count": len(earned_rows),
        "on_hold": on_hold,
        "payable_now": payable,
        "paid": paid,
        "upcoming": upcoming_liability(db, brand_ids,
                                       brand_sales_org_id=brand_sales_org_id,
                                       payee_user_id=payee_user_id, now=now),
        "brand_count": len(brand_ids),
    }


def upcoming_liability(db: Session, brand_ids: Iterable[str], *,
                       brand_sales_org_id: Optional[str] = None,
                       payee_user_id: Optional[str] = None,
                       now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """What becomes payable in the next 7 / 14 / 30 days.

    ONLY FROM ROWS THAT ALREADY EXIST. Every entry here was earned on a real
    collected payment and carries a stored `payable_at`, so these dates are
    known rather than forecast. Nothing in the open pipeline appears — a deal
    that has not been collected has no payable date, and inventing one would
    turn a forecast into a liability.
    """
    now = now or datetime.utcnow()
    out = []
    for days in (7, 14, 30):
        cutoff = now + timedelta(days=days)
        rows = _scoped_query(db, brand_ids=brand_ids, payee_user_id=payee_user_id,
                             brand_sales_org_id=brand_sales_org_id,
                             view=VIEW_ON_HOLD, now=now).filter(
            CompensationEntry.payable_at <= cutoff).all()
        out.append({
            "window_days": days,
            "amount": _f(sum((r.amount or ZERO) for r in rows) if rows else ZERO),
            "count": len(rows),
        })
    return out


def by_payee(db: Session, user: User, *, view: str = VIEW_PAYABLE_NOW,
             brand_sales_org_id: Optional[str] = None,
             now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """One line per person — what a payment run is actually made of."""
    now = now or datetime.utcnow()
    rows = _scoped_query(db, brand_ids=visible_brand_ids(db, user),
                         brand_sales_org_id=brand_sales_org_id, view=view,
                         now=now).all()
    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        g = grouped.setdefault(r.payee_user_id, {
            "payee_user_id": r.payee_user_id, "amount": ZERO, "count": 0,
            "seller_amount": ZERO, "override_amount": ZERO, "entry_ids": []})
        g["amount"] += (r.amount or ZERO)
        g["count"] += 1
        g["entry_ids"].append(r.id)
        if r.payee_kind == PAYEE_OVERRIDE:
            g["override_amount"] += (r.amount or ZERO)
        else:
            g["seller_amount"] += (r.amount or ZERO)

    names = {}
    if grouped:
        names = {u.id: (u.full_name or u.email) for u in
                 db.query(User).filter(User.id.in_(list(grouped))).all()}
    out = []
    for uid, g in grouped.items():
        out.append({**g,
                    "payee_name": names.get(uid) or "—",
                    "amount": _f(g["amount"]),
                    "seller_amount": _f(g["seller_amount"]),
                    "override_amount": _f(g["override_amount"])})
    out.sort(key=lambda r: r["amount"] or 0, reverse=True)
    return out


# ── projected, which is NOT a row ───────────────────────────────────────────

def projected(db: Session, user: User, *,
              brand_sales_org_id: Optional[str] = None,
              payee_user_id: Optional[str] = None) -> Dict[str, Any]:
    """What the OPEN pipeline would pay if it closed on today's terms.

    Computed live from open opportunities through the same engine that pays a
    real commission, and reported SEPARATELY from every figure above. A
    projection is not a liability: nothing here has been earned, nothing is
    owed, and appearing in this number must never move a row into the ledger.
    """
    from app.services import compensation as comp
    from app.models.compensation_models import (PROJ_PENDING_APPROVAL,
                                                PROJ_UNCONFIGURED)

    brand_ids = visible_brand_ids(db, user)
    if brand_sales_org_id:
        brand_ids = [b for b in brand_ids if b == brand_sales_org_id]
    if not brand_ids:
        return {"amount": 0.0, "deal_count": 0, "unconfigured_deals": 0,
                "pending_approval_amount": 0.0, "pending_approval_deals": 0}

    q = (db.query(Opportunity)
         .filter(Opportunity.brand_sales_org_id.in_(brand_ids),
                 Opportunity.status == "open"))
    if payee_user_id:
        q = q.filter(Opportunity.owner_user_id == payee_user_id)

    total = ZERO
    pending = ZERO
    deals = 0
    unconfigured = 0
    pending_deals = 0
    for opp in q.all():
        c = comp.compute(db, opp)
        if c["status"] == PROJ_UNCONFIGURED:
            unconfigured += 1
            continue
        amount = c["total"] or ZERO
        if payee_user_id:
            # One person's share of this deal, not the whole payout — a rep
            # must not see their manager's override folded into their own
            # projected number.
            amount = sum((p["amount"] or ZERO) for p in c["payouts"]
                         if p["payee_user_id"] == payee_user_id)
        if c["status"] == PROJ_PENDING_APPROVAL:
            pending += amount
            pending_deals += 1
            continue
        total += amount
        deals += 1
    return {
        "amount": _f(total),
        "deal_count": deals,
        # Held out of the headline, reported beside it: an unapproved deal is
        # not a promise.
        "pending_approval_amount": _f(pending),
        "pending_approval_deals": pending_deals,
        # Packages nobody has set a rate for. Reported as a COUNT, never as $0,
        # which would read as a decision to pay nothing.
        "unconfigured_deals": unconfigured,
    }
