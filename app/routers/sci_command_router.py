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


def _local_now():
    """Booked times are stored as Central wall time - compare with Central now."""
    from zoneinfo import ZoneInfo
    from app.services.booking_time import DEFAULT_ZONE
    return datetime.now(ZoneInfo(DEFAULT_ZONE)).replace(tzinfo=None)


def _home_visit_waiting(conv) -> bool:
    """The family asked us to come to them and nobody has arranged it yet."""
    from app.services.programs.ai_setter import HOME_VISIT_REASON, memory_of
    return bool(conv is not None and conv.state == "human" and conv.handoff_reason == HOME_VISIT_REASON
                and not (memory_of(conv).get("home_visit") or {}).get("done"))


def ai_setter_conv(db, lead_id):
    return (db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead_id)
            .order_by(ProgramConversation.created_at.desc()).first())


def _home_visit_of(conv) -> Optional[dict]:
    from app.services.programs.ai_setter import HOME_VISIT_REASON, memory_of
    if conv is None or conv.handoff_reason != HOME_VISIT_REASON:
        return None
    return dict(memory_of(conv).get("home_visit") or {}, waiting=_home_visit_waiting(conv))


def _badge(r, conv) -> str:
    if conv is not None and conv.state == "booked":
        return "Booked"
    if _home_visit_waiting(conv):
        return "Home visit"
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


_NEEDS_REPLY = ("new", "opened")
_APPT_STATES = ("offered", "booked", "callback_requested")
FILTERS = ("all", "needs_reply", "text", "email", "handled", "unread", "ai", "human", "appointments", "home_visits")


def _sender(db, r, lead) -> dict:
    """WHO ACTUALLY WROTE: an email's verified sender (address and display
    name, as the mail server gave them) is shown as itself - never relabelled
    as the person on the family record it was matched to."""
    if (r.channel or "") != "email":
        return {"sender_email": None, "sender_name": None, "subject": None}
    from app.services.programs import email_reply
    m = email_reply.inbound_meta(db, r)
    return {"sender_email": m.get("sender_email") or (getattr(lead, "email", None) if lead else None),
            "sender_name": m.get("sender_name"), "subject": m.get("subject")}


@router.get("")
def command_home(location_id: Optional[str] = None, q: Optional[str] = None, limit: int = Query(40, le=200),
                 view: str = Query("all", alias="filter"),
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
    owner_test_ids = set()
    if False:  # test conversations are never shown in SCI Live (owner, Oct 15)
        # The owner sees his OWN live test conversation (marked TEST) so he can
        # run it from his phone. Nobody else does, and it is never counted.
        from app.services.test_records import OWNER_TEST_LAST_NAME
        own = [i for (i,) in db.query(Lead.id).filter(Lead.organization_id == org, Lead.is_test.is_(True),
                                                       Lead.last_name == OWNER_TEST_LAST_NAME)]
        if own:
            mine = (db.query(ProgramResponse).filter(ProgramResponse.lead_id.in_(own),
                                                     ProgramResponse.handling_status != "closed",
                                                     ProgramResponse.received_at >= datetime.utcnow() - timedelta(days=2)))
            if location_id:
                mine = mine.filter(ProgramResponse.location_id == location_id)
            mine = mine.all()
            owner_test_ids = {r.lead_id for r in mine}
            open_rows = open_rows + mine
    flt = view if view in FILTERS else "all"
    if flt == "handled":
        # Handled = answered or closed in the last 14 days.
        open_rows = (rq.filter(ProgramResponse.handling_status.in_(("responded", "active", "closed")),
                               ProgramResponse.received_at >= datetime.utcnow() - timedelta(days=14)).all())
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
    _adv_ids = {l.assigned_to_id for l in leads.values() if l.assigned_to_id}
    advisors = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(list(_adv_ids)))} \
        if _adv_ids else {}
    needle = (q or "").strip().lower()
    items = []
    for lid, r in newest.items():
        lead = leads.get(lid)
        name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() if lead else "Family"
        loc = locs.get(r.location_id)
        who = _sender(db, r, lead)
        if needle and not any(needle in (s or "").lower() for s in
                              (name, loc, getattr(lead, "phone", ""), getattr(lead, "email", ""),
                               who["sender_email"], who["sender_name"], who["subject"])):
            continue
        conv = convs.get(lid)
        items.append({"lead_id": lid, "response_id": r.id, "name": name, "location": loc,
                      "phone": getattr(lead, "phone", None),
                      "channel_label": "EMAIL" if r.channel == "email" else ("VOICEMAIL" if r.channel == "voicemail" else "TEXT"),
                      "is_test": lid in owner_test_ids,
                      "handling_status": r.handling_status,
                      "owner": "human" if (conv is not None and conv.state in ("human", "stopped")) else "ai",
                      **who,
                      "location_id": r.location_id, "summary": (r.body_excerpt or r.summary or "")[:160],
                      "class": r.response_class, "badge": _badge(r, conv), "channel": r.channel,
                      "received_at": r.received_at.isoformat() + "Z" if r.received_at else None,
                      "sla_breached": bool(r.response_class == "hot" and r.handling_status == "new"
                                           and r.sla_due_at and r.sla_due_at <= datetime.utcnow()),
                      "setter_state": conv.state if conv else None, "actions": _actions(r, conv),
                      "home_visit": _home_visit_waiting(conv),
                      "advisor": advisors.get(getattr(lead, "assigned_to_id", None))})
    items.sort(key=lambda i: (not i["sla_breached"], not i["home_visit"], _RANK.get(i["class"], 5),
                              -(datetime.fromisoformat(i["received_at"][:-1]).timestamp() if i["received_at"] else 0)))
    needs = sum(1 for i in items if i["class"] in ("hot", "active", "bad_data", "wrong_person") and not i["is_test"])
    filter_counts = {"all": len(items),
                     "needs_reply": sum(1 for i in items if i["handling_status"] in _NEEDS_REPLY
                                        and i["class"] != "auto_reply"),
                     "text": sum(1 for i in items if i["channel"] != "email"),
                     "email": sum(1 for i in items if i["channel"] == "email"),
                     "unread": sum(1 for i in items if i["handling_status"] == "new"),
                     "ai": sum(1 for i in items if i["owner"] == "ai"),
                     "human": sum(1 for i in items if i["owner"] == "human"),
                     "appointments": sum(1 for i in items if i["setter_state"] in _APPT_STATES),
                     "home_visits": sum(1 for i in items if i["home_visit"])}
    if flt == "needs_reply":
        items = [i for i in items if i["handling_status"] in _NEEDS_REPLY and i["class"] != "auto_reply"]
    elif flt == "text":
        items = [i for i in items if i["channel"] != "email"]
    elif flt == "email":
        items = [i for i in items if i["channel"] == "email"]
    elif flt == "unread":
        items = [i for i in items if i["handling_status"] == "new"]
    elif flt in ("ai", "human"):
        items = [i for i in items if i["owner"] == flt]
    elif flt == "appointments":
        items = [i for i in items if i["setter_state"] in _APPT_STATES]
    elif flt == "home_visits":
        items = [i for i in items if i["home_visit"]]

    org_leads = db.query(Lead.id).filter(Lead.organization_id == org, Lead.is_test.isnot(True))
    booked = (db.query(BookingLink).filter(BookingLink.lead_id.in_(org_leads),
                                           BookingLink.status.in_(("booked", "confirmed")),
                                           BookingLink.booked_time.isnot(None),
                                           BookingLink.booked_time >= _local_now() - timedelta(hours=2)).count())
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
        "filter": flt, "filter_counts": filter_counts,
        "owner_alert": owner_alert,
        "inbox": items[:limit],
        "tasks": _open_tasks(db, org),
        "unknown": _unknown_senders(db, org, location_id),
        "upcoming": {"follow_up": len(items), "new": sum(1 for i in items if i["badge"] == "New"),
                     "review": sum(1 for i in items if i["badge"] == "Review"),
                     "callbacks": callbacks, "visits_booked": booked},
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


def _unknown_senders(db, org, location_id=None) -> list:
    """Open replies from numbers / addresses that match no family (texts,
    voicemails, emails). Never hidden: SCI Live lists them so a person can
    call back, play the voicemail and mark them handled."""
    from app.models.program_models import ProgramUnmatchedReply
    from app.models.telephony_models import Voicemail
    q = db.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.organization_id == org,
                                               ProgramUnmatchedReply.status == "open")
    if location_id:
        q = q.filter(ProgramUnmatchedReply.location_id == location_id)
    locs = _locations(db, org)
    # The owner's own test phone / email are tests, never "unknown senders".
    from app.models.program_models import OutreachProgram
    from app.services.test_records import _digits
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org).first()
    own_phone = _digits(getattr(prog, "alert_phone", None)) if prog else ""
    own_email = (getattr(prog, "alert_email", None) or "").strip().lower() if prog else ""
    out = []
    for u in q.order_by(ProgramUnmatchedReply.received_at.desc()).limit(50).all():
        frm = (u.from_address or "").strip().lower()
        if (own_phone and _digits(frm) == own_phone) or (own_email and frm == own_email):
            continue
        mid = u.mailbox_message_id or ""
        is_vm = mid.startswith("vm:") or (u.subject or "").lower() == "voicemail"
        is_email = "@" in (u.from_address or "")
        audio = None
        if is_vm:
            key = mid[3:]
            vm = (db.query(Voicemail).filter(Voicemail.organization_id == org,
                                             (Voicemail.recording_sid == key) | (Voicemail.id == key)).first()
                  if key else None)
            if vm is not None and (vm.recording_sid or vm.recording_url):
                audio = {"path": "/voicemails/%s/audio" % vm.id, "duration": vm.duration_seconds}
        out.append({"id": u.id, "from": u.from_address, "location": locs.get(u.location_id),
                    "channel_label": "VOICEMAIL" if is_vm else ("EMAIL" if is_email else "TEXT"),
                    "subject": u.subject if is_email else None, "summary": (u.body_excerpt or "")[:300],
                    "received_at": u.received_at.isoformat() + "Z" if u.received_at else None,
                    "audio_path": audio["path"] if audio else None,
                    "duration": audio["duration"] if audio else None})
    return out


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


_ERR = {"30007": "Carrier filtered (link)", "30003": "Not reached - phone off or no signal, will try again", "30005": "Not reached - number not active right now, will try again",
        "30006": "Landline or unreachable carrier", "30008": "Unknown carrier error", "21610": "Opted out (STOP)",
        "30004": "Blocked by recipient"}


@router.get("/pipeline")
def command_pipeline(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """THE OUTREACH OPERATION at a glance, real families only (test records
    excluded): how many are loaded, not yet contacted, enrolled today, first
    vs later touches sent and delivered today, holds by channel, failures with
    reasons, replies, AI conversations, appointments, and the daily batch."""
    from app.models.models import CadenceState, CadenceTouchLog, EmailMessage
    from app.services.programs import daily_batch, email_touches
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    start = _central_day_start_utc()
    real = (db.query(Lead.id).join(ProgramSourceRecord, ProgramSourceRecord.lead_id == Lead.id)
            .filter(Lead.organization_id == org, Lead.is_test.isnot(True),
                    ProgramSourceRecord.organization_id == org).distinct())
    real_ids = [i for (i,) in real.all()]
    texted = {i for (i,) in db.query(Message.lead_id).filter(Message.lead_id.in_(real_ids or ["-"])).distinct()}
    emailed = {i for (i,) in db.query(EmailMessage.lead_id).filter(EmailMessage.lead_id.in_(real_ids or ["-"]),
                                                                   EmailMessage.status == "sent").distinct()}
    states = db.query(CadenceState).filter(CadenceState.lead_id.in_(real_ids or ["-"])).all()
    logs = (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id.in_(real_ids or ["-"]),
                                             CadenceTouchLog.outcome == "sent",
                                             CadenceTouchLog.attempted_at >= start).all())
    msg_ids = [l.message_id for l in logs if l.message_id]
    status = {m.id: (m.delivery_status or m.twilio_status) for m in
              db.query(Message).filter(Message.id.in_(msg_ids or ["-"])).all()}

    def touches(first):
        rows = [l for l in logs if (l.touch_number == 1) == first and (l.channel or "sms") == "sms"]
        return {"sent": len(rows), "delivered": sum(1 for l in rows if status.get(l.message_id) == "delivered"),
                "failed": sum(1 for l in rows if status.get(l.message_id) in ("failed", "undelivered"))}
    failed = (db.query(Message).filter(Message.lead_id.in_(real_ids or ["-"]), Message.sent_at >= start,
                                       (Message.delivery_status.in_(("failed", "undelivered")))
                                       | (Message.twilio_status.in_(("failed", "undelivered")))).all())
    reasons: dict = {}
    for m in failed:
        k = _ERR.get(m.error_code or "", "Error %s" % (m.error_code or "unknown"))
        reasons[k] = reasons.get(k, 0) + 1
    leads = db.query(Lead).filter(Lead.id.in_(real_ids or ["-"])).all()
    holds_email = sum(1 for l in leads if email_touches.provider_hold_reason(prog, l.email))
    sms_not_textable = sum(1 for l in leads if (getattr(l, "phone_line_type", None) or "") not in ("mobile", ""))
    on_hold = (db.query(ProgramSourceRecord.lead_id).filter(ProgramSourceRecord.organization_id == org,
                                                            ProgramSourceRecord.on_hold.is_(True)).distinct().count())
    replies_today = (_tr.exclude_test_responses(db.query(ProgramResponse).filter(
        ProgramResponse.organization_id == org, ProgramResponse.received_at >= start,
        ProgramResponse.response_class.notin_(("auto_reply",)))).count())
    ai_convs = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org,
                                                     ProgramConversation.is_test.isnot(True),
                                                     ProgramConversation.ai_turns > 0).count())
    from app.models.models import BookingLink
    booked = [b for b in db.query(BookingLink).filter(BookingLink.lead_id.in_(real_ids or ["-"]),
                                                      BookingLink.status.in_(("booked", "confirmed", "completed")))]
    offered = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org,
                                                    ProgramConversation.is_test.isnot(True),
                                                    ProgramConversation.offered_at.isnot(None)).count())
    batch = daily_batch.status(prog)
    return {
        "loaded": len(real_ids),
        "not_yet_contacted": sum(1 for i in real_ids if i not in texted and i not in emailed),
        "in_outreach": sum(1 for st in states if st.status == "active"),
        "enrolled_today": sum(1 for st in states if st.cadence_started_at and st.cadence_started_at >= start),
        "first_touches_today": touches(True),
        "later_touches_today": touches(False),
        "email_holds": holds_email,
        "not_textable": sms_not_textable,
        "on_hold_for_review": on_hold,
        "failed_today": {"count": len(failed), "reasons": reasons},
        "replies_today": replies_today,
        "ai_conversations": ai_convs,
        "appointments_offered": offered,
        "appointments_confirmed": sum(1 for b in booked if getattr(b, "calendar_event_id", None)),
        "daily_batch": batch,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


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
    from app.services.programs import email_reply as _er
    thread = []
    # Voicemails: the recording plays through the authenticated proxy
    # (/voicemails/{id}/audio) - the provider URL never reaches the browser.
    from app.models.telephony_models import Voicemail
    vms = [v for v in db.query(Voicemail).filter(Voicemail.lead_id == lead.id,
                                                  Voicemail.organization_id == prog.organization_id).all()
           if v.recording_sid or v.recording_url]
    used = set()
    for r in resps:
        item = {"who": "family", "text": r.body_excerpt, "channel": r.channel, "response_id": r.id,
                "status": r.handling_status, "at": r.received_at.isoformat() + "Z" if r.received_at else None}
        if r.channel == "email":
            item.update(_sender(db, r, lead))
        if r.channel == "voicemail" and vms:
            near = min((v for v in vms if v.id not in used),
                       key=lambda v: abs(((v.received_at or r.received_at) - r.received_at).total_seconds())
                       if r.received_at else 0, default=None)
            if near is not None:
                used.add(near.id)
                item.update(audio_path="/voicemails/%s/audio" % near.id, duration=near.duration_seconds)
        thread.append(item)
    for v in vms:
        if v.id not in used:                 # a recording with no transcript reply yet
            thread.append({"who": "family", "text": v.transcript or "(Voicemail - transcript not ready yet)",
                           "channel": "voicemail", "audio_path": "/voicemails/%s/audio" % v.id,
                           "duration": v.duration_seconds,
                           "at": v.received_at.isoformat() + "Z" if v.received_at else None})
    for m in db.query(Message).filter(Message.lead_id == lead.id).all():
        thread.append({"who": "us", "text": m.body, "channel": "sms", "source": m.send_source,
                       "delivery": m.delivery_status, "at": m.sent_at.isoformat() + "Z" if m.sent_at else None})
    for e in db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).all():
        # The email ACTUALLY sent - its body, not only its subject line.
        thread.append({"who": "us", "text": _er.plain_text(e.body_html or "")[:4000], "subject": e.subject,
                       "channel": "email", "source": e.send_source, "to": getattr(e, "to_email", None),
                       "delivery": e.status, "error": getattr(e, "error", None),
                       "at": e.sent_at.isoformat() + "Z" if e.sent_at else None})
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
        "suggested_reply": (_er.strip_signature(db, lead, latest.suggested_reply)
                            if latest and latest.suggested_reply else None),
        "last_touch": {"at": last["at"], "channel": last["channel"]} if last else None,
        "thread": thread[-40:],
        "setter": {"state": conv.state, "offered": [A.describe(s) for s in A.offered(conv)],
                   "callback_window": conv.callback_window, "handoff_reason": conv.handoff_reason}
        if conv else None,
        "booking": {"when": booking.booked_time.isoformat(), "label": booking.appt_label,
                    "status": booking.status} if booking and booking.booked_time else None,
        "suggestion": _suggest(conv, latest, lead),
        "mail_task": _mail_task_of(conv),
        "home_visit": _home_visit_of(conv),
        "handling": _handling(db, conv, lead, latest),
        "reply_to": _reply_target(db, latest, lead),
    }


def _handling(db, conv, lead=None, latest=None) -> dict:
    opted_out = bool(lead is not None and ((getattr(lead, "status", None) or "") == "dnc"
                                           or (latest is not None and latest.response_class == "opt_out")))
    from app.services.programs import ai_setter
    by = None
    if conv is not None and getattr(conv, "owner_changed_by", None):
        u = db.query(User).filter(User.id == conv.owner_changed_by).first()
        by = (u.full_name or u.email) if u else None
    return {"owner": ai_setter.owner_of(conv) if not opted_out else "human",
            "opted_out": bool(opted_out or (conv is not None and conv.state == "stopped")),
            "reason": conv.handoff_reason if conv is not None else None,
            "changed_at": conv.owner_changed_at.isoformat() + "Z"
            if conv is not None and getattr(conv, "owner_changed_at", None) else None,
            "changed_by": by,
            "note": getattr(conv, "owner_note", None) if conv is not None else None}


def _reply_target(db, latest, lead) -> dict:
    """What pressing Reply answers: the newest family message, on its own
    channel. An email is answered by email, in its thread, to its sender."""
    if latest is None:
        return {"channel": "sms" if lead.phone else ("email" if lead.email else None), "response_id": None}
    if latest.channel == "email":
        from app.services.programs import email_reply
        m = email_reply.inbound_meta(db, latest)
        return {"channel": "email", "response_id": latest.id, "to": m.get("sender_email"),
                "to_name": m.get("sender_name"), "subject": m.get("subject"),
                "threadable": bool(m.get("threadable")),
                "why_not": None if m.get("threadable") else
                "This email is not in a connected mailbox, so it can't be answered in its thread."}
    return {"channel": "sms", "response_id": latest.id, "to": lead.phone}


class ReplyIn(BaseModel):
    lead_id: str
    channel: str
    text: str
    idempotency_key: str
    response_id: Optional[str] = None
    then: str = "human"               # who owns the conversation after this send: human | ai


@router.post("/reply")
def command_reply(body: ReplyIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """A person answers a family from SCI Live. An EMAIL reply goes back in
    the original thread to the verified sender, from the mailbox it came to; a
    TEXT goes out on the program line. Idempotent on idempotency_key. The
    family's message is marked responded only after the provider accepted it.
    `then` decides who has the conversation afterwards: "human" (a person
    keeps it) or "ai" ("Send & let AI continue": the AI answers the next
    reply, knowing what was just sent)."""
    from app.services import send_source as ss
    from app.services.programs import ai_setter, email_reply
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    if _is_demo(lead):
        raise HTTPException(status_code=409, detail="A staging demo family is never contacted.")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write a reply first.")
    # One approved sign-off, added by the system; {GUIDE} = the family's guide link.
    if body.channel == "sms" and ("{GUIDE}" in text or "http" in text.lower()):
        from app.services import sms_programs
        if sms_programs.is_sci_org(db, lead.organization_id) and not sms_programs.sms_links_enabled():
            raise HTTPException(status_code=422, detail=(
                "Links in texts are being blocked by carriers on this line (Twilio 30007). "
                "Send the guide link by email, or offer a printed copy by mail."))
    text = email_reply.guide_token(db, lead, email_reply.strip_signature(db, lead, text))
    if body.then not in ("human", "ai"):
        raise HTTPException(status_code=422, detail="then must be human or ai")
    resp = None
    if body.response_id:
        resp = (db.query(ProgramResponse).filter(ProgramResponse.id == body.response_id,
                                                 ProgramResponse.organization_id == prog.organization_id,
                                                 ProgramResponse.lead_id == lead.id).first())
        if resp is None:
            raise HTTPException(status_code=404, detail="That message is not in this family's conversation.")
    out: dict = {"channel": body.channel}
    if body.channel == "email":
        if resp is None or resp.channel != "email":
            raise HTTPException(status_code=422, detail="Choose the email you are answering.")
        res = email_reply.send_reply(db, prog, lead, resp, text, send_source=ss.MANUAL,
                                     idempotency_key="u:%s:%s" % (user.id, body.idempotency_key), actor_id=user.id)
        out.update(res)
        if not res.get("ok"):
            raise HTTPException(status_code=502, detail="Not sent: %s" % (res.get("error") or "the email was refused"))
        sent_at = datetime.utcnow()
    elif body.channel == "sms":
        from app.routers.compose_router import acting_advisor
        from app.services import send_guard
        from app.services.sms_service import send_sms
        token = send_guard.claim(db, lead.id, text)
        if token is None:
            raise HTTPException(status_code=409, detail=send_guard.DUPLICATE_DETAIL)
        try:
            msg = send_sms(db, acting_advisor(db, lead, user), lead, text, False,
                           send_source=ss.MANUAL, sent_by_user_id=user.id)
        except Exception as exc:                         # noqa: BLE001
            send_guard.release_after_failure(db, lead.id, text, token)
            raise HTTPException(status_code=400, detail="Not sent: %s" % str(exc)[:300])
        out.update(ok=True, message={"id": msg.id, "sid": msg.twilio_sid, "status": msg.twilio_status})
        sent_at = max(datetime.utcnow(), msg.sent_at or datetime.utcnow())
        if resp is not None:
            from app.services.programs import responses as R
            R.mark(db, resp, "responded", user)
    else:
        raise HTTPException(status_code=422, detail="channel must be sms or email")
    # Ownership AFTER the send, stamped after it, so the AI never reads this
    # manual message as "a person took over" when it was told to continue.
    try:
        conv = ai_setter.set_owner(db, lead, body.then, actor=user.id,
                                   note=("Owner replied and let AI continue" if body.then == "ai"
                                         else "Owner replied by hand"),
                                   at=sent_at + timedelta(seconds=1))
        out["handling"] = _handling(db, conv)
    except ValueError as exc:
        out["handling_error"] = str(exc)
    return out


class FollowUpIn(BaseModel):
    lead_id: str
    reason: str
    not_before: Optional[str] = None          # ISO UTC; never earlier than the spacing rule allows


@router.post("/follow-up")
def command_follow_up(body: FollowUpIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Schedule ONE AI follow-up for a family, written at send time from the
    whole conversation (including what a person already sent) for the reason
    given - e.g. invite them in with two real times. It goes through every
    production gate (AI Handling, AUTO, consent, contact hours, the 20-hour
    spacing backstop) and is cancelled automatically if the family writes
    first or a person takes the conversation. Never sends now."""
    from app.services import contact_hours
    from app.services.programs import ai_setter
    from app.services.programs import spacing as _sp
    _require_manager(db, user)
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    if (lead.status or "") == "dnc":
        raise HTTPException(status_code=409, detail="This family is Do Not Contact.")
    conv = ai_setter.conversation_for(db, lead)
    if conv.state in ("human", "stopped", "booked"):
        raise HTTPException(status_code=409, detail="Switch the conversation to AI Handling first (now: %s)." % conv.state)
    now = datetime.utcnow()
    at = now + timedelta(hours=1)
    last = _sp.last_text_at(db, lead.id)
    if last is not None:
        at = max(at, last + _sp.MIN_GAP)
    if body.not_before:
        try:
            at = max(at, datetime.fromisoformat(body.not_before.replace("Z", "")[:19]))
        except ValueError:
            raise HTTPException(status_code=422, detail="not_before must be an ISO time")
    if not contact_hours.check(lead, at).get("permitted"):
        at = contact_hours.next_permitted_utc(lead, at) or at
    conv.state = "info_requested" if conv.state not in ai_setter.FOLLOW_UP_STATES else conv.state
    conv.next_followup_at = at
    conv.followup_reason = (body.reason or "")[:250]
    conv.followups_sent = min(conv.followups_sent or 0, ai_setter.MAX_FOLLOWUPS - 1)
    ai_setter._turn(db, conv, "system", None, action="follow_up_scheduled",
                    reason="one follow-up at %s UTC: %s" % (at.strftime("%Y-%m-%d %H:%M"), conv.followup_reason))
    db.commit()
    return {"lead_id": lead.id, "follow_up_at": at.isoformat() + "Z", "reason": conv.followup_reason}


class HandlingIn(BaseModel):
    lead_id: str
    owner: str


@router.post("/handling")
def command_handling(body: HandlingIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """AI Handling / Human Handling for one family. Sends nothing and never
    re-processes an old reply: the owner takes the NEXT message."""
    from app.services.programs import ai_setter
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    try:
        conv = ai_setter.set_owner(db, lead, body.owner, actor=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="sci.conversation_owner", target_type="lead",
               target_id=lead.id, details={"owner": body.owner})
    db.commit()
    return {"lead_id": lead.id, "handling": _handling(db, conv),
            "program_mode": ai_setter.mode_of(prog)}


@router.get("/consent")
def command_consent(status: Optional[str] = None, q: Optional[str] = None, location_id: Optional[str] = None,
                    limit: int = Query(500, ge=1, le=2000),
                    db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Opt-ins & opt-outs: every real family's texting status, read only."""
    from app.services.programs import sci_consent
    prog = _program(db, user, managers_only=False)
    return sci_consent.listing(db, prog.organization_id, status=status, q=q, location_id=location_id, limit=limit)


class RestartTextsIn(BaseModel):
    lead_id: str
    from_touch: Optional[int] = None     # 1 = start the texts over; default: the next touch


# Never reopened here: the family said stop, replied, booked, or is do-not-contact.
_NO_RESTART = ("opted_out", "stopped_opted_out", "dnc", "stopped_dnc", "replied", "stopped_replied",
               "booked", "stopped_booked", "cancelled")


@router.post("/restart-texts")
def command_restart_texts(body: RestartTextsIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Restart a family's texts - from text 1 (from_touch=1) or from any touch.
    For texts the family never received through no choice of theirs: stopped
    as "no SMS consent" by a job that could not send (Oct 16), held for a
    location review, or blocked by the carrier (30007, Oct 15). The schedule
    gets a new start, so earlier attempts never cause a touch to be skipped
    or abandoned. Managers only; opt-outs, replies, bookings and do-not-contact
    are never reopened; every send still passes the SCI gate and spacing."""
    from app.models.models import CadenceState
    prog = _program(db, user, managers_only=True)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    if _tr.is_test_record(lead) or not _tr.is_outreach_eligible(lead):
        raise HTTPException(status_code=409, detail="This family is excluded from outreach.")
    if (lead.status or "").lower() in ("dnc", "replied", "booked", "hot"):
        raise HTTPException(status_code=409, detail="This family replied, booked or asked not to be contacted - a person has it.")
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is None:
        raise HTTPException(status_code=409, detail="This family has no text schedule.")
    if (st.status or "") in _NO_RESTART:
        raise HTTPException(status_code=409, detail="This text schedule ended for a reason a restart must not undo.")
    before = {"status": st.status, "touch": st.current_touch_number or 0}
    nxt = body.from_touch if body.from_touch else (st.current_touch_number or 0) + 1
    if nxt < 1 or nxt > 12:
        raise HTTPException(status_code=422, detail="from_touch must be a touch number.")
    now = datetime.utcnow()
    st.status = "active"
    st.completed_at = None
    st.current_touch_number = nxt - 1
    if body.from_touch:
        st.cadence_started_at = now          # a new run: days count from today
    st.next_touch_due_at = now
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="sci.restart_texts", target_type="lead",
               target_id=lead.id, details={"before": before, "next_touch": nxt})
    db.commit()
    return {"lead_id": lead.id, "status": st.status, "next_touch": nxt, "next_touch_at": now.isoformat() + "Z"}


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
    # Home visits a family asked for that nobody has arranged yet (#234 follow-up).
    from app.services.programs.ai_setter import HOME_VISIT_REASON
    for c in (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org_id,
                                                   ProgramConversation.state == "human",
                                                   ProgramConversation.handoff_reason == HOME_VISIT_REASON).all()):
        if c.lead_id in test_ids or not _home_visit_waiting(c):
            continue
        latest = ai_setter_conv(db, c.lead_id)          # only the family's current conversation counts
        if latest is None or latest.id != c.id:
            continue
        lead = db.query(Lead).filter(Lead.id == c.lead_id).first()
        if lead is None:
            continue
        out.append({"kind": "home_visit", "lead_id": lead.id, "what": "home visit",
                    "note": "They asked for a visit at home - call to set a time",
                    "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title()})
    return out


class HomeVisitIn(BaseModel):
    lead_id: str
    note: Optional[str] = None


@router.post("/home-visit")
def command_home_visit_arranged(body: HomeVisitIn, db: Session = Depends(get_db),
                                user: User = Depends(require_tenant_user)):
    """The advisor arranged the home visit the family asked for (by phone, on
    their own calendar): the task leaves the list, recorded by who did it.
    Nothing is booked or sent by this."""
    import json as _json
    from app.models.program_models import ProgramAlert
    from app.services.programs import ai_setter
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    conv = ai_setter.conversation_for(db, lead, create=False)
    if not _home_visit_waiting(conv):
        raise HTTPException(status_code=409, detail="This family has no home visit waiting to be arranged.")
    mem = ai_setter.memory_of(conv)
    who = getattr(user, "full_name", None) or getattr(user, "email", None) or "staff"
    mem["home_visit"] = {"done": True, "done_at": datetime.utcnow().isoformat() + "Z", "done_by": who,
                         "note": (body.note or "").strip()[:300] or None}
    mem["alternative_outcome"] = mem.get("alternative_outcome") or "accepted"
    conv.memory = _json.dumps(mem)
    name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A family"
    db.add(ProgramAlert(organization_id=prog.organization_id, kind="task", audience="team", channel="in_app",
                        delivered=True, reason="home_visit_arranged",
                        message="%s arranged the home visit for %s." % (who, name)))
    db.commit()
    return {"ok": True, "home_visit": mem["home_visit"]}


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


# ── SCI LIVE PERFORMANCE (Oct 15 redesign) ──────────────────────────────────
# Every number comes from one ledger (app/services/programs/sci_analytics.py)
# and every drill-down returns exactly the rows that number counts.
_TOUCHES = ("1", "2", "3", "4+", "2+", "3+")


def _analytics_window(range_: str, start: Optional[str], end: Optional[str]):
    from app.services.programs import sci_analytics as SA
    return SA.window(range_, start, end)


def _next_batch(prog) -> Optional[str]:
    """The next sending day (Central date) the daily batch will add families."""
    from datetime import date as _date
    from app.services.programs import daily_batch
    if not prog.daily_batch_count:
        return None
    local = daily_batch._local_now()
    d = local.date()
    if prog.daily_batch_last_date:
        try:
            d = max(d, _date.fromisoformat(prog.daily_batch_last_date) + timedelta(days=1))
        except ValueError:
            pass
    elif local.time() >= daily_batch.LATEST_START:
        d = d + timedelta(days=1)
    for i in range(8):
        day = d + timedelta(days=i)
        if str(day.isoweekday()) in (prog.daily_batch_days or "12345"):
            return day.isoformat()
    return None


def _link_retest(db, prog) -> dict:
    """The owner-number booking-link retest (Twilio ticket): the latest text
    with a link to the owner's own test contact, and what the carrier said."""
    owner = (db.query(Lead).filter(Lead.organization_id == prog.organization_id, Lead.is_test.is_(True),
                                   Lead.last_name == _tr.OWNER_TEST_LAST_NAME).first())
    if owner is None:
        return {"status": "no owner test contact"}
    m = (db.query(Message).filter(Message.lead_id == owner.id, Message.body.ilike("%http%"))
         .order_by(Message.sent_at.desc()).first())
    if m is None:
        return {"status": "not run"}
    return {"status": (m.delivery_status or m.twilio_status or "sent"), "at": m.sent_at.isoformat() + "Z" if m.sent_at else None,
            "error_code": m.error_code}


@router.get("/analytics")
def command_analytics(range_: str = Query("today", alias="range"), start: Optional[str] = None,
                      end: Optional[str] = None, location_id: Optional[str] = None,
                      db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Performance Home: recorded totals for the period (and all time), touch
    analytics, funnel, failure reasons, location performance, trend and the
    clearly separate projections. Real families only."""
    from app.services.programs import daily_batch, sci_analytics as SA
    prog = _program(db, user, managers_only=False)
    L = SA.ledger(db, prog.organization_id)
    out = SA.summary(L, _analytics_window(range_, start, end), location_id or None, daily_batch.status(prog))
    out["daily_batch"] = daily_batch.status(prog)
    out["overview"] = SA.overview(L, db, location_id or None)
    out["streams"] = SA.streams(L, location_id or None)
    out["next_batch"] = _next_batch(prog)
    out["link_retest"] = _link_retest(db, prog)
    out["data_as_of"] = L.built_at.isoformat() + "Z"
    out["timing_ms"] = L.timing
    out["locations_all"] = [{"id": k, "name": v} for k, v in sorted(L.locs.items(), key=lambda kv: kv[1] or "")]
    return out


@router.get("/analytics/records")
def command_analytics_records(metric: str, range_: str = Query("today", alias="range"), start: Optional[str] = None,
                              end: Optional[str] = None, location_id: Optional[str] = None,
                              channel: Optional[str] = None, touch: Optional[str] = None,
                              status: Optional[str] = None, limit: int = Query(1000, le=5000),
                              db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Metric drill-down: the exact records behind a number, with the same
    filters. `count` is the full number; `items` the newest `limit` rows."""
    from app.services.programs import sci_analytics as SA
    if metric not in SA.METRICS:
        raise HTTPException(status_code=400, detail="Unknown metric.")
    if touch and touch not in _TOUCHES:
        raise HTTPException(status_code=400, detail="Touch must be 1, 2, 3, 4+ or 2+.")
    if channel and channel not in ("sms", "email", "voicemail"):
        raise HTTPException(status_code=400, detail="Channel must be sms, email or voicemail.")
    prog = _program(db, user, managers_only=False)
    L = SA.ledger(db, prog.organization_id)
    w = _analytics_window(range_, start, end)
    f = {k: v for k, v in (("location_id", location_id), ("channel", channel), ("touch", touch),
                           ("status", status)) if v}
    rows = SA.rows(L, metric, w, f)
    items, truncated = SA.present(L, rows, limit)
    label, kind, help_ = SA.METRICS[metric]
    return {"metric": metric, "label": label, "help": help_, "row_kind": kind, "snapshot": metric in SA.SNAPSHOT,
            "window": {k: w[k] for k in ("preset", "label", "start_date", "end_date")}, "filters": f,
            "count": len(rows), "families": len({r["lead_id"] for r in rows}),
            "items": items, "truncated": truncated, "data_as_of": L.built_at.isoformat() + "Z",
            "locations_all": [{"id": k, "name": v} for k, v in sorted(L.locs.items(), key=lambda kv: kv[1] or "")]}


@router.get("/analytics/journey/{lead_id}")
def command_journey(lead_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """A family's complete recorded history, oldest first."""
    from app.services.programs import sci_analytics as SA
    prog = _program(db, user, managers_only=False)
    j = SA.journey(db, prog.organization_id, lead_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    return j
