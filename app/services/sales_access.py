"""
Sales Workspace access control — server-side, always.

Decision #26: "Sales-only routes must be protected server-side, not merely hidden
in navigation." The Lead Scraper already taught this lesson the hard way — it
shipped with requireGodAdmin in React and NO backend check at all, so any
authenticated user could have driven it. Every sales route uses a dependency
from this module. A hidden nav item is not access control.

Resolution order (see claude/SALES_WORKSPACE_ARCHITECTURE.md §2):
    god_admin                        → users.role   (unchanged, sees everything)
    sales_manager / sales_rep        → memberships  (this module)

`users.role` is NOT repurposed. This layer is additive.
"""
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User
from app.models.sales_models import (
    Membership, BrandSalesOrg, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)


def is_god(user: User) -> bool:
    return getattr(user, "role", None) == "god_admin"


# Memoised on the request's own User instance. get_current_user builds a fresh
# one per request, so the cache lives exactly as long as the request does - it
# cannot leak between users or outlast a membership change made by another
# request. Before this, sales_org_ids / is_sales_manager / require_sales_member
# each re-read the same handful of rows, so a single My Day load asked four
# times for an answer that cannot change while it is being assembled.
_MEMBERSHIP_MEMO = "_sales_memberships_memo"


def sales_memberships(user: User, db: Session) -> List[Membership]:
    """Active brand-sales memberships for this user. Empty for non-sales users."""
    if not user:
        return []
    cached = getattr(user, _MEMBERSHIP_MEMO, None)
    if cached is not None:
        return cached
    rows = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.scope_type == SCOPE_BRAND_SALES_ORG,
            Membership.is_active.is_(True),
        )
        .all()
    )
    try:
        setattr(user, _MEMBERSHIP_MEMO, rows)
    except Exception:
        # An instance that will not take an attribute must not break authorisation.
        pass
    return rows


def invalidate_sales_memberships(user: User) -> None:
    """Drop the memo after writing a Membership for this user in this request.

    Only matters when the actor changes their OWN membership mid-request, which
    is rare - but a cache with no way to clear it is a bug waiting for the one
    caller that needs it.
    """
    try:
        setattr(user, _MEMBERSHIP_MEMO, None)
    except Exception:
        pass


def sales_org_ids(user: User, db: Session) -> List[str]:
    """Brand sales orgs this user may act within.

    god_admin gets every brand sales org — the owner sells across all brands and
    is a legitimate meeting participant (decision #27).
    """
    if is_god(user):
        # A SELECTED BRAND NARROWS. IT NEVER WIDENS.
        #
        # The owner legitimately sells across every brand, so with nothing
        # selected this still returns all of them. But once a brand IS selected
        # in Workspaces, returning all of them is how two companies' pipelines
        # end up merged on one screen under one brand's name - invisible today
        # only because a single brand sales org exists.
        from app.services import platform_owner as _po
        from app.models.models import Platform as _Platform
        brand_platform_id = _po.selected_brand_id(user)
        if brand_platform_id:
            rows = (db.query(BrandSalesOrg.id)
                    .filter(BrandSalesOrg.platform_id == brand_platform_id).all())
            return [r[0] for r in rows]
        return [row[0] for row in db.query(BrandSalesOrg.id).all()]
    return [m.scope_id for m in sales_memberships(user, db)]


def is_sales_manager(user: User, db: Session, brand_sales_org_id: Optional[str] = None) -> bool:
    if is_god(user):
        return True
    for m in sales_memberships(user, db):
        if m.role != ROLE_SALES_MANAGER:
            continue
        if brand_sales_org_id is None or m.scope_id == brand_sales_org_id:
            return True
    return False


def is_sales_member(user: User, db: Session, brand_sales_org_id: Optional[str] = None) -> bool:
    """Manager OR rep. A manager sells personally too (decision #4), so manager
    always satisfies a rep-level requirement."""
    if is_god(user):
        return True
    for m in sales_memberships(user, db):
        if m.role not in (ROLE_SALES_MANAGER, ROLE_SALES_REP):
            continue
        if brand_sales_org_id is None or m.scope_id == brand_sales_org_id:
            return True
    return False


# ── FastAPI dependencies ────────────────────────────────────────────────────

def require_sales_member(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Any brand sales rep or manager (or god). Use on every /sales/* route."""
    if not is_sales_member(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sales workspace access required.",
        )
    return current_user


def require_sales_manager(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Manager-only actions: assign/reassign, team pipeline, team schedule (#5)."""
    if not is_sales_manager(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sales manager access required.",
        )
    return current_user


# ── Record-level authorization ──────────────────────────────────────────────

def assert_can_view_opportunity(user: User, opp: Opportunity, db: Session) -> None:
    """A rep sees their own; a manager sees everything in their brand sales org.

    Enforced per record, not per route — a rep must not read another brand's
    pipeline by guessing an id.
    """
    if is_god(user):
        return
    allowed = sales_org_ids(user, db)
    if opp.brand_sales_org_id not in allowed:
        # Same response as "not found" so ids cannot be probed for existence.
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if is_sales_manager(user, db, opp.brand_sales_org_id):
        return
    if opp.owner_user_id and opp.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This opportunity belongs to another representative.",
        )


def assert_can_edit_opportunity(user: User, opp: Opportunity, db: Session) -> None:
    """Same rule as viewing today; kept separate so edit can tighten independently."""
    assert_can_view_opportunity(user, opp, db)


def assert_can_reassign(user: User, opp: Opportunity, db: Session) -> None:
    """Reassignment is a manager capability (decision #5)."""
    if not is_sales_manager(user, db, opp.brand_sales_org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a sales manager can reassign an opportunity.",
        )
