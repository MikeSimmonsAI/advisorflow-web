"""
THE PROVISIONING ENDPOINT'S GUARANTEES.

Stripe is faked at the module boundary. That is deliberate and not a shortcut:
these tests are about the RULES this module enforces — test-mode only, reuse
before create, the catalogue as the only source of amounts — and every one of
them must hold without a network, on a laptop with no credentials, in CI.
The real objects are proved against Stripe once, by hand, in the live run.
"""

import os

import pytest

from app.models.billing_models import BrandBillingPlan
from app.models.models import Platform
from app.services import stripe_provisioning as sp


# ── a Stripe that records what it was asked to do ───────────────────────────

class FakeStripe:
    def __init__(self, products=None, prices=None, search_raises=False):
        self.api_key = None
        self._products = products or {}
        self._prices = prices or {}
        self.created_products = []
        self.created_prices = []
        self._search_raises = search_raises
        outer = self

        class Product:
            @staticmethod
            def retrieve(pid):
                if pid in outer._products:
                    return outer._products[pid]
                raise Exception("No such product")

            @staticmethod
            def search(query=None, limit=None):
                if outer._search_raises:
                    raise Exception("search unavailable on this API version")
                hits = [p for p in outer._products.values()
                        if query and p.get("metadata", {}).get("plan_key")
                        and ("'%s'" % p["metadata"]["plan_key"]) in query
                        and ("'%s'" % p["metadata"]["platform_id"]) in query]
                return {"data": hits[:limit or 10]}

            @staticmethod
            def create(**kw):
                pid = "prod_fake_%d" % (len(outer.created_products) + 1)
                obj = {"id": pid, "deleted": False, **kw}
                outer._products[pid] = obj
                outer.created_products.append(obj)
                return obj

        class Price:
            @staticmethod
            def retrieve(pid):
                if pid in outer._prices:
                    return outer._prices[pid]
                raise Exception("No such price")

            @staticmethod
            def list(product=None, active=None, limit=None):
                return {"data": [p for p in outer._prices.values()
                                 if p.get("product") == product]}

            @staticmethod
            def create(**kw):
                pid = "price_fake_%d" % (len(outer.created_prices) + 1)
                obj = {"id": pid, "active": True, **kw}
                outer._prices[pid] = obj
                outer.created_prices.append(obj)
                return obj

        self.Product = Product
        self.Price = Price


def _price_obj(pid, product, cents, currency="usd", interval="month",
               active=True, commitment=None):
    return {"id": pid, "product": product, "unit_amount": cents,
            "currency": currency, "active": active,
            "recurring": {"interval": interval, "interval_count": 1},
            "metadata": {"commitment": commitment} if commitment else {}}


@pytest.fixture
def fake(monkeypatch):
    st = FakeStripe()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_pretend")
    monkeypatch.setattr(sp, "_client", lambda: st)
    return st


@pytest.fixture
def brand(db_session):
    plat = Platform(id="plt-test-prov", name="TestBrand", slug="testbrand")
    db_session.add(plat)
    db_session.add(BrandBillingPlan(
        platform_id=plat.id, key="starter", name="Starter", sort_order=10,
        monthly_cents=50000, month_to_month_cents=59700, currency="usd",
        is_purchasable=True, is_active=True))
    db_session.add(BrandBillingPlan(
        platform_id=plat.id, key="enterprise", name="Enterprise", sort_order=99,
        monthly_cents=None, month_to_month_cents=None, currency="usd",
        is_purchasable=False, is_active=True))
    db_session.commit()
    return plat


# ── test mode is enforced on the KEY ────────────────────────────────────────

class TestNeverTouchesLiveMoney:

    def test_a_live_key_is_refused_before_any_stripe_call(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_realmoney")
        with pytest.raises(sp.ProvisioningRefused) as exc:
            sp._client()
        assert "LIVE" in str(exc.value)

    def test_a_live_restricted_key_is_refused_too(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "rk_live_realmoney")
        with pytest.raises(sp.ProvisioningRefused):
            sp._client()

    def test_an_unrecognisable_key_is_refused_rather_than_guessed(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "something_else_entirely")
        with pytest.raises(sp.ProvisioningRefused):
            sp._client()

    def test_a_missing_key_is_refused_with_a_usable_message(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with pytest.raises(sp.ProvisioningRefused) as exc:
            sp._client()
        assert "STRIPE_SECRET_KEY" in str(exc.value)

    def test_the_refusal_never_contains_the_key(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_SECRETVALUE123")
        with pytest.raises(sp.ProvisioningRefused) as exc:
            sp._client()
        assert "SECRETVALUE123" not in str(exc.value)


# ── the catalogue is the only source of money ───────────────────────────────

class TestAmountsComeFromTheCatalogue:

    def test_creates_one_price_per_configured_commitment(self, db_session, brand, fake):
        out = sp.provision_brand(db_session, brand)
        starter = [p for p in out["plans"] if p["plan_key"] == "starter"][0]
        assert starter["commitments"]["term"]["cents"] == 50000
        assert starter["commitments"]["month_to_month"]["cents"] == 59700
        assert len(fake.created_prices) == 2

    def test_the_created_price_carries_the_catalogue_amount(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand)
        amounts = sorted(p["unit_amount"] for p in fake.created_prices)
        assert amounts == [50000, 59700]

    def test_no_amount_is_hard_coded_in_the_module(self):
        """A figure in this file would be a second opinion on what a brand charges.

        CODE ONLY. Docstrings and comments are stripped first, because the
        explanation of why an amount must not appear here necessarily contains
        one — and a guard that fails on its own rationale teaches people to
        delete the rationale.
        """
        import inspect
        import re
        src = inspect.getsource(sp)
        src = re.sub(r'"""[\s\S]*?"""', ' ', src)
        src = re.sub(r"'''[\s\S]*?'''", ' ', src)
        src = re.sub(r'#.*$', ' ', src, flags=re.M)
        for forbidden in ("50000", "59700", "100000", "129700", "200000",
                          "259700", "1497", "2495", "4995"):
            assert forbidden not in src, "%s is hard-coded in the engine" % forbidden
        # No bare dollar figure survives either.
        assert not re.search(r'\$\s*\d', src), "a dollar amount is in the code"

    def test_changing_the_catalogue_changes_what_is_created(self, db_session, brand, fake):
        plan = (db_session.query(BrandBillingPlan)
                .filter_by(platform_id=brand.id, key="starter").first())
        plan.monthly_cents = 42000
        db_session.commit()
        sp.provision_brand(db_session, brand)
        assert 42000 in [p["unit_amount"] for p in fake.created_prices]

    def test_a_commitment_with_no_amount_is_skipped_not_created_at_zero(
            self, db_session, brand, fake):
        plan = (db_session.query(BrandBillingPlan)
                .filter_by(platform_id=brand.id, key="starter").first())
        plan.month_to_month_cents = None
        db_session.commit()
        out = sp.provision_brand(db_session, brand)
        starter = [p for p in out["plans"] if p["plan_key"] == "starter"][0]
        assert starter["commitments"]["month_to_month"]["status"] == "skipped"
        assert all(p["unit_amount"] != 0 for p in fake.created_prices)

    def test_a_non_purchasable_tier_gets_no_price(self, db_session, brand, fake):
        out = sp.provision_brand(db_session, brand)
        ent = [p for p in out["plans"] if p["plan_key"] == "enterprise"][0]
        assert "skipped" in ent
        assert "commitments" not in ent or not ent["commitments"]


# ── idempotency ─────────────────────────────────────────────────────────────

class TestRunningItTwiceCreatesNothing:

    def test_second_run_creates_no_new_price(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand)
        db_session.commit()
        first = len(fake.created_prices)
        sp.provision_brand(db_session, brand)
        assert len(fake.created_prices) == first

    def test_second_run_reports_reuse(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand)
        db_session.commit()
        out = sp.provision_brand(db_session, brand)
        starter = [p for p in out["plans"] if p["plan_key"] == "starter"][0]
        for c in ("term", "month_to_month"):
            assert starter["commitments"][c]["status"].startswith("reused")

    def test_second_run_creates_no_new_product(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand)
        db_session.commit()
        first = len(fake.created_products)
        sp.provision_brand(db_session, brand)
        assert len(fake.created_products) == first

    def test_the_ids_are_written_back_into_configuration(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand)
        db_session.commit()
        plan = (db_session.query(BrandBillingPlan)
                .filter_by(platform_id=brand.id, key="starter").first())
        assert plan.stripe_product_id
        assert plan.stripe_price_id_monthly
        assert plan.stripe_price_id_month_to_month
        # The two commitments must never collapse onto one Price.
        assert plan.stripe_price_id_monthly != plan.stripe_price_id_month_to_month

    def test_an_existing_hand_made_price_is_adopted_not_duplicated(
            self, db_session, brand, monkeypatch):
        st = FakeStripe(
            products={"prod_hand": {"id": "prod_hand", "deleted": False,
                                    "metadata": {"platform_id": brand.id,
                                                 "plan_key": "starter"}}},
            prices={"price_hand": _price_obj("price_hand", "prod_hand", 50000)})
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(sp, "_client", lambda: st)
        out = sp.provision_brand(db_session, brand)
        starter = [p for p in out["plans"] if p["plan_key"] == "starter"][0]
        assert starter["commitments"]["term"]["price_id"] == "price_hand"
        assert starter["commitments"]["term"]["status"] == "reused_found"

    def test_survives_a_stripe_without_search(self, db_session, brand, monkeypatch):
        st = FakeStripe(search_raises=True)
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(sp, "_client", lambda: st)
        out = sp.provision_brand(db_session, brand)
        assert out["summary"]["prices_created"] == 2


# ── a mapped price that no longer matches ───────────────────────────────────

class TestAPriceIsNeverEdited:

    def test_an_archived_mapped_price_is_replaced(self, db_session, brand, monkeypatch):
        st = FakeStripe(
            products={"prod_a": {"id": "prod_a", "deleted": False,
                                 "metadata": {"platform_id": brand.id,
                                              "plan_key": "starter"}}},
            prices={"price_old": _price_obj("price_old", "prod_a", 50000,
                                            active=False)})
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(sp, "_client", lambda: st)
        plan = (db_session.query(BrandBillingPlan)
                .filter_by(platform_id=brand.id, key="starter").first())
        plan.stripe_product_id = "prod_a"
        plan.stripe_price_id_monthly = "price_old"
        db_session.commit()
        sp.provision_brand(db_session, brand)
        db_session.commit()
        db_session.refresh(plan)
        assert plan.stripe_price_id_monthly != "price_old"

    def test_a_stale_amount_creates_a_new_price_and_leaves_the_old_alone(
            self, db_session, brand, monkeypatch):
        st = FakeStripe(
            products={"prod_a": {"id": "prod_a", "deleted": False,
                                 "metadata": {"platform_id": brand.id,
                                              "plan_key": "starter"}}},
            prices={"price_old": _price_obj("price_old", "prod_a", 12345)})
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(sp, "_client", lambda: st)
        plan = (db_session.query(BrandBillingPlan)
                .filter_by(platform_id=brand.id, key="starter").first())
        plan.stripe_product_id = "prod_a"
        plan.stripe_price_id_monthly = "price_old"
        db_session.commit()
        sp.provision_brand(db_session, brand)
        # The old Price still exists and is still active: existing subscribers
        # keep billing until somebody migrates them deliberately.
        assert st._prices["price_old"]["active"] is True

    def test_a_yearly_price_at_the_same_amount_is_not_reused(self, db_session, brand,
                                                             monkeypatch):
        st = FakeStripe(
            products={"prod_a": {"id": "prod_a", "deleted": False,
                                 "metadata": {"platform_id": brand.id,
                                              "plan_key": "starter"}}},
            prices={"price_yr": _price_obj("price_yr", "prod_a", 50000,
                                           interval="year")})
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(sp, "_client", lambda: st)
        out = sp.provision_brand(db_session, brand)
        starter = [p for p in out["plans"] if p["plan_key"] == "starter"][0]
        assert starter["commitments"]["term"]["price_id"] != "price_yr"


# ── dry run ─────────────────────────────────────────────────────────────────

class TestPreviewChangesNothing:

    def test_dry_run_creates_no_stripe_object(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand, dry_run=True)
        assert fake.created_prices == []
        assert fake.created_products == []

    def test_dry_run_writes_no_id_into_configuration(self, db_session, brand, fake):
        sp.provision_brand(db_session, brand, dry_run=True)
        plan = (db_session.query(BrandBillingPlan)
                .filter_by(platform_id=brand.id, key="starter").first())
        assert plan.stripe_price_id_monthly is None
        assert plan.stripe_price_id_month_to_month is None

    def test_dry_run_still_reports_what_it_would_do(self, db_session, brand, fake):
        out = sp.provision_brand(db_session, brand, dry_run=True)
        starter = [p for p in out["plans"] if p["plan_key"] == "starter"][0]
        assert starter["commitments"]["term"]["status"] == "would_create"
        assert out["dry_run"] is True
