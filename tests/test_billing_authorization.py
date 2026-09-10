"""WHO MAY SPEND, WHO MAY SEE, AND WHAT THE BROWSER IS ALLOWED TO SAY.

WHAT THIS FILE DEFENDS

  1. A BILLING RELATIONSHIP IS TENANT-SCOPED. An organization reads its own
     Stripe customer, its own invoices, its own plan — and there is no
     parameter through which it could ask for anybody else's.
  2. THE BROWSER NAMES A PLAN, NEVER A PRICE. No amount and no Stripe price id
     crosses the wire; both are resolved server-side from the caller's own
     brand's catalogue, so a tampered request buys nothing cheaper.
  3. ONE ORGANIZATION, ONE SUBSCRIPTION. A second checkout while one is live
     is the double-billing defect, and it is refused with a 409.
  4. GOD BILLING IS GOD-ONLY, AND SEGMENTS BY BRAND.

NOTHING HERE REACHES STRIPE. Every Stripe entry point on these paths is
patched, and the credentials are obvious fakes.
"""

import itertools
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import stripe

from app.models.billing_models import (BillingInvoice, BillingPayment,
                                       BrandBillingConfig, BrandBillingPlan,
                                       ChangeTiming, ProrationBehavior,
                                       SubscriptionStatus)
from app.models.models import Organization, Platform, User
from app.routers.billing_router import CheckoutRequest, ChangePlanRequest
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

FAKE_SECRET_KEY = "sk_test_FAKE_not_a_real_key"
FAKE_WEBHOOK_SECRET = "whsec_FAKE_not_a_real_secret"


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    """Obviously-fake credentials, set for every test on these paths."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", FAKE_SECRET_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", FAKE_WEBHOOK_SECRET)


@pytest.fixture(autouse=True)
def no_real_stripe_calls(monkeypatch):
    """THE SUITE MUST NEVER REACH api.stripe.com.

    Same reasoning as conftest's Twilio guard: a stale patch target that
    silently becomes a live HTTPS request is the worst failure mode available,
    because the only symptom is an assertion about a count. Any Stripe request
    this file does not deliberately mock is refused here by name.
    """
    def _refuse(*args, **kwargs):
        raise RuntimeError(
            "A test tried to make a real Stripe API request. Patch the exact "
            "entry point the route calls (stripe.checkout.Session.create, "
            "stripe.billing_portal.Session.create, stripe.Subscription."
            "retrieve/modify, stripe.Customer.create) instead.")

    monkeypatch.setattr(stripe.checkout.Session, "create", _refuse)
    monkeypatch.setattr(stripe.billing_portal.Session, "create", _refuse)
    monkeypatch.setattr(stripe.Customer, "create", _refuse)
    monkeypatch.setattr(stripe.Subscription, "retrieve", _refuse)
    monkeypatch.setattr(stripe.Subscription, "modify", _refuse)


def _platform(db, label="Alpha"):
    p = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(p)
    db.commit()
    return p


def _plan(db, platform, key, *, monthly=49700, annual=None, purchasable=True,
          price_id_monthly=None, sort_order=10):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=monthly, annual_cents=annual, currency="usd",
        is_purchasable=purchasable, is_active=True, sort_order=sort_order,
        stripe_price_id_monthly=price_id_monthly)
    db.add(plan)
    db.commit()
    return plan


def _org(db, platform, **kw):
    n = next(_SEQ)
    kw.setdefault("plan", "trial")
    kw.setdefault("stripe_customer_id", "cus_org_%d" % n)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=platform.id, is_active=True, **kw)
    db.add(org)
    db.commit()
    return org


def _admin_headers(db, org, role="org_admin"):
    admin = User(organization_id=org.id,
                 email="admin%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role=role, must_change_password=False)
    db.add(admin)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


def _god_headers(db):
    god = User(organization_id=None, email="god%d@evosyspro.live" % next(_SEQ),
               password_hash=hash_password("GodPass123!"), full_name="Owner",
               role="god_admin", must_change_password=False)
    db.add(god)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(god, db)}


def _checkout_session(url="https://checkout.stripe.com/c/pay/cs_test_fake"):
    session = MagicMock()
    session.url = url
    session.id = "cs_test_fake"
    return session


@pytest.fixture()
def brand(db_session):
    platform = _platform(db_session, "Alpha")
    _plan(db_session, platform, "starter", monthly=49700, sort_order=10)
    _plan(db_session, platform, "growth", monthly=99700, annual=1196400,
          sort_order=20)
    _plan(db_session, platform, "enterprise", monthly=None,
          purchasable=False, sort_order=40)
    return platform


# ═══════════════════════════════════════════════════════════════════════════
# 1. A STRIPE CUSTOMER ID IS SCOPED TO ITS OWN ORGANIZATION
# ═══════════════════════════════════════════════════════════════════════════

def test_an_organization_reads_only_its_own_billing_relationship(
        client, db_session, brand):
    """Two customers of the same brand. The identity comes from the token and
    there is no parameter that could point it at the other one."""
    mine = _org(db_session, brand, stripe_customer_id="cus_mine",
                stripe_subscription_id="sub_mine", billing_status="active",
                billing_plan_key="starter", plan="starter")
    theirs = _org(db_session, brand, stripe_customer_id="cus_theirs",
                  stripe_subscription_id="sub_theirs", billing_status="active",
                  billing_plan_key="growth", plan="growth")
    headers = _admin_headers(db_session, mine)

    db_session.add_all([
        BillingInvoice(organization_id=mine.id, platform_id=brand.id,
                       stripe_invoice_id="in_mine", status="paid",
                       amount_paid_cents=49700,
                       period_start=datetime(2026, 9, 1)),
        BillingInvoice(organization_id=theirs.id, platform_id=brand.id,
                       stripe_invoice_id="in_theirs", status="paid",
                       amount_paid_cents=99700,
                       period_start=datetime(2026, 9, 1)),
    ])
    db_session.commit()

    response = client.get("/billing/subscription", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["stripe_customer_id"] == "cus_mine"
    assert body["stripe_subscription_id"] == "sub_mine"
    assert body["plan"] == "starter"
    assert {i["id"] for i in body["invoices"]} == {"in_mine"}
    assert "cus_theirs" not in response.text


def test_the_plan_catalogue_served_is_the_callers_own_brands(
        client, db_session, brand):
    """A second brand's tier names are not discoverable through this route."""
    other = _platform(db_session, "Beta")
    _plan(db_session, other, "beta-elite", monthly=500000)
    org = _org(db_session, brand)
    response = client.get("/billing/plans",
                          headers=_admin_headers(db_session, org))
    assert response.status_code == 200
    keys = {p["key"] for p in response.json()["plans"]}
    assert keys == {"starter", "growth", "enterprise"}
    assert "beta-elite" not in response.text


def test_the_customer_facing_plan_view_withholds_the_stripe_mapping(
        client, db_session, brand):
    """`_plan_public` is not `_plan_full`. God Mode answers "which Price is
    this brand selling"; a customer has no business asking."""
    plan = _plan(db_session, brand, "mapped", monthly=79700,
                 price_id_monthly="price_secretish_mapping")
    org = _org(db_session, brand)
    response = client.get("/billing/plans",
                          headers=_admin_headers(db_session, org))
    assert response.status_code == 200
    assert "price_secretish_mapping" not in response.text
    served = [p for p in response.json()["plans"] if p["key"] == "mapped"][0]
    assert "stripe_price_id_monthly" not in served
    assert served["monthly_cents"] == 79700
    assert plan.stripe_price_id_monthly == "price_secretish_mapping"


def test_billing_routes_refuse_a_non_admin_member_of_the_org(
        client, db_session, brand, auth_headers):
    """The plan catalogue is for the person who can change the plan. It was
    previously public — the whole price list, ceilings included, to anybody."""
    for path in ("/billing/plans", "/billing/subscription"):
        assert client.get(path, headers=auth_headers).status_code == 403
    assert client.post("/billing/checkout", json={"plan": "starter"},
                       headers=auth_headers).status_code == 403
    assert client.post("/billing/portal", headers=auth_headers).status_code == 403


def test_billing_routes_refuse_an_anonymous_caller(client, db_session, brand):
    assert client.get("/billing/plans").status_code == 401
    assert client.post("/billing/checkout",
                       json={"plan": "starter"}).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 3, 7, 25. THE BROWSER NAMES A PLAN. IT NEVER NAMES A PRICE.
# ═══════════════════════════════════════════════════════════════════════════

def test_the_checkout_request_model_has_no_price_field():
    """A field that does not exist cannot be tampered with.

    The request carries SELECTORS ONLY — which plan, how often, and on what
    commitment — and never an amount. `commitment` joined `interval` when a
    live month-to-month customer was found to have been upgraded onto a
    committed-term price: it names WHICH of a tier's two configured rates
    applies, exactly as `interval` names which period. The money is still
    resolved server-side from the brand's catalogue, and an unrecognised
    commitment is refused by `require_purchasable` rather than priced.
    """
    fields = set(CheckoutRequest.model_fields)
    assert fields == {"plan", "interval", "commitment"}
    assert set(ChangePlanRequest.model_fields) == {"plan", "interval",
                                                   "commitment"}
    for forbidden in ("price", "amount", "unit_amount", "price_id",
                      "stripe_price_id", "currency", "org_id", "customer"):
        assert forbidden not in fields


def test_a_browser_supplied_amount_is_ignored_and_the_catalogue_decides(
        client, db_session, brand):
    """The charged amount comes only from `brand_billing_plans`. A request
    carrying its own number must buy Starter at $497, not at $1."""
    org = _org(db_session, brand)
    headers = _admin_headers(db_session, org)
    created = MagicMock(return_value=_checkout_session())

    with patch.object(stripe.checkout.Session, "create", created):
        response = client.post("/billing/checkout", json={
            "plan": "starter", "interval": "month",
            # Everything below is a client trying to price its own subscription.
            "price": 1, "amount": 1, "unit_amount": 1, "monthly_cents": 1,
            "stripe_price_id": "price_attacker_owned",
            "line_items": [{"price": "price_attacker_owned", "quantity": 1}],
        }, headers=headers)

    assert response.status_code == 200
    kwargs = created.call_args.kwargs
    line_item = kwargs["line_items"][0]
    assert line_item["price_data"]["unit_amount"] == 49700
    assert line_item["quantity"] == 1
    assert line_item["price_data"]["recurring"] == {"interval": "month"}
    assert "price_attacker_owned" not in str(kwargs)
    assert 1 not in (line_item["price_data"]["unit_amount"],)


def test_a_client_supplied_stripe_price_id_is_never_used(
        client, db_session, brand):
    """When the brand's catalogue carries a Stripe price id THAT id is used —
    the one on the row, never one the request named."""
    _plan(db_session, brand, "mapped", monthly=79700,
          price_id_monthly="price_from_the_catalogue")
    org = _org(db_session, brand)
    headers = _admin_headers(db_session, org)
    created = MagicMock(return_value=_checkout_session())

    with patch.object(stripe.checkout.Session, "create", created):
        response = client.post("/billing/checkout", json={
            "plan": "mapped", "interval": "month",
            "price": "price_attacker_owned",
            "stripe_price_id_monthly": "price_attacker_owned"},
            headers=headers)

    assert response.status_code == 200
    line_item = created.call_args.kwargs["line_items"][0]
    assert line_item == {"price": "price_from_the_catalogue", "quantity": 1}
    assert "price_attacker_owned" not in str(created.call_args)


def test_checkout_maps_the_session_to_the_calling_organization(
        client, db_session, brand):
    """The webhook resolves an organization from this metadata. Taking the
    org id from the request would let a caller bill somebody else's tenant."""
    mine = _org(db_session, brand, stripe_customer_id="cus_mine_2")
    theirs = _org(db_session, brand, stripe_customer_id="cus_theirs_2")
    headers = _admin_headers(db_session, mine)
    created = MagicMock(return_value=_checkout_session())

    with patch.object(stripe.checkout.Session, "create", created):
        response = client.post("/billing/checkout", json={
            "plan": "growth", "interval": "year",
            "org_id": theirs.id, "customer": "cus_theirs_2"}, headers=headers)

    assert response.status_code == 200
    kwargs = created.call_args.kwargs
    assert kwargs["customer"] == "cus_mine_2"
    assert kwargs["metadata"]["org_id"] == mine.id
    assert kwargs["metadata"]["plan"] == "growth"
    assert kwargs["metadata"]["interval"] == "year"
    assert kwargs["subscription_data"]["metadata"]["org_id"] == mine.id
    assert kwargs["mode"] == "subscription"
    assert theirs.id not in str(kwargs)


def test_no_trial_is_given_away_when_no_trial_policy_is_configured(
        client, db_session, brand):
    org = _org(db_session, brand)
    created = MagicMock(return_value=_checkout_session())
    with patch.object(stripe.checkout.Session, "create", created):
        client.post("/billing/checkout", json={"plan": "starter"},
                    headers=_admin_headers(db_session, org))
    assert "trial_period_days" not in created.call_args.kwargs["subscription_data"]


# ═══════════════════════════════════════════════════════════════════════════
# 2 & 4 over HTTP. A PLAN THAT IS NOT THIS BRAND'S IS NOT BUYABLE.
# ═══════════════════════════════════════════════════════════════════════════

def test_checkout_refuses_another_brands_plan_key(client, db_session, brand):
    other = _platform(db_session, "Beta")
    _plan(db_session, other, "beta-elite", monthly=500000)
    org = _org(db_session, brand)
    response = client.post("/billing/checkout", json={"plan": "beta-elite"},
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 400


@pytest.mark.parametrize("payload", [
    {"plan": "platinum"},
    {"plan": "enterprise"},                       # listed, never self-serve
    {"plan": "starter", "interval": "week"},
    {"plan": "starter", "interval": "year"},      # no annual price configured
])
def test_checkout_refuses_an_unbuyable_request(client, db_session, brand,
                                               payload):
    org = _org(db_session, brand)
    response = client.post("/billing/checkout", json=payload,
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 400


def test_an_organization_with_no_brand_cannot_check_out(client, db_session):
    orphan = Organization(name="Orphan", slug="orphan-%d" % next(_SEQ),
                          platform_id=None, plan="trial", is_active=True)
    db_session.add(orphan)
    db_session.commit()
    response = client.post("/billing/checkout", json={"plan": "starter"},
                           headers=_admin_headers(db_session, orphan))
    assert response.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════
# 8. ONE ORGANIZATION, ONE SUBSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("status", list(SubscriptionStatus.OCCUPIED))
def test_a_second_checkout_is_refused_while_a_subscription_is_live(
        client, db_session, brand, status):
    """Every plan card used to be a live Select Plan button that created a
    SECOND Stripe subscription without cancelling the first. A customer on
    Starter who clicked Growth was billed for both, every month."""
    org = _org(db_session, brand, stripe_subscription_id="sub_existing",
               billing_status=status, billing_plan_key="starter",
               plan="starter")
    response = client.post("/billing/checkout", json={"plan": "growth"},
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 409, status
    assert "twice" in response.json()["detail"]


@pytest.mark.parametrize("status", ["canceled", "incomplete_expired"])
def test_a_finished_subscription_does_not_block_a_new_one(
        client, db_session, brand, status):
    """A cancelled customer coming back must be able to buy again."""
    org = _org(db_session, brand, stripe_subscription_id="sub_old",
               billing_status=status, billing_plan_key="starter")
    created = MagicMock(return_value=_checkout_session())
    with patch.object(stripe.checkout.Session, "create", created):
        response = client.post("/billing/checkout", json={"plan": "growth"},
                               headers=_admin_headers(db_session, org))
    assert response.status_code == 200


def test_the_duplicate_guard_needs_both_a_subscription_id_and_a_live_status(
        client, db_session, brand):
    """An org with a status but no subscription id has nothing to duplicate."""
    org = _org(db_session, brand, stripe_subscription_id=None,
               billing_status="active")
    created = MagicMock(return_value=_checkout_session())
    with patch.object(stripe.checkout.Session, "create", created):
        response = client.post("/billing/checkout", json={"plan": "starter"},
                               headers=_admin_headers(db_session, org))
    assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 9 & 24. THE PORTAL OPENS THE CALLER'S OWN CUSTOMER, AND ONLY THAT
# ═══════════════════════════════════════════════════════════════════════════

def test_a_customer_cannot_open_another_organizations_portal(
        client, db_session, brand):
    """The billing portal can change a card and cancel a subscription. The
    customer id is taken from the token's organization; the request has no
    body a caller could put another one in."""
    mine = _org(db_session, brand, stripe_customer_id="cus_portal_mine")
    theirs = _org(db_session, brand, stripe_customer_id="cus_portal_theirs")
    headers = _admin_headers(db_session, mine)
    created = MagicMock(return_value=MagicMock(
        url="https://billing.stripe.com/p/session/fake"))

    with patch.object(stripe.billing_portal.Session, "create", created):
        response = client.post("/billing/portal", json={
            "customer": "cus_portal_theirs", "org_id": theirs.id,
            "stripe_customer_id": "cus_portal_theirs"}, headers=headers)

    assert response.status_code == 200
    kwargs = created.call_args.kwargs
    assert kwargs["customer"] == "cus_portal_mine"
    assert "cus_portal_theirs" not in str(created.call_args)


def test_a_portal_needs_a_billing_account_of_the_callers_own(
        client, db_session, brand):
    org = _org(db_session, brand, stripe_customer_id=None)
    response = client.post("/billing/portal",
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 400


def test_the_portal_returns_the_customer_to_their_own_brands_domain(
        client, db_session, brand):
    """A white-label customer bounced to an AdvisorFlow hostname after billing
    is told who their software really belongs to."""
    org = _org(db_session, brand, stripe_customer_id="cus_return_url")
    created = MagicMock(return_value=MagicMock(url="https://p/fake"))
    with patch.object(stripe.billing_portal.Session, "create", created):
        client.post("/billing/portal", headers=_admin_headers(db_session, org))
    assert created.call_args.kwargs["return_url"].endswith("/billing")


# ═══════════════════════════════════════════════════════════════════════════
# CHANGE-PLAN REFUSES RATHER THAN GUESSING A TIMING
# ═══════════════════════════════════════════════════════════════════════════

def test_change_plan_is_refused_when_the_brand_has_no_policy_for_that_direction(
        client, db_session, brand):
    """Applying a plan change on a guessed schedule either bills somebody
    early or hands them a tier they have not paid for. Nothing here reaches
    Stripe: the refusal happens before the processor is called at all."""
    org = _org(db_session, brand, stripe_subscription_id="sub_change_1",
               billing_status="active", billing_plan_key="starter",
               plan="starter", stripe_plan_interval="month")
    response = client.post("/billing/change-plan", json={"plan": "growth"},
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 409
    assert "upgrade" in response.json()["detail"]
    db_session.refresh(org)
    assert org.billing_plan_key == "starter"
    assert org.billing_pending_plan_key is None


def test_change_plan_refuses_a_downgrade_the_brand_has_not_decided(
        client, db_session, brand):
    """Half a policy is not a policy: an upgrade rule says nothing about how a
    downgrade should be timed."""
    db_session.add(BrandBillingConfig(
        platform_id=brand.id, upgrade_timing=ChangeTiming.IMMEDIATE,
        upgrade_proration=ProrationBehavior.CREATE_PRORATIONS))
    db_session.commit()
    org = _org(db_session, brand, stripe_subscription_id="sub_change_2",
               billing_status="active", billing_plan_key="growth",
               plan="growth", stripe_plan_interval="month")
    response = client.post("/billing/change-plan", json={"plan": "starter"},
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 409
    assert "downgrade" in response.json()["detail"]


def test_change_plan_refuses_a_lateral_move_rather_than_calling_stripe(
        client, db_session, brand):
    org = _org(db_session, brand, stripe_subscription_id="sub_change_3",
               billing_status="active", billing_plan_key="starter",
               plan="starter", stripe_plan_interval="month")
    response = client.post("/billing/change-plan",
                           json={"plan": "starter", "interval": "month"},
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 400


def test_change_plan_needs_an_existing_subscription(client, db_session, brand):
    org = _org(db_session, brand, stripe_subscription_id=None)
    response = client.post("/billing/change-plan", json={"plan": "growth"},
                           headers=_admin_headers(db_session, org))
    assert response.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════
# 28. GOD BILLING AUTHORITY BOUNDARIES
# ═══════════════════════════════════════════════════════════════════════════

GOD_BILLING_READS = ("/god/billing/brands", "/god/billing/revenue",
                     "/god/billing/events")


@pytest.mark.parametrize("path", GOD_BILLING_READS)
def test_god_billing_refuses_an_org_admin(client, db_session, brand,
                                          admin_auth_headers, path):
    """A subscription price is what every customer of a brand pays, and the
    policy fields decide whether a failed payment costs somebody access.
    Neither is delegable to a tenant admin."""
    assert client.get(path, headers=admin_auth_headers).status_code == 403


@pytest.mark.parametrize("path", GOD_BILLING_READS)
def test_god_billing_refuses_a_plain_advisor(client, db_session, brand,
                                             auth_headers, path):
    assert client.get(path, headers=auth_headers).status_code == 403


@pytest.mark.parametrize("path", GOD_BILLING_READS)
def test_god_billing_refuses_an_anonymous_caller(client, db_session, path):
    assert client.get(path).status_code == 401


def test_god_billing_writes_refuse_an_org_admin(client, db_session, brand,
                                                admin_auth_headers):
    """Reading a price and setting one are both god-only."""
    assert client.get("/god/billing/brands/%s" % brand.id,
                      headers=admin_auth_headers).status_code == 403
    assert client.put("/god/billing/brands/%s/plans/starter" % brand.id,
                      json={"monthly_cents": 1},
                      headers=admin_auth_headers).status_code == 403
    assert client.put("/god/billing/brands/%s/config" % brand.id,
                      json={"suspend_on_past_due": True},
                      headers=admin_auth_headers).status_code == 403
    assert client.post("/god/billing/brands/%s/seed" % brand.id,
                       json={"apply": True},
                       headers=admin_auth_headers).status_code == 403
    # And nothing was written by the attempt.
    db_session.refresh(brand)
    plan = (db_session.query(BrandBillingPlan)
            .filter(BrandBillingPlan.platform_id == brand.id,
                    BrandBillingPlan.key == "starter").one())
    assert plan.monthly_cents == 49700
    assert db_session.query(BrandBillingConfig).count() == 0


def test_master_billing_across_customers_is_god_only(client, db_session, brand,
                                                     admin_auth_headers):
    assert client.get("/billing/all",
                      headers=admin_auth_headers).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 29. BRAND FINANCE ISOLATION — /god/billing/revenue segments by brand
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def two_brands_with_revenue(db_session):
    """Two brands, one paying customer each, at different prices."""
    alpha = _platform(db_session, "Alpha")
    beta = _platform(db_session, "Beta")
    _plan(db_session, alpha, "starter", monthly=49700)
    _plan(db_session, beta, "starter", monthly=99700)

    a_org = _org(db_session, alpha, billing_status="active", plan="starter",
                 billing_plan_key="starter", stripe_subscription_id="sub_a",
                 stripe_plan_interval="month")
    b_org = _org(db_session, beta, billing_status="active", plan="starter",
                 billing_plan_key="starter", stripe_subscription_id="sub_b",
                 stripe_plan_interval="month")

    now = datetime.utcnow()
    db_session.add_all([
        BillingPayment(organization_id=a_org.id, platform_id=alpha.id,
                       collection_reference="stripe:in_alpha_1",
                       amount_cents=49700, currency="usd", collected_at=now),
        BillingPayment(organization_id=b_org.id, platform_id=beta.id,
                       collection_reference="stripe:in_beta_1",
                       amount_cents=99700, currency="usd", collected_at=now),
        BillingInvoice(organization_id=a_org.id, platform_id=alpha.id,
                       stripe_invoice_id="in_alpha_1", status="paid"),
        BillingInvoice(organization_id=b_org.id, platform_id=beta.id,
                       stripe_invoice_id="in_beta_1", status="paid"),
    ])
    db_session.commit()
    return {"alpha": alpha, "beta": beta, "a_org": a_org, "b_org": b_org}


def test_revenue_segments_by_brand(client, db_session, two_brands_with_revenue):
    """Brand A's finance sees Brand A. A figure that silently blended two
    brands' books would be wrong for both of them."""
    headers = _god_headers(db_session)
    alpha = two_brands_with_revenue["alpha"]
    beta = two_brands_with_revenue["beta"]

    a = client.get("/god/billing/revenue?platform_id=" + alpha.id,
                   headers=headers)
    assert a.status_code == 200
    a_body = a.json()
    assert a_body["scope"]["platform_id"] == alpha.id
    assert a_body["scope"]["all_brands"] is False
    assert a_body["mrr"]["value_cents"] == 49700
    assert a_body["collected_30d"]["value_cents"] == 49700
    assert a_body["collected_30d"]["payments_counted"] == 1
    assert a_body["organizations_in_scope"] == 1
    assert a_body["invoices_recorded"] == 1

    b_body = client.get("/god/billing/revenue?platform_id=" + beta.id,
                        headers=headers).json()
    assert b_body["mrr"]["value_cents"] == 99700
    assert b_body["collected_30d"]["value_cents"] == 99700

    everything = client.get("/god/billing/revenue", headers=headers).json()
    assert everything["scope"]["all_brands"] is True
    assert everything["mrr"]["value_cents"] == 49700 + 99700
    assert everything["collected_30d"]["value_cents"] == 49700 + 99700


def test_revenue_reports_no_source_rather_than_a_fabricated_zero(
        client, db_session, brand):
    """"No payment has ever been recorded" and "we collected nothing this
    month" are different facts, and only one of them means something broke."""
    _org(db_session, brand, stripe_subscription_id=None, billing_status=None)
    body = client.get("/god/billing/revenue?platform_id=" + brand.id,
                      headers=_god_headers(db_session)).json()
    assert body["mrr"]["value_cents"] is None
    assert body["mrr"]["no_source"]
    assert body["collected_30d"]["value_cents"] is None
    assert body["collected_30d"]["no_source"]
    # Row counts ARE real zeros and stay integers.
    assert body["active_count"] == 0
    assert body["past_due_count"] == 0


def test_a_refund_is_netted_off_the_collected_figure(
        client, db_session, two_brands_with_revenue):
    """Reporting gross collections while a refund sits unmentioned beside them
    is how a month looks better than the bank does."""
    alpha = two_brands_with_revenue["alpha"]
    payment = (db_session.query(BillingPayment)
               .filter(BillingPayment.platform_id == alpha.id).one())
    payment.refunded_cents = 10000
    db_session.commit()

    body = client.get("/god/billing/revenue?platform_id=" + alpha.id,
                      headers=_god_headers(db_session)).json()
    assert body["collected_30d"]["gross_cents"] == 49700
    assert body["collected_30d"]["refunded_cents"] == 10000
    assert body["collected_30d"]["value_cents"] == 39700


def test_the_brand_list_reports_which_brands_can_bill_anybody(
        client, db_session, two_brands_with_revenue):
    headers = _god_headers(db_session)
    body = client.get("/god/billing/brands", headers=headers).json()
    by_id = {b["platform_id"]: b for b in body["brands"]}
    alpha = by_id[two_brands_with_revenue["alpha"].id]
    assert alpha["purchasable_plan_count"] == 1
    assert alpha["catalogue_configured"] is True
    assert alpha["has_config_row"] is False
    # Every policy is open, reported verbatim from billing_policy.
    assert set(alpha["policy_required"]) == {
        "upgrade timing/proration", "downgrade timing/proration",
        "failed-payment consequence", "subscription cancellation timing",
        "refund/chargeback clawback"}
    assert alpha["trial_days"] is None


def test_god_billing_never_returns_a_stripe_credential(
        client, db_session, brand):
    """Nothing in that router reads the environment, and the write path
    refuses a value that looks like a key in a Stripe id column."""
    headers = _god_headers(db_session)
    detail = client.get("/god/billing/brands/%s" % brand.id, headers=headers)
    assert detail.status_code == 200
    assert FAKE_SECRET_KEY not in detail.text
    assert FAKE_WEBHOOK_SECRET not in detail.text

    for field in ("stripe_product_id", "stripe_price_id_monthly",
                  "stripe_price_id_annual"):
        rejected = client.put("/god/billing/brands/%s/plans/starter" % brand.id,
                              json={field: FAKE_SECRET_KEY}, headers=headers)
        assert rejected.status_code == 400, field
        # The offending value is never echoed back.
        assert FAKE_SECRET_KEY not in rejected.text
        assert field in rejected.json()["detail"]
