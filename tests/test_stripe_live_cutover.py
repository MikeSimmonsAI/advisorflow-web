"""Stripe LIVE for new EvoSys Pro Sales checkout - simulated, nothing charged.

Uses a fake Stripe client (no network) and synthetic records. Covers the
separate Live configuration, idempotent Live price mapping, the preflight that
gates the switch, Live checkout composition (Live key per request, Live
customer, Live price, Month-2 anchor, setup-only first payment), the Live
webhook endpoint, and that the sandbox paths and other brands are unchanged.
"""
import calendar
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.billing_models import BillingEvent
from app.services import deal_billing, stripe_live
from tests.test_package_pricing_alignment import (APPROVED, KEY, _apply, _codes, _deal,  # noqa: F401
                                                  evo)

LIVE_KEY = "rk_live_" + "x" * 24          # fake, shape only
LIVE_HOOK = "whsec_" + "y" * 24           # fake, shape only


class _Obj(dict):
    """A dict that also reads as attributes, like a Stripe object."""
    __getattr__ = dict.__getitem__


class FakeLive:
    """In-memory Stripe Live. Every call must carry the Live api_key."""
    def __init__(self, charges_enabled=True, webhook=None):
        self.products, self.prices, self.sessions, self.calls = {}, {}, {}, []
        self.charges_enabled = charges_enabled
        self.webhooks = [] if webhook is None else [webhook]
        o = self

        def _k(kw):
            assert kw.pop("api_key", None) == LIVE_KEY, "Live call without the Live key"
            o.calls.append(1)

        class Account:
            @staticmethod
            def retrieve(id=None, **kw):
                _k(kw)
                return {"id": "acct_live_test", "charges_enabled": o.charges_enabled,
                        "payouts_enabled": True, "details_submitted": True, "country": "US",
                        "default_currency": "usd",
                        "settings": {"dashboard": {"display_name": "EVO INTEGRATED SOLUTIONS LLC"}}}

        class Product:
            @staticmethod
            def retrieve(pid, **kw):
                _k(kw); return o.products[pid]

            @staticmethod
            def search(query=None, limit=1, **kw):
                _k(kw)
                return {"data": [p for p in o.products.values()
                                 if "'%s'" % p["metadata"]["plan_key"] in query]}

            @staticmethod
            def create(**kw):
                _k(kw)
                pid = "prod_live_%d" % (len(o.products) + 1)
                o.products[pid] = {"id": pid, "metadata": kw["metadata"], "name": kw["name"]}
                return o.products[pid]

        class Price:
            @staticmethod
            def retrieve(pid, **kw):
                _k(kw); return o.prices[pid]

            @staticmethod
            def list(product=None, active=True, limit=100, **kw):
                _k(kw)
                return {"data": [p for p in o.prices.values() if p["product"] == product]}

            @staticmethod
            def create(**kw):
                _k(kw)
                pid = "price_live_%d" % (len(o.prices) + 1)
                o.prices[pid] = {"id": pid, "product": kw["product"], "currency": kw["currency"],
                                 "unit_amount": kw["unit_amount"], "recurring": kw["recurring"],
                                 "metadata": kw["metadata"], "active": True, "livemode": True}
                return o.prices[pid]

        class WebhookEndpoint:
            @staticmethod
            def list(limit=100, **kw):
                _k(kw); return {"data": o.webhooks}

        class _Session:
            @staticmethod
            def create(**kw):
                _k(kw)
                sid = "cs_live_%d" % (len(o.sessions) + 1)
                amount = sum((li.get("price_data") or {}).get("unit_amount", 0)
                             for li in kw["line_items"]) if kw["mode"] == "payment" else 0
                o.sessions[sid] = _Obj(kw, id=sid, livemode=True, amount_total=amount,
                                       currency="usd", status="open", url="https://checkout.stripe.com/" + sid)
                return o.sessions[sid]

            @staticmethod
            def expire(sid, **kw):
                _k(kw); o.sessions[sid]["status"] = "expired"; return o.sessions[sid]

        class Customer:
            @staticmethod
            def create(**kw):
                _k(kw); return {"id": "cus_live_%d" % len(o.calls)}

        self.Account, self.Product, self.Price, self.WebhookEndpoint, self.Customer = \
            Account, Product, Price, WebhookEndpoint, Customer
        self.checkout = SimpleNamespace(Session=_Session)


def _hook(events=stripe_live.REQUIRED_EVENTS, url=None, status="enabled"):
    return {"id": "we_live_1", "url": url or stripe_live.webhook_url(), "status": status,
            "livemode": True, "enabled_events": list(events)}


@pytest.fixture()
def live_env(monkeypatch):
    monkeypatch.setenv(stripe_live.ENV_KEY, LIVE_KEY)
    monkeypatch.setenv(stripe_live.ENV_WEBHOOK, LIVE_HOOK)


# ── configuration ────────────────────────────────────────────────────────────

def test_key_state_never_returns_a_value(live_env):
    st = stripe_live.key_state()
    assert st == {"env": "STRIPE_LIVE_SECRET_KEY", "present": True, "kind": "live_restricted",
                  "webhook_secret_env": "STRIPE_LIVE_WEBHOOK_SECRET", "webhook_secret_present": True}
    assert LIVE_KEY not in json.dumps(st) and LIVE_HOOK not in json.dumps(st)


def test_a_sandbox_key_is_refused_as_live(monkeypatch):
    monkeypatch.setenv(stripe_live.ENV_KEY, "sk_test_abc")
    with pytest.raises(stripe_live.LiveNotReady):
        stripe_live.require_key()


def test_default_mode_is_the_sandbox(evo):
    assert stripe_live.checkout_mode(evo.db) == "test"


# ── Live prices ──────────────────────────────────────────────────────────────

def test_live_prices_are_created_once_kept_apart_and_verified(evo, live_env):
    _apply(evo)
    fake = FakeLive()
    sandbox_ids = {k: p.stripe_price_id_monthly for k, p in evo.plans.items()}
    prev = stripe_live.provision(evo.db, apply=False, stripe=fake)
    assert all(p["price"]["action"] == "would_create" for p in prev["packages"]) and not fake.prices
    r = stripe_live.provision(evo.db, apply=True, stripe=fake); evo.db.commit()
    assert len(fake.prices) == 3 and len(fake.products) == 3
    for plan, (setup, month, _pay) in APPROVED.items():
        bp = evo.plans[KEY[plan]]; evo.db.refresh(bp)
        live = fake.prices[bp.stripe_live_price_id]
        assert live["unit_amount"] == month * 100 and live["currency"] == "usd"
        assert live["recurring"] == {"interval": "month", "interval_count": 1}
        assert bp.stripe_price_id_monthly == sandbox_ids[KEY[plan]]       # sandbox untouched
        assert bp.stripe_live_price_id != bp.stripe_price_id_monthly
    v = stripe_live.verify_prices(evo.db, stripe=fake)
    assert v["all_ok"] and [p["unit_amount"] for p in v["packages"]] == [70000, 130000, 240000]
    # Idempotent.
    r2 = stripe_live.provision(evo.db, apply=True, stripe=fake)
    assert len(fake.prices) == 3
    assert all(p["price"]["action"] == "reused_mapped" for p in r2["packages"])


# ── preflight gates the switch ───────────────────────────────────────────────

def _ready(evo, fake):
    _apply(evo)
    stripe_live.provision(evo.db, apply=True, stripe=fake); evo.db.commit()


def test_switch_refused_without_key(evo, monkeypatch):
    monkeypatch.delenv(stripe_live.ENV_KEY, raising=False)
    with pytest.raises(stripe_live.LiveNotReady):
        stripe_live.set_mode(evo.db, "live")
    assert stripe_live.checkout_mode(evo.db) == "test"


@pytest.mark.parametrize("problem", ["no_webhook", "missing_event", "no_secret", "charges_off", "no_prices"])
def test_switch_refused_until_everything_verifies(evo, live_env, monkeypatch, problem):
    fake = FakeLive(charges_enabled=(problem != "charges_off"),
                    webhook=None if problem == "no_webhook" else
                    _hook(events=[e for e in stripe_live.REQUIRED_EVENTS if e != "invoice.paid"])
                    if problem == "missing_event" else _hook())
    if problem == "no_prices":
        _apply(evo)
    else:
        _ready(evo, fake)
    if problem == "no_secret":
        monkeypatch.delenv(stripe_live.ENV_WEBHOOK)
    pre = stripe_live.preflight(evo.db, stripe=fake)
    assert pre["ready"] is False and pre["blockers"]
    with pytest.raises(stripe_live.LiveNotReady):
        stripe_live.set_mode(evo.db, "live", stripe=fake)
    assert stripe_live.checkout_mode(evo.db) == "test"


def test_switch_to_live_and_back(evo, live_env):
    fake = FakeLive(webhook=_hook())
    _ready(evo, fake)
    pre = stripe_live.preflight(evo.db, stripe=fake)
    assert pre["ready"] is True, pre["blockers"]
    assert pre["account"]["charges_enabled"] and pre["webhook"]["ok"]
    stripe_live.set_mode(evo.db, "live", stripe=fake); evo.db.commit()
    assert stripe_live.checkout_mode(evo.db) == "live"
    stripe_live.set_mode(evo.db, "test"); evo.db.commit()
    assert stripe_live.checkout_mode(evo.db) == "test"


def test_probe_opens_and_expires_live_sessions_without_a_customer(evo, live_env):
    fake = FakeLive(webhook=_hook())
    _ready(evo, fake)
    r = stripe_live.probe(evo.db, stripe=fake)
    s, m = r["setup_session"], r["subscription_session"]
    assert s["livemode"] and m["livemode"] and s["expired"] and m["expired"]
    assert s["amount_total"] == 160000 and s["currency"] == "usd"
    sub = fake.sessions[m["session_id"]]
    assert "customer" not in sub and "customer" not in fake.sessions[s["session_id"]]
    assert sub["line_items"] == [{"price": evo.plans["starter"].stripe_live_price_id, "quantity": 1}]
    assert sub["subscription_data"]["proration_behavior"] == "none"


# ── Live checkout for a new EvoSys Pro Sales deal ────────────────────────────

def _go_live(evo, fake):
    _ready(evo, fake)
    stripe_live.set_mode(evo.db, "live", stripe=fake); evo.db.commit()


def _post(client, evo, d, part):
    from tests.test_deal_billing import _h
    return client.post("/sales/opportunities/%s/billing/checkout?part=%s" % (d.opp.id, part),
                       headers=_h(evo.db, evo.god))


@pytest.mark.parametrize("plan", list(APPROVED))
def test_live_checkout_uses_live_key_customer_price_and_month2(client, evo, live_env, plan):
    fake = FakeLive(webhook=_hook())
    _go_live(evo, fake)
    setup, month, _ = APPROVED[plan]
    paid = datetime.utcnow() - timedelta(days=2)
    d = _deal(evo, plan)
    with patch("stripe.checkout.Session.create", side_effect=fake.checkout.Session.create), \
         patch("stripe.Customer.create", side_effect=fake.Customer.create), \
         patch("app.routers.billing_router._stripe_client"), \
         patch("app.routers.billing_router._get_or_create_customer") as sandbox_customer, \
         patch("app.routers.billing_router._brand_base_url", return_value="https://brand.test"):
        r = _post(client, evo, d, "setup")
        assert r.status_code == 200, r.text
        assert r.json()["stripe_mode"] == "live"
        s = fake.sessions[r.json()["stripe_session_id"]]
        assert s["mode"] == "payment" and s["amount_total"] == setup * 100
        assert s["customer"].startswith("cus_live_") and s["metadata"]["stripe_mode"] == "live"
        evo.db.refresh(d.org)
        assert d.org.stripe_live_customer_id == s["customer"]
        assert d.org.stripe_customer_id == "cus_test"            # sandbox customer untouched
        # Monthly link: still waits for the actual setup payment.
        r = _post(client, evo, d, "subscription")
        assert r.status_code == 409
        d.impl.setup_payment_status = "paid"; d.impl.setup_paid_at = paid; evo.db.commit()
        r = _post(client, evo, d, "subscription")
        assert r.status_code == 200, r.text
        m = fake.sessions[r.json()["stripe_session_id"]]
        assert m["line_items"] == [{"price": evo.plans[KEY[plan]].stripe_live_price_id, "quantity": 1}]
        assert fake.prices[m["line_items"][0]["price"]]["unit_amount"] == month * 100
        assert m["subscription_data"]["proration_behavior"] == "none"
        assert m["subscription_data"]["billing_cycle_anchor"] == calendar.timegm(
            deal_billing.add_one_month(paid).utctimetuple())
        sandbox_customer.assert_not_called()


def test_live_mode_still_enforces_the_quote(client, evo, live_env):
    from decimal import Decimal
    from app.models.sales_quote_models import SalesQuoteDraft
    fake = FakeLive(webhook=_hook())
    _go_live(evo, fake)
    d = _deal(evo, "growth", quote=False)
    evo.db.add(SalesQuoteDraft(opportunity_id=d.opp.id, revision=2, plan_key="growth",
                               setup_total=Decimal(2500), monthly_total=Decimal(1297),
                               addon_setup=Decimal(0), data_json="{}", rules_version="2026-11-13"))
    evo.db.commit()
    with patch("stripe.checkout.Session.create", side_effect=fake.checkout.Session.create) as c:
        r = _post(client, evo, d, "setup")
    assert r.status_code == 409 and not fake.sessions
    assert deal_billing.B_QUOTE_SETUP_MISMATCH in json.dumps(r.json())


def test_sandbox_mode_checkout_is_unchanged(client, evo, live_env):
    from tests.test_deal_billing import _Sess
    _apply(evo)
    d = _deal(evo, "basic")
    with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
         patch("app.routers.billing_router._stripe_client"), \
         patch("app.routers.billing_router._get_or_create_customer", return_value="cus_test"), \
         patch("app.routers.billing_router._brand_base_url", return_value="https://brand.test"):
        r = _post(client, evo, d, "setup")
    assert r.status_code == 200 and r.json()["stripe_mode"] == "test"
    assert "api_key" not in create.call_args.kwargs
    assert create.call_args.kwargs["customer"] == "cus_test"


def test_live_mode_without_a_live_price_blocks_the_monthly_link(evo, live_env):
    fake = FakeLive(webhook=_hook())
    _go_live(evo, fake)
    evo.plans["growth"].stripe_live_price_id = None; evo.db.commit()
    d = _deal(evo, "growth", paid_setup_at=datetime.utcnow() - timedelta(days=1))
    t = deal_billing.terms_for(evo.db, d.opp)
    assert t["checkout_mode"] == "live"
    assert deal_billing.B_NO_LIVE_PRICE in _codes(t, "subscription")


# ── the Live webhook endpoint ────────────────────────────────────────────────

def _signed(payload: dict, secret: str):
    import hashlib, hmac, time
    body = json.dumps(payload)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), ("%d.%s" % (ts, body)).encode(), hashlib.sha256).hexdigest()
    return body, {"stripe-signature": "t=%d,v1=%s" % (ts, sig), "content-type": "application/json"}


def test_live_webhook_verifies_with_the_live_secret_only(client, db_session, live_env):
    ev = {"id": "evt_live_sig_1", "object": "event", "type": "checkout.session.expired",
          "livemode": True, "data": {"object": {"id": "cs_live_x", "object": "checkout.session"}}}
    body, h = _signed(ev, "whsec_wrong")
    assert client.post("/billing/webhook/live", content=body, headers=h).status_code == 400
    body, h = _signed(ev, LIVE_HOOK)
    r = client.post("/billing/webhook/live", content=body, headers=h)
    assert r.status_code == 200 and r.json()["livemode"] is True
    row = db_session.query(BillingEvent).filter_by(stripe_event_id="evt_live_sig_1").one()
    assert row.detail.startswith("[live]")
    # A replay is a duplicate, handled once.
    body, h = _signed(ev, LIVE_HOOK)
    assert client.post("/billing/webhook/live", content=body, headers=h).json()["duplicate"] is True


def test_live_webhook_ignores_a_non_live_event(client, db_session, live_env):
    ev = {"id": "evt_test_on_live", "object": "event", "type": "invoice.paid", "livemode": False,
          "data": {"object": {"id": "in_x", "object": "invoice"}}}
    body, h = _signed(ev, LIVE_HOOK)
    r = client.post("/billing/webhook/live", content=body, headers=h)
    assert r.status_code == 200 and r.json().get("ignored") is True
    assert db_session.query(BillingEvent).filter_by(stripe_event_id="evt_test_on_live").count() == 0


def test_live_webhook_not_configured(client, monkeypatch):
    monkeypatch.delenv(stripe_live.ENV_WEBHOOK, raising=False)
    assert client.post("/billing/webhook/live", content="{}").status_code == 503


def test_live_setup_payment_lands_on_the_live_customer_and_books_flat_pay(evo, live_env):
    from app.models.compensation_models import CompensationEntry
    from app.services import billing_webhook
    _apply(evo)
    d = _deal(evo, "professional")
    d.org.stripe_live_customer_id = "cus_live_abc"; evo.db.commit()
    assert billing_webhook._org_by_customer(evo.db, "cus_live_abc").id == d.org.id
    billing_webhook._handle_checkout_completed(evo.db, d.org, {
        "id": "cs_live_paid", "mode": "payment", "livemode": True, "customer": "cus_live_abc",
        "payment_status": "paid", "payment_intent": "pi_live_1", "amount_total": 590000,
        "currency": "usd", "metadata": {"opportunity_id": d.opp.id, "part": "setup",
                                        "source": "deal_billing", "stripe_mode": "live"}})
    evo.db.commit()
    assert d.org.stripe_customer_id == "cus_test"
    rows = evo.db.query(CompensationEntry).filter_by(opportunity_id=d.opp.id).all()
    assert sorted(float(e.amount) for e in rows) == [400.0, 900.0, 2000.0]
