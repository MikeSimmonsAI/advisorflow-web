"""PROGRAM EXPERIENCE - which product a login is working in, decided by the server.

Production 2026-10-20: wholesale@evosyspro.live (a named Wholesale seat on its
home org, EVO Integrated Solutions) could still open EVO's GENERAL EvoSys
platform screens - dashboard, campaigns, CRM, admin - whose sidebar also
carries SCI's "Family Service Center". The login redirect was right; nothing
stopped the platform screens themselves. Hiding menu items is not isolation.

This module is the single answer to "what experience is this workspace for
this person", and the server enforces it:

  experience_for(...)    'wholesale' | 'sci' | 'roofing' | 'insurance' | 'acquisition' | 'energy' | 'platform'
                         per workspace, published in /auth/my-contexts so the
                         app opens the right shell and never the platform one
                         for a program workspace;
  enforce(...)           called from deps.get_current_user for EVERY
                         authenticated request: while a Wholesale-seat login
                         works in its Wholesale workspace, any API outside the
                         Wholesale surface (and the small shared core every
                         screen needs - session, branding, notifications,
                         profile) is refused with 403.

Not locked: god_admin / super_admin (the EvoSys internal administration
experience), and any person or workspace this does not name. SCI operations,
Atlantis and every other customer behave exactly as before.
"""
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

WHOLESALE = "wholesale"
WHOLESALE_HOST_SLUG = "evo-integrated-solutions-llc"
# Explicit allow-list key that makes ANOTHER org a Wholesale workspace (second
# tenant of the shared engine, e.g. BookaBoost - #272). Registered in
# app/services/entitlements.py. Only an explicit listing counts.
WHOLESALE_WORKSPACE_KEY = "wholesale_workspace"
INTERNAL_ROLES = ("god_admin", "super_admin")

# What a Wholesale-locked request may reach. Everything Wholesale itself calls
# (/wholesale/...) plus the shared core that any shell needs to render.
WHOLESALE_ALLOWED_PREFIXES = (
    "/wholesale",
    "/auth/",
    "/branding",
    "/notifications",
    "/push",
    "/settings/profile",
    "/settings/my-capabilities",
    "/workspace-views",
    "/work/identity",
    "/program/status",      # answers active:false here - the shell asks it
    "/launch/me",
    "/org-settings",        # terminology the shell reads
    "/telephony/me",
    "/admin/users",         # Wholesale Team screen manages its own people
    "/esign",
    "/health",
    "/demo/environment",
    # A God-granted preview of one deal's demo (deal_preview_grants). The
    # route re-checks the grant for the caller on every open; the grant row is
    # the whole authorization, so the Wholesale lock has nothing to add.
    "/program-access/previews/",
)


BACK_OFFICE_PREFIXES = ("/sales",)


def _named_wholesale_seat(db: Session, user) -> bool:
    from app.models.sales_models import SCOPE_PRODUCT, Membership
    return db.query(Membership.id).filter(
        Membership.user_id == user.id, Membership.scope_type == SCOPE_PRODUCT,
        Membership.scope_id == WHOLESALE, Membership.is_active.is_(True)).first() is not None


def is_wholesale_host(org, user) -> bool:
    """An org that hosts a named Wholesale seat. Two kinds, and nothing else:

      * EvoSys's internal Wholesale org (WHOLESALE_HOST_SLUG) - built in, so
        EvoSys Wholesale keeps working with no data change; and
      * any other org whose stored allow-list EXPLICITLY names
        WHOLESALE_WORKSPACE_KEY together with `wholesale_real_estate` - a second
        Wholesale tenant such as BookaBoost (#272) runs the same shared engine
        on its own data.

    Never by industry, slug pattern or a NULL ("everything") allow-list, so SCI,
    Roofing, Atlantis and every customer that merely has the Wholesale feature
    (e.g. a CRM customer with `wholesale_real_estate`) are NOT hosts. The caller
    has already established that this person holds an active membership there
    (home org for wholesale@, an org_admin seat for an operator)."""
    if org is None or user is None:
        return False
    if (org.slug or "") == WHOLESALE_HOST_SLUG:
        return True
    return _explicit(org, WHOLESALE_WORKSPACE_KEY) and _explicit(org, "wholesale_real_estate")


# ─────────────────────────────────────────────────────────────────────────────
# BUSINESS IDENTITY IS NOT A FEATURE SWITCH (2026-10-21)
#
# Production: switching Roofing + Insurance capabilities ON for Restland (a
# funeral home) made the launcher call Restland "Roofing" - a second Roofing
# card - because identity was read straight from the switches. A capability
# says what a workspace CAN DO; it must never say what the business IS.
#
# Rule: a capability may name the business only when the business's own
# industry agrees, or when the org records no specific industry at all
# (generic / other / unset - those orgs have no identity of their own to
# protect, which keeps every existing setup working). A funeral home, an
# energy company, a cleaning company ... keeps its own identity whatever
# capabilities are switched on, and the capabilities themselves keep working
# (the Roofing API still checks `roofing_program`, the agency API still checks
# `insurance_agency` - untouched here).
# ─────────────────────────────────────────────────────────────────────────────
NEUTRAL_INDUSTRIES = frozenset({"", "generic", "other", "none", "unknown"})
ROOFING_FEATURE = "roofing_program"
INSURANCE_FEATURE = "insurance_agency"


def industry_of(org) -> str:
    return (getattr(org, "industry", "") or "").strip().lower()


def identity_allows(org, business: str) -> bool:
    """May a capability present this org as `business`? Only when its own
    industry is that business or names none."""
    ind = industry_of(org)
    return ind == business or ind in NEUTRAL_INDUSTRIES


def roofing_identity(org) -> bool:
    """This org IS a roofing company running the Roofing program: switch on
    AND identity allows it. (The switch alone still enables the capability.)"""
    if org is None:
        return False
    try:
        from app.services.roofing.common import explicitly_enabled
        enabled = explicitly_enabled(org)
    except Exception:  # pragma: no cover - never let a hint break login
        return False
    return bool(enabled) and identity_allows(org, "roofing")


def acquisition_identity(org) -> bool:
    """This org IS the managed-acquisition client (Issue #452): explicit
    `client_acquisition` key AND a cleaning (or neutral) identity."""
    if org is None:
        return False
    try:
        from app.services.acquisition.common import acquisition_identity as _ident
        return bool(_ident(org))
    except Exception:  # pragma: no cover - never let a hint break login
        return False


def insurance_identity(org) -> bool:
    """This org IS an insurance agency (Max Life Command shell)."""
    return org is not None and _explicit(org, INSURANCE_FEATURE) and identity_allows(org, "insurance")


def experience_for(db: Session, user, org, is_program: bool, named_wholesale: Optional[bool] = None) -> str:
    # 1. Explicit identity markers first - they are set on purpose, never by a
    #    capability toggle: the SCI program, and the Wholesale host (EvoSys's
    #    own org, or an org explicitly listed as a Wholesale workspace).
    if is_program:
        return "sci"
    if is_wholesale_host(org, user):
        if (getattr(org, "slug", "") or "") == WHOLESALE_HOST_SLUG:
            # EvoSys's own Wholesale: the platform (legacy) named seat, as before.
            if named_wholesale is None:
                named_wholesale = _named_wholesale_seat(db, user)
            if named_wholesale:
                return "wholesale"
        elif _second_tenant_access(db, user, org):
            # A second tenant (#272): its admins, or a seat on ITS OWN team.
            # An EvoSys (legacy) seat does not open it.
            return "wholesale"
    # 2. Programs a capability can open - only where identity allows.
    if roofing_identity(org):
        return "roofing"
    if insurance_identity(org):
        return "insurance"
    if acquisition_identity(org):
        return "acquisition"
    # 3. The business's own industry.
    if industry_of(org) == "energy":
        return "energy"
    return "platform"


def _explicit(org, key: str) -> bool:
    """The org's stored allow-list names `key` (a NULL legacy list does not count)."""
    import json
    try:
        keys = json.loads(getattr(org, "enabled_features", None) or "null")
    except (TypeError, ValueError):
        return False
    return isinstance(keys, list) and key in keys


def locked_experience(db: Session, user, request: Optional[Request]) -> Optional[str]:
    """The program this request is confined to, or None."""
    if user is None or getattr(user, "role", None) in INTERNAL_ROLES:
        return None
    named = _named_wholesale_seat(db, user)
    # Cheap gate first: only a named seat, a second-tenant team seat, or an
    # admin of a Wholesale-workspace org can be locked - everyone else stops
    # here after at most three indexed lookups.
    if not named and not _tenant_seat_anywhere(db, user) and not _admin_anywhere(db, user):
        return None
    from app.models.models import Organization
    from app.services import lead_scope
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id:
        return None
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not is_wholesale_host(org, user):
        return None
    if (getattr(org, "slug", "") or "") == WHOLESALE_HOST_SLUG:
        return WHOLESALE if named else None
    return WHOLESALE if _second_tenant_access(db, user, org) else None


def _tenant_seat_anywhere(db: Session, user) -> bool:
    """An active seat on SOME second-tenant workspace's own team (#272)."""
    from app.models.sales_models import SCOPE_PRODUCT, Membership
    from app.services.product_access import TENANT_SEAT_PREFIX
    return db.query(Membership.id).filter(
        Membership.user_id == user.id, Membership.scope_type == SCOPE_PRODUCT,
        Membership.scope_id.like(TENANT_SEAT_PREFIX + "%"), Membership.is_active.is_(True)).first() is not None


def _second_tenant_access(db: Session, user, org) -> bool:
    """A second-tenant Wholesale workspace opens for its org admins and above
    and for the people on ITS OWN team (product_access tenant seat)."""
    if user is None or getattr(user, "role", None) in INTERNAL_ROLES:
        return False
    from app.services.product_access import holds_tenant_seat
    return _admin_of_second_tenant(db, user, org) or holds_tenant_seat(db, user, org)


def _admin_anywhere(db: Session, user) -> bool:
    """One query: is this person an admin of ANY org whose stored list mentions
    the Wholesale-workspace key? (A coarse text match; the exact rule is
    re-checked by workspace_admin_access on the active org.) Ordinary admins of
    every other customer stop here, so the lock costs them one query."""
    from app.models.models import Organization
    from app.models.sales_models import Membership
    from app.services.product_access import WORKSPACE_ADMIN_ROLES
    return (db.query(Membership.id)
            .join(Organization, Organization.id == Membership.scope_id)
            .filter(Membership.user_id == user.id, Membership.scope_type == "customer_org",
                    Membership.role.in_(WORKSPACE_ADMIN_ROLES), Membership.is_active.is_(True),
                    Organization.enabled_features.like('%"' + WHOLESALE_WORKSPACE_KEY + '"%'))
            .first() is not None)


def _admin_of_second_tenant(db: Session, user, org) -> bool:
    """Owner rule (#272): in a Wholesale workspace other than EvoSys's, its org
    admins and above open Wholesale without a named seat."""
    if user is None or getattr(user, "role", None) in INTERNAL_ROLES:
        return False
    from app.services.product_access import workspace_admin_access
    return workspace_admin_access(db, user, org)


def enforce(db: Session, user, request: Request) -> None:
    path = request.url.path or "/"
    if path.startswith(WHOLESALE_ALLOWED_PREFIXES):
        return
    # The sales Back Office is its own destination on the launcher, not a
    # general platform screen: a Wholesale-seat login that is also a member of
    # the brand sales team works it (wholesale@, owner decision 2026-10-21).
    if path.startswith(BACK_OFFICE_PREFIXES):
        from app.services import workspace_access
        if workspace_access.has_back_office(user, db):
            return
    if locked_experience(db, user, request) == WHOLESALE:
        raise HTTPException(
            status_code=403,
            # Brand-neutral: a second Wholesale tenant (BookaBoost) must not be
            # told it runs "EvoSys Wholesale".
            detail="This workspace runs Wholesale. That screen is not part of Wholesale.")
