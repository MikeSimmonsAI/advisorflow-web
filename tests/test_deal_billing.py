"""
Deal → billing: the join between what was sold and what gets charged.

THE FAILURE THIS FILE EXISTS TO PREVENT is charging a customer an amount
nobody agreed to. Every refusal below therefore also asserts that NO Stripe
call was made — an endpoint that refuses after creating a checkout session has
not refused anything.
"""

import itertools
from datetime import datetime
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

    # THE CATALOGUE PLAN, SHAPED LIKE PRODUCTION: one tier, TWO monthly prices.
    # `monthly_cents` is the committed (term) rate; `month_to_month_cents` is
    # the same tier without a commitment, and it is higher. A fixture with only
    # one of them cannot tell a correct resolution from a lucky one.
    plan = BrandBillingPlan(platform_id=plat.id, key="growth", name="Growth",
                            is_active=True,
                            monthly_cents=50000,
                            month_to_month_cents=59700,
                            stripe_price_id_monthly="price_growth_term_test",
                            stripe_price_id_month_to_month="price_growth_m2m_test")
    db_session.add(plan)

    # The sold package carries the matching pair: `contract_monthly_price` is
    # earned by the term, `monthly_price` is the no-commitment rate.
    pkg = BrandPackage(platform_id=plat.id, key="growth", name="Growth Package",
                       price=Decimal("2500.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13)
    db_session.add(pkg)
    db_session.commit()

    org = Organization(name="Acme Power", slug=_n("acme"), platform_id=plat.id,
                       plan="standard", is_active=True)
    db_session.add(org)
    db_session.commit()

    # BOTH, because a real Won deal has both — and because the two drift apart
    # on purpose once the customer is provisioned. See
    # test_a_PROVISIONED_customer_is_still_Won.
    # An explicit TERM deal. The old fixture said `billing_option="monthly"`,
    # which is not one of the two valid options — `normalize_option` therefore
    # fell back to the default, month-to-month, and every test that thought it
    # was exercising the committed rate was silently exercising the other one.
    opp = Opportunity(company_name="Acme Power", brand_sales_org_id=brand.id,
                      stage="won", status="won", selected_package_id=pkg.id,
                      billing_option="term_agreement", contract_term_months=13)
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
        """Charging either number silently would be wrong. Say so.

        A CATALOGUE deal: the package carries the rate, the mapped plan
        disagrees with it, and neither is quietly preferred. (The same figures
        arrived at through a per-deal CUSTOM rate take the custom-approval
        route instead — see TestCustomRecurring — because there the catalogue
        rate is not what was sold.)
        """
        world["pkg"].billing_plan_key = "growth"
        world["plan"].monthly_cents = 60000          # catalogue moved under it
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        codes = {b["code"] for b in terms["blockers"]}
        assert deal_billing.B_RATE_MISMATCH in codes
        row = next(b for b in terms["blockers"]
                   if b["code"] == deal_billing.B_RATE_MISMATCH)
        assert row["deal_cents"] == 50000            # 500.00 package rate
        assert row["catalogue_cents"] == 60000
        assert not terms.get("billable")
        assert terms["charges"] == [] or all(
            c["kind"] != "subscription" for c in terms["charges"])

    def test_a_deal_with_no_customer_org_cannot_be_billed(self, db_session, world):
        db_session.delete(world["impl"])
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_NO_CUSTOMER_ORG in {b["code"] for b in terms["blockers"]}

    def test_a_deal_that_is_not_won_is_blocked(self, db_session, world):
        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "demo_proposal"
        world["opp"].status = "open"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_NOT_WON in {b["code"] for b in terms["blockers"]}

    def test_a_PROVISIONED_customer_is_still_Won(self, db_session, world):
        """THE BUG LIVE VERIFICATION FOUND, and the reason it was invisible here.

        `provisioning.provision_customer` creates the customer organization AND
        moves the deal to stage 'onboarding', keeping status 'won' — its own
        comment says every Won metric filters on status. This module checked
        `stage`, so it demanded a customer organization (which only provisioning
        creates) and a stage of 'won' (which provisioning immediately ends).
        Nothing could ever be billed through the intended flow.

        Every earlier test set stage and status together, which is exactly why
        none of them caught it. This one reproduces the real post-provisioning
        shape.
        """
        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "onboarding"        # provisioning moved it
        world["opp"].status = "won"              # ...and left this alone
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_NOT_WON not in {b["code"] for b in terms["blockers"]}
        assert terms["blockers"] == [], terms["blockers"]
        assert terms["billable"] is True

    def test_a_won_STAGE_alone_is_not_enough(self, db_session, world):
        """The other half of the fix, and the reason `stage` is not a fallback.

        A deal parked at stage 'won' whose status is still open must not be
        chargeable — compensation.earn() refuses it, so billing that would
        charge a customer nobody gets paid on.
        """
        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "won"
        world["opp"].status = "open"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        row = next(b for b in terms["blockers"]
                   if b["code"] == deal_billing.B_NOT_WON)
        assert row["status"] == "open"
        assert row["stage"] == "won"          # says both, so it reads as sense
        assert not terms.get("billable")

    def test_billing_and_compensation_agree_about_what_Won_MEANS(
            self, db_session, world):
        """Two definitions of Won in one money path is how a customer gets
        charged for a deal nobody gets paid on, or the reverse.

        `compensation.earn()` refuses on `status`. So must this.
        """
        from app.services import compensation as comp

        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "won"               # stage says yes...
        world["opp"].status = "open"             # ...status says no
        db_session.commit()

        with pytest.raises(comp.NotEarnable):
            comp.earn(db_session, world["opp"], collection_reference="pi_x",
                      collected_amount=Decimal("100.00"))
        # And billing must not be readier than payroll on the same deal: the one
        # thing that must never happen is charging a deal compensation refuses.
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert isinstance(terms["blockers"], list)
        assert terms["status"] == "open"

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


# ── term vs month-to-month: the same tier, two configured prices ────────────

# The approved EvoSys rate card, as (setup, term /mo, month-to-month /mo).
# Stated once here so a test cannot pass against a figure it also supplied.
RATE_CARD = {
    "starter":      (150000,  50000,  59700),
    "growth":       (250000, 100000, 129700),
    "professional": (500000, 200000, 259700),
}


class TestCommitmentResolution:
    """A term agreement and a month-to-month deal are two prices on ONE tier.

    The customer bought Starter either way. What differs is the rate, because
    the lower one is earned by committing. The failure this class guards is
    resolving the committed rate for a customer who committed to nothing —
    which hands away the discount silently, on every renewal, forever.
    """

    def _tier(self, db, world, key, *, commitment):
        """Configure one tier end to end and point the deal at it.

        UPSERTS the plan: the brand is unique on (platform_id, key), the fixture
        already ships a `growth` plan, and one test configures the same tier
        twice to compare the two commitments.
        """
        from app.models.billing_models import BrandBillingPlan
        setup, term_cents, m2m_cents = RATE_CARD[key]

        plan = (db.query(BrandBillingPlan)
                .filter(BrandBillingPlan.platform_id == world["plat"].id,
                        BrandBillingPlan.key == key).first())
        if plan is None:
            plan = BrandBillingPlan(platform_id=world["plat"].id, key=key,
                                    name=key.title())
            db.add(plan)
        plan.name = key.title()
        plan.is_active = True
        plan.is_purchasable = True
        plan.monthly_cents = term_cents
        plan.month_to_month_cents = m2m_cents
        plan.stripe_price_id_monthly = "price_%s_term" % key
        plan.stripe_price_id_month_to_month = "price_%s_m2m" % key

        pkg = BrandPackage(
            platform_id=world["plat"].id, key=_n(key), name=key.title(),
            setup_fee=Decimal(setup) / 100,
            monthly_price=Decimal(m2m_cents) / 100,          # no commitment
            contract_monthly_price=Decimal(term_cents) / 100,  # committed
            contract_term_months=13,
            billing_plan_key=key)
        db.add(pkg)
        db.commit()

        world["opp"].selected_package_id = pkg.id
        world["opp"].billing_option = (
            "term_agreement" if commitment == "term_agreement" else "month_to_month")
        world["opp"].contract_term_months = (
            13 if commitment == "term_agreement" else None)
        db.commit()
        return plan, pkg

    @pytest.mark.parametrize("key", ["starter", "growth", "professional"])
    def test_a_TERM_deal_resolves_the_committed_rate(self, db_session, world, key):
        setup, term_cents, m2m_cents = RATE_CARD[key]
        self._tier(db_session, world, key, commitment="term_agreement")

        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        assert terms["commitment"] == "term_agreement"
        assert terms["setup_cents"] == setup
        assert terms["recurring_cents"] == term_cents
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["cents"] == term_cents
        assert sub["commitment"] == "term_agreement"
        assert sub["plan"] == key
        # The discount is real: the committed rate is BELOW the standard one.
        assert term_cents < m2m_cents
        assert terms["billable"] is True

    @pytest.mark.parametrize("key", ["starter", "growth", "professional"])
    def test_a_MONTH_TO_MONTH_deal_resolves_the_standard_rate(
            self, db_session, world, key):
        setup, term_cents, m2m_cents = RATE_CARD[key]
        self._tier(db_session, world, key, commitment="month_to_month")

        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        assert terms["commitment"] == "month_to_month"
        assert terms["setup_cents"] == setup          # setup is identical
        assert terms["recurring_cents"] == m2m_cents
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["cents"] == m2m_cents
        assert sub["commitment"] == "month_to_month"
        # STILL THE SAME TIER. Not "starter_mtm", not a fourth package.
        assert sub["plan"] == key
        assert terms["plan"]["key"] == key

    @pytest.mark.parametrize("key", ["starter", "growth", "professional"])
    def test_the_two_commitments_never_resolve_the_same_amount(
            self, db_session, world, key):
        """The whole point, stated as one assertion: switching the commitment
        must move the money."""
        self._tier(db_session, world, key, commitment="term_agreement")
        term = deal_billing.terms_for(db_session, world["opp"])["recurring_cents"]
        self._tier(db_session, world, key, commitment="month_to_month")
        m2m = deal_billing.terms_for(db_session, world["opp"])["recurring_cents"]
        assert term != m2m
        assert (term, m2m) == RATE_CARD[key][1:]

    def test_a_month_to_month_deal_uses_the_month_to_month_STRIPE_PRICE(
            self, client, db_session, world):
        """Not just the right number — the right Stripe object. Charging the
        committed Price would bill $500 while the panel promised $597."""
        self._tier(db_session, world, "growth", commitment="month_to_month")
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        kw = create.call_args.kwargs
        assert kw["line_items"][0]["price"] == "price_growth_m2m"
        assert kw["metadata"]["plan"] == "growth"
        assert kw["metadata"]["commitment"] == "month_to_month"
        # SELLER-ASSISTED CHECKOUT RETURNS THE CUSTOMER TO THE SAME PLACE
        # THEIR OWN CHECKOUT WOULD. Both URLs are built by
        # `stripe_return` from this organization's own brand host and an
        # allowlisted path — never an infrastructure hostname, and the cancel
        # carries `part` so the page can say what was backed out of.
        # (The `_brand_base_url` patch above is now inert: the resolver moved
        # to `stripe_return`. It is left in place because this file never
        # asserted on the base, and these assertions check the real thing.)
        for key in ("success_url", "cancel_url"):
            assert kw[key].startswith("https://")
            assert "/billing?" in kw[key]
            assert "part=subscription" in kw[key]
            for infra in ("onrender.com", "vercel.app", "localhost"):
                assert infra not in kw[key]

    def test_the_setup_fee_NEVER_rides_the_subscription_session(
            self, client, db_session, world):
        """THE PRODUCT DECISION THIS PINS, made after reading the live page.

        Two defects lived here in sequence and this test has now guarded both.

        The first was mechanical: the setup fee was sent as
        `subscription_data.add_invoice_items`, which belongs to the
        Subscriptions API and is not a parameter of a Checkout Session. Stripe
        rejected every call outright —

            Received unknown parameter: subscription_data[add_invoice_items]

        — and no test caught it, because every test here mocks
        `stripe.checkout.Session.create`, which records any kwarg happily. It
        was 'fixed' by moving the fee to a second non-recurring line item on
        the SAME session, which Stripe does accept.

        The second was commercial, and worse. That fix made Growth quote
        $2,500 setup + $1,000 first month as one $3,500 charge on one page. A
        combined charge cannot be tracked, reconciled, refunded or chased by
        half, and a customer who has paid to be implemented but not started
        their subscription — a normal, common state — becomes unrepresentable.

        So the subscription session now carries the subscription and NOTHING
        else. The fee has its own session, its own link and its own status.
        """
        self._tier(db_session, world, "growth", commitment="term_agreement")
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=subscription"
                % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        kw = create.call_args.kwargs

        # The parameter Stripe refuses must never be sent again.
        assert "add_invoice_items" not in kw.get("subscription_data", {})

        # ONE line. The recurring one. Nothing else may be attached to it —
        # any second line here is the $3,500 page coming back.
        assert kw["mode"] == "subscription"
        assert len(kw["line_items"]) == 1, \
            "the subscription session must carry the subscription alone"
        assert kw["line_items"][0]["price"] == "price_growth_term"
        assert not any("price_data" in li for li in kw["line_items"]), \
            "a one-off price_data line on the subscription session is the "\
            "combined charge"

    def test_a_deal_with_no_setup_fee_sends_one_line_item(
            self, client, db_session, world):
        """The fee is appended only where the deal has one."""
        _plan, pkg = self._tier(db_session, world, "growth",
                                commitment="term_agreement")
        pkg.setup_fee = None
        pkg.price = None
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text
        assert len(create.call_args.kwargs["line_items"]) == 1

    def test_a_missing_month_to_month_price_REFUSES_rather_than_falling_back(
            self, db_session, world):
        """THE REVENUE LEAK THIS PREVENTS. With no month-to-month price
        configured, falling back to the committed rate would give a customer who
        promised nothing the discount earned by a 13-month commitment — and
        nothing would ever report it."""
        plan, _pkg = self._tier(db_session, world, "growth",
                                commitment="month_to_month")
        plan.month_to_month_cents = None
        plan.stripe_price_id_month_to_month = None
        db_session.commit()

        terms = deal_billing.terms_for(db_session, world["opp"])
        codes = {b["code"] for b in terms["blockers"]}
        assert deal_billing.B_NO_PRICE_ID in codes
        row = next(b for b in terms["blockers"]
                   if b["code"] == deal_billing.B_NO_PRICE_ID)
        assert row["commitment"] == "month_to_month"
        assert all(c["kind"] != "subscription" for c in terms["charges"])
        # And emphatically not the term rate.
        assert not any(c.get("cents") == 100000 for c in terms["charges"])

    def test_the_webhook_can_map_a_month_to_month_price_back_to_its_plan(
            self, db_session, world):
        """`billing_webhook` resolves the plan from the price on the live
        subscription on EVERY renewal. If the month-to-month price did not
        resolve, every such customer would silently lose their tier — and their
        entitlements — at the first renewal."""
        from app.services import billing_catalog

        plan, _pkg = self._tier(db_session, world, "growth",
                                commitment="month_to_month")
        found = billing_catalog.resolve_plan_by_price_id(
            db_session, world["plat"].id, "price_growth_m2m")
        assert found is not None
        assert found.key == "growth"
        assert billing_catalog.interval_for_price_id(found, "price_growth_m2m") == "month"
        assert billing_catalog.commitment_for_price_id(
            found, "price_growth_m2m") == "month_to_month"
        assert billing_catalog.commitment_for_price_id(
            found, "price_growth_term") == "term_agreement"

    def test_a_price_from_ANOTHER_BRAND_never_resolves(self, db_session, world):
        """Cross-brand contamination, on the new column as well as the old."""
        from app.models.billing_models import BrandBillingPlan
        from app.services import billing_catalog

        other = Platform(name="Other", slug=_n("oth"))
        db_session.add(other)
        db_session.commit()
        db_session.add(BrandBillingPlan(
            platform_id=other.id, key="growth", name="Growth", is_active=True,
            monthly_cents=1, month_to_month_cents=2,
            stripe_price_id_month_to_month="price_OTHER_m2m"))
        db_session.commit()

        assert billing_catalog.resolve_plan_by_price_id(
            db_session, world["plat"].id, "price_OTHER_m2m") is None


# ── a custom recurring rate ─────────────────────────────────────────────────

def _approval(db, opp, brand, manager, *, unit, units=1, status="approved"):
    """A custom-deal pricing approval request in a given state.

    Written through the model rather than through approve_custom_deal() on
    purpose: these tests are about what deal_billing will and will not BILL
    given an approval row, and building the row directly is what lets a denied,
    pending or wrong-amount row be tested at all.
    """
    from app.models.sales_models import PricingApprovalRequest
    req = PricingApprovalRequest(
        brand_sales_org_id=brand.id, opportunity_id=opp.id,
        requested_by=manager.id, reason="Negotiated on the call.",
        request_kind="custom_deal",
        requested_unit_price=Decimal(str(unit)), requested_min_units=units,
        requested_term_months=12, status=status,
        decided_by=manager.id if status != "pending" else None,
        decided_at=datetime.utcnow() if status != "pending" else None)
    db.add(req)
    db.commit()
    return req


class TestCustomRecurring:
    """A custom rate is billable at the deal's agreed figure, not at a tier's.

    The gate is NOT "an approved request exists". A custom-deal approval request
    is only created when pricing_authority returns NEEDS_APPROVAL, which needs a
    catalogue rate to measure against — and a Custom package has none, so the
    verdict is ALLOWED and no request is ever created. Demanding one would
    refuse every real Custom deal while making the approval that would clear it
    impossible to obtain.

    What is refused is a real signal: a DENIED figure, and a PENDING decision.
    """

    def _custom_deal(self, db, world, unit, units=1):
        """A Won deal whose recurring rate is a per-deal custom figure."""
        world["opp"].custom_unit_price = Decimal(str(unit))
        world["opp"].custom_min_units = units
        world["opp"].custom_term_months = 12
        db.commit()

    def test_a_custom_rate_with_no_approval_row_at_all_is_still_billable(
            self, db_session, world):
        """THE CASE THAT BREAKS A NAIVE APPROVAL CHECK, and the common one.

        A true Custom package has no catalogue rate, so pricing_authority finds
        no discount to measure, returns ALLOWED, and sales_router writes the rate
        with no request created. There is no approval row and there never will
        be. If this deal is not billable, custom deals do not work at all.
        """
        self._custom_deal(db_session, world, "250.00", units=15)
        from app.models.sales_models import PricingApprovalRequest
        assert db_session.query(PricingApprovalRequest).count() == 0

        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["pricing_mode"] == deal_billing.PRICING_CUSTOM
        assert sub["cents"] == 375000                    # $250 x 15
        assert sub["plan"] is None                       # no tier is claimed
        assert sub["stripe_price_id_configured"] is False
        assert sub["authority"] == "pricing_authority"
        assert terms["billable"] is True

    def test_a_manager_approved_rate_records_the_approval(self, db_session, world):
        """Where an approval DOES exist it is the strongest provenance there is,
        so it is named on the charge — the audit trail from an invoice back to
        the person who said yes."""
        self._custom_deal(db_session, world, "1750.00")
        req = _approval(db_session, world["opp"], world["brand"], world["god"],
                        unit="1750.00")
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["authority"] == "manager_approval"
        assert sub["approval_id"] == req.id
        assert terms["custom_pricing"]["authority"] == "manager_approval"

    def test_it_does_NOT_have_to_equal_a_catalogue_price(self, db_session, world):
        """The requirement Mike named: a custom rate must not be forced onto a
        standard tier's figure to be chargeable."""
        world["pkg"].billing_plan_key = "growth"      # catalogue is 500.00/mo
        self._custom_deal(db_session, world, "1750.00")
        terms = deal_billing.terms_for(db_session, world["opp"])
        codes = {b["code"] for b in terms["blockers"]}
        assert deal_billing.B_RATE_MISMATCH not in codes
        assert terms["blockers"] == [], terms["blockers"]
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["cents"] == 175000
        assert sub["cents"] != world["plan"].monthly_cents

    def test_a_DENIED_figure_is_never_charged(self, db_session, world):
        """A manager said no to this exact amount. However it got onto the deal,
        it must not reach a card."""
        self._custom_deal(db_session, world, "1750.00")
        req = _approval(db_session, world["opp"], world["brand"], world["god"],
                        unit="1750.00", status="denied")
        terms = deal_billing.terms_for(db_session, world["opp"])
        row = next(b for b in terms["blockers"]
                   if b["code"] == deal_billing.B_CUSTOM_DENIED)
        assert row["denied_request_id"] == req.id
        assert row["deal_cents"] == 175000
        assert not terms.get("billable")
        assert all(c["kind"] != "subscription" for c in terms["charges"])

    def test_a_denial_at_a_DIFFERENT_figure_does_not_block_this_one(
            self, db_session, world):
        """A refused $3,000 says nothing about an agreed $1,750. Blocking on any
        denial anywhere would make one rejected ask poison the deal forever."""
        self._custom_deal(db_session, world, "1750.00")
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="3000.00", status="denied")
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        assert any(c["kind"] == "subscription" for c in terms["charges"])

    def test_a_PENDING_decision_holds_the_charge(self, db_session, world):
        """Somebody is still deciding the price. Charging now can bill an amount
        that is about to change — and the block clears either way once they
        answer, so it is not a dead end."""
        self._custom_deal(db_session, world, "1750.00")
        req = _approval(db_session, world["opp"], world["brand"], world["god"],
                        unit="2100.00", status="pending")
        terms = deal_billing.terms_for(db_session, world["opp"])
        row = next(b for b in terms["blockers"]
                   if b["code"] == deal_billing.B_CUSTOM_DECISION_PENDING)
        assert row["pending_request_id"] == req.id
        assert row["pending_cents"] == 210000
        assert row["deal_cents"] == 175000
        assert all(c["kind"] != "subscription" for c in terms["charges"])

    def test_a_pending_request_outranks_an_earlier_approval(
            self, db_session, world):
        """A renegotiation in flight means the settled figure is no longer
        settled. The pending question wins."""
        self._custom_deal(db_session, world, "1750.00")
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="1750.00", status="approved")
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="1200.00", status="pending")
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_CUSTOM_DECISION_PENDING in {
            b["code"] for b in terms["blockers"]}

    def test_a_denial_outranks_an_approval_at_the_same_figure(
            self, db_session, world):
        """Contradictory decisions on one amount resolve to NOT CHARGING it.
        The direction of that tie-break is the whole point."""
        self._custom_deal(db_session, world, "1750.00")
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="1750.00", status="approved")
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="1750.00", status="denied")
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert deal_billing.B_CUSTOM_DENIED in {
            b["code"] for b in terms["blockers"]}
        assert all(c["kind"] != "subscription" for c in terms["charges"])

    def test_another_deals_denial_does_not_block_this_deal(
            self, db_session, world):
        """Decisions are per-deal. One customer's refused rate is not a bar on
        another customer agreeing the same figure."""
        other = Opportunity(company_name="Other Co", stage="won",
                            brand_sales_org_id=world["brand"].id)
        db_session.add(other)
        db_session.commit()
        _approval(db_session, other, world["brand"], world["god"],
                  unit="1750.00", status="denied")
        self._custom_deal(db_session, world, "1750.00")
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]

    def test_a_withdrawn_or_stale_request_neither_blocks_nor_licenses(
            self, db_session, world):
        """Neither is a decision about the money. They are closed questions."""
        for status in ("withdrawn", "stale"):
            _approval(db_session, world["opp"], world["brand"], world["god"],
                      unit="1750.00", status=status)
        self._custom_deal(db_session, world, "1750.00")
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["blockers"] == [], terms["blockers"]
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["authority"] == "pricing_authority"

    def test_a_custom_rate_snapshotted_on_a_PROPOSAL_is_billed_from_it(
            self, db_session, world):
        """deal_pricing prefers the sent proposal, and that is correct: the
        document the customer holds is what they agreed to. A proposal's custom
        rate can only be set by a manager (proposal_service.apply_custom_rate
        requires can_override_price), so it carries the same authority."""
        from app.models.models import Proposal

        prop = Proposal(
            opportunity_id=world["opp"].id, organization_id=world["org"].id,
            proposal_number=_n("P"), version=1, title="Acme Power proposal",
            created_by_id=world["rep"].id,
            sent_at=datetime.utcnow(), withhold_pricing=False,
            package_id=world["pkg"].id,
            custom_unit_price=Decimal("1850.00"), custom_min_units=1,
            custom_term_months=12, billing_option="monthly")
        db_session.add(prop)
        db_session.commit()

        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["recurring_cents"] == 185000
        assert terms["proposal_id"] == prop.id
        sub = next(c for c in terms["charges"] if c["kind"] == "subscription")
        assert sub["cents"] == 185000

    def test_the_setup_fee_still_rides_the_same_deal(self, db_session, world):
        """A custom recurring rate must not lose the one-time fee beside it."""
        self._custom_deal(db_session, world, "1750.00")
        terms = deal_billing.terms_for(db_session, world["opp"])
        kinds = {c["kind"] for c in terms["charges"]}
        assert kinds == {"setup_fee", "subscription"}


class TestCustomEntitlements:
    """A Custom customer is on no tier. That must not mean "no limits".

    Nor may it mean "whatever Starter says". The agreed ceilings are recorded
    against the customer and read from there, and a dimension nobody agreed is
    reported as NEEDS CONFIGURATION rather than silently granted.
    """

    def _custom_customer(self, db, world):
        """A won Custom deal with a provisioned org on no catalogue plan."""
        world["opp"].custom_unit_price = Decimal("250.00")
        world["opp"].custom_min_units = 15
        world["opp"].custom_term_months = 24
        world["org"].billing_plan_key = None
        world["org"].plan = None
        db.commit()

    def test_a_custom_customer_on_no_tier_reports_UNSET_not_unlimited(
            self, db_session, world):
        """The exact failure this closes: silence read as permission."""
        from app.services import plan_limits

        self._custom_customer(db_session, world)
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["source"] == plan_limits.ENTITLEMENT_NONE
        assert state["policy_required"] is True
        # Every dimension is named as unrecorded, not quietly uncapped.
        assert set(state["unset_dimensions"]) == set(plan_limits.SNAPSHOT_DIMENSIONS)
        assert state["agreed_unlimited"] == []
        assert "NEEDS CONFIGURATION" in state["explanation"]

    def test_the_deal_surfaces_that_state_rather_than_a_bare_flag(
            self, db_session, world):
        self._custom_customer(db_session, world)
        terms = deal_billing.terms_for(db_session, world["opp"])
        ent = terms["custom_pricing"]["entitlement"]
        assert ent["source"] == "unset"
        assert ent["policy_required"] is True

    def test_recorded_agreement_ceilings_are_what_apply(self, client, db_session, world):
        """Agreement-driven, and enforced through the EXISTING limits engine —
        not a second one."""
        from app.services import plan_limits

        self._custom_customer(db_session, world)
        r = client.put(
            "/god/billing/customers/%s/entitlements" % world["org"].id,
            json={"max_users": 25, "max_leads": 50000, "max_locations": 12,
                  "unlimited": ["email_monthly_allowance"],
                  "opportunity_id": world["opp"].id,
                  "note": "Negotiated: 12 locations, 25 seats."},
            headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text

        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["source"] == plan_limits.ENTITLEMENT_SNAPSHOT
        assert state["limits"]["max_users"] == 25
        assert state["limits"]["max_leads"] == 50000
        assert state["opportunity_id"] == world["opp"].id
        # Explicitly agreed as uncapped — a decision, not an absence.
        assert "email_monthly_allowance" in state["agreed_unlimited"]
        # Still unrecorded, and still reported as such.
        assert "sms_monthly_allowance" in state["unset_dimensions"]
        assert state["policy_required"] is True

        # And the ONE enforcement path reads it.
        assert plan_limits.limit_for(db_session, world["org"], "max_users") == 25
        assert plan_limits.limit_for(db_session, world["org"], "max_leads") == 50000

    def test_a_custom_customer_is_never_given_a_standard_tiers_limits(
            self, client, db_session, world):
        """The other wrong fix. Growth allows 3 users; this customer agreed 25
        and must not be capped at somebody else's number."""
        from app.services import plan_limits

        world["plan"].max_users = 3
        db_session.commit()
        self._custom_customer(db_session, world)
        client.put("/god/billing/customers/%s/entitlements" % world["org"].id,
                   json={"max_users": 25},
                   headers=_h(db_session, world["god"]))
        assert plan_limits.limit_for(db_session, world["org"], "max_users") == 25
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["plan_key"] is None

    def test_a_catalogue_customer_still_uses_the_catalogue(self, db_session, world):
        """The snapshot is a FALLBACK for customers on no tier. It must not
        override a tier the customer actually is on."""
        from app.services import plan_limits

        world["plan"].max_users = 3
        world["org"].billing_plan_key = "growth"
        db_session.commit()
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["source"] == plan_limits.ENTITLEMENT_CATALOGUE
        assert state["plan_key"] == "growth"
        assert state["policy_required"] is False
        assert plan_limits.limit_for(db_session, world["org"], "max_users") == 3

    def test_a_snapshot_does_not_MUTATE_when_the_catalogue_changes(
            self, client, db_session, world):
        """Matrix item 21. The agreement is a snapshot of literal numbers, so a
        brand editing its rate card later cannot rewrite what an existing
        customer was sold."""
        from app.services import plan_limits

        self._custom_customer(db_session, world)
        client.put("/god/billing/customers/%s/entitlements" % world["org"].id,
                   json={"max_users": 25, "max_leads": 50000},
                   headers=_h(db_session, world["god"]))

        # The brand rewrites its catalogue afterwards.
        world["plan"].max_users = 1
        world["plan"].max_leads = 10
        world["plan"].monthly_cents = 999999
        db_session.commit()

        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["limits"]["max_users"] == 25
        assert state["limits"]["max_leads"] == 50000

    def test_a_renegotiation_SUPERSEDES_and_keeps_history(
            self, client, db_session, world):
        from app.models.billing_models import CustomerEntitlementSnapshot
        from app.services import plan_limits

        self._custom_customer(db_session, world)
        first = client.put(
            "/god/billing/customers/%s/entitlements" % world["org"].id,
            json={"max_users": 10}, headers=_h(db_session, world["god"])).json()
        second = client.put(
            "/god/billing/customers/%s/entitlements" % world["org"].id,
            json={"max_users": 40}, headers=_h(db_session, world["god"])).json()

        assert second["superseded_snapshot_id"] == first["snapshot_id"]
        assert plan_limits.limit_for(db_session, world["org"], "max_users") == 40
        # Nothing was deleted — last quarter's terms stay answerable.
        assert db_session.query(CustomerEntitlementSnapshot).filter(
            CustomerEntitlementSnapshot.organization_id == world["org"].id
        ).count() == 2

    def test_an_unknown_dimension_or_feature_is_refused(self, client, db_session, world):
        """A typo recorded as an entitlement grants nothing and is never noticed."""
        from app.models.billing_models import CustomerEntitlementSnapshot

        self._custom_customer(db_session, world)
        bad_dim = client.put(
            "/god/billing/customers/%s/entitlements" % world["org"].id,
            json={"unlimited": ["max_widgets"]},
            headers=_h(db_session, world["god"]))
        assert bad_dim.status_code == 400
        bad_feat = client.put(
            "/god/billing/customers/%s/entitlements" % world["org"].id,
            json={"features": ["teleportation"]},
            headers=_h(db_session, world["god"]))
        assert bad_feat.status_code == 400
        # NOTHING WAS WRITTEN by either refusal.
        assert db_session.query(CustomerEntitlementSnapshot).count() == 0

    def test_a_rep_cannot_record_entitlements(self, client, db_session, world):
        """Commercial entitlement is control-plane, not sales."""
        from app.models.billing_models import CustomerEntitlementSnapshot

        r = client.put("/god/billing/customers/%s/entitlements" % world["org"].id,
                       json={"max_users": 999},
                       headers=_h(db_session, world["rep"]))
        assert r.status_code == 403
        assert db_session.query(CustomerEntitlementSnapshot).count() == 0

    def test_anonymous_cannot_record_entitlements(self, client, db_session, world):
        r = client.put("/god/billing/customers/%s/entitlements" % world["org"].id,
                       json={"max_users": 999})
        assert r.status_code in (401, 403)


class TestCustomCheckoutComposition:
    def _ready(self, db, world, unit="1750.00", units=1):
        world["opp"].custom_unit_price = Decimal(unit)
        world["opp"].custom_min_units = units
        world["opp"].custom_term_months = 12
        world["org"].stripe_customer_id = "cus_test123"
        db.commit()
        _approval(db, world["opp"], world["brand"], world["god"],
                  unit=unit, units=units)

    def _post(self, client, db, world):
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                headers=_h(db, world["god"]))
        return r, create

    def test_a_custom_rate_becomes_an_inline_RECURRING_price(
            self, client, db_session, world):
        """Without `recurring` the line is a one-off and the session silently
        stops being a subscription — the customer pays once and never again."""
        self._ready(db_session, world)
        r, create = self._post(client, db_session, world)
        assert r.status_code == 200, r.text
        kw = create.call_args.kwargs
        assert kw["mode"] == "subscription"
        item = kw["line_items"][0]
        assert "price" not in item                  # no catalogue price id
        assert item["price_data"]["unit_amount"] == 175000
        assert item["price_data"]["recurring"]["interval"] == "month"

    def test_no_plan_is_named_in_metadata_so_the_webhook_maps_no_tier(
            self, client, db_session, world):
        """billing_webhook writes billing_plan_key from `plan` metadata. Naming
        a tier for an inline price would put the org on a plan it is not paying
        for — on the first invoice AND on every renewal."""
        self._ready(db_session, world)
        _r, create = self._post(client, db_session, world)
        kw = create.call_args.kwargs
        assert "plan" not in kw["metadata"]
        assert "plan" not in kw["subscription_data"]["metadata"]
        assert kw["metadata"]["deal_kind"] == "custom"
        assert kw["metadata"]["opportunity_id"] == world["opp"].id

    def test_what_licensed_the_amount_is_recorded_on_the_session(
            self, client, db_session, world):
        """A custom price with no provenance is a number nobody can audit back
        to a decision."""
        self._ready(db_session, world)
        r, create = self._post(client, db_session, world)
        meta = create.call_args.kwargs["metadata"]
        assert meta["pricing_authority"] == "manager_approval"
        assert meta["pricing_approval_id"]
        # ONE SESSION BILLS ONE OBLIGATION, so the response returns the single
        # charge it created rather than the deal's whole charge list. Returning
        # every charge here is how a caller renders "you are collecting $3,500"
        # over a $2,500 setup link.
        body = r.json()
        assert body["part"] == "subscription"
        assert body["charge"]["kind"] == "subscription"
        assert body["charge"]["approval_id"]

    def test_a_rate_set_without_an_approval_row_still_records_its_authority(
            self, client, db_session, world):
        """The common Custom case. `pricing_authority` is a real answer to "on
        what basis", not a blank."""
        world["opp"].custom_unit_price = Decimal("1750.00")
        world["opp"].custom_min_units = 1
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        r, create = self._post(client, db_session, world)
        assert r.status_code == 200, r.text
        meta = create.call_args.kwargs["metadata"]
        assert meta["pricing_authority"] == "pricing_authority"
        assert "pricing_approval_id" not in meta
        assert "plan" not in meta

    def test_a_DENIED_rate_never_reaches_stripe(self, client, db_session, world):
        """The assertion that matters: NOTHING was charged."""
        world["opp"].custom_unit_price = Decimal("1750.00")
        world["opp"].custom_min_units = 1
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="1750.00", status="denied")
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 409
        assert create.call_count == 0
        codes = {b["code"] for b in r.json()["detail"]["blockers"]}
        assert deal_billing.B_CUSTOM_DENIED in codes

    def test_a_PENDING_decision_never_reaches_stripe(
            self, client, db_session, world):
        world["opp"].custom_unit_price = Decimal("1750.00")
        world["opp"].custom_min_units = 1
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        _approval(db_session, world["opp"], world["brand"], world["god"],
                  unit="1900.00", status="pending")
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 409
        assert create.call_count == 0
        codes = {b["code"] for b in r.json()["detail"]["blockers"]}
        assert deal_billing.B_CUSTOM_DECISION_PENDING in codes

    def test_a_catalogue_deal_still_uses_the_configured_stripe_price(
            self, client, db_session, world):
        """The custom path must not have loosened the standard one: a Starter,
        Growth or Professional deal still bills only against its brand's own
        configured price id."""
        world["pkg"].billing_plan_key = "growth"
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        r, create = self._post(client, db_session, world)
        assert r.status_code == 200, r.text
        item = create.call_args.kwargs["line_items"][0]
        assert item["price"] == "price_growth_term_test"
        assert "price_data" not in item
        assert create.call_args.kwargs["metadata"]["plan"] == "growth"


# ── authority ───────────────────────────────────────────────────────────────

class TestAuthority:
    def test_anonymous_is_refused_on_both_routes(self, client, world):
        oid = world["opp"].id
        assert client.get("/sales/opportunities/%s/billing" % oid
                          ).status_code in (401, 403)
        assert client.post("/sales/opportunities/%s/billing/checkout?part=setup" % oid
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
                "/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                headers=_h(db_session, world["god"]))
            assert r.status_code == 409
            assert create.call_count == 0
        body = r.json()["detail"]
        assert body["blockers"]

    def test_a_not_won_deal_never_reaches_stripe(self, client, db_session, world):
        world["pkg"].billing_plan_key = "growth"
        world["opp"].stage = "closing"
        world["opp"].status = "open"
        db_session.commit()
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=setup" % world["opp"].id,
                headers=_h(db_session, world["god"]))
            assert r.status_code == 409
            assert create.call_count == 0


# ── checkout composition ────────────────────────────────────────────────────

class _Sess:
    url = "https://checkout.stripe.test/session_123"
    # The router records the session id against the obligation it bills, so a
    # seller can reopen the exact page that was sent. A stub without one would
    # pass every assertion here while the real route raised AttributeError.
    id = "cs_test_session_123"


class TestCheckoutComposition:
    def _ready(self, db, world):
        world["pkg"].billing_plan_key = "growth"
        world["org"].stripe_customer_id = "cus_test123"
        db.commit()

    def _post(self, client, db, world, part):
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=%s"
                % (world["opp"].id, part),
                headers=_h(db, world["god"]))
        return r, create

    def test_setup_and_subscription_are_TWO_sessions(
            self, client, db_session, world):
        """The correction. One page cannot be two bills.

        This test replaces `test_setup_plus_subscription_is_ONE_session`, which
        asserted the opposite and was right about the mechanics and wrong about
        the product: it made Growth a single $3,500 charge. Setup and
        subscription are separate obligations with separate money, separate
        lifecycles and separate answers to "has this been collected?", so they
        get separate Stripe objects.
        """
        self._ready(db_session, world)

        r_sub, create_sub = self._post(client, db_session, world, "subscription")
        assert r_sub.status_code == 200, r_sub.text
        assert create_sub.call_count == 1
        sub_kw = create_sub.call_args.kwargs
        assert sub_kw["mode"] == "subscription"
        assert len(sub_kw["line_items"]) == 1
        assert sub_kw["line_items"][0]["price"] == "price_growth_term_test"
        assert sub_kw["metadata"]["part"] == "subscription"

        r_setup, create_setup = self._post(client, db_session, world, "setup")
        assert r_setup.status_code == 200, r_setup.text
        setup_kw = create_setup.call_args.kwargs
        # A ONE-TIME FEE IS A PAYMENT, NOT A SUBSCRIPTION. `mode` is what
        # decides whether Stripe ever charges this customer again.
        assert setup_kw["mode"] == "payment"
        assert len(setup_kw["line_items"]) == 1
        fee = setup_kw["line_items"][0]
        assert fee["price_data"]["unit_amount"] == 250000
        assert "recurring" not in fee["price_data"]
        assert setup_kw["metadata"]["part"] == "setup"

        # THE TWO SESSIONS MUST NOT SHARE A PLAN. `plan` metadata is what moves
        # the customer onto a catalogue tier; on a setup session it would put
        # them on a plan the moment they paid an implementation fee, with no
        # subscription existing at all.
        assert "plan" not in setup_kw["metadata"]
        assert sub_kw["metadata"]["plan"] == "growth"

        # And the two responses name which bill they are, so a caller cannot
        # mistake one link for the other.
        assert r_sub.json()["part"] == "subscription"
        assert r_setup.json()["part"] == "setup"

    def test_the_deal_trail_is_carried_in_metadata(self, client, db_session, world):
        """Without this the webhook cannot connect money back to what was sold."""
        self._ready(db_session, world)
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            client.post("/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                        headers=_h(db_session, world["god"]))
        meta = create.call_args.kwargs["metadata"]
        assert meta["org_id"] == world["org"].id
        assert meta["opportunity_id"] == world["opp"].id
        assert meta["source"] == "deal_billing"

    def test_a_setup_only_deal_uses_payment_mode(self, client, db_session, world):
        """No recurring rate means no subscription — not a £0 one.

        BOTH monthly rates have to go. Clearing only `monthly_price` leaves the
        contracted rate standing, and this deal is a term agreement.
        """
        world["pkg"].monthly_price = None
        world["pkg"].contract_monthly_price = None
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=setup" % world["opp"].id,
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
            client.post("/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                        headers=_h(db_session, world["god"]))
        assert db_session.query(BillingPayment).count() == before


# ── the seller sees ONE authoritative commercial truth ──────────────────────

class TestNoContradictoryCustomerPrice:
    """Matrix item 20. Two authoritative-looking prices on one screen.

    Live production showed `DEAL VALUE $1,497` in the deal's Record block while
    the Billing panel — correctly — showed a $1,500 one-time setup. Both looked
    definitive. A rep reading the first quotes a number the customer will not be
    charged.

    The column is deliberately NOT deleted: `deal_pricing` still reads it as
    `legacy_one_time_value` for deals that have nothing else, and pipeline
    reporting sums it. This guards the PRESENTATION only.
    """

    def _record_block(self) -> str:
        import pathlib
        import re
        src = pathlib.Path("frontend/src/pages/sales/OpportunityDetail.jsx").read_text(
            encoding="utf-8")
        # Comments explain the reasoning and legitimately mention the old label;
        # only shipped markup matters, so they are stripped first.
        src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
        src = re.sub(r"//[^\n]*", "", src)
        start = src.index('className="sw-infogrid"')
        return src[start:start + 2000]

    def test_the_record_block_never_shows_deal_value_unconditionally(self):
        block = self._record_block()
        assert "deal_value" in block, (
            "The legacy value should still be reachable for deals that have "
            "nothing else — this guard is about how, not whether.")
        # It must be gated on the deal having no resolved commercial terms.
        assert "selected_package_id" in block and "custom_unit_price" in block, (
            "`deal_value` is rendered without checking whether the deal has "
            "resolved commercial terms. On a deal with a package or a "
            "negotiated rate it competes with the Billing panel, which is the "
            "authority on what the customer is charged.")

    def test_it_is_not_labelled_as_though_it_were_the_charge(self):
        block = self._record_block()
        assert 'label="DEAL VALUE"' not in block, (
            "A bare 'DEAL VALUE' label reads as what the customer will pay.")
        assert "LEGACY" in block and "REPORTING" in block

    def test_the_billing_panel_states_the_commitment(self):
        """A recurring figure with no commitment beside it is ambiguous between
        two real prices — $500 committed and $597 not."""
        import pathlib
        src = pathlib.Path("frontend/src/pages/sales/DealBillingPanel.jsx").read_text(
            encoding="utf-8")
        assert "COMMITMENT" in src
        assert "commitment_label" in src

    def test_terms_for_always_answers_the_seller_questions(self, db_session, world):
        """Package, setup, recurring, commitment, pricing source — the five
        things a rep has to be able to state without inferring any of them."""
        world["pkg"].billing_plan_key = "growth"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        for field in ("package_name", "setup_cents", "recurring_cents",
                      "commitment", "commitment_label", "pricing_source",
                      "billable", "blockers"):
            assert field in terms, field
        assert terms["commitment_label"]


# ── no secret ever leaves the server ────────────────────────────────────────

class TestNoSecretLeak:
    def test_no_stripe_secret_appears_in_any_response(self, client, db_session, world):
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


# ── TWO BILLS, INDEPENDENTLY TRACKED ────────────────────────────────────────

class TestTheTwoObligationsAreIndependent:
    """The correction Mike asked for, pinned end to end.

    ═══════════════════════════════════════════════════════════════════════
    WHAT WENT WRONG, IN THE CUSTOMER'S OWN WORDS
    ═══════════════════════════════════════════════════════════════════════
    A Growth deal's Stripe Checkout page read: $2,500 setup + $1,000
    subscription = $3,500 due today. One page, one amount, one payment status.

    That is not a presentation bug. It makes three ordinary situations
    impossible to represent at all:

      * setup paid, subscription not started (the customer is being built)
      * subscription running, setup fee still outstanding
      * either one refunded, chased or re-sent without touching the other

    So: two Stripe objects, two links, two statuses, no ordering rule — because
    no configured policy anywhere says one must precede the other, and the UI
    is not the place to invent terms the business never agreed.
    """

    def _ready(self, db, world):
        world["pkg"].billing_plan_key = "growth"
        world["org"].stripe_customer_id = "cus_test123"
        db.commit()

    def _create(self, client, db, world, part):
        with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
             patch("app.routers.billing_router._stripe_client"), \
             patch("app.routers.billing_router._get_or_create_customer",
                   return_value="cus_test123"), \
             patch("app.routers.billing_router._brand_base_url",
                   return_value="https://brand.test"):
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=%s"
                % (world["opp"].id, part),
                headers=_h(db, world["god"]))
        return r, create

    # ── the parameter itself ────────────────────────────────────────────

    def test_a_checkout_with_no_part_is_REFUSED_not_guessed(
            self, client, db_session, world):
        """No default, and deliberately so.

        A default would silently reinstate the combined bill the first time a
        caller forgot the parameter — and the caller that forgets is the one
        that was written before the split existed.
        """
        self._ready(db_session, world)
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 422
        assert create.call_count == 0

    def test_an_invented_part_is_refused(self, client, db_session, world):
        self._ready(db_session, world)
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=both"
                % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 422
        assert create.call_count == 0

    def test_billing_a_half_the_deal_does_not_have_is_refused(
            self, client, db_session, world):
        """Asking for a setup fee on a deal with none must not quietly become
        a subscription — that is how somebody collecting an implementation fee
        starts a customer's recurring billing by accident."""
        world["pkg"].billing_plan_key = "growth"
        world["pkg"].price = None
        world["pkg"].setup_fee = None
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        with patch("stripe.checkout.Session.create") as create:
            r = client.post(
                "/sales/opportunities/%s/billing/checkout?part=setup"
                % world["opp"].id,
                headers=_h(db_session, world["god"]))
        assert r.status_code == 409, r.text
        assert create.call_count == 0
        assert "setup" in r.json()["detail"]["message"].lower()
        # AND IT IS REFUSED BEFORE STRIPE IS TOUCHED. This asserted 503 for a
        # while: the caller error was reported as "the payment processor is
        # unavailable" because the client was constructed first. Worse,
        # `_get_or_create_customer` writes — a request that is about to be
        # refused was creating a Stripe customer on the way to refusing it.
        assert r.json()["detail"]["part"] == "setup"

    # ── creating one does not touch the other ───────────────────────────

    def test_creating_a_checkout_marks_NOTHING_paid(
            self, client, db_session, world):
        """A link is not money. Only a verified webhook says otherwise."""
        self._ready(db_session, world)
        for part in ("setup", "subscription"):
            r, _ = self._create(client, db_session, world, part)
            assert r.status_code == 200, r.text
        db_session.refresh(world["impl"])
        db_session.refresh(world["org"])
        assert world["impl"].setup_payment_status == "checkout_pending"
        assert world["impl"].setup_paid_at is None
        assert world["impl"].setup_paid_cents is None
        # No subscription was started by creating a page for one.
        assert world["org"].billing_status in (None, "")
        assert world["org"].stripe_subscription_id is None

    def test_opening_the_setup_checkout_does_not_start_the_subscription(
            self, client, db_session, world):
        self._ready(db_session, world)
        r, create = self._create(client, db_session, world, "setup")
        assert r.status_code == 200, r.text
        assert create.call_args.kwargs["mode"] == "payment"
        db_session.refresh(world["impl"])
        db_session.refresh(world["org"])
        assert world["impl"].subscription_checkout_session_id is None
        assert world["impl"].subscription_checkout_url is None
        assert world["org"].billing_plan_key in (None, "")

    def test_opening_the_subscription_checkout_does_not_mark_setup_paid(
            self, client, db_session, world):
        self._ready(db_session, world)
        r, _ = self._create(client, db_session, world, "subscription")
        assert r.status_code == 200, r.text
        db_session.refresh(world["impl"])
        assert world["impl"].setup_payment_status in (None, "not_sent")
        assert world["impl"].setup_checkout_url is None

    # ── the link survives the response ──────────────────────────────────

    def test_the_link_is_recorded_so_nobody_hunts_browser_history(
            self, client, db_session, world):
        """THE UX DEFECT THIS FIXES. The only copy of a customer's payment page
        used to be the HTTP response — recoverable from Chrome history, or by
        generating a second page and giving the customer two ways to pay one
        bill. It is now stored against the obligation it bills."""
        self._ready(db_session, world)
        self._create(client, db_session, world, "setup")
        self._create(client, db_session, world, "subscription")
        db_session.refresh(world["impl"])
        assert world["impl"].setup_checkout_url == _Sess.url
        assert world["impl"].setup_checkout_session_id == _Sess.id
        assert world["impl"].subscription_checkout_url == _Sess.url
        assert world["impl"].subscription_checkout_session_id == _Sess.id

        # And the seller's own screen reads it back without a second call.
        r = client.get("/sales/opportunities/%s/billing" % world["opp"].id,
                       headers=_h(db_session, world["god"]))
        state = r.json()["state"]
        assert state["setup"]["checkout_url"] == _Sess.url
        assert state["subscription"]["checkout_url"] == _Sess.url

    # ── two statuses, never one ─────────────────────────────────────────

    def test_billing_state_reports_the_two_halves_separately(
            self, db_session, world):
        """A single 'paid' flag cannot answer which half arrived."""
        state = deal_billing.billing_state(db_session, world["opp"])
        assert set(state) == {"setup", "subscription"}
        assert state["setup"]["status"] == "not_sent"
        # NULL, not "inactive": no subscription has ever existed, which is not
        # the same as one that stopped.
        assert state["subscription"]["status"] in (None, "")

    def test_a_paid_setup_fee_leaves_the_subscription_untouched(
            self, db_session, world):
        world["impl"].setup_payment_status = "paid"
        world["impl"].setup_paid_cents = 250000
        world["impl"].setup_paid_at = datetime.utcnow()
        db_session.commit()
        state = deal_billing.billing_state(db_session, world["opp"])
        assert state["setup"]["status"] == "paid"
        assert state["setup"]["paid_cents"] == 250000
        assert state["subscription"]["status"] in (None, "")

    def test_an_active_subscription_leaves_the_setup_fee_outstanding(
            self, db_session, world):
        """The state the combined charge could not express."""
        world["org"].billing_status = "active"
        world["org"].stripe_subscription_id = "sub_test_1"
        db_session.commit()
        state = deal_billing.billing_state(db_session, world["opp"])
        assert state["subscription"]["status"] == "active"
        assert state["setup"]["status"] == "not_sent"

    # ── a blocker blocks only what it is about ──────────────────────────

    def test_an_existing_subscription_does_not_block_the_setup_fee(
            self, client, db_session, world):
        """The coupling, moved into readiness, would be the same bug.

        "This customer already has a subscription" is a real reason not to
        start a second one. It is not a reason their implementation fee cannot
        be collected.
        """
        self._ready(db_session, world)
        world["org"].billing_status = "active"
        world["org"].stripe_subscription_id = "sub_test_1"
        db_session.commit()

        terms = deal_billing.terms_for(db_session, world["opp"])
        codes = {b["code"] for b in terms["blockers"]}
        assert deal_billing.B_SUBSCRIPTION_EXISTS in codes
        assert terms["billable_parts"]["setup"] is True
        assert terms["billable_parts"]["subscription"] is False

        r, create = self._create(client, db_session, world, "setup")
        assert r.status_code == 200, r.text
        assert create.call_args.kwargs["mode"] == "payment"

        r2, create2 = self._create(client, db_session, world, "subscription")
        assert r2.status_code == 409
        assert create2.call_count == 0

    def test_an_unscoped_blocker_still_stops_BOTH(self, client, db_session, world):
        """Not-won is about the deal, not about one of its bills. A blocker
        with no declared scope must fail closed."""
        self._ready(db_session, world)
        world["opp"].stage = "closing"
        world["opp"].status = "open"
        db_session.commit()
        terms = deal_billing.terms_for(db_session, world["opp"])
        assert terms["billable_parts"] == {"setup": False, "subscription": False}
        for part in ("setup", "subscription"):
            with patch("stripe.checkout.Session.create") as create:
                r = client.post(
                    "/sales/opportunities/%s/billing/checkout?part=%s"
                    % (world["opp"].id, part),
                    headers=_h(db_session, world["god"]))
            assert r.status_code == 409, part
            assert create.call_count == 0


class TestTheSetupWebhookTouchesSetupOnly:
    """The webhook is the ONLY thing that says money arrived — for one half."""

    def _event(self, world, *, event_id, payment_status="paid",
               amount=250000, pi="pi_test_1", mode="payment"):
        return {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": "cs_test_setup_1",
                "object": "checkout.session",
                "mode": mode,
                "customer": "cus_test123",
                "payment_status": payment_status,
                "payment_intent": pi,
                "amount_total": amount,
                "currency": "usd",
                "metadata": {"org_id": world["org"].id,
                             "opportunity_id": world["opp"].id,
                             "source": "deal_billing",
                             "part": "setup"},
            }},
        }

    def _fire(self, db, event):
        from app.services import billing_webhook
        return billing_webhook.handle_event(db, event)

    def test_a_paid_setup_session_marks_the_setup_fee_paid(
            self, db_session, world):
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        self._fire(db_session, self._event(world, event_id="evt_setup_1"))
        db_session.refresh(world["impl"])
        assert world["impl"].setup_payment_status == "paid"
        assert world["impl"].setup_paid_cents == 250000
        assert world["impl"].setup_payment_intent_id == "pi_test_1"
        assert world["impl"].setup_paid_at is not None

    def test_it_starts_no_subscription_and_stamps_no_plan(
            self, db_session, world):
        """A customer who paid to be implemented has bought exactly ONE thing.

        Inferring a subscription from a setup payment would put them on a plan
        they are not paying for and would make the Billing screen report
        recurring revenue that does not exist.
        """
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        self._fire(db_session, self._event(world, event_id="evt_setup_2"))
        db_session.refresh(world["org"])
        assert world["org"].stripe_subscription_id is None
        assert world["org"].billing_status in (None, "")
        assert world["org"].billing_plan_key in (None, "")

    def test_an_unpaid_completed_session_is_not_a_payment(
            self, db_session, world):
        """"The customer finished the form" is not "the money arrived"."""
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        self._fire(db_session, self._event(world, event_id="evt_setup_3",
                                           payment_status="unpaid"))
        db_session.refresh(world["impl"])
        assert world["impl"].setup_payment_status == "checkout_pending"
        assert world["impl"].setup_paid_at is None

    def test_the_same_payment_arriving_twice_banks_once(
            self, db_session, world):
        """Stripe retries, and a person can re-send an event from the
        dashboard. The event-level dedupe catches the identical event; this
        catches the SAME payment under a different event id."""
        from app.models.billing_models import BillingPayment
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        self._fire(db_session, self._event(world, event_id="evt_setup_4a"))
        self._fire(db_session, self._event(world, event_id="evt_setup_4b"))
        rows = (db_session.query(BillingPayment)
                .filter(BillingPayment.collection_reference == "stripe_pi:pi_test_1")
                .all())
        assert len(rows) == 1
        assert rows[0].amount_cents == 250000
        # A setup fee is neither a first subscription payment nor a renewal,
        # and the reason no commission was earned is stated rather than blank.
        assert rows[0].is_initial is False
        assert rows[0].earned_compensation is False
        assert rows[0].compensation_skipped_reason

    def test_a_subscription_checkout_never_marks_the_setup_fee_paid(
            self, db_session, world):
        """The mirror of the rule above, from the other side."""
        world["org"].stripe_customer_id = "cus_test123"
        db_session.commit()
        ev = self._event(world, event_id="evt_sub_1", mode="subscription")
        ev["data"]["object"]["subscription"] = "sub_test_9"
        ev["data"]["object"]["metadata"]["part"] = "subscription"
        self._fire(db_session, ev)
        db_session.refresh(world["impl"])
        assert world["impl"].setup_payment_status in (None, "not_sent")
        assert world["impl"].setup_paid_at is None
