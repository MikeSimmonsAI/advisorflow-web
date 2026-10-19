"""SPECTATOR - see the workspace, change nothing (Oct 19).

A spectator is a login whose ONLY customer-workspace memberships carry the
role "spectator". Inside that workspace they read what a manager reads -
families, conversations, lead activity, appointments, reports - so the
read checks (lead_scope.is_manager_here) let them in. Every write is refused
HERE, centrally, in get_current_user, for every route: a hidden button is UX,
this is the security. Nothing a spectator sends can edit or delete a record,
send a message, change AI or program settings, export records, touch billing
or manage users.

What a spectator MAY still do is limited to their own session: sign in and
out, refresh the session, change their own password, and finish their own
account setup from an invitation link.

A login that also holds any other (working) membership is NOT a spectator:
the fence never narrows someone's existing access.
"""
from typing import Optional

SPECTATOR = "spectator"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# Their own session and their own password - nothing else is writable.
OWN_SESSION_WRITES = (
    "/auth/login", "/auth/verify", "/auth/refresh", "/auth/logout", "/auth/logout-all",
    "/auth/change-password", "/auth/forgot-password", "/auth/staff-activation/accept",
    "/auth/activation/accept",
)

# Reads that copy records out in bulk, or that reach a provider and write
# back: refused like a write.
_SENSITIVE_READ_MARKERS = ("export", "download", ".csv", "/sms-check", "/god/", "/billing")

REFUSAL = "View-only access: this account can see SCI but cannot change, send or export anything."


def _memberships(db, user):
    from app.services import workspace_access
    return workspace_access.workspace_memberships(user, db)


def is_spectator(db, user) -> bool:
    """True for a login whose every workspace membership is spectator-only,
    and who is not a platform operator."""
    if user is None or getattr(user, "role", None) in ("god_admin", "super_admin", "org_admin"):
        return False
    if getattr(user, "organization_id", None):
        return False                                    # a real tenant member of some organization
    try:
        rows = _memberships(db, user)
    except Exception:                                   # noqa: BLE001
        return False
    return bool(rows) and all((m.role or "") == SPECTATOR for m in rows)


def holds_spectator_seat(db, user, organization_id: Optional[str]) -> bool:
    """Does this person sit in THAT workspace as a spectator?"""
    if not organization_id:
        return False
    try:
        from app.services import workspace_access
        return workspace_access.workspace_role(user, db, organization_id) == SPECTATOR
    except Exception:                                   # noqa: BLE001
        return False


def refusal(db, user, path: str, method: str) -> Optional[str]:
    """None when the request may go ahead; the refusal text otherwise."""
    if not is_spectator(db, user):
        return None
    p = (path or "").lower()
    m = (method or "GET").upper()
    if m in SAFE_METHODS:
        return REFUSAL if any(x in p for x in _SENSITIVE_READ_MARKERS) else None
    if any(p == w or p.startswith(w + "/") for w in OWN_SESSION_WRITES):
        return None
    return REFUSAL
