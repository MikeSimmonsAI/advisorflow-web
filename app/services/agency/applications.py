"""APPLICATION CASE STATE MACHINE.

States are recorded case-management states entered by the agency. Nothing here
underwrites, binds, or talks to a carrier: `approved`/`declined`/`issued` are
what the agent RECORDS the carrier told them.
"""
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.agency_models import AgencyApplication, AgencyApplicationEvent
from app.services.agency.common import now

STATES = ["draft", "prepared", "submitted", "requirements_requested", "awaiting_client",
          "awaiting_agent", "underwriting", "approved", "modified", "declined", "withdrawn", "issued"]
TERMINAL = {"declined", "withdrawn", "issued"}
_WORKING = {"requirements_requested", "awaiting_client", "awaiting_agent", "underwriting"}

TRANSITIONS: Dict[str, set] = {
    "draft": {"prepared", "withdrawn"},
    "prepared": {"submitted", "draft", "withdrawn"},
    "submitted": _WORKING | {"approved", "modified", "declined", "withdrawn"},
    "requirements_requested": (_WORKING - {"requirements_requested"}) | {"withdrawn", "declined"},
    "awaiting_client": (_WORKING - {"awaiting_client"}) | {"withdrawn", "declined"},
    "awaiting_agent": (_WORKING - {"awaiting_agent"}) | {"withdrawn", "declined"},
    "underwriting": {"requirements_requested", "awaiting_client", "awaiting_agent",
                     "approved", "modified", "declined", "withdrawn"},
    "approved": {"issued", "withdrawn"},
    "modified": {"approved", "issued", "awaiting_client", "withdrawn", "declined"},
    "declined": set(),
    "withdrawn": set(),
    "issued": set(),
}

NEXT_ACTION = {
    "draft": "Complete application details",
    "prepared": "Review with client and submit",
    "submitted": "Watch for carrier requirements",
    "requirements_requested": "Collect requested requirements",
    "awaiting_client": "Follow up with client",
    "awaiting_agent": "Agent action needed",
    "underwriting": "Check carrier status",
    "approved": "Record issue / delivery",
    "modified": "Review modified offer with client",
    "declined": None, "withdrawn": None, "issued": None,
}


def days_in_status(app: AgencyApplication, at=None) -> int:
    since = app.status_changed_at or app.created_at
    if since is None:
        return 0
    return max(0, ((at or now()) - since).days)


def is_stalled(app: AgencyApplication, stalled_days: int, at=None) -> bool:
    return app.status not in TERMINAL and app.status != "draft" and \
        days_in_status(app, at) >= int(stalled_days)


def transition(db: Session, app: AgencyApplication, to: str, actor_id: str,
               note: Optional[str] = None) -> AgencyApplicationEvent:
    if to not in STATES:
        raise HTTPException(422, "Unknown application state %r" % to)
    if to == "issued":
        raise HTTPException(409, "Use POST /agency/applications/{id}/issue to record an issued policy")
    allowed = TRANSITIONS.get(app.status, set())
    if to not in allowed:
        raise HTTPException(409, "Transition %s -> %s is not allowed. Allowed: %s"
                            % (app.status, to, ", ".join(sorted(allowed)) or "none (terminal)"))
    return _move(db, app, to, actor_id, note)


def _move(db, app, to, actor_id, note):
    t = now()
    ev = AgencyApplicationEvent(organization_id=app.organization_id, application_id=app.id,
                                from_status=app.status, to_status=to, at=t,
                                by_user_id=actor_id, note=note)
    app.status = to
    app.status_changed_at = t
    if to == "submitted" and app.submitted_at is None:
        app.submitted_at = t
    db.add(ev)
    return ev


def mark_issued(db: Session, app: AgencyApplication, actor_id: str, note=None):
    if "issued" not in TRANSITIONS.get(app.status, set()):
        raise HTTPException(409, "An application in %s cannot be recorded as issued" % app.status)
    return _move(db, app, "issued", actor_id, note)


def allowed_from(status: str) -> Dict[str, Any]:
    return sorted(t for t in TRANSITIONS.get(status, set()))
