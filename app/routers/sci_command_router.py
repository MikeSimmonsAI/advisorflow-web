"""SCI COMMAND - the phone command center (Mike's two-phone mockup, Oct 14).

Families needing attention come first, statistics second. Everything the
screens show is read from the systems that already own it (replies, the
appointment setter, bookings, notes, outbound messages); the only writes here
are the two actions that had no home yet: SCHEDULE (book a real calendar time
through the appointment engine) and ASSIGN. Reply / Email / Note / Close use
the existing endpoints (/sms/send, /email/send, /work/leads/{id}/notes,
/program/responses/{id}/mark).

Test / synthetic contacts never appear in the counts or the inbox.
Owner-only system issues are returned only to the owner, as one compact line.
"""
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer, require_tenant_user
from app.models.models import Lead, Message, User
from app.models.program_models import (LocationProfile, ProgramConversation, ProgramResponse,
                                       ProgramSourceRecord)
from app.routers.program_router import _program, _require_manager
from app.services import test_records as _tr

router = APIRouter(prefix="/program/command", tags=["sci-command"])

# Priority: who needs a person first.
_RANK = {"hot": 0, "active": 1, "low": 2, "bad_data": 3, "wrong_person": 3, "auto_reply": 4}


def _team(db, org_id):
    """Everyone who works in this customer workspace: users whose home is the
    organization AND users given a seat in it (memberships) - SCI's staff
    hold seats, so the old home-organization query listed nobody and Assign
    had no one to choose."""
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    seat_ids = [i for (i,) in db.query(Membership.user_id).filter(Membership.scope_type == SCOPE_CUSTOMER_ORG,
                                                                  Membership.scope_id == org_id,
                                                                  Membership.is_active.is_(True)).all()]
    q = db.query(User).filter(User.is_active.is_(True))
    from sqlalchemy import or_
    users = q.filter(or_(User.organization_id == org_id, User.id.in_(seat_ids) if seat_ids else False)).all()
    return sorted({u.id: u for u in users if getattr(u, "role", None) != "god_admin"}.values(),
                  key=lambda u: (u.full_name or u.email or "").lower())


def _is_demo(lead) -> bool:
    from app.services.sci_staging_demo import is_demo_lead
    return is_demo_lead(lead)


def _central_day_start_utc():
    from zoneinfo import ZoneInfo
    tz, utc = ZoneInfo("America/Chicago"), ZoneInfo("UTC")
    today = datetime.now(utc).astimezone(tz).date()
    return datetime.combine(today, datetime.min.time(), tz).astimezone(utc).replace(tzinfo=None)


def _locations(db, org_id):
    return {p.location_id: p.official_name for p in db.query(LocationProfile)
            .filter(LocationProfile.organization_id == org_id, LocationProfile.is_review_bucket.isnot(True)).all()}


def _badge(r, conv) -> str:
    if conv is not None and conv.state == "booked":
        return "Booked"
    if r.response_class == "hot":
        return "New" if r.handling_status == "new" else "Hot"
    if r.response_class in ("bad_data", "wrong_person"):
        return "Review"
    if r.response_class in ("low", "auto_reply"):
        return "Low"
    return "New" if r.handling_status == "new" else "Active"


def _actions(r, conv) -> list:
    if r.response_class == "auto_reply":
        return ["review", "dismiss"]
    if r.response_class in ("bad_data", "wrong_person"):
        return ["reply", "note", "close"]
    if r.response_class == "low":
        return ["reply", "snooze", "call"]
    return ["reply", "call", "schedule"]


@router.get("")
def command_home(location_id: Optional[str] = None, q: Optional[str] = None, limit: int = Query(40, le=200),
                 db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.models.models import BookingLink
    from app.services.programs import engagement as _eng
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    locs = _locations(db, org)
    start = _central_day_start_utc()

    rq = _tr.exclude_test_responses(db.query(ProgramResponse).filter(ProgramResponse.organization_id == org))
    if location_id:
        rq = rq.filter(ProgramResponse.location_id == location_id)
    open_rows = rq.filter(ProgramResponse.handling_status != "closed").all()
    today_rows = rq.filter(ProgramResponse.received_at >= start,
                           ProgramResponse.response_class.notin_(("auto_reply",))).count()
    # one inbox line per family: their newest open reply
    newest = {}
    for r in sorted(open_rows, key=lambda r: r.received_at or datetime.min, reverse=True):
        newest.setdefault(r.lead_id, r)
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(list(newest))).all()} if newest else {}
    convs = {c.lead_id: c for c in db.query(ProgramConversation)
             .filter(ProgramConversation.lead_id.in_(list(newest))).order_by(ProgramConversation.created_at).all()} \
        if newest else {}
    needle = (q or "").strip().lower()
    items = []
    for lid, r in newest.items():
        lead = leads.get(lid)
        name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() if lead else "Family"
        loc = locs.get(r.location_id)
        if needle and not any(needle in (s or "").lower() for s in
                              (name, loc, getattr(lead, "phone", ""), getattr(lead, "email", ""))):
            continue
        conv = convs.get(lid)
        items.append({"lead_id": lid, "response_id": r.id, "name": name, "location": loc,
                      "phone": getattr(lead, "phone", None),
                      "location_id": r.location_id, "summary": (r.body_excerpt or r.summary or "")[:160],
                      "class": r.response_class, "badge": _badge(r, conv), "channel": r.channel,
                      "received_at": r.received_at.isoformat() + "Z" if r.received_at else None,
                      "sla_breached": bool(r.response_class == "hot" and r.handling_status == "new"
                                           and r.sla_due_at and r.sla_due_at <= datetime.utcnow()),
                      "setter_state": conv.state if conv else None, "actions": _actions(r, conv)})
    items.sort(key=lambda i: (not i["sla_breached"], _RANK.get(i["class"], 5),
                              -(datetime.fromisoformat(i["received_at"][:-1]).timestamp() if i["received_at"] else 0)))
    needs = sum(1 for i in items if i["class"] in ("hot", "active", "bad_data", "wrong_person"))

    org_leads = db.query(Lead.id).filter(Lead.organization_id == org, Lead.is_test.isnot(True))
    booked = (db.query(BookingLink).filter(BookingLink.lead_id.in_(org_leads),
                                           BookingLink.status.in_(("booked", "confirmed")),
                                           BookingLink.booked_time.isnot(None),
                                           BookingLink.booked_time >= datetime.utcnow() - timedelta(hours=8)).count())
    contacted = (_eng.summary(db, org, since=start) or {}).get("contacted", 0)

    owner_alert = None
    if getattr(user, "role", None) == "god_admin":
        try:
            from app.services.programs import self_check
            bad = [c for c in self_check.run(db, org)["checks"] if not c["ok"] and not c.get("acknowledged")]
            if bad:
                owner_alert = {"count": len(bad), "text": "%s · %s" % (bad[0]["title"], bad[0]["detail"])}
        except Exception:                                # noqa: BLE001
            owner_alert = None
    callbacks = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org,
                                                      ProgramConversation.is_test.isnot(True),
                                                      ProgramConversation.state == "callback_requested").count())
    return {
        "locations": [{"id": k, "name": v} for k, v in sorted(locs.items(), key=lambda kv: kv[1])],
        "counts": {"needs_attention": needs, "replies_today": today_rows, "visits_booked": booked,
                   "contacted_today": contacted},
        "owner_alert": owner_alert,
        "inbox": items[:limit],
        "tasks": _open_tasks(db, org),
        "upcoming": {"follow_up": len(items), "new": sum(1 for i in items if i["badge"] == "New"),
                     "review": sum(1 for i in items if i["badge"] == "Review"),
                     "callbacks": callbacks, "visits_booked": booked},
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _suggest(conv, latest, lead) -> dict:
    """The next step, from what the family said and where the conversation is."""
    intents = json.loads(latest.intents or "[]") if latest is not None else []
    steps = []
    if "information_request" in intents or "general_planning" in intents:
        steps.append("Send planning guide")
    if latest is not None and ("?" in (latest.body_excerpt or "") or latest.response_class == "active"):
        steps.append("Answer questions")
    if conv is not None and conv.state == "callback_requested":
        steps.insert(0, "Call back: %s" % (conv.callback_window or "they asked for a call"))
    if conv is None or conv.state not in ("booked",):
        steps.append("Offer a personal visit")
    title = " + ".join(s.split(":")[0].lower() for s in steps) if steps else "Keep the conversation going"
    return {"title": title[:1].upper() + title[1:], "steps": steps}


@router.get("/search")
def command_search(q: str = Query("", max_length=80), location_id: Optional[str] = None,
                   limit: int = Query(25, le=50), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    """Every SCI family - not just the ones who replied - by name, phone,
    email or location. Read-only."""
    from sqlalchemy import func, or_
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    needle = (q or "").strip()
    if len(needle) < 2:
        return {"items": []}
    locs = _locations(db, org)
    low = needle.lower()
    digits = "".join(ch for ch in needle if ch.isdigit())
    like = "%" + low + "%"
    conds = [func.lower(Lead.first_name).like(like), func.lower(Lead.last_name).like(like),
             func.lower(Lead.email).like(like),
             func.lower(func.coalesce(Lead.first_name, "") + " " + func.coalesce(Lead.last_name, "")).like(like)]
    if len(digits) >= 4:
        conds.append(Lead.phone.like("%" + digits + "%"))
    loc_ids = [k for k, v in locs.items() if low in (v or "").lower()]
    if loc_ids:
        conds.append(Lead.id.in_(db.query(ProgramSourceRecord.lead_id)
                                 .filter(ProgramSourceRecord.organization_id == org,
                                         ProgramSourceRecord.location_id.in_(loc_ids))))
    qry = db.query(Lead).filter(Lead.organization_id == org, Lead.is_test.isnot(True), or_(*conds))
    if location_id:
        qry = qry.filter(Lead.id.in_(db.query(ProgramSourceRecord.lead_id)
                                     .filter(ProgramSourceRecord.organization_id == org,
                                             ProgramSourceRecord.location_id == location_id)))
    leads = qry.order_by(Lead.last_name, Lead.first_name).limit(limit).all()
    ids = [l.id for l in leads]
    recs = {r.lead_id: r for r in db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org, ProgramSourceRecord.lead_id.in_(ids or ["-"]))}
    replied = {i for (i,) in db.query(ProgramResponse.lead_id).filter(ProgramResponse.lead_id.in_(ids or ["-"]))}
    return {"items": [{
        "lead_id": l.id,
        "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title() or "Family",
        "phone": l.phone,
        "location": locs.get(recs[l.id].location_id) if l.id in recs else None,
        "replied": l.id in replied,
    } for l in leads]}


@router.get("/family/{lead_id}")
def command_family(lead_id: str, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    from app.models.models import BookingLink, EmailMessage
    from app.services.programs import appointments as A
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    rec = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == prog.organization_id,
                                                ProgramSourceRecord.lead_id == lead.id).first())
    locs = _locations(db, prog.organization_id)
    resps = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
             .order_by(ProgramResponse.received_at).all())
    latest = resps[-1] if resps else None
    conv = (db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id)
            .order_by(ProgramConversation.created_at.desc()).first())
    thread = [{"who": "family", "text": r.body_excerpt, "channel": r.channel,
               "at": r.received_at.isoformat() + "Z" if r.received_at else None} for r in resps]
    for m in db.query(Message).filter(Message.lead_id == lead.id).all():
        thread.append({"who": "us", "text": m.body, "channel": "sms", "source": m.send_source,
                       "delivery": m.delivery_status, "at": m.sent_at.isoformat() + "Z" if m.sent_at else None})
    for e in db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).all():
        thread.append({"who": "us", "text": getattr(e, "subject", None), "channel": "email",
                       "at": e.created_at.isoformat() + "Z" if getattr(e, "created_at", None) else None})
    if conv is not None:
        from app.models.program_models import ProgramConversationTurn
        for t in (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == conv.id,
                                                            ProgramConversationTurn.role == "ai",
                                                            ProgramConversationTurn.delivery.in_(("draft", "simulated", "held")))):
            thread.append({"who": "ai", "text": t.text, "channel": "sms", "delivery": t.delivery,
                           "at": t.created_at.isoformat() + "Z" if t.created_at else None})
    thread = sorted([t for t in thread if t["at"]], key=lambda t: t["at"])
    assigned = db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else None
    last = thread[-1] if thread else None
    booking = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                            BookingLink.status.in_(("booked", "confirmed")))
               .order_by(BookingLink.booked_time.desc()).first())
    team = [{"id": u.id, "name": u.full_name or u.email} for u in _team(db, prog.organization_id)]
    return {
        "lead_id": lead.id, "is_test": bool(lead.is_test), "is_demo": _is_demo(lead),
        "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title(),
        "phone": lead.phone, "email": lead.email,
        "location": locs.get(rec.location_id) if rec else None,
        "campaign": (rec.campaign_family or "").replace("_", " ").title() if rec else None,
        "assigned_to": {"id": assigned.id, "name": assigned.full_name or assigned.email} if assigned else None,
        "team": team,
        "status": latest.handling_status if latest else None,
        "badge": _badge(latest, conv) if latest else None,
        "response_id": latest.id if latest else None,
        "suggested_reply": latest.suggested_reply if latest else None,
        "last_touch": {"at": last["at"], "channel": last["channel"]} if last else None,
        "thread": thread[-40:],
        "setter": {"state": conv.state, "offered": [A.describe(s) for s in A.offered(conv)],
                   "callback_window": conv.callback_window, "handoff_reason": conv.handoff_reason}
        if conv else None,
        "booking": {"when": booking.booked_time.isoformat(), "label": booking.appt_label,
                    "status": booking.status} if booking and booking.booked_time else None,
        "suggestion": _suggest(conv, latest, lead),
        "mail_task": _mail_task_of(conv),
    }


@router.get("/slots")
def command_slots(lead_id: str, kind: str = "visit", db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    """Two real open times on the program's booking calendar - the same
    times the booking page and the appointment setter would offer."""
    from app.services.programs import appointments as A
    prog = _program(db, user, managers_only=False)
    slots, why = A.available_slots(db, prog, want=4, kind=kind if kind in (A.PHONE, A.VISIT) else A.VISIT)
    return {"slots": [{"value": s.isoformat(), "label": A.describe(s),
                       "day": s.strftime("%a %b %-d"), "time": s.strftime("%-I:%M %p")} for s in slots],
            "reason": why}


class ScheduleIn(BaseModel):
    lead_id: str
    slot: str                       # local Central ISO, from /slots
    kind: str = "visit"             # visit | phone


@router.post("/schedule")
def command_schedule(body: ScheduleIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """A person books the family into a real time. Re-checked, booked through
    the booking page's own confirmation path, and only reported confirmed when
    the calendar reads back booked at that time."""
    from app.services.programs import appointments as A
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    try:
        slot = datetime.fromisoformat(body.slot[:16])
    except ValueError:
        raise HTTPException(status_code=422, detail="Pick one of the offered times.")
    kind = body.kind if body.kind in (A.PHONE, A.VISIT) else A.VISIT
    res = A.book(db, prog, lead, slot, kind)
    if res.get("ok") and not res.get("simulated"):
        conv = (db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id)
                .order_by(ProgramConversation.created_at.desc()).first())
        if conv is not None:
            conv.state, conv.booked_time = "booked", slot
        A.notify_team_booked(db, prog, lead, slot, kind)
        db.commit()
    return dict(res, label=A.describe(slot))


class AssignIn(BaseModel):
    lead_id: str
    user_id: Optional[str] = None


@router.post("/assign")
def command_assign(body: AssignIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    if body.user_id:
        who = next((u for u in _team(db, prog.organization_id) if u.id == body.user_id), None)
        if who is None:
            raise HTTPException(status_code=422, detail="That person is not on this team.")
    lead.assigned_to_id = body.user_id or None
    db.commit()
    return {"lead_id": lead.id, "assigned_to_id": lead.assigned_to_id}


class DemoSendIn(BaseModel):
    lead_id: str
    channel: str = "sms"            # sms | email
    text: str
    subject: Optional[str] = None


@router.post("/demo-send")
def command_demo_send(body: DemoSendIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user)):
    """A made-up staging demo family: record the reply in the thread as
    SIMULATED. Nothing is sent - the demo flag is refused by every sender."""
    from app.models.models import EmailMessage
    from app.services import send_source as _ss
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    if not _is_demo(lead):
        raise HTTPException(status_code=400, detail="Only demo families can be simulated.")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write something first.")
    if body.channel == "email":
        db.add(EmailMessage(lead_id=lead.id, sender_id=user.id, sent_by_user_id=user.id,
                            subject=(body.subject or "Following up").strip()[:200], body_html=text,
                            send_source=_ss.DEMO, status="simulated"))
    else:
        db.add(Message(lead_id=lead.id, sender_id=user.id, sent_by_user_id=user.id, body=text,
                       send_source=_ss.DEMO, delivery_status="simulated", send_state="simulated"))
    resp = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
            .order_by(ProgramResponse.received_at.desc()).first())
    if resp is not None and resp.handling_status in ("new", "opened"):
        resp.handling_status = "responded"
        resp.responded_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "simulated": True}


# ── people's tasks: a printed guide to mail ────────────────────────────────
# The setter (or a person) records the request; a PERSON mails it and marks
# it done here. Nothing claims a guide was mailed until someone says so.

def _mail_task_of(conv):
    from app.services.programs.ai_setter import memory_of
    if conv is None:
        return None
    mr = memory_of(conv).get("mail_request")
    if not isinstance(mr, dict) or not mr.get("requested"):
        return None
    return {"what": mr.get("what") or "printed planning guide", "note": mr.get("note"),
            "done": bool(mr.get("done")), "done_at": mr.get("done_at"), "done_by": mr.get("done_by")}


def _open_tasks(db, org_id):
    out = []
    test_ids = {i for (i,) in db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.is_test.is_(True))}
    rows = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org_id,
                                                 ProgramConversation.memory.isnot(None),
                                                 ProgramConversation.memory.like('%mail_request%')).all())
    for c in rows:
        t = _mail_task_of(c)
        if not t or t["done"] or c.lead_id in test_ids:
            continue
        lead = db.query(Lead).filter(Lead.id == c.lead_id).first()
        if lead is None:
            continue
        out.append({"kind": "mail_guide", "lead_id": lead.id, "what": t["what"], "note": t.get("note"),
                    "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title()})
    return out


class MailTaskIn(BaseModel):
    lead_id: str
    note: Optional[str] = None
    done: bool = False


@router.post("/mail-task")
def command_mail_task(body: MailTaskIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Record that a family asked for a printed guide by mail (done=False), or
    mark it mailed (done=True) - by the person who did it."""
    import json as _json
    from app.models.program_models import ProgramAlert
    from app.services.programs import ai_setter
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    conv = ai_setter.conversation_for(db, lead)
    mem = ai_setter.memory_of(conv)
    mr = mem.get("mail_request") if isinstance(mem.get("mail_request"), dict) else {}
    mr.update({"requested": True, "what": mr.get("what") or "printed planning guide"})
    if body.note:
        mr["note"] = body.note.strip()[:300]
    who = getattr(user, "full_name", None) or getattr(user, "email", None) or "staff"
    name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A family"
    if body.done:
        mr.update({"done": True, "done_at": datetime.utcnow().isoformat() + "Z", "done_by": who})
        msg = "%s marked the printed guide for %s as mailed." % (who, name)
    else:
        mr.update({"done": False})
        msg = "%s asked for a printed planning guide by mail. Confirm the address and mail it, then mark it done." % name
    mem["mail_request"] = mr
    conv.memory = _json.dumps(mem)
    db.add(ProgramAlert(organization_id=prog.organization_id, kind="task", audience="team", channel="in_app",
                        delivered=True, reason="mail_guide_done" if body.done else "mail_guide", message=msg))
    db.commit()
    return {"ok": True, "mail_task": _mail_task_of(conv)}
