"""GOD MODE -> RELAY CONTROL ROOM.

    GET /god/relay/state     read-only, evidence-only state of the agent relay
    GET /god/relay/worker    the same state; the name the Control Room UI calls on every
                             line (on sci-program /god/relay/state is the older snapshot)

God-only. Reads GitHub (GET only); mutates nothing. Responses are never cacheable
so the page cannot display a stale worker.
"""
# No `from __future__ import annotations` in a router module (see god_access_router).
from fastapi import APIRouter, Depends, Response

from app.deps import require_god
from app.models.models import User
from app.services import relay_control_room as rcr

router = APIRouter(prefix="/god/relay", tags=["God Mode — Relay Control Room"])


@router.get("/state")
def relay_state(response: Response, _god: User = Depends(require_god)):
    for k, v in rcr.NO_CACHE_HEADERS.items():
        response.headers[k] = v
    return rcr.get_state()


@router.get("/worker")
def relay_worker(response: Response, _god: User = Depends(require_god)):
    for k, v in rcr.NO_CACHE_HEADERS.items():
        response.headers[k] = v
    return rcr.get_state()
