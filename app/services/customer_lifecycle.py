"""CANCELLING A CUSTOMER. WHICH IS NOT DELETING ONE.

THE RULE THIS MODULE EXISTS TO ENFORCE

A customer leaving is a commercial event. It changes their STATUS and records
WHEN and WHY. It does not touch the originating opportunity, the proposal or
its version, the pricing snapshot on the implementation, the commission already
earned or paid, the leads, the users, or the audit trail. Every one of those is
the record of something that really happened, and a business that destroys them
when a customer leaves cannot answer a question about its own past.

So there is no code path below that deletes anything. Not one.

WHAT EACH ACTION ACTUALLY DOES
------------------------------
  request_cancellation   records the intent, the reason and the date service
                         is due to end. Changes NOTHING about access: a notice
                         period is normal and cutting a customer off the moment
                         somebody clicks Cancel would break agreements we are
                         still being paid under.
  start_offboarding      marks the leaving work as underway. Still no access
                         change — exports usually need the workspace open.
  complete_cancellation  the relationship is over. THIS is where workspace
                         access closes, and even that is a suspension
                         (`is_active = False`), which is reversible and leaves
                         memberships intact.
  archive                a VIEW decision. Files a cancelled customer out of
                         everyday lists. Destroys nothing and is reversible.
  reactivate             they came back. The history of them leaving stays.

WHAT THIS MODULE WILL NOT PRETEND
---------------------------------
Offboarding in a real business touches billing, integrations, communications
providers and running automations. This codebase does not have a subscription
engine, and nothing here can cancel an invoice or stop a payment that a system
we do not own is going to take. Rather than quietly implying those were handled,
`manual_offboarding_steps()` returns the list of things a HUMAN still has to do,
and the confirmation screen shows it. A checklist that admits what it cannot do
is worth more than an automation that silently does not run.

CONTRACTUAL OBLIGATIONS ARE NOT ERASED BY A CLICK. Where the sale recorded a
term, the remaining months are stated back to the operator before they confirm,
and recorded on the event. This module makes no attempt to decide whether that
money is still owed — that is a commercial and legal judgement nobody has
configured here, and inventing a rule for it would be worse than showing the
facts and letting a person decide.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.customer_lifecycle_models import (
    CANCELLATION_REASONS, CUST_ACTIVE, CUST_ARCHIVED, CUST_CANCELLATION_REQUESTED,
    CUST_CANCELLED, CUST_OFFBOARDING, CUSTOMER_STATUS_LABELS,
    EVENT_ARCHIVED, EVENT_CANCELLATION_REQUESTED, EVENT_CANCELLED,
    EVENT_OFFBOARDING_STARTED, EVENT_REACTIVATED, CustomerLifecycleEvent,
    may_transition)
from app.models.implementation_models import Implementation
from app.models.models import Lead, Organization, User
from app.routers.audit_log_router import log_action
from app.services import customer_360 as c360

log = logging.getLogger(__name__)


def _audit(db: Session, org: Organization, actor: User, action: str,
           before: Optional[dict], after: Optional[dict],
           note: Optional[str] = None) -> None:
    """Control-plane audit, in the ONE audit table.

    Written alongside the lifecycle event, not instead of it. The audit log
    answers "what did somebody do to the system"; the lifecycle event answers
    "what happened to this customer" and is what a churn report reads. Losing
    either would leave a real question unanswerable.
    """
    log_action(db, org.id, actor.id, action=action,
               target_type="organization", target_id=org.id,
               platform_id=org.platform_id,
               before=before, after=after, note=note, commit=False)


def _record(db: Session, org: Organization, actor: User, *, event: str,
            from_status: str, to_status: str,
            effective_at: Optional[datetime] = None,
            reason: Optional[str] = None, note: Optional[str] = None,
            obligations_note: Optional[str] = None) -> CustomerLifecycleEvent:
    row = CustomerLifecycleEvent(
        organization_id=org.id, event=event, from_status=from_status,
        to_status=to_status, effective_at=effective_at, reason=reason,
        note=note, obligations_note=obligations_note,
        actor_user_id=actor.id)
    db.add(row)
    return row


def _guard(org: Organization, target: str) -> str:
    """Refuse a transition that makes no sense, and say which one it was."""
    current = c360.status_of(org)
    if current == target:
        raise HTTPException(
            status_code=409,
            detail="This customer is already %s."
                   % CUSTOMER_STATUS_LABELS.get(target, target).lower())
    if not may_transition(current, target):
        raise HTTPException(
            status_code=409,
            detail="A customer that is %s cannot become %s."
                   % (CUSTOMER_STATUS_LABELS.get(current, current).lower(),
                      CUSTOMER_STATUS_LABELS.get(target, target).lower()))
    return current


# ── what a human still has to do ────────────────────────────────────────────

def manual_offboarding_steps(db: Session, org: Organization) -> List[Dict[str, Any]]:
    """The offboarding work this platform CANNOT do for you.

    Every entry is something real that exists for this customer and that no code
    path here touches. Showing an empty automation checklist would be worse than
    showing nothing; showing this is the honest version.
    """
    steps: List[Dict[str, Any]] = []

    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id).first())

    if impl is not None and impl.billing_status not in (None, "not_configured"):
        steps.append({
            "key": "billing",
            "label": "Stop billing",
            "detail": ("Billing for this customer is recorded as '%s'. Nothing "
                       "in this platform charges anybody — the record is intent "
                       "only — so any live subscription, invoice or payment "
                       "schedule must be stopped where it actually runs."
                       % impl.billing_status),
        })
    if impl is not None and impl.external_billing_ref:
        steps.append({
            "key": "external_billing",
            "label": "Cancel the external billing record",
            "detail": ("This customer is linked to external billing reference "
                       "%s. That system is not reachable from here."
                       % impl.external_billing_ref),
        })

    user_count = db.query(User).filter(User.organization_id == org.id).count()
    if user_count:
        steps.append({
            "key": "users",
            "label": "Confirm what happens to %d user account%s"
                     % (user_count, "" if user_count == 1 else "s"),
            "detail": ("Completing the cancellation suspends the workspace, so "
                       "nobody can enter it. The accounts themselves are kept, "
                       "along with their memberships, so the customer can be "
                       "reactivated without rebuilding their team."),
        })

    lead_count = db.query(Lead).filter(Lead.organization_id == org.id).count()
    if lead_count:
        steps.append({
            "key": "data",
            "label": "Offer a data export before access closes",
            "detail": ("This customer has %d lead records. They are retained, "
                       "not deleted, but the workspace is the only place they "
                       "can be exported from." % lead_count),
        })

    # Communications and integrations are per-organization credentials. Naming
    # them individually would mean listing every provider this codebase has ever
    # integrated; naming the category is what an operator actually needs.
    if getattr(org, "resend_api_key", None) or getattr(org, "from_email", None):
        steps.append({
            "key": "comms",
            "label": "Retire the sending identity",
            "detail": ("This customer sends from their own address. Suspension "
                       "stops the app, but a sending domain and any provider "
                       "credentials are managed outside it."),
        })

    steps.append({
        "key": "sequences",
        "label": "Check for running automations",
        "detail": ("Suspending the workspace closes the doors; it is not a "
                   "guarantee that every scheduled job for this customer has "
                   "been drained. Confirm nothing is still due to send."),
    })
    return steps


def contractual_position(db: Session, org: Organization) -> Dict[str, Any]:
    """What was agreed, and how much of it is left. FACTS, NOT A JUDGEMENT.

    This does not decide whether the remaining months are owed. That depends on
    the agreement, on notice, and on decisions nobody has configured here, and a
    rule invented in this file would be applied to real money.
    """
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id).first())
    com = c360.commercials(impl)
    out = {
        "structure": com["structure"],
        "term_months": com["term_months"],
        "mrr": com["mrr"],
        "total_contract_value": com["total_contract_value"],
        "months_elapsed": None,
        "months_remaining": None,
        "note": None,
    }

    if com["structure"] != c360.STRUCTURE_TERM or impl is None:
        out["note"] = ("No fixed term was recorded for this customer, so there "
                       "is no remaining contract length to state."
                       if com["structure"] != c360.STRUCTURE_TERM else None)
        return out

    started = impl.launched_at or impl.billing_start_date or impl.created_at
    if started is not None:
        elapsed = max(0, int((datetime.utcnow() - started).days // 30))
        out["months_elapsed"] = elapsed
        out["months_remaining"] = max(0, int(com["term_months"]) - elapsed)
        out["note"] = (
            "This customer committed to %d months and roughly %d %s elapsed. "
            "Whether the remainder is still owed is a commercial decision this "
            "platform does not make and has not been configured to make."
            % (com["term_months"], elapsed,
               "month has" if elapsed == 1 else "months have"))
    return out


def offboarding_preview(db: Session, org: Organization) -> Dict[str, Any]:
    """WHAT CANCELLING WILL AND WILL NOT DO. Shown before anybody confirms."""
    return {
        "organization_id": org.id,
        "name": org.name,
        "current_status": c360.status_of(org),
        "workspace_active": bool(org.is_active),
        "will_happen": [
            "The customer's status changes, and the date and reason are recorded.",
            "A lifecycle event is written and the action is audited.",
            "When the cancellation is COMPLETED, the workspace is suspended so "
            "nobody can enter it.",
        ],
        "will_not_happen": [
            "Nothing is deleted. Not the customer, not their leads, not their users.",
            "The originating opportunity, the proposal and its version, and the "
            "pricing agreed at the sale are all preserved.",
            "Commission already earned, payable or paid is untouched.",
            "Memberships are kept, so the customer can be reactivated without "
            "rebuilding their team.",
        ],
        "manual_steps": manual_offboarding_steps(db, org),
        "contract": contractual_position(db, org),
    }


# ── the transitions ─────────────────────────────────────────────────────────

def request_cancellation(db: Session, org: Organization, actor: User, *,
                         reason: Optional[str] = None,
                         effective_at: Optional[datetime] = None,
                         note: Optional[str] = None,
                         obligations_note: Optional[str] = None) -> Dict[str, Any]:
    """Record that a customer is leaving. Changes no access whatsoever."""
    if reason is not None and reason not in CANCELLATION_REASONS:
        raise HTTPException(status_code=400,
                            detail="Unknown cancellation reason %r." % reason)
    was = _guard(org, CUST_CANCELLATION_REQUESTED)
    before = {"lifecycle_status": was, "is_active": bool(org.is_active)}

    org.lifecycle_status = CUST_CANCELLATION_REQUESTED
    org.cancellation_requested_at = datetime.utcnow()
    org.cancellation_requested_by = actor.id
    org.cancellation_effective_at = effective_at
    org.cancellation_reason = reason
    org.cancellation_note = note

    _record(db, org, actor, event=EVENT_CANCELLATION_REQUESTED,
            from_status=was, to_status=CUST_CANCELLATION_REQUESTED,
            effective_at=effective_at, reason=reason, note=note,
            obligations_note=obligations_note)
    _audit(db, org, actor, "customer.cancellation_requested", before,
           {"lifecycle_status": CUST_CANCELLATION_REQUESTED,
            "effective_at": str(effective_at) if effective_at else None,
            "reason": reason},
           note="Access unchanged; service continues until completion.")
    db.commit()
    return c360.customer_360(db, org)


def start_offboarding(db: Session, org: Organization, actor: User, *,
                      note: Optional[str] = None) -> Dict[str, Any]:
    was = _guard(org, CUST_OFFBOARDING)
    before = {"lifecycle_status": was}
    org.lifecycle_status = CUST_OFFBOARDING
    _record(db, org, actor, event=EVENT_OFFBOARDING_STARTED,
            from_status=was, to_status=CUST_OFFBOARDING, note=note)
    _audit(db, org, actor, "customer.offboarding_started", before,
           {"lifecycle_status": CUST_OFFBOARDING}, note=note)
    db.commit()
    return c360.customer_360(db, org)


def complete_cancellation(db: Session, org: Organization, actor: User, *,
                          effective_at: Optional[datetime] = None,
                          note: Optional[str] = None) -> Dict[str, Any]:
    """The relationship ends. The record does not.

    This is the ONLY transition that touches access, and it suspends rather than
    revokes: `is_active = False` closes the workspace while leaving every
    membership in place, which is what makes reactivation possible without
    rebuilding the customer's team.
    """
    was = _guard(org, CUST_CANCELLED)
    before = {"lifecycle_status": was, "is_active": bool(org.is_active)}

    org.lifecycle_status = CUST_CANCELLED
    org.cancelled_at = datetime.utcnow()
    if effective_at is not None:
        org.cancellation_effective_at = effective_at
    org.is_active = False

    _record(db, org, actor, event=EVENT_CANCELLED, from_status=was,
            to_status=CUST_CANCELLED,
            effective_at=effective_at or org.cancellation_effective_at,
            reason=org.cancellation_reason, note=note)
    _audit(db, org, actor, "customer.cancelled", before,
           {"lifecycle_status": CUST_CANCELLED, "is_active": False},
           note="Workspace suspended. No records were deleted.")
    db.commit()
    return c360.customer_360(db, org)


def archive(db: Session, org: Organization, actor: User, *,
            note: Optional[str] = None) -> Dict[str, Any]:
    """File a customer out of everyday lists. A VIEW decision, not a data one.

    Everything remains and remains findable: archived customers are excluded
    from the default filter and returned the moment somebody asks for them.
    """
    was = _guard(org, CUST_ARCHIVED)
    before = {"lifecycle_status": was, "is_active": bool(org.is_active)}
    org.lifecycle_status = CUST_ARCHIVED
    org.archived_at = datetime.utcnow()
    org.archived_by = actor.id
    # Archiving an ACTIVE customer closes their workspace too — otherwise a
    # customer nobody is watching keeps running. Archiving one already cancelled
    # changes nothing, because completion already suspended them.
    org.is_active = False
    _record(db, org, actor, event=EVENT_ARCHIVED, from_status=was,
            to_status=CUST_ARCHIVED, note=note)
    _audit(db, org, actor, "customer.archived", before,
           {"lifecycle_status": CUST_ARCHIVED, "is_active": False},
           note="Filed out of active views. Every record retained.")
    db.commit()
    return c360.customer_360(db, org)


def reactivate(db: Session, org: Organization, actor: User, *,
               note: Optional[str] = None) -> Dict[str, Any]:
    """They came back. The record of them leaving stays exactly where it is.

    The cancellation dates and reason are deliberately NOT cleared: "this
    customer churned in March and returned in June" is a fact about them, and
    wiping it would make the return look like it never happened.
    """
    was = _guard(org, CUST_ACTIVE)
    before = {"lifecycle_status": was, "is_active": bool(org.is_active)}
    org.lifecycle_status = CUST_ACTIVE
    org.reactivated_at = datetime.utcnow()
    org.is_active = True
    _record(db, org, actor, event=EVENT_REACTIVATED, from_status=was,
            to_status=CUST_ACTIVE, note=note)
    _audit(db, org, actor, "customer.reactivated", before,
           {"lifecycle_status": CUST_ACTIVE, "is_active": True},
           note="Prior cancellation history retained.")
    db.commit()
    return c360.customer_360(db, org)
