"""GOD MODE -> LAUNCH BOARD (dynamic project portfolio).

    GET  /god/launch-board                         lanes, queue, summary
    POST /god/launch-board/projects                add (lands in backlog, unapproved)
    POST /god/launch-board/projects/{id}/{action}  lane|priority|approve|revoke|evidence|task|product

God-only (require_god). Mutates only the board file; never triggers a relay run,
messages anyone, or touches production.
"""
# No `from __future__ import annotations` in a router module (see god_access_router).
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.deps import require_god
from app.models.models import User
from app.services import launch_board as lb
from app.services import relay_control_room as rcr

router = APIRouter(prefix="/god/launch-board", tags=["God Mode — Launch Board"])


class ProjectIn(BaseModel):
    name: str
    summary: str = ""
    priority: Optional[int] = None


class ActionIn(BaseModel):
    lane: Optional[str] = None
    priority: Optional[int] = None
    kind: Optional[str] = None
    state: Optional[str] = None
    ref: str = ""
    summary: str = ""
    complete: Optional[bool] = None


def _actor(u: User) -> str:
    return getattr(u, "email", None) or str(getattr(u, "id", "god"))


def _run(fn):
    try:
        return lb.mutate(fn)
    except lb.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def board(response: Response, _god: User = Depends(require_god)):
    for k, v in rcr.NO_CACHE_HEADERS.items():
        response.headers[k] = v
    try:
        out = lb.view(lb.load())
        out["storage"] = lb.storage_status()
        return out
    except lb.BoardError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/projects")
def add(body: ProjectIn, god: User = Depends(require_god)):
    p = _run(lambda b: lb.add_project(b, body.name, body.summary, _actor(god), body.priority))
    return {"id": p["id"]}


@router.post("/projects/{pid}/{action}")
def act(pid: int, action: str, body: ActionIn, god: User = Depends(require_god)):
    a = _actor(god)
    ops = {
        "lane": lambda b: lb.set_lane(b, pid, body.lane or "", a),
        "priority": lambda b: lb.set_priority(b, pid, body.priority, a),
        "approve": lambda b: lb.approve(b, pid, a),
        "revoke": lambda b: lb.revoke_approval(b, pid, a),
        "evidence": lambda b: lb.record_evidence(b, pid, body.kind or "", body.state or "", body.ref, a),
        "task": lambda b: lb.complete_task(b, pid, body.summary, a),
        "product": lambda b: lb.mark_product_complete(b, pid, bool(body.complete), a),
    }
    if action not in ops:
        raise HTTPException(status_code=404, detail="unknown action")
    _run(ops[action])
    return {"ok": True}
