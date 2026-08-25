"""
Tenant-context guard — the single place that decides whether a user may touch
customer-tenant data.

WHY THIS EXISTS
---------------
`users.organization_id` became nullable on Aug 25 2026 so brand-sales staff and
global users can exist without a customer tenant. That creates one new failure
mode worth naming precisely:

    A user with organization_id = NULL reaches a customer-tenant write path and
    the record is created with organization_id = NULL — tenant-owned data owned
    by nobody, invisible to every org-scoped query, and impossible to attribute.

Reads are already safe: `filter(Lead.organization_id == user.organization_id)`
with NULL matches nothing, which is the correct answer for someone with no
tenant. WRITES are the danger. There are ~32 assignment-style sites across
leads / cadence / campaigns / compliance / crm.

Mike's instruction, verbatim:

    "A user with organization_id = NULL cannot create or mutate customer-tenant
     records unless they are operating under an explicitly authorized
     customer-organization context. Do that with a common helper/service
     instead of sprinkling one-off checks everywhere."

So: call `tenant_org_id(user)` wherever you were about to write
`current_user.organization_id` into a new record. It returns a guaranteed-non-null
org id or raises. Never write the raw attribute again.

god_admin operating under X-Org-Override already has organization_id populated
by get_current_user, so the override path is an explicitly authorized context and
passes naturally.
"""
from typing import Optional

from fastapi import Depends, HTTPException, status

from app.deps import get_current_user
from app.models.models import User


class NoTenantContext(HTTPException):
    """403 with a message that says what is actually wrong, so a sales rep who
    lands on a tenant screen sees a real explanation instead of a bare error."""

    def __init__(self, detail: Optional[str] = None):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail or (
                "This account is not a member of a customer organization, so it "
                "cannot create or modify customer data. Brand-sales accounts use "
                "the Sales Workspace."
            ),
        )


def has_tenant_context(user: User) -> bool:
    """True when this user is acting inside a customer organization."""
    return bool(user is not None and getattr(user, "organization_id", None))


def tenant_org_id(user: User) -> str:
    """The org id to stamp on a customer-tenant record. Raises if there is none.

    Use INSTEAD of `current_user.organization_id` on every write path:

        # before
        lead = Lead(organization_id=current_user.organization_id, ...)
        # after
        lead = Lead(organization_id=tenant_org_id(current_user), ...)

    The whole point is that this can never silently return None.
    """
    if not has_tenant_context(user):
        raise NoTenantContext()
    return user.organization_id


def require_tenant_context(current_user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency for whole routers that only make sense inside a tenant.

    Cheaper and safer than auditing every handler: attach it once at the router
    and the entire surface is closed to non-tenant users.
    """
    if not has_tenant_context(current_user):
        raise NoTenantContext()
    return current_user


def assert_same_tenant(user: User, record_org_id: Optional[str]) -> None:
    """Guard a mutation of an existing record against cross-tenant access.

    god_admin is exempt: it legitimately operates across tenants, and under
    X-Org-Override its organization_id is already the target org.
    """
    if getattr(user, "role", None) == "god_admin":
        return
    if not has_tenant_context(user):
        raise NoTenantContext()
    if record_org_id and record_org_id != user.organization_id:
        # 404 rather than 403 so record ids cannot be probed for existence.
        raise HTTPException(status_code=404, detail="Not found")
