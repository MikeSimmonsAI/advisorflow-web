"""The commercial/revenue API contracts the new screens depend on.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS FILE IS FOR
═══════════════════════════════════════════════════════════════════════════

Two surfaces went in together and both are read by a page rather than by a
person, so the contract is the product:

  GET /billing/subscription   the customer's own Billing & Plan screen. It has
                              returned `limits` since plan limits were built
                              and the page rendered NONE of it - a customer
                              could hit a ceiling with no way to have seen it
                              coming. Now it also carries the recurring amount,
                              the card summary and customer-since.

  GET /god/billing/customers  the operational roster. /god/billing had seven
                              working routes and no frontend at all: built,
                              deployed, unreachable. This is the endpoint the
                              new page reads.

═══════════════════════════════════════════════════════════════════════════
THE TWO INVARIANTS THAT MATTER MOST
═══════════════════════════════════════════════════════════════════════════

BRAND ISOLATION. A brand-scoped roster must never contain another brand's
customer. Tested positively and negatively.

A NULL FIGURE IS NOT ZERO. An unpriceable subscription returns null with a
reason. "We cannot explain what this customer pays" and "this customer pays
nothing" are opposite facts and must not render identically.

NO MONEY IS EVER DERIVED IN THE BROWSER. The recurring amount is resolved
server-side from the brand's own catalogue; this file asserts the server sends
it, because the page that used to compute it had already drifted from the
catalogue once.
"""

import itertools

import pytest

from app.models.billing_models import BillingInvoice, BillingPayment, BrandBillingPlan
from app.models.models import Lead, Organization, Platform, User
from app.services import lead_capacity
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(40000)


def _platform(db, label):
    p = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(p); db.commit()
    return p


def _plan(db, platform, key, *, monthly=49700, annual=None, max_users=None,
          max_leads=None, purchasable=True):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=monthly, annual_cents=annual, currency="usd",
        is_purchasable=purchasable, is_active=True, sort_order=10,
        max_users=max_users, max_leads=max_leads)
    db.add(plan); db.commit()
    return plan


def _org(db, platform, **kw):
    n = next(_SEQ)
    kw.setdefault("plan", "trial")
    org = Organization(name=kw.pop("name", "Cust %d" % n),
                       slug="cust-ops-%d" % n,
                       platform_id=platform.id if platform else None,
                       is_active=True, **kw)
    db.add(org); db.commit()
    return org


def _user(db, org, role="org_admin"):
    u = User(organization_id=getattr(org, "id", None),
             email="ops%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("OpsPass123!"),
             full_name="Ops User", role=role, must_change_password=False,
             is_active=True)
    db.add(u); db.commit()
    return u


def _headers(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


@pytest.fixture()
def alpha(db_session):
    p = _platform(db_session, "AlphaOps")
    _plan(db_session, p, "starter", monthly=49700, max_users=2, max_leads=50)
    _plan(db_session, p, "growth", monthly=99700, max_users=5, max_leads=500)
    _plan(db_session, p, "enterprise", monthly=None, purchasable=False)
    return p


@pytest.fixture()
def god(db_session):
    return _user(db_session, None, role="god_admin")


# ═══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER'S OWN BILLING SCREEN
# ═══════════════════════════════════════════════════════════════════════════

def test_subscription_returns_usage_the_screen_can_render(client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter",
               billing_status="active")
    admin = _user(db_session, org)
    db_session.add(Lead(organization_id=org.id, first_name="A", last_name="B",
                        status="new"))
    db_session.commit()

    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()

    limits = body["limits"]["limits"]
    assert limits["max_leads"]["limit"] == 50
    assert limits["max_leads"]["used"] == 1
    # The admin fixture user occupies a seat, which is how a real org's first
    # seat is spent.
    assert limits["max_users"]["used"] >= 1
    assert limits["max_users"]["limit"] == 2


def test_subscription_reports_the_recurring_amount_from_the_catalogue(
        client, db_session, alpha):
    """No amount is ever derived in the browser."""
    org = _org(db_session, alpha, billing_plan_key="growth", plan="growth",
               billing_status="active", stripe_plan_interval="month")
    admin = _user(db_session, org)
    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()
    assert body["recurring_cents"] == 99700
    assert body["currency"] == "usd"


def test_a_plan_with_no_price_for_the_interval_reports_none_not_zero(
        client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="enterprise",
               plan="enterprise", billing_status="active",
               stripe_plan_interval="month")
    admin = _user(db_session, org)
    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()
    assert body["recurring_cents"] is None, (
        "an unpriced plan reported a number. Free and unpriceable are "
        "different facts and must not render identically.")


def test_held_prospects_are_visible_to_the_customer(client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter",
               billing_status="active")
    admin = _user(db_session, org)
    for _ in range(3):
        db_session.add(Lead(organization_id=org.id, first_name="Held",
                            last_name="X%d" % next(_SEQ), status="new",
                            capacity_state=lead_capacity.OVER_CAPACITY))
    db_session.commit()

    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()
    assert body["limits"]["capacity_hold"]["held"] == 3, (
        "held prospects are invisible to the customer, who therefore has no "
        "reason to upgrade and no idea business is waiting")
    # And they still do not consume the plan.
    assert body["limits"]["limits"]["max_leads"]["used"] == 0


def test_the_payment_method_is_a_summary_and_never_a_card(client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter",
               billing_status="active", stripe_customer_id="cus_x",
               billing_card_brand="visa", billing_card_last4="4242",
               billing_card_exp_month=11, billing_card_exp_year=2030)
    admin = _user(db_session, org)
    raw = client.get("/billing/subscription", headers=_headers(db_session, admin)).text
    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()

    pm = body["payment_method"]
    assert pm["on_file"] is True
    assert pm["last4"] == "4242"
    assert pm["brand"] == "visa"
    # Four digits is the whole of it. Nothing that could charge anything.
    for forbidden in ("number", "cvc", "card_number", "payment_method_id", "pm_"):
        assert forbidden not in raw, "response leaked %r" % forbidden


def test_no_card_on_file_says_so_rather_than_inventing_one(client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter",
               billing_status="active", stripe_customer_id="cus_y")
    admin = _user(db_session, org)
    pm = client.get("/billing/subscription",
                    headers=_headers(db_session, admin)).json()["payment_method"]
    assert pm["on_file"] is False
    assert pm["last4"] is None
    assert pm["brand"] is None
    assert pm["manageable"] is True   # there is a Portal to send them to


def test_customer_since_is_reported(client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter")
    admin = _user(db_session, org)
    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()
    assert body["customer_since"] is not None


def test_a_customer_cannot_read_another_orgs_billing(client, db_session, alpha):
    """Customer-org isolation on the customer's own screen."""
    mine = _org(db_session, alpha, name="Mine", billing_plan_key="starter",
                plan="starter", billing_status="active")
    theirs = _org(db_session, alpha, name="Theirs", billing_plan_key="growth",
                  plan="growth", billing_status="active")
    _user(db_session, theirs)
    admin = _user(db_session, mine)

    body = client.get("/billing/subscription", headers=_headers(db_session, admin)).json()
    assert body["plan"] == "starter", (
        "the billing screen resolved a different organization's plan")


# ═══════════════════════════════════════════════════════════════════════════
# THE GOD BILLING ROSTER
# ═══════════════════════════════════════════════════════════════════════════

def test_the_roster_returns_one_actionable_row_per_customer(
        client, db_session, alpha, god):
    org = _org(db_session, alpha, name="Acme", billing_plan_key="growth",
               plan="growth", billing_status="active",
               stripe_subscription_id="sub_1")
    db_session.add(BillingPayment(
        organization_id=org.id, platform_id=alpha.id,
        collection_reference="ref-%d" % next(_SEQ), currency="usd",
        amount_cents=99700))
    db_session.add(BillingInvoice(
        organization_id=org.id, platform_id=alpha.id,
        stripe_invoice_id="in_%d" % next(_SEQ), status="paid"))
    db_session.commit()

    body = client.get("/god/billing/customers",
                      headers=_headers(db_session, god)).json()
    row = next(r for r in body["customers"] if r["organization_id"] == org.id)

    assert row["plan_name"] == "Growth"
    assert row["billing_status"] == "active"
    assert row["mrr_cents"] == 99700
    assert row["invoice_count"] == 1
    assert row["last_payment_cents"] == 99700
    assert row["has_subscription"] is True


def test_an_unpriceable_subscription_reports_null_with_a_reason(
        client, db_session, alpha, god):
    org = _org(db_session, alpha, name="Mystery", billing_plan_key="ghost",
               plan="ghost", billing_status="active",
               stripe_subscription_id="sub_2")
    db_session.commit()

    body = client.get("/god/billing/customers",
                      headers=_headers(db_session, god)).json()
    row = next(r for r in body["customers"] if r["organization_id"] == org.id)

    assert row["mrr_cents"] is None, (
        "an active subscription whose plan does not resolve was priced at a "
        "number. That customer is being charged something the platform cannot "
        "explain, and a total that hides it is worse than one that names it.")
    assert row["mrr_unavailable_reason"]


def test_the_roster_is_brand_scoped_and_excludes_other_brands(
        client, db_session, alpha, god):
    """THE isolation test. Brand A's billing must never appear under Brand B."""
    beta = _platform(db_session, "BetaOps")
    _plan(db_session, beta, "starter", monthly=49700)

    a = _org(db_session, alpha, name="Alpha Customer", billing_plan_key="starter",
             plan="starter", billing_status="active")
    b = _org(db_session, beta, name="Beta Customer", billing_plan_key="starter",
             plan="starter", billing_status="active")

    a_ids = {r["organization_id"] for r in client.get(
        "/god/billing/customers?platform_id=%s" % alpha.id,
        headers=_headers(db_session, god)).json()["customers"]}
    b_ids = {r["organization_id"] for r in client.get(
        "/god/billing/customers?platform_id=%s" % beta.id,
        headers=_headers(db_session, god)).json()["customers"]}

    assert a.id in a_ids and b.id not in a_ids, "Beta's customer leaked into Alpha"
    assert b.id in b_ids and a.id not in b_ids, "Alpha's customer leaked into Beta"


@pytest.mark.parametrize("filt", [
    "all", "active", "trialing", "payment_problem", "pending_downgrade",
    "pending_cancellation", "no_payment_method", "held_capacity",
    "commercial_data_incomplete", "canceled",
])
def test_every_advertised_filter_is_accepted(client, db_session, alpha, god, filt):
    r = client.get("/god/billing/customers?filter=%s" % filt,
                   headers=_headers(db_session, god))
    assert r.status_code == 200, r.text


def test_an_unknown_filter_is_refused_rather_than_silently_ignored(
        client, db_session, god):
    r = client.get("/god/billing/customers?filter=whatever",
                   headers=_headers(db_session, god))
    assert r.status_code == 400, (
        "an unknown filter returned rows. A filter that silently does nothing "
        "shows a finance user the wrong list and tells them it is the right one.")


def test_the_payment_problem_filter_spans_past_due_and_unpaid(
        client, db_session, alpha, god):
    pd = _org(db_session, alpha, name="PastDue", billing_plan_key="starter",
              plan="starter", billing_status="past_due",
              stripe_subscription_id="sub_pd")
    un = _org(db_session, alpha, name="Unpaid", billing_plan_key="starter",
              plan="starter", billing_status="unpaid",
              stripe_subscription_id="sub_un")
    ok = _org(db_session, alpha, name="Fine", billing_plan_key="starter",
              plan="starter", billing_status="active")
    db_session.commit()

    ids = {r["organization_id"] for r in client.get(
        "/god/billing/customers?filter=payment_problem",
        headers=_headers(db_session, god)).json()["customers"]}
    assert pd.id in ids and un.id in ids
    assert ok.id not in ids


def test_the_held_capacity_filter_finds_customers_with_waiting_prospects(
        client, db_session, alpha, god):
    org = _org(db_session, alpha, name="Full", billing_plan_key="starter",
               plan="starter", billing_status="active")
    db_session.add(Lead(organization_id=org.id, first_name="Held", last_name="One",
                        status="new", capacity_state=lead_capacity.OVER_CAPACITY))
    db_session.commit()

    rows = client.get("/god/billing/customers?filter=held_capacity",
                      headers=_headers(db_session, god)).json()["customers"]
    row = next(r for r in rows if r["organization_id"] == org.id)
    assert row["held_lead_count"] == 1


def test_a_customer_created_outside_the_pipeline_says_so(
        client, db_session, alpha, god):
    """Never a fabricated salesperson - somebody eventually gets paid for one."""
    org = _org(db_session, alpha, name="Self Serve", billing_plan_key="starter",
               plan="starter", billing_status="active")
    db_session.commit()

    rows = client.get("/god/billing/customers",
                      headers=_headers(db_session, god)).json()["customers"]
    row = next(r for r in rows if r["organization_id"] == org.id)
    assert row["commercial_source"]["from_pipeline"] is False
    assert row["commercial_source"]["sales_rep"] is None
    assert row["commercial_source"]["note"]


def test_filter_counts_are_computed_over_the_same_scope(
        client, db_session, alpha, god):
    """The tabs must show real numbers, not make somebody click each one."""
    _org(db_session, alpha, name="A1", billing_plan_key="starter", plan="starter",
         billing_status="active")
    _org(db_session, alpha, name="T1", billing_plan_key="starter", plan="starter",
         billing_status="trialing")
    db_session.commit()

    body = client.get("/god/billing/customers?platform_id=%s" % alpha.id,
                      headers=_headers(db_session, god)).json()
    assert body["counts"]["all"] == body["total_in_scope"]
    assert body["counts"]["active"] >= 1
    assert body["counts"]["trialing"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# AUTHORITY — a finance surface is not a customer surface
# ═══════════════════════════════════════════════════════════════════════════

def test_the_roster_requires_god_and_refuses_a_customer_admin(
        client, db_session, alpha):
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter")
    admin = _user(db_session, org, role="org_admin")
    r = client.get("/god/billing/customers", headers=_headers(db_session, admin))
    assert r.status_code in (401, 403), (
        "a customer org admin reached the platform-wide billing roster")


def test_the_roster_refuses_a_sales_manager(client, db_session, alpha):
    """A sales manager is not automatically a billing administrator."""
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter")
    mgr = _user(db_session, org, role="sales_manager")
    r = client.get("/god/billing/customers", headers=_headers(db_session, mgr))
    assert r.status_code in (401, 403)


def test_the_roster_refuses_an_unauthenticated_caller(client):
    assert client.get("/god/billing/customers").status_code in (401, 403)


def test_an_advisor_cannot_read_their_orgs_billing_screen(client, db_session, alpha):
    """Billing is an admin surface inside the customer org too."""
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter")
    advisor = _user(db_session, org, role="advisor")
    r = client.get("/billing/subscription", headers=_headers(db_session, advisor))
    assert r.status_code in (401, 403)


def test_no_stripe_secret_ever_appears_in_either_response(
        client, db_session, alpha, god, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_FAKE_ROSTER_SENTINEL")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_FAKE_ROSTER_SENTINEL")
    org = _org(db_session, alpha, billing_plan_key="starter", plan="starter",
               billing_status="active", stripe_customer_id="cus_z",
               stripe_subscription_id="sub_z")
    admin = _user(db_session, org)
    db_session.commit()

    for raw in (client.get("/god/billing/customers",
                           headers=_headers(db_session, god)).text,
                client.get("/billing/subscription",
                           headers=_headers(db_session, admin)).text):
        assert "FAKE_ROSTER_SENTINEL" not in raw
        assert "sk_test" not in raw
        assert "whsec_" not in raw
