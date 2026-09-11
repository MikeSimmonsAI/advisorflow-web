"""THE BRAND CATALOGUE — what a brand sells besides the subscription itself.

Recurring add-ons, one-time products and services, and quoted work. God Mode
owns the configuration; the seller and customer surfaces consume it.

Most of what is worth testing here is REFUSAL. A catalogue row charges people
money, so the failure that matters is not "the listing did not render" — it is
an item being sellable when nobody decided it should be, at a price nobody
entered, in a shape that turns a single charge into a subscription.
"""
import pytest

from app.models.billing_models import BillingInterval
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.services import brand_catalog


def _item(**kw) -> BrandCatalogItem:
    """A well-formed fixed-price recurring add-on unless told otherwise."""
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


# ═══════════════════════════════════════════════════════════════════════════
# SHAPE — a one-time charge must stay one-time
# ═══════════════════════════════════════════════════════════════════════════

class TestTheKindDecidesTheShape:

    def test_a_recurring_addon_needs_an_interval(self):
        problems = brand_catalog.validate(
            CatalogItemKind.RECURRING_ADDON, CatalogPricingMode.FIXED,
            2500, None)
        assert problems and "interval" in problems[0].lower()

    def test_a_one_time_item_must_not_have_an_interval(self):
        """THE ONE THAT MATTERS MOST. An interval on a one-time service turns a
        $750 migration into $750 every month."""
        problems = brand_catalog.validate(
            CatalogItemKind.ONE_TIME, CatalogPricingMode.FIXED,
            75000, BillingInterval.MONTH)
        assert problems
        assert "subscription" in problems[0].lower()

    def test_a_well_formed_item_of_each_kind_validates(self):
        assert brand_catalog.validate(
            CatalogItemKind.RECURRING_ADDON, CatalogPricingMode.FIXED,
            2500, BillingInterval.MONTH) == []
        assert brand_catalog.validate(
            CatalogItemKind.ONE_TIME, CatalogPricingMode.FIXED,
            75000, None) == []

    def test_an_unknown_kind_is_refused(self):
        assert brand_catalog.validate(
            "subscription_maybe", CatalogPricingMode.FIXED, 100, None)

    def test_every_problem_is_reported_at_once(self):
        """An operator should not discover the faults one save at a time."""
        problems = brand_catalog.validate(
            "nonsense", "invented", -5, BillingInterval.MONTH)
        assert len(problems) >= 3


# ═══════════════════════════════════════════════════════════════════════════
# PRICE — unpriced is not free
# ═══════════════════════════════════════════════════════════════════════════

class TestNotPricedIsNotSellable:

    def test_a_fixed_item_with_no_amount_is_not_sellable(self):
        assert brand_catalog.is_sellable(_item(amount_cents=None)) is False

    def test_zero_is_a_real_price_and_is_allowed(self):
        """Refusing NULL and allowing 0 is the distinction. A brand may
        genuinely include something at no charge; what it may not do is charge
        nothing because nobody filled the field in."""
        assert brand_catalog.validate(
            CatalogItemKind.ONE_TIME, CatalogPricingMode.FIXED, 0, None) == []
        assert brand_catalog.is_sellable(
            _item(kind=CatalogItemKind.ONE_TIME, billing_interval=None,
                  amount_cents=0)) is True

    def test_a_negative_price_is_refused(self):
        assert brand_catalog.validate(
            CatalogItemKind.ONE_TIME, CatalogPricingMode.FIXED, -1, None)

    def test_a_quoted_item_must_not_carry_a_catalogue_price(self):
        """A 'suggested' price on a quoted item is a price that gets quoted."""
        problems = brand_catalog.validate(
            CatalogItemKind.ONE_TIME, CatalogPricingMode.QUOTED, 50000, None)
        assert problems and "quoted" in problems[0].lower()

    def test_a_quoted_item_without_an_amount_is_sellable(self):
        """That is the point of it — the amount comes from the deal."""
        assert brand_catalog.is_sellable(
            _item(kind=CatalogItemKind.ONE_TIME, billing_interval=None,
                  pricing_mode=CatalogPricingMode.QUOTED,
                  amount_cents=None)) is True

    def test_an_inactive_item_is_never_sellable(self):
        assert brand_catalog.is_sellable(_item(is_active=False)) is False


# ═══════════════════════════════════════════════════════════════════════════
# THE GATES — nobody may buy anything until somebody says so
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def gate(monkeypatch):
    """Both gates, with a single stubbed item behind them."""
    holder = {}

    def _resolve(db, platform_id, key):
        item = holder.get("item")
        if item is None or platform_id != item.platform_id or key != item.key:
            return None
        return item

    monkeypatch.setattr(brand_catalog, "resolve", _resolve)

    def _install(item):
        holder["item"] = item
        return brand_catalog
    return _install


class TestSelfServeRefusesByDefault:

    def test_a_new_item_cannot_be_bought_by_a_customer(self, gate):
        """Created off. The cost of an item nobody enabled is one
        configuration step; the cost of one enabled by default is a customer
        buying something the brand never decided to sell."""
        bc = gate(_item())
        with pytest.raises(bc.ItemNotAvailable) as exc:
            bc.require_purchasable(None, "plat_1", "extra_users")
        assert "self-service" in str(exc.value).lower()

    def test_an_enabled_priced_item_can_be_bought(self, gate):
        bc = gate(_item(self_service=True))
        assert bc.require_purchasable(None, "plat_1", "extra_users") is not None

    def test_an_unpriced_item_is_refused_with_a_reason(self, gate):
        bc = gate(_item(self_service=True, amount_cents=None))
        with pytest.raises(bc.ItemNotAvailable) as exc:
            bc.require_purchasable(None, "plat_1", "extra_users")
        assert "not free" in str(exc.value).lower()

    def test_a_quoted_item_cannot_be_bought_directly(self, gate):
        """There is nothing for a customer to click: the amount does not
        exist until somebody prices the deal."""
        bc = gate(_item(self_service=True,
                        pricing_mode=CatalogPricingMode.QUOTED,
                        amount_cents=None))
        with pytest.raises(bc.ItemNotAvailable) as exc:
            bc.require_purchasable(None, "plat_1", "extra_users")
        assert "quoted" in str(exc.value).lower()

    def test_another_brands_item_does_not_exist(self, gate):
        """Brand scoping is a defence, not an optimisation: one brand's
        customer cannot buy — or discover — another brand's catalogue."""
        bc = gate(_item(self_service=True))
        with pytest.raises(bc.ItemNotAvailable):
            bc.require_purchasable(None, "plat_other", "extra_users")


class TestTheSellerGateIsWiderAndStillAGate:

    def test_a_seller_may_sell_a_quoted_item(self, gate):
        """Pricing it is what the pricing-authority machinery is for."""
        bc = gate(_item(seller_assisted=True,
                        pricing_mode=CatalogPricingMode.QUOTED,
                        amount_cents=None))
        assert bc.require_seller_sellable(None, "plat_1", "extra_users")

    def test_a_seller_may_not_sell_an_item_not_enabled_for_them(self, gate):
        bc = gate(_item(self_service=True, seller_assisted=False))
        with pytest.raises(bc.ItemNotAvailable) as exc:
            bc.require_seller_sellable(None, "plat_1", "extra_users")
        assert "seller-assisted" in str(exc.value).lower()

    def test_a_seller_may_not_sell_an_inactive_item(self, gate):
        bc = gate(_item(seller_assisted=True, is_active=False))
        with pytest.raises(bc.ItemNotAvailable):
            bc.require_seller_sellable(None, "plat_1", "extra_users")

    def test_the_two_audiences_are_independent(self, gate):
        """A service a seller scopes on a call is not automatically a thing to
        put behind a self-serve button, and the reverse."""
        bc = gate(_item(self_service=True, seller_assisted=False))
        assert bc.require_purchasable(None, "plat_1", "extra_users")
        with pytest.raises(bc.ItemNotAvailable):
            bc.require_seller_sellable(None, "plat_1", "extra_users")


class TestWhatEachAudienceIsShown:

    def test_the_customer_payload_carries_no_stripe_id(self):
        out = brand_catalog.public_out(
            _item(stripe_product_id="prod_x", stripe_price_id="price_x"))
        blob = repr(out)
        assert "prod_x" not in blob and "price_x" not in blob
        assert "stripe" not in blob.lower()

    def test_the_customer_payload_withholds_the_internal_note(self):
        """Written for the team — delivery caveats, what it really costs us."""
        out = brand_catalog.public_out(
            _item(internal_description="route to Ops; 3h of real work"))
        assert "internal_description" not in out
        assert "Ops" not in repr(out)

    def test_a_quoted_item_is_flagged_so_the_screen_says_contact_us(self):
        out = brand_catalog.public_out(
            _item(pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None))
        assert out["is_quoted"] is True
        assert out["amount_cents"] is None

    def test_the_admin_payload_names_what_blocks_a_row(self):
        """An item that looks configured but is not sellable is the failure an
        operator cannot see from the columns alone."""
        out = brand_catalog.admin_out(_item(amount_cents=None))
        assert out["sellable"] is False
        assert out["blockers"]
        assert "not free" in " ".join(out["blockers"]).lower()

    def test_the_admin_payload_flags_a_missing_stripe_mapping(self):
        out = brand_catalog.admin_out(_item())
        assert out["needs_stripe_mapping"] is True
        assert brand_catalog.admin_out(
            _item(stripe_price_id="price_x"))["needs_stripe_mapping"] is False

    def test_a_quoted_item_does_not_need_a_stripe_price(self):
        """Priced per deal — there is nothing to map in advance."""
        out = brand_catalog.admin_out(
            _item(pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None))
        assert out["needs_stripe_mapping"] is False
