"""Read-only routes for the configured workflow screens.

Two endpoints and no writes. The navigation asks what exists; a screen asks
what is in one of them. Everything else a reader can do from here - opening a
record, changing it, messaging somebody - happens on the record's own screen,
which already has the audit trail and the permission checks for it.

WHY `require_tenant_or_observer` AND NOT `require_tenant_user`: an executive
observing a customer is exactly the reader these screens are for, and
observation is already read-only end to end. Using the stricter dependency
would have given the observer a navigation item that 403s.
"""

# NO `from __future__ import annotations` IN THIS FILE. slowapi's rate-limit
# decorator wraps the endpoint with functools.wraps, which does not carry
# `__globals__` across - so with PEP 563 in force FastAPI resolves the
# request-body annotation against slowapi's module namespace and raises
# PydanticUndefinedAnnotation at import time. Real annotations, not strings.

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import Organization, User
from app.services import lead_scope, workspace_views

router = APIRouter(prefix="/workspace-views", tags=["workspace-views"])


def _active_org(db: Session, user: User, request: Request):
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id:
        return None
    return db.query(Organization).filter(Organization.id == org_id).first()


@router.get("")
@router.get("/")
def list_views(request: Request,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_tenant_or_observer)):
    """What this workspace calls its own screens.

    An operator standing outside any customer gets an empty list rather than
    an error: there is no workspace to have views, and a 4xx here would turn a
    perfectly ordinary navigation render into a console full of failures.
    """
    org = _active_org(db, current_user, request)
    return {"views": workspace_views.summary(db, current_user, org, request=request)}


@router.get("/{view_key}")
def get_view(view_key: str,
             request: Request,
             limit: int = Query(workspace_views.DEFAULT_ROWS, ge=1,
                                le=workspace_views.MAX_ROWS),
             db: Session = Depends(get_db),
             current_user: User = Depends(require_tenant_or_observer)):
    """One screen's counters and rows.

    404 for a key this workspace has not configured - the same answer as a key
    that does not exist anywhere, so the response cannot be used to discover
    what another customer has switched on.
    """
    org = _active_org(db, current_user, request)
    view = workspace_views.find(org, view_key)
    if view is None:
        raise HTTPException(status_code=404, detail="No such view in this workspace.")
    return workspace_views.render(db, current_user, view, limit=limit, request=request)
