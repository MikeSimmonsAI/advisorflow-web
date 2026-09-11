"""ADD-ONS ARE REVENUE, AND THE REVENUE SCREENS HAVE TO SAY SO.

Every figure in God billing described the plan and only the plan, which was
complete for exactly as long as the plan was the only recurring thing a
customer could be charged for. A Growth customer paying $997 plus $250 of
add-ons read as $997 — understated by a quarter, on the screen finance totals
from.

What this file defends:

  * live monthly add-ons are counted, at quantity;
  * an unpaid or removed one is not;
  * the PLAN figure stays the plan figure — the two are reported apart, so a
    tier total and a revenue total never have to be the same number;
  * a customer whose plan cannot be priced contributes no add-on revenue
    either, or the total would silently cover a customer the unpriced list
    still names as missing.
"""
import itertools

import pytest

from app.models.billing_models import (BillingInterval, BrandBillingPlan,
                                       SubscriptionStatus)
from app.models.catalog_models import CatalogItemKind
from app.models.models import Organization, Platform, User
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture
def world(db_session):
    n = next(_SEQ)
    platform = Platform(name="EvoSys Pro", slug="plat-rev-%d" % n)
    db_session.add(platform)
    db_session.commit()

    plan = BrandBillingPlan(platform_id=platform.id, key="growth-%d" % n,
                            name="Growth", monthly_cents=99700,
                            currency="usd", is_purchasable=True,
                            is_active=True)
    db_session.add(plan)
    db_session.commit()

    org = Organization(name="ZZ Revenue %d" % n, slug="zz-rev-%d" % n,
                       platform_id=platform.id, plan=plan.key,
                       billing_plan_key=plan.key,
                       billing_status=SubscriptionStatus.ACTIVE,
                       stripe_customer_id="cus_rev_%d" % n,
                       stripe_subscription_id="sub_rev_%d" % n)
    db_session.add(org)
    db_session.commit()
    return {"platform": platform, "plan": plan, "org": org}


def _god_headers(db):
    u = User(organization_id=None, email="god%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("GodPass123!"), full_name="God",
             role="god_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _addon(db, world, *, cents=2500, quantity=1,
           status=PurchaseStatus.ACTIVE, interval=BillingInterval.MONTH,
           kind=CatalogItemKind.RECURRING_ADDON):
    p = CatalogPurchase(
        organization_id=world["org"].id, platform_id=world["platform"].id,
        item_key="pack-%d" % next(_SEQ), item_name="Additional Users",
        kind=kind, amount_cents=cents, currency="usd", quantity=quantity,
        billing_interval=interval, status=status,
        pricing_source=PricingSource.CATALOGUE)
    db.add(p)
    db.commit()
    return p


def _row(client, db, world):
    r = client.get("/god/billing/customers?platform_id=%s"
                   % world["platform"].id, headers=_god_headers(db))
    assert r.status_code == 200
    body = r.json()
    rows = [c for c in body["customers"]
            if c["organization_id"] == world["org"].id]
    assert rows, "the customer should be on the roster"
    return rows[0], body


class TestTheCustomerRow:

    def test_a_customer_with_no_addons_totals_their_plan(self, client,
                                                         db_session, world):
        row, _ = _row(client, db_session, world)
        assert row["mrr_cents"] == 99700
        assert row["addons_mrr_cents"] == 0
        assert row["total_mrr_cents"] == 99700

    def test_a_live_addon_is_counted(self, client, db_session, world):
        _addon(db_session, world, cents=25000)
        row, _ = _row(client, db_session, world)

        assert row["addons_mrr_cents"] == 25000
        assert row["total_mrr_cents"] == 124700

    def test_the_plan_figure_is_untouched_by_it(self, client, db_session,
                                                world):
        """A Growth customer must not show an MRR no Growth price explains."""
        _addon(db_session, world, cents=25000)
        row, _ = _row(client, db_session, world)
        assert row["mrr_cents"] == 99700

    def test_quantity_is_counted(self, client, db_session, world):
        _addon(db_session, world, cents=2500, quantity=4)
        row, _ = _row(client, db_session, world)
        assert row["addons_mrr_cents"] == 10000

    def test_an_unpaid_purchase_is_not_revenue(self, client, db_session,
                                               world):
        _addon(db_session, world, cents=25000,
               status=PurchaseStatus.PENDING)
        row, _ = _row(client, db_session, world)
        assert row["addons_mrr_cents"] == 0

    def test_a_removed_addon_is_not_revenue(self, client, db_session, world):
        _addon(db_session, world, cents=25000,
               status=PurchaseStatus.CANCELED)
        row, _ = _row(client, db_session, world)
        assert row["addons_mrr_cents"] == 0

    def test_a_one_time_purchase_is_not_recurring_revenue(self, client,
                                                          db_session, world):
        """A migration is money, but it is not MONTHLY money."""
        _addon(db_session, world, cents=75000, kind=CatalogItemKind.ONE_TIME,
               interval=None, status=PurchaseStatus.PAID)
        row, _ = _row(client, db_session, world)
        assert row["addons_mrr_cents"] == 0

    def test_a_yearly_addon_is_excluded_rather_than_guessed(self, client,
                                                            db_session, world):
        """Counting it at face value would overstate MRR twelvefold, and no
        conversion has been decided."""
        _addon(db_session, world, cents=120000,
               interval=BillingInterval.YEAR)
        row, _ = _row(client, db_session, world)
        assert row["addons_mrr_cents"] == 0

    def test_an_unpriceable_plan_has_no_total(self, client, db_session, world):
        """"$250 of add-ons on a plan we cannot explain" must not render as a
        $250 customer."""
        world["org"].billing_plan_key = "no-such-tier"
        world["org"].plan = "no-such-tier"
        db_session.commit()
        _addon(db_session, world, cents=25000)

        row, _ = _row(client, db_session, world)
        assert row["mrr_cents"] is None
        assert row["total_mrr_cents"] is None
        assert row["mrr_unavailable_reason"]


class TestTheVisibleTotal:

    def test_addons_reach_the_total(self, client, db_session, world):
        _addon(db_session, world, cents=25000)
        _row_, body = _row(client, db_session, world)

        assert body["visible_addons_mrr_cents"] == 25000
        assert (body["visible_mrr_cents"]
                == body["visible_plan_mrr_cents"] + 25000)

    def test_the_plan_total_stays_the_plan_total(self, client, db_session,
                                                 world):
        _addon(db_session, world, cents=25000)
        _row_, body = _row(client, db_session, world)
        assert body["visible_plan_mrr_cents"] == 99700

    def test_addons_on_an_unpriceable_plan_are_left_out(self, client,
                                                        db_session, world):
        world["org"].billing_plan_key = "no-such-tier"
        world["org"].plan = "no-such-tier"
        db_session.commit()
        _addon(db_session, world, cents=25000)

        _row_, body = _row(client, db_session, world)
        assert body["visible_addons_mrr_cents"] == 0
