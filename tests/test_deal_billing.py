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

    # BOTH, because a real Won deal has both — and because the two drift apart
    # on purpose once the customer is provisioned. See
    # test_a_PROVISIONED_customer_is_still_Won.
    opp = Opportunity(company_name="Acme Power", brand_sales_org_id=brand.id,
                      stage="won", status="won", selected_package_id=pkg.id,
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
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
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
        sub = next(c for c in r.json()["charges"] if c["kind"] == "subscription")
        assert sub["approval_id"]

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
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
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
                "/sales/opportunities/%s/billing/checkout" % world["opp"].id,
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
        assert item["price"] == "price_growth_test"
        assert "price_data" not in item
        assert create.call_args.kwargs["metadata"]["plan"] == "growth"


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
        world["opp"].status = "open"
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
