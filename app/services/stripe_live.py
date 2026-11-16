"""Stripe LIVE for new EvoSys Pro Sales checkout — and nothing else.

WHY A SEPARATE CONFIGURATION
The platform's STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET are the SANDBOX pair
and stay that way: self-serve billing, plan changes, catalogue purchases,
other brands, the two existing subscriptions and every sandbox record keep
using them. Live is a second, separate pair used ONLY when a NEW EvoSys Pro
Sales package deal creates a payment link while the God Mode switch says
"live":

    STRIPE_LIVE_SECRET_KEY      sk_live_… or rk_live_… (restricted key)
    STRIPE_LIVE_WEBHOOK_SECRET  whsec_… of the Live endpoint below

Both are environment variables on the backend service, entered by the owner
in the Render dashboard. This module never returns, logs or stores either
value - only whether it is present and which kind of key it is.

LIVE IDS ARE KEPT APART. Live products/prices live in
`brand_billing_plans.stripe_live_*`, Live customers in
`organizations.stripe_live_customer_id`. Sandbox ids are never overwritten.

THE SWITCH REFUSES UNTIL EVERYTHING VERIFIES: a Live key, an account with
charges enabled, the three Live prices at the approved amounts, the Live
webhook secret, and a Live webhook endpoint at WEBHOOK_PATH subscribed to
every event the handler needs.

Calls pass `api_key=` per request instead of setting the global
`stripe.api_key`, so a Live call can never leave the sandbox flows running on
the Live key (or the reverse) in a concurrent request.
"""
from __future__ import annotations

import calendar
import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import BrandBillingConfig, BrandBillingPlan
from app.services import deal_builder_commission as dbc

log = logging.getLogger(__name__)

ENV_KEY = "STRIPE_LIVE_SECRET_KEY"
ENV_WEBHOOK = "STRIPE_LIVE_WEBHOOK_SECRET"
LIVE_PREFIXES = ("sk_live_", "rk_live_")
MODE_TEST, MODE_LIVE = "test", "live"
WEBHOOK_PATH = "/billing/webhook/live"
# Everything billing_webhook handles, plus checkout.session.expired so the
# preflight probe (which opens and immediately expires a session, charging
# nothing) leaves a real Live delivery on record.
REQUIRED_EVENTS = (
    "checkout.session.completed",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "charge.refunded",
)
PROBE_MARK = "evosys_live_preflight"


class LiveNotReady(Exception):
    """A precondition for Live is missing. The message is safe to show."""


# ── configuration ────────────────────────────────────────────────────────────

def _key() -> str:
    return (os.environ.get(ENV_KEY) or "").strip()


def _hook_secret() -> str:
    return (os.environ.get(ENV_WEBHOOK) or "").strip()


def key_state() -> Dict[str, Any]:
    k = _key()
    kind = ("missing" if not k else
            "live_restricted" if k.startswith("rk_live_") else
            "live_secret" if k.startswith("sk_live_") else
            "not_live")
    return {"env": ENV_KEY, "present": bool(k), "kind": kind,
            "webhook_secret_env": ENV_WEBHOOK, "webhook_secret_present": bool(_hook_secret())}


def require_key() -> str:
    k = _key()
    if not k:
        raise LiveNotReady("%s is not set on the backend service." % ENV_KEY)
    if not k.startswith(LIVE_PREFIXES):
        raise LiveNotReady("%s is set but is not a Live key (expected sk_live_… or "
                           "rk_live_…). Refusing rather than guessing the mode." % ENV_KEY)
    return k


def webhook_secret() -> Optional[str]:
    return _hook_secret() or None


def backend_base_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("BACKEND_PUBLIC_URL")
            or "https://advisorflow-backend.onrender.com").rstrip("/")


def webhook_url() -> str:
    return backend_base_url() + WEBHOOK_PATH


def _stripe():
    import stripe
    return stripe


def _program_platform_id(db: Session) -> Optional[str]:
    from app.services import sales_team
    b = sales_team.program_brand(db)
    return getattr(b, "platform_id", None)


def _config(db: Session, platform_id: str, create: bool = False) -> Optional[BrandBillingConfig]:
    cfg = (db.query(BrandBillingConfig)
           .filter(BrandBillingConfig.platform_id == platform_id).first())
    if cfg is None and create:
        cfg = BrandBillingConfig(platform_id=platform_id)
        db.add(cfg)
        db.flush()
    return cfg


def checkout_mode(db: Session) -> str:
    """The Stripe mode NEW EvoSys Pro Sales checkout uses right now."""
    pid = _program_platform_id(db)
    cfg = _config(db, pid) if pid else None
    return MODE_LIVE if getattr(cfg, "sales_checkout_mode", None) == MODE_LIVE else MODE_TEST


def _plans(db: Session, platform_id: str) -> List[BrandBillingPlan]:
    keys = [p["catalog_key"] for p in dbc.STANDARD_PACKAGES]
    rows = (db.query(BrandBillingPlan)
            .filter(BrandBillingPlan.platform_id == platform_id,
                    BrandBillingPlan.key.in_(keys)).all())
    by = {r.key: r for r in rows}
    return [by[k] for k in keys if k in by]


def _spec(key: str) -> Dict[str, Any]:
    return next(p for p in dbc.STANDARD_PACKAGES if p["catalog_key"] == key)


# ── account ──────────────────────────────────────────────────────────────────

def account(stripe=None) -> Dict[str, Any]:
    stripe = stripe or _stripe()
    acct = stripe.Account.retrieve(api_key=require_key())
    settings = acct.get("settings") or {}
    return {"id": acct.get("id"),
            "business_name": ((settings.get("dashboard") or {}).get("display_name")
                              or (acct.get("business_profile") or {}).get("name")),
            "country": acct.get("country"),
            "default_currency": acct.get("default_currency"),
            "charges_enabled": bool(acct.get("charges_enabled")),
            "payouts_enabled": bool(acct.get("payouts_enabled")),
            "details_submitted": bool(acct.get("details_submitted"))}


# ── Live products and prices ─────────────────────────────────────────────────

def _price_ok(price: Dict[str, Any], cents: int) -> bool:
    rec = price.get("recurring") or {}
    return (bool(price.get("active")) and bool(price.get("livemode"))
            and int(price.get("unit_amount") or -1) == int(cents)
            and str(price.get("currency") or "").lower() == "usd"
            and rec.get("interval") == "month" and int(rec.get("interval_count") or 1) == 1)


def provision(db: Session, *, apply: bool = False, stripe=None) -> Dict[str, Any]:
    """Create or reuse the Live product and monthly price for each package.

    Idempotent: the mapped Live price is reused when it matches; otherwise an
    active matching price on the Live product (metadata-tagged) is adopted;
    only then is one created. A replaced Live price id is kept in the plan's
    legacy list so a Live subscription already on it still resolves.
    The one-time setup is not a Stripe price: checkout charges it as an
    exact one-time amount (price_data), as it does in the sandbox.
    """
    stripe = stripe or _stripe()
    key = require_key()
    pid = _program_platform_id(db)
    if not pid:
        raise LiveNotReady("The EvoSys Pro Sales brand was not found.")
    out: List[Dict[str, Any]] = []
    for plan in _plans(db, pid):
        spec = _spec(plan.key)
        cents = int(Decimal(spec["monthly"]) * 100)
        row: Dict[str, Any] = {"package": spec["name"], "catalog_key": plan.key,
                               "monthly_cents": cents, "setup_cents": int(Decimal(spec["setup"]) * 100)}
        md = {"platform_id": plan.platform_id, "plan_key": plan.key, "managed_by": "advisorflow",
              "commitment": "standard", "prices_version": dbc.STANDARD_PRICES_VERSION}
        # product
        prod_id, prod_action = plan.stripe_live_product_id, None
        if prod_id:
            try:
                p = stripe.Product.retrieve(prod_id, api_key=key)
                prod_action = "reused_mapped" if p and not p.get("deleted") else None
            except Exception:
                prod_action = None
        if not prod_action:
            prod_id = None
            try:
                found = stripe.Product.search(
                    query="metadata['platform_id']:'%s' AND metadata['plan_key']:'%s'"
                          % (plan.platform_id, plan.key), limit=1, api_key=key)
                data = (found or {}).get("data") or []
                if data:
                    prod_id, prod_action = data[0]["id"], "reused_found"
            except Exception as exc:
                log.info("stripe_live: product search unavailable (%s)", exc)
        if not prod_id:
            if apply:
                prod_id = stripe.Product.create(name="EvoSys Pro %s" % spec["name"],
                                                metadata=md, api_key=key)["id"]
                prod_action = "created"
            else:
                prod_action = "would_create"
        row["product"] = {"id": prod_id, "action": prod_action}
        # price
        price_id, price_action = None, None
        if plan.stripe_live_price_id:
            try:
                pr = stripe.Price.retrieve(plan.stripe_live_price_id, api_key=key)
                if _price_ok(pr, cents):
                    price_id, price_action = pr["id"], "reused_mapped"
            except Exception:
                pass
        if not price_id and prod_id:
            try:
                listed = stripe.Price.list(product=prod_id, active=True, limit=100, api_key=key)
                for pr in (listed or {}).get("data") or []:
                    if _price_ok(pr, cents) and (pr.get("metadata") or {}).get("commitment") == "standard":
                        price_id, price_action = pr["id"], "reused_found"
                        break
            except Exception as exc:
                log.info("stripe_live: price list unavailable (%s)", exc)
        if not price_id:
            if apply and prod_id:
                price_id = stripe.Price.create(
                    product=prod_id, currency="usd", unit_amount=cents,
                    recurring={"interval": "month", "interval_count": 1},
                    nickname="%s — monthly from Month 2 (%s)" % (spec["name"], dbc.STANDARD_PRICES_VERSION),
                    metadata=md, api_key=key)["id"]
                price_action = "created"
            else:
                price_action = "would_create"
        row["price"] = {"id": price_id, "action": price_action}
        if apply and prod_id and price_id:
            from app.services import billing_catalog
            if plan.stripe_live_price_id and plan.stripe_live_price_id != price_id:
                legacy = billing_catalog.legacy_price_ids(plan)
                if plan.stripe_live_price_id not in legacy:
                    legacy.append(plan.stripe_live_price_id)
                plan.stripe_price_ids_legacy = json.dumps(legacy)
            plan.stripe_live_product_id = prod_id
            plan.stripe_live_price_id = price_id
        out.append(row)
    if apply:
        db.flush()
    return {"applied": bool(apply), "packages": out}


def verify_prices(db: Session, stripe=None) -> Dict[str, Any]:
    stripe = stripe or _stripe()
    key = require_key()
    pid = _program_platform_id(db)
    rows, ok = [], bool(pid)
    for plan in _plans(db, pid) if pid else []:
        spec = _spec(plan.key)
        cents = int(Decimal(spec["monthly"]) * 100)
        r = {"package": spec["name"], "catalog_key": plan.key, "live_price_id": plan.stripe_live_price_id,
             "expected_monthly_cents": cents, "setup_cents": int(Decimal(spec["setup"]) * 100)}
        try:
            pr = stripe.Price.retrieve(plan.stripe_live_price_id, api_key=key) if plan.stripe_live_price_id else None
        except Exception as exc:
            pr, r["error"] = None, str(exc)[:200]
        r["ok"] = bool(pr) and _price_ok(pr, cents)
        if pr:
            r.update(unit_amount=pr.get("unit_amount"), currency=pr.get("currency"),
                     livemode=pr.get("livemode"), active=pr.get("active"),
                     interval=(pr.get("recurring") or {}).get("interval"))
        ok = ok and r["ok"]
        rows.append(r)
    return {"all_ok": ok and len(rows) == len(dbc.STANDARD_PACKAGES), "packages": rows}


# ── Live webhook endpoint ────────────────────────────────────────────────────

def verify_webhook(stripe=None) -> Dict[str, Any]:
    stripe = stripe or _stripe()
    key = require_key()
    want = webhook_url()
    out = {"url": want, "required_events": list(REQUIRED_EVENTS),
           "secret_configured": bool(webhook_secret())}
    try:
        eps = stripe.WebhookEndpoint.list(limit=100, api_key=key)
    except Exception as exc:
        out.update(found=False, ok=False, error=("Could not list Live webhook endpoints: %s"
                                                 % str(exc)[:160]))
        return out
    match = None
    for ep in (eps or {}).get("data") or []:
        if (ep.get("url") or "").rstrip("/") == want:
            match = ep
            break
    if match is None:
        out.update(found=False, ok=False)
        return out
    enabled = set(match.get("enabled_events") or [])
    missing = [e for e in REQUIRED_EVENTS if "*" not in enabled and e not in enabled]
    out.update(found=True, endpoint_id=match.get("id"), status=match.get("status"),
               livemode=match.get("livemode"), missing_events=missing,
               ok=(match.get("status") == "enabled" and bool(match.get("livemode"))
                   and not missing and out["secret_configured"]))
    return out


# ── preflight, probe, switch ─────────────────────────────────────────────────

def preflight(db: Session, stripe=None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"mode": checkout_mode(db), "key": key_state(),
                           "webhook_url": webhook_url(), "checks": {}, "blockers": []}
    c = out["checks"]
    try:
        require_key()
        c["live_key"] = True
    except LiveNotReady as exc:
        c["live_key"] = False
        out["blockers"].append(str(exc))
        out["ready"] = False
        return out
    try:
        out["account"] = account(stripe)
        c["charges_enabled"] = out["account"]["charges_enabled"]
        if not c["charges_enabled"]:
            out["blockers"].append("The Live Stripe account does not have charges enabled yet.")
    except Exception as exc:
        c["charges_enabled"] = False
        out["blockers"].append("Could not read the Live account: %s" % str(exc)[:160])
    try:
        out["prices"] = verify_prices(db, stripe)
        c["live_prices"] = out["prices"]["all_ok"]
        if not c["live_prices"]:
            out["blockers"].append("The three Live prices are not all mapped and verified yet.")
    except Exception as exc:
        c["live_prices"] = False
        out["blockers"].append("Could not verify Live prices: %s" % str(exc)[:160])
    out["webhook"] = verify_webhook(stripe)
    c["webhook_secret"] = out["webhook"]["secret_configured"]
    c["webhook_endpoint"] = bool(out["webhook"].get("ok"))
    if not c["webhook_secret"]:
        out["blockers"].append("%s is not set on the backend service." % ENV_WEBHOOK)
    if not out["webhook"].get("found"):
        out["blockers"].append("No Live webhook endpoint at %s." % out["webhook_url"])
    elif out["webhook"].get("missing_events"):
        out["blockers"].append("The Live webhook endpoint is missing events: %s"
                               % ", ".join(out["webhook"]["missing_events"]))
    out["ready"] = all(c.values())
    return out


def probe(db: Session, stripe=None) -> Dict[str, Any]:
    """Open, inspect and immediately EXPIRE two Live checkout sessions for an
    internal non-customer record - one setup, one monthly - so the Live
    amounts, mode and Month-2 anchor are proven on Stripe's side. Nothing can
    be paid: no customer, no link sent, and both sessions are expired at once."""
    from app.services import deal_billing
    stripe = stripe or _stripe()
    key = require_key()
    pid = _program_platform_id(db)
    plan = next((p for p in _plans(db, pid) if p.key == "starter"), None) if pid else None
    if plan is None or not plan.stripe_live_price_id:
        raise LiveNotReady("Map the Live prices first.")
    spec = _spec("starter")
    anchor = deal_billing.add_one_month(datetime.utcnow()) - deal_billing.MONTH2_MAX_SLACK
    base = backend_base_url()
    md = {PROBE_MARK: "true", "purpose": "Live preflight - internal, never sent, expired at once"}
    setup = stripe.checkout.Session.create(
        mode="payment", api_key=key, metadata=md,
        line_items=[{"price_data": {"currency": "usd", "unit_amount": int(Decimal(spec["setup"]) * 100),
                                    "product_data": {"name": "One-time setup & implementation fee"}},
                     "quantity": 1}],
        success_url=base + "/health", cancel_url=base + "/health")
    sub = stripe.checkout.Session.create(
        mode="subscription", api_key=key, metadata=md,
        line_items=[{"price": plan.stripe_live_price_id, "quantity": 1}],
        subscription_data={"billing_cycle_anchor": int(calendar.timegm(anchor.utctimetuple())),
                           "proration_behavior": "none", "metadata": md},
        success_url=base + "/health", cancel_url=base + "/health")
    res = []
    for s in (setup, sub):
        try:
            stripe.checkout.Session.expire(s["id"], api_key=key)
            expired = True
        except Exception as exc:                          # pragma: no cover
            log.warning("stripe_live: probe session %s not expired: %s", s.get("id"), exc)
            expired = False
        res.append({"session_id": s.get("id"), "mode": s.get("mode"), "livemode": s.get("livemode"),
                    "amount_total": s.get("amount_total"), "currency": s.get("currency"),
                    "expired": expired})
    return {"setup_session": res[0], "subscription_session": res[1],
            "subscription_anchor": anchor.date().isoformat(),
            "expected_setup_cents": int(Decimal(spec["setup"]) * 100),
            "monthly_price_id": plan.stripe_live_price_id}


def set_mode(db: Session, mode: str, stripe=None) -> Dict[str, Any]:
    if mode not in (MODE_TEST, MODE_LIVE):
        raise LiveNotReady("Mode must be 'test' or 'live'.")
    pid = _program_platform_id(db)
    if not pid:
        raise LiveNotReady("The EvoSys Pro Sales brand was not found.")
    pre = None
    if mode == MODE_LIVE:
        pre = preflight(db, stripe)
        if not pre["ready"]:
            raise LiveNotReady("Not switched to Live: " + "; ".join(pre["blockers"]))
    cfg = _config(db, pid, create=True)
    before = cfg.sales_checkout_mode or MODE_TEST
    cfg.sales_checkout_mode = mode
    db.flush()
    return {"before": before, "after": mode, "preflight": pre}


# ── helpers for checkout ─────────────────────────────────────────────────────

def live_customer(db: Session, org, stripe=None) -> str:
    stripe = stripe or _stripe()
    if getattr(org, "stripe_live_customer_id", None):
        return org.stripe_live_customer_id
    c = stripe.Customer.create(name=org.name, metadata={"org_id": org.id, "org_slug": org.slug},
                               api_key=require_key())
    org.stripe_live_customer_id = c["id"]
    db.flush()
    return c["id"]
