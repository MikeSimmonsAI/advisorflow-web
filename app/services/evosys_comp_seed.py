"""THE EVOSYS RULES THAT HAVE ACTUALLY BEEN DECIDED, AND ONLY THOSE.

This file is a transcription, not a design. Every figure below was stated
explicitly by the owner; nothing here was derived, rounded, or inferred from a
neighbouring package.

WHAT IS DECIDED
    Starter, $1,497 implementation
        salesperson  $500 fixed
        manager      $100 fixed, level 1, ONLY where an eligible manager exists
        cap          $800 total across everyone on this package
        holdback     14 days after the collected-funds requirement is met

    Multi-tenant SaaS
        salesperson  10% of the eligible initial payment
                     10% of eligible recurring payments

WHAT IS NOT DECIDED, AND IS THEREFORE NOT WRITTEN
    Growth and Professional commission amounts. There is no rule for them and
    this file will not invent one. They report UNCONFIGURED on the console,
    which is a different statement from $0: zero reads as "we decided these pay
    nothing", unconfigured reads as "somebody still has to decide". The
    difference is a business decision that has not been made, and a seed script
    is the worst possible place to make it.

    Override amounts above level 1. Contemplated, not specified.

IDEMPOTENT AND PREVIEWABLE. `plan()` computes what would happen and writes
nothing; `apply()` performs it and is safe to run twice. Nothing here deletes
or overwrites a figure somebody has already set by hand - an existing rule is
reported as `kept`, never silently replaced, because a seed that overwrites is
a seed that quietly reverts a deliberate change.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.compensation_models import (BASIS_FIXED, BASIS_PCT_COLLECTED,
                                            ELIGIBLE_BOTH, ELIGIBLE_INITIAL,
                                            PAYEE_OVERRIDE, PAYEE_SELLER,
                                            CompensationPackageCap,
                                            CompensationPlan, CompensationRule)
from app.models.sales_models import BrandPackage, BrandSalesOrg

# Package keys this seed knows about, matched case-insensitively on `key` and
# then on `name`. A package that does not match is left entirely alone.
STARTER_KEYS = ("starter",)
SAAS_KEYS = ("multi_tenant", "multi-tenant", "multitenant", "saas")

# Packages that exist and are DELIBERATELY not given a rule here.
UNDECIDED_KEYS = ("growth", "professional")

PLAN_NAME_DIRECT = "EvoSys Direct Sales"
PLAN_NAME_SAAS = "EvoSys Multi-Tenant SaaS"

STARTER_SELLER_AMOUNT = Decimal("500.00")
STARTER_OVERRIDE_AMOUNT = Decimal("100.00")
STARTER_TOTAL_CAP = Decimal("800.00")
HOLDBACK_DAYS = 14
SAAS_PERCENT = Decimal("10.000")


def _match(pkg: BrandPackage, keys) -> bool:
    hay = ((pkg.key or "") + " " + (pkg.name or "")).lower()
    return any(k in hay for k in keys)


def _packages(db: Session, brand: BrandSalesOrg) -> List[BrandPackage]:
    return (db.query(BrandPackage)
            .filter(BrandPackage.platform_id == brand.platform_id,
                    BrandPackage.is_active.is_(True))
            .order_by(BrandPackage.sort_order.asc()).all())


def _describe(db: Session, brand: BrandSalesOrg,
              effective_from: date) -> Dict[str, Any]:
    """What the seed WOULD do. Shared by the preview and the write, so the
    preview cannot drift from what actually happens."""
    pkgs = _packages(db, brand)
    starter = next((p for p in pkgs if _match(p, STARTER_KEYS)), None)
    saas = next((p for p in pkgs if _match(p, SAAS_KEYS)), None)
    undecided = [p for p in pkgs if _match(p, UNDECIDED_KEYS)]
    # Anything else with no rule is also unconfigured; named so the screen can
    # list every open decision rather than only the two we happen to know about.
    others = [p for p in pkgs
              if p is not starter and p is not saas and p not in undecided]

    existing_direct = (db.query(CompensationPlan)
                       .filter(CompensationPlan.brand_sales_org_id == brand.id,
                               CompensationPlan.name == PLAN_NAME_DIRECT).first())
    existing_saas = (db.query(CompensationPlan)
                     .filter(CompensationPlan.brand_sales_org_id == brand.id,
                             CompensationPlan.name == PLAN_NAME_SAAS).first())

    actions: List[Dict[str, Any]] = []

    def note(kind, what, detail, blocked=None):
        actions.append({"action": kind, "what": what, "detail": detail,
                        "blocked_reason": blocked})

    if starter is None:
        note("skip", PLAN_NAME_DIRECT,
             "No Starter package found on this brand's platform.",
             blocked="starter_package_missing")
    else:
        note("create" if existing_direct is None else "keep", PLAN_NAME_DIRECT,
             "Plan effective %s, %d-day holdback, 1 override level."
             % (effective_from, HOLDBACK_DAYS))
        note("create", "Starter — salesperson",
             "$%s fixed" % STARTER_SELLER_AMOUNT)
        note("create", "Starter — manager override",
             "$%s fixed at level 1, paid only where an eligible manager exists"
             % STARTER_OVERRIDE_AMOUNT)
        note("create", "Starter — payout cap",
             "$%s total across everyone on this package" % STARTER_TOTAL_CAP)

    if saas is None:
        note("skip", PLAN_NAME_SAAS,
             "No multi-tenant / SaaS package found on this brand's platform.",
             blocked="saas_package_missing")
    else:
        note("create" if existing_saas is None else "keep", PLAN_NAME_SAAS,
             "Plan effective %s, %d-day holdback." % (effective_from, HOLDBACK_DAYS))
        note("create", "Multi-tenant — salesperson",
             "%s%% of the eligible initial payment" % SAAS_PERCENT.normalize())
        note("create", "Multi-tenant — salesperson, recurring",
             "%s%% of eligible recurring payments" % SAAS_PERCENT.normalize())

    for p in undecided + others:
        note("leave_unconfigured", p.name,
             "No commission rate has been decided for this package. Left "
             "unconfigured rather than defaulted to $0.")

    return {
        "brand_sales_org_id": brand.id,
        "brand_name": brand.name,
        "effective_from": effective_from,
        "starter_package": ({"id": starter.id, "name": starter.name}
                            if starter else None),
        "saas_package": ({"id": saas.id, "name": saas.name} if saas else None),
        "unconfigured_packages": [{"id": p.id, "name": p.name}
                                  for p in (undecided + others)],
        "actions": actions,
    }


def preview(db: Session, brand: BrandSalesOrg,
            effective_from: Optional[date] = None) -> Dict[str, Any]:
    """WRITES NOTHING. What the console shows before anybody commits."""
    out = _describe(db, brand, effective_from or date.today())
    out["applied"] = False
    return out


def _ensure_plan(db: Session, brand: BrandSalesOrg, name: str,
                 effective_from: date, created_by: Optional[str]) -> CompensationPlan:
    plan = (db.query(CompensationPlan)
            .filter(CompensationPlan.brand_sales_org_id == brand.id,
                    CompensationPlan.name == name).first())
    if plan is not None:
        return plan
    plan = CompensationPlan(brand_sales_org_id=brand.id, name=name,
                            effective_from=effective_from,
                            holdback_days=HOLDBACK_DAYS,
                            max_override_levels=1, is_active=True,
                            created_by=created_by,
                            note="Seeded from the rules stated by the owner. "
                                 "Only decided figures were written.")
    db.add(plan)
    db.flush()
    return plan


def _ensure_rule(db: Session, plan: CompensationPlan, *, package_id, payee_kind,
                 basis, amount=None, percent=None, override_level=None,
                 eligible_payment=None, recurring_months=None,
                 sort_order=0, note=None) -> Dict[str, Any]:
    """Create if absent; otherwise KEEP what is there.

    Never overwrites. A seed that replaces an existing figure is a seed that
    silently reverts a deliberate change somebody made on the console.
    """
    q = (db.query(CompensationRule)
         .filter(CompensationRule.plan_id == plan.id,
                 CompensationRule.payee_kind == payee_kind,
                 CompensationRule.basis == basis))
    q = (q.filter(CompensationRule.package_id == package_id) if package_id
         else q.filter(CompensationRule.package_id.is_(None)))
    if override_level is None:
        q = q.filter(CompensationRule.override_level.is_(None))
    else:
        q = q.filter(CompensationRule.override_level == override_level)
    if eligible_payment is not None:
        q = q.filter(CompensationRule.eligible_payment == eligible_payment)
    existing = q.first()
    if existing is not None:
        return {"status": "kept", "rule_id": existing.id}

    r = CompensationRule(plan_id=plan.id, package_id=package_id,
                         payee_kind=payee_kind, override_level=override_level,
                         basis=basis, amount=amount, percent=percent,
                         eligible_payment=eligible_payment,
                         recurring_months=recurring_months,
                         sort_order=sort_order, is_active=True, note=note)
    db.add(r)
    db.flush()
    return {"status": "created", "rule_id": r.id}


def apply(db: Session, brand: BrandSalesOrg, *, created_by: Optional[str] = None,
          effective_from: Optional[date] = None, commit: bool = True) -> Dict[str, Any]:
    """Write the decided rules. Idempotent; re-running keeps what exists."""
    effective_from = effective_from or date.today()
    result = _describe(db, brand, effective_from)
    written: List[Dict[str, Any]] = []

    pkgs = _packages(db, brand)
    starter = next((p for p in pkgs if _match(p, STARTER_KEYS)), None)
    saas = next((p for p in pkgs if _match(p, SAAS_KEYS)), None)

    if starter is not None:
        plan = _ensure_plan(db, brand, PLAN_NAME_DIRECT, effective_from, created_by)
        written.append({"what": "Starter — salesperson",
                        **_ensure_rule(db, plan, package_id=starter.id,
                                       payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                                       amount=STARTER_SELLER_AMOUNT,
                                       eligible_payment=ELIGIBLE_INITIAL,
                                       sort_order=1,
                                       note="Stated by the owner.")})
        written.append({"what": "Starter — manager override",
                        **_ensure_rule(db, plan, package_id=starter.id,
                                       payee_kind=PAYEE_OVERRIDE, basis=BASIS_FIXED,
                                       amount=STARTER_OVERRIDE_AMOUNT,
                                       override_level=1,
                                       eligible_payment=ELIGIBLE_INITIAL,
                                       sort_order=2,
                                       note="Paid only where an eligible manager "
                                            "exists in the org chart.")})
        cap = (db.query(CompensationPackageCap)
               .filter(CompensationPackageCap.plan_id == plan.id,
                       CompensationPackageCap.package_id == starter.id).first())
        if cap is None:
            db.add(CompensationPackageCap(
                plan_id=plan.id, package_id=starter.id,
                max_total_payout=STARTER_TOTAL_CAP,
                note="Total across everyone on this package. Stated by the owner."))
            written.append({"what": "Starter — payout cap", "status": "created"})
        else:
            written.append({"what": "Starter — payout cap", "status": "kept"})

    if saas is not None:
        plan = _ensure_plan(db, brand, PLAN_NAME_SAAS, effective_from, created_by)
        written.append({"what": "Multi-tenant — initial payment",
                        **_ensure_rule(db, plan, package_id=saas.id,
                                       payee_kind=PAYEE_SELLER,
                                       basis=BASIS_PCT_COLLECTED,
                                       percent=SAAS_PERCENT,
                                       eligible_payment=ELIGIBLE_INITIAL,
                                       sort_order=1,
                                       note="10% of the eligible initial payment.")})
        written.append({"what": "Multi-tenant — recurring payments",
                        **_ensure_rule(db, plan, package_id=saas.id,
                                       payee_kind=PAYEE_SELLER,
                                       basis=BASIS_PCT_COLLECTED,
                                       percent=SAAS_PERCENT,
                                       eligible_payment=ELIGIBLE_BOTH,
                                       sort_order=2,
                                       note="10% of eligible recurring payments.")})

    if commit:
        db.commit()
    result["applied"] = True
    result["written"] = written
    return result
