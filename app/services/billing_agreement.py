"""Turning approved deal intent into an executable billing agreement.

THE CONVERSION THIS OWNS

    Implementation (approved, Numeric, sales vocabulary)
            |
            |  copy - never recompute
            v
    BillingAgreement (executable, integer minor units)

Two rules govern every line below.

COPY, NEVER RECOMPUTE. The Implementation's amounts were approved when the deal
was provisioned and are deliberately frozen against later catalogue movement.
Re-deriving them here from package_pricing - or worse from the legacy PLANS
dictionary - would silently reprice a customer the moment a package changed,
which is the single failure this whole layer exists to prevent. Nothing in this
module reads a price list.

REFUSE RATHER THAN INVENT. An Implementation with no approved recurring amount
does not become an agreement with a guessed one. It raises. A billing system
that fills in a blank is a billing system that charges somebody the wrong
number, and the blank is the signal that a human has not finished the deal.

IDEMPOTENCY

`create_from_implementation` is safe to call repeatedly. Provisioning gets
retried - a double-clicked button, a re-run job, a webhook replay - and the
unique constraint on `implementation_id` is the backstop, but this returns the
existing row before reaching it so a retry is a no-op rather than an
IntegrityError the caller has to interpret.

SUPERSESSION, NOT MUTATION

`supersede` creates the NEW agreement and links it. It never edits the old
one's terms. The old row keeps its amounts, its dates and its Stripe
references exactly as they were, because invoices already issued against it
have to keep making sense.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.billing_agreement_models import (AGREEMENT_ACTIVE,
                                                 AGREEMENT_CANCELLED,
                                                 AGREEMENT_DRAFT,
                                                 AGREEMENT_ENDED,
                                                 AGREEMENT_LIVE_STATUSES,
                                                 AGREEMENT_SUPERSEDED,
                                                 BILLING_INTERVALS,
                                                 INTERVAL_MONTH,
                                                 SOURCE_IMPLEMENTATION,
                                                 BillingAgreement)
from app.models.models import Organization, Platform
from app.services import merchant_entity as entity_svc
from app.services.money import to_cents


class AgreementRefused(ValueError):
    """The deal does not contain what an executable agreement requires.

    A distinct type so a caller can tell "this deal is not ready" apart from a
    programming error, and so no caller is tempted to except-and-continue past
    a missing price.
    """


def _package_name(db: Session, package_id: Optional[str]) -> Optional[str]:
    if not package_id:
        return None
    from app.models.sales_models import BrandPackage
    pkg = (db.query(BrandPackage)
           .filter(BrandPackage.id == package_id).first())
    return pkg.name if pkg else None


def get_for_implementation(db: Session, implementation_id: str
                           ) -> Optional[BillingAgreement]:
    if not implementation_id:
        return None
    return (db.query(BillingAgreement)
            .filter(BillingAgreement.implementation_id == implementation_id)
            .first())


def current_for_organization(db: Session, organization_id: str
                             ) -> Optional[BillingAgreement]:
    """The agreement governing this customer's billing right now.

    PAST_DUE counts as live - see AGREEMENT_LIVE_STATUSES. A customer whose
    card failed is still on their agreement, and treating them as having none
    is how a failed payment becomes an unbilled account.
    """
    return (db.query(BillingAgreement)
            .filter(BillingAgreement.organization_id == organization_id,
                    BillingAgreement.status.in_(AGREEMENT_LIVE_STATUSES))
            .order_by(BillingAgreement.created_at.desc())
            .first())


def history_for_organization(db: Session, organization_id: str) -> list:
    """Every agreement this customer has ever had, newest first."""
    return (db.query(BillingAgreement)
            .filter(BillingAgreement.organization_id == organization_id)
            .order_by(BillingAgreement.created_at.desc())
            .all())


def create_from_implementation(db: Session, implementation,
                               created_by: Optional[str] = None,
                               activate: bool = False) -> BillingAgreement:
    """Build the executable agreement for an approved, provisioned deal.

    Idempotent: an existing agreement for this implementation is returned
    untouched. Raises AgreementRefused when the deal carries no approved
    recurring amount and no approved setup fee - an agreement that charges
    nothing is not a thing worth creating silently.
    """
    existing = get_for_implementation(db, implementation.id)
    if existing is not None:
        return existing

    org = (db.query(Organization)
           .filter(Organization.id == implementation.organization_id).first())
    if org is None:
        raise AgreementRefused(
            "Implementation %s has no organization; there is nobody to bill."
            % implementation.id)

    currency = getattr(implementation, "currency", None) or "USD"
    recurring_cents = to_cents(
        getattr(implementation, "recurring_amount", None), currency)
    setup_cents = to_cents(
        getattr(implementation, "implementation_fee", None), currency)

    if not recurring_cents and not setup_cents:
        raise AgreementRefused(
            "Implementation %s has neither an approved recurring amount nor an "
            "approved implementation fee. The deal's pricing has not been "
            "completed, and billing must not invent one." % implementation.id)

    platform = None
    if getattr(implementation, "platform_id", None):
        platform = (db.query(Platform)
                    .filter(Platform.id == implementation.platform_id).first())
    if platform is None and getattr(org, "platform_id", None):
        platform = (db.query(Platform)
                    .filter(Platform.id == org.platform_id).first())
    entity = entity_svc.resolve_for_platform(db, platform)

    # Approved custom pricing, copied from the Opportunity the deal came from.
    # Read-only, and absent when the deal was a straight package sale.
    opp = None
    if getattr(implementation, "opportunity_id", None):
        from app.models.sales_models import Opportunity
        opp = (db.query(Opportunity)
               .filter(Opportunity.id == implementation.opportunity_id).first())

    custom_unit_cents = None
    unit_label = None
    min_units = None
    if opp is not None:
        custom_unit_cents = to_cents(
            getattr(opp, "custom_unit_price", None), currency)
        unit_label = getattr(opp, "custom_unit_label", None)
        min_units = getattr(opp, "custom_min_units", None)

    agreement = BillingAgreement(
        organization_id=org.id,
        merchant_entity_id=entity.id if entity else None,
        platform_id=platform.id if platform else None,
        implementation_id=implementation.id,
        opportunity_id=getattr(implementation, "opportunity_id", None),
        package_id=getattr(implementation, "package_id", None),
        source=SOURCE_IMPLEMENTATION,
        status=AGREEMENT_ACTIVE if activate else AGREEMENT_DRAFT,
        currency=currency,
        setup_fee_cents=setup_cents,
        recurring_amount_cents=recurring_cents,
        billing_interval=INTERVAL_MONTH,
        billing_option=getattr(implementation, "billing_option", None),
        contract_term_months=getattr(implementation, "contract_term_months", None),
        quantity=min_units,
        unit_label=unit_label,
        min_units=min_units,
        custom_unit_price_cents=custom_unit_cents,
        has_custom_pricing=bool(custom_unit_cents),
        billing_start_date=getattr(implementation, "billing_start_date", None),
        trial_start=getattr(implementation, "trial_start", None),
        trial_end=getattr(implementation, "trial_end", None),
        # The customer's Stripe reference AS IT IS NOW. If the organization's
        # Stripe customer is later replaced, this agreement must keep naming
        # the customer it was actually executed against.
        stripe_customer_id=getattr(org, "stripe_customer_id", None),
        # ── the snapshot, written once ──
        merchant_legal_name=entity.legal_name if entity else None,
        brand_name=platform.name if platform else None,
        organization_name=org.name,
        package_name=_package_name(db, getattr(implementation, "package_id", None)),
        created_by=created_by,
        activated_at=datetime.utcnow() if activate else None,
    )
    db.add(agreement)
    db.commit()
    db.refresh(agreement)
    return agreement


def activate(db: Session, agreement: BillingAgreement) -> BillingAgreement:
    """Move a draft agreement into force. Idempotent."""
    if agreement.status == AGREEMENT_ACTIVE:
        return agreement
    if agreement.status in (AGREEMENT_SUPERSEDED, AGREEMENT_CANCELLED,
                            AGREEMENT_ENDED):
        raise AgreementRefused(
            "Agreement %s is %s and cannot be activated. Create a new "
            "agreement instead - a closed one is history and history is not "
            "edited." % (agreement.id, agreement.status))
    agreement.status = AGREEMENT_ACTIVE
    agreement.activated_at = agreement.activated_at or datetime.utcnow()
    db.commit()
    db.refresh(agreement)
    return agreement


def cancel(db: Session, agreement: BillingAgreement,
           reason: Optional[str] = None) -> BillingAgreement:
    """End an agreement without a replacement. Terms are left untouched."""
    if agreement.status == AGREEMENT_CANCELLED:
        return agreement
    agreement.status = AGREEMENT_CANCELLED
    agreement.cancelled_at = datetime.utcnow()
    agreement.ended_at = agreement.ended_at or datetime.utcnow()
    if reason:
        agreement.supersede_reason = reason
    db.commit()
    db.refresh(agreement)
    return agreement


def supersede(db: Session, old: BillingAgreement,
              new_terms: dict,
              reason: str = "replacement",
              created_by: Optional[str] = None) -> BillingAgreement:
    """Replace an agreement with a new one, preserving the old one exactly.

    The old row's AMOUNTS, DATES AND STRIPE REFERENCES ARE NOT TOUCHED. Only
    its status, its ended_at and its forward link change - everything an
    already-issued invoice might refer to stays as it was.

    `new_terms` carries only what differs. Anything absent is inherited from
    the agreement being replaced, so a renewal that changes one number does not
    require the caller to restate the whole deal and risk mistyping the rest.
    """
    inherited = dict(
        organization_id=old.organization_id,
        merchant_entity_id=old.merchant_entity_id,
        platform_id=old.platform_id,
        implementation_id=None,   # the unique constraint belongs to the old row
        opportunity_id=old.opportunity_id,
        package_id=old.package_id,
        source=old.source,
        currency=old.currency,
        setup_fee_cents=None,     # a replacement does not re-charge setup
        recurring_amount_cents=old.recurring_amount_cents,
        billing_interval=old.billing_interval,
        billing_option=old.billing_option,
        contract_term_months=old.contract_term_months,
        quantity=old.quantity,
        unit_label=old.unit_label,
        min_units=old.min_units,
        custom_unit_price_cents=old.custom_unit_price_cents,
        has_custom_pricing=old.has_custom_pricing,
        billing_start_date=old.billing_start_date,
        stripe_customer_id=old.stripe_customer_id,
        merchant_legal_name=old.merchant_legal_name,
        brand_name=old.brand_name,
        organization_name=old.organization_name,
        package_name=old.package_name,
    )
    inherited.update(new_terms or {})

    new = BillingAgreement(
        status=AGREEMENT_ACTIVE,
        supersedes_id=old.id,
        supersede_reason=reason,
        created_by=created_by,
        activated_at=datetime.utcnow(),
        **inherited)
    db.add(new)
    db.flush()

    old.status = AGREEMENT_SUPERSEDED
    old.superseded_by_id = new.id
    old.ended_at = old.ended_at or datetime.utcnow()
    db.commit()
    db.refresh(new)
    return new


def attach_stripe_subscription(db: Session, agreement: BillingAgreement,
                               subscription_id: str,
                               price_id: Optional[str] = None
                               ) -> BillingAgreement:
    """Record which Stripe subscription executes this agreement.

    Refuses to REPLACE an existing, different subscription reference. Two
    subscriptions silently pointing at one agreement - or an agreement quietly
    re-pointed at a different one - is a double-charge waiting to be
    discovered on a customer's statement rather than here.
    """
    if not subscription_id:
        raise AgreementRefused("No subscription id supplied.")
    if (agreement.stripe_subscription_id
            and agreement.stripe_subscription_id != subscription_id):
        raise AgreementRefused(
            "Agreement %s is already executed by subscription %s. Supersede it "
            "rather than re-pointing an agreement at a different subscription."
            % (agreement.id, agreement.stripe_subscription_id))
    agreement.stripe_subscription_id = subscription_id
    if price_id:
        agreement.stripe_price_id = price_id
    db.commit()
    db.refresh(agreement)
    return agreement
