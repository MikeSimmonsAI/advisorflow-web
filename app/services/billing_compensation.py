"""
The bridge from a Stripe payment to the EXISTING compensation engine.

═══════════════════════════════════════════════════════════════════════════
THIS FILE CONTAINS NO COMMISSION MATH. NOT ONE LINE.
═══════════════════════════════════════════════════════════════════════════

There is ONE compensation engine - app/services/compensation.py - and it owns
rate resolution, the upline walk, per-rule and per-deal caps, the holdback and
the snapshot. A second implementation would be a second answer to "what are we
paying this person", and the two would eventually disagree in a way nobody
notices until someone is underpaid.

So the whole job of this module is RESOLUTION and REFUSAL:

    resolve   which sold deal a Stripe payment belongs to
    refuse    clearly, and in writing, when it cannot tell
    call      compensation.earn() with a stable reference, and nothing else

═══════════════════════════════════════════════════════════════════════════
THE CHAIN, AND WHY IT IS AWKWARD
═══════════════════════════════════════════════════════════════════════════

    Stripe payment
      -> stripe_customer_id  ->  Organization
      -> Implementation.organization_id
      -> Implementation.opportunity_id
      -> Opportunity          (what compensation.earn needs)

`Organization` has no opportunity_id. The customer-tenant tree and the
brand-sales tree meet in exactly one place - `implementations`, plus
`Opportunity.customer_organization_id` - and that is deliberate, so the join
has to go the long way round. Any shortcut here would be a fabricated
relationship between a paying customer and a salesperson's commission, which
is the worst possible thing to guess about.

═══════════════════════════════════════════════════════════════════════════
NOT EVERY PAYMENT EARNS COMMISSION
═══════════════════════════════════════════════════════════════════════════

A Stripe invoice being paid is not, by itself, evidence that anybody is owed
anything. Every one of these is a legitimate reason to earn nothing:

  - the organization was never sold by anyone (self-serve signup, migration)
  - no Implementation links it to an Opportunity
  - the Opportunity is not Won
  - no compensation plan is configured for that brand
  - no rule covers this package or this payment type

In every case the answer is ZERO, with the reason recorded on the payment row,
NEVER an invented rule. Growth and Professional direct-sale compensation are
deliberately UNCONFIGURED; a payment against those must earn nothing and say
so, not fall back to Starter's numbers or to a percentage someone assumed.

═══════════════════════════════════════════════════════════════════════════
DUPLICATE PROTECTION, THREE DEEP
═══════════════════════════════════════════════════════════════════════════

One card charge can produce four Stripe events - invoice.paid,
invoice.payment_succeeded, payment_intent.succeeded, charge.succeeded. Paying
commission on each would pay four times for one payment.

  1. billing_events.stripe_event_id UNIQUE   a retried EVENT is a no-op
  2. billing_payments.collection_reference UNIQUE
                                             related events collapse to ONE
                                             payment, because the reference is
                                             derived from the invoice or
                                             payment intent, not the event
  3. compensation_entries UNIQUE (opportunity, payee, level,
     collection_reference)                   even a direct double-call to
                                             earn() returns the existing rows

The same `collection_reference` string is used for the local payment row and
for the compensation entry, on purpose: the two cannot disagree about how many
payments there were, because they are keyed on the same value.

A MANUAL COLLECTION AND A STRIPE COLLECTION CANNOT DOUBLE UP EITHER, provided
whoever records the manual one uses the Stripe id as the reference - and
`collection_reference_for()` below is the one place that string is built, so
there is a single spelling of it to match.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import BillingPayment
from app.models.models import Organization
from app.services import compensation as comp

log = logging.getLogger(__name__)


# Reasons compensation was not earned. Strings, stored on the payment row and
# surfaced in God Mode. "Nobody was paid" and "nobody should have been paid"
# look identical without one of these.
SKIP_NO_ORG = "no_organization_resolved"
SKIP_NO_IMPLEMENTATION = "no_implementation_links_this_customer_to_a_deal"
SKIP_NO_OPPORTUNITY = "implementation_has_no_opportunity"
SKIP_NOT_WON = "opportunity_is_not_won"
SKIP_NO_RULE = "no_configured_compensation_rule_resolves"
SKIP_ZERO_AMOUNT = "payment_amount_was_not_positive"
SKIP_ERROR = "resolution_error"


def collection_reference_for(*, stripe_invoice_id: Optional[str] = None,
                             stripe_payment_intent_id: Optional[str] = None,
                             stripe_charge_id: Optional[str] = None) -> Optional[str]:
    """THE ONE PLACE a Stripe payment's stable identity is spelled.

    Preference order is invoice, then payment intent, then charge, and it is an
    ORDER rather than a choice: for subscription billing every related event
    carries the same invoice id, so keying on the invoice is what collapses
    `invoice.paid`, `invoice.payment_succeeded`, `payment_intent.succeeded` and
    `charge.succeeded` into one payment. Keying on whichever id happened to be
    in the event being processed would produce a different reference per event
    and defeat the entire dedup design.

    The `stripe:` prefix makes the origin obvious wherever the reference is
    displayed - on a compensation entry, in an audit log, in a payables export
    - so nobody has to guess whether a reference came from Stripe or from
    somebody typing a cheque number.
    """
    for value in (stripe_invoice_id, stripe_payment_intent_id, stripe_charge_id):
        if value:
            return "stripe:%s" % value
    return None


def resolve_opportunity(db: Session, org: Organization):
    """Organization -> Implementation -> Opportunity, or (None, reason).

    Never invents a link. Returns the reason so the caller can record WHY
    nothing was earned, which is the difference between a deliberate zero and
    an unnoticed bug.
    """
    from app.models.implementation_models import Implementation

    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id)
            .order_by(Implementation.created_at.desc())
            .first())
    if impl is None:
        return None, SKIP_NO_IMPLEMENTATION

    opp_id = getattr(impl, "opportunity_id", None)
    if not opp_id:
        return None, SKIP_NO_OPPORTUNITY

    from app.models.sales_models import Opportunity
    opp = db.query(Opportunity).filter(Opportunity.id == opp_id).first()
    if opp is None:
        return None, SKIP_NO_OPPORTUNITY

    if (opp.status or "").lower() != "won":
        # A payment arriving against a deal that is not Won is a real
        # situation - the deal may have been reopened, or provisioned early.
        # It is not an error and it is not a commission.
        return None, SKIP_NOT_WON

    return opp, None


def earn_for_payment(db: Session, payment: BillingPayment, *,
                     commit: bool = True) -> dict:
    """Attempt to earn compensation for one recorded Stripe payment.

    ALWAYS RETURNS. Never raises for a business reason - "nobody is owed
    anything for this payment" is an ordinary outcome, and a webhook that 500s
    on it would have Stripe retry the event forever.

    Writes the outcome onto the payment row either way, so the answer to "why
    did this payment not pay a commission" lives next to the payment rather
    than only in a log line that has since rotated away.
    """
    result = {"earned": False, "entries": 0, "reason": None,
              "opportunity_id": None}

    if payment.amount_cents is None or payment.amount_cents <= 0:
        payment.compensation_skipped_reason = SKIP_ZERO_AMOUNT
        result["reason"] = SKIP_ZERO_AMOUNT
        if commit:
            db.commit()
        return result

    org = (db.query(Organization)
           .filter(Organization.id == payment.organization_id)
           .first())
    if org is None:
        payment.compensation_skipped_reason = SKIP_NO_ORG
        result["reason"] = SKIP_NO_ORG
        if commit:
            db.commit()
        return result

    try:
        opp, reason = resolve_opportunity(db, org)
    except Exception:
        # A resolution failure must not take the webhook down with it. Record
        # it and move on; the payment itself is still correctly banked.
        log.exception("billing_compensation: resolving opportunity for org %s", org.id)
        payment.compensation_skipped_reason = SKIP_ERROR
        result["reason"] = SKIP_ERROR
        if commit:
            db.commit()
        return result

    if opp is None:
        payment.compensation_skipped_reason = reason
        result["reason"] = reason
        if commit:
            db.commit()
        return result

    payment.opportunity_id = opp.id
    result["opportunity_id"] = opp.id

    # Cents -> dollars for the engine, which works in Decimal dollars
    # throughout. Decimal, never float: a float here becomes a rounding error
    # in somebody's payout.
    collected = (Decimal(int(payment.amount_cents)) / Decimal(100))

    try:
        entries = comp.earn(
            db, opp,
            collection_reference=payment.collection_reference,
            collected_amount=collected,
            collected_at=payment.collected_at or datetime.utcnow(),
            commit=False,
        )
    except comp.NotEarnable as exc:
        # THE IMPORTANT BRANCH. NotEarnable is the engine saying, correctly,
        # that no configured rule covers this - an unconfigured brand plan, a
        # package with no rule, a deal that is not Won. It is NOT an error to
        # route around, and it is NOT an invitation to compute something here.
        # Record the engine's own words and earn nothing.
        payment.compensation_skipped_reason = "%s: %s" % (SKIP_NO_RULE, exc)
        result["reason"] = SKIP_NO_RULE
        result["detail"] = str(exc)
        if commit:
            db.commit()
        return result
    except Exception:
        log.exception("billing_compensation: earn() failed for payment %s",
                      payment.id)
        payment.compensation_skipped_reason = SKIP_ERROR
        result["reason"] = SKIP_ERROR
        if commit:
            db.commit()
        return result

    if not entries:
        # The plan resolved but produced no payouts - e.g. a rule set with no
        # rule matching this basis, or an upline level nobody occupies. Zero is
        # the right answer and it is recorded as a real outcome.
        payment.compensation_skipped_reason = SKIP_NO_RULE
        result["reason"] = SKIP_NO_RULE
        if commit:
            db.commit()
        return result

    payment.earned_compensation = True
    payment.compensation_skipped_reason = None
    result["earned"] = True
    result["entries"] = len(entries)

    if commit:
        db.commit()
    return result


def record_refund(db: Session, payment: BillingPayment, *,
                  refunded_cents: int, refunded_at: Optional[datetime] = None,
                  commit: bool = True) -> dict:
    """Record that money went back. DOES NOT TOUCH COMPENSATION.

    A refund or chargeback is recorded against the payment and reported. Paid
    compensation history is not rewritten, because clawback policy has
    deliberately not been decided and because a paid compensation entry is a
    record of money that actually left the business and reached a person.
    Editing it to match a later reversal would make the ledger disagree with
    the bank, silently.

    When a policy exists it will be append-only adjustments referencing this
    payment, the Stripe reversal and the original entry - a new row saying the
    money came back, never an edit to the row that says it went out.
    """
    payment.refunded_cents = int(refunded_cents or 0)
    payment.refunded_at = refunded_at or datetime.utcnow()
    if commit:
        db.commit()
    return {
        "recorded": True,
        "refunded_cents": payment.refunded_cents,
        "compensation_adjusted": False,
        "reason": "clawback_policy_required",
        "explanation": (
            "The refund is recorded against the payment. Compensation already "
            "earned or paid is deliberately unchanged: no clawback policy has "
            "been configured, and paid compensation is never rewritten in "
            "place."
        ),
    }
