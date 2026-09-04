"""Resolving WHO ISSUES a charge, and seeding the entity in operation today.

THE ONE QUESTION THIS ANSWERS

    Given an organization, which legal entity issues its invoices?

and it answers it by walking the hierarchy rather than by storing the answer
twice:

    Organization.platform_id -> Platform.merchant_entity_id -> MerchantEntity

An organization does NOT carry its own merchant_entity_id. It could have, and
that would have been one fewer join - but then moving a customer between brands
would leave the issuer pointing at the old one, and nothing would notice until
an invoice went out in the wrong company's name. Derived beats duplicated here.

WHAT THIS IS NOT

Not authorization. `resolve_for_organization` answers a question about billing
identity; it does not decide who may ask. Every caller is still behind whatever
guard its route already has, and nothing here reads current_user. The Phase 3
tenant-authorization work is a separate dependency - see the note in
tests/test_merchant_entity.py.

Not a Stripe client. Nothing here calls Stripe. `stripe_config_status` records
what the application has been TOLD; confirming it against the live account is a
later phase's job and has its own status value waiting (STRIPE_VERIFIED).
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.billing_entity_models import (EVO_LEGAL_NAME, EVO_SLUG,
                                              EVOSYS_PRO_PLATFORM_SLUG,
                                              ENTITY_LLC, MerchantEntity,
                                              STRIPE_CONFIGURED,
                                              STRIPE_UNCONFIGURED)
from app.models.models import Organization, Platform


def get_by_slug(db: Session, slug: str) -> Optional[MerchantEntity]:
    return db.query(MerchantEntity).filter(MerchantEntity.slug == slug).first()


def get_default(db: Session) -> Optional[MerchantEntity]:
    """The entity to fall back on when a brand names none.

    Ordered by is_default so a correctly-configured install returns the marked
    row, and an install where nobody marked one still returns SOMETHING active
    rather than None - which would silently produce invoices with no issuer.
    """
    return (db.query(MerchantEntity)
            .filter(MerchantEntity.is_active.is_(True))
            .order_by(MerchantEntity.is_default.desc(),
                      MerchantEntity.created_at.asc())
            .first())


def set_default(db: Session, entity: MerchantEntity) -> MerchantEntity:
    """Mark one entity as the default and clear the flag from every other.

    AT MOST ONE DEFAULT is enforced here rather than by a partial unique index,
    because a partial index does not exist on the SQLite the tests run against
    and a rule that only holds in production is not a rule. Going through this
    function is therefore the only supported way to set the flag.
    """
    for other in db.query(MerchantEntity).filter(
            MerchantEntity.is_default.is_(True),
            MerchantEntity.id != entity.id).all():
        other.is_default = False
    entity.is_default = True
    db.commit()
    db.refresh(entity)
    return entity


def resolve_for_platform(db: Session, platform: Optional[Platform]
                         ) -> Optional[MerchantEntity]:
    """The entity behind a brand, falling back to the default."""
    if platform is not None and getattr(platform, "merchant_entity_id", None):
        entity = (db.query(MerchantEntity)
                  .filter(MerchantEntity.id == platform.merchant_entity_id)
                  .first())
        if entity is not None:
            return entity
    return get_default(db)


def resolve_for_organization(db: Session, organization: Optional[Organization]
                             ) -> Optional[MerchantEntity]:
    """The entity that issues this customer's invoices.

    An organization with no platform_id is not an error - most rows predate
    multi-brand - so it resolves to the default entity, which is exactly what
    was true before P1 existed.
    """
    if organization is None:
        return None
    platform = None
    if getattr(organization, "platform_id", None):
        platform = (db.query(Platform)
                    .filter(Platform.id == organization.platform_id).first())
    return resolve_for_platform(db, platform)


def issuer_snapshot(db: Session, organization: Optional[Organization]) -> dict:
    """The issuer fields to freeze onto an invoice at the moment it is issued.

    Returns the four columns P0 already declared on Invoice. Both the ids and
    the human-readable names are captured: the ids so the row can be joined
    later, the NAMES so the invoice still reads correctly after a rename. A
    paid invoice must keep saying what was true when it was paid, and a join
    alone cannot promise that.

    Every value may be None. An organization with no brand and an install with
    no entity is the state P0 shipped in, and this returning None for those is
    how a caller can tell rather than being handed an invented issuer.
    """
    platform = None
    if organization is not None and getattr(organization, "platform_id", None):
        platform = (db.query(Platform)
                    .filter(Platform.id == organization.platform_id).first())
    entity = resolve_for_organization(db, organization)
    return {
        "merchant_entity_id": entity.id if entity else None,
        "merchant_legal_name": entity.legal_name if entity else None,
        "platform_id": platform.id if platform else None,
        "brand_name": platform.name if platform else None,
    }


def stripe_account_matches(entity: MerchantEntity, account_id: str) -> bool:
    """Whether a webhook claiming this account belongs to this entity.

    An entity with no account id recorded matches NOTHING. Treating "not
    configured" as "matches anything" is the shape of mistake that lets one
    merchant's webhook be attributed to another the day a second entity exists.
    """
    if not account_id or not entity or not entity.stripe_account_id:
        return False
    return entity.stripe_account_id == account_id


def entity_for_stripe_account(db: Session, account_id: str
                              ) -> Optional[MerchantEntity]:
    """Which entity owns a Stripe account id, or None. Never falls back to the
    default - an unrecognised account is a fact worth surfacing, not one to
    paper over by guessing."""
    if not account_id:
        return None
    return (db.query(MerchantEntity)
            .filter(MerchantEntity.stripe_account_id == account_id).first())


# ── Seeding the entity in operation today ───────────────────────────────────

def ensure_evo_entity(db: Session,
                      stripe_account_id: Optional[str] = None
                      ) -> MerchantEntity:
    """Create or update EVO INTEGRATED SOLUTIONS LLC. Idempotent.

    Called explicitly - never on import - so that importing a model never
    writes to a database. Safe to run repeatedly: an existing row is returned
    with its legal name corrected if it drifted, and nothing else is
    overwritten, so an operator who filled in the address does not lose it on
    the next call.

    The Stripe account id is OPTIONAL and is only recorded when supplied by the
    caller from the environment. P1 does not read Stripe configuration itself
    and does not call Stripe.
    """
    entity = get_by_slug(db, EVO_SLUG)
    if entity is None:
        entity = MerchantEntity(
            slug=EVO_SLUG,
            legal_name=EVO_LEGAL_NAME,
            display_name="Evo Integrated Solutions",
            entity_type=ENTITY_LLC,
            address_country="US",
            is_active=True,
            stripe_config_status=STRIPE_UNCONFIGURED,
        )
        db.add(entity)
    else:
        entity.legal_name = EVO_LEGAL_NAME

    if stripe_account_id:
        entity.stripe_account_id = stripe_account_id
        entity.stripe_config_status = STRIPE_CONFIGURED

    db.commit()
    db.refresh(entity)
    if not db.query(MerchantEntity).filter(
            MerchantEntity.is_default.is_(True)).first():
        set_default(db, entity)
    return entity


def link_platform(db: Session, platform: Platform,
                  entity: MerchantEntity) -> Platform:
    """Point a brand at the legal entity that sells it. Idempotent."""
    platform.merchant_entity_id = entity.id
    db.commit()
    db.refresh(platform)
    return platform


def ensure_evosys_pro_configuration(db: Session,
                                    stripe_account_id: Optional[str] = None
                                    ) -> tuple:
    """The configuration in operation today: EvoSys Pro sold by EVO.

    Returns (entity, platform). The platform is looked up, never created - a
    brand is provisioned by the platform tooling and inventing one here would
    manufacture an identity. A missing brand returns (entity, None) so the
    caller can see that rather than being handed a row that does not represent
    anything.
    """
    entity = ensure_evo_entity(db, stripe_account_id=stripe_account_id)
    platform = (db.query(Platform)
                .filter(Platform.slug == EVOSYS_PRO_PLATFORM_SLUG).first())
    if platform is not None:
        link_platform(db, platform, entity)
    return entity, platform
