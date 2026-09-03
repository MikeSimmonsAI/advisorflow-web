"""
Shared FastAPI dependencies: DB session injection and auth guard.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session


@dataclass
class ExecutiveObservationContext:
    """Request-scoped observation authority for a brand executive.

    Lives in request.state.executive_observation -- NOT on the User object.

    The three concepts that must remain separate:
      IDENTITY    -- user.organization_id stays None (Michael is Michael)
      AUTHORITY   -- executive's brand membership (validated in get_current_user)
      OBSERVATION -- which customer org is being read right now (this object)

    read_only is always True. There is no write observation mode.
    """
    executive_user_id: str
    platform_id: str
    observed_org_id: str
    read_only: bool = True

from app.services.auth_service import decode_access_token
from app.models.models import User, Organization

_log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./advisorflow.db")

# ── Connection pool hardening ──────────────────────────────────────────────
_is_sqlite = "sqlite" in DATABASE_URL
_pool_kwargs = (
    {"connect_args": {"check_same_thread": False}}
    if _is_sqlite
    else {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }
)
engine = create_engine(DATABASE_URL, **_pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = decode_access_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Single-session enforcement
    token_jti = payload.get("jti")
    if token_jti and user.session_token != token_jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        )

    # Enforce must_change_password server-side
    if user.must_change_password:
        path = request.url.path
        if not path.startswith("/auth/"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must change your password before continuing. Use /auth/change-password."
            )

    # Cross-org context: god_admin can view any org's data or see ALL orgs combined.
    # expunge() detaches the user from the SQLAlchemy session BEFORE any mutation
    # so changes are never written back to the DB.
    if user.role == "god_admin":
        org_override = request.headers.get("X-Org-Override")
        # BRAND CONTEXT — the level between the platform and a customer.
        #
        # A brand used to be a LABEL derived from whichever customer was
        # selected, never a place the owner could stand. So "the sales
        # workspace" meant every brand's pipeline at once: with one brand
        # seeded that looked like a sensible default, and the moment a second
        # brand existed it would have silently blended two companies' deals
        # onto one screen under a single brand's name.
        #
        # Same shape as X-Org-Override exactly: god only, request-scoped,
        # never persisted, and validated against a real platform row so a
        # typo narrows nothing rather than narrowing to nothing.
        brand_override = request.headers.get("X-Brand-Override")
        db.expunge(user)  # always detach first
        if brand_override:
            from app.models.models import Platform as _Platform
            _plat = (db.query(_Platform)
                     .filter(_Platform.id == brand_override).first())
            if _plat is not None:
                user._selected_brand_id = _plat.id
                user._selected_brand_name = _plat.name
                user._selected_brand_slug = _plat.slug
                _log.info(
                    "AUDIT: god_admin %s (id=%s) activated X-Brand-Override -> "
                    "platform=%s (%s) from IP=%s",
                    user.email, user.id, _plat.id, _plat.slug,
                    request.client.host if request.client else "unknown",
                )
        if org_override:
            # The platform's own pseudo-org is not a customer and is never a
            # context you can enter. Selecting it would reintroduce exactly the
            # mis-attribution the else-branch below exists to prevent, just by
            # the front door instead of by default.
            if str(org_override) == "org-god-platform":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The AdvisorFlow platform account is not a customer "
                           "organization and cannot be entered as one.")
            target_org = db.query(Organization).filter(Organization.id == org_override).first()
            if target_org:
                _log.info(
                    "AUDIT: god_admin %s (id=%s) activated X-Org-Override -> org=%s from IP=%s",
                    user.email, user.id, org_override,
                    request.client.host if request.client else "unknown",
                )
                user.organization_id = org_override
        else:
            # No org selected — "All Orgs" god mode.
            # Routers check this flag and skip the org filter, returning data
            # across all organizations. The flag is never persisted.
            user._god_all_orgs = True

            # ...and the owner is NEUTRAL while no customer is selected.
            #
            # Until Aug 27 2026 the owner kept whatever organization_id their
            # row carried, which app/main.py had been setting to the real
            # pseudo-org 'org-god-platform' on every boot. Three routers check
            # _god_all_orgs before filtering. The other ~179 read the attribute
            # at face value, so a context-less owner did not get an empty result
            # or a loud failure — they quietly read and wrote the platform
            # pseudo-org's own tenant data, and an imported lead landed
            # somewhere that belonged to nobody.
            #
            # Nulling it here makes the unguarded READS return empty instead of
            # returning the wrong tenant's rows, and makes the unguarded WRITES
            # fail instead of silently mis-attributing. `require_tenant_context`
            # / `tenant_write_org_id` in app/services/platform_owner.py turn
            # that failure into a clean 409 that names the fix.
            #
            # Safe to assign: `db.expunge(user)` above detached this instance
            # before any mutation, so nothing here is written back.
            user.organization_id = None

    # ── EXECUTIVE OBSERVATION CONTEXT ────────────────────────────────────────
    # A brand_executive may send X-Executive-Observe to enter a customer org's
    # data in strict read-only mode.
    #
    # IDENTITY CONTRACT: user.organization_id is NEVER modified here.
    # Michael's identity stays organization_id=None throughout the request.
    # Observation scope is stored on user._executive_observation_org_id.
    # lead_scope.active_workspace_org_id reads that attribute and returns the
    # observed org_id for data queries. require_tenant_or_observer passes for
    # authorized observers WITHOUT requiring organization_id to be set.
    #
    # Platform isolation is enforced here: org.platform_id must match the
    # executive's membership scope_id. A cross-brand org_id returns 403.
    #
    # God path: god already has X-Org-Override for full operational access.
    # Observation mode is for brand_executive only at this layer.
    if user.role != "god_admin":
        obs_org_id = request.headers.get("X-Executive-Observe")
        if obs_org_id:
            from app.models.sales_models import Membership, ROLE_BRAND_EXECUTIVE, SCOPE_PLATFORM
            mem = (
                db.query(Membership)
                .filter(
                    Membership.user_id == user.id,
                    Membership.scope_type == SCOPE_PLATFORM,
                    Membership.role == ROLE_BRAND_EXECUTIVE,
                    Membership.is_active.is_(True),
                )
                .first()
            )
            if not mem:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Executive Observation Mode requires a brand executive membership.",
                )
            obs_org = db.query(Organization).filter(Organization.id == obs_org_id).first()
            if not obs_org:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found.")
            if obs_org.platform_id != mem.scope_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Organization is not in your brand's portfolio.",
                )
            db.expunge(user)
            # DO NOT set user.organization_id — identity must stay None.
            # Scope lives here, separate from identity.
            request.state.executive_observation = ExecutiveObservationContext(
                executive_user_id=user.id,
                platform_id=mem.scope_id,
                observed_org_id=obs_org_id,
                read_only=True,
            )
            _log.info(
                "AUDIT: brand_executive %s (id=%s) entered observation context -> org=%s from IP=%s",
                user.email, user.id, obs_org_id,
                request.client.host if request.client else "unknown",
            )

    return user


def require_not_observation(request: Request,
                            user: User = Depends(get_current_user)) -> User:
    """Mutation guard for Executive Observation Mode.

    Any endpoint that writes data must declare this dependency alongside
    require_tenant_user. If the caller entered via X-Executive-Observe
    (observation context), the mutation is refused with 403 regardless
    of how the request was constructed.

    Frontend hiding of buttons is UX, not security. This is security.
    """
    obs = getattr(request.state, "executive_observation", None)
    if obs is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation is not permitted in Executive Observation Mode.",
        )
    return user


def require_tenant_or_observer(request: Request = None,
                               user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)) -> User:
    """Gate for routes that executive observers may read but normal non-tenant
    callers may not.

    Passes when ANY of these is true:
    - user is god_admin
    - user.organization_id is not None  (real tenant member)
    - user._executive_observation is True  (authorized brand_executive observer)

    MUST NOT set user.organization_id. Observation scope lives on
    user._executive_observation_org_id. lead_scope.active_workspace_org_id
    reads both sources and returns the right org_id for data queries.

    require_tenant_user is NOT modified to accept observers. That dependency
    guards mutation routes and must remain strict. This dependency is ONLY
    wired onto the GET endpoints Overview calls during observation mode.
    """
    from app.services.lead_scope import is_god
    if is_god(user):
        return user
    if user.organization_id is not None:
        return user
    _obs = None
    if request is not None:
        _obs = getattr(request.state, "executive_observation", None)
    if _obs is not None and _obs.executive_user_id == user.id:
        return user
    from fastapi import HTTPException, status as http_status
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail="This route requires an active customer workspace.",
    )


def require_tenant_user(request: Request = None,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> User:
    """This route belongs to a CUSTOMER TENANT. The caller must be inside one.

    A brand-sales user has `organization_id = NULL` as a positive architectural
    assertion - they sell the product, they do not use a tenant of it. Before
    this guard existed, tenant routes accepted them and filtered on
    `organization_id == None`, which SQLAlchemy renders as `IS NULL`. That
    returned nothing, so the audit found 200s with empty bodies and no leak.

    The reason it did not leak is that `Lead.organization_id` and
    `PipelineConversation.organization_id` are `nullable=False`, so no row could
    ever match - a real guarantee, but one that lives in the schema rather than
    in any authorization decision. It would end silently the day somebody made
    one of those columns nullable for an unrelated reason, and nothing would
    fail a test.

    So the answer is now a refusal rather than an empty list. god_admin passes:
    the owner legitimately operates inside tenants, and `get_current_user`
    already resolves an org for them via X-Org-Override.

    A SECOND WAY OF BEING INSIDE A TENANT — added with the context switcher.

    "NULL organization_id means brand sales" was true while customer tenancy WAS
    that column. It is not any more: a person can hold customer_org memberships
    and no column value at all, which is precisely D'Angelo - a BookaBoost sales
    manager who also administers We Epic Game. Judging him by the column alone
    refuses him from the workspace he was deliberately given, and the refusal
    would read as the old, correct message while being the wrong answer.

    So the column is checked first because it is free and covers nearly every
    request, and a caller without one is asked the real question: did you SELECT
    a workspace you hold a membership in? `selected_workspace_id` re-derives that
    from the database and returns nothing for an id the caller merely asserted,
    so this widens access to exactly the people an operator already added to a
    workspace and to nobody else. A brand salesperson with no customer
    membership still gets the same refusal, in the same words.
    """
    if user.role == "god_admin":
        return user
    if getattr(user, "organization_id", None) is not None:
        return user

    selected = None
    if request is not None and db is not None:
        try:
            from app.services import workspace_access
            selected = workspace_access.selected_workspace_id(user, db, request)
        except Exception:
            selected = None
    if selected:
        return user

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This is a customer workspace route. Your account belongs to a "
               "brand sales organization, not a customer organization.")


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    """Platform-operator guard — super_admin and god_admin pass.
    god_admin sits above super_admin and is never blocked by this gate."""
    if user.role not in ("super_admin", "god_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    return user


def require_god(user: User = Depends(get_current_user)) -> User:
    """OWNER_CONTROL_PLANE guard — only god_admin accounts pass this."""
    if user.role != "god_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return user


def require_brand_executive(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Executive Suite guard — caller must hold a brand_executive membership,
    OR be a god_admin with an explicit brand context selected.

    Returns a 3-tuple (user, membership, platform) so routes have the
    brand's Platform object without a second query.

    Architecture notes:
    - For normal users, authority comes from the membership row.
      scope_type="platform" + scope_id=<platform_id> naturally isolates
      one brand's executive from every other brand.
    - For god_admin, authority derives from root platform ownership.
      god does NOT require a lower-level brand_executive membership row.
      god DOES require explicit brand context selection (X-Brand-Override
      header → user._selected_brand_id set by get_current_user) so the
      system knows which brand's data to scope to. No brand selected → 403.
      This preserves brand isolation and audit trail: every executive query
      scopes to exactly one platform, same as the membership path.
    - The returned membership object for god is a lightweight sentinel that
      satisfies the router contract (role, created_at, scope_id, scope_type)
      without creating any database row.
    """
    import types
    from app.models.sales_models import Membership, ROLE_BRAND_EXECUTIVE, SCOPE_PLATFORM
    from app.models.models import Platform

    # ── GOD ROOT AUTHORITY PATH ──────────────────────────────────────────────
    # god_admin is AdvisorFlow root authority and does not require a
    # brand_executive membership. Explicit brand context is still required
    # so executive queries remain scoped to exactly one brand.
    if user.role == "god_admin":
        brand_id = getattr(user, "_selected_brand_id", None)
        if not brand_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Select a brand context to enter the Executive Suite.",
            )
        platform = db.query(Platform).filter(Platform.id == brand_id).first()
        if not platform:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Selected brand context not found.",
            )
        # Sentinel membership: satisfies router contract, creates no DB row.
        sentinel = types.SimpleNamespace(
            role=ROLE_BRAND_EXECUTIVE,
            scope_type=SCOPE_PLATFORM,
            scope_id=platform.id,
            created_at=None,
        )
        return user, sentinel, platform

    # ── NORMAL MEMBERSHIP PATH ───────────────────────────────────────────────
    mem = (
        db.query(Membership)
        .filter(
            Membership.user_id == user.id,
            Membership.scope_type == SCOPE_PLATFORM,
            Membership.role == ROLE_BRAND_EXECUTIVE,
            Membership.is_active.is_(True),
        )
        .first()
    )
    if not mem:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Executive Suite access required.",
        )
    platform = db.query(Platform).filter(Platform.id == mem.scope_id).first()
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Executive Suite access required.",
        )
    return user, mem, platform


def get_platform_org_ids(user: User, db) -> list:
    """Return org IDs scoped to the user's access level:
    - god_admin  → all orgs across all platforms
    - super_admin → all orgs on their platform only (via platform_id)
    - org_admin  → their own org only
    """
    if user.role == "god_admin":
        # A SELECTED CUSTOMER IS A NARROWING, NOT A DECORATION.
        #
        # This returned every organization for a god_admin unconditionally,
        # including while the owner had deliberately entered ONE customer via
        # X-Org-Override. The tenant screens that call this - the admin
        # dashboard, advisor metrics, the funnel - therefore showed every
        # advisor on the platform under the banner naming a single customer.
        # The owner asked "how is Greenland doing" and was answered with
        # everyone.
        #
        # get_current_user sets _god_all_orgs ONLY when no customer is
        # selected, so it is the exact signal for "the owner is neutral". When
        # they are neutral this still returns everything, which is what God
        # Mode is for. This only ever narrows; it never widens anyone's scope.
        if getattr(user, "_god_all_orgs", False) or getattr(user, "organization_id", None) is None:
            return [str(row[0]) for row in db.query(Organization.id).all()]
        return [str(user.organization_id)]
    if user.role == "super_admin" and getattr(user, "platform_id", None):
        return [
            str(row[0])
            for row in db.query(Organization.id)
            .filter(Organization.platform_id == user.platform_id)
            .all()
        ]
    return [str(user.organization_id)]


# ---------------------------------------------------------------------------
# PER-RECORD PLATFORM GUARDS
#
# get_platform_org_ids() above scopes LIST endpoints correctly, but a guard
# that is only applied when listing is not a boundary. A probe of the live
# route table on Aug 26 2026 found that a super_admin on one platform could
# read every platform, rename another platform's organization, wipe its data,
# and reset the password of the platform OWNER - and then log in as them.
# require_super_admin proves the caller is *a* platform operator; it says
# nothing about *which* platform, and ten routes took a record id straight
# from the URL and loaded it with no second question asked.
#
# These two loaders are that second question. Every route that accepts an
# org_id or user_id in its path must go through one of them.
#
# They refuse with 404, not 403. A 403 on a record you may not touch confirms
# the record exists, which is how you enumerate another platform's customers
# one id at a time. update_user already used 404 for this reason; these follow it.
# ---------------------------------------------------------------------------

ELEVATED_ROLES = ("super_admin", "god_admin")


def load_org_in_scope(db, actor: User, org_id: str) -> Organization:
    """Load an Organization the actor is entitled to act on, else 404.

    god_admin reaches every org on every platform - that is the whole point of
    the owner control plane. Everyone else is confined to the orgs returned by
    get_platform_org_ids, which for a super_admin means their own platform.
    """
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    if actor.role == "god_admin":
        return org
    if str(org.id) not in set(get_platform_org_ids(actor, db)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


def load_user_in_scope(db, actor: User, user_id: str) -> User:
    """Load a User the actor is entitled to administer, else 404.

    Three refusals, in order of how badly they were needed:

    1. NOBODY BELOW god_admin MAY TOUCH A god_admin. This is the one that
       closes the takeover: reset-password had no such check, so any platform
       operator could set the owner's password and sign in as the owner.
       A god_admin acting on another god_admin is allowed - owners are peers.

    2. No non-owner may administer another elevated account. Editing a peer
       super_admin's email is the same takeover with an extra step: change the
       address, then send yourself the reset link. Acting on your own account
       is always fine.

    3. Brand-sales identities (organization_id IS NULL by positive assertion)
       belong to the God control plane, not to any platform admin. They are
       also why the org comparison below is guarded: if the actor themselves
       has no organization_id, `organization_id == actor.organization_id`
       renders as IS NULL and would match every brand-sales user at once.
    """
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if actor.role == "god_admin":
        return target

    if target.id == actor.id:
        return target

    if target.role in ELEVATED_ROLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if getattr(target, "organization_id", None) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    _allowed_orgs = set(get_platform_org_ids(actor, db))
    if not _allowed_orgs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if str(target.organization_id) not in _allowed_orgs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return target


def platform_ids_in_scope(actor: User, db) -> Optional[list]:
    """Platform ids the actor may see. None means 'all of them' (god_admin only).

    /admin/platforms carried the docstring "God admin only" over a
    require_super_admin guard and returned every platform to anyone who asked,
    which handed a BookaBoost operator the id of every other brand on the box.
    """
    if actor.role == "god_admin":
        return None
    pid = getattr(actor, "platform_id", None)
    return [pid] if pid else []
