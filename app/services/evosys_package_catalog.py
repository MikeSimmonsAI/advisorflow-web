"""EvoSys Pro standard packages: apply the approved prices to production.

ONE SOURCE. The approved Basic / Growth / Professional prices live in
`deal_builder_commission.STANDARD_PACKAGES` (version STANDARD_PRICES_VERSION).
This module writes them, idempotently, into everything that charges a customer:

  brand_packages       the sales catalogue a deal is priced from
                       (setup fee, monthly price; the old 13-month term
                       discount is withdrawn - the approved model has one
                       monthly price, starting in Month 2)
  brand_billing_plans  the subscription catalogue checkout reads
                       (term and month-to-month both = the approved price,
                       so a legacy "term" selector cannot charge $500/$1,000/
                       $2,000 on a new deal)
  Stripe (TEST key)    one recurring Price per package at the approved amount,
                       reused when it already exists, mapped into both
                       columns. Earlier Price ids are kept in
                       `stripe_price_ids_legacy`, so subscriptions already on
                       them keep resolving to their plan on every renewal.

NOT TOUCHED: existing Stripe subscriptions and invoices, existing payment
links, accepted agreements, booked commission records, Custom SaaS
(`multi_tenant` / `enterprise`), add-ons, and every other brand.

Basic's catalogue key stays `starter` - existing deals, links, Stripe metadata
and organizations carry it - and only the name becomes "Basic".

Run from God Mode: GET .../evosys-standard-packages (preview + verification),
POST .../evosys-standard-packages/apply. A second apply changes nothing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import BrandBillingPlan
from app.models.sales_models import BrandPackage
from app.services import deal_builder_commission as dbc

log = logging.getLogger(__name__)

COMMITMENT = "standard"


def _platform_id(db: Session) -> Optional[str]:
    from app.services import sales_team
    brand = sales_team.program_brand(db)
    return getattr(brand, "platform_id", None)


def _num(v) -> Optional[float]:
    return None if v is None else float(Decimal(str(v)))


def _package_target(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": spec["name"], "setup_fee": float(spec["setup"]), "price": float(spec["setup"]),
            "monthly_price": float(spec["monthly"]), "contract_monthly_price": None,
            "contract_term_months": None, "billing_plan_key": spec["catalog_key"],
            "is_active": True}


def _plan_target(spec: Dict[str, Any]) -> Dict[str, Any]:
    cents = int(Decimal(spec["monthly"]) * 100)
    return {"name": spec["name"], "monthly_cents": cents, "month_to_month_cents": cents,
            "is_active": True, "is_purchasable": True}


def _pkg_now(pkg: BrandPackage) -> Dict[str, Any]:
    return {"name": pkg.name, "setup_fee": _num(pkg.setup_fee), "price": _num(pkg.price),
            "monthly_price": _num(pkg.monthly_price),
            "contract_monthly_price": _num(pkg.contract_monthly_price),
            "contract_term_months": pkg.contract_term_months,
            "billing_plan_key": pkg.billing_plan_key, "is_active": bool(pkg.is_active)}


def _plan_now(plan: BrandBillingPlan) -> Dict[str, Any]:
    return {"name": plan.name, "monthly_cents": plan.monthly_cents,
            "month_to_month_cents": plan.month_to_month_cents,
            "is_active": bool(plan.is_active), "is_purchasable": bool(plan.is_purchasable)}


def _diff(now: Dict[str, Any], want: Dict[str, Any]) -> Dict[str, Any]:
    return {k: {"from": now.get(k), "to": v} for k, v in want.items() if now.get(k) != v}


# ── Stripe ───────────────────────────────────────────────────────────────────

def _stripe():
    """The TEST-mode client from stripe_provisioning (refuses a live key)."""
    from app.services import stripe_provisioning
    return stripe_provisioning._client()


def _find_or_create_price(stripe, plan: BrandBillingPlan, cents: int, *,
                          create: bool) -> Dict[str, Any]:
    """The one approved recurring Price for this plan. Reused when one exists."""
    from app.services import stripe_provisioning as sp
    currency = (plan.currency or "usd").lower()
    for pid in (plan.stripe_price_id_month_to_month, plan.stripe_price_id_monthly):
        if not pid:
            continue
        try:
            price = stripe.Price.retrieve(pid)
            if sp._price_matches(price, cents, currency):
                return {"id": price["id"], "action": "reused_mapped"}
        except Exception as exc:                         # pragma: no cover
            log.warning("evosys catalogue: mapped price %s unreadable: %s", pid, exc)
    if plan.stripe_product_id:
        try:
            listed = stripe.Price.list(product=plan.stripe_product_id, active=True, limit=100)
            for price in (listed or {}).get("data") or []:
                md = price.get("metadata") or {}
                if (sp._price_matches(price, cents, currency)
                        and md.get("commitment") == COMMITMENT):
                    return {"id": price["id"], "action": "reused_found"}
        except Exception as exc:                         # pragma: no cover
            log.info("evosys catalogue: price list unavailable (%s)", exc)
    if not plan.stripe_product_id:
        return {"id": None, "action": "blocked",
                "reason": "No Stripe product is mapped for this plan."}
    if not create:
        return {"id": None, "action": "would_create"}
    price = stripe.Price.create(
        product=plan.stripe_product_id, currency=currency, unit_amount=int(cents),
        recurring={"interval": "month", "interval_count": 1},
        nickname="%s — monthly from Month 2 (%s)" % (plan.name, dbc.STANDARD_PRICES_VERSION),
        metadata={"platform_id": plan.platform_id, "plan_key": plan.key,
                  "commitment": COMMITMENT, "managed_by": "advisorflow",
                  "prices_version": dbc.STANDARD_PRICES_VERSION})
    return {"id": price["id"], "action": "created"}


# ── Plan / apply ─────────────────────────────────────────────────────────────

def run(db: Session, *, apply: bool = False, stripe_client=None) -> Dict[str, Any]:
    """Preview (default) or apply. Returns before/after for every object."""
    platform_id = _platform_id(db)
    out: Dict[str, Any] = {"version": dbc.STANDARD_PRICES_VERSION, "platform_id": platform_id,
                           "applied": False, "packages": [], "blockers": []}
    if not platform_id:
        out["blockers"].append("The EvoSys Pro Sales brand was not found.")
        return out
    stripe = stripe_client
    stripe_error = None
    if stripe is None:
        try:
            stripe = _stripe()
        except Exception as exc:
            stripe_error = str(exc)
            out["blockers"].append("Stripe: %s" % exc)

    for spec in dbc.STANDARD_PACKAGES:
        key = spec["catalog_key"]
        row: Dict[str, Any] = {"package": spec["name"], "catalog_key": key,
                               "approved": {"setup": float(spec["setup"]),
                                            "monthly": float(spec["monthly"]),
                                            "payout": {r: float(v) for r, v in spec["payout"].items()}}}
        pkg = (db.query(BrandPackage)
               .filter(BrandPackage.platform_id == platform_id, BrandPackage.key == key).first())
        plan = (db.query(BrandBillingPlan)
                .filter(BrandBillingPlan.platform_id == platform_id, BrandBillingPlan.key == key).first())
        if pkg is None or plan is None:
            row["blocker"] = "Missing %s for key %r." % ("package" if pkg is None else "billing plan", key)
            out["blockers"].append(row["blocker"])
            out["packages"].append(row)
            continue
        row["package_id"] = pkg.id
        row["package_before"] = _pkg_now(pkg)
        row["package_changes"] = _diff(row["package_before"], _package_target(spec))
        row["plan_before"] = dict(_plan_now(plan), stripe_price_id_monthly=plan.stripe_price_id_monthly,
                                  stripe_price_id_month_to_month=plan.stripe_price_id_month_to_month)
        row["plan_changes"] = _diff(_plan_now(plan), _plan_target(spec))
        cents = int(Decimal(spec["monthly"]) * 100)
        price = ({"id": None, "action": "blocked", "reason": stripe_error}
                 if stripe is None else
                 _find_or_create_price(stripe, plan, cents, create=apply))
        row["stripe_price"] = price

        if apply and row["package_changes"]:
            for k, ch in row["package_changes"].items():
                setattr(pkg, k, ch["to"])
            pkg.updated_at = datetime.utcnow()
        if apply and row["plan_changes"]:
            for k, ch in row["plan_changes"].items():
                setattr(plan, k, ch["to"])
        if apply and price.get("id"):
            from app.services import billing_catalog
            legacy = billing_catalog.legacy_price_ids(plan)
            for old in (plan.stripe_price_id_monthly, plan.stripe_price_id_month_to_month):
                if old and old != price["id"] and old not in legacy:
                    legacy.append(old)
            plan.stripe_price_ids_legacy = json.dumps(legacy) if legacy else None
            plan.stripe_price_id_monthly = price["id"]
            plan.stripe_price_id_month_to_month = price["id"]
        out["packages"].append(row)

    if apply:
        db.flush()
        out["applied"] = True
    out["verification"] = verify(db, stripe_client=stripe, stripe_error=stripe_error)
    return out


def verify(db: Session, *, stripe_client=None, stripe_error: Optional[str] = None) -> Dict[str, Any]:
    """Read-only: does every layer charge the approved amounts?"""
    from app.services import billing_catalog, stripe_provisioning as sp
    platform_id = _platform_id(db)
    rows: List[Dict[str, Any]] = []
    ok = bool(platform_id)
    stripe = stripe_client
    for spec in dbc.STANDARD_PACKAGES:
        key = spec["catalog_key"]
        cents = int(Decimal(spec["monthly"]) * 100)
        pkg = (db.query(BrandPackage)
               .filter(BrandPackage.platform_id == platform_id, BrandPackage.key == key).first())
        plan = (db.query(BrandBillingPlan)
                .filter(BrandBillingPlan.platform_id == platform_id, BrandBillingPlan.key == key).first())
        r: Dict[str, Any] = {"package": spec["name"], "catalog_key": key, "checks": {}}
        c = r["checks"]
        c["package_setup"] = bool(pkg) and _num(pkg.setup_fee) == float(spec["setup"])
        c["package_monthly"] = bool(pkg) and _num(pkg.monthly_price) == float(spec["monthly"])
        c["no_term_discount"] = bool(pkg) and pkg.contract_monthly_price is None
        c["package_maps_plan"] = bool(pkg) and pkg.billing_plan_key == key
        c["plan_amounts"] = bool(plan) and plan.monthly_cents == cents and plan.month_to_month_cents == cents
        c["plan_price_mapped"] = bool(plan) and bool(plan.stripe_price_id_monthly) and \
            plan.stripe_price_id_monthly == plan.stripe_price_id_month_to_month
        r["stripe_price_id"] = getattr(plan, "stripe_price_id_monthly", None)
        r["legacy_price_ids"] = billing_catalog.legacy_price_ids(plan) if plan else []
        if stripe is not None and r["stripe_price_id"]:
            try:
                price = stripe.Price.retrieve(r["stripe_price_id"])
                c["stripe_price_amount"] = sp._price_matches(price, cents, (plan.currency or "usd"))
                r["stripe_unit_amount"] = price.get("unit_amount")
                r["stripe_livemode"] = price.get("livemode")
            except Exception as exc:                     # pragma: no cover
                c["stripe_price_amount"] = False
                r["stripe_error"] = str(exc)[:200]
        else:
            r["stripe_note"] = stripe_error or "Stripe not checked"
        ok = ok and all(c.values())
        rows.append(r)
    return {"all_match": ok, "stripe_checked": stripe is not None, "packages": rows}
