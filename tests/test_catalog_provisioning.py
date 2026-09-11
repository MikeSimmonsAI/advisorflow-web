"""STRIPE OBJECTS FOR THE CATALOGUE — and the one line that matters most.

A Price created with a `recurring` block bills every period. A Price created
without one bills once. That single difference is all that stands between a
$750 data migration and $750 every month for ever, and the customer would be
right to call the second one theft rather than a bug.

So the tests that matter here are not "did it create a Price". They are:

  * a one-time item's Price has NO recurring block, ever;
  * a recurring item's Price has the interval the catalogue says;
  * a Price left over from a different shape is NOT reused after the item's
    kind changes;
  * quoted and unpriced items are SKIPPED with a reason rather than failing
    the run or, far worse, being minted at some invented figure.
"""
import pytest

from app.models.billing_models import BillingInterval
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.services import brand_catalog, catalog_provisioning


class _Platform:
    id = "plat_1"
    name = "EvoSys Pro"


def _item(**kw) -> BrandCatalogItem:
    base = dict(
        platform_id="plat_1", key="extra_users", name="Additional Users",
        kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.FIXED,
        amount_cents=2500, currency="usd",
        billing_interval=BillingInterval.MONTH,
        stripe_product_id=None, stripe_price_id=None,
        self_service=False, seller_assisted=False, is_active=True,
        customer_description=None, internal_description=None,
        entitlement_key=None, entitlement_value=None,
        category=None, sort_order=0,
    )
    base.update(kw)
    return BrandCatalogItem(**base)


class _FakeStripe:
    """Records what would be created. Search and list return nothing, so every
    run takes the create path unless a test says otherwise."""

    def __init__(self, existing_prices=None):
        self.created_prices = []
        self.created_products = []
        self._existing = existing_prices or []

        outer = self

        class Product:
            @staticmethod
            def retrieve(pid):
                return {"id": pid, "deleted": False}

            @staticmethod
            def search(**kw):
                return {"data": []}

            @staticmethod
            def create(**kw):
                outer.created_products.append(kw)
                return {"id": "prod_new"}

        class Price:
            @staticmethod
            def retrieve(pid):
                for p in outer._existing:
                    if p["id"] == pid:
                        return p
                raise RuntimeError("no such price")

            @staticmethod
            def list(**kw):
                return {"data": outer._existing}

            @staticmethod
            def create(**kw):
                outer.created_prices.append(kw)
                return {"id": "price_new"}

        self.Product = Product
        self.Price = Price


@pytest.fixture
def provision(monkeypatch):
    """Run provisioning against a fake Stripe and a fixed item list."""
    def _run(items, existing_prices=None, dry_run=False):
        fake = _FakeStripe(existing_prices)
        monkeypatch.setattr(catalog_provisioning, "_client", lambda: fake)
        monkeypatch.setattr(brand_catalog, "items_for",
                            lambda db, pid, kind=None, active_only=True: items)

        class _Db:
            def commit(self):
                pass

        result = catalog_provisioning.provision_brand_catalog(
            _Db(), _Platform(), dry_run=dry_run)
        return result, fake
    return _run


class TestAOneTimeChargeStaysOneTime:

    def test_a_one_time_price_has_no_recurring_block(self, provision):
        """THE LINE THAT MATTERS. An interval here bills a migration monthly."""
        item = _item(key="migration", name="Data Migration",
                     kind=CatalogItemKind.ONE_TIME, billing_interval=None,
                     amount_cents=75000)
        _result, fake = provision([item])

        assert len(fake.created_prices) == 1
        assert "recurring" not in fake.created_prices[0], (
            "a one-time item was given a recurring Price — that is a single "
            "charge turned into a subscription")
        assert fake.created_prices[0]["unit_amount"] == 75000

    def test_a_recurring_price_carries_the_configured_interval(self, provision):
        _result, fake = provision([_item()])

        assert fake.created_prices[0]["recurring"] == {
            "interval": "month", "interval_count": 1}

    def test_a_one_time_item_does_not_match_a_recurring_price(self):
        """So a Price from before the kind changed cannot be reused."""
        item = _item(kind=CatalogItemKind.ONE_TIME, billing_interval=None)
        recurring_price = {"id": "price_old", "active": True,
                           "unit_amount": 2500, "currency": "usd",
                           "recurring": {"interval": "month",
                                         "interval_count": 1}}
        assert catalog_provisioning._price_matches(recurring_price, item) is False

    def test_a_recurring_item_does_not_match_a_one_time_price(self):
        one_time_price = {"id": "price_old", "active": True,
                          "unit_amount": 2500, "currency": "usd",
                          "recurring": None}
        assert catalog_provisioning._price_matches(
            one_time_price, _item()) is False

    def test_an_archived_price_is_never_reused(self):
        """Reusing an inactive Price produces a checkout that fails at the
        till — the worst place to discover a configuration problem."""
        price = {"id": "price_old", "active": False, "unit_amount": 2500,
                 "currency": "usd",
                 "recurring": {"interval": "month", "interval_count": 1}}
        assert catalog_provisioning._price_matches(price, _item()) is False


class TestNothingIsInvented:

    def test_a_quoted_item_is_skipped_with_a_reason(self, provision):
        item = _item(key="custom_dev", kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None,
                     pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None)
        result, fake = provision([item])

        assert fake.created_prices == []
        row = result["items"][0]
        assert row["skipped"] is True
        assert "deal" in row["reason"].lower()

    def test_an_unpriced_item_is_skipped_rather_than_minted_free(self, provision):
        item = _item(amount_cents=None)
        result, fake = provision([item])

        assert fake.created_prices == []
        assert result["items"][0]["skipped"] is True
        assert "not free" in result["items"][0]["reason"].lower()

    def test_a_misconfigured_item_is_skipped_not_created(self, provision):
        """A one-time item that still carries an interval must not reach
        Stripe at all."""
        item = _item(kind=CatalogItemKind.ONE_TIME,
                     billing_interval=BillingInterval.MONTH)
        result, fake = provision([item])

        assert fake.created_prices == []
        assert result["items"][0]["skipped"] is True

    def test_skipping_is_not_failing(self, provision):
        """Treating a quoted item as an error would teach operators to ignore
        this report — which is where the real failures appear."""
        result, _fake = provision([
            _item(key="a", pricing_mode=CatalogPricingMode.QUOTED,
                  amount_cents=None),
            _item(key="b", amount_cents=2500),
        ])
        assert result["summary"]["skipped"] == 1
        assert result["summary"]["prices_created"] == 1


class TestItIsIdempotentAndSafeToRepeat:

    def test_a_matching_existing_price_is_reused(self, provision):
        existing = [{"id": "price_existing", "active": True,
                     "unit_amount": 2500, "currency": "usd",
                     "recurring": {"interval": "month", "interval_count": 1}}]
        result, fake = provision([_item()], existing_prices=existing)

        assert fake.created_prices == []
        assert result["items"][0]["price"]["id"] == "price_existing"
        assert result["summary"]["reused"] == 1

    def test_a_dry_run_creates_nothing(self, provision):
        result, fake = provision([_item()], dry_run=True)

        assert fake.created_prices == []
        assert fake.created_products == []
        assert result["dry_run"] is True

    def test_a_dry_run_does_not_write_the_mapping_back(self, provision):
        item = _item()
        provision([item], dry_run=True)
        assert item.stripe_price_id is None

    def test_applying_writes_the_mapping_back(self, provision):
        item = _item()
        provision([item])
        assert item.stripe_price_id == "price_new"
        assert item.stripe_product_id == "prod_new"


class TestWhatReachesStripe:

    def test_the_product_carries_the_customer_description_not_the_internal(
            self, provision):
        """The internal note is written for the team and may say what the work
        really costs us. It must not appear on an invoice."""
        item = _item(customer_description="Adds 5 user seats",
                     internal_description="3h of setup; route to Ops")
        _result, fake = provision([item])

        blob = repr(fake.created_products)
        assert "Adds 5 user seats" in blob
        assert "route to Ops" not in blob

    def test_the_metadata_cannot_collide_with_a_tier(self, provision):
        """A catalogue item and a subscription tier must never match each
        other's search, or provisioning one could reuse the other's Product."""
        md = catalog_provisioning._item_metadata(_item())
        assert "catalog_key" in md
        assert "plan_key" not in md


class TestThePreviewCountsWhatItWouldCreate:
    """FOUND ON A LIVE SCREEN. Two brand-new items, both listed as "would
    create", under a header reading "Would create 0 product(s) and 0 price(s)".

    The summary is what an operator actually reads, and a summary saying
    nothing will happen is the one thing that stops them reading the rows that
    say otherwise.
    """

    def test_a_dry_run_counts_the_items_it_would_create(self, provision):
        items = [_item(key="a"),
                 _item(key="b", kind=CatalogItemKind.ONE_TIME,
                       billing_interval=None)]
        result, _fake = provision(items, dry_run=True)

        would = [r for r in result["items"]
                 if not r.get("skipped")
                 and r["price"]["action"] == "would_create"]
        assert len(would) == 2
        assert result["summary"]["prices_created"] == 2
        assert result["summary"]["products_created"] == 2

    def test_the_summary_agrees_with_the_rows(self, provision):
        items = [_item(key="a"), _item(key="unpriced", amount_cents=None)]
        result, _fake = provision(items, dry_run=True)

        assert result["summary"]["skipped"] == 1
        assert result["summary"]["considered"] == len(result["items"]) == 2

    def test_a_dry_run_still_creates_nothing_at_stripe(self, provision):
        result, fake = provision([_item()], dry_run=True)

        assert result["dry_run"] is True
        assert fake.created_prices == []
        assert fake.created_products == []

    def test_an_unenforceable_grant_is_skipped_rather_than_sold(self,
                                                               provision):
        """An item claiming capacity nothing enforces would bill fine and do
        nothing. It is not given a Stripe price."""
        item = _item(key="mystery", entitlement_key="sms_credits",
                     entitlement_value=1000)
        result, fake = provision([item])

        row = result["items"][0]
        assert row["skipped"] is True
        assert "not a limit this platform enforces" in row["reason"]
        assert fake.created_prices == []
