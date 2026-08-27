"""Owner-level aggregation across every brand, every deal, every customer.

Every number in this module is a query against real rows. There are no
placeholder metrics, no seeded demo constants and no "coming soon" tiles - if a
question cannot be answered from the schema, it is absent rather than faked.

WHY THE COUNTS LOOK PARANOID ABOUT `status` VERSUS `stage`
---------------------------------------------------------
`Opportunity` carries both. `status` is the terminal outcome (open / won /
lost); `stage` is the position in the lifecycle, and Checkpoint 6 starts moving
it past `won` into `onboarding` and `live` as customers get provisioned and
launched. Every Won figure here therefore filters on `status == "won"`, which
means a customer going live does not silently subtract itself from the brand's
won total.

WHY NOTHING SAYS "EVOSYS PRO"
-----------------------------
Brand identity is data. Platforms drive the whole thing, so a second brand that
starts selling appears here with no code change (§42).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (
    Organization, Platform, User, Proposal, Lead,
    PROP_SENT, PROP_VIEWED, PROP_ACCEPTED, PROP_CHANGE_REQUESTED,
    PROP_SUPERSEDED, PROP_DECLINED, PROP_EXPIRED,
)
from app.models.sales_models import (
    Opportunity, BrandSalesOrg, BrandPackage, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_CLOSING, STAGE_WON, STAGE_LOST, STAGE_ONBOARDING, STAGE_LIVE,
)
from app.models.scheduling_models import SalesAppointment, APPT_SCHEDULED
from app.models.implementation_models import (
    Implementation, ImplementationMilestone,
    IMPL_LIVE, IMPL_BLOCKED, IMPL_READY_FOR_LAUNCH, MILESTONE_SETTLED,
)
from app.services.customer_activation import invite_state, invite_state_bulk

# An opportunity nobody has touched in this long is stalled. Not a setting: one
# number, in one place, that every stalled count in the control plane uses, so
# the god dashboard and the brand drilldown can never disagree.
STALLED_DAYS = 14

# Proposals still waiting on the buyer.
OUTSTANDING_PROPOSAL_STATUSES = (PROP_SENT, PROP_VIEWED, PROP_CHANGE_REQUESTED)
CLOSED_PROPOSAL_STATUSES = (PROP_ACCEPTED, PROP_DECLINED, PROP_EXPIRED, PROP_SUPERSEDED)


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _deal_value(opp: Opportunity) -> float:
    """The deal's value. One column.

    `deal_value_override` is a BOOLEAN - it records that a manager set the value
    by hand instead of deriving it from the package, and it is not a second
    amount. Reading it as one makes every pipeline total zero, because
    `float(False or 0)` is 0.0 and the flag is False on almost every row. This
    matches `manager_workspace` and `sales_router`, which have always read
    `deal_value` alone.
    """
    return float(opp.deal_value or 0)


# ── per-brand ───────────────────────────────────────────────────────────────

def brand_summary(db: Session, bso: BrandSalesOrg, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One brand's whole operating picture, sales through live customers."""
    now = now or datetime.utcnow()
    stale_before = now - timedelta(days=STALLED_DAYS)

    platform = (db.query(Platform).filter(Platform.id == bso.platform_id).first()
                if bso.platform_id else None)

    memberships = (db.query(Membership)
                     .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                             Membership.scope_id == bso.id,
                             Membership.is_active.is_(True))
                     .all())
    manager_ids = [m.user_id for m in memberships if m.role == ROLE_SALES_MANAGER]
    rep_ids = [m.user_id for m in memberships if m.role == ROLE_SALES_REP]
    managers = (db.query(User).filter(User.id.in_(manager_ids)).all() if manager_ids else [])
    active_reps = (db.query(func.count(User.id))
                     .filter(User.id.in_(rep_ids), User.is_active.is_(True)).scalar()
                   if rep_ids else 0)

    opps = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso.id).all()
    open_opps = [o for o in opps if o.status == "open"]
    won_opps = [o for o in opps if o.status == "won"]
    closing = [o for o in open_opps if o.stage == STAGE_CLOSING]
    stalled = [o for o in open_opps
               if (o.updated_at or o.created_at or now) < stale_before]
    overdue = [o for o in open_opps
               if o.next_action_due_at is not None and o.next_action_due_at < now]

    meetings = (db.query(func.count(SalesAppointment.id))
                  .filter(SalesAppointment.brand_sales_org_id == bso.id,
                          SalesAppointment.status == APPT_SCHEDULED,
                          SalesAppointment.starts_at >= now).scalar() or 0)

    props = (db.query(Proposal)
               .filter(Proposal.brand_sales_org_id == bso.id,
                       Proposal.deleted_at.is_(None)).all())
    outstanding = [p for p in props if p.sales_status in OUTSTANDING_PROPOSAL_STATUSES]
    with_activity = [p for p in outstanding if p.first_viewed_at is not None]

    impls = (db.query(Implementation)
               .filter(Implementation.brand_sales_org_id == bso.id).all())
    provisioned_opp_ids = {i.opportunity_id for i in impls}
    awaiting = [o for o in won_opps if o.id not in provisioned_opp_ids]
    live = [i for i in impls if i.status == IMPL_LIVE]
    blocked = [i for i in impls if i.status == IMPL_BLOCKED]
    onboarding = [i for i in impls if i.status != IMPL_LIVE]

    # A manager who needs attention has a real, listed reason. An alert with no
    # cause behind it is a decoration, and §2 says not to build those.
    attention: List[str] = []
    if not managers:
        attention.append("No sales manager assigned to this brand.")
    if len(stalled) > 0:
        attention.append("%d opportunit%s stalled over %d days."
                         % (len(stalled), "y" if len(stalled) == 1 else "ies", STALLED_DAYS))
    if len(overdue) > 0:
        attention.append("%d overdue next action%s." % (len(overdue), "" if len(overdue) == 1 else "s"))
    if awaiting:
        attention.append("%d Won deal%s awaiting provisioning."
                         % (len(awaiting), "" if len(awaiting) == 1 else "s"))
    if blocked:
        attention.append("%d implementation%s blocked." % (len(blocked), "" if len(blocked) == 1 else "s"))

    return {
        "brand_sales_org_id": bso.id,
        "brand_sales_org_name": bso.name,
        "platform": ({"id": platform.id, "name": platform.name, "slug": platform.slug}
                     if platform else None),
        "is_active": bool(bso.is_active),
        "managers": [{"id": m.id, "name": m.full_name, "email": m.email,
                      "is_active": bool(m.is_active)} for m in managers],
        "rep_count": len(rep_ids),
        "active_rep_count": int(active_reps),
        "open_opportunities": len(open_opps),
        "pipeline_value": round(sum(_deal_value(o) for o in open_opps), 2),
        "closing_opportunities": len(closing),
        "stalled_opportunities": len(stalled),
        "overdue_next_actions": len(overdue),
        "meetings_scheduled": int(meetings),
        "proposals_outstanding": len(outstanding),
        "proposals_with_buyer_activity": len(with_activity),
        "won_deals": len(won_opps),
        "won_value": round(sum(_deal_value(o) for o in won_opps), 2),
        "won_awaiting_provisioning": len(awaiting),
        "customers_provisioned": len(impls),
        "customers_onboarding": len(onboarding),
        "customers_live": len(live),
        "implementations_blocked": len(blocked),
        "attention": attention,
    }


def brands(db: Session, platform_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q = db.query(BrandSalesOrg)
    if platform_id:
        q = q.filter(BrandSalesOrg.platform_id == platform_id)
    return [brand_summary(db, b) for b in q.order_by(BrandSalesOrg.name).all()]


# ── platform-wide ───────────────────────────────────────────────────────────

def sales_operations(db: Session) -> Dict[str, Any]:
    """The command-centre answer to every question in Checkpoint 6 §2."""
    now = datetime.utcnow()
    per_brand = brands(db)

    def s(k):
        return sum(b[k] for b in per_brand)

    total_customers = db.query(func.count(Organization.id)).scalar() or 0
    unprovisioned_customers = (
        db.query(func.count(Organization.id))
          .outerjoin(Implementation, Implementation.organization_id == Organization.id)
          .filter(Implementation.id.is_(None)).scalar() or 0
    )

    return {
        "generated_at": now,
        "brands_selling": len([b for b in per_brand if b["is_active"]]),
        "brands": per_brand,
        "totals": {
            "open_opportunities": s("open_opportunities"),
            "pipeline_value": round(sum(b["pipeline_value"] for b in per_brand), 2),
            "closing_opportunities": s("closing_opportunities"),
            "stalled_opportunities": s("stalled_opportunities"),
            "overdue_next_actions": s("overdue_next_actions"),
            "meetings_scheduled": s("meetings_scheduled"),
            "proposals_outstanding": s("proposals_outstanding"),
            "proposals_with_buyer_activity": s("proposals_with_buyer_activity"),
            "won_deals": s("won_deals"),
            "won_value": round(sum(b["won_value"] for b in per_brand), 2),
            "won_awaiting_provisioning": s("won_awaiting_provisioning"),
            "customers_provisioned": s("customers_provisioned"),
            "customers_onboarding": s("customers_onboarding"),
            "customers_live": s("customers_live"),
            "implementations_blocked": s("implementations_blocked"),
            "active_reps": s("active_rep_count"),
            # Customer organisations that exist but were never sold through this
            # pipeline - migrated tenants, hand-created orgs. Stated rather than
            # hidden, because pretending every customer came from an opportunity
            # is how a control plane starts lying to its owner.
            "customer_organizations_total": int(total_customers),
            "customer_organizations_without_implementation": int(unprovisioned_customers),
        },
        "queues": decision_queues(db),
    }


# ── §37 decision queues ─────────────────────────────────────────────────────

def decision_queues(db: Session) -> Dict[str, Any]:
    """Exception queues, each of which leads to an action that resolves it.

    Every entry here is resolvable from a screen that exists. Counting things
    nobody can act on would make the dashboard a source of guilt rather than a
    source of work.
    """
    now = datetime.utcnow()
    impls = db.query(Implementation).all()

    no_owner = [i for i in impls if not i.owner_user_id and i.status != IMPL_LIVE]
    blocked = [i for i in impls if i.status == IMPL_BLOCKED]
    ready = [i for i in impls if i.status == IMPL_READY_FOR_LAUNCH]
    overdue = [i for i in impls
               if i.status != IMPL_LIVE and i.target_launch_date is not None
               and i.target_launch_date < now]
    billing_review = [i for i in impls
                      if i.status in (IMPL_READY_FOR_LAUNCH, IMPL_LIVE)
                      and (i.billing_status or "not_configured") == "not_configured"]

    provisioned_opp_ids = {i.opportunity_id for i in impls}
    awaiting = (db.query(Opportunity)
                  .filter(Opportunity.status == "won")
                  .order_by(Opportunity.won_at.desc())
                  .all())
    awaiting = [o for o in awaiting if o.id not in provisioned_opp_ids]

    # Invite state for every candidate org in two queries rather than one call
    # per implementation - the same customer was re-checked for every one of its
    # implementations, and each check cost a query per admin.
    candidates = [i for i in impls if i.status != IMPL_LIVE]
    invite_states = invite_state_bulk(db, [i.organization_id for i in candidates])
    not_invited = []
    for i in candidates:
        st = invite_states.get(str(i.organization_id))
        if st is None:
            continue
        if st["needs_invite"] or (st["has_admin"] and st["invites_pending"] == 0
                                  and st["invites_accepted"] == 0):
            not_invited.append(i)

    # One prefetch across every queue. These six lists overlap heavily - the same
    # blocked, unowned implementation appears in several of them - so without the
    # shared row memo it was serialised once per queue it belongs to.
    _pre = ImplPrefetch(db, list(not_invited) + list(no_owner) + list(blocked)
                        + list(overdue) + list(ready) + list(billing_review))

    def _impl_rows(rows):
        return [_implementation_row(db, i, _pre) for i in rows]

    return {
        "won_awaiting_provisioning": [
            {"opportunity_id": o.id, "company_name": o.company_name,
             "brand_sales_org_id": o.brand_sales_org_id,
             "won_at": o.won_at, "deal_value": _deal_value(o),
             "owner_user_id": o.owner_user_id}
            for o in awaiting
        ],
        "customer_admin_not_invited": _impl_rows(not_invited),
        "implementation_has_no_owner": _impl_rows(no_owner),
        "blocked_implementations": _impl_rows(blocked),
        "launch_date_overdue": _impl_rows(overdue),
        "ready_for_launch": _impl_rows(ready),
        "billing_review_needed": _impl_rows(billing_review),
    }


# ── implementation rows ─────────────────────────────────────────────────────

class ImplPrefetch:
    """Everything `_implementation_row` needs for a LIST of implementations, in
    seven queries instead of nine per row.

    WHY. The row serializer did nine independent lookups - organisation,
    opportunity, brand sales org, platform, package, owner, sold-by, and two
    milestone counts. `implementations()` called it once per row, so a 25-row
    board cost 227 statements, and the won-queue called it again across SIX
    overlapping lists, re-serialising the same blocked implementation up to four
    times.

    Nothing here changes what a row contains. It changes how many times the same
    handful of parent rows are fetched. Rows are memoised by implementation id,
    so an implementation appearing in four queues is built once.
    """

    __slots__ = ("orgs", "opps", "bsos", "platforms", "packages", "users",
                 "milestones", "_rows")

    def __init__(self, db: Session, impls):
        self.orgs, self.opps, self.bsos = {}, {}, {}
        self.platforms, self.packages, self.users = {}, {}, {}
        self.milestones = {}
        self._rows = {}
        impls = [i for i in impls if i is not None]
        if not impls:
            return

        def by_id(model, ids):
            ids = sorted({i for i in ids if i})
            if not ids:
                return {}
            return {r.id: r for r in db.query(model).filter(model.id.in_(ids)).all()}

        self.orgs = by_id(Organization, (i.organization_id for i in impls))
        self.opps = by_id(Opportunity, (i.opportunity_id for i in impls))
        self.bsos = by_id(BrandSalesOrg, (i.brand_sales_org_id for i in impls))
        self.platforms = by_id(Platform, (i.platform_id for i in impls))
        self.packages = by_id(BrandPackage, (i.package_id for i in impls))
        # Owners and sellers come from the same table, so they are one query.
        self.users = by_id(User, [i.owner_user_id for i in impls]
                           + [i.sold_by_user_id for i in impls])

        impl_ids = sorted({i.id for i in impls})
        # Two counts per implementation become two grouped counts for all of
        # them. An implementation with no milestones is simply absent and reads
        # back as 0 - which is what `.scalar() or 0` produced before.
        for impl_id, n in (db.query(ImplementationMilestone.implementation_id,
                                    func.count(ImplementationMilestone.id))
                           .filter(ImplementationMilestone.implementation_id.in_(impl_ids))
                           .group_by(ImplementationMilestone.implementation_id).all()):
            self.milestones[impl_id] = [int(n), 0]
        for impl_id, n in (db.query(ImplementationMilestone.implementation_id,
                                    func.count(ImplementationMilestone.id))
                           .filter(ImplementationMilestone.implementation_id.in_(impl_ids),
                                   ImplementationMilestone.status.in_(MILESTONE_SETTLED))
                           .group_by(ImplementationMilestone.implementation_id).all()):
            self.milestones.setdefault(impl_id, [0, 0])[1] = int(n)

    def counts(self, impl_id):
        return tuple(self.milestones.get(impl_id, (0, 0)))


def _implementation_row(db: Session, impl: Implementation,
                        pre: Optional[ImplPrefetch] = None) -> Dict[str, Any]:
    if pre is not None:
        if impl.id in pre._rows:
            return pre._rows[impl.id]
        org = pre.orgs.get(impl.organization_id)
        opp = pre.opps.get(impl.opportunity_id)
        bso = pre.bsos.get(impl.brand_sales_org_id) if impl.brand_sales_org_id else None
        platform = pre.platforms.get(impl.platform_id) if impl.platform_id else None
        pkg = pre.packages.get(impl.package_id) if impl.package_id else None
        owner = pre.users.get(impl.owner_user_id) if impl.owner_user_id else None
        sold_by = pre.users.get(impl.sold_by_user_id) if impl.sold_by_user_id else None
        total, settled = pre.counts(impl.id)
    else:
        org = db.query(Organization).filter(Organization.id == impl.organization_id).first()
        opp = db.query(Opportunity).filter(Opportunity.id == impl.opportunity_id).first()
        bso = (db.query(BrandSalesOrg).filter(BrandSalesOrg.id == impl.brand_sales_org_id).first()
               if impl.brand_sales_org_id else None)
        platform = (db.query(Platform).filter(Platform.id == impl.platform_id).first()
                    if impl.platform_id else None)
        pkg = (db.query(BrandPackage).filter(BrandPackage.id == impl.package_id).first()
               if impl.package_id else None)
        owner = (db.query(User).filter(User.id == impl.owner_user_id).first()
                 if impl.owner_user_id else None)
        sold_by = (db.query(User).filter(User.id == impl.sold_by_user_id).first()
                   if impl.sold_by_user_id else None)

        total = (db.query(func.count(ImplementationMilestone.id))
                   .filter(ImplementationMilestone.implementation_id == impl.id).scalar() or 0)
        settled = (db.query(func.count(ImplementationMilestone.id))
                     .filter(ImplementationMilestone.implementation_id == impl.id,
                             ImplementationMilestone.status.in_(MILESTONE_SETTLED)).scalar() or 0)

    now = datetime.utcnow()
    row = {
        "implementation_id": impl.id,
        "organization_id": impl.organization_id,
        "organization_name": org.name if org else None,
        "organization_slug": org.slug if org else None,
        "opportunity_id": impl.opportunity_id,
        "opportunity_company": opp.company_name if opp else None,
        "platform": {"id": platform.id, "name": platform.name} if platform else None,
        "brand_sales_org": {"id": bso.id, "name": bso.name} if bso else None,
        "package": {"id": pkg.id, "key": pkg.key, "name": pkg.name} if pkg else None,
        "sold_by": {"id": sold_by.id, "name": sold_by.full_name} if sold_by else None,
        "owner": {"id": owner.id, "name": owner.full_name} if owner else None,
        "status": impl.status,
        "milestones_total": int(total),
        "milestones_settled": int(settled),
        "percent_complete": int(round(100.0 * settled / total)) if total else 0,
        "target_launch_date": impl.target_launch_date,
        "is_overdue": bool(impl.target_launch_date and impl.status != IMPL_LIVE
                           and impl.target_launch_date < now),
        "blocker_note": impl.blocker_note,
        "blocked_at": impl.blocked_at,
        "last_activity_at": impl.last_activity_at,
        "launched_at": impl.launched_at,
        "is_live": impl.is_live(),
        "billing_status": impl.billing_status,
        "created_at": impl.created_at,
    }
    if pre is not None:
        pre._rows[impl.id] = row
    return row


def implementations(db: Session, *, platform_id: Optional[str] = None,
                    brand_sales_org_id: Optional[str] = None,
                    status: Optional[str] = None,
                    owner_user_id: Optional[str] = None,
                    blocked: Optional[bool] = None,
                    overdue: Optional[bool] = None,
                    live: Optional[bool] = None,
                    limit: int = 200) -> List[Dict[str, Any]]:
    q = db.query(Implementation)
    if platform_id:
        q = q.filter(Implementation.platform_id == platform_id)
    if brand_sales_org_id:
        q = q.filter(Implementation.brand_sales_org_id == brand_sales_org_id)
    if status:
        q = q.filter(Implementation.status == status)
    if owner_user_id:
        q = q.filter(Implementation.owner_user_id == owner_user_id)
    if blocked is True:
        q = q.filter(Implementation.status == IMPL_BLOCKED)
    if live is True:
        q = q.filter(Implementation.status == IMPL_LIVE)
    elif live is False:
        q = q.filter(Implementation.status != IMPL_LIVE)
    rows = q.order_by(Implementation.created_at.desc()).limit(max(1, min(limit, 500))).all()
    pre = ImplPrefetch(db, rows)
    out = [_implementation_row(db, i, pre) for i in rows]
    if overdue is True:
        out = [r for r in out if r["is_overdue"]]
    elif overdue is False:
        out = [r for r in out if not r["is_overdue"]]
    return out


# ── §20 customer organisations ──────────────────────────────────────────────

def customer_organizations(db: Session, *, platform_id: Optional[str] = None,
                           limit: int = 300) -> List[Dict[str, Any]]:
    from app.services.platform_owner import exclude_platform_org

    # The platform's own pseudo-organization is not a customer. It appeared in
    # this list, and therefore in every customer count and picker built on it,
    # which made "how many customers do we have" wrong by one everywhere.
    q = exclude_platform_org(db.query(Organization))
    if platform_id:
        q = q.filter(Organization.platform_id == platform_id)
    orgs = q.order_by(Organization.created_at.desc()).limit(max(1, min(limit, 1000))).all()

    # Five queries per organisation - platform, implementation, package, and two
    # counts - became five for the whole page. `implementations` is unique on
    # organization_id, so the single row `.first()` returned is the only one
    # there is; setdefault below preserves that same choice regardless.
    _org_ids = sorted({o.id for o in orgs})
    _impls, _users, _leads, _platforms, _packages = {}, {}, {}, {}, {}
    if _org_ids:
        for im in (db.query(Implementation)
                     .filter(Implementation.organization_id.in_(_org_ids)).all()):
            _impls.setdefault(im.organization_id, im)
        _users = {str(k): int(n) for k, n in
                  db.query(User.organization_id, func.count(User.id))
                    .filter(User.organization_id.in_(_org_ids))
                    .group_by(User.organization_id).all()}
        _leads = {str(k): int(n) for k, n in
                  db.query(Lead.organization_id, func.count(Lead.id))
                    .filter(Lead.organization_id.in_(_org_ids))
                    .group_by(Lead.organization_id).all()}
        _plat_ids = sorted({o.platform_id for o in orgs if o.platform_id})
        if _plat_ids:
            _platforms = {p.id: p for p in
                          db.query(Platform).filter(Platform.id.in_(_plat_ids)).all()}
        _pkg_ids = sorted({im.package_id for im in _impls.values() if im.package_id})
        if _pkg_ids:
            _packages = {p.id: p for p in
                         db.query(BrandPackage).filter(BrandPackage.id.in_(_pkg_ids)).all()}

    out = []
    for o in orgs:
        platform = _platforms.get(o.platform_id) if o.platform_id else None
        impl = _impls.get(o.id)
        pkg = _packages.get(impl.package_id) if impl and impl.package_id else None
        users = _users.get(str(o.id), 0)
        leads = _leads.get(str(o.id), 0)
        out.append({
            "organization_id": o.id,
            "name": o.name,
            "slug": o.slug,
            "platform": {"id": platform.id, "name": platform.name} if platform else None,
            "is_active": bool(o.is_active),
            "plan": o.plan,
            "industry": o.industry,
            "user_count": int(users),
            "lead_count": int(leads),
            "package": {"key": pkg.key, "name": pkg.name} if pkg else None,
            "implementation": ({"id": impl.id, "status": impl.status,
                                "is_live": impl.is_live(),
                                "launched_at": impl.launched_at,
                                "opportunity_id": impl.opportunity_id}
                               if impl else None),
            # Stated, not hidden: an organisation with no implementation did not
            # come through the Won -> Provision path.
            "provisioned_from_sale": impl is not None,
            "created_at": o.created_at,
        })
    return out
