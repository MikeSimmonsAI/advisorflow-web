"""
Deal → billing: the join between what was sold and what gets charged.

THE FAILURE THIS FILE EXISTS TO PREVENT is charging a customer an amount
nobody agreed to. Every refusal below therefore also asserts that NO Stripe
call was made — an endpoint that refuses after creating a checkout session has
not refused anything.
"""

import itertools
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandPackage, BrandSalesOrg, Opportunity
from app.services import deal_billing
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _n(p):
    return "%s-%d" % (p, next(_SEQ))


@pytest.fixture()
def world(db_session):
    from app.models.billing_models import BrandBillingPlan

    plat = Platform(name="EvoSys Pro", slug=_n("evo"))
    db_session.add(plat)
    db_session.commit()

    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Sales", slug=_n("evos"))
    db_session.add(brand)
    db_session.commit()

    # The catalogue plan the package will map to.
    plan = BrandBillingPlan(platform_id=plat.id, key="growth", name="Growth",
                            is_active=True, monthly_cents=50000,
                            stripe_price_id_monthly="price_growth_test")
    db_session.add(plan)

    pkg = BrandPackage(platform_id=plat.id, key="growth", name="Growth Package",
                       price=Decimal("2500.00"), monthly_price=Decimal("500.00"))
    db_session.add(pkg)
    db_session.commit()

    org = Organization(name="Acme Power", slug=_n("acme"), platform_id=plat.id,
                       plan="standard", is_active=True)
    db_session.add(org)
    db_session.commit()

    opp = Opportunity(company_name="Acme Power", brand_sales_org_id=brand.id,
                      stage="won", selected_package_id=pkg.id,
                      billing_option="monthly")
    db_session.add(opp)
    db_session.commit()

    impl = Implementation(organization_id=org.id, opportunity_id=opp.id,
                          platform_id=plat.id, status="not_started")
    db_session.add(impl)
    db_session.commit()

    rep = User(organization_id=None, email=_n("rep") + "@t.local",
               password_hash=hash_password("TestPass123!"), full_name="Rep",
               role="user", must_change_password=False)
    god = User(organization_id=None, email=_n("god") + "@t.local",
               password_hash=hash_password("TestPass123!"), full_name="Owner",
               role="god_admin", must_change_password=False)
    db_session.add_all([rep, god])
    db_session.commit()

    return dict(plat=plat, brand=brand, plan=plan, pkg=pkg, org=org,
                opp=opp, impl=impl, rep=rep, god=god)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ── terms resolution ────────────────────────────────────────────────────────

class TestTermsResolution:
    def test_an_unmapped_package_blocks_rather_than_guessing_the_plan(
            self, db_session, world):
        """BrandPackage.key and BrandBillingPlan.key are both 'growth' here.

        A coincidental string match is NOT a configuration, and matching on it
        is how a brand that renames a package silently starts billing the wrong
        tier. Only billing_plan_key counts.
        """
        assert world["pkg"].billing_plan_key is None
        terms = deal_billing.terms_for(db_session, world["opp"])
        codes = {b["code"] for b in terms["blockers"]}
        assert deal_billing.B_PACKAGE_UNMAPPED in codes
        assert not terms.get("billable")

    def test_a_mapped_package_resolves_the_plan_and_both_charges(
            self, db_session, world):
        world["pkg"].billing_plan_key = "growth"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        kinds = {c["kind"] for c in terms["charges"]}
        assert kinds == {"setup_fee", "subscription"}
        setup = next(c for c in terms["charges"] if c["kind"] == "setup_fee")
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert setup["cents"] == 250000          # 2500.00 implementation fee
        assert sub["cents"] == 50000             # 500.00/mo catalogue
        assert terms["billable"] is True

    def test_a_negotiated_rate_that_differs_from_catalogue_is_refused_not_guessed(
            self, db_session, world):
        """Charging either number silently would be wrong. Say so."""
        world["pkg"].billing_plan_key = "growth"
        world["opp"].custom_unit_price = Decimal("399.00")
        world["opp"].custom_min_units = 1
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        codes = {b["code"] for b in terms["blockers"]}
        assert deal_billing.B_RATE_MISMATCH in codes
        row = next(b for b in terms["blockers"]
                   if b["code"] == deal_billing.B_RATE_MISMATCH)
        assert row["deal_cents"] == 39900
        assert row["catalogue_cents"] == 50000

    def test_a_deal_with_no_customer_org_cannot_be_billed(self, db_session, world):
        db_session.delete(world["impl"])
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_NO_CUSTOMER_ORG in {b["code"] for b in terms["blockers"]}

    def test_a_deal_that_is_not_won_is_blocked(self, db_session, world):
        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "demo_proposal"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_NOT_WON in {b["code"] for b in terms["blockers"]}

    def test_money_never_becomes_a_float(self, db_session, world):
        world["pkg"].billing_plan_key = "growth"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        for c in terms["charges"]:
            assert isinstance(c["cents"], int)
        assert isinstance(terms["setup_amount"], str)

    def test_not_known_is_never_reported_as_zero(self, db_session, world):
        """An empty deal must not read as 'charge nothing', which is a number."""
        bare = Opportunity(company_name="Bare", stage="won",
                           brand_sales_org_id=world["brand"].id)
        db_session.add(bare)
        db_session.commit()
        terms = deal_billing.terms_for(db_session, bare)
        assert terms["setup_cents"] is None
        assert terms["recurring_cents"] is None
        assert terms["charges"] == []


# ── authority ───────────────────────────────────────────────────────────────

class TestAuthority:
    def test_anonymous_is_refused_on_both_routes(self, client, world):
        oid = world["opp"].id
        assert client.get("/sales/opportunities/%s/billing" % oid
                          ).status_code in (401, 403)
        assert client.post("/sales/opportunities/%s/billing/checkout" % oid
                           ).status_code in (401, 403)

    def test_an_unknown_opportunity_is_404(self, client, db_session, world):
        r = client.get("/sales/opportunities/does-not-exist/billing",
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 404

    def test_a_rep_outside_the_brand_gets_404_not_403(self, client, db_session, world):
        """403 would confirm the deal exists — that is pipeline enumeration."""
        other_plat = Platform(name="Other", slug=_n("oth"))
        db_session.add(other_plat)
        db_session.commit()
        other_brand = BrandSalesOrg(platform_id=other_plat.id, name="Other Sales",
                                    slug=_n("os"))
        db_session.add(other_brand)
        db_session.commit()
        outsider = User(organization_id=None, email=_n("out") + "@t.local",
                        password_hash=hash_password("TestPass123!"),
                        full_name="Outsider", role="user",
                        must_change_password=False)
        db_session.add(outsider)
        db_session.commit()

        r = client.get("/sales/opportunities/%s/billing" % world["opp"].id,
                       headers=_h(db_session, outsider))
        assert r.status_code in (403, 404)
        assert r.status_code != 200


# ── checkout refuses before touching Stripe ─────────────────────────────────

class TestCheckoutRefusesSafely:
    def test_a_blocked_deal_never_reaches_stripe(self, client, db_session, world):
        """The assertion that matters: NOTHING was charged."""
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                headers=_h(db_session, world["god"]))
            assert r.status_code == 409
            assert create.call_count == 0
        body = r.json()["detail"]
        assert body["blockers"]

    def test_a_not_won_deal_never_reaches_stripe(self, client, db_session, world):
        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "closing"
        db_session.commit()
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                headers=_h(db_session, world["god"]))
            assert r.status_code == 409
            assert create.call_count == 0


# ── checkout composition ────────────────────────────────────────────────────

class _Sess:
    url = "https://checkout.stripe.test/session_123"


class TestCheckoutComposition:
    def _ready(self, db, world):
        world["pkg"].billing_plan_key = "growth"
        world["org"].stripe_customer_id = "cus_test123"
        db.commit()

    def test_setup_plus_subscription_is_ONE_session(self, client, db_session, world):
        """Two links is one link the customer does not pay."""
        self._ready(db_session, world)
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        assert create.call_count == 1
        kw = create.call_args.kwargs
        assert kw["mode"] == "subscription"
        assert kw["line_items"][0]["price"] == "price_growth_test"
        # The setup fee rides the first invoice rather than a second session.
        items = kw["subscription_data"]["add_invoice_items"]
        assert items[0]["price_data"]["unit_amount"] == 250000

    def test_the_deal_trail_is_carried_in_metadata(self, client, db_session, world):
        """Without this the webhook cannot connect money back to what was sold."""
        self._ready(db_session, world)
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            client.post("/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                        headers=_h(db_session, world["god"]))
        meta = create.call_args.kwargs["metadata"]
        assert meta["org_id"] == world["org"].id
        assert meta["opportunity_id"] == world["opp"].id
        assert meta["source"] == "deal_billing"

    def test_a_setup_only_deal_uses_payment_mode(self, client, db_session, world):
        """No recurring rate means no subscription — not a £0 one."""
        world["pkg"].monthly_price = None
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        assert create.call_args.kwargs["mode"] == "payment"

    def test_checkout_writes_no_payment_record(self, client, db_session, world):
        """A payment that has not happened must leave no trace that looks like one."""
        from app.models.billing_models import BillingPayment
        self._ready(db_session, world)
        before = db_session.query(BillingPayment).count()
        with patch("stripe.checkout.Session.create", return_value=_Sess()), \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            client.post("/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                        headers=_h(db_session, world["god"]))
        assert db_session.query(BillingPayment).count() == before


# ── no secret ever leaves the server ────────────────────────────────────────

class TestNoSecretLeak:
    def test_no_stripe_secret_appears_in_any_response(self, client, db_session, world):
        self._marker = "sk_test_LEAKCANARY"
        world["pkg"].billing_plan_key = "growth"
        db_session.commit()
        r = client.get("/sales/opportunities/%s/billing" % world["opp"].id,
                       headers=_h(db_session, world["god"]))
        assert "sk_" not in r.text
        assert "whsec_" not in r.text


# ── the mapping that makes the money path run ───────────────────────────────

class TestPackageBillingPlanMapping:
    """Without this endpoint the mapping is unsettable and the feature is
    permanently blocked. With it, the mapping must not be able to point at
    another brand's tier."""

    def test_a_god_can_map_a_package_to_its_brands_plan(
            self, client, db_session, world):
        r = client.patch(
            "/god/ops/packages/%s/billing-plan" % world["pkg"].id,
            json={"billing_plan_key": "growth"},
            headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        assert r.json()["billing_plan_key"] == "growth"
        db_session.refresh(world["pkg"])
        assert world["pkg"].billing_plan_key == "growth"

    def test_mapping_to_a_nonexistent_plan_is_refused(self, client, db_session, world):
        r = client.patch(
            "/god/ops/packages/%s/billing-plan" % world["pkg"].id,
            json={"billing_plan_key": "no-such-plan"},
            headers=_h(db_session, world["god"]))
        assert r.status_code == 400
        db_session.refresh(world["pkg"])
        assert world["pkg"].billing_plan_key is None      # NOTHING CHANGED

    def test_mapping_to_ANOTHER_BRANDS_plan_is_refused(self, client, db_session, world):
        """The expensive mistake: billing a customer against a price their own
        brand does not sell."""
        from app.models.billing_models import BrandBillingPlan

        other = Platform(name="OtherBrand", slug=_n("ob"))
        db_session.add(other)
        db_session.commit()
        db_session.add(BrandBillingPlan(
            platform_id=other.id, key="elite", name="Elite", is_active=True,
            monthly_cents=99900, stripe_price_id_monthly="price_other_elite"))
        db_session.commit()

        r = client.patch(
            "/god/ops/packages/%s/billing-plan" % world["pkg"].id,
            json={"billing_plan_key": "elite"},
            headers=_h(db_session, world["god"]))
        assert r.status_code == 400
        db_session.refresh(world["pkg"])
        assert world["pkg"].billing_plan_key is None      # NOTHING CHANGED

    def test_clearing_the_mapping_is_expressible(self, client, db_session, world):
        """A one-time implementation package legitimately has no plan."""
        world["pkg"].billing_plan_key = "growth"
        db_session.commit()
        r = client.patch(
            "/god/ops/packages/%s/billing-plan" % world["pkg"].id,
            json={"billing_plan_key": None},
            headers=_h(db_session, world["god"]))
        assert r.status_code == 200
        db_session.refresh(world["pkg"])
        assert world["pkg"].billing_plan_key is None

    def test_a_rep_cannot_change_the_mapping(self, client, db_session, world):
        """Pricing configuration is control-plane, not sales."""
        r = client.patch(
            "/god/ops/packages/%s/billing-plan" % world["pkg"].id,
            json={"billing_plan_key": "growth"},
            headers=_h(db_session, world["rep"]))
        assert r.status_code == 403
        db_session.refresh(world["pkg"])
        assert world["pkg"].billing_plan_key is None      # NOTHING CHANGED

    def test_anonymous_cannot_change_the_mapping(self, client, db_session, world):
        r = client.patch("/god/ops/packages/%s/billing-plan" % world["pkg"].id,
                         json={"billing_plan_key": "growth"})
        assert r.status_code in (401, 403)
        db_session.refresh(world["pkg"])
        assert world["pkg"].billing_plan_key is None

    def test_the_pricing_route_still_exists(self):
        """I broke its decorator once by inserting this endpoint above it. The
        route vanished silently and every pricing change would have 404'd."""
        from collections import Counter

        from app.main import app
        seen = Counter()
        for route in app.routes:
            for method in getattr(route, "methods", set()) or set():
                seen[(method, getattr(route, "path", None))] += 1
        assert seen[("PATCH", "/god/ops/packages/{package_id}/pricing")] == 1
        assert seen[("PATCH", "/god/ops/packages/{package_id}/billing-plan")] == 1
