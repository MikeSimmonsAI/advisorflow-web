"""PROGRAM EXPERIENCE - which product a login is working in, decided by the server.

Production 2026-10-20: wholesale@evosyspro.live (a named Wholesale seat on its
home org, EVO Integrated Solutions) could still open EVO's GENERAL EvoSys
platform screens - dashboard, campaigns, CRM, admin - whose sidebar also
carries SCI's "Family Service Center". The login redirect was right; nothing
stopped the platform screens themselves. Hiding menu items is not isolation.

This module is the single answer to "what experience is this workspace for
this person", and the server enforces it:

  experience_for(...)    'wholesale' | 'sci' | 'roofing' | 'insurance' | 'energy' | 'platform'
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
)


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


def experience_for(db: Session, user, org, is_program: bool, named_wholesale: Optional[bool] = None) -> str:
    if is_program:
        return "sci"
    try:
        from app.services.roofing.common import explicitly_enabled
        if explicitly_enabled(org):
            return "roofing"
    except Exception:  # pragma: no cover
        pass
    if named_wholesale is None:
        named_wholesale = _named_wholesale_seat(db, user)
    if named_wholesale and is_wholesale_host(org, user):
        return "wholesale"
    if _explicit(org, "insurance_agency"):
        return "insurance"
    if (getattr(org, "industry", "") or "").strip().lower() == "energy":
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
    if not _named_wholesale_seat(db, user):
        return None
    from app.models.models import Organization
    from app.services import lead_scope
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id:
        return None
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return WHOLESALE if is_wholesale_host(org, user) else None


def enforce(db: Session, user, request: Request) -> None:
    path = request.url.path or "/"
    if path.startswith(WHOLESALE_ALLOWED_PREFIXES):
        return
    if locked_experience(db, user, request) == WHOLESALE:
        raise HTTPException(
            status_code=403,
            # Brand-neutral: a second Wholesale tenant (BookaBoost) must not be
            # told it runs "EvoSys Wholesale".
            detail="This workspace runs Wholesale. That screen is not part of Wholesale.")
