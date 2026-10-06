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

router = APIRouter(prefix="/god/relay", tags=["god-relay"])
log = logging.getLogger(__name__)


class DirectionIn(BaseModel):
    text: str
    mode: str = rc.DEFAULT_MODE


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
    try:
        snap = rc.snapshot(rc.fetch_comments(force=refresh))
    except Exception as e:                                   # noqa: BLE001
        log.warning("relay state: GitHub read failed: %s", type(e).__name__)
        return {**base, "available": False,
                "error": "Could not read the relay log from GitHub just now. It will retry automatically.",
                "state": {"actor": "Idle", "status": "Idle", "task": "Relay log unavailable.", "project": "",
                          "branch": "", "started_ct": "", "elapsed": "", "last_update_ct": "",
                          "next_action": "Retrying automatically."},
                "needs_mike": None,
                "events": [], "queue": {"current": None, "queued": [], "completed_today": [], "attention": []},
                "notifications": []}
    return {**base, "available": True, "error": None, **snap}


@router.post("/direction")
def give_direction(body: DirectionIn, user: User = Depends(require_god)):
    who = getattr(user, "email", None) or "god_admin"
    try:
        return rc.submit_direction(body.text, body.mode, who)
    except rc.DirectionError as e:
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})
