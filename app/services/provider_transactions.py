"""Writing and reading the provider transaction record.

ONE WRITE PATH. Every provider answer lands here and nowhere else, so the
guarantee "no sensitive field is ever persisted" is enforced in one function
rather than remembered in ten call sites.
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.provider_transaction_models import ProviderTransaction
from app.services.providers import base


def _json(value: Any, where: str) -> Optional[str]:
    """Serialize for storage, refusing anything sensitive.

    REFUSES, does not strip. A silent redaction here would mean a new field
    name upstream leaks until somebody reads the table; a raised error means
    the test suite says so on the day it is added.
    """
    if value is None:
        return None
    base.assert_no_sensitive(value, where)
    try:
        return json.dumps(value, default=str)[:20000]
    except Exception:  # noqa: BLE001
        return json.dumps({"unserializable": str(type(value))})


def record(db: Session, *, organization_id: str, result: base.ProviderResult,
           provider: str, environment: Optional[str] = None,
           lead_id: Optional[str] = None,
           actor_user_id: Optional[str] = None,
           product_id=None, esiid=None, utility_id=None,
           service_type=None, service_start_date=None,
           plan_snapshot: Optional[Dict[str, Any]] = None,
           commit: bool = True) -> ProviderTransaction:
    """Persist one provider answer.

    `needs_human` is derived from the normalized status, not passed in: the
    set of states that need a person is a property of the vocabulary, and a
    caller that could override it would eventually override it wrongly.
    """
    now = datetime.utcnow()
    row = ProviderTransaction(
        organization_id=organization_id,
        lead_id=lead_id,
        actor_user_id=actor_user_id,
        provider=provider,
        operation=result.operation,
        environment=environment,
        product_id=str(product_id) if product_id is not None else None,
        esiid=str(esiid) if esiid is not None else None,
        utility_id=str(utility_id) if utility_id is not None else None,
        service_type=service_type,
        service_start_date=service_start_date,
        provider_reference=result.reference_number,
        raw_status=result.raw_status,
        raw_message=result.raw_message,
        normalized_status=result.normalized_status,
        reason_code=result.reason,
        amount_quoted=result.amount_quoted,
        http_status=result.http_status,
        needs_human=result.needs_human,
        request_echo=_json((result.detail or {}).get("request"), "request_echo"),
        response_body=_json(result.data, "response_body"),
        plan_snapshot=_json(plan_snapshot, "plan_snapshot"),
        submitted_at=now,
        last_provider_update_at=now,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def apply_result(db: Session, row: ProviderTransaction,
                 result: base.ProviderResult, *, commit: bool = True):
    """Update an existing transaction with a LATER provider answer.

    THIS IS THE SEAM THE MISSING STATUS MECHANISM WILL USE. Chariot's Broker
    API v1.0 documents no webhook and no status lookup, so today nothing
    calls this except a human reconciling by hand. When a status endpoint or
    a callback appears, it produces a ProviderResult and calls this — the row,
    the reference number and the history stay exactly where they are.

    THE REFERENCE NUMBER IS NEVER OVERWRITTEN WITH NOTHING. A later answer
    that omits it does not erase the one we hold; that value is unrecoverable.
    """
    if result.reference_number:
        row.provider_reference = result.reference_number
    row.raw_status = result.raw_status or row.raw_status
    row.raw_message = result.raw_message or row.raw_message
    row.normalized_status = result.normalized_status
    row.reason_code = result.reason
    row.amount_quoted = result.amount_quoted or row.amount_quoted
    row.http_status = result.http_status or row.http_status
    row.needs_human = result.needs_human
    row.attempt_count = (row.attempt_count or 1) + 1
    row.last_provider_update_at = datetime.utcnow()
    if result.data is not None:
        row.response_body = _json(result.data, "response_body")
    if commit:
        db.commit()
    return row


def resolve(db: Session, row: ProviderTransaction, *, user_id: str,
            note: str, commit: bool = True):
    """A HUMAN closes it. Nothing else may.

    Not a status change: `normalized_status` still says what the provider
    said. This records that a person has dealt with it, which is a different
    fact and the only one a human is entitled to assert.
    """
    row.needs_human = False
    row.resolved_at = datetime.utcnow()
    row.resolved_by_user_id = user_id
    row.resolution_note = (note or "")[:2000]
    if commit:
        db.commit()
    return row


def open_transactions(db: Session, organization_id: str, *, limit: int = 100):
    """The reconciliation queue: everything still owed an answer, oldest first.

    Oldest first because the number that matters is how long a customer has
    been waiting without knowing it.
    """
    return (db.query(ProviderTransaction)
            .filter(ProviderTransaction.organization_id == organization_id,
                    ProviderTransaction.needs_human == True)  # noqa: E712
            .order_by(ProviderTransaction.submitted_at.asc())
            .limit(limit).all())


def pending_provider(db: Session, organization_id: str, *, limit: int = 100):
    """Accepted by the provider, outcome unknown, and no API will ever say.

    Separate from `open_transactions` because these are not anybody's
    mistake — they are the documented consequence of a provider with no
    status mechanism, and they need chasing rather than fixing.
    """
    return (db.query(ProviderTransaction)
            .filter(ProviderTransaction.organization_id == organization_id,
                    ProviderTransaction.normalized_status == base.PENDING_PROVIDER,
                    ProviderTransaction.resolved_at.is_(None))
            .order_by(ProviderTransaction.submitted_at.asc())
            .limit(limit).all())
