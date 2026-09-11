"""GOD MODE OWNS THE CATALOGUE — and creating something is not selling it.

The configuration surface for everything a brand sells besides the base
subscription. What these tests mostly guard is the gap between "this row
exists" and "a customer may buy this", because collapsing that gap is how a
half-configured item ends up charging somebody.
"""
import itertools

import pytest
from fastapi import HTTPException

from app.models.billing_models import BillingInterval
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.models import Platform
from app.routers import god_catalog_router as gcr


_SEQ = itertools.count(1)


def _platform(db, name="EvoSys Pro") -> Platform:
    p = Platform(name=name, slug="%s-%d" % (name.lower().replace(" ", "-"),
                                            next(_SEQ)))
    db.add(p)
    db.commit()
    return p


def _god(db):
    """A stand-in for the injected god user. Authority is asserted separately;
    these tests are about what the endpoints DO once past it."""
    class _U:
        id = "god_user"
    return _U()


def _create(db, platform, **kw):
    body = gcr.CatalogItemIn(**{
        "key": kw.pop("key", "extra_users"),
        "name": kw.pop("name", "Additional Users"),
        "kind": kw.pop("kind", CatalogItemKind.RECURRING_ADDON),
        "pricing_mode": kw.pop("pricing_mode", CatalogPricingMode.FIXED),
        "billing_interval": kw.pop("billing_interval", BillingInterval.MONTH),
        **kw,
    })
    return gcr.create_item(platform.id, body, db, _god(db))


class TestCreatingIsNotSelling:

    def test_a_new_item_is_enabled_for_nobody(self, db_session):
        """THE DEFAULT THAT MATTERS. An item exists in the catalogue before
        anybody decides who may buy it, and the safe answer in between is
        nobody."""
        platform = _platform(db_session)
        out = _create(db_session, platform, amount_cents=2500)

        assert out["self_service"] is False
        assert out["seller_assisted"] is False

    def test_a_new_item_may_be_created_without_a_price(self, db_session):
        """Adding something to the catalogue and pricing it are separate
        decisions — and an unpriced item says plainly that it cannot be sold
        rather than looking finished."""
        platform = _platform(db_session)
        out = _create(db_session, platform)

        assert out["amount_cents"] is None
        assert out["sellable"] is False
        assert out["blockers"]

    def test_enabling_is_a_second_deliberate_call(self, db_session):
        platform = _platform(db_session)
        created = _create(db_session, platform, amount_cents=2500)

        out = gcr.update_item(
            platform.id, created["id"],
            gcr.CatalogItemPatch(self_service=True), db_session, _god(db_session))

        assert out["self_service"] is True
        assert out["sellable"] is True


class TestMisconfigurationIsRefused:

    def test_a_one_time_item_with_an_interval_is_refused(self, db_session):
        platform = _platform(db_session)
        with pytest.raises(HTTPException) as exc:
            _create(db_session, platform, key="migration",
                    kind=CatalogItemKind.ONE_TIME,
                    billing_interval=BillingInterval.MONTH,
                    amount_cents=75000)
        assert exc.value.status_code == 422
        assert "subscription" in str(exc.value.detail).lower()

    def test_a_quoted_item_with_a_price_is_refused(self, db_session):
        platform = _platform(db_session)
        with pytest.raises(HTTPException) as exc:
            _create(db_session, platform, key="custom_dev",
                    kind=CatalogItemKind.ONE_TIME, billing_interval=None,
                    pricing_mode=CatalogPricingMode.QUOTED, amount_cents=50000)
        assert exc.value.status_code == 422

    def test_a_duplicate_key_within_a_brand_is_refused(self, db_session):
        """Keys are how every other surface refers to an item."""
        platform = _platform(db_session)
        _create(db_session, platform, amount_cents=2500)

        with pytest.raises(HTTPException) as exc:
            _create(db_session, platform, amount_cents=9900)
        assert exc.value.status_code == 409

    def test_the_same_key_on_another_brand_is_fine(self, db_session):
        """Two brands both selling 'extra_users' are selling two different
        things at two different prices."""
        a, b = _platform(db_session, "Brand A"), _platform(db_session, "Brand B")
        _create(db_session, a, amount_cents=2500)
        out = _create(db_session, b, amount_cents=9900)

        assert out["amount_cents"] == 9900

    def test_an_unknown_brand_is_a_404(self, db_session):
        with pytest.raises(HTTPException) as exc:
            gcr.list_items("no-such-brand", None, True, db_session,
                           _god(db_session))
        assert exc.value.status_code == 404


class TestEditingRevalidatesTheWholeRow:

    def test_changing_kind_without_clearing_the_interval_is_refused(
            self, db_session):
        """THE REASON THE WHOLE ROW IS RE-VALIDATED. Validating only the
        edited field would leave a one-time item billing every month."""
        platform = _platform(db_session)
        created = _create(db_session, platform, amount_cents=2500)

        with pytest.raises(HTTPException) as exc:
            gcr.update_item(platform.id, created["id"],
                            gcr.CatalogItemPatch(kind=CatalogItemKind.ONE_TIME),
                            db_session, _god(db_session))
        assert exc.value.status_code == 422

    def test_changing_kind_and_clearing_the_interval_together_works(
            self, db_session):
        platform = _platform(db_session)
        created = _create(db_session, platform, amount_cents=2500)

        out = gcr.update_item(
            platform.id, created["id"],
            gcr.CatalogItemPatch(kind=CatalogItemKind.ONE_TIME,
                                 billing_interval=None),
            db_session, _god(db_session))

        assert out["kind"] == CatalogItemKind.ONE_TIME
        assert out["billing_interval"] is None

    def test_an_item_from_another_brand_cannot_be_edited(self, db_session):
        a, b = _platform(db_session, "Brand A"), _platform(db_session, "Brand B")
        created = _create(db_session, a, amount_cents=2500)

        with pytest.raises(HTTPException) as exc:
            gcr.update_item(b.id, created["id"],
                            gcr.CatalogItemPatch(amount_cents=1),
                            db_session, _god(db_session))
        assert exc.value.status_code == 404


class TestTheListingIsAConfigurationScreen:

    def test_inactive_rows_are_included_by_default(self, db_session):
        """The item somebody disabled and forgot is exactly what an operator
        came here to find."""
        platform = _platform(db_session)
        created = _create(db_session, platform, amount_cents=2500)
        gcr.update_item(platform.id, created["id"],
                        gcr.CatalogItemPatch(is_active=False),
                        db_session, _god(db_session))

        out = gcr.list_items(platform.id, None, True, db_session, _god(db_session))
        assert len(out["items"]) == 1
        assert out["items"][0]["is_active"] is False

    def test_the_counts_separate_existing_from_sellable(self, db_session):
        platform = _platform(db_session)
        _create(db_session, platform, key="priced", amount_cents=2500)
        _create(db_session, platform, key="unpriced")

        out = gcr.list_items(platform.id, None, True, db_session, _god(db_session))

        assert out["counts"]["total"] == 2
        assert out["counts"]["sellable"] == 1
        # Neither is enabled for an audience yet, priced or not.
        assert out["counts"]["self_service"] == 0
        assert out["counts"]["blocked"] == 1

    def test_a_brand_sees_only_its_own_items(self, db_session):
        a, b = _platform(db_session, "Brand A"), _platform(db_session, "Brand B")
        _create(db_session, a, amount_cents=2500)

        assert gcr.list_items(b.id, None, True, db_session,
                              _god(db_session))["items"] == []
