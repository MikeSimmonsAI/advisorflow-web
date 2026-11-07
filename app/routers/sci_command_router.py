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
from app.models.program_models import (CampaignFamily, LocationProfile, ProgramConversation, ProgramResponse,
                                       ProgramSourceRecord)
from app.routers.program_router import _program, _require_manager, _sees_program
from app.services import booking_time as _booking_time
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


def _home_visit_requested(conv) -> bool:
    """The request itself, as recorded in the family's memory when the setter
    handed the conversation over (home_visit.requested_at) - it survives a
    person taking the conversation or switching it back to the AI. Older
    conversations without requested_at fall back to the hand-off reason."""
    from app.services.programs.ai_setter import HOME_VISIT_REASON, memory_of
    if conv is None:
        return False
    hv = memory_of(conv).get("home_visit")
    hv = hv if isinstance(hv, dict) else {}
    if hv.get("requested_at"):
        return not hv.get("done")
    return bool(conv.state == "human" and conv.handoff_reason == HOME_VISIT_REASON and not hv.get("done"))


def _booked_text(db, booking) -> str:
    """'Thursday, October 22 at 9 AM CT' - the stored local wall time in its own
    zone, exactly as the family's confirmation said it (never converted)."""
    from zoneinfo import ZoneInfo
    from app.services import booking_time
    from app.services.programs import appointments as _A
    bt = booking.booked_time.replace(tzinfo=None)
    zone = booking_time.zone_of_user(db, booking.user_id)
    return "%s %s" % (_A.describe(bt), bt.replace(tzinfo=ZoneInfo(zone)).strftime("%Z"))


def _home_visit_waiting(conv) -> bool:
    """The family asked us to come to them and nobody has arranged it yet -
    on their CURRENT conversation, and never once they opted out."""
    if conv is None or conv.state in ("stopped", "booked", "closed") or not _home_visit_requested(conv):
        return False
    from sqlalchemy.orm import object_session
    try:
        sess = object_session(conv)
    except Exception:                                    # noqa: BLE001 - not a mapped row
        sess = None
    if sess is not None and getattr(conv, "lead_id", None):
        latest = ai_setter_conv(sess, conv.lead_id)
        if latest is not None and latest.id != conv.id:
            return False
    return True


def ai_setter_conv(db, lead_id):
    return (db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead_id)
            .order_by(ProgramConversation.created_at.desc()).first())


def _home_visit_of(conv) -> Optional[dict]:
    from app.services.programs.ai_setter import HOME_VISIT_REASON, memory_of
    if conv is None:
        return None
    hv = memory_of(conv).get("home_visit")
    if not isinstance(hv, dict) and conv.handoff_reason != HOME_VISIT_REASON:
        return None
    return dict(hv if isinstance(hv, dict) else {}, waiting=_home_visit_waiting(conv))


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
_HANDOFF_STATES = ("human", "callback_requested")


def _human_handoff(r, conv) -> bool:
    """RED FLAG (Mike, Oct 22): the AI handed this family to a person and
    nobody has handled it yet. Clears when the reply is answered or closed,
    when they book, or when the conversation goes back to the AI."""
    return bool(conv is not None and conv.state in _HANDOFF_STATES and conv.booked_time is None
                and r is not None and r.handling_status in _NEEDS_REPLY)
_APPT_STATES = ("offered", "booked", "callback_requested")
FILTERS = ("all", "handoff", "needs_reply", "text", "email", "handled", "unread", "ai", "human", "appointments",
           "home_visits", "opted_out")
# #515: closed conversations a person may still want to see. They are never
# counted as open work, and they never change the open-work counts.
CLOSED_VIEWS = ("handled", "opted_out")
CLOSED_VIEW_DAYS = 14


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


def _campaign_scope(M, campaign: Optional[str]) -> str:
    """The selected campaign scope, or 404 - another workspace's campaign is
    answered exactly like one that does not exist."""
    from app.services.programs import campaign_attribution as CA
    try:
        return M.check_scope(campaign)
    except CA.CampaignNotFound:
        raise HTTPException(status_code=404, detail="Campaign not found.")


def _campaign_filter(query, lead_col, org: str, scope: str):
    """Limit a query to families in the campaign scope, in SQL (indexed on the
    organization; never by loading every conversation into the browser)."""
    from app.services.programs import campaign_attribution as CA
    R = ProgramSourceRecord
    if scope == CA.ALL:
        return query
    if scope == CA.UNATTRIBUTED:
        keyed = (R.organization_id == org) & R.campaign_family.isnot(None) & R.lead_id.isnot(None)
        from sqlalchemy import select
        return query.filter(lead_col.notin_(select(R.lead_id).where(keyed)))
    from sqlalchemy import select
    cond = (R.import_run_id == scope[len(CA.IMPORT_PREFIX):]) if scope.startswith(CA.IMPORT_PREFIX) \
        else (R.campaign_family == scope)
    return query.filter(lead_col.in_(select(R.lead_id).where((R.organization_id == org) & cond
                                                              & R.lead_id.isnot(None))))


def _campaign_stamp(M, lead_id: str, latest=None) -> dict:
    """The campaign shown on an inbox card: the campaign of the family's newest
    attributable activity (the reply on the card), and every campaign the
    family has been in, so earlier participation is never hidden."""
    at = getattr(latest, "received_at", None) if latest is not None else None
    explicit = getattr(latest, "campaign_family", None) if latest is not None else None
    k, _run, ev = M.attribute(lead_id, at or datetime.utcnow(), explicit)
    keys = M.keys_of(lead_id)
    return {"campaign": M.stamp(k, ev),
            "campaigns": [{"key": x, "name": M.families[x].name if x in M.families else x} for x in keys]}


@router.get("")
def command_home(location_id: Optional[str] = None, q: Optional[str] = None, limit: int = Query(40, le=200),
                 view: str = Query("all", alias="filter"), campaign: Optional[str] = None,
                 db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.models.models import BookingLink
    from app.services.programs import engagement as _eng
    from app.services.programs import campaign_attribution as CA
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    locs = _locations(db, org)
    start = _central_day_start_utc()
    M = CA.Memberships(db, org)
    scope = _campaign_scope(M, campaign)

    rq = _tr.exclude_test_responses(db.query(ProgramResponse).filter(ProgramResponse.organization_id == org))
    if location_id:
        rq = rq.filter(ProgramResponse.location_id == location_id)
    # CAMPAIGN FILTER, on the server: the conversations of families who are in
    # the selected campaign (one canonical thread per family - never split).
    rq = _campaign_filter(rq, ProgramResponse.lead_id, org, scope)
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
    # #515: the open-work counts always come from the OPEN conversations. A
    # closed view (Handled / Opted out) lists its own rows but never replaces
    # the counts - on Nov 4 the chips showed the Handled set's numbers.
    since_closed = datetime.utcnow() - timedelta(days=CLOSED_VIEW_DAYS)
    view_rows = None
    if flt == "handled":
        # Handled = answered or closed in the last 14 days.
        view_rows = (rq.filter(ProgramResponse.handling_status.in_(("responded", "active", "closed")),
                               ProgramResponse.received_at >= since_closed).all())
    elif flt == "opted_out":
        view_rows = (rq.filter(ProgramResponse.response_class == "opt_out",
                               ProgramResponse.received_at >= since_closed).all())
    opted_out_families = (rq.filter(ProgramResponse.response_class == "opt_out",
                                    ProgramResponse.received_at >= since_closed)
                          .with_entities(ProgramResponse.lead_id).distinct().count())
    # The newest reply recorded in this scope, open or not - so a quiet list
    # never hides that something came in (Nov 4: 15 opt-outs after 2:11 PM).
    latest_any = rq.order_by(ProgramResponse.received_at.desc()).first()
    today_rows = rq.filter(ProgramResponse.received_at >= start,
                           ProgramResponse.response_class.notin_(("auto_reply",))).count()
    items = _inbox_items(db, org, open_rows, locs, M, q, owner_test_ids)
    view_items = _inbox_items(db, org, view_rows, locs, M, q, owner_test_ids) if view_rows is not None else None
    return _command_response(db, user, prog, org, location_id, items, view_items, flt, limit, today_rows, start,
                             scope, M, opted_out_families, latest_any, locs)


def _inbox_items(db, org, rows, locs, M, q, owner_test_ids) -> list:
    """One inbox line per family - their newest reply in `rows` - newest first."""
    newest = {}
    for r in sorted(rows, key=lambda r: r.received_at or datetime.min, reverse=True):
        newest.setdefault(r.lead_id, r)
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(list(newest))).all()} if newest else {}
    convs = {c.lead_id: c for c in db.query(ProgramConversation)
             .filter(ProgramConversation.lead_id.in_(list(newest))).order_by(ProgramConversation.created_at).all()} \
        if newest else {}
    _adv_ids = {l.assigned_to_id for l in leads.values() if l.assigned_to_id}
    advisors = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(list(_adv_ids)))} \
        if _adv_ids else {}
    needle = (q or "").strip().lower()
    from app.services.programs import responses as R
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
        answered = R.ai_answered(db, conv, r) if r.handling_status in _NEEDS_REPLY else False
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
                                           and not answered
                                           and r.sla_due_at and r.sla_due_at <= datetime.utcnow()),
                      # the AI already answered this reply: shown, but not as waiting on staff
                      "ai_answered": answered,
                      "setter_state": conv.state if conv else None, "actions": _actions(r, conv),
                      "home_visit": _home_visit_waiting(conv),
                      "human_handoff": _human_handoff(r, conv),
                      "handoff_reason": (conv.handoff_reason or "")[:200] if _human_handoff(r, conv) else None,
                      "advisor": advisors.get(getattr(lead, "assigned_to_id", None)),
                      **_campaign_stamp(M, lid, r)})
    # NEWEST FIRST, always (Oct 22, Mike): what came in today is at the top - an
    # older hot or overdue reply never pushes today's messages down the list.
    # Hot / overdue / home-visit still show as badges and in their own filters.
    items.sort(key=lambda i: -(datetime.fromisoformat(i["received_at"][:-1]).timestamp()
                               if i["received_at"] else 0))
    return items


def _command_response(db, user, prog, org, location_id, items, view_items, flt, limit, today_rows, start,
                      scope, M, opted_out_families, latest_any, locs) -> dict:
    from app.models.models import BookingLink
    from app.services.programs import engagement as _eng
    from app.services.programs import campaign_attribution as CA
    needs = sum(1 for i in items if i["class"] in ("hot", "active", "bad_data", "wrong_person")
                and i["handling_status"] in _NEEDS_REPLY and not i["is_test"] and not i["ai_answered"])
    filter_counts = {"all": len(items),
                     "handoff": sum(1 for i in items if i["human_handoff"] and not i["is_test"]),
                     "needs_reply": sum(1 for i in items if i["handling_status"] in _NEEDS_REPLY
                                        and i["class"] != "auto_reply" and not i["ai_answered"]),
                     "text": sum(1 for i in items if i["channel"] != "email"),
                     "email": sum(1 for i in items if i["channel"] == "email"),
                     "unread": sum(1 for i in items if i["handling_status"] == "new"),
                     "ai": sum(1 for i in items if i["owner"] == "ai"),
                     "human": sum(1 for i in items if i["owner"] == "human"),
                     "appointments": sum(1 for i in items if i["setter_state"] in _APPT_STATES),
                     "home_visits": sum(1 for i in items if i["home_visit"]),
                     "opted_out": opted_out_families}
    if view_items is not None:
        items = view_items
    elif flt == "handoff":
        items = [i for i in items if i["human_handoff"]]
    elif flt == "needs_reply":
        items = [i for i in items if i["handling_status"] in _NEEDS_REPLY and i["class"] != "auto_reply"
                 and not i["ai_answered"]]
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
        "counts": {"needs_attention": needs, "human_handoff": filter_counts["handoff"], "replies_today": today_rows, "visits_booked": booked,
                   "contacted_today": contacted},
        "filter": flt, "filter_counts": filter_counts,
        "campaign": scope, "campaign_name": M.name_of(scope),
        # family counts in the selector: real families only, as on Analytics
        "campaign_options": CA.catalog(M, {i for (i,) in db.query(Lead.id).filter(Lead.organization_id == org,
                                                                                Lead.is_test.isnot(True))}),
        "owner_alert": owner_alert,
        "inbox": items[:limit],
        # #515: how many lines this view really has, so a cut-off list says so
        "total": len(items), "shown": min(len(items), limit),
        "latest_activity": _latest_activity(db, latest_any, locs),
        "tasks": _open_tasks(db, org, location_id),
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


SEARCH_SHOW = ("all", "replied", "booked", "needs_review", "dnc", "not_contacted")


@router.get("/search")
def command_search(q: str = Query("", max_length=80), location_id: Optional[str] = None,
                   limit: int = Query(25, ge=1, le=50), offset: int = Query(0, ge=0, le=200000),
                   show: str = Query("all"), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    """Every family - not just the ones who replied - by name, phone, email
    or location. Read-only.

    No search term BROWSES every family (Issue #507: the screen used to stay
    empty until something was typed, and Restland looked like it had no
    families). Paged, with an accurate total and a stable order (last name,
    first name, id). One character is still too short to search and returns
    nothing, as before. Test records are never listed."""
    from sqlalchemy import func, or_
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    needle = (q or "").strip()
    if len(needle) == 1:
        return {"items": [], "total": 0, "offset": 0, "limit": limit, "browse": False}
    if show not in SEARCH_SHOW:
        raise HTTPException(status_code=400, detail="Unknown filter.")
    locs = _locations(db, org)
    qry = db.query(Lead).filter(Lead.organization_id == org, Lead.is_test.isnot(True))
    if needle:
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
        qry = qry.filter(or_(*conds))
    if location_id:
        qry = qry.filter(Lead.id.in_(db.query(ProgramSourceRecord.lead_id)
                                     .filter(ProgramSourceRecord.organization_id == org,
                                             ProgramSourceRecord.location_id == location_id)))
    from app.models.models import BookingLink, EmailMessage, Reply
    if show == "replied":
        qry = qry.filter(or_(Lead.id.in_(db.query(ProgramResponse.lead_id)
                                         .filter(ProgramResponse.organization_id == org)),
                             Lead.id.in_(db.query(Reply.lead_id))))
    elif show == "booked":
        qry = qry.filter(Lead.id.in_(db.query(BookingLink.lead_id)
                                     .filter(BookingLink.booked_time.isnot(None),
                                             BookingLink.status.in_(("booked", "confirmed", "completed")))))
    elif show == "needs_review":
        qry = qry.filter(Lead.status == "needs_tier_review")
    elif show == "dnc":
        qry = qry.filter(Lead.status == "dnc")
    elif show == "not_contacted":
        qry = qry.filter(~Lead.id.in_(db.query(Message.lead_id)), ~Lead.id.in_(db.query(EmailMessage.lead_id)))
    total = qry.count()
    leads = (qry.order_by(func.lower(func.coalesce(Lead.last_name, "")), func.lower(func.coalesce(Lead.first_name, "")),
                          Lead.id).offset(offset).limit(limit).all())
    ids = [l.id for l in leads]
    recs = {r.lead_id: r for r in db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org, ProgramSourceRecord.lead_id.in_(ids or ["-"]))}
    replied = {i for (i,) in db.query(ProgramResponse.lead_id).filter(ProgramResponse.lead_id.in_(ids or ["-"]))}
    earlier = {i for (i,) in db.query(Reply.lead_id).filter(Reply.lead_id.in_(ids or ["-"]))}
    return {"items": [{
        "lead_id": l.id,
        "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title() or "Family",
        "phone": l.phone,
        "email": l.email,
        "status": l.status,
        "location": locs.get(recs[l.id].location_id) if l.id in recs else None,
        "replied": l.id in replied,
        "earlier_replies": l.id in earlier,
    } for l in leads], "total": total, "offset": offset, "limit": limit, "browse": not needle}


@router.get("/earlier-conversations")
def command_earlier_conversations(offset: int = Query(0, ge=0, le=100000), limit: int = Query(25, ge=1, le=100),
                                  db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Original replies received before this workspace ran the program, read
    in place (Issue #507). Historical correspondence: never unread, never a
    hand-off, never replayed through inbound handling."""
    from app.services.programs import family_history as FH
    prog = _program(db, user, managers_only=False)
    return FH.earlier_conversations(db, prog.organization_id, offset=offset, limit=limit)


@router.get("/case-files")
def command_case_files(lead_id: Optional[str] = None, offset: int = Query(0, ge=0, le=100000),
                       limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_or_observer)):
    """Appointment case files recorded in this workspace, read-only (#507)."""
    from app.services.programs import family_history as FH
    prog = _program(db, user)                      # managers: these carry outcomes and notes
    return FH.case_files(db, prog.organization_id, lead_id=lead_id, offset=offset, limit=limit)


@router.get("/family/{lead_id}")
def command_family(lead_id: str, history_n: int = Query(40, ge=1, le=500, alias="history"), db: Session = Depends(get_db),
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
    # Original replies from before the program (Issue #507), read in place and
    # marked historical: they are shown, never replayed or counted as unread.
    from app.services.programs import family_history as _FH
    thread.extend(_FH.earlier_replies(db, lead))
    thread = sorted([t for t in thread if t["at"]], key=lambda t: t["at"])
    # Each message's campaign, from the evidence (campaign_attribution.py).
    from app.services.programs import campaign_attribution as CA
    M = CA.Memberships(db, prog.organization_id)
    explicit = {r.id: r.campaign_family for r in resps}
    for t in thread:
        try:
            at_ = datetime.fromisoformat(t["at"].rstrip("Z"))
        except (TypeError, ValueError):
            at_ = None
        k, _run, ev = M.attribute(lead.id, at_, explicit.get(t.get("response_id")))
        t["campaign"] = M.stamp(k, ev)
    history = []
    for m_ in M.by_lead.get(lead.id, ()):
        run = M.runs.get(m_["run"]) if m_["run"] else None
        history.append({"key": m_["key"], "name": (M.families[m_["key"]].name if m_["key"] in M.families
                                                    else m_["key"]) if m_["key"] else "No campaign on record",
                        "joined_at": m_["at"].isoformat() + "Z" if m_["at"] else None,
                        "import_file": run.filename if run is not None else None})
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
        "campaign_history": history,
        **_campaign_stamp(M, lead.id, latest),
        "assigned_to": {"id": assigned.id, "name": assigned.full_name or assigned.email} if assigned else None,
        "team": team,
        "status": latest.handling_status if latest else None,
        "badge": _badge(latest, conv) if latest else None,
        "human_handoff": _human_handoff(latest, conv),
        "handoff_reason": conv.handoff_reason if conv is not None and _human_handoff(latest, conv) else None,
        # The family message that needs the person (the handoff's trigger).
        "handoff_message": ({"text": (latest.body_excerpt or latest.summary or "")[:500],
                             "channel": latest.channel,
                             "received_at": latest.received_at.isoformat() + "Z" if latest.received_at else None}
                            if conv is not None and _human_handoff(latest, conv) else None),
        "response_id": latest.id if latest else None,
        "suggested_reply": (_er.strip_signature(db, lead, latest.suggested_reply)
                            if latest and latest.suggested_reply else None),
        "last_touch": {"at": last["at"], "channel": last["channel"]} if last else None,
        "thread": thread[-history_n:],
        "thread_total": len(thread),
        "earlier_record": _FH.earlier_record(db, lead),
        # The program's own AI setter switch: when it is OFF nobody's reply is
        # answered by the AI, whatever this conversation's owner says.
        "program_ai_mode": (getattr(prog, "ai_setter_mode", None) or "off"),
        "bookings": _FH.bookings(db, lead),
        "case_files": (_FH.case_files(db, prog.organization_id, lead_id=lead.id)["items"]
                       if _sees_program(db, user, prog.organization_id) else None),
        "setter": {"state": conv.state, "offered": [A.describe(s) for s in A.offered(conv)],
                   "callback_window": conv.callback_window, "handoff_reason": conv.handoff_reason}
        if conv else None,
        "booking": {"when": _booking_time.wire(db, booking), "label": booking.appt_label,
                    # the appointment in its own time zone, as the family was told
                    "when_text": _booked_text(db, booking),
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
            "note": getattr(conv, "owner_note", None) if conv is not None else None,
            "ownership": ownership_state(conv, opted_out)}


def ownership_state(conv, opted_out: bool = False) -> str:
    """The conversation's ownership, named (owner, Nov 4), derived from the
    canonical conversation state - no second source of truth:
    AI_ACTIVE, HUMAN_REQUIRED, AI_RESUMED, HUMAN_RETAINED, CLOSED.
    (HUMAN_RESPONDING / HUMAN_REPLY_PENDING exist only inside one reply
    request: the send claim and idempotency key hold them, and ownership
    changes only after the provider accepts the message.)"""
    if opted_out or (conv is not None and conv.state in ("stopped", "closed")):
        return "CLOSED"
    if conv is None:
        return "AI_ACTIVE"
    note = getattr(conv, "owner_note", None) or ""
    if conv.state in _HANDOFF_STATES:
        return "HUMAN_RETAINED" if (conv.owner_changed_by and note in (KEPT_NOTE, "Owner replied by hand",
                                                                       "Human handling on")) else "HUMAN_REQUIRED"
    # The resume is stamped one second after the provider accepted the reply,
    # so an AI turn later than (stamp - 1 s) came after the human's answer.
    if note == RESUMED_NOTE and not (conv.last_ai_at and conv.owner_changed_at
                                     and conv.last_ai_at >= conv.owner_changed_at - timedelta(seconds=1)):
        return "AI_RESUMED"
    return "AI_ACTIVE"


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
    # Who owns the conversation after this send. Handoff is TEMPORARY by
    # default (owner, Nov 4): "ai" - the AI takes the NEXT reply. "human" =
    # Keep Human Control, chosen on purpose.
    then: str = "ai"


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
    if body.channel == "sms" and ("{GUIDE}" in text or "{BOOKING}" in text or "http" in text.lower()):
        from app.services import sms_programs
        if sms_programs.is_sci_org(db, lead.organization_id) and not sms_programs.sms_links_enabled():
            raise HTTPException(status_code=422, detail=(
                "Links in texts are being blocked by carriers on this line (Twilio 30007). "
                "Send the guide link by email, or offer a printed copy by mail."))
    text = email_reply.guide_token(db, lead, email_reply.strip_signature(db, lead, text))
    if "{BOOKING}" in text:
        # {BOOKING} = this family's own pick-a-time link (the cadence's link).
        from app.services.programs import identity as _identity
        _url = _identity.program_booking_url(db, lead)
        if not _url:
            raise HTTPException(status_code=422, detail="This family has no booking link (no booking calendar is set).")
        text = text.replace("{BOOKING}", _url)
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
    # Who had it before this send, and when the send began: a family message
    # that arrives while the reply is going out must not be left with nobody.
    conv_before = db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).first()
    prior_state = conv_before.state if conv_before is not None else None
    prior_reason = conv_before.handoff_reason if conv_before is not None else None
    send_started = datetime.utcnow()
    if body.channel == "email":
        if resp is None or resp.channel != "email":
            raise HTTPException(status_code=422, detail="Choose the email you are answering.")
        res = email_reply.send_reply(db, prog, lead, resp, text, send_source=ss.MANUAL,
                                     idempotency_key="u:%s:%s" % (user.id, body.idempotency_key), actor_id=user.id)
        out.update(res)
        if not res.get("ok"):
            raise HTTPException(status_code=502, detail="Not sent: %s" % (res.get("error") or "the email was refused"))
        sent_at = datetime.utcnow()
        from app.services.programs import responses as R
        R.mark(db, resp, "responded", user)          # and the family's earlier open replies
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
    out["response_status"] = resp.handling_status if resp is not None else None
    # Ownership AFTER the provider accepted the send (a failure raised above,
    # leaving the handoff open and nothing claimed), stamped after it, so the
    # AI never reads this manual message as "a person took over".
    after = _ownership_after_reply(db, lead, resp, body.then, send_started)
    try:
        conv = ai_setter.set_owner(db, lead, after["owner"], actor=user.id, note=after["note"],
                                   at=sent_at + timedelta(seconds=1))
        # A family message that landed between that check and this commit:
        # it was processed while a person owned the conversation, so the AI
        # never answered it - give it back to a person rather than let it sit.
        if after["owner"] == ai_setter.AI_OWNER and _newer_family_message(db, lead, resp, send_started):
            after = {"owner": ai_setter.HUMAN_OWNER, "note": RACE_NOTE, "race": True}
            conv = ai_setter.set_owner(db, lead, after["owner"], actor=user.id, note=after["note"],
                                       at=sent_at + timedelta(seconds=2))
        out["handling"] = _handling(db, conv)
    except ValueError as exc:
        out["handling_error"] = str(exc)
        conv = conv_before
    was_handoff = prior_state in _HANDOFF_STATES
    out["handoff"] = {"was_handoff": was_handoff, "ai_resumed": after["owner"] == ai_setter.AI_OWNER
                      and "handling_error" not in out,
                      "kept_human": after["owner"] == ai_setter.HUMAN_OWNER, "race": bool(after.get("race")),
                      "note": after["note"]}
    # Human-assisted learning: the answer to a handed-off question becomes a
    # CANDIDATE (redacted; never used by the AI until a manager approves it).
    if was_handoff and resp is not None:
        try:
            from app.services.programs import handoff_learning as HL
            c = HL.capture_candidate(db, prog, lead, conv, resp, text, channel=body.channel,
                                     outcome="ai_resumed" if out["handoff"]["ai_resumed"] else "human_retained",
                                     user_id=user.id, reason=prior_reason)
            db.commit()
            out["learning_candidate_id"] = getattr(c, "id", None)
        except Exception:                                # noqa: BLE001
            db.rollback()
            import logging
            logging.getLogger(__name__).exception("learning candidate not captured for lead %s", lead.id)
    return out


RESUMED_NOTE = "Human replied - AI resumed"
KEPT_NOTE = "Human replied - kept by a person"
RACE_NOTE = "The family wrote again while the reply was sending - still with a person"


def _newer_family_message(db, lead, resp, since) -> bool:
    """An open family message newer than the one answered (or than the start
    of the send, when no message was chosen)."""
    q = db.query(ProgramResponse.id).filter(ProgramResponse.organization_id == lead.organization_id,
                                            ProgramResponse.lead_id == lead.id,
                                            ProgramResponse.handling_status.in_(_NEEDS_REPLY))
    if resp is not None:
        q = q.filter(ProgramResponse.id != resp.id, ProgramResponse.received_at > resp.received_at)
    else:
        q = q.filter(ProgramResponse.received_at >= since)
    return q.first() is not None


def _ownership_after_reply(db, lead, resp, then: str, since) -> dict:
    from app.services.programs import ai_setter
    if then == "human":
        return {"owner": ai_setter.HUMAN_OWNER, "note": KEPT_NOTE}
    if _newer_family_message(db, lead, resp, since):
        return {"owner": ai_setter.HUMAN_OWNER, "note": RACE_NOTE, "race": True}
    return {"owner": ai_setter.AI_OWNER, "note": RESUMED_NOTE}


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
    from app.services.programs import booking_policy as _bp
    _pol = _bp.for_program(prog)
    return {"slots": [{"value": s.isoformat(), "label": A.describe(s),
                       "day": s.strftime("%a %b %-d"), "time": s.strftime("%-I:%M %p")} for s in slots],
            "reason": why,
            # Issue #507: where the visit can be (office / the family's home).
            "meeting_options": _bp.public_options(_pol) if _pol else [],
            "minutes": _pol["duration"] if _pol else None}


@router.get("/day-slots")
def command_day_slots(lead_id: str, date: str, kind: str = "visit", db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_or_observer)):
    """Every open time on the day a person picks ("Choose custom time")."""
    from datetime import date as _date
    from app.services.programs import appointments as A
    prog = _program(db, user, managers_only=False)
    try:
        day = _date.fromisoformat(date[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail="Pick a date.")
    slots, why = A.all_slots_on_day(db, prog, day, kind=kind if kind in (A.PHONE, A.VISIT) else A.VISIT)
    return {"date": day.isoformat(),
            "slots": [{"value": s.isoformat(), "label": A.describe(s),
                       "day": s.strftime("%a %b %-d"), "time": s.strftime("%-I:%M %p")} for s in slots],
            "reason": why}


class DncIn(BaseModel):
    lead_id: str
    reason: Optional[str] = None


@router.post("/dnc")
def command_do_not_contact(body: DncIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """A person puts a family on Do Not Contact: no texts, emails, AI replies
    or calls from any path - exactly what a texted STOP does (Oct 25)."""
    from app.services.programs import dnc
    prog = _program(db, user, managers_only=False)
    lead = db.query(Lead).filter(Lead.id == body.lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    return dnc.mark_do_not_contact(db, lead, user, body.reason)


class ScheduleIn(BaseModel):
    lead_id: str
    slot: str                       # local Central ISO, from /slots
    kind: str = "visit"             # visit | phone
    meeting_key: Optional[str] = None     # office | home (programs with their own rules)
    visit_address: Optional[str] = None   # the family's address for a home visit


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
    res = A.book(db, prog, lead, slot, kind, meeting_key=body.meeting_key, visit_address=body.visit_address)
    if res.get("status") == "needs_place":
        raise HTTPException(status_code=422, detail=res.get("reason"))
    if res.get("ok") and not res.get("simulated"):
        # Like the setter: the conversation is booked (created if the family
        # never had one) and the family's open replies are answered.
        from app.services.programs import ai_setter
        from app.services.programs import responses as R
        conv = ai_setter.conversation_for(db, lead)
        conv.state, conv.booked_time = "booked", slot
        if res.get("booking_id"):
            conv.booking_link_id = res.get("booking_id")
        newest = (db.query(ProgramResponse).filter(ProgramResponse.organization_id == prog.organization_id,
                                                   ProgramResponse.lead_id == lead.id,
                                                   ProgramResponse.handling_status != "closed")
                  .order_by(ProgramResponse.received_at.desc()).first())
        if newest is not None:
            R.mark(db, newest, "responded", user)
        A.notify_team_booked(db, prog, lead, slot, kind)
        from app.routers.audit_log_router import log_action
        log_action(db, prog.organization_id, user.id, action="sci.schedule", target_type="lead",
                   target_id=lead.id, details={"booking_id": res.get("booking_id"), "time": slot.isoformat(),
                                               "kind": kind}, commit=False)
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
    before = lead.assigned_to_id
    lead.assigned_to_id = body.user_id or None
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="sci.assign", target_type="lead", target_id=lead.id,
               before={"assigned_to_id": before}, after={"assigned_to_id": lead.assigned_to_id}, commit=False)
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


def _family_location(db, org_id, lead_id) -> Optional[str]:
    """Where a family sits for the location filter: the location of their
    newest reply (what the inbox filters on), else their source record's."""
    r = (db.query(ProgramResponse.location_id).filter(ProgramResponse.organization_id == org_id,
                                                      ProgramResponse.lead_id == lead_id)
         .order_by(ProgramResponse.received_at.desc()).first())
    if r is not None and r[0]:
        return r[0]
    rec = (db.query(ProgramSourceRecord.location_id).filter(ProgramSourceRecord.organization_id == org_id,
                                                            ProgramSourceRecord.lead_id == lead_id).first())
    return rec[0] if rec is not None else None


def _open_tasks(db, org_id, location_id=None):
    out = []
    test_ids = {i for (i,) in db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.is_test.is_(True))}

    def _elsewhere(lead_id):
        return bool(location_id) and _family_location(db, org_id, lead_id) != location_id
    rows = (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org_id,
                                                 ProgramConversation.memory.isnot(None),
                                                 ProgramConversation.memory.like('%mail_request%')).all())
    for c in rows:
        t = _mail_task_of(c)
        if not t or t["done"] or c.lead_id in test_ids or _elsewhere(c.lead_id):
            continue
        lead = db.query(Lead).filter(Lead.id == c.lead_id).first()
        if lead is None:
            continue
        out.append({"kind": "mail_guide", "lead_id": lead.id, "what": t["what"], "note": t.get("note"),
                    "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title()})
    # Home visits a family asked for that nobody has arranged yet (#234 follow-up).
    # Found by the request in memory (it survives a change of owner) or, for
    # older conversations, by the hand-off reason; _home_visit_waiting decides.
    from sqlalchemy import or_
    from app.services.programs.ai_setter import HOME_VISIT_REASON
    for c in (db.query(ProgramConversation).filter(ProgramConversation.organization_id == org_id,
                                                   or_(ProgramConversation.memory.like('%home_visit%'),
                                                       ProgramConversation.handoff_reason == HOME_VISIT_REASON))
              .all()):
        if c.lead_id in test_ids or not _home_visit_waiting(c) or _elsewhere(c.lead_id):
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
        if str(day.isoweekday()) in daily_batch.days_of(prog):
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
                      end: Optional[str] = None, location_id: Optional[str] = None, campaign: Optional[str] = None,
                      db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Performance Home: recorded totals for the period (and all time), touch
    analytics, funnel, failure reasons, location performance, trend and the
    clearly separate projections. Real families only."""
    from app.services.programs import daily_batch, sci_analytics as SA
    prog = _program(db, user, managers_only=False)
    L = SA.ledger(db, prog.organization_id)
    from app.services.programs import campaign_attribution as CA
    scope = _campaign_scope(L.memberships, campaign)
    out = SA.summary(L, _analytics_window(range_, start, end), location_id or None, daily_batch.status(prog),
                     campaign=scope)
    out["campaign_name"] = L.memberships.name_of(scope)
    out["campaign_options"] = CA.catalog(L.memberships, set(L.families))
    out["daily_batch"] = dict(daily_batch.status(prog), ready=daily_batch.ready_detail(db, prog))
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
                              campaign: Optional[str] = None,
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
    scope = _campaign_scope(L.memberships, campaign)
    f = {k: v for k, v in (("location_id", location_id), ("channel", channel), ("touch", touch),
                           ("status", status), ("campaign", scope if scope != "all" else None)) if v}
    rows = SA.rows(L, metric, w, f)
    items, truncated = SA.present(L, rows, limit)
    label, kind, help_ = SA.METRICS[metric]
    return {"metric": metric, "label": label, "help": help_, "row_kind": kind, "snapshot": metric in SA.SNAPSHOT,
            "window": {k: w[k] for k in ("preset", "label", "start_date", "end_date")}, "filters": f,
            "count": len(rows), "families": len({r["lead_id"] for r in rows}),
            "items": items, "truncated": truncated, "data_as_of": L.built_at.isoformat() + "Z",
            "campaign": scope, "campaign_name": L.memberships.name_of(scope),
            "locations_all": [{"id": k, "name": v} for k, v in sorted(L.locs.items(), key=lambda kv: kv[1] or "")]}


@router.get("/analytics/campaigns")
def command_analytics_campaigns(range_: str = Query("today", alias="range"), start: Optional[str] = None,
                                end: Optional[str] = None, location_id: Optional[str] = None,
                                db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """COMPARE CAMPAIGNS + RECONCILIATION: every campaign over the same window
    and the same metric definitions, the unattributed / ambiguous remainder,
    and the check that the parts add up to All campaigns. Real families only."""
    from app.services.programs import sci_analytics as SA
    prog = _program(db, user, managers_only=False)
    L = SA.ledger(db, prog.organization_id)
    w = _analytics_window(range_, start, end)
    out = SA.campaign_rows(L, w, location_id or None)
    out["window"] = {k: w[k] for k in ("preset", "label", "start_date", "end_date")}
    out["data_as_of"] = L.built_at.isoformat() + "Z"
    return out


@router.get("/analytics/journey/{lead_id}")
def command_journey(lead_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """A family's complete recorded history, oldest first."""
    from app.services.programs import sci_analytics as SA
    prog = _program(db, user, managers_only=False)
    j = SA.journey(db, prog.organization_id, lead_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Family not found.")
    return j


@router.get("/pipeline-health")
def command_pipeline_health(hours: int = Query(24, ge=1, le=336), db: Session = Depends(get_db),
                            user: User = Depends(require_tenant_or_observer)):
    """PIPELINE HEALTH (Phase Nine): stage-by-stage counts for the window and
    every place a family's reply is stuck - what failed, where, how many,
    since when, record ids to open, and the action needed. Read-only; no
    names, numbers, emails or message words. Managers (and view-only
    spectators) of THIS workspace only, like /program/self-check."""
    from app.services.programs import pipeline_health
    prog = _program(db, user)
    return pipeline_health.report(db, prog, hours=hours)


@router.get("/campaign-progress")
def command_campaign_progress(campaign: Optional[str] = Query(None, max_length=80), db: Session = Depends(get_db),
                              user: User = Depends(require_tenant_or_observer)):
    """CAMPAIGN PROGRESS (Nov 7): one campaign's whole roster in mutually
    exclusive groups, what went out each day, what is due in the next seven
    days, the senders' health, and the sending days and hours. Read-only;
    counts only. Defaults to the largest active campaign."""
    from app.services.programs import campaign_progress as CP
    prog = _program(db, user)
    fams = CP.families(db, prog)
    key = campaign or next((f["key"] for f in fams if f["active"]), None)
    if key is None:
        return {"campaigns": fams, "report": None}
    try:
        rep = CP.report(db, prog, key)
    except LookupError:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return {"campaigns": fams, "report": rep}


def _latest_activity(db, r, locs) -> Optional[dict]:
    """The newest reply recorded in this scope, open or closed, and where to
    find it - "Opted out", "Handled" or the open inbox."""
    if r is None:
        return None
    lead = db.query(Lead).filter(Lead.id == r.lead_id).first()
    name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() if lead else "Family"
    where = ("opted_out" if r.response_class == "opt_out"
             else "handled" if r.handling_status in ("responded", "active", "closed") else "all")
    return {"lead_id": r.lead_id, "name": name, "location": locs.get(r.location_id),
            "channel_label": "EMAIL" if r.channel == "email" else ("VOICEMAIL" if r.channel == "voicemail" else "TEXT"),
            "received_at": r.received_at.isoformat() + "Z" if r.received_at else None,
            "open": r.handling_status != "closed", "class": r.response_class, "where": where}


# ── Human-assisted AI learning (review queue) ───────────────────────────────

@router.get("/learning")
def learning_list(status: str = Query("candidate"), db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    """Answers people gave to handed-off questions, for review. Candidates are
    redacted and never reach the AI; only ACTIVE answers do."""
    from app.models.handoff_learning_models import STATUSES, ProgramHandoffLearning
    from app.services.programs import handoff_learning as HL
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    q = db.query(ProgramHandoffLearning).filter(ProgramHandoffLearning.organization_id == org)
    if status in STATUSES:
        q = q.filter(ProgramHandoffLearning.status == status)
    rows = q.order_by(ProgramHandoffLearning.created_at.desc()).limit(200).all()
    locs = _locations(db, org)
    return {"items": [HL.view(r, locs.get(r.location_id)) for r in rows], "counts": HL.counts(db, org)}


class LearningApproveIn(BaseModel):
    question: str
    answer: str
    scope: str = "location"
    activate: bool = True


@router.post("/learning/{learning_id}/approve")
def learning_approve(learning_id: str, body: LearningApproveIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    from app.services.programs import handoff_learning as HL
    from app.routers.audit_log_router import log_action
    prog = _program(db, user)                            # managers only
    try:
        row = HL.approve(db, prog.organization_id, learning_id, user.id, question=body.question,
                         answer=body.answer, scope=body.scope, activate=body.activate)
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found.")
    except HL.Refused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    log_action(db, prog.organization_id, user.id, action="sci.learning.approve", target_type="handoff_learning",
               target_id=row.id, details={"status": row.status, "scope": row.scope}, commit=False)
    db.commit()
    return HL.view(row)


class LearningStatusIn(BaseModel):
    status: str                          # active | retired | rejected


@router.post("/learning/{learning_id}/status")
def learning_status(learning_id: str, body: LearningStatusIn, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    from app.services.programs import handoff_learning as HL
    from app.routers.audit_log_router import log_action
    prog = _program(db, user)                            # managers only
    try:
        row = HL.set_status(db, prog.organization_id, learning_id, user.id, body.status)
    except LookupError:
        raise HTTPException(status_code=404, detail="Not found.")
    except HL.Refused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    log_action(db, prog.organization_id, user.id, action="sci.learning." + body.status,
               target_type="handoff_learning", target_id=row.id, commit=False)
    db.commit()
    return HL.view(row)


# ── DATA REVIEW + CAMPAIGN AUDIENCE (Issue #507) ─────────────────────────────
# Read-only views over the recorded clean-up (lead_dispositions) and the
# campaign audience snapshot (campaign_audience_members). Manager seats only;
# scoped to the selected workspace like every program screen.

REVIEW_BUCKETS = {
    # bucket -> (kind, value)
    "ready": ("segment", "ready"), "batch_1": ("batch", 1),
    "prior_conversation": ("segment", "prior_conversation"), "past_appointment": ("segment", "past_appointment"),
    "earlier_sequence": ("segment", "earlier_sequence"), "previously_contacted": ("segment", "previously_contacted"),
    "shared_number": ("segment", "shared_number"), "audience_suppressed": ("segment", "suppressed"),
    "needs_review": ("queue", "needs_classification"), "contact_data": ("queue", "contact_data"),
    "duplicate_review": ("queue", "duplicate"), "suppressed": ("disposition", "suppressed"),
    "merged_duplicate": ("disposition", "merged_duplicate"), "email_only": ("disposition", "email_only"),
    "test": ("disposition", "test"), "retain": ("disposition", "retain"),
    # Issue #507 rotation
    "in_round": ("segment", "in_round"), "resting": ("segment", "resting"), "returning": ("segment", "returning"),
}


def _last_contact(db, ids):
    from sqlalchemy import func
    from app.models.models import EmailMessage, Reply
    out = {}
    for model, col in ((Message, Message.sent_at), (EmailMessage, EmailMessage.sent_at)):
        for lid, at in (db.query(model.lead_id, func.max(col)).filter(model.lead_id.in_(ids or ["-"]))
                        .group_by(model.lead_id).all()):
            if at and (lid not in out or at > out[lid]):
                out[lid] = at
    replies = {lid for (lid,) in db.query(Reply.lead_id).filter(Reply.lead_id.in_(ids or ["-"])).distinct()}
    return out, replies


@router.get("/data-review/summary")
def data_review_summary(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    import json as _json
    from sqlalchemy import func
    from app.models.reset_models import CampaignAudienceMember, LeadDisposition
    from app.services.programs import workspace_reset as WR
    prog = _program(db, user)
    org = prog.organization_id
    run = WR.last_run(db, org)
    disp = dict(db.query(LeadDisposition.disposition, func.count(LeadDisposition.id))
                .filter(LeadDisposition.organization_id == org).group_by(LeadDisposition.disposition).all())
    queues = dict(db.query(LeadDisposition.review_queue, func.count(LeadDisposition.id))
                  .filter(LeadDisposition.organization_id == org, LeadDisposition.review_queue.isnot(None))
                  .group_by(LeadDisposition.review_queue).all())
    segs = dict(db.query(CampaignAudienceMember.segment, func.count(CampaignAudienceMember.id))
                .filter(CampaignAudienceMember.organization_id == org,
                        CampaignAudienceMember.campaign_key == WR.CAMPAIGN_KEY)
                .group_by(CampaignAudienceMember.segment).all())
    staged = (db.query(CampaignAudienceMember.id).filter_by(organization_id=org, campaign_key=WR.CAMPAIGN_KEY,
                                                            is_staged=True).count())
    fam = db.query(CampaignFamily).filter_by(organization_id=org, key=WR.CAMPAIGN_KEY).first()
    from app.services.programs import campaigns as _camp
    from app.services.programs import booking_policy as _bp
    from app.services.programs import restland_ffr as _F
    from app.services.programs import rotation as _rot
    from app.services.programs import workspace_configure as _wc
    _pol = _bp.for_program(prog)
    _bu = db.query(User).filter(User.id == prog.booking_user_id).first() if getattr(prog, "booking_user_id", None) \
        else None
    _cfg = _wc.last_run(db, org)
    _cfg_sum = _json.loads(_cfg.summary_json or "{}") if _cfg is not None else {}
    _ret = db.query(CampaignFamily).filter_by(organization_id=org, key=_F.RETURN_KEY).first()
    return {
        "configure_run": None if _cfg is None else {"id": _cfg.id, "key": _cfg.run_key, "status": _cfg.status,
                                                    "finished_at": _cfg.finished_at.isoformat() + "Z"
                                                    if _cfg.finished_at else None,
                                                    "changes": _cfg_sum.get("changes"), "notes": _cfg_sum.get("notes"),
                                                    "batch_1": _cfg_sum.get("batch_1")},
        "booking": {
            "user": None if _bu is None else {"name": _bu.full_name, "email": _bu.email},
            "rules": None if _pol is None else {
                "days": "Every day" if _pol["days"] == "0123456" else _pol["days"],
                "starts": ["%d:%02d %s" % ((h - 1) % 12 + 1, m, "AM" if h < 12 else "PM") for h, m in _pol["starts"]],
                "minutes": _pol["duration"], "gap_minutes": _pol["gap_after"], "timezone": _pol.get("timezone"),
                "places": [o["label"] for o in _bp.public_options(_pol)]},
            "calendar": _cfg_sum.get("calendar"),
        },
        "schedule": _F.schedule_summary(),
        "rotation": _rot.groups(db, org),
        "return_campaign": None if _ret is None else {"name": _ret.name, "is_active": bool(_ret.is_active),
                                                     "activation_blocker": _camp.activation_blocker(_ret, db)},
        "run": None if run is None else {"id": run.id, "key": run.run_key, "status": run.status,
                                         "finished_at": run.finished_at.isoformat() + "Z" if run.finished_at else None,
                                         "summary": _json.loads(run.summary_json or "{}")},
        "families": db.query(Lead.id).filter(Lead.organization_id == org).count(),
        "dispositions": disp, "review_queues": queues, "audience_segments": segs, "batch_1": staged,
        "campaign": None if fam is None else {
            "key": fam.key, "name": fam.name, "is_active": bool(fam.is_active),
            "sender": WR.SENDER_E164, "timezone": "America/Chicago",
            "steps": [{"touch": n, "day": d + 1, "hour": h, "text": _camp.sms_for(fam, n)}
                      for n, d, h in _camp.sms_steps(db, org)],
            "email_steps": [{"touch": n, "day": d + 1, **(_camp.email_for(fam, n) or {})}
                            for n, d in _camp.email_steps(fam.key)],
            "activation_blocker": _camp.activation_blocker(fam, db),
            "booking_user_set": bool(getattr(prog, "booking_user_id", None)),
        },
    }


@router.get("/data-review/list")
def data_review_list(bucket: str = Query(...), offset: int = Query(0, ge=0, le=200000),
                     limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    import json as _json
    from app.models.reset_models import CampaignAudienceMember, LeadDisposition
    from app.services.programs import workspace_reset as WR
    prog = _program(db, user)
    org = prog.organization_id
    if bucket not in REVIEW_BUCKETS:
        raise HTTPException(status_code=400, detail="Unknown list.")
    kind, value = REVIEW_BUCKETS[bucket]
    if kind in ("segment", "batch"):
        q = db.query(CampaignAudienceMember).filter_by(organization_id=org, campaign_key=WR.CAMPAIGN_KEY)
        q = q.filter(CampaignAudienceMember.segment == value) if kind == "segment" else q.filter(
            CampaignAudienceMember.batch == value)
    else:
        q = db.query(LeadDisposition).filter_by(organization_id=org)
        q = q.filter(LeadDisposition.disposition == value) if kind == "disposition" else q.filter(
            LeadDisposition.review_queue == value)
    total = q.count()
    rows = q.order_by(getattr(q.column_descriptions[0]["entity"], "lead_id")).offset(offset).limit(limit).all()
    ids = [r.lead_id for r in rows]
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(ids or ["-"]), Lead.organization_id == org)}
    disp = {d.lead_id: d for d in db.query(LeadDisposition).filter(LeadDisposition.organization_id == org,
                                                                      LeadDisposition.lead_id.in_(ids or ["-"]))}
    last, replied = _last_contact(db, ids)
    # Issue #507: each family's round, booking and campaign assignment.
    from app.models.models import BookingLink as _BL
    from app.models.reset_models import CampaignCycleMember as _CCM
    from app.services.programs import rotation as _rot
    rounds = {}
    for c in (db.query(_CCM).filter(_CCM.organization_id == org, _CCM.lead_id.in_(ids or ["-"]))
              .order_by(_CCM.created_at).all()):
        rounds[c.lead_id] = c
    booked = {}
    for b in db.query(_BL).filter(_BL.lead_id.in_(ids or ["-"]), _BL.booked_time.isnot(None)).all():
        if b.lead_id not in booked or (b.booked_time and b.booked_time > booked[b.lead_id].booked_time):
            booked[b.lead_id] = b
    camp = {r.lead_id: r.campaign_family for r in db.query(ProgramSourceRecord).filter(
        ProgramSourceRecord.organization_id == org, ProgramSourceRecord.lead_id.in_(ids or ["-"])).all()}
    items = []
    for r in rows:
        l, d = leads.get(r.lead_id), disp.get(r.lead_id)
        if l is None:
            continue
        reasons = _json.loads(getattr(r, "reasons_json", None) or getattr(r, "notes_json", None) or "[]")
        items.append({"lead_id": l.id, "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title()
                      or "Family", "status": l.status, "tier": l.tier, "source_file": l.source_file,
                      "phone": d.phone_e164 if d else None, "phone_issue": d.phone_issue if d else None,
                      "email_issue": d.email_issue if d else None, "shares_phone_with": d.shares_phone_with if d else 0,
                      "disposition": d.disposition if d else None, "reasons": reasons,
                      "segment": getattr(r, "segment", None), "batch": getattr(r, "batch", None),
                      "last_contact_at": last[l.id].isoformat() + "Z" if l.id in last else None,
                      "replied_before": l.id in replied,
                      "round": _rot.member_view(rounds.get(l.id)),
                      "next_touch": ("Text 1 on the start day - not scheduled while the campaign is off"
                                     if (rounds.get(l.id) is not None and rounds[l.id].status == "staged") else None),
                      "booking": None if l.id not in booked else {"status": booked[l.id].status,
                                                                   "when": _booking_time.wire(db, booked[l.id])},
                      "campaign": camp.get(l.id)})
    return {"items": items, "total": total, "offset": offset, "limit": limit, "bucket": bucket}


# ── APPOINTMENT REASSIGNMENT (Issue #507) ────────────────────────────────────
# Staff only. Moves a booked appointment to another member of THIS workspace's
# team when that person is free (their bookings + gap, their calendar). Keeps
# the time, place, family and history; audited; the family is not messaged.

class ApptAssignIn(BaseModel):
    user_id: str


@router.get("/appointments/{booking_id}/assignees")
def appointment_assignees(booking_id: str, db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_user)):
    from app.models.models import BookingLink
    prog = _program(db, user, managers_only=False)
    b = (db.query(BookingLink).join(Lead, Lead.id == BookingLink.lead_id)
         .filter(BookingLink.id == booking_id, Lead.organization_id == prog.organization_id).first())
    if b is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return {"booking_id": b.id, "current_user_id": b.user_id,
            "team": [{"id": u.id, "name": u.full_name or u.email} for u in _team(db, prog.organization_id)]}


@router.post("/appointments/{booking_id}/assign")
def appointment_assign(booking_id: str, body: ApptAssignIn, db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user)):
    from app.models.models import BookingLink
    from app.services.programs import booking_policy as BP
    prog = _program(db, user, managers_only=False)
    b = (db.query(BookingLink).join(Lead, Lead.id == BookingLink.lead_id)
         .filter(BookingLink.id == booking_id, Lead.organization_id == prog.organization_id).first())
    if b is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    who = next((u for u in _team(db, prog.organization_id) if u.id == body.user_id), None)
    if who is None:
        raise HTTPException(status_code=422, detail="That person is not on this team.")
    try:
        return BP.reassign(db, prog, b, who, user)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionError:
        db.rollback()
        raise HTTPException(status_code=404, detail="Appointment not found.")


@router.post("/data-review/rotation/refresh")
def data_review_rotation_refresh(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    """Bring every open round up to date (replies, bookings, opt-outs, finished
    sequences -> rest dates). Reads history; sends nothing; enrolls nobody."""
    from app.services.programs import rotation as _rot
    prog = _program(db, user)
    _require_manager(db, user)
    counts = _rot.refresh(db, prog.organization_id)
    return {"counts": counts, "groups": _rot.groups(db, prog.organization_id)}
