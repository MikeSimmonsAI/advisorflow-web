"""TEAM CHAT for the EvoSys Pro team app.

Three kinds of conversation, one rule each:

  DM                    exactly two people. ONLY those two can list, read,
                        post or download its files. Chat management and the
                        platform role grant nothing here.
  Sales Team (room)     the people in its member list. The platform's sales
                        reps and managers are enrolled the first time the room
                        is opened; a chat manager adds and removes people, and
                        a removed person stays removed.
  Company Announcements everyone with Chat reads; only `announce` posts.

Every read re-checks membership on the server. Attachments are bytes in the
platform's private storage (app/services/wholesale_files - the existing private
store, database by default) and are served only through an authenticated
endpoint that runs the same room check. A storage key never leaves the server.

Updates reach the other person by polling `GET /team-app/chat/rooms` and
`/messages?after=` - no new service, no socket dependency.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.team_app_models import (ROOM_ANNOUNCEMENTS, ROOM_DM, ROOM_GROUP, TeamChatAttachment,
                                        TeamChatMember, TeamChatMessage, TeamChatRead, TeamChatRoom)
from app.services import team_app_access as acc

def iso(dt: Optional[datetime]) -> Optional[str]:
    """Naive UTC from the database -> '2026-11-16T14:05:00Z' for the browser."""
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


SALES_SLUG = "sales-team"
ANNOUNCE_SLUG = "announcements"
MAX_BODY = 4000
DRIVE_RE = re.compile(r"^https://(drive|docs)\.google\.com/[^\s]+$")


def initials(name: Optional[str]) -> str:
    parts = [p for p in re.split(r"\s+", name or "") if p[:1].isalpha()]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


# ── Built-in rooms ───────────────────────────────────────────────────────────

def _ensure_room(db: Session, platform_id: str, slug: str, kind: str, name: str) -> TeamChatRoom:
    room = (db.query(TeamChatRoom)
            .filter(TeamChatRoom.platform_id == platform_id, TeamChatRoom.slug == slug).first())
    if room:
        return room
    room = TeamChatRoom(platform_id=platform_id, slug=slug, kind=kind, name=name)
    db.add(room)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        room = (db.query(TeamChatRoom)
                .filter(TeamChatRoom.platform_id == platform_id, TeamChatRoom.slug == slug).first())
    return room


def ensure_builtin(db: Session, platform_id: str) -> None:
    sales = _ensure_room(db, platform_id, SALES_SLUG, ROOM_GROUP, "Sales Team")
    _ensure_room(db, platform_id, ANNOUNCE_SLUG, ROOM_ANNOUNCEMENTS, "Company Announcements")
    # Enroll the platform's sales people and platform users who were never in
    # the room. Someone a manager removed has a row and is NOT re-added.
    have = {r[0] for r in db.query(TeamChatMember.user_id).filter(TeamChatMember.room_id == sales.id).all()}
    want = acc.sales_member_ids(db, platform_id) | acc._platform_admins(db, platform_id)
    added = False
    for uid in want - have:
        db.add(TeamChatMember(room_id=sales.id, user_id=uid))
        added = True
    if added:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()


# ── Who may see a room ───────────────────────────────────────────────────────

def _is_member(db: Session, room_id: str, user_id: str) -> bool:
    return db.query(TeamChatMember.id).filter(
        TeamChatMember.room_id == room_id, TeamChatMember.user_id == user_id,
        TeamChatMember.removed_at.is_(None)).first() is not None


def load_room(db: Session, access, user: User, room_id: str) -> TeamChatRoom:
    """The room, if THIS user may read it. Otherwise 404 - a room someone is
    not in does not exist to them, so a direct link proves nothing."""
    room = db.query(TeamChatRoom).filter(TeamChatRoom.id == room_id).first()
    if not room or room.platform_id != access.platform_id:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if room.kind == ROOM_ANNOUNCEMENTS:
        return room
    if _is_member(db, room.id, user.id):
        return room
    raise HTTPException(status_code=404, detail="Conversation not found.")


def can_post(db: Session, access, user: User, room: TeamChatRoom) -> bool:
    if room.kind == ROOM_ANNOUNCEMENTS:
        return access.announce
    return _is_member(db, room.id, user.id)


# ── Lists ────────────────────────────────────────────────────────────────────

def _visible_rooms(db: Session, access, user: User) -> List[TeamChatRoom]:
    member_ids = [r[0] for r in db.query(TeamChatMember.room_id).filter(
        TeamChatMember.user_id == user.id, TeamChatMember.removed_at.is_(None)).all()]
    q = db.query(TeamChatRoom).filter(TeamChatRoom.platform_id == access.platform_id)
    return [r for r in q.all() if r.kind == ROOM_ANNOUNCEMENTS or r.id in member_ids]


def _last_read(db: Session, room_id: str, user_id: str) -> Optional[datetime]:
    r = db.query(TeamChatRead).filter(TeamChatRead.room_id == room_id,
                                      TeamChatRead.user_id == user_id).first()
    return r.last_read_at if r else None


def _unread(db: Session, room: TeamChatRoom, user_id: str) -> int:
    q = db.query(TeamChatMessage.id).filter(TeamChatMessage.room_id == room.id,
                                            TeamChatMessage.sender_id != user_id)
    lr = _last_read(db, room.id, user_id)
    if lr:
        q = q.filter(TeamChatMessage.created_at > lr)
    return q.count()


def _dm_other(db: Session, room: TeamChatRoom, user_id: str) -> Optional[User]:
    ids = [i for i in (room.dm_key or "").split(":") if i and i != user_id]
    return db.query(User).filter(User.id == ids[0]).first() if ids else None


def room_title(db: Session, room: TeamChatRoom, user_id: str) -> Dict:
    if room.kind == ROOM_DM:
        other = _dm_other(db, room, user_id)
        name = other.full_name if other else "Former coworker"
        return {"name": name, "initials": initials(name), "other_user_id": other.id if other else None}
    return {"name": room.name, "initials": initials(room.name), "other_user_id": None}


def room_summary(db: Session, access, user: User, room: TeamChatRoom) -> Dict:
    last = (db.query(TeamChatMessage).filter(TeamChatMessage.room_id == room.id)
            .order_by(TeamChatMessage.created_at.desc()).first())
    preview = None
    if last:
        sender = db.query(User).filter(User.id == last.sender_id).first()
        text = last.body or ""
        if not text:
            has_file = db.query(TeamChatAttachment.id).filter(
                TeamChatAttachment.message_id == last.id).first()
            text = "Sent a file" if has_file else ""
        preview = {"text": text[:140], "sender_name": sender.full_name if sender else None,
                   "mine": last.sender_id == user.id, "at": iso(last.created_at)}
    return {"id": room.id, "kind": room.kind, "slug": room.slug, **room_title(db, room, user.id),
            "unread": _unread(db, room, user.id), "last": preview,
            "can_post": can_post(db, access, user, room),
            "can_manage": room.kind == ROOM_GROUP and access.chat_manage}


def list_rooms(db: Session, access, user: User) -> Dict:
    ensure_builtin(db, access.platform_id)
    rooms = [room_summary(db, access, user, r) for r in _visible_rooms(db, access, user)]
    # Newest conversation first; rooms with no messages yet keep the built-in
    # rooms (Sales Team, then Announcements) above empty DMs.
    kind_rank = {ROOM_GROUP: 0, ROOM_ANNOUNCEMENTS: 1, ROOM_DM: 2}
    rooms.sort(key=lambda r: kind_rank.get(r["kind"], 3))
    rooms.sort(key=lambda r: (r["last"] or {}).get("at") or "", reverse=True)
    return {"rooms": rooms, "unread_total": sum(r["unread"] for r in rooms)}


def unread_total(db: Session, access, user: User) -> int:
    return sum(_unread(db, r, user.id) for r in _visible_rooms(db, access, user))


def people(db: Session, access, user: User) -> List[Dict]:
    return [{"id": u.id, "name": u.full_name, "initials": initials(u.full_name)}
            for u in acc.chat_people(db, access.platform_id) if u.id != user.id]


def open_dm(db: Session, access, user: User, other_id: str) -> TeamChatRoom:
    if other_id == user.id:
        raise HTTPException(status_code=400, detail="Choose a coworker.")
    if other_id not in {u.id for u in acc.chat_people(db, access.platform_id)}:
        raise HTTPException(status_code=403, detail="That person is not on Chat.")
    key = ":".join(sorted([user.id, other_id]))
    room = (db.query(TeamChatRoom).filter(TeamChatRoom.platform_id == access.platform_id,
                                          TeamChatRoom.dm_key == key).first())
    if room:
        return room
    room = TeamChatRoom(platform_id=access.platform_id, kind=ROOM_DM, dm_key=key, created_by=user.id)
    db.add(room)
    try:
        db.flush()
        db.add(TeamChatMember(room_id=room.id, user_id=user.id, added_by=user.id))
        db.add(TeamChatMember(room_id=room.id, user_id=other_id, added_by=user.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        room = (db.query(TeamChatRoom).filter(TeamChatRoom.platform_id == access.platform_id,
                                              TeamChatRoom.dm_key == key).first())
    return room


# ── Messages ─────────────────────────────────────────────────────────────────

def _attachment_json(a: TeamChatAttachment) -> Dict:
    return {"id": a.id, "kind": a.kind, "filename": a.filename, "content_type": a.content_type,
            "byte_size": a.byte_size, "url": a.url if a.kind == "drive_link" else None,
            "download_path": "/team-app/chat/attachments/%s" % a.id if a.kind == "file" else None}


def serialize_messages(db: Session, user: User, rows: List[TeamChatMessage]) -> List[Dict]:
    if not rows:
        return []
    ids = [m.id for m in rows]
    sender_ids = {m.sender_id for m in rows}
    reply_ids = {m.reply_to_id for m in rows if m.reply_to_id}
    replies = {m.id: m for m in db.query(TeamChatMessage).filter(TeamChatMessage.id.in_(reply_ids)).all()} if reply_ids else {}
    sender_ids |= {m.sender_id for m in replies.values()}
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(sender_ids)).all()}
    atts: Dict[str, List[Dict]] = {}
    for a in db.query(TeamChatAttachment).filter(TeamChatAttachment.message_id.in_(ids)).all():
        atts.setdefault(a.message_id, []).append(_attachment_json(a))
    out = []
    for m in rows:
        r = replies.get(m.reply_to_id) if m.reply_to_id else None
        out.append({
            "id": m.id, "room_id": m.room_id, "sender_id": m.sender_id,
            "sender_name": names.get(m.sender_id), "sender_initials": initials(names.get(m.sender_id)),
            "mine": m.sender_id == user.id, "body": m.body, "created_at": iso(m.created_at),
            "mentions": json.loads(m.mentions) if m.mentions else [],
            "mentions_me": bool(m.mentions and user.id in json.loads(m.mentions)),
            "reply_to": ({"id": r.id, "sender_name": names.get(r.sender_id), "body": (r.body or "")[:140]}
                         if r else None),
            "attachments": atts.get(m.id, []),
        })
    return out


def messages(db: Session, user: User, room: TeamChatRoom, after: Optional[str],
             before: Optional[str], limit: int = 50) -> List[Dict]:
    q = db.query(TeamChatMessage).filter(TeamChatMessage.room_id == room.id)
    limit = max(1, min(limit, 200))
    if after:
        try:
            a = datetime.fromisoformat(after.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Bad 'after' time.")
        rows = q.filter(TeamChatMessage.created_at > a).order_by(TeamChatMessage.created_at).limit(limit).all()
    else:
        if before:
            try:
                b = datetime.fromisoformat(before.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                raise HTTPException(status_code=400, detail="Bad 'before' time.")
            q = q.filter(TeamChatMessage.created_at < b)
        rows = list(reversed(q.order_by(TeamChatMessage.created_at.desc()).limit(limit).all()))
    return serialize_messages(db, user, rows)


def mark_read(db: Session, room: TeamChatRoom, user: User) -> None:
    now = datetime.utcnow()
    r = db.query(TeamChatRead).filter(TeamChatRead.room_id == room.id,
                                      TeamChatRead.user_id == user.id).first()
    if r:
        r.last_read_at = now
    else:
        db.add(TeamChatRead(room_id=room.id, user_id=user.id, last_read_at=now))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def _readers(db: Session, access, room: TeamChatRoom) -> set:
    if room.kind == ROOM_ANNOUNCEMENTS:
        return {u.id for u in acc.chat_people(db, access.platform_id)}
    return {r[0] for r in db.query(TeamChatMember.user_id).filter(
        TeamChatMember.room_id == room.id, TeamChatMember.removed_at.is_(None)).all()}


def post(db: Session, access, user: User, room: TeamChatRoom, body: str,
         reply_to_id: Optional[str], mentions: Optional[List[str]],
         client_key: Optional[str], attachment_ids: Optional[List[str]],
         drive_url: Optional[str]) -> Dict:
    if not can_post(db, access, user, room):
        raise HTTPException(status_code=403, detail=(
            "Only people allowed to publish announcements can post here."
            if room.kind == ROOM_ANNOUNCEMENTS else "You are not in this conversation."))
    if client_key:
        prior = db.query(TeamChatMessage).filter(TeamChatMessage.sender_id == user.id,
                                                 TeamChatMessage.client_key == client_key).first()
        if prior:
            return serialize_messages(db, user, [prior])[0]
    body = (body or "").strip()
    if len(body) > MAX_BODY:
        raise HTTPException(status_code=400, detail="Messages are limited to %d characters." % MAX_BODY)
    drive_url = (drive_url or "").strip() or None
    if drive_url and not DRIVE_RE.match(drive_url):
        raise HTTPException(status_code=400, detail="Use a Google Drive or Google Docs link.")
    attachment_ids = attachment_ids or []
    atts = []
    for aid in attachment_ids[:10]:
        a = db.query(TeamChatAttachment).filter(TeamChatAttachment.id == aid).first()
        if not a or a.uploader_id != user.id or a.room_id != room.id or a.message_id:
            raise HTTPException(status_code=400, detail="That file is not ready to send here.")
        atts.append(a)
    if not body and not atts and not drive_url:
        raise HTTPException(status_code=400, detail="Write a message or add a file.")
    if reply_to_id:
        parent = db.query(TeamChatMessage).filter(TeamChatMessage.id == reply_to_id).first()
        if not parent or parent.room_id != room.id:
            raise HTTPException(status_code=400, detail="You can only reply within this conversation.")
    readers = _readers(db, access, room)
    clean_mentions = sorted({m for m in (mentions or []) if m in readers and m != user.id})
    now = datetime.utcnow()
    msg = TeamChatMessage(room_id=room.id, sender_id=user.id, body=body, reply_to_id=reply_to_id,
                          mentions=json.dumps(clean_mentions) if clean_mentions else None,
                          client_key=(client_key or None), created_at=now)
    db.add(msg)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        prior = db.query(TeamChatMessage).filter(TeamChatMessage.sender_id == user.id,
                                                 TeamChatMessage.client_key == client_key).first()
        return serialize_messages(db, user, [prior])[0]
    for a in atts:
        a.message_id = msg.id
    if drive_url:
        db.add(TeamChatAttachment(room_id=room.id, message_id=msg.id, uploader_id=user.id,
                                  kind="drive_link", filename=_drive_label(drive_url), url=drive_url))
    room.last_message_at = now
    # The sender has read their own conversation up to here.
    r = db.query(TeamChatRead).filter(TeamChatRead.room_id == room.id, TeamChatRead.user_id == user.id).first()
    if r:
        r.last_read_at = now
    else:
        db.add(TeamChatRead(room_id=room.id, user_id=user.id, last_read_at=now))
    alert_ids = _queue_phone_alerts(db, user, room, msg, readers, clean_mentions)
    db.commit()
    if alert_ids:
        from app.services import web_push_service as wps
        wps.deliver_now_in_background(alert_ids)
    return serialize_messages(db, user, [msg])[0]


def _first_name(u: Optional[User]) -> str:
    name = (getattr(u, "name", None) or getattr(u, "full_name", None) or "").strip()
    return name.split()[0] if name else "a teammate"


def _alert_title(user: User, room: TeamChatRoom, mentioned: bool) -> str:
    """The only words on the lock screen: who and where - never the message
    itself (a lock screen is public)."""
    who = _first_name(user)
    if room.kind == ROOM_ANNOUNCEMENTS:
        return "New company announcement"
    if mentioned:
        return ("%s mentioned you" % who)[:80]
    if room.kind == ROOM_DM:
        return ("New message from %s" % who)[:80]
    return ("%s posted in %s" % (who, room.name or "Team Chat"))[:80]


def _queue_phone_alerts(db: Session, user: User, room: TeamChatRoom, msg: TeamChatMessage,
                        readers: set, mentions: List[str]) -> List[str]:
    """One phone alert per other person in the conversation, in the same
    transaction as the message. Never raises: a missing alert must never
    stop a message from sending."""
    try:
        from app.models.push_models import TYPE_TEAM_CHAT
        from app.services import web_push_service as wps
        ids = []
        for uid in sorted(readers - {user.id}):
            ev = wps.enqueue(db, target_user_id=uid, organization_id=None, event_type=TYPE_TEAM_CHAT,
                             idempotency_key="chat:%s:%s" % (msg.id, uid),
                             title=_alert_title(user, room, uid in mentions),
                             url="/app/chat/%s" % room.id, record_type="team_chat_room", record_id=room.id)
            if ev is not None:
                ids.append(ev.id)
        return ids
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("team chat: could not queue phone alerts")
        return []


def _drive_label(url: str) -> str:
    if "/spreadsheets/" in url:
        return "Google Sheet"
    if "/document/" in url:
        return "Google Doc"
    if "/presentation/" in url:
        return "Google Slides"
    return "Google Drive link"


# ── Room membership (chat managers) ──────────────────────────────────────────

def manageable_room(db: Session, access, room_id: str) -> TeamChatRoom:
    acc.require(access, "chat_manage")
    room = db.query(TeamChatRoom).filter(TeamChatRoom.id == room_id).first()
    # A DM is never manageable and never revealed: same 404 as a missing room.
    if not room or room.platform_id != access.platform_id or room.kind != ROOM_GROUP:
        raise HTTPException(status_code=404, detail="Room not found.")
    return room


def room_members(db: Session, access, room: TeamChatRoom) -> Dict:
    rows = db.query(TeamChatMember).filter(TeamChatMember.room_id == room.id,
                                           TeamChatMember.removed_at.is_(None)).all()
    inside = {r.user_id for r in rows}
    everyone = acc.chat_people(db, access.platform_id)
    return {"room": {"id": room.id, "name": room.name},
            "members": [{"id": u.id, "name": u.full_name, "initials": initials(u.full_name)}
                        for u in everyone if u.id in inside],
            "can_add": [{"id": u.id, "name": u.full_name, "initials": initials(u.full_name)}
                        for u in everyone if u.id not in inside]}


def set_member(db: Session, access, actor: User, room: TeamChatRoom, user_id: str, present: bool) -> None:
    if present and user_id not in {u.id for u in acc.chat_people(db, access.platform_id)}:
        raise HTTPException(status_code=400, detail="That person does not have Chat.")
    row = db.query(TeamChatMember).filter(TeamChatMember.room_id == room.id,
                                          TeamChatMember.user_id == user_id).first()
    if present:
        if row:
            row.removed_at = None
            row.removed_by = None
            row.added_by = actor.id
        else:
            db.add(TeamChatMember(room_id=room.id, user_id=user_id, added_by=actor.id))
    elif row and row.removed_at is None:
        row.removed_at = datetime.utcnow()
        row.removed_by = actor.id
    db.commit()
