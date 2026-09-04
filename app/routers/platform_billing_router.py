"""P7 — the back-office Billing Command Center's HTTP surface.

ITS OWN ROUTER, ITS OWN PREFIX, ITS OWN AUTHORITY

`/platform/billing/*`, not `/billing/*`. The separation is not tidiness: the
customer surface and this one answer to different authorities, and putting them
in one file is how a route eventually gets the wrong dependency by being copied
from the one above it. P6's routes resolve a BillingScope from the caller's
active workspace; every route here requires god_admin AND the non-delegable
`platform_billing` capability, and none of them consults the caller's workspace
at all.

TWO REFUSALS, DELIBERATELY

`require_capability("platform_billing")` puts "master billing is God-only" in
the capability registry, where `set_self_management` refuses to delegate it. The
inline role check is the second: a customer org_admin, and a customer holding
`billing_view` or `billing_manage`, are refused here no matter what their own
workspace permits. Being able to manage your own billing has nothing to do with
being allowed to read another company's.

EVERY MUTATION IS P4's

Nothing here re-implements a Stripe call, a money rule or a duplicate guard.
The handlers select an organization, build a platform scope for it, and hand it
to the same `billing_operations` functions the customer surface uses - so the
subscription double-charge guard, the integer-minor-unit enforcement and the
ownership filters all still apply, and there is no second implementation to
keep in step.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.routers.audit_log_router import log_action
from app.services import billing_operations as ops
from app.services import billing_integrity as integrity
from app.services import platform_billing as pb
from app.services.billing_operations import BillingOperationRefused
from app.services.capabilities import require_capability
from app.services.billing_integrity import RepairRefused
from app.services.platform_billing import PlatformBillingRefused
from app.services.stripe_gateway import (LiveModeRefused,
                                         StripeOperationFailed,
                                         StripeUnavailable)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/platform/billing", tags=["platform-billing"])


def require_platform_billing(
    current_user: User = Depends(get_current_user),
    _cap: User = Depends(require_capability("platform_billing")),
) -> User:
    """Cross-organization billing authority. NOT customer billing authority.

    A customer org_admin holds billing authority over one tenant by membership.
    That is a statement about their own company's money and says nothing about
    whether they may read another company's invoices, so it grants nothing
    here. `platform_billing` is registered non-delegable, so no God screen can
    hand it to a customer either.
    """
    if current_user.role != "god_admin":
        # Deliberately the same answer a customer admin gets, and deliberately
        # not "you need god_admin": this surface is advertised to nobody, and
        # naming the level required would confirm a guess.
        raise HTTPException(status_code=403, detail="Not permitted.")
    return current_user


def _handle(fn, *args, **kwargs):
    """Translate service failures. Same mapping as the customer surface.

        refused         400  not valid for this data
        unavailable     503  Stripe unreachable - retryable
        live key        503  refused on purpose; this build is sandbox-only
        Stripe refusal  402  Stripe declined the operation itself
    """
    try:
        return fn(*args, **kwargs)
    except (PlatformBillingRefused, BillingOperationRefused,
            RepairRefused) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LiveModeRefused as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except StripeUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Billing is temporarily unavailable. %s" % exc)
    except StripeOperationFailed as exc:
        raise HTTPException(status_code=402, detail=str(exc))


def _audit(db, actor, org_id, action, target_type, target_id, details=None):
    """Record the action through the platform's EXISTING audit table.

    `log_action` already accepts a null organization for control-plane events
    and takes before/after; there is no reason for billing to grow its own
    audit framework. A failure to audit must not undo a Stripe operation that
    already happened, so this never raises - the alternative is a customer
    charged with no record, which is worse than a missing log line.
    """
    try:
        log_action(db, organization_id=org_id, actor_user_id=actor.id,
                   action=action, target_type=target_type,
                   target_id=str(target_id), details=details)
    except Exception:
        logger.warning("platform billing audit failed: %s %s/%s",
                       action, target_type, target_id, exc_info=True)


# ── the dashboard ───────────────────────────────────────────────────────────

@router.get("/command-center")
def command_center(limit: int = 500,
                   actor: User = Depends(require_platform_billing),
                   db: Session = Depends(get_db)):
    """The whole book in one request.

    ONE AGGREGATE, not a call per organization: the per-org version is slow at
    fifty customers and unusable at five hundred, and it invites the frontend
    to add up money. Reads the local mirror only, so it loads during a Stripe
    outage.
    """
    return _handle(pb.command_center, db, limit)


@router.get("/organizations")
def organizations(q: Optional[str] = None, status: str = "all",
                  platform_id: Optional[str] = None, limit: int = 200,
                  actor: User = Depends(require_platform_billing),
                  db: Session = Depends(get_db)):
    """Find an organization to work on, with the operational filters."""
    return _handle(pb.organizations, db, q, status, platform_id, limit)


@router.get("/organizations/{organization_id}")
def organization_detail(organization_id: str,
                        actor: User = Depends(require_platform_billing),
                        db: Session = Depends(get_db)):
    """One organization's billing in full, under platform authority.

    The operator does NOT switch their own workspace to get here. That is the
    P6 authority path and it is the wrong mechanism for administering somebody
    else's account.
    """
    return _handle(pb.organization_detail, db, organization_id)


# ── invoices ────────────────────────────────────────────────────────────────

class PlatformLineItem(BaseModel):
    # INTEGER MINOR UNITS. The type is the guard, at the edge, exactly as on
    # the customer surface - a float here is how a rounding error becomes
    # somebody's invoice.
    amount_cents: int
    description: Optional[str] = None
    currency: Optional[str] = None


class PlatformInvoiceRequest(BaseModel):
    line_items: List[PlatformLineItem]
    description: Optional[str] = None
    days_until_due: int = 30
    # THE OPERATOR CHOOSES A PURPOSE, NEVER A STRIPE PAYMENT METHOD.
    # "setup" and "manual" are business intents; which methods are eligible for
    # the resulting hosted invoice is Stripe's answer, from the account's own
    # payment method configuration. An operator should not have to know what a
    # payment method type is, and this application must not promise one Stripe
    # may refuse.
    purpose: str = "manual"
    request_id: Optional[str] = None


PURPOSES = {"setup": "one_time_setup", "manual": "manual_invoice"}


@router.post("/organizations/{organization_id}/invoices")
def create_invoice(organization_id: str, req: PlatformInvoiceRequest,
                   actor: User = Depends(require_platform_billing),
                   db: Session = Depends(get_db)):
    """Create a DRAFT invoice for one organization. Nothing is charged.

    Draft is the whole point: it stays reviewable and reversible until it is
    finalized, which is a separate deliberate call.

    THE AMOUNT HERE IS A MANUAL LINE ITEM AND IS LABELLED AS ONE. It does not
    read, and cannot change, the organization's BillingAgreement - the
    agreement remains the authority for recurring billing, and an invoice typed
    on this screen is exactly what it says it is.
    """
    if req.purpose not in PURPOSES:
        raise HTTPException(
            status_code=400,
            detail="purpose must be one of: %s" % ", ".join(sorted(PURPOSES)))
    org = _handle(pb.organization_or_refuse, db, organization_id)
    scope = pb.platform_scope(org)
    items = [{"amount_cents": i.amount_cents, "description": i.description,
              "currency": i.currency} for i in req.line_items]
    result = _handle(ops.create_draft_invoice, db, scope, items,
                     req.description, req.days_until_due, req.request_id)
    result["purpose"] = req.purpose
    result["payment_flow"] = PURPOSES[req.purpose]
    _audit(db, actor, org.id, "billing_invoice_created", "invoice",
           result.get("stripe_invoice_id"),
           {"purpose": req.purpose, "line_items": len(items),
            "days_until_due": req.days_until_due})
    return result


@router.post("/organizations/{organization_id}/invoices/{invoice_id}/finalize")
def finalize_invoice(organization_id: str, invoice_id: str,
                     actor: User = Depends(require_platform_billing),
                     db: Session = Depends(get_db)):
    """Turn a draft into a real financial document."""
    org = _handle(pb.organization_or_refuse, db, organization_id)
    result = _handle(ops.finalize_invoice, db, pb.platform_scope(org),
                     invoice_id)
    _audit(db, actor, org.id, "billing_invoice_finalized", "invoice",
           invoice_id, {"number": result.get("number")})
    return result


@router.post("/organizations/{organization_id}/invoices/{invoice_id}/send")
def send_invoice(organization_id: str, invoice_id: str,
                 actor: User = Depends(require_platform_billing),
                 db: Session = Depends(get_db)):
    """Ask STRIPE to email the invoice.

    Stripe already delivers invoices and hosts the payment page. Building a
    second email path here would mean two systems believing they had sent the
    same document, and only one of them tracking whether it was paid.
    """
    org = _handle(pb.organization_or_refuse, db, organization_id)
    result = _handle(ops.send_invoice, db, pb.platform_scope(org), invoice_id)
    _audit(db, actor, org.id, "billing_invoice_sent", "invoice", invoice_id,
           {"number": result.get("number")})
    return result


@router.post("/organizations/{organization_id}/invoices/{invoice_id}/void")
def void_invoice(organization_id: str, invoice_id: str,
                 actor: User = Depends(require_platform_billing),
                 db: Session = Depends(get_db)):
    org = _handle(pb.organization_or_refuse, db, organization_id)
    result = _handle(ops.void_invoice, db, pb.platform_scope(org), invoice_id)
    _audit(db, actor, org.id, "billing_invoice_voided", "invoice", invoice_id,
           {"number": result.get("number")})
    return result


# ── subscriptions ───────────────────────────────────────────────────────────

@router.post("/organizations/{organization_id}/agreements/{agreement_id}/subscribe")
def subscribe_agreement(organization_id: str, agreement_id: str,
                        actor: User = Depends(require_platform_billing),
                        db: Session = Depends(get_db)):
    """Execute a BillingAgreement as a Stripe subscription.

    THE DOUBLE-CHARGE GUARD IS P4's AND IS NOT RE-IMPLEMENTED HERE. An
    agreement that already names a subscription returns it with
    `created: false` before any Stripe call - so this route cannot open the
    path P4 closed, however many times an operator clicks. The screen shows an
    existing subscription and disables the control; this is the refusal behind
    it.
    """
    org = _handle(pb.organization_or_refuse, db, organization_id)
    result = _handle(ops.create_subscription, db, pb.platform_scope(org),
                     agreement_id)
    if result.get("created"):
        _audit(db, actor, org.id, "billing_subscription_started",
               "billing_agreement", agreement_id,
               {"subscription_id": result.get("subscription_id")})
    return result


class PlatformCancelRequest(BaseModel):
    # End of period by default: the customer paid for the period they are in.
    at_period_end: bool = True
    reason: Optional[str] = None


@router.post("/organizations/{organization_id}/agreements/{agreement_id}/cancel")
def cancel_subscription(organization_id: str, agreement_id: str,
                        req: PlatformCancelRequest = PlatformCancelRequest(),
                        actor: User = Depends(require_platform_billing),
                        db: Session = Depends(get_db)):
    org = _handle(pb.organization_or_refuse, db, organization_id)
    result = _handle(ops.cancel_subscription, db, pb.platform_scope(org),
                     agreement_id, req.at_period_end)
    _audit(db, actor, org.id, "billing_subscription_cancelled",
           "billing_agreement", agreement_id,
           {"at_period_end": req.at_period_end, "reason": req.reason,
            "subscription_id": result.get("subscription_id")})
    return result


@router.post("/organizations/{organization_id}/portal")
def open_portal(organization_id: str,
                actor: User = Depends(require_platform_billing),
                db: Session = Depends(get_db)):
    """A Stripe Billing Portal link for this organization's customer.

    For handing to the customer so THEY can update their payment method.
    Which methods may be saved for recurring collection is decided by Stripe
    inside that portal, from the account's payment method configuration - not
    by anything this application lists.
    """
    import os

    import stripe

    from app.services import stripe_gateway as gw

    org = _handle(pb.organization_or_refuse, db, organization_id)
    customer_id = getattr(org, "stripe_customer_id", None)
    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="This organization has no Stripe customer yet.")
    gw.client()  # configures the key and refuses a live one
    base_url = (os.environ.get("APP_BASE_URL", "").strip()
                or "https://advisorflow-frontend.onrender.com")
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id, return_url="%s/billing" % base_url)
    except Exception as exc:
        logger.warning("portal session failed for org=%s: %s", org.id, exc)
        raise HTTPException(status_code=502,
                            detail="Stripe could not open a billing portal "
                                   "session for this customer.")
    _audit(db, actor, org.id, "billing_portal_opened", "organization", org.id)
    return {"portal_url": session.url}


# ═════════════════════════════════════════════════════════════════════════════
# P8 — INTEGRITY, HEALTH AND REPAIR
#
# Read endpoints write nothing: `integrity.run` has no mutation path at all.
# The one write endpoint defaults to a dry run and refuses, structurally, any
# discrepancy whose resolution is a business decision - creating or cancelling
# a subscription, changing an amount, a currency, a term, a legal seller. Those
# are not implemented, which is a stronger guarantee than not being permitted.
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/integrity")
def billing_integrity(organization_id: Optional[str] = None,
                      include_stripe: bool = True, limit: int = 200,
                      actor: User = Depends(require_platform_billing),
                      db: Session = Depends(get_db)):
    """Does the mirror still agree with Stripe, and with itself.

    `include_stripe=false` runs the local-only checks, which is what a
    dashboard wants and what still works during a Stripe outage.

    THE RUN ITSELF IS AUDITED. Reading every customer's billing state is a
    privileged action even though it changes nothing, and an audit trail that
    records only writes cannot answer "who looked".
    """
    report = _handle(integrity.run, db, organization_id, include_stripe, limit)
    _audit(db, actor, organization_id, "billing_reconciliation_run",
           "organization", organization_id or "all",
           {"organizations_checked": report["organizations_checked"],
            "findings": report["total_findings"],
            "by_severity": report["by_severity"],
            "stripe_checked": report["stripe_checked"]})
    return report


@router.get("/webhook-health")
def stripe_webhook_health(window_hours: int = 168,
                          actor: User = Depends(require_platform_billing),
                          db: Session = Depends(get_db)):
    """Is Stripe's side of the conversation being heard.

    A stalled webhook pipeline is invisible from every other screen - nothing
    errors, the numbers simply stop moving. Counts, types, ages and error text
    only: never a stored event body, which carries customer payment detail, and
    never the signing secret, which is not in the database at all.
    """
    return _handle(integrity.webhook_health, db, 30, window_hours)


class RepairRequest(BaseModel):
    code: str
    target_id: str
    # DRY RUN IS THE DEFAULT AND HAS TO BE TURNED OFF DELIBERATELY. The
    # dangerous version of this endpoint is the one somebody runs across a
    # whole findings list to "clear the queue".
    apply: bool = False


@router.post("/integrity/repair")
def repair_discrepancy(req: RepairRequest,
                       actor: User = Depends(require_platform_billing),
                       db: Session = Depends(get_db)):
    """Apply ONE safe local-mirror repair, or show what it would do.

    Safe means Stripe is the authority, we already know what it says, and our
    row disagrees - so copying its answer corrects a record of money that
    already moved. Anything that would decide something is refused with a 400
    naming why.
    """
    result = _handle(integrity.apply_repair, db, req.code, req.target_id,
                     not req.apply)
    if result.get("applied"):
        _audit(db, actor, None, "billing_safe_repair_applied",
               DISCREPANCY_TARGETS.get(req.code, "billing"), req.target_id,
               {"code": req.code, "outcome": result.get("outcome")})
    return result


# What each repairable code acts on, for the audit trail's target_type. Kept
# here rather than in the service because it describes the audit vocabulary,
# not the repair.
DISCREPANCY_TARGETS = {
    "stale_invoice_status": "invoice",
    "missing_local_invoice": "stripe_invoice",
    "stale_org_billing_status": "organization",
    "unresolved_past_due": "organization",
    "recovered_but_past_due": "organization",
    "webhook_failed": "webhook_event",
    "webhook_stuck": "webhook_event",
}
