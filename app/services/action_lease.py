"""Leases: "this exact action is in progress" - visible to every instance.

    token = action_lease.acquire(db, "email.send", key, ttl_seconds=120)
    if token is None:
        -> somebody (any instance) is doing this right now: refuse / 409
    try:
        ... the side effect ...
    finally:
        action_lease.release(db, "email.send", key, token)

THE CLAIM IS ITS OWN TRANSACTION. It is written and committed on a separate
session bound to the same database, so another instance sees it immediately -
before this request reaches the provider - and the caller's own transaction is
not committed early by it.

ATOMIC TAKE-OVER OF AN EXPIRED LEASE. A holder that died leaves a row behind.
The next caller takes it over with a CONDITIONAL update (`WHERE expires_at <
now`): of two callers racing for the same stale lease exactly one updates a
row; the other sees rowcount 0 and is refused.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.idempotency_models import ActionLease

log = logging.getLogger(__name__)


def _own_session(db: Session) -> Session:
    return Session(bind=db.get_bind())


def acquire(db: Session, scope: str, key: str, ttl_seconds: int = 120,
            now: Optional[datetime] = None) -> Optional[str]:
    now = now or datetime.utcnow()
    token = uuid.uuid4().hex
    k = (key or "")[:300]
    s = _own_session(db)
    try:
        s.add(ActionLease(scope=scope, key=k, holder=token, acquired_at=now,
                          expires_at=now + timedelta(seconds=ttl_seconds)))
        s.commit()
        return token
    except IntegrityError:
        s.rollback()
        res = s.execute(update(ActionLease)
                        .where(ActionLease.scope == scope, ActionLease.key == k,
                               ActionLease.expires_at < now)
                        .values(holder=token, acquired_at=now,
                                expires_at=now + timedelta(seconds=ttl_seconds)))
        s.commit()
        return token if res.rowcount == 1 else None
    finally:
        s.close()


def release(db: Session, scope: str, key: str, token: Optional[str]) -> None:
    if not token:
        return
    s = _own_session(db)
    try:
        s.execute(delete(ActionLease).where(ActionLease.scope == scope,
                                            ActionLease.key == (key or "")[:300],
                                            ActionLease.holder == token))
        s.commit()
    except Exception:                                            # noqa: BLE001
        s.rollback()
        log.exception("could not release lease %s/%s (it will expire)", scope, key)
    finally:
        s.close()


def held(db: Session, scope: str, key: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    return db.query(ActionLease.id).filter(ActionLease.scope == scope,
                                           ActionLease.key == (key or "")[:300],
                                           ActionLease.expires_at >= now).first() is not None
