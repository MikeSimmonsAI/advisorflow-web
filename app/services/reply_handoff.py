"""Who is told about an inbound reply - one rule for every inbound channel.

The lead's assigned advisor when it has one. A lead nobody has claimed (website
intake creates them unassigned) falls back to the ACTIVE org_admins of the
LEAD'S OWN workspace, never another tenant's and never a platform/god user.
Before this, the SMS webhook gated its hot-reply alert on `lead.assigned_to`, so
an intake lead's "yes, call me" text was stored and nobody was told.

Pure: no imports from the app at module level, so it can be exercised without
a database.
"""
from typing import Callable, Iterable, List, Optional


def reply_recipients(assigned_user, org_admin_loader: Callable[[], Iterable]) -> List:
    """[assigned_user] if set, else the workspace's active org_admins."""
    if assigned_user is not None:
        return [assigned_user]
    return [u for u in org_admin_loader() if getattr(u, "is_active", True) is not False]


def workspace_org_admins(db, organization_id: Optional[str]) -> list:
    """Active org_admin Users of exactly this organization."""
    if not organization_id:
        return []
    from app.models.models import User
    return (db.query(User)
            .filter(User.organization_id == organization_id,
                    User.role == "org_admin",
                    User.is_active.isnot(False)).all())
