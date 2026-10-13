"""CHARIOT ENROLLMENT WORKFLOW: one submission per person, recorded before it
is sent, and closed only by a person.

What Chariot told Atlantis (Tim Richmond, Oct 6 2026), and what each fact
forces here:

* RESIDENTIAL ONLY. `energy_enrollment.build_enrollment_payload` refuses any
  other classification before anything is assembled.
* UP TO 120 SECONDS. The adapter waits 125 s; a timeout comes back pending
  with "outcome unknown - do not resubmit".
* NO STATUS ENDPOINT, NO WEBHOOK, NO REFERENCE LOOKUP after SubmitEnrollment,
  and the final documents go to the customer's own email. So no answer from
  the API is ever treated as a finished enrollment: EVERY submission lands in
  the reconciliation queue, and only a person marks it enrolled, after
  checking with the customer or Chariot's weekly promo-code report.
* PENDING FRAUD REVIEW (~48 h, may never resolve) is just another pending row.

DUPLICATES. A second submission for the same person or the same meter while
an earlier one is unresolved - or after one that may have gone through - is
refused. The row is written BEFORE the call (status pending, reason
"submitting"), so two clicks, two tabs or a crash mid-call still leave
exactly one attempt on record, and that attempt reads as "outcome unknown".
A person can clear the way for a retry only by recording, on the earlier
row, that Chariot did NOT receive it.

DISARMED. Nothing here reaches Chariot unless CHARIOT_ALLOW_TRANSACTIONS is
set on the server; until then a submission is refused before any row is
written.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.provider_transaction_models import ProviderTransaction
from app.services import energy_enrollment as ee
from app.services import provider_transactions as ptx
from app.services.providers import base, chariot

PROVIDER = chariot.PROVIDER

REASON_SUBMITTING = "submitting"

# What a person can record when they close a submission.
OUTCOME_ENROLLED = "enrolled"                 # confirmed with customer / Chariot report
OUTCOME_NOT_ENROLLED = "not_enrolled"         # Chariot declined or customer cancelled
OUTCOME_NOT_RECEIVED = "not_received"         # Chariot never got it: a retry is allowed
OUTCOMES = (OUTCOME_ENROLLED, OUTCOME_NOT_ENROLLED, OUTCOME_NOT_RECEIVED)


class SubmissionRefused(ValueError):
    """The submission is not allowed right now. The message says why."""


def _blocking_row(db: Session, org_id: str, lead_id: Optional[str],
                  esiid: Optional[str], operation: str) -> Optional[ProviderTransaction]:
    """An earlier attempt that forbids another one: anything unresolved, or
    resolved as anything other than "Chariot never received it"."""
    q = db.query(ProviderTransaction).filter(
        ProviderTransaction.organization_id == org_id,
        ProviderTransaction.provider == PROVIDER,
        ProviderTransaction.operation == operation)
    keys = []
    if lead_id:
        keys.append(ProviderTransaction.lead_id == lead_id)
    if esiid:
        keys.append(ProviderTransaction.esiid == str(esiid))
    if not keys:
        return None
    from sqlalchemy import or_
    for row in q.filter(or_(*keys)).order_by(ProviderTransaction.submitted_at.desc()).all():
        if row.resolved_at is None:
            return row
        if (row.resolution_note or "").startswith("[%s]" % OUTCOME_NOT_RECEIVED):
            continue
        if row.normalized_status in (base.FAILED_VALIDATION,):
            continue        # our request was malformed; nothing was enrolled
        return row
    return None


def submit_enrollment(db: Session, *, organization_id: str, lead, actor_user_id: str,
                      classification: str, fields: Dict[str, Any],
                      plan_snapshot: Optional[Dict[str, Any]] = None) -> ProviderTransaction:
    """Submit one residential enrollment. Returns the recorded row.

    Order matters and is the whole design: refuse what is not allowed; write
    the row; then call. `fields` holds identity data for the length of this
    function only and is never stored."""
    if not chariot.transactions_armed():
        raise SubmissionRefused("Chariot enrollment is switched off on this server. "
                                "Nothing was sent.")
    payload, audit = ee.build_enrollment_payload(classification=classification, fields=fields)
    esiid = audit.get("ESIID")
    lead_id = getattr(lead, "id", None)
    earlier = _blocking_row(db, organization_id, lead_id, esiid, base.OP_SUBMIT_ENROLLMENT)
    if earlier is not None:
        raise SubmissionRefused(
            "An enrollment for this person or meter was already submitted on %s and has not "
            "been cleared. Confirm it with the customer or Chariot first; nothing was sent."
            % earlier.submitted_at.strftime("%Y-%m-%d %H:%M UTC"))

    claim = base.ProviderResult(operation=base.OP_SUBMIT_ENROLLMENT,
                                normalized_status=base.PENDING_PROVIDER,
                                reason=REASON_SUBMITTING)
    row = ptx.record(db, organization_id=organization_id, result=claim, provider=PROVIDER,
                     environment=chariot.environment(), lead_id=lead_id,
                     actor_user_id=actor_user_id, product_id=audit.get("ProductID"),
                     esiid=esiid, service_type=audit.get("ServiceType"),
                     service_start_date=audit.get("ServiceStartDate"),
                     plan_snapshot=plan_snapshot, commit=True)
    row.needs_human = True
    db.commit()

    try:
        result = chariot.submit_enrollment(payload)
    except Exception as exc:  # noqa: BLE001 - the row stays "outcome unknown"
        result = base.ProviderResult(operation=base.OP_SUBMIT_ENROLLMENT,
                                     normalized_status=base.PENDING_PROVIDER,
                                     reason=chariot.REASON_OUTCOME_UNKNOWN,
                                     raw_message=str(exc)[:300])
    finally:
        payload = None   # noqa: F841 - identity data goes no further
    ptx.apply_result(db, row, result, commit=False)
    row.attempt_count = 1            # one wire attempt, whatever apply_result counted
    # No API answer closes an enrollment: a person confirms every one.
    row.needs_human = True
    db.commit()
    return row


def submit_renewal(db: Session, *, organization_id: str, lead, actor_user_id: str,
                   account_number: str, product_id) -> ProviderTransaction:
    """SubmitRenewal: same rules. Chariot: it "succeeds if not rejected by
    validation", so a success is still confirmed by a person."""
    if not chariot.transactions_armed():
        raise SubmissionRefused("Chariot renewal is switched off on this server. Nothing was sent.")
    lead_id = getattr(lead, "id", None)
    earlier = _blocking_row(db, organization_id, lead_id, None, base.OP_SUBMIT_RENEWAL)
    if earlier is not None:
        raise SubmissionRefused("A renewal for this person was already submitted and has not "
                                "been cleared. Nothing was sent.")
    claim = base.ProviderResult(operation=base.OP_SUBMIT_RENEWAL,
                                normalized_status=base.PENDING_PROVIDER, reason=REASON_SUBMITTING)
    row = ptx.record(db, organization_id=organization_id, result=claim, provider=PROVIDER,
                     environment=chariot.environment(), lead_id=lead_id,
                     actor_user_id=actor_user_id, product_id=product_id, commit=True)
    row.needs_human = True
    db.commit()
    try:
        result = chariot.submit_renewal(account_number, product_id)
    except Exception as exc:  # noqa: BLE001
        result = base.ProviderResult(operation=base.OP_SUBMIT_RENEWAL,
                                     normalized_status=base.PENDING_PROVIDER,
                                     reason=chariot.REASON_OUTCOME_UNKNOWN,
                                     raw_message=str(exc)[:300])
    ptx.apply_result(db, row, result, commit=False)
    row.attempt_count = 1
    row.needs_human = True
    db.commit()
    return row


def reconcile(db: Session, row: ProviderTransaction, *, user_id: str, outcome: str,
              note: str) -> ProviderTransaction:
    """A person closes a submission with what they verified.

    `normalized_status` keeps what Chariot said; the outcome a person
    verified is recorded beside it, prefixed on the note so it can be read
    back without a schema change."""
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be one of %s" % ", ".join(OUTCOMES))
    if not (note or "").strip():
        raise ValueError("Say how it was verified (customer call, Chariot report, ...).")
    ptx.resolve(db, row, user_id=user_id, note="[%s] %s" % (outcome, note.strip()), commit=False)
    db.commit()
    return row


def queue(db: Session, organization_id: str, *, limit: int = 100):
    """Every Chariot submission still waiting for a person, oldest first."""
    return (db.query(ProviderTransaction)
            .filter(ProviderTransaction.organization_id == organization_id,
                    ProviderTransaction.provider == PROVIDER,
                    ProviderTransaction.operation.in_(base.TRANSACTIONAL_OPERATIONS),
                    ProviderTransaction.resolved_at.is_(None))
            .order_by(ProviderTransaction.submitted_at.asc()).limit(limit).all())


def row_out(row: ProviderTransaction) -> Dict[str, Any]:
    outcome = None
    note = row.resolution_note or ""
    if note.startswith("["):
        outcome = note[1:note.find("]")]
    return {
        "id": row.id, "lead_id": row.lead_id, "operation": row.operation,
        "environment": row.environment, "product_id": row.product_id, "esiid": row.esiid,
        "service_type": row.service_type, "service_start_date": row.service_start_date,
        "provider_reference": row.provider_reference,
        "provider_said": row.raw_status, "provider_message": row.raw_message,
        "status": row.normalized_status, "reason": row.reason_code,
        "amount_quoted": row.amount_quoted,
        "submitted_at": row.submitted_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.submitted_at else None,
        "resolved_at": row.resolved_at.strftime("%Y-%m-%dT%H:%M:%SZ") if row.resolved_at else None,
        "verified_outcome": outcome,
        "waiting_hours": (round((datetime.utcnow() - row.submitted_at).total_seconds() / 3600, 1)
                          if row.submitted_at and not row.resolved_at else None),
    }
