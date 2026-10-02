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
