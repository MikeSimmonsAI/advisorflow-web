"""ONE ROW PER SIDE EFFECT THAT MUST HAPPEN AT MOST ONCE.

WHY A TABLE AND NOT A LOCK IN MEMORY
------------------------------------
Several duplicate guards on this platform were a Python set or a lock held by
one process ("the in-flight claim is per server instance"). That is correct
while Render runs one web instance and wrong the moment it runs two: each
instance has its own set, and both send.

A unique index is the only guard every instance shares. `claim()` in
app/services/idempotency.py INSERTs a (scope, key) row inside a savepoint; the
first writer wins and every later writer - on any instance, in any process -
hits the unique constraint and is told it lost. No polling, no clocks, no
"probably". Postgres and SQLite enforce it the same way, so the tests prove
the production behaviour.

WHAT A ROW MEANS
----------------
`scope` names the kind of side effect ("site.demo_request", "email.send",
...). `key` is the caller's identity for one occurrence of it - a form's
submission id, a provider message id, an outbox row id. `result` is an
optional small JSON summary so a replay can answer with what the first call
produced instead of doing the work again. `organization_id` is informational
(for support and cleanup); uniqueness is (scope, key) only, so a key can never
be "claimed twice, once per tenant".

Rows are never updated except to attach `result`, and never deleted by
application code.
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Index, String, Text, UniqueConstraint

from app.models.models import Base


def _uuid():
    return str(uuid.uuid4())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id = Column(String, primary_key=True, default=_uuid)
    scope = Column(String(80), nullable=False)
    key = Column(String(200), nullable=False)
    organization_id = Column(String, nullable=True)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_created", "created_at"),
    )


class ActionLease(Base):
    """A short-lived "I am doing this right now" claim, shared by every instance.

    For side effects that may legitimately happen AGAIN later but must not
    happen twice AT ONCE: a double-clicked email send, two workers reaching the
    same provider call. Unlike IdempotencyKey a lease is released when the work
    finishes and EXPIRES if the holder dies, so a crashed request never blocks
    the action forever. See app/services/action_lease.py.
    """
    __tablename__ = "action_leases"

    id = Column(String, primary_key=True, default=_uuid)
    scope = Column(String(80), nullable=False)
    key = Column(String(300), nullable=False)
    holder = Column(String(64), nullable=False)
    acquired_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_action_lease_scope_key"),
    )


class LoginFailure(Base):
    """One failed sign-in, for the brute-force throttle. Shared by every
    instance - the in-memory counter it replaces allowed N x the limit across N
    instances. Rows older than the window are pruned on read."""
    __tablename__ = "login_failures"

    id = Column(String, primary_key=True, default=_uuid)
    throttle_key = Column(String(300), nullable=False)
    failed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_login_failures_key_time", "throttle_key", "failed_at"),
    )
