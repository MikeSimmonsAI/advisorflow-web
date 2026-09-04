"""P2 — BillingAgreement: the executable commercial relationship.

The assertions that matter most here are about money and history:

  * amounts are COPIED from the approved deal, never recomputed from a
    catalogue, so a package price change cannot reprice a live customer
  * a deal with no approved price REFUSES rather than inventing one
  * provisioning retried twice produces ONE agreement, not two subscriptions
  * superseding never edits the terms of the agreement it replaces
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.models.billing_agreement_models import (AGREEMENT_ACTIVE,
                                                 AGREEMENT_CANCELLED,
                                                 AGREEMENT_DRAFT,
                                                 AGREEMENT_PAST_DUE,
                                                 AGREEMENT_SUPERSEDED,
                                                 BillingAgreement)
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform
from app.models.sales_models import BrandPackage, Opportunity
from app.services import billing_agreement as svc
from app.services import merchant_entity as entity_svc
from app.services.billing_agreement import AgreementRefused


# ── fixtures-as-helpers ──────────────────────────────────────────────────────

def _brand(db, slug="evosyspro", name="EvoSys Pro"):
    p = Platform(name=name, slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name="Restland", platform=None, stripe_customer_id=None):
    o = Organization(name=name, slug=name.lower().replace(" ", "-"),
                     plan="standard",
                     platform_id=platform.id if platform else None,
                     stripe_customer_id=stripe_customer_id)
    db.add(o)
    db.commit()
    return o


def _sales_org(db, platform):
    """Opportunity.brand_sales_org_id is NOT NULL: a deal belongs to a brand's
    sales organisation."""
    from app.models.sales_models import BrandSalesOrg
    existing = (db.query(BrandSalesOrg)
                .filter(BrandSalesOrg.platform_id == platform.id).first())
    if existing:
        return existing
    sales_org = BrandSalesOrg(platform_id=platform.id,
                              name=platform.name + " Sales",
                              slug=platform.slug + "-sales")
    db.add(sales_org)
    db.commit()
    return sales_org


def _opp(db, platform, **kw):
    """Implementation.opportunity_id is NOT NULL - an implementation always
    comes from a won deal, so the tests build one rather than pretend."""
    opp = Opportunity(company_name=kw.pop("company_name", "Restland"),
                      brand_sales_org_id=_sales_org(db, platform).id,
                      status="won", **kw)
    db.add(opp)
    db.commit()
    return opp


def _impl(db, org, platform=None, *, recurring="499.00", setup="1500.00",
          term=13, option="term_agreement", opportunity_id=None,
          package_id=None, currency="USD"):
    if opportunity_id is None:
        opportunity_id = _opp(db, platform).id
    impl = Implementation(
        organization_id=org.id,
        platform_id=platform.id if platform else None,
        opportunity_id=opportunity_id,
        package_id=package_id,
        billing_option=option,
        contract_term_months=term,
        implementation_fee=Decimal(setup) if setup is not None else None,
        recurring_amount=Decimal(recurring) if recurring is not None else None,
        currency=currency,
        billing_start_date=datetime(2026, 10, 1),
    )
    db.add(impl)
    db.commit()
    return impl


def _configured(db):
    """EVO INTEGRATED SOLUTIONS LLC selling EvoSys Pro - the live setup."""
    brand = _brand(db)
    entity, _ = entity_svc.ensure_evosys_pro_configuration(db)
    return entity, brand


# ── money crosses the boundary exactly once ──────────────────────────────────

def test_amounts_are_converted_to_minor_units(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand, recurring="499.00", setup="1500.00")

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.recurring_amount_cents == 49900
    assert agreement.setup_fee_cents == 150000
    assert agreement.currency == "USD"


def test_fractional_amounts_do_not_lose_a_cent(db_session):
    """Numeric(12,2) on the sales side, integer cents on the billing side. The
    conversion happens once, in money.to_cents, and must be exact."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand, recurring="333.33", setup="0.01")

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.recurring_amount_cents == 33333
    assert agreement.setup_fee_cents == 1


def test_terms_are_copied_from_the_deal_not_recomputed(db_session):
    """THE RULE THIS LAYER EXISTS FOR. The package catalogue moves after the
    deal is signed; the agreement must not."""
    entity, brand = _configured(db_session)
    pkg = BrandPackage(platform_id=brand.id, key="growth", name="Growth",
                       monthly_price=Decimal("899.00"),
                       contract_monthly_price=Decimal("799.00"))
    db_session.add(pkg)
    db_session.commit()

    org = _org(db_session, platform=brand)
    # The approved deal was 499, not the catalogue's 899 or 799.
    impl = _impl(db_session, org, brand, recurring="499.00", package_id=pkg.id)

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.recurring_amount_cents == 49900
    # And the catalogue moving afterwards changes nothing.
    pkg.monthly_price = Decimal("1299.00")
    db_session.commit()
    db_session.refresh(agreement)
    assert agreement.recurring_amount_cents == 49900


# ── refuse rather than invent ────────────────────────────────────────────────

def test_a_deal_with_no_approved_price_is_refused(db_session):
    """A billing system that fills in a blank charges somebody the wrong
    number. The blank means a human has not finished the deal."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand, recurring=None, setup=None)

    with pytest.raises(AgreementRefused, match="has not been completed"):
        svc.create_from_implementation(db_session, impl)

    assert db_session.query(BillingAgreement).count() == 0


def test_a_setup_fee_alone_is_enough_to_agree(db_session):
    """A one-time implementation with no recurring component is a real deal."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand, recurring=None, setup="2500.00")

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.setup_fee_cents == 250000
    assert agreement.recurring_amount_cents is None


def test_an_implementation_whose_organization_is_gone_is_refused(db_session):
    """implementations.organization_id is NOT NULL, so the case this guards is
    not a null column - it is an id that no longer resolves, which is what a
    deleted or wrongly-copied organization looks like. Billing must refuse
    rather than issue an agreement with nobody on the other end."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand)
    impl.organization_id = "org-that-does-not-exist"

    with pytest.raises(AgreementRefused, match="nobody to bill"):
        svc.create_from_implementation(db_session, impl)

    db_session.rollback()
    assert db_session.query(BillingAgreement).count() == 0


# ── idempotency: a retried provisioning is not a second subscription ─────────

def test_creating_twice_returns_the_same_agreement(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand)

    first = svc.create_from_implementation(db_session, impl)
    second = svc.create_from_implementation(db_session, impl)

    assert first.id == second.id
    assert db_session.query(BillingAgreement).count() == 1


def test_the_database_refuses_a_second_agreement_for_one_implementation(db_session):
    """Belt and braces: the service returns early, and the unique constraint
    is there for anything that bypasses it."""
    from sqlalchemy.exc import IntegrityError
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand)
    svc.create_from_implementation(db_session, impl)

    db_session.add(BillingAgreement(organization_id=org.id,
                                    implementation_id=impl.id,
                                    currency="USD"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── the hierarchy is resolved, not guessed ───────────────────────────────────

def test_agreement_records_its_place_in_the_hierarchy(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand)

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.merchant_entity_id == entity.id
    assert agreement.platform_id == brand.id
    assert agreement.organization_id == org.id
    assert agreement.implementation_id == impl.id


def test_the_commercial_snapshot_is_captured_at_creation(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, "Restland", brand)
    pkg = BrandPackage(platform_id=brand.id, key="growth", name="Growth Plan")
    db_session.add(pkg)
    db_session.commit()
    impl = _impl(db_session, org, brand, package_id=pkg.id)

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.merchant_legal_name == "EVO INTEGRATED SOLUTIONS LLC"
    assert agreement.brand_name == "EvoSys Pro"
    assert agreement.organization_name == "Restland"
    assert agreement.package_name == "Growth Plan"


def test_a_rename_does_not_rewrite_an_existing_agreement(db_session):
    """HISTORICAL INTEGRITY. Ids answer 'which row'; the snapshot answers
    'what did this say at the time', and a rename makes those different."""
    entity, brand = _configured(db_session)
    org = _org(db_session, "Restland", brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand))

    entity.legal_name = "EVO INTEGRATED SOLUTIONS LLC (RENAMED)"
    brand.name = "EvoSys Pro Enterprise"
    org.name = "Restland Memorial"
    db_session.commit()
    db_session.refresh(agreement)

    assert agreement.merchant_legal_name == "EVO INTEGRATED SOLUTIONS LLC"
    assert agreement.brand_name == "EvoSys Pro"
    assert agreement.organization_name == "Restland"


def test_approved_custom_pricing_is_carried_across(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    opp = _opp(db_session, brand, custom_unit_price=Decimal("12.50"),
               custom_unit_label="active paying customer",
               custom_min_units=200)
    impl = _impl(db_session, org, brand, opportunity_id=opp.id)

    agreement = svc.create_from_implementation(db_session, impl)

    assert agreement.custom_unit_price_cents == 1250
    assert agreement.unit_label == "active paying customer"
    assert agreement.min_units == 200
    assert agreement.has_custom_pricing is True


def test_a_straight_package_sale_carries_no_custom_pricing(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand))

    assert agreement.has_custom_pricing is False
    assert agreement.custom_unit_price_cents is None


def test_the_stripe_customer_is_pinned_at_execution_time(db_session):
    """If the organization's Stripe customer is later replaced, a historical
    agreement must keep naming the customer it actually charged."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_original")
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand))
    assert agreement.stripe_customer_id == "cus_original"

    org.stripe_customer_id = "cus_replacement"
    db_session.commit()
    db_session.refresh(agreement)

    assert agreement.stripe_customer_id == "cus_original"


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_agreements_start_as_drafts_unless_activated(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    impl = _impl(db_session, org, brand)

    assert svc.create_from_implementation(db_session, impl).status == AGREEMENT_DRAFT


def test_activation_is_idempotent(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand))

    svc.activate(db_session, agreement)
    first_activated_at = agreement.activated_at
    svc.activate(db_session, agreement)

    assert agreement.status == AGREEMENT_ACTIVE
    assert agreement.activated_at == first_activated_at


def test_a_closed_agreement_cannot_be_reactivated(db_session):
    """History is not edited. A new arrangement is a new agreement."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand))
    svc.cancel(db_session, agreement)

    with pytest.raises(AgreementRefused, match="history is not"):
        svc.activate(db_session, agreement)


def test_a_past_due_customer_still_has_a_live_agreement(db_session):
    """Treating a failed card as 'no agreement' is how an account silently
    stops being billed."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand), activate=True)
    agreement.status = AGREEMENT_PAST_DUE
    db_session.commit()

    assert agreement.is_live is True
    assert svc.current_for_organization(db_session, org.id).id == agreement.id


# ── supersession preserves history ───────────────────────────────────────────

def test_superseding_does_not_touch_the_old_terms(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    old = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand, recurring="499.00"),
        activate=True)

    new = svc.supersede(db_session, old,
                        {"recurring_amount_cents": 79900},
                        reason="upgrade")

    db_session.refresh(old)
    assert old.recurring_amount_cents == 49900          # untouched
    assert old.status == AGREEMENT_SUPERSEDED
    assert old.superseded_by_id == new.id
    assert old.ended_at is not None
    assert new.recurring_amount_cents == 79900
    assert new.supersedes_id == old.id
    assert new.status == AGREEMENT_ACTIVE


def test_a_replacement_inherits_what_it_does_not_restate(db_session):
    """A renewal that changes one number must not require restating the whole
    deal - that is how the rest of it gets mistyped."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    old = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand, recurring="499.00", term=13),
        activate=True)

    new = svc.supersede(db_session, old, {"contract_term_months": 24},
                        reason="renewal")

    assert new.recurring_amount_cents == 49900
    assert new.currency == old.currency
    assert new.brand_name == "EvoSys Pro"
    assert new.contract_term_months == 24


def test_a_replacement_does_not_recharge_the_setup_fee(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    old = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand, setup="1500.00"), activate=True)
    assert old.setup_fee_cents == 150000

    new = svc.supersede(db_session, old, {}, reason="renewal")

    assert new.setup_fee_cents is None


def test_only_the_newest_agreement_is_current(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    old = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand), activate=True)
    new = svc.supersede(db_session, old, {"recurring_amount_cents": 79900})

    assert svc.current_for_organization(db_session, org.id).id == new.id
    assert len(svc.history_for_organization(db_session, org.id)) == 2


# ── Stripe subscription linkage ──────────────────────────────────────────────

def test_attaching_a_subscription_is_idempotent(db_session):
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand), activate=True)

    svc.attach_stripe_subscription(db_session, agreement, "sub_123", "price_1")
    svc.attach_stripe_subscription(db_session, agreement, "sub_123")

    assert agreement.stripe_subscription_id == "sub_123"
    assert agreement.stripe_price_id == "price_1"


def test_an_agreement_cannot_be_repointed_at_a_different_subscription(db_session):
    """Two subscriptions on one agreement is a double charge discovered on a
    customer's statement rather than here."""
    entity, brand = _configured(db_session)
    org = _org(db_session, platform=brand)
    agreement = svc.create_from_implementation(
        db_session, _impl(db_session, org, brand), activate=True)
    svc.attach_stripe_subscription(db_session, agreement, "sub_original")

    with pytest.raises(AgreementRefused, match="already executed"):
        svc.attach_stripe_subscription(db_session, agreement, "sub_different")

    assert agreement.stripe_subscription_id == "sub_original"


# ── tenant isolation ─────────────────────────────────────────────────────────

def test_agreements_do_not_leak_between_organizations(db_session):
    entity, brand = _configured(db_session)
    org_a = _org(db_session, "Restland", brand)
    org_b = _org(db_session, "Somebody Else", brand)
    svc.create_from_implementation(db_session, _impl(db_session, org_a, brand),
                                   activate=True)

    assert svc.current_for_organization(db_session, org_b.id) is None
    assert svc.history_for_organization(db_session, org_b.id) == []


def test_two_brands_two_entities_two_issuers(db_session):
    """An agreement's issuer comes from its own brand, never from whichever
    entity happens to be the default."""
    evo, evo_brand = _configured(db_session)
    from app.models.billing_entity_models import MerchantEntity
    other = MerchantEntity(slug="other-co", legal_name="OTHER CO LLC")
    db_session.add(other)
    db_session.commit()
    other_brand = _brand(db_session, "otherbrand", "Other Brand")
    entity_svc.link_platform(db_session, other_brand, other)

    org_a = _org(db_session, "Restland", evo_brand)
    org_b = _org(db_session, "Other Customer", other_brand)
    a = svc.create_from_implementation(db_session, _impl(db_session, org_a, evo_brand))
    b = svc.create_from_implementation(db_session, _impl(db_session, org_b, other_brand))

    assert a.merchant_legal_name == "EVO INTEGRATED SOLUTIONS LLC"
    assert b.merchant_legal_name == "OTHER CO LLC"
