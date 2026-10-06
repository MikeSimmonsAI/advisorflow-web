"""Control Room API: the ChatGPT <-> Claude relay, for god_admin only.

GET  /god/relay/state      normalized relay status, timeline, queue (read-only)
POST /god/relay/direction  Mike's raw direction -> audit comment + ChatGPT wake-up

Mike's text is recorded as a human-input event for ChatGPT Work to review. It is
never a directive and never reaches Claude directly. See app/services/relay_control.py.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import require_god
from app.models.models import User
from app.services import relay_control as rc
from app.services import relay_overnight as ov

router = APIRouter(prefix="/god/relay", tags=["god-relay"])
log = logging.getLogger(__name__)


class DirectionIn(BaseModel):
    text: str
    mode: str = rc.DEFAULT_MODE


class PackageIn(BaseModel):
    name: str = ""
    objectives: list[str] = []


def _recipients() -> int:
    return len([x for x in (os.environ.get("RELAY_NOTIFY_RECIPIENTS") or "").split(",") if x.strip()])


@router.get("/state")
def relay_state(refresh: bool = False, user: User = Depends(require_god)):
    can_write = bool(rc.write_token())
    base = {
        "give_direction": {
            "active": can_write,
            "setup_required": not can_write,
            "message": None if can_write else
            "SETUP REQUIRED: one narrow GitHub credential must be added to the server before "
            "Give Direction can send. Monitoring works without it.",
        },
        "modes": [{"id": k, "label": v[0]} for k, v in rc.MODES.items()],
        "default_mode": rc.DEFAULT_MODE,
        # Plumbing only: recipients come from configuration, nothing is sent from staging.
        "notify": {"recipients_configured": _recipients(), "sends_in_staging": False},
    }
    snap_comments, monitoring = None, {"state": "ok", "code": None, "message": None}
    try:
        snap_comments = rc.fetch_comments(force=refresh)
    except rc.MonitorError as e:
        log.warning("relay state: GitHub read failed: %s", e.code)
        stale = rc.stale_comments()
        monitoring = {"state": "setup_required" if e.code in ("setup_required", "credential_rejected", "credential_scope")
                      else "degraded", "code": e.code, "message": e.message}
        if stale is not None and monitoring["state"] == "degraded":
            snap_comments = stale
            monitoring["message"] += " Showing the last good data."
    except Exception as e:                                   # noqa: BLE001
        log.warning("relay state: unexpected read failure: %s", type(e).__name__)
        monitoring = {"state": "degraded", "code": "unexpected", "message": "The relay log could not be read just now. Retrying automatically."}
    # Which credential is configured is reported; its value never is.
    monitoring["read_credential_configured"] = bool(rc.read_token())
    base["monitoring"] = monitoring
    if snap_comments is None:
        return {**base, "available": False, "error": monitoring["message"],
                "state": {"actor": "Idle", "status": "Idle", "task": "Relay log unavailable.", "project": "",
                          "branch": "", "started_ct": "", "elapsed": "", "last_update_ct": "",
                          "next_action": "Setup required." if monitoring["state"] == "setup_required" else "Retrying automatically."},
                "needs_mike": None,
                "events": [], "queue": {"current": None, "queued": [], "completed_today": [], "attention": []},
                "notifications": [], "completed_work": [], "suggested_next": [],
                "overnight": {"status": "None", "package": None, "morning_summary": ov.morning_summary([])},
                "home": ov.home_summary({"task": "", "actor": "Idle", "status": "Idle"}, [], [],
                                        {"status": "None", "package": None})}
    return {**base, "available": True, "error": None, **rc.snapshot(snap_comments)}


@router.post("/package/review")
def review_package(body: PackageIn, user: User = Depends(require_god)):
    """Safety review of a draft package. Read-only: writes nothing, needs no credential."""
    return ov.package_safety([o.strip() for o in body.objectives if o and o.strip()])


@router.post("/package")
def start_package(body: PackageIn, user: User = Depends(require_god)):
    who = getattr(user, "email", None) or "god_admin"
    try:
        return rc.submit_package(body.name, body.objectives, who)
    except rc.DirectionError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


@router.post("/direction")
def give_direction(body: DirectionIn, user: User = Depends(require_god)):
    who = getattr(user, "email", None) or "god_admin"
    try:
        return rc.submit_direction(body.text, body.mode, who)
    except rc.DirectionError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})
