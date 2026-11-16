"""/team-app — EvoSys Pro Mobile: Chat, Activity and the Team access screen.

Program Access is NOT here: the app reads the existing /auth/my-contexts
answer for that, exactly as the web launcher does.

Every route authenticates with the normal EvoSys session (get_current_user)
and re-decides authority from team_app_access on every call. Nothing the phone
sends about who it is or what it may do is believed. Every response is
`Cache-Control: no-store` so no browser, proxy or service worker keeps a
message, a file or an activity item.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.models.team_app_models import TeamChatAttachment
from app.services import team_activity as activity
from app.services import team_app_access as acc
from app.services import team_chat as chat
from app.services import wholesale_files as files

router = APIRouter(prefix="/team-app", tags=["team-app"])


def _nostore(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _access(db: Session, user: User):
    return acc.access_for(db, user)


# ── Who am I in the team app ────────────────────────────────────────────────

@router.get("/me")
def me(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    out = {"user": {"id": user.id, "name": user.full_name, "email": user.email,
                    "initials": chat.initials(user.full_name), "role": user.role},
           "access": a.as_dict(), "time_zone": activity.user_tz(db, user)}
    if a.chat:
        chat.ensure_builtin(db, a.platform_id)
        out["chat_unread"] = chat.unread_total(db, a, user)
    return out


# ── Chat ────────────────────────────────────────────────────────────────────

@router.get("/chat/rooms")
def rooms(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    return chat.list_rooms(db, a, user)


@router.get("/chat/people")
def chat_people(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    return {"people": chat.people(db, a, user)}


class DmIn(BaseModel):
    user_id: str


@router.post("/chat/dm")
def open_dm(body: DmIn, response: Response, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    room = chat.open_dm(db, a, user, body.user_id)
    return chat.room_summary(db, a, user, room)


@router.get("/chat/rooms/{room_id}")
def room_detail(room_id: str, response: Response, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    room = chat.load_room(db, a, user, room_id)
    out = chat.room_summary(db, a, user, room)
    readers = chat._readers(db, a, room)
    out["mentionable"] = [{"id": u.id, "name": u.full_name}
                          for u in acc.chat_people(db, a.platform_id)
                          if u.id in readers and u.id != user.id]
    return out


@router.get("/chat/rooms/{room_id}/messages")
def room_messages(room_id: str, response: Response, after: Optional[str] = None,
                  before: Optional[str] = None, limit: int = 50,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    room = chat.load_room(db, a, user, room_id)
    return {"messages": chat.messages(db, user, room, after, before, limit)}


class PostIn(BaseModel):
    body: str = ""
    reply_to_id: Optional[str] = None
    mentions: List[str] = Field(default_factory=list)
    client_key: Optional[str] = Field(default=None, max_length=80)
    attachment_ids: List[str] = Field(default_factory=list)
    drive_url: Optional[str] = Field(default=None, max_length=1000)


@router.post("/chat/rooms/{room_id}/messages")
def room_post(room_id: str, body: PostIn, response: Response,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    room = chat.load_room(db, a, user, room_id)
    return chat.post(db, a, user, room, body.body, body.reply_to_id, body.mentions,
                     body.client_key, body.attachment_ids, body.drive_url)


@router.post("/chat/rooms/{room_id}/read")
def room_read(room_id: str, response: Response, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    room = chat.load_room(db, a, user, room_id)
    chat.mark_read(db, room, user)
    return {"ok": True, "unread_total": chat.unread_total(db, a, user)}


@router.post("/chat/rooms/{room_id}/attachments")
async def room_upload(room_id: str, response: Response, file: UploadFile = File(...),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    room = chat.load_room(db, a, user, room_id)
    if not chat.can_post(db, a, user, room):
        raise HTTPException(status_code=403, detail="You cannot post files here.")
    data, ctype, name = await files.read_upload(file)
    stored = files.put("team-app-%s" % a.platform_id, "chat", data, ctype, db=db)
    att = TeamChatAttachment(room_id=room.id, uploader_id=user.id, kind="file", filename=name,
                             content_type=ctype, byte_size=stored["byte_size"],
                             storage_backend=stored["storage_backend"], storage_key=stored["storage_key"])
    db.add(att)
    db.commit()
    return chat._attachment_json(att)


@router.get("/chat/attachments/{attachment_id}")
def attachment_download(attachment_id: str, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    a = _access(db, user)
    acc.require(a, "chat")
    att = db.query(TeamChatAttachment).filter(TeamChatAttachment.id == attachment_id).first()
    if not att or att.kind != "file":
        raise HTTPException(status_code=404, detail="File not found.")
    # The same room check as reading the conversation; an unsent upload is
    # visible only to the person who uploaded it.
    chat.load_room(db, a, user, att.room_id)
    if not att.message_id and att.uploader_id != user.id:
        raise HTTPException(status_code=404, detail="File not found.")
    data = files.fetch(att.storage_backend, att.storage_key, db=db)
    safe = (att.filename or "file").replace('"', "")
    return Response(content=data, media_type=att.content_type or "application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % safe,
                             "Cache-Control": "no-store, private",
                             "X-Content-Type-Options": "nosniff"})


@router.get("/chat/unread")
def unread(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    acc.require(a, "chat")
    return {"unread_total": chat.unread_total(db, a, user)}


# Chat management: membership of group rooms only. Never DMs.

@router.get("/chat/rooms/{room_id}/members")
def members(room_id: str, response: Response, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    room = chat.manageable_room(db, a, room_id)
    return chat.room_members(db, a, room)


class MemberIn(BaseModel):
    user_id: str


@router.post("/chat/rooms/{room_id}/members")
def add_member(room_id: str, body: MemberIn, response: Response,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    room = chat.manageable_room(db, a, room_id)
    chat.set_member(db, a, user, room, body.user_id, True)
    return chat.room_members(db, a, room)


@router.delete("/chat/rooms/{room_id}/members/{member_id}")
def remove_member(room_id: str, member_id: str, response: Response,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _access(db, user)
    room = chat.manageable_room(db, a, room_id)
    chat.set_member(db, a, user, room, member_id, False)
    return chat.room_members(db, a, room)


# ── Activity ────────────────────────────────────────────────────────────────
# Open to every signed-in person: each source inside applies its own rule
# (workspaces this login may open, appointments it takes part in, chat rooms
# it can read). The brand host is read the same way /auth/my-contexts reads it,
# so Activity covers exactly the workspaces desktop Program Access lists.

def _host_slug(request: Request):
    from app.routers.auth_router import _brand_domain_slug
    return _brand_domain_slug(request)


@router.get("/activity/updates")
def activity_updates(request: Request, response: Response, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    _nostore(response)
    return activity.updates(db, user, _access(db, user), _host_slug(request))


@router.get("/activity/upcoming")
def activity_upcoming(request: Request, response: Response, date: Optional[str] = None,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    return activity.upcoming(db, user, date, _host_slug(request))


# ── Team access (platform only) ─────────────────────────────────────────────

def _require_platform(db: Session, user: User):
    a = _access(db, user)
    acc.require(a, "is_platform")
    return a


@router.get("/admin/people")
def admin_people(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _require_platform(db, user)
    sales = acc.sales_member_ids(db, a.platform_id)
    out = []
    for u in acc.access_candidates(db, a.platform_id):
        out.append({"id": u.id, "name": u.full_name, "initials": chat.initials(u.full_name),
                    "is_platform": acc.is_platform_user(u, a.platform_id),
                    "sales_member": u.id in sales,
                    "grants": acc.team_grants(db, u.id, a.platform_id)})
    return {"people": out, "keys": list(acc.TEAM_KEYS)}


class GrantsIn(BaseModel):
    keys: List[str]


@router.put("/admin/people/{user_id}/grants")
def admin_set_grants(user_id: str, body: GrantsIn, response: Response,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _nostore(response)
    a = _require_platform(db, user)
    from app.services import capabilities
    target = db.query(User).filter(User.id == user_id).first()
    if not target or target.id not in {u.id for u in acc.access_candidates(db, a.platform_id)}:
        raise HTTPException(status_code=404, detail="Person not found.")
    if acc.is_platform_user(target, a.platform_id):
        raise HTTPException(status_code=400, detail="The platform already has every team right.")
    wanted = [k for k in body.keys if k in acc.TEAM_KEYS]
    # Keep every non-team platform grant (Demo Suite etc.) exactly as it was.
    others = [k for k in capabilities.platform_grants_for(db, target.id, a.platform_id)
              if k not in acc.TEAM_KEYS]
    capabilities.set_platform_grants(db, target, a.platform_id, a.platform_name, user,
                                     others + wanted, commit=True)
    return {"id": target.id, "grants": acc.team_grants(db, target.id, a.platform_id)}
