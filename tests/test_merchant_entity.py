"""P1 — the entity/configuration layer: WHO issues, WHO is billed.

Three models, deliberately not one, and these tests exist mostly to keep them
apart:

    MerchantEntity   EVO INTEGRATED SOLUTIONS LLC - the legal seller
    Platform         EvoSys Pro - the brand the customer recognises
    Organization     the SaaS customer being billed

The most valuable assertions here are the ones about what must NOT happen: no
Stripe secret reachable through the ORM, no issuer resolved across a tenant
boundary, and no existing production row broken by a column that did not exist
yesterday.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.billing_entity_models import (ENTITY_LLC, EVO_LEGAL_NAME,
                                              EVO_SLUG, MerchantEntity,
                                              STRIPE_CONFIGURED,
                                              STRIPE_UNCONFIGURED)
from app.models.models import Organization, Platform
from app.services import merchant_entity as svc


# ── helpers ──────────────────────────────────────────────────────────────────

def _platform(db, slug="evosyspro", name="EvoSys Pro"):
    p = Platform(name=name, slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name, platform=None):
    o = Organization(name=name, slug=name.lower().replace(" ", "-"),
                     plan="standard",
                     platform_id=platform.id if platform else None)
    db.add(o)
    db.commit()
    return o


# ── model integrity ──────────────────────────────────────────────────────────

def test_merchant_entity_can_be_created_with_its_legal_identity(db_session):
    entity = MerchantEntity(slug=EVO_SLUG, legal_name=EVO_LEGAL_NAME,
                            entity_type=ENTITY_LLC, jurisdiction="TX")
    db_session.add(entity)
    db_session.commit()

    stored = db_session.query(MerchantEntity).filter(
        MerchantEntity.slug == EVO_SLUG).one()
    assert stored.legal_name == "EVO INTEGRATED SOLUTIONS LLC"
    assert stored.entity_type == ENTITY_LLC
    assert stored.is_active is True
    assert stored.is_default is False
    assert stored.stripe_config_status == STRIPE_UNCONFIGURED


def test_legal_name_is_unique(db_session):
    """Two rows claiming to be the same company makes 'who issued this'
    ambiguous exactly where it must not be."""
    db_session.add(MerchantEntity(slug="one", legal_name=EVO_LEGAL_NAME))
    db_session.commit()

    db_session.add(MerchantEntity(slug="two", legal_name=EVO_LEGAL_NAME))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_slug_is_unique(db_session):
    db_session.add(MerchantEntity(slug=EVO_SLUG, legal_name="First LLC"))
    db_session.commit()

    db_session.add(MerchantEntity(slug=EVO_SLUG, legal_name="Second LLC"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_one_stripe_account_belongs_to_one_entity(db_session):
    """Two entities on the same Stripe account would make attribution of a
    webhook a coin toss."""
    db_session.add(MerchantEntity(slug="a", legal_name="A LLC",
                                  stripe_account_id="acct_shared"))
    db_session.commit()

    db_session.add(MerchantEntity(slug="b", legal_name="B LLC",
                                  stripe_account_id="acct_shared"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_several_entities_may_have_no_stripe_account_yet(db_session):
    """UNIQUE must not collapse to 'only one unconfigured entity' - NULL is not
    a value, and an entity is created before its account is connected."""
    db_session.add(MerchantEntity(slug="a", legal_name="A LLC"))
    db_session.add(MerchantEntity(slug="b", legal_name="B LLC"))
    db_session.commit()

    assert db_session.query(MerchantEntity).count() == 2


# ── no secrets ───────────────────────────────────────────────────────────────

_SECRET_MARKERS = ("secret", "api_key", "apikey", "private_key", "token",
                   "password", "signing", "restricted_key", "sk_", "rk_",
                   "whsec")


def test_no_stripe_secret_can_be_persisted_on_the_entity():
    """STRUCTURAL, not a convention.

    Secrets stay in the environment, where P0 already reads them from. This
    walks the mapped columns rather than the source text, so a secret added
    through any route - a column, a mixin, an inherited attribute - fails here.
    """
    columns = [c.name.lower() for c in MerchantEntity.__table__.columns]
    offenders = [name for name in columns
                 if any(marker in name for marker in _SECRET_MARKERS)]
    assert offenders == [], (
        "merchant_entities must hold no secret material; found %s" % offenders)


def test_the_stripe_identifier_that_is_stored_is_the_public_account_id():
    """acct_... appears in every webhook payload and in the dashboard URL. It
    is an identifier, not a credential, and it is the one Stripe value this
    table needs in order to attribute an event to an entity."""
    columns = {c.name for c in MerchantEntity.__table__.columns}
    assert "stripe_account_id" in columns
    assert "stripe_config_status" in columns
    # And nothing that would carry a live key.
    assert not {"stripe_secret_key", "stripe_api_key",
                "stripe_webhook_secret"} & columns


# ── Brand -> MerchantEntity ──────────────────────────────────────────────────

def test_platform_links_to_its_merchant_entity(db_session):
    entity = svc.ensure_evo_entity(db_session)
    platform = _platform(db_session)

    svc.link_platform(db_session, platform, entity)

    assert platform.merchant_entity_id == entity.id
    assert svc.resolve_for_platform(db_session, platform).id == entity.id


def test_one_entity_stands_behind_several_brands(db_session):
    """The reason the link lives on Platform and not on MerchantEntity: today
    one LLC sells EvoSys Pro, BookaBoost and Harmony & Hustle."""
    entity = svc.ensure_evo_entity(db_session)
    for slug, name in (("evosyspro", "EvoSys Pro"),
                       ("bookaboost", "BookaBoost"),
                       ("harmonyhustle", "Harmony & Hustle")):
        svc.link_platform(db_session, _platform(db_session, slug, name), entity)

    linked = db_session.query(Platform).filter(
        Platform.merchant_entity_id == entity.id).count()
    assert linked == 3


# ── resolution: organization -> brand -> entity ──────────────────────────────

def test_organization_resolves_its_issuer_through_its_brand(db_session):
    entity = svc.ensure_evo_entity(db_session)
    platform = _platform(db_session)
    svc.link_platform(db_session, platform, entity)
    org = _org(db_session, "Restland", platform)

    assert svc.resolve_for_organization(db_session, org).id == entity.id


def test_organization_does_not_carry_its_own_merchant_entity_id(db_session):
    """DERIVED, NOT DUPLICATED. If an organization stored the issuer itself,
    moving that customer to another brand would leave it pointing at the old
    legal entity and nothing would notice until an invoice went out in the
    wrong company's name."""
    assert "merchant_entity_id" not in {c.name for c in Organization.__table__.columns}


def test_moving_a_brand_to_another_entity_moves_its_customers_with_it(db_session):
    """The payoff of deriving it: one write, and every organization under that
    brand issues from the new entity."""
    old = svc.ensure_evo_entity(db_session)
    platform = _platform(db_session)
    svc.link_platform(db_session, platform, old)
    org = _org(db_session, "Restland", platform)
    assert svc.resolve_for_organization(db_session, org).id == old.id

    new = MerchantEntity(slug="successor", legal_name="SUCCESSOR HOLDINGS LLC")
    db_session.add(new)
    db_session.commit()
    svc.link_platform(db_session, platform, new)

    assert svc.resolve_for_organization(db_session, org).id == new.id


# ── tenant safety ────────────────────────────────────────────────────────────

def test_billing_identity_does_not_leak_across_brands(db_session):
    """Two customers on two brands sold by two different legal entities must
    resolve to their own issuer and never to each other's."""
    evo = svc.ensure_evo_entity(db_session)
    other = MerchantEntity(slug="other-co", legal_name="OTHER CO LLC")
    db_session.add(other)
    db_session.commit()

    evo_brand = _platform(db_session, "evosyspro", "EvoSys Pro")
    other_brand = _platform(db_session, "otherbrand", "Other Brand")
    svc.link_platform(db_session, evo_brand, evo)
    svc.link_platform(db_session, other_brand, other)

    evo_org = _org(db_session, "Restland", evo_brand)
    other_org = _org(db_session, "Somebody Else", other_brand)

    assert svc.resolve_for_organization(db_session, evo_org).id == evo.id
    assert svc.resolve_for_organization(db_session, other_org).id == other.id
    assert svc.issuer_snapshot(db_session, other_org)["merchant_legal_name"] \
        == "OTHER CO LLC"


def test_an_unrecognised_stripe_account_resolves_to_nothing(db_session):
    """Never falls back to the default. An unknown account is a fact worth
    surfacing, not one to paper over by guessing - guessing is how one
    merchant's webhook gets attributed to another."""
    svc.ensure_evo_entity(db_session, stripe_account_id="acct_evo")

    assert svc.entity_for_stripe_account(db_session, "acct_someone_else") is None
    assert svc.entity_for_stripe_account(db_session, "acct_evo") is not None


def test_an_entity_with_no_account_matches_no_webhook(db_session):
    """'Not configured' must not read as 'matches anything'."""
    entity = svc.ensure_evo_entity(db_session)
    assert entity.stripe_account_id is None
    assert svc.stripe_account_matches(entity, "acct_anything") is False


# ── existing production rows keep working ────────────────────────────────────

def test_a_platform_that_predates_the_column_still_resolves(db_session):
    """NULLABLE AND NOT BACKFILLED. Every platform row in production today has
    no merchant_entity_id, and must keep behaving exactly as it did before the
    column existed."""
    entity = svc.ensure_evo_entity(db_session)
    legacy = _platform(db_session, "legacy", "Legacy Brand")
    assert legacy.merchant_entity_id is None

    assert svc.resolve_for_platform(db_session, legacy).id == entity.id


def test_an_organization_with_no_brand_still_resolves(db_session):
    """Most organization rows predate multi-brand and have platform_id NULL.
    That is not an error state."""
    entity = svc.ensure_evo_entity(db_session)
    org = _org(db_session, "No Brand Org", None)
    assert org.platform_id is None

    assert svc.resolve_for_organization(db_session, org).id == entity.id


def test_no_entity_at_all_returns_none_rather_than_inventing_one(db_session):
    """The state P0 shipped in. issuer_snapshot returning None is how a caller
    can tell, instead of being handed an issuer nobody configured."""
    org = _org(db_session, "Orphan", None)

    snapshot = svc.issuer_snapshot(db_session, org)
    assert snapshot["merchant_entity_id"] is None
    assert snapshot["merchant_legal_name"] is None


# ── the configuration in operation today ─────────────────────────────────────

def test_evosys_pro_configuration_can_be_represented(db_session):
    """EVO INTEGRATED SOLUTIONS LLC sells EvoSys Pro to Restland."""
    _platform(db_session, "evosyspro", "EvoSys Pro")

    entity, platform = svc.ensure_evosys_pro_configuration(
        db_session, stripe_account_id="acct_evo_live")

    assert entity.legal_name == "EVO INTEGRATED SOLUTIONS LLC"
    assert entity.slug == EVO_SLUG
    assert entity.stripe_account_id == "acct_evo_live"
    assert entity.stripe_config_status == STRIPE_CONFIGURED
    assert platform.name == "EvoSys Pro"
    assert platform.merchant_entity_id == entity.id

    org = _org(db_session, "Restland", platform)
    snapshot = svc.issuer_snapshot(db_session, org)
    assert snapshot["merchant_legal_name"] == "EVO INTEGRATED SOLUTIONS LLC"
    assert snapshot["brand_name"] == "EvoSys Pro"
    assert snapshot["merchant_entity_id"] == entity.id
    assert snapshot["platform_id"] == platform.id


def test_seeding_is_idempotent(db_session):
    _platform(db_session, "evosyspro", "EvoSys Pro")
    first, _ = svc.ensure_evosys_pro_configuration(db_session)
    second, _ = svc.ensure_evosys_pro_configuration(db_session)

    assert first.id == second.id
    assert db_session.query(MerchantEntity).count() == 1


def test_seeding_does_not_overwrite_details_an_operator_filled_in(db_session):
    entity = svc.ensure_evo_entity(db_session)
    entity.address_line1 = "123 Main St"
    entity.billing_email = "billing@example.com"
    db_session.commit()

    svc.ensure_evo_entity(db_session)

    db_session.refresh(entity)
    assert entity.address_line1 == "123 Main St"
    assert entity.billing_email == "billing@example.com"


def test_missing_brand_returns_none_rather_than_manufacturing_one(db_session):
    """A brand is provisioned by the platform tooling. Inventing one here would
    manufacture an identity nobody approved."""
    entity, platform = svc.ensure_evosys_pro_configuration(db_session)

    assert entity is not None
    assert platform is None


def test_at_most_one_default_entity(db_session):
    first = svc.ensure_evo_entity(db_session)
    assert first.is_default is True

    second = MerchantEntity(slug="second", legal_name="SECOND LLC")
    db_session.add(second)
    db_session.commit()
    svc.set_default(db_session, second)

    db_session.refresh(first)
    assert first.is_default is False
    assert db_session.query(MerchantEntity).filter(
        MerchantEntity.is_default.is_(True)).count() == 1


# ── deployment mechanism ─────────────────────────────────────────────────────

def test_merchant_entities_is_registered_for_create_all():
    """A model module the registry does not import is a table create_all()
    never builds - the exact failure app/models/registry.py exists to prevent."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "models" / "registry.py").read_text(encoding="utf-8")
    assert "import app.models.billing_entity_models" in src


def test_the_one_existing_table_column_is_in_auto_migrate():
    """merchant_entities is a NEW table and create_all() builds it. The link
    column sits on platforms, which already exists, so it is the only part of
    P1 that needs auto_migrate - and without the entry it would exist in the
    model and not in the database."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "auto_migrate.py").read_text(encoding="utf-8")
    assert '("platforms", "merchant_entity_id", "VARCHAR")' in src
