"""Hierarchical feature entitlements: scoped overrides (platform/brand/org/workspace/role/user)
and their audit history. Owned by WS1 (overnight build 2026-09-28).

WHY A TABLE AND NOT A COLUMN ON `platforms`. The brand layer (EvoSys Pro,
BookaBoost, ...) is the `platforms` table, and it has no notion of modules.
Rather than bolt an `allowed_modules` JSON onto a core table, a brand's
decision is a row here with scope="brand", scope_id=<platform id>. That keeps
the schema change purely additive (two new tables, created by create_all) and
gives every layer the same shape, the same history and the same reset.

ABSENCE OF A ROW IS "INHERITED". There is no "inherited" state stored anywhere:
resetting an override deletes its row (and records the deletion in the event
table). A table that stored "inherited" rows would need a second rule for
which of two contradictory rows wins.

SCOPES
  platform   scope_id = "global"                     the whole deployment
  brand      scope_id = platforms.id                 one white-label brand
  org        scope_id = organizations.id             one customer
  workspace  scope_id = locations.id, org_id = owner a site inside a customer
  role       scope_id = role name,   org_id = org    a role inside one customer
  user       scope_id = users.id,    org_id = org    one person inside one customer
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Index, String, Text, UniqueConstraint

from app.models.models import Base  # noqa: F401


def _uuid() -> str:
    return str(uuid.uuid4())


SCOPES = ("platform", "brand", "org", "workspace", "role", "user")
STATES = ("enabled", "disabled")
PLATFORM_SCOPE_ID = "global"


class FeatureOverride(Base):
    """One explicit decision about one feature at one scope."""

    __tablename__ = "feature_overrides"

    id = Column(String, primary_key=True, default=_uuid)
    scope = Column(String, nullable=False)          # SCOPES
    scope_id = Column(String, nullable=False)
    # For workspace/role/user scopes: the customer organization the decision
    # lives inside. "" (never NULL) for platform/brand/org so the unique
    # constraint below works on every database (NULLs are never equal).
    org_id = Column(String, nullable=False, default="", server_default="")
    feature_key = Column(String, nullable=False)
    state = Column(String, nullable=False)          # STATES
    reason = Column(Text, nullable=True)
    actor_user_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", "scope_id", "org_id", "feature_key",
                         name="uq_feature_override_scope_feature"),
        Index("ix_feature_overrides_org", "org_id"),
        Index("ix_feature_overrides_scope", "scope", "scope_id"),
    )


class FeatureOverrideEvent(Base):
    """Append-only history. Never updated, never deleted."""

    __tablename__ = "feature_override_events"

    id = Column(String, primary_key=True, default=_uuid)
    scope = Column(String, nullable=False)
    scope_id = Column(String, nullable=False)
    org_id = Column(String, nullable=False, default="", server_default="")
    feature_key = Column(String, nullable=False)
    action = Column(String, nullable=False)          # set | reset
    previous_state = Column(String, nullable=True)   # enabled | disabled | None (inherited)
    new_state = Column(String, nullable=True)        # enabled | disabled | None (inherited)
    reason = Column(Text, nullable=True)
    actor_user_id = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_feature_override_events_feature", "feature_key", "at"),
        Index("ix_feature_override_events_scope", "scope", "scope_id"),
    )
