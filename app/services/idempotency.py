"""Database-backed "at most once" for side effects. See app/models/idempotency_models.py.

    if not idempotency.claim(db, scope, key):
        return already_done()

`claim` runs inside a SAVEPOINT, so losing the race rolls back only the claim
row, never the caller's surrounding transaction. It flushes; the caller's
commit makes the claim durable together with whatever the claim protects - if
that work fails and the caller rolls back, the claim goes with it and a retry
is allowed, which is exactly right: nothing happened, so it may happen now.

An empty or missing key means "the caller has no identity for this occurrence"
and is always allowed through - refusing it would turn a form that forgot to
send a submission id into a form that can never be submitted.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.idempotency_models import IdempotencyKey

log = logging.getLogger(__name__)

MAX_KEY = 200


def _norm(key: Optional[str]) -> str:
    return (str(key).strip() if key is not None else "")[:MAX_KEY]


def claim(db: Session, scope: str, key: Optional[str], *,
          organization_id: Optional[str] = None) -> bool:
    """True if this caller is the first for (scope, key); False if it already happened."""
    k = _norm(key)
    if not k:
        return True
    nested = db.begin_nested()
    try:
        db.add(IdempotencyKey(scope=scope, key=k, organization_id=organization_id))
        db.flush()
        nested.commit()
        return True
    except IntegrityError:
        nested.rollback()
        return False


def seen(db: Session, scope: str, key: Optional[str]) -> Optional[IdempotencyKey]:
    k = _norm(key)
    if not k:
        return None
    return (db.query(IdempotencyKey)
            .filter(IdempotencyKey.scope == scope, IdempotencyKey.key == k)
            .first())


def remember(db: Session, scope: str, key: Optional[str], result: Any) -> None:
    """Attach a small JSON summary to a claimed key, for replays to return."""
    row = seen(db, scope, key)
    if row is None:
        return
    try:
        row.result = json.dumps(result, default=str)[:4000]
    except (TypeError, ValueError):
        log.warning("idempotency result for %s not serialisable", scope)


def previous_result(db: Session, scope: str, key: Optional[str]) -> Optional[Any]:
    row = seen(db, scope, key)
    if row is None or not row.result:
        return None
    try:
        return json.loads(row.result)
    except (TypeError, ValueError):
        return None
