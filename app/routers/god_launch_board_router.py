"""GOD MODE -> LAUNCH BOARD (dynamic project portfolio).

    GET  /god/launch-board                         lanes, queue, summary
    POST /god/launch-board/projects                add (lands in backlog, unapproved)
    POST /god/launch-board/projects/{id}/{action}  lane|priority|approve|revoke|evidence|task|product

The launch controls require God Mode. Checklist routes also allow the EvoSys platform login. Mutates only the launch-board tables (durable DB store; the file adapter is explicit
local/test use via LAUNCH_BOARD_BACKEND=file); never triggers a relay run,
messages anyone, or touches production.
"""
# No `from __future__ import annotations` in a router module (see god_access_router).
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.limiter import limiter
from app.deps import get_current_user, require_god
from app.models.models import User
from app.services import launch_board as lb
from app.services import launch_board_store as store
from app.services import relay_control_room as rcr
from app.services import project_checklist as checklist
from app.services import project_checklist_chat

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
    expected_version: Optional[int] = None   # refuse if the project moved on since it was loaded


def _actor(u: User) -> str:
    return getattr(u, "email", None) or str(getattr(u, "id", "god"))


def _run(fn, request_key: Optional[str] = None, expected=None):
    """400 rule refusal, 409 stale/replay-conflict, 503 store unavailable or not migrated."""
    try:
        return store.get_store().mutate(fn, request_key=request_key or None, expected=expected)
    except store.StaleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.StorageError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except lb.BoardError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def board(response: Response, _god: User = Depends(require_god)):
    for k, v in rcr.NO_CACHE_HEADERS.items():
        response.headers[k] = v
    try:
        out = lb.view(store.get_store().load())
        out["storage"] = store.storage_status()
        return out
    except lb.BoardError as e:   # includes StorageError: fail closed, show the reason
        raise HTTPException(status_code=503, detail=str(e))


def require_checklist_access(user: User = Depends(get_current_user)) -> User:
    if not checklist.can_manage(user):
        raise HTTPException(status_code=403, detail="Project Command Center access denied.")
    return user


class ChecklistTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class ChecklistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)
    status: Literal["todo", "in_progress", "done", "wishlist"]
    tasks: list[ChecklistTaskIn] = Field(default_factory=list, max_length=100)
    expected_version: Optional[int] = None


def _checklist_projects():
    try:
        projects = store.get_store().load()["projects"]
    except lb.BoardError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return [{
        "id": p["id"], "name": p["name"], "summary": p["summary"],
        "status": checklist.status_for(p), "tasks": p.get("checklist_tasks", []),
        "version": p.get("version"),
    } for p in sorted(projects, key=lambda project: (project["priority"], project["id"]))
      if p["lane"] != "archived"]


@router.get("/checklist")
def read_checklist(response: Response, _god: User = Depends(require_checklist_access)):
    response.headers["Cache-Control"] = "no-store"
    return {"projects": _checklist_projects(), "storage": store.storage_status()}


class ChecklistChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    previous_message: str = Field(default="", max_length=1500)
    project_id: Optional[int] = None


class ChecklistChatResult(BaseModel):
    reply: str = Field(min_length=1, max_length=2500)
    action: Literal["answer", "create", "update"]
    draft: Optional[ChecklistIn] = None


@router.post("/checklist/chat")
@limiter.limit("15/minute")
def chat_checklist(request: Request, response: Response, body: ChecklistChatIn,
                   user: User = Depends(require_checklist_access)):
    response.headers["Cache-Control"] = "no-store"
    projects = _checklist_projects()
    if body.project_id is not None:
        selected = next((p for p in projects if p["id"] == body.project_id), None)
        if selected is None:
            raise HTTPException(status_code=404, detail="That project is no longer on the board.")
    else:
        named = [p for p in projects if p["name"].casefold() in body.message.casefold()]
        selected = named[0] if len(named) == 1 else None
    try:
        result = ChecklistChatResult.model_validate(project_checklist_chat.propose(
            body.message, body.previous_message, projects, selected, str(user.id)))
    except Exception as e:
        raise HTTPException(status_code=503, detail="Project chat could not respond. Try again in a moment.") from e
    if result.action == "answer":
        return {"reply": result.reply}
    if result.draft is None or (result.action == "update" and selected is None):
        return {"reply": "Choose a project to update, or describe the new card you want to add."}
    draft = result.draft.model_dump(exclude={"expected_version"})
    if result.action == "update":
        draft.update(id=selected["id"], name=selected["name"], version=selected["version"])
    return {"reply": result.reply, "draft": draft}


@router.post("/checklist")
def create_checklist(body: ChecklistIn, god: User = Depends(require_checklist_access),
                     idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    pid, replayed = _run(lambda b: checklist.save(b, None, body.model_dump(), _actor(god)),
                         idempotency_key)
    return {"id": pid, "replayed": replayed}


@router.post("/checklist/{pid}/save")
def update_checklist(pid: int, body: ChecklistIn, god: User = Depends(require_checklist_access),
                     idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    if store.backend() == "db" and body.expected_version is None:
        raise HTTPException(status_code=400, detail="Reload the project before saving.")
    expected = {pid: body.expected_version} if body.expected_version is not None else None
    _run(lambda b: checklist.save(b, pid, body.model_dump(), _actor(god)), idempotency_key, expected)
    return {"ok": True}


@router.post("/checklist/starter")
def starter_checklist(god: User = Depends(require_checklist_access)):
    added, _ = _run(lambda b: checklist.import_starter(b, _actor(god)))
    return {"added": added}


@router.post("/projects")
def add(body: ProjectIn, god: User = Depends(require_god),
        idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    pid, replayed = _run(lambda b: lb.add_project(b, body.name, body.summary, _actor(god), body.priority)["id"],
                         idempotency_key)
    return {"id": pid, "replayed": replayed}


@router.post("/projects/{pid}/{action}")
def act(pid: int, action: str, body: ActionIn, god: User = Depends(require_god),
        idempotency_key: Optional[str] = Header(default=None, max_length=120)):
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
    _, replayed = _run(ops[action], idempotency_key,
                       {pid: body.expected_version} if body.expected_version is not None else None)
    return {"ok": True, "replayed": replayed}


@router.get("/github")
def github_status(refresh: bool = False, _god: User = Depends(require_god)):
    """Per-project delivery ladder from GitHub (read-only, cached 90s).

    Committed / pushed / PR opened / merged / in production are separate
    states; anything that cannot be checked is "unknown", never assumed.
    See app/services/launch_status.py.
    """
    from app.services import launch_status
    return launch_status.cached(force=bool(refresh))
