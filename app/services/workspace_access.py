"""WHICH CUSTOMER WORKSPACES MAY THIS PERSON ENTER — and nothing else.

THE BUG THIS CLOSES
-------------------
Customer tenancy in this codebase has been ONE COLUMN: `users.organization_id`.
`Membership` with `SCOPE_CUSTOMER_ORG` has existed since the sales models were
written and grants nothing - two separate files say so in their own comments:

    customer_activation.py  "There is no customer membership table - Membership
                             with SCOPE_CUSTOMER_ORG exists but grants nothing"
    customer_provisioning.py "there is no additive customer membership"

One column means one workspace per identity, forever. So D'Angelo, who sells
BookaBoost AND administers We Epic Game, could be one or the other and never
both: `add_existing_user` refuses outright with a 409 rather than move him, and
`HomeRedirect` sends anybody with a NULL organization_id to /sales. There was no
door into a workspace because there was nothing that could describe holding two.

WHAT IS AUTHORITATIVE NOW
-------------------------
An ACTIVE `Membership` row with `scope_type = customer_org`. That is the only
thing any function here reads. `users.organization_id` is not consulted by any
authorization decision in this module.

The column does not vanish - hundreds of routes still resolve the current tenant
through it, and P0's `active_workspace_org_id` is the seam where that resolution
happens. It is now a MIGRATION SOURCE rather than an authority:
`backfill_from_legacy_column` materialises it into real memberships, at startup
for everybody and again at login for the individual, so no existing customer
user loses access on the deploy that flips the authority over.

WHAT THIS MODULE DOES NOT ANSWER
--------------------------------
WHICH DATA a person may see once they are inside. Membership answers "can Jason
enter Restland". `lead_scope` answers "which Restland leads may Jason read", and
the answer is still his own. Two advisors in one workspace both hold a
membership and neither gets the other's book. Keeping those separate is the
whole point: mixing them is how "you are in this org" quietly became "you may
read this org".

WORKSPACE ROLE IS NOT PLATFORM ROLE
-----------------------------------
`Membership.role` on a customer_org row is the person's role IN THAT WORKSPACE.
It is never inferred from `users.role`, and `users.role` is never inferred from
it. D'Angelo is a sales_manager on the platform and an org_admin inside We Epic
Game; both are true, neither implies the other.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import Organization, User
from app.models.sales_models import (
    Membership, SCOPE_CUSTOMER_ORG, SCOPE_BRAND_SALES_ORG,
)

_log = logging.getLogger(__name__)
_sec = logging.getLogger("security.authz")

# Per-request memo, exactly as sales_access does it. Same name shape so the two
# are recognisably the same mechanism rather than two inventions.
_MEMBERSHIP_MEMO = "_workspace_memberships_memo"

# Roles a person can hold INSIDE a customer workspace. Deliberately the customer
# vocabulary and nothing else: no god_admin, because provisioning must never be
# able to mint one, and no sales_manager/sales_rep, because those are the BRAND
# sales vocabulary and reusing them here is how two scopes start sharing a
# meaning they do not have.
WORKSPACE_ROLES = ("org_admin", "advisor", "viewer", "super_admin")
DEFAULT_WORKSPACE_ROLE = "advisor"

# The header a client uses to say which workspace it is currently in. It is a
# REQUEST, never a grant: every function below re-checks it against a real
# membership, so a hand-typed id gets the same answer as a clicked one.
WORKSPACE_HEADER = "X-Workspace-Id"


def is_god(user: User) -> bool:
    return getattr(user, "role", None) == "god_admin"


# ─────────────────────────────────────────────────────────────────────────────
# READING MEMBERSHIP
# ─────────────────────────────────────────────────────────────────────────────

def workspace_memberships(user: User, db: Session) -> List[Membership]:
    """Active customer-workspace memberships for this user.

    Empty for a platform-only identity, which is the correct answer and the one
    the Workspace button reads: no memberships, no button.
    """
    if not user:
        return []
    cached = getattr(user, _MEMBERSHIP_MEMO, None)
    if cached is not None:
        return cached
    rows = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.scope_type == SCOPE_CUSTOMER_ORG,
            Membership.is_active.is_(True),
        )
        .all()
    )
    try:
        setattr(user, _MEMBERSHIP_MEMO, rows)
    except Exception:
        # An instance that will not take an attribute must not break
        # authorisation - it just costs a query.
        pass
    return rows


def invalidate_workspace_memberships(user: User) -> None:
    """Drop the memo after writing a membership for this user in this request.

    Invite acceptance does exactly that, and a memo with no way to clear it is a
    bug waiting for the one caller that needs it.
    """
    try:
        setattr(user, _MEMBERSHIP_MEMO, None)
    except Exception:
        pass


def workspace_org_ids(user: User, db: Session) -> List[str]:
    """Organization ids this person may enter.

    god_admin is NOT widened to every organization here. The owner reaches any
    customer through X-Org-Override and the God console, which is its own
    audited door with its own banner; quietly returning 400 workspaces in a
    switcher menu would be a different feature wearing this one's clothes.
    """
    return [m.scope_id for m in workspace_memberships(user, db)]


def has_workspace(user: User, db: Session, organization_id: str) -> bool:
    if not organization_id:
        return False
    return organization_id in workspace_org_ids(user, db)


def workspace_role(user: User, db: Session, organization_id: str) -> Optional[str]:
    """This person's role IN THAT WORKSPACE, or None. Never `users.role`."""
    for m in workspace_memberships(user, db):
        if m.scope_id == organization_id:
            return m.role
    return None


def platform_memberships(user: User, db: Session) -> List[Membership]:
    """Brand-sales memberships, read through the module that owns them."""
    from app.services import sales_access
    return sales_access.sales_memberships(user, db)


def has_back_office(user: User, db: Session) -> bool:
    """May this person stand in the brand sales back office?

    Asks sales_access rather than re-deriving it, so the button and the /sales
    routes can never disagree about who is allowed in.
    """
    from app.services import sales_access
    return sales_access.is_sales_member(user, db)


# ─────────────────────────────────────────────────────────────────────────────
# THE AUTHORIZED CONTEXT LIST — built by the server, never by the browser
# ─────────────────────────────────────────────────────────────────────────────

def authorized_contexts(db: Session, user: User) -> Dict[str, Any]:
    """Every context this caller may enter, and where they should land.

    THE SERVER BUILDS THIS. The browser renders it. A context that is not in
    this list does not exist as far as the UI is concerned, and - separately,
    because button visibility is never the control - every route behind it
    re-checks membership on its own.
    """
    contexts: List[Dict[str, Any]] = []

    # ── platform / back office ──
    if is_god(user):
        contexts.append({
            "type": "platform",
            "label": "Platform Console",
            "role": "god_admin",
            "path": "/god",
        })
    for m in platform_memberships(user, db):
        contexts.append({
            "type": "platform",
            "label": _brand_sales_label(db, m.scope_id),
            "role": m.role,
            "brand_sales_org_id": m.scope_id,
            "path": "/sales",
        })

    # ── customer workspaces ──
    rows = workspace_memberships(user, db)
    org_ids = [m.scope_id for m in rows]
    orgs = {}
    if org_ids:
        for o in db.query(Organization).filter(Organization.id.in_(org_ids)).all():
            orgs[o.id] = o
    for m in rows:
        org = orgs.get(m.scope_id)
        if org is None:
            # A membership pointing at an organization that no longer exists is
            # not an error to raise at the person - it is a row to skip, and to
            # leave for the operator to clean up. Offering it would produce a
            # menu entry that 404s.
            _log.warning("workspace membership %s points at missing org %s",
                         m.id, m.scope_id)
            continue
        if org.is_active is False:
            # A suspended customer is not a place to walk into.
            continue
        contexts.append({
            "type": "workspace",
            "label": org.name,
            "organization_id": org.id,
            "organization_name": org.name,
            "organization_slug": org.slug,
            "role": m.role,
            "path": "/workspace/%s" % org.id,
        })

    platform = [c for c in contexts if c["type"] == "platform"]
    workspaces = [c for c in contexts if c["type"] == "workspace"]

    # ── where login should land ──
    #
    # Exactly Mike's table. The one rule worth naming: a person with back-office
    # access AND workspaces lands in the back office, because that is the
    # context they were hired into; the switcher is how they leave it.
    if platform:
        default = platform[0]
    elif len(workspaces) == 1:
        default = workspaces[0]
    elif workspaces:
        default = {"type": "workspace_selector", "path": "/workspaces"}
    else:
        # No membership of either kind. Not an error: this is every ordinary
        # customer user until the backfill reaches them, and the legacy tenant
        # home is still theirs.
        default = {"type": "legacy_tenant", "path": "/"}

    return {
        "contexts": contexts,
        "platform_contexts": platform,
        "workspace_contexts": workspaces,
        "has_back_office": bool(platform),
        "workspace_count": len(workspaces),
        "default_context": default,
    }


def _brand_sales_label(db: Session, brand_sales_org_id: str) -> str:
    from app.models.sales_models import BrandSalesOrg
    row = (db.query(BrandSalesOrg)
             .filter(BrandSalesOrg.id == brand_sales_org_id).first())
    return row.name if row is not None else "Sales Back Office"


# ─────────────────────────────────────────────────────────────────────────────
# ENFORCEMENT
# ─────────────────────────────────────────────────────────────────────────────

def assert_workspace_membership(db: Session, user: User, organization_id: str,
                                request: Optional[Request] = None) -> Membership:
    """The caller holds an ACTIVE membership in this workspace, or 403.

    Every workspace-scoped route ends up here. Hiding the button is UX; this is
    the control, and it runs whether the client asked politely through the
    switcher or typed the id into the address bar.
    """
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No workspace was named.")
    for m in workspace_memberships(user, db):
        if m.scope_id == organization_id:
            return m
    _sec.warning(
        "AUTHZ DENIED user=%s role=%s endpoint=%s workspace=%s reason=%s",
        getattr(user, "id", None), getattr(user, "role", None),
        (str(request.url.path) if request is not None else None),
        organization_id, "no active customer_org membership",
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to that workspace.",
    )


def require_workspace_membership(organization_id_param: str = "organization_id"):
    """Dependency factory for routes carrying the workspace id in their path.

        @router.get("/workspace/{organization_id}/thing")
        def thing(m = Depends(require_workspace_membership())): ...

    god_admin is deliberately NOT exempted. The owner enters a customer through
    X-Org-Override, which is audited and banners the screen; letting god through
    this door as well would give the same access by a route that does neither.
    """
    def _dep(request: Request,
             db: Session = Depends(get_db),
             current_user: User = Depends(get_current_user)) -> Membership:
        org_id = request.path_params.get(organization_id_param)
        return assert_workspace_membership(db, current_user, org_id, request)
    return _dep


def selected_workspace_id(user: User, db: Session,
                          request: Optional[Request]) -> Optional[str]:
    """The workspace this request is in, IF the caller may be in it.

    Reads the client's X-Workspace-Id and then throws it away unless a
    membership backs it. A browser cannot select a workspace by asserting one -
    it can only choose among the ones it already holds.

    Returns None when the header is absent, so the caller falls back to the
    single-workspace or legacy answer rather than being refused for not sending
    a header the old client does not know about.
    """
    if request is None:
        return None
    requested = request.headers.get(WORKSPACE_HEADER)
    if not requested:
        return None
    if has_workspace(user, db, requested):
        return requested
    _sec.warning(
        "AUTHZ DENIED user=%s role=%s endpoint=%s workspace=%s reason=%s",
        getattr(user, "id", None), getattr(user, "role", None),
        str(request.url.path), requested,
        "X-Workspace-Id names a workspace the caller has no membership in",
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# WRITING MEMBERSHIP
# ─────────────────────────────────────────────────────────────────────────────

def grant_workspace_membership(db: Session, user_id: str, organization_id: str,
                               role: str = DEFAULT_WORKSPACE_ROLE,
                               granted_by: Optional[str] = None,
                               commit: bool = True) -> Membership:
    """Create or reactivate ONE customer_org membership. IDEMPOTENT.

    Idempotent by lookup on (user, scope_type, scope_id) WITHOUT the role, not
    by the table's unique constraint - that constraint includes `role`, so
    inserting blind would give a person two live memberships in one workspace
    the moment their role changed, and `workspace_role` would then answer with
    whichever row came back first.

    Accepting the same invitation twice therefore updates one row rather than
    adding a second, which is exactly what Mike asked idempotent to mean here.
    """
    role = (role or DEFAULT_WORKSPACE_ROLE)
    if role not in WORKSPACE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace role must be one of: %s." % ", ".join(WORKSPACE_ROLES))

    existing = (
        db.query(Membership)
        .filter(
            Membership.user_id == user_id,
            Membership.scope_type == SCOPE_CUSTOMER_ORG,
            Membership.scope_id == organization_id,
        )
        .order_by(Membership.created_at.asc())
        .first()
    )
    if existing is not None:
        existing.is_active = True
        existing.role = role
        if granted_by and not existing.granted_by:
            existing.granted_by = granted_by
        if commit:
            db.commit()
            db.refresh(existing)
        return existing

    m = Membership(
        user_id=user_id,
        scope_type=SCOPE_CUSTOMER_ORG,
        scope_id=organization_id,
        role=role,
        is_active=True,
        granted_by=granted_by,
    )
    db.add(m)
    if commit:
        db.commit()
        db.refresh(m)
    else:
        db.flush()
    return m


def revoke_workspace_membership(db: Session, user_id: str, organization_id: str,
                                commit: bool = True) -> int:
    """Deactivate, never delete - the history of who could enter is worth keeping."""
    rows = (
        db.query(Membership)
        .filter(
            Membership.user_id == user_id,
            Membership.scope_type == SCOPE_CUSTOMER_ORG,
            Membership.scope_id == organization_id,
            Membership.is_active.is_(True),
        )
        .all()
    )
    for m in rows:
        m.is_active = False
    if commit and rows:
        db.commit()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# MIGRATING OFF THE COLUMN
# ─────────────────────────────────────────────────────────────────────────────

def workspace_role_for_user_row(user: User) -> str:
    """The workspace role implied by a legacy `users.role`, for backfill only.

    Used in exactly one place: turning the old single-column tenancy into a real
    membership. It is not an inference anybody makes at request time - once the
    membership exists, the membership's own role is the answer and this function
    is never consulted about that person again.
    """
    r = getattr(user, "role", None)
    if r in ("org_admin", "super_admin", "advisor", "viewer"):
        return r
    return DEFAULT_WORKSPACE_ROLE


def backfill_from_legacy_column(db: Session, user: Optional[User] = None,
                                commit: bool = True) -> int:
    """Materialise `users.organization_id` into real customer_org memberships.

    THIS IS A MIGRATION, NOT A FALLBACK. It writes exactly what the column
    already says - no inference, no widening, nobody gains access to anything
    they could not already reach this morning. After it runs, membership is the
    authority and the column is only the thing that seeded it.

    It runs in two places, both idempotent:
      - startup, for everybody, so the switcher is right on the first load
      - login, for the one person signing in, so a user created between
        deploys is never stranded waiting for a restart

    Skipped deliberately: god_admin (the owner reaches customers through
    X-Org-Override, not through membership) and the platform pseudo-org (not a
    customer, never a place to stand).
    """
    q = db.query(User).filter(
        User.organization_id.isnot(None),
        User.is_active.is_(True),
        User.role != "god_admin",
    )
    if user is not None:
        q = q.filter(User.id == user.id)

    made = 0
    for u in q.all():
        org_id = u.organization_id
        if not org_id or org_id == "org-god-platform":
            continue
        exists = (
            db.query(Membership.id)
            .filter(
                Membership.user_id == u.id,
                Membership.scope_type == SCOPE_CUSTOMER_ORG,
                Membership.scope_id == org_id,
            )
            .first()
        )
        if exists:
            continue
        db.add(Membership(
            user_id=u.id,
            scope_type=SCOPE_CUSTOMER_ORG,
            scope_id=org_id,
            role=workspace_role_for_user_row(u),
            is_active=True,
        ))
        made += 1

    if made and commit:
        db.commit()
        _log.info("workspace backfill: created %d customer_org membership(s)", made)
    return made
