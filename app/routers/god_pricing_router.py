"""PRICING & COMPENSATION — the owner's control panel.

WHY THIS ROUTER EXISTS. Discount floors and commission rates were configuration
that only existed in the database. That makes every rate change a developer
task, and a business rule that requires an engineer is a business rule that
stops being maintained. These are commercial decisions and they belong to the
person who makes them.

EVERY ROUTE IS god_admin. `require_god` returns 403 with no information to
anybody else, including super_admins. Setting a commission rate is setting
payroll and a discount ceiling is the company's margin; neither is delegable to
a tenant admin or a sales manager, and the sales workspace deliberately has no
route that reaches any of this.

WHAT THIS ROUTER WILL NOT DO
----------------------------
It does not compute a price and it does not compute a commission. It writes
configuration; `pricing_authority` and `compensation` read it. A second place
that knew how to apply a rate would be a second answer to what somebody earns.

It also refuses to invent a number. Every create takes what the caller gave and
stores exactly that — there are no defaulted rates anywhere below, because a
rate this file made up would be indistinguishable, a year later, from one Mike
decided.

AUDIT IS NOT OPTIONAL HERE. Every mutation writes an audit row with the actor,
the before state and the after state, because "who lowered the floor" and "who
changed what the team earns" are the questions that get asked after something
has already gone wrong.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.compensation_models import (BASES, BASIS_LABELS, DEAL_KINDS,
                                            ELIGIBILITIES, PAYEE_KINDS,
                                            PAYEE_OVERRIDE, PAYEE_SELLER,
                                            CompensationPackageCap,
                                            CompensationPlan,
                                            CompensationRule)
from app.models.models import AuditLogEntry, User
from app.models.pricing_policy_models import PricingPolicy
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     BrandPackage, BrandSalesOrg)
from app.routers.audit_log_router import log_action

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/pricing", tags=["god-pricing"])

# The roles a policy may name. Kept to the two that actually resolve in
# pricing_authority.actor_role — offering a third would let somebody configure a
# ceiling that can never match anybody.
POLICY_ROLES = (ROLE_SALES_REP, ROLE_SALES_MANAGER)

AUDIT_TARGET_POLICY = "pricing_policy"
AUDIT_TARGET_PLAN   = "compensation_plan"
AUDIT_TARGET_RULE   = "compensation_rule"
AUDIT_TARGET_CAP    = "compensation_cap"


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(status_code=400, detail="%r is not a number." % v)


def _f(v):
    return None if v is None else float(v)


def _audit(db: Session, actor: User, action: str, target_type: str,
           target_id: str, before: Optional[dict], after: Optional[dict],
           note: Optional[str] = None) -> None:
    """Who changed what, from what, to what.

    `organization_id` is the ACTOR's, which for a god_admin is NULL — the column
    has been nullable since Checkpoint 6 precisely so control-plane actions can
    be logged. A pricing policy belongs to a brand, not to a customer tenant, so
    there is no customer org to attribute it to and inventing one would file
    this under somebody else's history.
    """
    log_action(db, getattr(actor, "organization_id", None), actor.id,
               action=action, target_type=target_type, target_id=target_id,
               before=before, after=after, note=note, commit=False)


# ═══════════════════════════════════════════════════════════════════════════
# Serializers — what the screen reads
# ═══════════════════════════════════════════════════════════════════════════

def _policy_out(p: PricingPolicy) -> Dict[str, Any]:
    return {
        "id": p.id,
        "brand_sales_org_id": p.brand_sales_org_id,
        "role": p.role,
        # NULL is UNLIMITED, not zero, and the screen must be able to tell the
        # difference — so it is sent as null and labelled, never coerced to 0.
        "max_discount_pct_setup": _f(p.max_discount_pct_setup),
        "max_discount_pct_monthly": _f(p.max_discount_pct_monthly),
        "min_term_months": p.min_term_months,
        "below_floor_requires_approval": bool(p.below_floor_requires_approval),
        "effective_from": p.effective_from,
        "effective_to": p.effective_to,
        "is_active": bool(p.is_active),
        "note": p.note,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _rule_out(r: CompensationRule, pkg_name: Optional[str] = None) -> Dict[str, Any]:
    # A rule with neither an amount nor a percent is UNCONFIGURED, and says so
    # rather than reporting a confident zero. $0 reads as "we decided this pays
    # nothing"; unconfigured reads as "somebody still has to decide".
    configured = (r.amount is not None) or (r.percent is not None)
    return {
        "id": r.id,
        "plan_id": r.plan_id,
        "package_id": r.package_id,
        "package_name": pkg_name,
        "deal_kind": r.deal_kind,
        "payee_kind": r.payee_kind,
        "override_level": r.override_level,
        "basis": r.basis,
        "basis_label": BASIS_LABELS.get(r.basis, r.basis),
        "amount": _f(r.amount),
        "percent": _f(r.percent),
        "recurring_months": r.recurring_months,
        "eligible_payment": r.eligible_payment,
        "max_amount": _f(r.max_amount),
        "sort_order": r.sort_order,
        "is_active": bool(r.is_active),
        "configured": configured,
        "note": r.note,
    }


def _plan_out(db: Session, p: CompensationPlan) -> Dict[str, Any]:
    rules = (db.query(CompensationRule)
             .filter(CompensationRule.plan_id == p.id)
             .order_by(CompensationRule.sort_order.asc()).all())
    caps = (db.query(CompensationPackageCap)
            .filter(CompensationPackageCap.plan_id == p.id).all())
    pkg_names = {}
    ids = {r.package_id for r in rules if r.package_id} | {c.package_id for c in caps}
    if ids:
        for pkg in db.query(BrandPackage).filter(BrandPackage.id.in_(ids)).all():
            pkg_names[pkg.id] = pkg.name
    return {
        "id": p.id,
        "brand_sales_org_id": p.brand_sales_org_id,
        "name": p.name,
        "effective_from": p.effective_from,
        "effective_to": p.effective_to,
        "holdback_days": p.holdback_days,
        "max_override_levels": p.max_override_levels,
        "is_active": bool(p.is_active),
        "note": p.note,
        "rules": [_rule_out(r, pkg_names.get(r.package_id)) for r in rules],
        "caps": [{"id": c.id, "package_id": c.package_id,
                  "package_name": pkg_names.get(c.package_id),
                  "max_total_payout": _f(c.max_total_payout), "note": c.note}
                 for c in caps],
    }


# ═══════════════════════════════════════════════════════════════════════════
# The screen's single read
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/overview")
def overview(brand_sales_org_id: str = Query(None),
             db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Everything the console renders, in one request.

    Includes the PACKAGES so the screen can show which ones have no commission
    rule at all — the unconfigured list is the most useful thing on this page,
    because it is the list of decisions nobody has made yet.
    """
    brands = (db.query(BrandSalesOrg)
              .filter(BrandSalesOrg.is_active.is_(True))
              .order_by(BrandSalesOrg.name.asc()).all())
    brand_ids = [b.id for b in brands]
    scope = brand_sales_org_id if brand_sales_org_id in brand_ids else None

    pq = db.query(PricingPolicy)
    plq = db.query(CompensationPlan)
    if scope:
        # A brand's own rows PLUS the platform-wide ones, because a platform
        # policy is what actually governs that brand when it has none of its
        # own — hiding it would show an empty screen for a brand that is in fact
        # governed.
        pq = pq.filter((PricingPolicy.brand_sales_org_id == scope) |
                       (PricingPolicy.brand_sales_org_id.is_(None)))
        plq = plq.filter((CompensationPlan.brand_sales_org_id == scope) |
                         (CompensationPlan.brand_sales_org_id.is_(None)))

    policies = pq.all()
    plans = plq.order_by(CompensationPlan.effective_from.desc()).all()

    platform_ids = {b.platform_id for b in brands}
    packages = (db.query(BrandPackage)
                .filter(BrandPackage.platform_id.in_(platform_ids),
                        BrandPackage.is_active.is_(True))
                .order_by(BrandPackage.sort_order.asc()).all()
                if platform_ids else [])

    # Which packages have a seller rule on an active plan. Everything else is
    # UNCONFIGURED and is named as such.
    ruled = set()
    for pl in plans:
        if not pl.is_active:
            continue
        for r in db.query(CompensationRule).filter(
                CompensationRule.plan_id == pl.id,
                CompensationRule.is_active.is_(True)).all():
            if r.package_id and (r.amount is not None or r.percent is not None):
                ruled.add(r.package_id)

    return {
        "brands": [{"id": b.id, "name": b.name, "platform_id": b.platform_id}
                   for b in brands],
        "selected_brand_sales_org_id": scope,
        "policies": [_policy_out(p) for p in policies],
        "plans": [_plan_out(db, p) for p in plans],
        "packages": [{"id": p.id, "name": p.name, "key": p.key,
                      "platform_id": p.platform_id,
                      "setup_fee": _f(p.setup_fee if p.setup_fee is not None else p.price),
                      "monthly_price": _f(p.monthly_price),
                      "contract_monthly_price": _f(p.contract_monthly_price),
                      "contract_term_months": p.contract_term_months,
                      "compensation_configured": p.id in ruled}
                     for p in packages],
        "vocabulary": {
            "roles": list(POLICY_ROLES),
            "bases": [{"key": b, "label": BASIS_LABELS[b]} for b in BASES],
            "payee_kinds": list(PAYEE_KINDS),
            "deal_kinds": list(DEAL_KINDS),
            "eligibilities": list(ELIGIBILITIES),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Pricing policies
# ═══════════════════════════════════════════════════════════════════════════

class PolicyIn(BaseModel):
    brand_sales_org_id: Optional[str] = None
    role: Optional[str] = None
    max_discount_pct_setup: Optional[float] = None
    max_discount_pct_monthly: Optional[float] = None
    min_term_months: Optional[int] = None
    below_floor_requires_approval: Optional[bool] = True
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_active: Optional[bool] = True
    note: Optional[str] = None


def _validate_policy(body: PolicyIn, db: Session) -> None:
    if body.role is not None and body.role not in POLICY_ROLES:
        raise HTTPException(status_code=400,
                            detail="Role must be one of %s, or blank for every role."
                                   % ", ".join(POLICY_ROLES))
    if body.brand_sales_org_id:
        if not db.query(BrandSalesOrg).filter(
                BrandSalesOrg.id == body.brand_sales_org_id).first():
            raise HTTPException(status_code=404, detail="Sales organization not found.")
    for field in ("max_discount_pct_setup", "max_discount_pct_monthly"):
        v = getattr(body, field)
        if v is not None and not (0 <= v <= 100):
            raise HTTPException(status_code=400,
                                detail="A discount ceiling is a percentage between 0 and 100.")
    if (body.effective_from and body.effective_to
            and body.effective_to < body.effective_from):
        raise HTTPException(status_code=400,
                            detail="The end date is before the start date.")


@router.post("/policies")
def create_policy(body: PolicyIn, db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    _validate_policy(body, db)
    p = PricingPolicy(
        brand_sales_org_id=body.brand_sales_org_id or None,
        role=body.role or None,
        max_discount_pct_setup=_dec(body.max_discount_pct_setup),
        max_discount_pct_monthly=_dec(body.max_discount_pct_monthly),
        min_term_months=body.min_term_months,
        below_floor_requires_approval=(True if body.below_floor_requires_approval
                                       is None else bool(body.below_floor_requires_approval)),
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        is_active=True if body.is_active is None else bool(body.is_active),
        note=(body.note or None),
        created_by=user.id,
    )
    db.add(p)
    db.flush()
    _audit(db, user, "pricing_policy.created", AUDIT_TARGET_POLICY, p.id,
           before=None, after=_policy_out(p))
    db.commit()
    return _policy_out(p)


@router.patch("/policies/{policy_id}")
def update_policy(policy_id: str, body: PolicyIn,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    p = db.query(PricingPolicy).filter(PricingPolicy.id == policy_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    _validate_policy(body, db)
    before = _policy_out(p)

    data = body.dict(exclude_unset=True)
    if "brand_sales_org_id" in data:
        p.brand_sales_org_id = data["brand_sales_org_id"] or None
    if "role" in data:
        p.role = data["role"] or None
    if "max_discount_pct_setup" in data:
        p.max_discount_pct_setup = _dec(data["max_discount_pct_setup"])
    if "max_discount_pct_monthly" in data:
        p.max_discount_pct_monthly = _dec(data["max_discount_pct_monthly"])
    if "min_term_months" in data:
        p.min_term_months = data["min_term_months"]
    if "below_floor_requires_approval" in data:
        p.below_floor_requires_approval = bool(data["below_floor_requires_approval"])
    if "effective_from" in data:
        p.effective_from = data["effective_from"]
    if "effective_to" in data:
        p.effective_to = data["effective_to"]
    if "is_active" in data:
        p.is_active = bool(data["is_active"])
    if "note" in data:
        p.note = data["note"] or None

    _audit(db, user, "pricing_policy.updated", AUDIT_TARGET_POLICY, p.id,
           before=before, after=_policy_out(p))
    db.commit()
    return _policy_out(p)


# ═══════════════════════════════════════════════════════════════════════════
# Compensation plans, rules and caps
# ═══════════════════════════════════════════════════════════════════════════

class PlanIn(BaseModel):
    brand_sales_org_id: Optional[str] = None
    name: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    holdback_days: Optional[int] = None
    max_override_levels: Optional[int] = None
    is_active: Optional[bool] = None
    note: Optional[str] = None


@router.post("/plans")
def create_plan(body: PlanIn, db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    if not (body.name or "").strip():
        raise HTTPException(status_code=400, detail="A plan needs a name.")
    if body.effective_from is None:
        raise HTTPException(
            status_code=400,
            detail="A plan needs a start date. Without one it cannot be "
                   "superseded later without rewriting history.")
    if body.brand_sales_org_id and not db.query(BrandSalesOrg).filter(
            BrandSalesOrg.id == body.brand_sales_org_id).first():
        raise HTTPException(status_code=404, detail="Sales organization not found.")
    p = CompensationPlan(
        brand_sales_org_id=body.brand_sales_org_id or None,
        name=body.name.strip(),
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        holdback_days=(14 if body.holdback_days is None else int(body.holdback_days)),
        max_override_levels=(1 if body.max_override_levels is None
                             else int(body.max_override_levels)),
        is_active=True if body.is_active is None else bool(body.is_active),
        note=(body.note or None),
        created_by=user.id,
    )
    db.add(p)
    db.flush()
    _audit(db, user, "compensation_plan.created", AUDIT_TARGET_PLAN, p.id,
           before=None, after=_plan_out(db, p))
    db.commit()
    return _plan_out(db, p)


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: str, body: PlanIn, db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Editing a plan changes what FUTURE deals earn.

    It cannot change what a past one earned: `CompensationEntry` snapshots the
    rate that produced it, so nothing here reaches an existing payout. That is
    asserted in the tests rather than left as an intention.
    """
    p = db.query(CompensationPlan).filter(CompensationPlan.id == plan_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    before = _plan_out(db, p)
    data = body.dict(exclude_unset=True)
    for field in ("name", "effective_from", "effective_to", "holdback_days",
                  "max_override_levels", "note"):
        if field in data:
            v = data[field]
            if field == "name":
                if not (v or "").strip():
                    raise HTTPException(status_code=400, detail="A plan needs a name.")
                v = v.strip()
            if field == "note":
                v = v or None
            setattr(p, field, v)
    if "is_active" in data:
        p.is_active = bool(data["is_active"])
    if "brand_sales_org_id" in data:
        p.brand_sales_org_id = data["brand_sales_org_id"] or None
    _audit(db, user, "compensation_plan.updated", AUDIT_TARGET_PLAN, p.id,
           before=before, after=_plan_out(db, p))
    db.commit()
    return _plan_out(db, p)


class RuleIn(BaseModel):
    package_id: Optional[str] = None
    deal_kind: Optional[str] = None
    payee_kind: Optional[str] = None
    override_level: Optional[int] = None
    basis: Optional[str] = None
    amount: Optional[float] = None
    percent: Optional[float] = None
    recurring_months: Optional[int] = None
    eligible_payment: Optional[str] = None
    max_amount: Optional[float] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    note: Optional[str] = None


def _validate_rule(body: RuleIn, existing: Optional[CompensationRule] = None) -> None:
    basis = body.basis or (existing.basis if existing else None)
    payee = body.payee_kind or (existing.payee_kind if existing else None)
    if basis not in BASES:
        raise HTTPException(status_code=400,
                            detail="Basis must be one of: %s" % ", ".join(BASES))
    if payee not in PAYEE_KINDS:
        raise HTTPException(status_code=400,
                            detail="Payee must be one of: %s" % ", ".join(PAYEE_KINDS))
    level = body.override_level if body.override_level is not None else (
        existing.override_level if existing else None)
    if payee == PAYEE_OVERRIDE and not level:
        raise HTTPException(
            status_code=400,
            detail="An override rule needs a level — 1 is the seller's direct "
                   "manager, 2 that person's manager.")
    if payee == PAYEE_SELLER and level:
        raise HTTPException(status_code=400,
                            detail="A seller rule has no override level.")
    if body.percent is not None and not (0 <= body.percent <= 100):
        raise HTTPException(status_code=400,
                            detail="A percentage is between 0 and 100.")
    if body.deal_kind is not None and body.deal_kind not in DEAL_KINDS:
        raise HTTPException(status_code=400,
                            detail="Deal kind must be one of: %s" % ", ".join(DEAL_KINDS))


@router.post("/plans/{plan_id}/rules")
def create_rule(plan_id: str, body: RuleIn, db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    plan = db.query(CompensationPlan).filter(CompensationPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    _validate_rule(body)
    # NOTE: a rule with neither amount nor percent is allowed on purpose. It is
    # how a package is listed as "somebody still has to decide this" rather than
    # being absent from the screen entirely.
    r = CompensationRule(
        plan_id=plan.id,
        package_id=body.package_id or None,
        deal_kind=body.deal_kind or None,
        payee_kind=body.payee_kind,
        override_level=body.override_level,
        basis=body.basis,
        amount=_dec(body.amount),
        percent=_dec(body.percent),
        recurring_months=body.recurring_months,
        eligible_payment=body.eligible_payment or None,
        max_amount=_dec(body.max_amount),
        sort_order=body.sort_order or 0,
        is_active=True if body.is_active is None else bool(body.is_active),
        note=(body.note or None),
    )
    db.add(r)
    db.flush()
    _audit(db, user, "compensation_rule.created", AUDIT_TARGET_RULE, r.id,
           before=None, after=_rule_out(r), note="plan=%s" % plan.name)
    db.commit()
    return _rule_out(r)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleIn, db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    r = db.query(CompensationRule).filter(CompensationRule.id == rule_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    _validate_rule(body, existing=r)
    before = _rule_out(r)
    data = body.dict(exclude_unset=True)
    for field in ("package_id", "deal_kind", "payee_kind", "override_level",
                  "basis", "recurring_months", "eligible_payment", "sort_order",
                  "note"):
        if field in data:
            v = data[field]
            if field in ("package_id", "deal_kind", "eligible_payment", "note"):
                v = v or None
            setattr(r, field, v)
    for field in ("amount", "percent", "max_amount"):
        if field in data:
            setattr(r, field, _dec(data[field]))
    if "is_active" in data:
        r.is_active = bool(data["is_active"])
    _audit(db, user, "compensation_rule.updated", AUDIT_TARGET_RULE, r.id,
           before=before, after=_rule_out(r))
    db.commit()
    return _rule_out(r)


@router.delete("/rules/{rule_id}")
def deactivate_rule(rule_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_god)):
    """DEACTIVATES, never deletes.

    A deleted rule cannot explain the payout it produced. Every entry points at
    its rule id, and a dangling one turns a payroll record into an unanswerable
    question six months later.
    """
    r = db.query(CompensationRule).filter(CompensationRule.id == rule_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    before = _rule_out(r)
    r.is_active = False
    _audit(db, user, "compensation_rule.deactivated", AUDIT_TARGET_RULE, r.id,
           before=before, after=_rule_out(r))
    db.commit()
    return {"ok": True, "rule": _rule_out(r)}


class CapIn(BaseModel):
    package_id: str
    max_total_payout: float
    note: Optional[str] = None


@router.post("/plans/{plan_id}/caps")
def upsert_cap(plan_id: str, body: CapIn, db: Session = Depends(get_db),
               user: User = Depends(require_god)):
    """The most this plan pays IN TOTAL on one package, across everyone.

    Upsert rather than create: two caps on one package is two answers to one
    question, and the unique constraint would refuse the second anyway with an
    error the screen could not explain.
    """
    plan = db.query(CompensationPlan).filter(CompensationPlan.id == plan_id).first()
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if not db.query(BrandPackage).filter(BrandPackage.id == body.package_id).first():
        raise HTTPException(status_code=404, detail="Package not found")
    amount = _dec(body.max_total_payout)
    if amount is None or amount < 0:
        raise HTTPException(status_code=400, detail="A cap cannot be negative.")

    cap = (db.query(CompensationPackageCap)
           .filter(CompensationPackageCap.plan_id == plan_id,
                   CompensationPackageCap.package_id == body.package_id).first())
    before = ({"max_total_payout": _f(cap.max_total_payout)} if cap else None)
    if cap is None:
        cap = CompensationPackageCap(plan_id=plan_id, package_id=body.package_id,
                                     max_total_payout=amount,
                                     note=(body.note or None))
        db.add(cap)
    else:
        cap.max_total_payout = amount
        if body.note is not None:
            cap.note = body.note or None
    db.flush()
    _audit(db, user, "compensation_cap.set", AUDIT_TARGET_CAP, cap.id,
           before=before, after={"max_total_payout": _f(cap.max_total_payout)},
           note="plan=%s" % plan.name)
    db.commit()
    return {"id": cap.id, "package_id": cap.package_id,
            "max_total_payout": _f(cap.max_total_payout), "note": cap.note}


# ═══════════════════════════════════════════════════════════════════════════
# Audit history
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/audit")
def pricing_audit(limit: int = Query(50, le=200),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    """Who changed a floor or a rate, from what, to what, and when."""
    rows = (db.query(AuditLogEntry)
            .filter(AuditLogEntry.target_type.in_(
                [AUDIT_TARGET_POLICY, AUDIT_TARGET_PLAN,
                 AUDIT_TARGET_RULE, AUDIT_TARGET_CAP]))
            .order_by(AuditLogEntry.created_at.desc())
            .limit(limit).all())
    actors = {}
    ids = {r.actor_user_id for r in rows if r.actor_user_id}
    if ids:
        for u in db.query(User).filter(User.id.in_(ids)).all():
            actors[u.id] = u.full_name or u.email
    return {"entries": [{
        "id": r.id,
        "action": r.action,
        "target_type": r.target_type,
        "target_id": r.target_id,
        "actor_name": actors.get(r.actor_user_id) or "—",
        "before": r.before_state,
        "after": r.after_state,
        "note": r.note,
        "created_at": r.created_at,
    } for r in rows]}


# ═══════════════════════════════════════════════════════════════════════════
# SEEDING THE DECIDED EVOSYS RULES
#
# A PREVIEW AND A COMMIT, NOT A SCRIPT. Writing commission rates into a live
# database from a migration or a console command means nobody sees what changed
# until somebody's cheque is wrong. This shows exactly what it will write, and
# writes only when a god_admin says so.
#
# Idempotent, and it never overwrites. A rule already on the plan is reported
# as `kept` — a seed that replaced an existing figure would silently revert a
# deliberate change made on this very screen.
# ═══════════════════════════════════════════════════════════════════════════

class SeedIn(BaseModel):
    brand_sales_org_id: str
    effective_from: Optional[date] = None
    # Explicit, and false by default. The preview is the safe call; writing
    # payroll configuration should require saying so.
    confirm: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# WHO MAY SEE AND SETTLE THIS BRAND'S COMPENSATION
#
# WHY IT LIVES HERE. Brand-scoped capabilities are, today, exactly the two
# compensation ones, and the person deciding who runs a commission run is
# already on this screen deciding what a commission IS. Users & Identity
# administers PEOPLE across the platform; this administers one commercial
# authority over one brand. If brand-scoped capabilities ever grow beyond
# compensation, this belongs in a general capability surface instead.
#
# ROLES ARE NOT INVENTED HERE. There is no "Finance Manager" to assign — a
# named role would be a second permission model beside the capability one, and
# the first time they disagreed nobody would know which was authoritative.
# Roles may bundle capabilities later; authorization stays capability + scope.
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/brand-access")
def brand_access(brand_sales_org_id: str = Query(...),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    """Everyone who holds a compensation capability over this brand.

    Candidates are the brand's own sales people plus anybody already granted —
    a finance user with no membership must remain visible here, or revoking
    their access would mean finding them by memory.
    """
    from app.models.models import UserCapabilityGrant
    from app.models.sales_models import SCOPE_BRAND_SALES_ORG, Membership
    from app.services import capabilities as caps

    brand = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == brand_sales_org_id).first())
    if brand is None:
        raise HTTPException(status_code=404, detail="Sales organization not found.")

    granted_ids = {r.user_id for r in db.query(UserCapabilityGrant).filter(
        UserCapabilityGrant.scope_type == SCOPE_BRAND_SALES_ORG,
        UserCapabilityGrant.scope_id == brand.id,
        UserCapabilityGrant.is_active.is_(True)).all()}
    member_ids = {m.user_id for m in db.query(Membership).filter(
        Membership.scope_type == SCOPE_BRAND_SALES_ORG,
        Membership.scope_id == brand.id,
        Membership.is_active.is_(True)).all()}

    ids = sorted(granted_ids | member_ids)
    people = []
    if ids:
        for u in db.query(User).filter(User.id.in_(ids)).all():
            held = caps.brand_grants_for(db, u.id, brand.id)
            people.append({
                "user_id": u.id,
                "name": u.full_name or u.email,
                "email": u.email,
                "is_brand_member": u.id in member_ids,
                "capabilities": held,
            })
    people.sort(key=lambda p: p["name"].lower())

    return {
        "brand": {"id": brand.id, "name": brand.name},
        "people": people,
        "available_capabilities": [
            {"key": k, "label": caps.CAPABILITIES[k].label,
             "why": caps.CAPABILITIES[k].why}
            for k in caps.BRAND_SCOPED_CAPABILITIES if k in caps.CAPABILITIES
        ],
    }


class BrandAccessIn(BaseModel):
    brand_sales_org_id: str
    user_id: str
    # The COMPLETE set this person should hold over this brand. An empty list
    # revokes everything, which is why it is a replace rather than an add: a
    # grant screen that can only add is a screen nobody can undo a mistake on.
    capabilities: List[str] = []


@router.put("/brand-access")
def set_brand_access(body: BrandAccessIn, db: Session = Depends(get_db),
                     user: User = Depends(require_god)):
    """Grant or revoke compensation capabilities over one brand, for one person."""
    from app.services import capabilities as caps

    brand = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == body.brand_sales_org_id).first())
    if brand is None:
        raise HTTPException(status_code=404, detail="Sales organization not found.")
    target = db.query(User).filter(User.id == body.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    after = caps.set_brand_grants(db, target, brand.id, brand.name, user,
                                  body.capabilities)
    db.commit()
    return {"user_id": target.id, "brand_sales_org_id": brand.id,
            "capabilities": after}


@router.post("/seed/evosys")
def seed_evosys(body: SeedIn, db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Preview (default) or apply the decided EvoSys compensation rules."""
    from app.services import evosys_comp_seed as seed

    brand = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == body.brand_sales_org_id).first())
    if brand is None:
        raise HTTPException(status_code=404, detail="Sales organization not found.")

    if not body.confirm:
        return seed.preview(db, brand, effective_from=body.effective_from)

    result = seed.apply(db, brand, created_by=user.id,
                        effective_from=body.effective_from, commit=False)
    _audit(db, user, "compensation_plan.seeded", AUDIT_TARGET_PLAN,
           brand.id, before=None,
           after={"written": result.get("written"),
                  "unconfigured": result.get("unconfigured_packages")},
           note="EvoSys decided rules seeded for %s" % brand.name)
    db.commit()
    return result
