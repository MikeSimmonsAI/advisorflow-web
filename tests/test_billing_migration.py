"""P5 — retiring `PLANS` as a pricing authority WITHOUT repricing anybody.

THE ONE THING THIS FILE IS DEFENDING

An existing customer pays what they actually agreed to pay. Every test below
is some version of that sentence:

  * a BillingAgreement's amount is immune to the catalogue changing under it -
    both catalogues, the legacy `PLANS` dict and `brand_packages`
  * reconciliation looks and reports; it cannot write, and the tests assert
    that against the Stripe fake and against the database
  * a disagreement between what Stripe charges and what the deal says is
    REPORTED, never resolved by picking one
  * a legacy customer's agreement is reconstructed from what they are BILLED,
    never from what the catalogue says that plan costs today

The repricing bug this phase exists to prevent is a quiet one: nothing throws,
nothing looks wrong, and a customer's invoice is a different number next month.
So the assertions are on the exact integer, before and after.
"""

import itertools
from decimal import Decimal
from datetime import datetime

import pytest

from app.models.billing_agreement_models import (AGREEMENT_ACTIVE,
                                                 SOURCE_MIGRATION,
                                                 BillingAgreement)
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandPackage, BrandSalesOrg, Opportunity
from app.services import billing_agreement as agreements
from app.services import billing_migration as migration
from app.services import billing_operations as ops
from app.services import merchant_entity as entity_svc
from app.services import stripe_gateway as gw
from app.services.auth_service import create_access_token, hash_password
from app.services.billing_access import BillingScope

_SEQ = itertools.count(1)


# ═════════════════════════════════════════════════════════════════════════════
# A fake Stripe that RECORDS EVERY CALL
#
# The recording is the point. "Reconciliation does not mutate" is not provable
# by checking a return value - it is provable by asserting that the only Stripe
# operations that happened were retrievals.
# ═════════════════════════════════════════════════════════════════════════════

class _Obj(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


class FakeStripe:

    MUTATIONS = ("create", "modify", "delete", "void", "finalize", "send",
                 "update", "cancel")

    def __init__(self, unit_amount=49900, currency="usd", status="active",
                 interval="month"):
        self.calls = []
        self.raise_on_retrieve = None
        self._sub = _Obj(
            id="sub_legacy", status=status,
            items=_Obj(data=[_Obj(price=_Obj(unit_amount=unit_amount,
                                             currency=currency,
                                             recurring=_Obj(interval=interval)))]))
        fake = self

        class Subscription:
            @staticmethod
            def retrieve(*a, **k):
                fake.calls.append(("Subscription.retrieve", a, k))
                if fake.raise_on_retrieve:
                    raise fake.raise_on_retrieve
                return fake._sub

            @staticmethod
            def create(*a, **k):
                fake.calls.append(("Subscription.create", a, k))
                return _Obj(id="sub_new", status="active")

            @staticmethod
            def modify(*a, **k):
                fake.calls.append(("Subscription.modify", a, k))
                return fake._sub

            @staticmethod
            def delete(*a, **k):
                fake.calls.append(("Subscription.delete", a, k))
                return fake._sub

        self.Subscription = Subscription

    @property
    def mutations(self):
        """Every recorded call that could have changed something at Stripe."""
        return [c[0] for c in self.calls
                if any(m in c[0].split(".")[-1].lower()
                       for m in self.MUTATIONS)]


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(gw, "client", lambda: fake)
    monkeypatch.setattr(gw, "_stripe", lambda: fake)
    return fake


@pytest.fixture
def no_stripe(monkeypatch):
    def _unavailable():
        raise gw.StripeUnavailable("no key in this test")
    monkeypatch.setattr(gw, "client", _unavailable)


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════

def _brand(db, slug="evosyspro"):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p:
        return p
    p = Platform(name="EvoSys Pro", slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name="Restland", platform=None, plan="growth",
         stripe_customer_id=None, stripe_subscription_id=None):
    o = Organization(name=name, slug="%s-%d" % (name.lower(), next(_SEQ)),
                     plan=plan,
                     platform_id=platform.id if platform else None,
                     stripe_customer_id=stripe_customer_id,
                     stripe_subscription_id=stripe_subscription_id)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id if org else None,
             email="p5.%d@example.com" % next(_SEQ),
             password_hash=hash_password("TestPass123!"),
             full_name="Billing Person", role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _scope(db, org):
    return BillingScope(_user(db, org), org, True, True)


def _package(db, platform, key="growth", monthly="997.00"):
    pkg = BrandPackage(platform_id=platform.id, key=key, name=key.title(),
                       monthly_price=Decimal(monthly), price=Decimal(monthly),
                       setup_fee=Decimal("2500.00"), currency="USD")
    db.add(pkg)
    db.commit()
    return pkg


def _impl(db, org, platform, recurring="499.00", setup="1500.00",
          package=None):
    so = (db.query(BrandSalesOrg)
          .filter(BrandSalesOrg.platform_id == platform.id).first())
    if so is None:
        so = BrandSalesOrg(platform_id=platform.id, name="Sales",
                           slug="sales-%d" % next(_SEQ))
        db.add(so)
        db.commit()
    opp = Opportunity(company_name=org.name, status="won",
                      brand_sales_org_id=so.id)
    db.add(opp)
    db.commit()
    impl = Implementation(
        organization_id=org.id, platform_id=platform.id, opportunity_id=opp.id,
        package_id=package.id if package else None,
        billing_option="term_agreement", contract_term_months=13,
        implementation_fee=Decimal(setup) if setup else None,
        recurring_amount=Decimal(recurring) if recurring else None,
        currency="USD", billing_start_date=datetime(2026, 10, 1))
    db.add(impl)
    db.commit()
    return impl


def _agreement(db, org, platform, recurring="499.00", package=None):
    entity_svc.ensure_evosys_pro_configuration(db)
    impl = _impl(db, org, platform, recurring=recurring, package=package)
    return agreements.create_from_implementation(db, impl, activate=True)


def _codes(report):
    return {f["code"] for f in report["findings"]}


# ═════════════════════════════════════════════════════════════════════════════
# NEW BILLING DOES NOT READ THE CATALOGUE
# ═════════════════════════════════════════════════════════════════════════════

def test_a_new_agreement_takes_its_amount_from_the_deal_not_from_plans(
        db_session):
    """The org is on plan 'growth', which the legacy catalogue lists at $997.
    The approved deal says $499. The deal wins, and it is not close."""
    from app.routers.billing_router import PLANS
    brand = _brand(db_session)
    org = _org(db_session, plan="growth")

    agreement = _agreement(db_session, org, brand, recurring="499.00")

    assert agreement.recurring_amount_cents == 49900
    assert PLANS["growth"]["monthly_cents"] == 99700
    assert agreement.recurring_amount_cents != PLANS["growth"]["monthly_cents"]


def test_subscribing_charges_the_agreement_amount_not_the_plan_amount(
        db_session, stripe, monkeypatch):
    """END TO END. The organization's plan column says 'professional' - $1997
    in the catalogue - and Stripe is asked for $499, because that is what was
    agreed."""
    from app.routers.billing_router import PLANS
    brand = _brand(db_session)
    org = _org(db_session, plan="professional", stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand, recurring="499.00")

    created = {}

    class Product:
        @staticmethod
        def create(*a, **k):
            return _Obj(id="prod_1")

    class Price:
        @staticmethod
        def create(*a, **k):
            created.update(k)
            return _Obj(id="price_1")

    stripe.Product = Product
    stripe.Price = Price

    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    assert created["unit_amount"] == 49900
    assert created["unit_amount"] != PLANS["professional"]["monthly_cents"]


def test_changing_plans_does_not_change_an_existing_agreement(db_session,
                                                              monkeypatch):
    """THE REPRICING TEST. The catalogue is edited underneath a live customer
    and their agreed amount does not move."""
    import app.routers.billing_router as br
    brand = _brand(db_session)
    org = _org(db_session, plan="growth")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    before = agreement.recurring_amount_cents

    monkeypatch.setitem(br.PLANS, "growth",
                        dict(br.PLANS["growth"], monthly_cents=250000,
                             monthly_usd=2500))
    db_session.expire_all()
    reloaded = (db_session.query(BillingAgreement)
                .filter(BillingAgreement.id == agreement.id).first())

    assert reloaded.recurring_amount_cents == before == 49900


def test_changing_the_package_catalogue_does_not_change_an_existing_agreement(
        db_session):
    """Same test against the OTHER catalogue. `brand_packages` is the modern
    price list and it is just as forbidden as `PLANS` from repricing a deal
    that is already agreed."""
    brand = _brand(db_session)
    package = _package(db_session, brand, monthly="997.00")
    org = _org(db_session)
    agreement = _agreement(db_session, org, brand, recurring="499.00",
                           package=package)
    before = agreement.recurring_amount_cents

    package.monthly_price = Decimal("2500.00")
    package.price = Decimal("2500.00")
    db_session.commit()
    db_session.expire_all()
    reloaded = (db_session.query(BillingAgreement)
                .filter(BillingAgreement.id == agreement.id).first())

    assert reloaded.recurring_amount_cents == before == 49900


def test_an_agreement_records_the_package_name_as_a_snapshot_not_a_lookup(
        db_session):
    brand = _brand(db_session)
    package = _package(db_session, brand)
    org = _org(db_session)
    agreement = _agreement(db_session, org, brand, package=package)
    before = agreement.package_name

    package.name = "Renamed Tier"
    db_session.commit()
    db_session.expire_all()
    reloaded = (db_session.query(BillingAgreement)
                .filter(BillingAgreement.id == agreement.id).first())

    assert reloaded.package_name == before


# ═════════════════════════════════════════════════════════════════════════════
# RECONCILIATION IS READ ONLY
# ═════════════════════════════════════════════════════════════════════════════

def test_reconciliation_makes_no_stripe_mutation(db_session, stripe):
    """Asserted against the recorded call list, not against a return value."""
    brand = _brand(db_session)
    org = _org(db_session, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_legacy")
    _agreement(db_session, org, brand)

    migration.reconcile_organization(db_session, org)

    assert stripe.mutations == []
    assert [c[0] for c in stripe.calls] == ["Subscription.retrieve"]


def test_reconciliation_makes_no_local_financial_mutation(db_session, stripe):
    """Every financial column, before and after, compared field by field."""
    brand = _brand(db_session)
    org = _org(db_session, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_legacy")
    agreement = _agreement(db_session, org, brand, recurring="349.00")

    def snapshot():
        db_session.expire_all()
        a = (db_session.query(BillingAgreement)
             .filter(BillingAgreement.id == agreement.id).first())
        o = (db_session.query(Organization)
             .filter(Organization.id == org.id).first())
        return (a.recurring_amount_cents, a.setup_fee_cents, a.currency,
                a.status, a.stripe_subscription_id, o.plan,
                o.stripe_subscription_id, o.stripe_customer_id,
                o.billing_status)

    before = snapshot()
    migration.reconcile_organization(db_session, org)

    assert snapshot() == before


def test_reconciliation_never_creates_an_agreement(db_session, stripe):
    org = _org(db_session, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_legacy")

    migration.reconcile_organization(db_session, org)

    assert db_session.query(BillingAgreement).count() == 0


def test_the_report_declares_itself_a_dry_run(db_session, stripe):
    org = _org(db_session)

    assert migration.reconcile_organization(db_session, org)["dry_run"] is True
    assert migration.reconcile_all(db_session)["dry_run"] is True


def test_an_existing_subscription_is_not_repriced_by_reconciliation(
        db_session, stripe):
    """THE HEADLINE. Stripe charges $349, the deal says $499, the catalogue
    says $997. Nothing about the subscription changes, and the customer's
    actual price is reported as it stands."""
    brand = _brand(db_session)
    org = _org(db_session, plan="growth", stripe_customer_id="cus_x",
               stripe_subscription_id="sub_legacy")
    _impl(db_session, org, brand, recurring="499.00")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    report = migration.reconcile_organization(db_session, org)

    assert stripe.mutations == []
    assert report["stripe"]["recurring_amount_cents"] == 34900
    assert report["verdict"] == migration.NEEDS_REVIEW


# ═════════════════════════════════════════════════════════════════════════════
# DISAGREEMENTS ARE REPORTED, NEVER RESOLVED
# ═════════════════════════════════════════════════════════════════════════════

def test_a_deal_stripe_mismatch_is_reported_not_fixed(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, stripe_subscription_id="sub_legacy")
    _impl(db_session, org, brand, recurring="499.00")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    report = migration.reconcile_organization(db_session, org)

    assert "deal_vs_stripe" in _codes(report)
    detail = [f["detail"] for f in report["findings"]
              if f["code"] == "deal_vs_stripe"][0]
    assert "349.00" in detail and "499.00" in detail


def test_an_agreement_stripe_mismatch_is_reported(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, stripe_subscription_id="sub_legacy")
    _agreement(db_session, org, brand, recurring="499.00")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    report = migration.reconcile_organization(db_session, org)

    assert "agreement_vs_stripe" in _codes(report)


def test_catalogue_drift_is_reported_as_information_not_a_defect(db_session,
                                                                 stripe):
    """A customer differing from list price is usually CORRECT - it is a
    negotiated rate. The finding says so in words, because the next person to
    read it is the one who might otherwise "fix" it."""
    org = _org(db_session, plan="growth", stripe_subscription_id="sub_legacy")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    report = migration.reconcile_organization(db_session, org)

    detail = [f["detail"] for f in report["findings"]
              if f["code"] == "catalogue_drift"][0]
    assert "reference only" in detail.lower()
    assert report["catalogue_reference"]["note"].startswith("REFERENCE ONLY")


def test_a_legacy_customer_billed_with_no_agreement_is_listed(db_session,
                                                              stripe):
    """The P5 worklist, one row per customer."""
    org = _org(db_session, plan="growth", stripe_subscription_id="sub_legacy")

    report = migration.reconcile_organization(db_session, org)

    assert "live_without_agreement" in _codes(report)


def test_a_stale_local_subscription_id_is_a_finding_not_a_crash(db_session,
                                                                stripe):
    org = _org(db_session, stripe_subscription_id="sub_deleted")
    stripe.raise_on_retrieve = gw.StripeOperationFailed("No such subscription")

    report = migration.reconcile_organization(db_session, org)

    assert "stripe_unreadable" in _codes(report)


def test_a_stripe_outage_degrades_the_report_rather_than_failing_it(
        db_session, no_stripe):
    """A reconciliation run that dies on the first unreachable customer is a
    run nobody finishes."""
    brand = _brand(db_session)
    org = _org(db_session, stripe_subscription_id="sub_legacy")
    _agreement(db_session, org, brand)

    report = migration.reconcile_organization(db_session, org)

    assert report["stripe"]["unavailable_reason"]
    assert report["agreement"]["recurring_amount_cents"] == 49900


def test_a_clean_organization_reports_ok(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, plan=None, stripe_subscription_id="sub_legacy")
    _agreement(db_session, org, brand, recurring="499.00")

    report = migration.reconcile_organization(db_session, org)

    assert report["findings"] == []
    assert report["verdict"] == migration.OK


def test_the_platform_report_counts_findings_by_code(db_session, stripe):
    brand = _brand(db_session)
    _org(db_session, "Alpha", plan="growth",
         stripe_subscription_id="sub_legacy")
    _org(db_session, "Beta", plan="growth", stripe_subscription_id="sub_legacy")

    report = migration.reconcile_all(db_session)

    assert report["organization_count"] == 2
    assert report["findings_by_code"]["live_without_agreement"] == 2
    assert stripe.mutations == []


# ═════════════════════════════════════════════════════════════════════════════
# LEGACY RECONSTRUCTION PRESERVES WHAT IS ACTUALLY PAID
# ═════════════════════════════════════════════════════════════════════════════

def test_a_legacy_proposal_preserves_the_stripe_amount_not_the_catalogue(
        db_session, stripe):
    """THE OTHER HEADLINE. Plan 'growth' lists at $997; this customer pays
    $349; the proposal says $349."""
    org = _org(db_session, plan="growth", stripe_customer_id="cus_x",
               stripe_subscription_id="sub_legacy")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    result = migration.propose_legacy_agreement(db_session, org)

    assert result["proposal"]["recurring_amount_cents"] == 34900
    assert result["proposal"]["amount_source"] == "stripe_subscription"
    assert result["catalogue_reference"]["monthly_cents"] == 99700


def test_a_proposal_writes_nothing_by_default(db_session, stripe):
    org = _org(db_session, plan="growth", stripe_subscription_id="sub_legacy")

    result = migration.propose_legacy_agreement(db_session, org)

    assert result["dry_run"] is True
    assert result["applied"] is False
    assert db_session.query(BillingAgreement).count() == 0
    assert stripe.mutations == []


def test_applying_a_proposal_records_the_billed_amount_and_calls_no_stripe(
        db_session, stripe):
    """It records what is ALREADY happening. Creating or modifying a
    subscription here would be the repricing this phase forbids."""
    brand = _brand(db_session)
    entity_svc.ensure_evosys_pro_configuration(db_session)
    org = _org(db_session, platform=brand, plan="growth",
               stripe_customer_id="cus_x", stripe_subscription_id="sub_legacy")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    result = migration.propose_legacy_agreement(db_session, org, apply=True)

    assert result["applied"] is True
    agreement = (db_session.query(BillingAgreement)
                 .filter(BillingAgreement.id == result["agreement_id"]).first())
    assert agreement.recurring_amount_cents == 34900
    assert agreement.source == SOURCE_MIGRATION
    assert agreement.stripe_subscription_id == "sub_legacy"
    assert stripe.mutations == []


def test_conflicting_evidence_is_flagged_and_nothing_is_written(db_session,
                                                                stripe):
    """Stripe says $349, the approved deal says $499. That is a business
    question - an approved discount, or a price change never applied - and it
    is not answered by picking one."""
    brand = _brand(db_session)
    org = _org(db_session, plan="growth", stripe_subscription_id="sub_legacy")
    _impl(db_session, org, brand, recurring="499.00")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    result = migration.propose_legacy_agreement(db_session, org, apply=True)

    assert result["status"] == migration.NEEDS_REVIEW
    assert result["applied"] is False
    assert db_session.query(BillingAgreement).count() == 0


def test_no_price_evidence_refuses_rather_than_using_the_catalogue(db_session,
                                                                   stripe):
    """The catalogue price is not evidence of what THIS customer pays. With no
    Stripe amount and no approved deal there is nothing to preserve, and
    inventing a number from list price is the repricing bug."""
    org = _org(db_session, plan="growth")

    result = migration.propose_legacy_agreement(db_session, org, apply=True)

    assert result["status"] == migration.NO_EVIDENCE
    assert result["applied"] is False
    assert db_session.query(BillingAgreement).count() == 0


def test_the_approved_deal_is_used_when_stripe_has_nothing(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, plan="growth")
    _impl(db_session, org, brand, recurring="499.00")

    result = migration.propose_legacy_agreement(db_session, org)

    assert result["proposal"]["recurring_amount_cents"] == 49900
    assert result["proposal"]["amount_source"] == "approved_implementation"


def test_an_existing_agreement_is_never_rebuilt(db_session, stripe):
    """P2 owns that record. Re-deriving one is how a customer gets repriced by
    a migration that was supposed to be safe."""
    brand = _brand(db_session)
    org = _org(db_session, plan="growth", stripe_subscription_id="sub_legacy")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34900

    result = migration.propose_legacy_agreement(db_session, org, apply=True)

    assert result["status"] == "already_migrated"
    assert result["applied"] is False
    db_session.expire_all()
    reloaded = (db_session.query(BillingAgreement)
                .filter(BillingAgreement.id == agreement.id).first())
    assert reloaded.recurring_amount_cents == 49900


def test_the_amount_is_an_integer_all_the_way_through(db_session, stripe):
    """No float ever touches a billing number."""
    org = _org(db_session, plan="growth", stripe_subscription_id="sub_legacy")
    stripe._sub["items"]["data"][0]["price"]["unit_amount"] = 34955

    result = migration.propose_legacy_agreement(db_session, org)

    amount = result["proposal"]["recurring_amount_cents"]
    assert isinstance(amount, int) and not isinstance(amount, bool)
    assert result["proposal"]["recurring_amount"] == "349.55"


# ═════════════════════════════════════════════════════════════════════════════
# THE LEGACY CHECKOUT FLOW
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def checkout_stripe(monkeypatch):
    """The router imports `stripe` directly, so the seam is there."""
    import app.routers.billing_router as br
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_p5")
    calls = []

    class Session_:
        @staticmethod
        def create(**k):
            calls.append(k)
            return _Obj(url="https://checkout.stripe.test/session")

    class Customer:
        @staticmethod
        def create(**k):
            return _Obj(id="cus_new")

    class Subscription:
        status = "active"

        @staticmethod
        def retrieve(sub_id):
            return _Obj(id=sub_id, status=Subscription.status)

    fake = type("S", (), {"checkout": type("C", (), {"Session": Session_}),
                          "Customer": Customer,
                          "Subscription": Subscription,
                          "api_key": None})
    monkeypatch.setattr(br, "stripe", fake)
    fake.calls = calls
    return fake


def test_legacy_checkout_still_works_for_a_customer_with_no_agreement(
        client, db_session, checkout_stripe):
    """COMPATIBILITY. The self-serve path is unchanged for the customers it
    was written for, and it still prices from the catalogue for them."""
    from app.routers.billing_router import PLANS
    org = _org(db_session, plan="trial")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/checkout", headers=headers,
                    json={"plan": "growth", "interval": "month"})

    assert r.status_code == 200
    assert r.json()["checkout_url"].startswith("https://")
    line = checkout_stripe.calls[0]["line_items"][0]["price_data"]
    assert line["unit_amount"] == PLANS["growth"]["monthly_cents"]


def test_the_legacy_annual_discount_formula_is_untouched(client, db_session,
                                                         checkout_stripe):
    """Eleven months billed for twelve. P5 does not redesign pricing, so this
    is asserted as it stands rather than improved."""
    from app.routers.billing_router import PLANS
    org = _org(db_session, plan="trial")
    headers = _headers(db_session, _user(db_session, org))

    client.post("/billing/checkout", headers=headers,
                json={"plan": "growth", "interval": "year"})

    line = checkout_stripe.calls[0]["line_items"][0]["price_data"]
    assert line["unit_amount"] == PLANS["growth"]["monthly_cents"] * 11


def test_a_customer_with_an_agreement_is_not_billed_from_the_catalogue(
        client, db_session, checkout_stripe):
    """THE GUARD. This organization negotiated $499. The catalogue says $997.
    Legacy checkout must not be the route that quietly bills them $997."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, plan="growth")
    _agreement(db_session, org, brand, recurring="499.00")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/checkout", headers=headers,
                    json={"plan": "growth", "interval": "month"})

    assert r.status_code == 409
    assert "agreement" in r.json()["detail"].lower()
    assert checkout_stripe.calls == []


def test_checkout_refuses_to_create_a_second_subscription(client, db_session,
                                                          checkout_stripe):
    """Previously absent, and a live double-charge: an organization already
    carrying a subscription got a second one."""
    org = _org(db_session, plan="growth", stripe_customer_id="cus_x",
               stripe_subscription_id="sub_existing")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/checkout", headers=headers,
                    json={"plan": "growth", "interval": "month"})

    assert r.status_code == 409
    assert checkout_stripe.calls == []


def test_a_cancelled_subscription_does_not_block_a_new_signup(
        client, db_session, checkout_stripe):
    """Stripe is asked rather than the local column trusted, so a customer who
    left and came back can sign up again."""
    checkout_stripe.Subscription.status = "canceled"
    org = _org(db_session, plan="trial", stripe_customer_id="cus_x",
               stripe_subscription_id="sub_old")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/checkout", headers=headers,
                    json={"plan": "growth", "interval": "month"})

    assert r.status_code == 200
    checkout_stripe.Subscription.status = "active"


# ═════════════════════════════════════════════════════════════════════════════
# THE RECONCILIATION SURFACE IS TENANT-SAFE (P3 MUST NOT REGRESS)
# ═════════════════════════════════════════════════════════════════════════════

def test_reconciliation_reports_only_the_active_workspace(client, db_session,
                                                          stripe):
    mine = _org(db_session, "Restland")
    _org(db_session, "Hillcrest", stripe_subscription_id="sub_legacy")
    headers = _headers(db_session, _user(db_session, mine))

    body = client.get("/billing/reconciliation", headers=headers).json()

    assert body["local"]["organization_id"] == mine.id
    assert body["dry_run"] is True


def test_an_advisor_cannot_read_reconciliation(client, db_session, stripe):
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org, "advisor"))

    assert client.get("/billing/reconciliation",
                      headers=headers).status_code == 403


def test_the_platform_report_is_god_only(client, db_session, stripe):
    """A tenant admin must never see another organization's Stripe ids,
    subscription or amount."""
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org))

    r = client.get("/billing/reconciliation/platform", headers=headers)

    assert r.status_code == 403


def test_the_reconciliation_route_accepts_no_organization_id(db_session):
    """STRUCTURAL. Adding one later would reopen exactly the hole P3 closed."""
    from app.routers.billing_router import router

    routes = [r for r in router.routes if "reconciliation" in r.path]
    assert routes
    for route in routes:
        assert "{org_id}" not in route.path
        assert "{organization_id}" not in route.path
