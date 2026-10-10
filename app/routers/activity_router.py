"""
Activity feed — unified sent-message log for SMS + email.

Returns the most recent outbound messages (SMS + email) for the advisor's
organization, merged and sorted by sent time newest-first. Designed for the
Activity page and for the "sent today" badge on the Leads list.

Delivery status is included for SMS messages (updated by Twilio status-callback
webhook). Email delivery status is always 'sent' until read-tracking is wired.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Message, EmailMessage
from app.services import lead_scope
from app.services import send_source as _src
from app.utils.time_fmt import iso_utc  # S17: explicit-UTC timestamps

router = APIRouter(prefix="/activity", tags=["activity"])


# SS4 — WHICH SEND SOURCES MEAN "AN AI WROTE THIS".
#
# send_source.py records WHY a send happened; it has no AI/not-AI axis of its
# own, so the Activity screen could not tell a robot's message from a human's.
# These are the sources whose content an AI model produced:
#   BULK_AI              AI drafted, an advisor triggered the batch
#   AI_CONVERSATION      the AI conversation engine, no human in the loop
#   PIPELINE_AUTO_REPLY  the pipeline answered an inbound automatically
#   AI_EMPLOYEE          an AI workforce employee
# Deliberately NOT included: CADENCE (scheduled templates), AUTO_SEND (the
# message row does not record whether the queued draft came from AI - claiming
# it did would be a guess), VOICE_BOOKING_LINK (a link sent around a call), and
# NULL, which means "unrecorded" and is never reported as either.
AI_GENERATED_SOURCES = frozenset({
    _src.BULK_AI,
    _src.AI_CONVERSATION,
    _src.PIPELINE_AUTO_REPLY,
    _src.AI_EMPLOYEE,
})


def is_ai_generated(source) -> bool:
    return bool(source) and source in AI_GENERATED_SOURCES


def _source_fields(source) -> dict:
    """The additive attribution fields every activity row carries."""
    return {
        "send_source": source,
        "send_source_label": _src.source_label(source),
        "ai_generated": is_ai_generated(source),
    }


@router.get("/today")
def sent_today(
    limit: int = Query(default=500, ge=1, le=2000),
    tz: Optional[str] = Query(default=None,
                              description="IANA timezone for the day boundary."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SS3 — everything this organization sent today, answered by the server.

    The Activity screen answered this by fetching 300 rows from a 30-day
    endpoint and filtering them in the browser. An organization sending more
    than 300 messages a month therefore undercounted today, silently, with a
    confident number on screen - and the day boundary came from the viewer's
    laptop rather than the business's own clock.

    Each row carries the channel, the timestamp, the delivery state, the
    source that produced it, the human who pressed the button and the advisor
    the family hears from - the last two being different people whenever a
    lead belongs to a colleague. `touches_today` answers "did we contact this
    family more than once", which nothing could answer before.
    """
    org_id = lead_scope.active_workspace_org_id(current_user, db)
    user_ids = None
    if not lead_scope.is_manager_here(current_user, db):
        user_ids = [current_user.id]
    from app.services import activity_reporting
    result = activity_reporting.sent_today(
        db, org_id, user_ids=user_ids, limit=limit, tzname=tz)
    # Additive: the same AI flag /activity/sent carries, so both lists agree.
    for item in (result or {}).get("items") or []:
        item.setdefault("send_source_label", _src.source_label(item.get("send_source")))
        item.setdefault("ai_generated", is_ai_generated(item.get("send_source")))
    return result


@router.get("/sent")
def sent_activity(
    limit: int = Query(default=200, ge=1, le=500),
    days: int = Query(default=30, ge=1, le=365, description="How many days back to look"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Unified activity feed: last N sends (SMS + email) for this advisor's org,
    merged newest-first. Includes delivery status for SMS.
    god_admin with no org selected sees activity across ALL orgs.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    god_all = getattr(current_user, '_god_all_orgs', False)

    # ── SMS sends ──────────────────────────────────────────────────────────
    sms_base = db.query(Message, Lead).join(Lead, Message.lead_id == Lead.id)
    sms_filters = [Message.sent_at >= cutoff]
    if not god_all:
        sms_filters.append(Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
    sms_query = sms_base.filter(*sms_filters)
    if not god_all:
        # sender_id is the lead's ASSIGNED advisor (compose_router.acting_advisor),
        # not whoever pressed send, so filtering it against the caller hid an
        # advisor's own sends from them. See lead_scope.own_or_assigned_records_only.
        sms_query = lead_scope.own_or_assigned_records_only(
            sms_query, Message.sender_id, Lead.assigned_to_id, current_user, db
        )
    sms_rows = sms_query.order_by(Message.sent_at.desc()).limit(limit).all()

    sms_items = [
        {
            "id": msg.id,
            "channel": "sms",
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.phone or "—",
            "lead_phone": lead.phone,
            "lead_email": lead.email,
            "body_preview": (msg.body[:120] + "…") if msg.body and len(msg.body) > 120 else (msg.body or ""),
            "sent_at": iso_utc(msg.sent_at),
            "delivery_status": msg.delivery_status or msg.twilio_status or "pending",
            "delivery_status_at": iso_utc(getattr(msg, "delivery_status_at", None)),
            **_source_fields(getattr(msg, "send_source", None)),
        }
        for msg, lead in sms_rows
    ]

    # ── Email sends ────────────────────────────────────────────────────────
    email_base = db.query(EmailMessage, Lead).join(Lead, EmailMessage.lead_id == Lead.id)
    email_filters = [EmailMessage.sent_at >= cutoff]
    if not god_all:
        email_filters.append(Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
    email_query = email_base.filter(*email_filters)
    if not god_all:
        email_query = lead_scope.own_or_assigned_records_only(
            email_query, EmailMessage.sender_id, Lead.assigned_to_id, current_user, db
        )
    email_rows = email_query.order_by(EmailMessage.sent_at.desc()).limit(limit).all()

    email_items = [
        {
            "id": msg.id,
            "channel": "email",
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email or "—",
            "lead_phone": lead.phone,
            "lead_email": lead.email,
            "subject": msg.subject,
            "body_preview": None,
            "sent_at": iso_utc(msg.sent_at),
            "delivery_status": msg.status or "sent",
            "delivery_status_at": None,
            **_source_fields(getattr(msg, "send_source", None)),
        }
        for msg, lead in email_rows
    ]

    # Merge and sort newest-first
    merged = sorted(
        sms_items + email_items,
        key=lambda x: x["sent_at"] or "",
        reverse=True,
    )[:limit]

    return merged


# ── ACTIVITY & CALL HISTORY (Oct 2026) ───────────────────────────────────────
#
# One chronological, cross-channel log: texts and emails in BOTH directions,
# calls (AI and human, both directions) and voicemails - each row once, from
# the table that owns it. The scope is the one lead scope
# (lead_scope.authorized_lead_query), so an advisor sees their own leads and a
# manager the workspace, exactly as everywhere else. Nothing is synthesized:
# an inbound text is a Reply row, a call is a VoiceCall row, a voicemail is a
# Voicemail row. A voicemail left on a call is shown on that call, not again.
#
# `ref` is the source row's id. The screen shows it only to admins, in a
# collapsed technical drawer.

def _feed_lead_names(db: Session, ids) -> dict:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    rows = db.query(Lead.id, Lead.first_name, Lead.last_name, Lead.phone, Lead.email, Lead.is_test) \
             .filter(Lead.id.in_(ids)).all()
    out = {}
    for r in rows:
        name = ("%s %s" % (r.first_name or "", r.last_name or "")).strip() or r.phone or r.email or "Unnamed contact"
        out[r.id] = {"name": name, "phone": r.phone, "email": r.email, "is_test": bool(r.is_test)}
    return out


def _feed_actor(src):
    """Who produced an outbound message: ai | human | system, or None when the
    row never recorded it (old rows) - never guessed."""
    if not src:
        return None
    if is_ai_generated(src):
        return "ai"
    return "human" if src in (_src.MANUAL, _src.BULK) else "system"


def _clip(text, n=160):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


@router.get("/feed")
def activity_feed(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.models import Reply, VoiceCall
    from app.models.telephony_models import Voicemail
    cutoff = datetime.utcnow() - timedelta(days=days)
    scope = lead_scope.authorized_lead_query(db, current_user, Lead.id).subquery()
    in_scope = lambda col: col.in_(db.query(scope.c.id))  # noqa: E731
    items = []

    for m in (db.query(Message).filter(in_scope(Message.lead_id), Message.sent_at >= cutoff)
              .order_by(Message.sent_at.desc()).limit(limit).all()):
        src = getattr(m, "send_source", None)
        items.append({"kind": "message", "channel": "sms", "direction": "outbound", "ref": m.id,
                      "lead_id": m.lead_id, "at": iso_utc(m.sent_at), "summary": _clip(m.body),
                      "result": getattr(m, "delivery_status", None) or m.twilio_status or "pending",
                      "actor": _feed_actor(src),
                      "source_label": _src.source_label(src)})

    for e in (db.query(EmailMessage).filter(in_scope(EmailMessage.lead_id), EmailMessage.sent_at >= cutoff)
              .order_by(EmailMessage.sent_at.desc()).limit(limit).all()):
        src = getattr(e, "send_source", None)
        items.append({"kind": "message", "channel": "email", "direction": "outbound", "ref": e.id,
                      "lead_id": e.lead_id, "at": iso_utc(e.sent_at),
                      "summary": _clip(e.subject or "(no subject)"), "result": e.status or "sent",
                      "actor": _feed_actor(src),
                      "source_label": _src.source_label(src)})

    for r in (db.query(Reply).filter(in_scope(Reply.lead_id), Reply.received_at >= cutoff)
              .order_by(Reply.received_at.desc()).limit(limit).all()):
        cls = getattr(r.classification, "value", r.classification)
        items.append({"kind": "message", "channel": "email" if (r.source or "sms") == "email" else "sms",
                      "direction": "inbound", "ref": r.id, "lead_id": r.lead_id, "at": iso_utc(r.received_at),
                      "summary": _clip(r.body), "result": "hot" if r.is_hot else (cls or "received"),
                      "actor": "customer", "source_label": None})

    calls_by_sid = {}
    for c in (db.query(VoiceCall).filter(in_scope(VoiceCall.lead_id), VoiceCall.created_at >= cutoff)
              .order_by(VoiceCall.created_at.desc()).limit(limit).all()):
        direction = c.direction or "outbound"
        human = bool(c.is_human_call) or c.provider == "manual"
        result = c.disposition or c.outcome or c.answered_by or c.status
        items.append({"kind": "call", "channel": "voice", "direction": direction, "ref": c.id,
                      "lead_id": c.lead_id, "at": iso_utc(c.started_at or c.created_at),
                      "summary": _clip(c.summary or c.disposition_notes or ""),
                      "result": (result or "unknown").replace("_", " "),
                      "duration_seconds": c.duration_seconds, "voicemail_left": bool(c.voicemail_left),
                      "has_transcript": bool(c.transcript or c.voicemail_transcript),
                      "has_recording": bool(c.recording_sid or c.recording_url),
                      "actor": ("customer" if direction == "inbound" else "human" if human else "ai"),
                      "source_label": "Call from own phone" if c.provider == "manual" else None})
        if c.call_sid:
            calls_by_sid[c.call_sid] = items[-1]

    for v in (db.query(Voicemail).filter(in_scope(Voicemail.lead_id), Voicemail.received_at >= cutoff)
              .order_by(Voicemail.received_at.desc()).limit(limit).all()):
        call = calls_by_sid.get(v.call_sid) if v.call_sid else None
        if call is not None:
            # The same event as that inbound call: shown once, on the call.
            call["voicemail_received"] = True
            call["has_recording"] = call.get("has_recording") or bool(v.recording_sid or v.recording_url)
            if v.transcript and not call.get("summary"):
                call["summary"] = _clip(v.transcript)
            continue
        items.append({"kind": "voicemail", "channel": "voice", "direction": "inbound", "ref": v.id,
                      "lead_id": v.lead_id, "at": iso_utc(v.received_at),
                      "summary": _clip(v.transcript or ""), "result": v.status or "new",
                      "duration_seconds": v.duration_seconds,
                      "has_recording": bool(v.recording_sid or v.recording_url),
                      "audio_path": ("/voicemails/%s/audio" % v.id) if (v.recording_sid or v.recording_url) else None,
                      "actor": "customer", "source_label": None})

    items.sort(key=lambda x: x["at"] or "", reverse=True)
    items = items[:limit]
    names = _feed_lead_names(db, [i["lead_id"] for i in items])
    for i in items:
        info = names.get(i["lead_id"]) or {}
        i["lead_name"] = info.get("name") or "Unknown contact"
        i["lead_phone"] = info.get("phone")
        i["lead_email"] = info.get("email")
        i["is_test"] = bool(info.get("is_test"))
        i["id"] = "%s:%s" % (i["kind"] if i["kind"] != "message" else i["channel"] + "-" + i["direction"], i["ref"])
    return {"items": items, "days": days, "limit": limit, "capped": len(items) >= limit}
