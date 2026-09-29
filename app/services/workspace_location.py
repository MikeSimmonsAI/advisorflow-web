"""WHICH WORKSPACE (LOCATION) A REQUEST IS IN — the context that makes
workspace-scoped feature overrides enforceable.

"Workspace" in the entitlement control plane is a customer LOCATION
(app/models/location_models.py: `Location`, membership through `UserLocation`).
Override rows with scope="workspace" were stored and shown in God Mode, but
nothing told the server which location a request was working in, so
`require_feature` could not apply them. This module is that missing input.

THE HEADER
==========
`X-Workspace-Location: <location id>` — the location the person selected in
the shell. It is a REQUEST, never a grant, exactly like X-Workspace-Id one
level up: the server re-derives it on every request.

A selection is VALID only when ALL hold:
  * the location exists, is active, and belongs to the request's resolved
    organization (the acting workspace org, `lead_scope.active_workspace_org_id`);
  * the caller is assigned to it (a `UserLocation` row in that organization),
    OR is an administrator of that organization (org_admin / super_admin in
    this workspace), OR is god_admin (the owner previewing a location).

An INVALID selection (unknown id, another organization's location, an inactive
one, one the caller is not assigned to) is REJECTED:
  * feature-gated APIs (`require_feature`) answer 403 — consistently, whatever
    the reason, so the answer does not reveal whether another tenant's
    location id exists;
  * GET /branding/org (the shell's bootstrap call) must never be broken by a
    stale selection, so it IGNORES the selection, falls back to the implicit
    rule below and reports `rejected: true` so the client clears it.
  Neither path can widen access: the fallback is never broader than any valid
  selection.

NO HEADER (or a rejected one on /branding/org) — THE IMPLICIT RULE
==================================================================
So that omitting the header can never bypass a workspace-level OFF:
  * assigned to exactly ONE active location  -> that location;
  * assigned to SEVERAL                     -> MOST RESTRICTIVE: a feature is
    allowed only if it is allowed in EVERY one of them (any location that
    switched it off wins);
  * assigned to NONE                        -> organization level (no workspace
    narrowing). A person who works at no location is not a member of any
    workspace an override was written for; administrators without an
    assignment operate at organization level, and may select any location.
god_admin with no header is at organization level (never narrowed).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models.location_models import Location, UserLocation

LOCATION_HEADER = "X-Workspace-Location"
ADMIN_ROLES = ("org_admin", "super_admin")
GOD_ROLE = "god_admin"

MODE_SELECTED = "selected"
MODE_SINGLE = "implicit_single"
MODE_ALL = "all_assigned_most_restrictive"
MODE_ORG = "organization"
MODE_NONE = "none"

_sec = logging.getLogger("security.authz")


class LocationContext:
    """The resolved workspace (location) context of one request."""

    def __init__(self, org_id: Optional[str], mode: str,
                 location_ids: Tuple[str, ...] = (),
                 selected: Optional[Location] = None,
                 requested: Optional[str] = None,
                 rejected: bool = False,
                 available: Optional[List[Location]] = None,
                 role: Optional[str] = None):
        self.org_id = org_id
        self.mode = mode
        # The location ids enforcement evaluates. Empty = organization level.
        self.location_ids = tuple(location_ids)
        self.selected = selected
        self.requested = requested
        self.rejected = rejected
        self.available = list(available or [])
        self.role = role

    def as_payload(self, header_supported: bool = True) -> Dict[str, Any]:
        return {
            # Only present when the browser may actually send it (CORS allows
            # it); otherwise the client must not send it - see header_allowed_by_cors.
            "header": LOCATION_HEADER if header_supported else None,
            "mode": self.mode,
            "selected": ({"id": self.selected.id, "name": self.selected.name}
                         if self.selected is not None else None),
            "effective_location_ids": list(self.location_ids),
            "available": [{"id": l.id, "name": l.name, "is_primary": bool(l.is_primary)}
                          for l in self.available],
            "rejected": self.rejected,
            "enforced": True,
        }


def requested_location(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    try:
        v = request.headers.get(LOCATION_HEADER)
    except Exception:  # noqa: BLE001
        return None
    v = (v or "").strip()
    return v or None


def assigned_location_ids(db: Session, user_id: Optional[str], org_id: str) -> List[str]:
    """Active locations of `org_id` this person is assigned to (stable order)."""
    if not user_id or not org_id:
        return []
    rows = (db.query(Location.id)
            .join(UserLocation, UserLocation.location_id == Location.id)
            .filter(UserLocation.user_id == user_id,
                    UserLocation.organization_id == org_id,
                    Location.organization_id == org_id,
                    Location.is_active.is_(True))
            .order_by(Location.name, Location.id).all())
    seen, out = set(), []
    for (lid,) in rows:
        if lid not in seen:
            seen.add(lid)
            out.append(lid)
    return out


def _org_locations(db: Session, org_id: str) -> List[Location]:
    return (db.query(Location)
            .filter(Location.organization_id == org_id, Location.is_active.is_(True))
            .order_by(Location.is_primary.desc(), Location.name, Location.id).all())


def resolve(db: Session, user, org_id: Optional[str], request: Optional[Request] = None,
            *, role: Optional[str] = None, strict: bool = False) -> LocationContext:
    """Resolve the workspace (location) context. See the module docstring.

    `strict=True` raises 403 on an invalid selection (enforcement);
    `strict=False` ignores it and flags `rejected` (the shell's bootstrap).
    """
    if not org_id or db is None:
        return LocationContext(org_id, MODE_NONE)
    user_role = getattr(user, "role", None)
    is_god = user_role == GOD_ROLE
    if role is None and not is_god:
        role = _workspace_role(db, user, org_id)
    is_admin = is_god or role in ADMIN_ROLES
    user_id = getattr(user, "id", None)
    assigned = [] if is_god else assigned_location_ids(db, user_id, org_id)

    available: List[Location]
    if is_admin:
        available = _org_locations(db, org_id)
    elif assigned:
        available = (db.query(Location).filter(Location.id.in_(assigned))
                     .order_by(Location.is_primary.desc(), Location.name, Location.id).all())
    else:
        available = []

    requested = requested_location(request)
    rejected = False
    if requested:
        loc = (db.query(Location)
               .filter(Location.id == requested, Location.organization_id == org_id,
                       Location.is_active.is_(True)).first())
        if loc is not None and (is_admin or loc.id in assigned):
            return LocationContext(org_id, MODE_SELECTED, (loc.id,), selected=loc,
                                   requested=requested, available=available, role=role)
        _sec.warning(
            "AUTHZ DENIED user=%s role=%s org=%s location=%s reason=%s",
            user_id, user_role, org_id, requested,
            "X-Workspace-Location names a location the caller may not work in")
        if strict:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The selected workspace location is not available to your account "
                       "in this organization. Choose another location and try again.")
        rejected = True

    if is_god:
        return LocationContext(org_id, MODE_ORG, (), requested=requested,
                               rejected=rejected, available=available, role=role)
    if len(assigned) == 1:
        sel = next((l for l in available if l.id == assigned[0]), None)
        return LocationContext(org_id, MODE_SINGLE, (assigned[0],), selected=sel,
                               requested=requested, rejected=rejected,
                               available=available, role=role)
    if len(assigned) > 1:
        return LocationContext(org_id, MODE_ALL, tuple(assigned), requested=requested,
                               rejected=rejected, available=available, role=role)
    return LocationContext(org_id, MODE_ORG, (), requested=requested, rejected=rejected,
                           available=available, role=role)


def _workspace_role(db: Session, user, org_id: str) -> Optional[str]:
    return workspace_role_in(db, user, org_id)


def workspace_role_in(db: Session, user, org_id: Optional[str],
                      request: Optional[Request] = None) -> Optional[str]:
    """This person's role IN `org_id` - what decides location authority there.

    * god_admin: god (the owner's authority is not a customer's grant).
    * a membership in `org_id`: that membership's role (lead_scope.effective_role
      answers the same way for the selected workspace).
    * no membership, `org_id` is the HOME organization: users.role (legacy
      accounts from before memberships, as effective_role falls back).
    * no membership, executive observation of `org_id`: users.role - the
      observation path's existing rule (read-only; lead_scope honours it).
    * no membership in ANOTHER organization: None. users.role describes the
      home organization only, so a home org_admin is NOT an administrator of
      a second workspace they merely belong to (review finding, 2026-09-29).
    """
    if getattr(user, "role", None) == GOD_ROLE:
        return GOD_ROLE
    role = None
    try:
        from app.services.workspace_access import workspace_role
        role = workspace_role(user, db, org_id)
    except Exception:  # noqa: BLE001
        role = None
    if role:
        return role
    if org_id and org_id == getattr(user, "organization_id", None):
        return getattr(user, "role", None)
    if request is None:
        try:
            from app.services.lead_scope import _ambient_request
            request = _ambient_request()
        except Exception:  # noqa: BLE001
            request = None
    obs = getattr(getattr(request, "state", None), "executive_observation", None)
    if obs is not None and getattr(obs, "observed_org_id", None) == org_id:
        return getattr(user, "role", None)
    return None


def header_allowed_by_cors(request: Optional[Request]) -> bool:
    """May a browser send LOCATION_HEADER to this deployment?

    A custom header missing from the CORS allow-list turns EVERY request that
    carries it into a failed preflight the server never sees (main.py records
    this happening once with X-Workspace-Id). The client therefore sends the
    header only when the server says it is safe, and this is how the server
    knows: it reads the allow-list the running app was actually built with.
    No CORS middleware at all (same-origin deployment, tests) means yes.
    """
    app = getattr(request, "app", None) if request is not None else None
    if app is None:
        return True
    try:
        from starlette.middleware.cors import CORSMiddleware
        found = False
        for m in getattr(app, "user_middleware", []) or []:
            if getattr(m, "cls", None) is CORSMiddleware:
                found = True
                allowed = (getattr(m, "kwargs", {}) or {}).get("allow_headers") or ()
                allowed = {str(h).lower() for h in allowed}
                if "*" in allowed or LOCATION_HEADER.lower() in allowed:
                    return True
        return not found
    except Exception:  # noqa: BLE001
        return False
