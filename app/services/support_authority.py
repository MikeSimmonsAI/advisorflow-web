"""WHO MAY OPERATE SUPPORT, AND OVER WHOM.

GOD MODE IS ROOT AUTHORITY. PERIOD.
------------------------------------
There is no Support Superadmin, no support root, no second permission
architecture. This module adds exactly one thing to what already exists: a
BRAND-SCOPED support operator, expressed through the capability system
(`capabilities.support_console`) and the membership tables that were already
there.

    god_admin                -> every brand, every customer, every action
    brand support operator   -> one brand's customers, read and respond
    everybody else           -> nothing

THE THREE THINGS A BRAND OPERATOR STILL CANNOT DO
--------------------------------------------------
    1. See another brand. `visible_platform_ids` returns a LIST, and every
       query filters on it. A god sees None, which means unfiltered.
    2. Approve a remediation that requires approval. That is God's, by risk
       class, in `support_remediation.authorization_for` — which never reads
       this module. A support operator with an approval button would make the
       GOD_APPROVAL class decorative.
    3. Change platform configuration: entitlements, hours, fix policy,
       service catalogue. Those are God-only routes.

WHY BRAND SCOPE IS A LIST OF PLATFORM IDS
------------------------------------------
A capability grant is written against a `BrandSalesOrg` — that is the scope
the existing table supports and the people who do this work belong to one.
But every support row is scoped by `platform_id`. So this module does the one
translation, `BrandSalesOrg.platform_id`, in ONE place, and everything
downstream filters on platform ids it was handed rather than re-deriving
authority per query.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User

log = logging.getLogger(__name__)

SUPPORT_CAPABILITY = "support_console"

_DENIED = ("Support operations are restricted to the platform team. If you "
           "need access to a brand's support queue, an owner can grant it.")


def is_god(user: Optional[User]) -> bool:
    return bool(user is not None and getattr(user, "role", None) == "god_admin")


def support_brand_ids(db: Session, user: Optional[User]) -> List[str]:
    """The BrandSalesOrg ids this user may operate support for.

    Empty for god, deliberately — the same choice `capabilities.brands_with_
    capability` documents: a function that answered "all brands" from a
    per-brand lookup would put a global answer inside a scoped one, and a
    caller that forgot to check `is_god` first would silently get everything.
    """
    if user is None or is_god(user):
        return []
    try:
        from app.services import capabilities
        return capabilities.brands_with_capability(db, user, SUPPORT_CAPABILITY)
    except Exception:                                          # noqa: BLE001
        log.exception("support_authority: brand capability lookup failed for %s",
                      getattr(user, "id", None))
        return []


def visible_platform_ids(db: Session, user: Optional[User]) -> Optional[List[str]]:
    """Which brands' support this user may see. None means ALL (god only).

    None-means-all is the same convention `deps.platform_ids_in_scope` already
    uses, so a caller that has seen one has seen both. An empty LIST means
    "no brands", which is a different and much more common answer than "all"
    and must never be confused with it — hence None rather than an empty list
    for the owner.
    """
    if is_god(user):
        return None
    brand_ids = support_brand_ids(db, user)
    if not brand_ids:
        return []
    from app.models.sales_models import BrandSalesOrg
    rows = (db.query(BrandSalesOrg.platform_id)
            .filter(BrandSalesOrg.id.in_(brand_ids)).all())
    return sorted({row[0] for row in rows if row[0]})


def may_operate_support(db: Session, user: Optional[User]) -> bool:
    if is_god(user):
        return True
    return bool(visible_platform_ids(db, user))


def require_support_operator(user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)) -> User:
    """Route guard for the support console.

    god_admin OR a brand support operator. Everyone else gets 403 with a
    message that tells them how access is obtained rather than pretending the
    screen does not exist — the route is discoverable either way, and a vague
    refusal just generates a support ticket about the support console.
    """
    if not may_operate_support(db, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_DENIED)
    return user


def scope_query(query, model, db: Session, user: User):
    """Apply this operator's brand scope to a support query.

    THE FILTER IS APPLIED HERE OR NOWHERE. Every God-side list endpoint runs
    its query through this function, so adding a new list endpoint that
    forgets the brand filter requires deliberately not calling it.
    """
    platform_ids = visible_platform_ids(db, user)
    if platform_ids is None:
        return query
    if not platform_ids:
        # An operator with no brands sees nothing, expressed as a filter that
        # matches nothing rather than as an early return the caller might skip.
        return query.filter(model.platform_id.is_(None),
                            model.platform_id.isnot(None))
    return query.filter(model.platform_id.in_(platform_ids))


def assert_visible(db: Session, user: User, *, platform_id: Optional[str]) -> None:
    """Refuse a single record that belongs to a brand this operator cannot see.

    404 RATHER THAN 403, matching `deps.load_org_in_scope`: telling somebody
    "that ticket exists but is not yours" is an enumeration oracle, and ticket
    numbers are short.
    """
    if is_god(user):
        return
    allowed = visible_platform_ids(db, user)
    if not allowed or platform_id not in allowed:
        raise HTTPException(status_code=404, detail="Not found.")


def require_god_for_configuration(user: User = Depends(get_current_user)) -> User:
    """Platform configuration is the owner's, not a brand operator's.

    Support entitlements, business hours, the fix policy and the service
    catalogue all describe what the PLATFORM promises and what it may do by
    itself. A brand operator running a queue does not get to change either,
    and this is the guard that says so rather than a comment hoping somebody
    remembers.
    """
    if not is_god(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Support configuration is controlled by the platform owner.")
    return user
