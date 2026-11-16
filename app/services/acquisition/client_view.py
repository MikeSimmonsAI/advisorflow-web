"""OPEN CLIENT PORTAL for an EvoSys Acquisition admin (#452, P0 2026-11-15).

THE DEFECT. "Open Client Portal" on Service Companies called the God-only
POST /god/platform/context/customer/{id} and then relied on X-Org-Override,
which the server honours for god_admin only. An Acquisition admin
(platform@evosyspro.live: org_admin of EVO's own org with a named Wholesale
seat) therefore never got in. Worse, the Wholesale program lock ran first:
the request resolved to EVO's Wholesale workspace (home org, or the stored
X-Workspace-Id selection), /god/platform/... is not a Wholesale screen, and the
admin was told "This workspace runs Wholesale. That screen is not part of
Wholesale." without ever reaching the God check.

THE FIX. The admin grant already says "every client's operations"
(acquisition/admins.py). This module makes that real for the client's OWN
portal, without God and without a membership:

  * The admin's browser sends the selected client in X-Org-Override (the same
    header God uses), only on the client-portal screens.
  * The server honours it ONLY when ALL hold: the login holds an active
    Acquisition admin grant, the target org is an acquisition client, and the
    request is for the client portal's own API. Anything else ignores it - the
    request is exactly what it was before.
  * When honoured, for THIS request only (the user row is detached first and
    never written): the tenant is that client, the role is the client's admin,
    any stale X-Workspace-Id is overridden, and the client portal's narrow
    allow-list applies (acquisition_customer_locked) - so it opens nothing of
    the client the client itself can't see, and nothing of Wholesale or the
    platform.
  * Entering and leaving are audited on the client's log. No membership is
    created; the enter endpoint reports the count before and after.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

HEADER = "X-Org-Override"
ATTR = "_acq_client_org"
# The client portal's own API (a subset of ACQUISITION_CLIENT_ALLOWED_PREFIXES:
# sign-in/session, profile and push stay the admin's own).
HONORED_PREFIXES = (
    "/acquisition/",
    "/service-ops/",
    "/branding",
    "/program/status",
    "/workspace-views",
    "/settings/my-capabilities",
)


def client_org_for(db: Session, user, request) -> Optional[str]:
    """The acquisition client this request may act as, or None."""
    if request is None or user is None or getattr(user, "role", None) in ("god_admin", "super_admin"):
        return None
    org_id = request.headers.get(HEADER)
    if not org_id:
        return None
    path = request.url.path or "/"
    if not path.startswith(HONORED_PREFIXES):
        return None
    from app.services.acquisition.admins import is_admin_grant
    if not is_admin_grant(db, user):
        return None
    from app.models.models import Organization
    from app.services.program_experience import acquisition_identity
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or not acquisition_identity(org):
        return None
    return org.id


def apply(db: Session, user, request) -> None:
    """Put this request in the client's portal context (request-scoped)."""
    org_id = client_org_for(db, user, request)
    if not org_id:
        return
    try:
        db.expunge(user)          # never written back
    except Exception:  # noqa: BLE001 - already detached
        pass
    user.organization_id = org_id
    user.role = "org_admin"
    setattr(user, ATTR, org_id)


def active(user) -> Optional[str]:
    return getattr(user, ATTR, None)


def membership_count(db: Session, user_id: str) -> int:
    from app.models.sales_models import Membership
    return db.query(Membership.id).filter(Membership.user_id == user_id).count()
