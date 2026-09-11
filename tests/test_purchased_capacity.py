"""CAPACITY SOMEBODY BOUGHT HAS TO ACTUALLY ARRIVE.

A customer who buys "5 additional users" is billed for it every month. If the
guard that refuses the eleventh user has never heard of that purchase, the
platform is taking money for something it then withholds — and nobody finds
out until the customer hits the ceiling they already paid to raise.

So the rules under test are:

  * a live add-on raises the ceiling it names, by its amount times quantity;
  * an unpaid one raises nothing;
  * an unlimited dimension stays unlimited — a purchase never invents a cap;
  * a purchase never raises a limit it does not name;
  * capacity is scoped to the customer who bought it;
  * and a grant keyed to a dimension nothing enforces cannot be configured in
    the first place, because selling one is selling a promise nothing keeps.
"""
import itertools

import pytest

from app.models.billing_models import BillingInterval, BrandBillingPlan
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.models import Organization, Platform
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.services import brand_catalog, plan_limits

_SEQ = itertools.count(1)


@pytest.fixture
def world(db_session):
    n = next(_SEQ)
    platform = Platform(name="EvoSys Pro", slug="plat-cap-%d" % n)
    db_session.add(platform)
    db_session.commit()

    plan = BrandBillingPlan(platform_id=platform.id, key="starter-%d" % n,
                            name="Starter", monthly_cents=49700,
                            currency="usd", is_purchasable=True,
                            is_active=True, max_users=2, max_leads=500)
    org = Organization(name="ZZ Capacity %d" % n, slug="zz-cap-%d" % n,
                       platform_id=platform.id, plan=plan.key,
                       billing_plan_key=plan.key, billing_status="active")
    db_session.add_all([plan, org])
    db_session.commit()
    return {"platform": platform, "plan": plan, "org": org}


def _grant(db, world, *, key="max_users", value=5, status=PurchaseStatus.ACTIVE,
           quantity=1, org=None):
    org = org or world["org"]
    item = BrandCatalogItem(
        platform_id=world["platform"].id, key="pack-%d" % next(_SEQ),
        name="Additional Users", kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.FIXED, amount_cents=2500,
        currency="usd", billing_interval=BillingInterval.MONTH,
        self_service=True, seller_assisted=True, is_active=True,
        entitlement_key=key, entitlement_value=value, sort_order=0)
    db.add(item)
    db.commit()

    purchase = CatalogPurchase(
        organization_id=org.id, platform_id=world["platform"].id,
        catalog_item_id=item.id, item_key=item.key, item_name=item.name,
        kind=item.kind, amount_cents=2500, currency="usd", quantity=quantity,
        status=status, pricing_source=PricingSource.CATALOGUE)
    db.add(purchase)
    db.commit()
    return item, purchase


class TestBoughtCapacityReachesTheGuard:

    def test_the_plan_alone_gives_the_plans_ceiling(self, db_session, world):
        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) == 2

    def test_a_live_addon_raises_the_ceiling(self, db_session, world):
        _grant(db_session, world, key="max_users", value=5)
        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) == 7

    def test_quantity_multiplies_the_raise(self, db_session, world):
        _grant(db_session, world, key="max_users", value=5, quantity=3)
        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) == 17

    def test_an_unpaid_purchase_raises_nothing(self, db_session, world):
        """Granting capacity on the strength of an unpaid link is how a
        platform gives away the thing it is trying to sell."""
        _grant(db_session, world, key="max_users", value=5,
               status=PurchaseStatus.PENDING)
        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) == 2

    def test_a_removed_addon_stops_raising_it(self, db_session, world):
        _item, purchase = _grant(db_session, world, key="max_users", value=5)
        purchase.status = PurchaseStatus.CANCELED
        db_session.commit()

        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) == 2

    def test_it_does_not_raise_a_limit_it_does_not_name(self, db_session,
                                                        world):
        _grant(db_session, world, key="max_users", value=5)
        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_LEADS) == 500

    def test_an_unlimited_dimension_stays_unlimited(self, db_session, world):
        """There is nothing to raise, and a number here would invent a cap out
        of a purchase meant to remove one."""
        world["plan"].max_users = None
        db_session.commit()
        _grant(db_session, world, key="max_users", value=5)

        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) is None

    def test_another_customers_purchase_raises_nothing_here(self, db_session,
                                                            world):
        n = next(_SEQ)
        other = Organization(name="Other %d" % n, slug="other-cap-%d" % n,
                             platform_id=world["platform"].id,
                             plan=world["plan"].key,
                             billing_plan_key=world["plan"].key)
        db_session.add(other)
        db_session.commit()
        _grant(db_session, world, key="max_users", value=5, org=other)

        assert plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS) == 2
        assert plan_limits.limit_for(
            db_session, other, plan_limits.LIMIT_USERS) == 7


class TestTheReportingSurfaceAgreesWithTheGuard:
    """A support question is usually "why can they add seven users on a
    two-user plan". That is only answerable if the tier's own figure is still
    visible next to the purchase that raised it."""

    def test_the_base_ceiling_is_still_reported(self, db_session, world):
        _grant(db_session, world, key="max_users", value=5)
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["limits"]["max_users"] == 2

    def test_what_was_bought_is_reported_separately(self, db_session, world):
        _grant(db_session, world, key="max_users", value=5)
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["purchased_capacity"] == {"max_users": 5}

    def test_the_effective_ceiling_matches_what_is_enforced(self, db_session,
                                                            world):
        _grant(db_session, world, key="max_users", value=5)
        state = plan_limits.entitlement_state(db_session, world["org"])

        assert state["effective_limits"]["max_users"] == plan_limits.limit_for(
            db_session, world["org"], plan_limits.LIMIT_USERS)

    def test_an_unlimited_dimension_is_reported_unlimited(self, db_session,
                                                          world):
        world["plan"].max_users = None
        db_session.commit()
        _grant(db_session, world, key="max_users", value=5)

        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["effective_limits"]["max_users"] is None

    def test_a_customer_who_bought_nothing_reports_nothing(self, db_session,
                                                           world):
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert state["purchased_capacity"] == {}
        assert state["effective_limits"]["max_users"] == 2

    def test_the_explanation_says_capacity_was_purchased(self, db_session,
                                                         world):
        _grant(db_session, world, key="max_users", value=5)
        state = plan_limits.entitlement_state(db_session, world["org"])
        assert "purchased capacity" in state["explanation"].lower()


class TestAGrantNothingEnforcesCannotBeConfigured:
    """An item claiming to grant capacity under a name nothing reads sells
    fine, bills fine, and does nothing."""

    def _validate(self, **kw):
        base = dict(kind=CatalogItemKind.RECURRING_ADDON,
                    pricing_mode=CatalogPricingMode.FIXED,
                    amount_cents=2500,
                    billing_interval=BillingInterval.MONTH,
                    entitlement_key=None, entitlement_value=None)
        base.update(kw)
        return brand_catalog.validate(
            base["kind"], base["pricing_mode"], base["amount_cents"],
            base["billing_interval"], base["entitlement_key"],
            base["entitlement_value"])

    def test_granting_nothing_is_fine(self):
        """Training, a migration, priority support: sold and delivered without
        changing what the software permits."""
        assert self._validate() == []

    def test_an_enforced_dimension_is_accepted(self):
        assert self._validate(entitlement_key="max_users",
                              entitlement_value=5) == []

    def test_an_unenforced_key_is_refused(self):
        problems = self._validate(entitlement_key="sms_credits",
                                  entitlement_value=1000)
        assert problems
        assert "not a limit this platform enforces" in problems[0]

    def test_a_key_with_no_amount_is_refused(self):
        problems = self._validate(entitlement_key="max_users")
        assert problems
        assert "not how much" in problems[0]

    def test_an_amount_with_no_key_is_refused(self):
        problems = self._validate(entitlement_value=5)
        assert problems
        assert "names no limit" in problems[0]

    def test_a_zero_grant_is_refused(self):
        assert self._validate(entitlement_key="max_users",
                              entitlement_value=0)

    def test_a_misconfigured_grant_makes_the_item_unsellable(self, db_session,
                                                             world):
        """Refused at the gate too, not merely at configuration time — an item
        that predates this rule must not keep selling."""
        item = BrandCatalogItem(
            platform_id=world["platform"].id, key="bad-%d" % next(_SEQ),
            name="Mystery Capacity", kind=CatalogItemKind.RECURRING_ADDON,
            pricing_mode=CatalogPricingMode.FIXED, amount_cents=2500,
            currency="usd", billing_interval=BillingInterval.MONTH,
            self_service=True, seller_assisted=True, is_active=True,
            entitlement_key="sms_credits", entitlement_value=1000,
            sort_order=0)
        db_session.add(item)
        db_session.commit()

        assert brand_catalog.is_sellable(item) is False
        with pytest.raises(brand_catalog.ItemNotAvailable) as exc:
            brand_catalog.require_purchasable(
                db_session, world["platform"].id, item.key)
        assert "not a limit this platform enforces" in str(exc.value)
