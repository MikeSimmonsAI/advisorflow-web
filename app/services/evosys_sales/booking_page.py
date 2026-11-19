"""EvoSys Sales: each lead's own booking page (owner request 2026-11-18).

A cleaning business taps the link in our text and lands on a page on our own
domain - not the EvoSys website's demo form - showing Mike's open times for
the intro call. One tap books it: the appointment goes through the existing
scheduler (availability, calendar sync, the existing email confirmations and
reminders), the lead's automatic texts stop, the business gets a confirmation
text from the team number, and the owner gets the same text + email alert as a
reply. No form: the signed link already says which lead it is.

The link names one lead and cannot be forged or guessed (HMAC token). It shows
only that lead's business name and open times - never who else is booked or why
a time is busy.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.sales_models import Opportunity, OpportunityEvent

log = logging.getLogger(__name__)
PATH = "/evosys-sales-public/book"
MEETING_KEY = "discovery"          # the existing 30-minute intro call type
GAP_MINUTES = 15                   # owner, 2026-11-18: 15 minutes free after each call


def base_url() -> str:
    base = (os.environ.get("PROGRAM_ASSET_BASE_URL") or "").strip().rstrip("/")
    if not base:
        from app.services.twilio_callbacks import public_api_base
        base = public_api_base()
    return base


def page_base() -> str:
    return base_url() + PATH


def is_page_base(url: Optional[str]) -> bool:
    return (url or "").strip().rstrip("/").endswith(PATH)


def url_for(opp_id: str) -> str:
    from app.services.programs import unsubscribe as _u
    return "%s/%s" % (page_base(), _u.make_token("evb:" + opp_id))


def short_url_for(opp) -> str:
    """A short link for texts: .../b/<10-character code>. The code is the
    lead's existing random reply code (unique, unguessable); it redirects to
    the full signed booking page. Falls back to the full link."""
    from sqlalchemy.orm import object_session
    from app.models.evosys_sales_models import EvoSysOutreachState
    from app.services.evosys_sales import replies as R
    db = object_session(opp)
    st = db.get(EvoSysOutreachState, opp.id) if db is not None else None
    if st is None:
        return url_for(opp.id)
    return "%s/b/%s" % (base_url() + PATH.rsplit("/", 1)[0], R.ensure_code(db, st))


def by_code(db: Session, code: str):
    from app.models.evosys_sales_models import EvoSysOutreachState
    st = (db.query(EvoSysOutreachState).filter(EvoSysOutreachState.reply_code == code).first()
          if code and len(code) <= 16 else None)
    return db.get(Opportunity, st.opportunity_id) if st else None


def read(token: str) -> Optional[str]:
    from app.services.programs import unsubscribe as _u
    raw = _u.read_token(token)
    return raw[4:] if raw and raw.startswith("evb:") else None


def _owner(db: Session, opp: Opportunity):
    from app.models.models import User
    return db.get(User, opp.owner_user_id) if opp.owner_user_id else None


def slots(db: Session, opp: Opportunity, start: Optional[date] = None, days: int = 7,
          exclude_appt_id: Optional[str] = None) -> dict:
    from fastapi import HTTPException
    from app.routers.sales_scheduling_router import FindTimeIn, find_team_time
    from app.routers.evosys_sales_work_router import _meeting_type
    owner = _owner(db, opp)
    if owner is None:
        return {"slots": [], "problem": "not_set_up"}
    today = datetime.utcnow().date()
    start = max(start or today, today)
    mt = _meeting_type(db, opp.brand_sales_org_id, MEETING_KEY)
    try:
        r = find_team_time(FindTimeIn(meeting_type_id=mt.id if mt else None,
                                      duration_minutes=None if mt else 30,
                                      required_user_ids=[owner.id], date_from=start,
                                      date_to=start + timedelta(days=days - 1), opportunity_id=opp.id,
                                      brand_sales_org_id=opp.brand_sales_org_id,
                                      exclude_appointment_id=exclude_appt_id), user=owner, db=db)
    except HTTPException as e:
        log.info("[evosys-sales booking] no slots for %s: %s", opp.id, e.detail)
        return {"slots": [], "problem": "unavailable"}
    now = datetime.utcnow() + timedelta(hours=2)          # never offer a time less than 2 hours out
    dur = timedelta(minutes=((mt.duration_minutes if mt else 30) or 30))
    gap = timedelta(minutes=GAP_MINUTES)
    from app.models.scheduling_models import AppointmentParticipant
    busy = [(p.busy_start_at, p.busy_end_at) for p in db.query(AppointmentParticipant).filter(
        AppointmentParticipant.user_id == owner.id, AppointmentParticipant.is_blocking.is_(True),
        AppointmentParticipant.busy_end_at >= datetime.combine(start, datetime.min.time()) - timedelta(days=1),
        AppointmentParticipant.busy_start_at <= datetime.combine(start, datetime.min.time()) + timedelta(days=days + 1),
        AppointmentParticipant.appointment_id != (exclude_appt_id or "")).all()]
    out = []
    for s in (r or {}).get("slots") or []:
        st = s.get("starts_at") or s.get("start")
        try:
            t = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            if t.tzinfo is not None:
                t = t.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
        except Exception:                                    # noqa: BLE001
            continue
        if t < now:
            continue
        # keep GAP_MINUTES clear before and after every booked call
        if any(t < e + gap and t + dur + gap > b for b, e in busy):
            continue
        out.append(t.isoformat() + "Z")
    return {"slots": out[:40], "duration_minutes": (mt.duration_minutes if mt else 30) or 30,
            "timezone": "America/Chicago"}


def current(db: Session, opp: Opportunity):
    from app.models.scheduling_models import SalesAppointment
    return (db.query(SalesAppointment).filter(SalesAppointment.opportunity_id == opp.id,
                                              SalesAppointment.status == "scheduled",
                                              SalesAppointment.starts_at >= datetime.utcnow())
            .order_by(SalesAppointment.starts_at).first())


def booked_out(db: Session, a) -> dict:
    return {"status": "booked", "starts_at": a.starts_at.isoformat() + "Z", "ends_at": a.ends_at.isoformat() + "Z",
            "zoom_url": zoom_link(db, a.id)}


def reschedule(db: Session, opp: Opportunity, starts_at: datetime) -> dict:
    """The business moves its own call from the booking page. The appointment
    MOVES (same Zoom meeting, same calendar event, updated) - never cancelled
    and recreated."""
    from fastapi import HTTPException
    from app.routers.sales_scheduling_router import RescheduleIn, reschedule_appointment
    a = current(db, opp)
    owner = _owner(db, opp)
    if a is None or owner is None:
        return book(db, opp, starts_at)
    when = starts_at if starts_at.tzinfo is None else \
        starts_at.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
    offered = slots(db, opp, when.date() - timedelta(days=1), 3, exclude_appt_id=a.id).get("slots", [])
    if when not in {datetime.fromisoformat(x[:-1]) for x in offered}:
        return {"status": "taken", "message": "That time was just taken. Please pick another one."}
    try:
        reschedule_appointment(a.id, RescheduleIn(starts_at=when, reason="Moved by the business from its booking link"),
                               user=owner, db=db)
    except HTTPException as e:
        db.rollback()
        if e.status_code == 409:
            return {"status": "taken", "message": "That time was just taken. Please pick another one."}
        log.warning("[evosys-sales booking] %s not moved: %s", opp.id, e.detail)
        return {"status": "error", "message": "We couldn't move your call. Please try another time or text us back."}
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="intro_call_moved",
                            summary="Moved their intro call from the booking link",
                            detail=when.isoformat() + "Z", occurred_at=datetime.utcnow()))
    db.commit()
    db.refresh(a)
    _after_booking(db, opp, when, a.id, moved=True)
    return booked_out(db, a)


def book(db: Session, opp: Opportunity, starts_at: datetime) -> dict:
    from fastapi import HTTPException
    from app.models.scheduling_models import SalesAppointment
    from app.routers.sales_scheduling_router import BookIn, create_appointment
    from app.routers.evosys_sales_work_router import _meeting_type
    from app.services.evosys_sales import outreach as O
    owner = _owner(db, opp)
    if owner is None:
        return {"status": "error", "message": "Booking isn't set up yet. Please text us back and we'll set a time."}
    when = starts_at if starts_at.tzinfo is None else \
        starts_at.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
    prior = (db.query(SalesAppointment).filter(SalesAppointment.opportunity_id == opp.id,
                                               SalesAppointment.status == "scheduled",
                                               SalesAppointment.starts_at >= datetime.utcnow()).first())
    if prior is not None:
        return dict(booked_out(db, prior), already_booked=True)
    if when not in {datetime.fromisoformat(x[:-1]) for x in slots(db, opp, when.date() - timedelta(days=1), 3).get("slots", [])}:
        return {"status": "taken", "message": "That time was just taken. Please pick another one."}
    mt = _meeting_type(db, opp.brand_sales_org_id, MEETING_KEY)
    try:
        made = create_appointment(BookIn(starts_at=when, meeting_type_id=mt.id if mt else None,
                                  duration_minutes=None if mt else 30, opportunity_id=opp.id,
                                  brand_sales_org_id=opp.brand_sales_org_id,
                                  title="Intro call - %s" % (opp.company_name or ""),
                                  timezone="America/Chicago", required_user_ids=[owner.id],
                                  prospect_name=opp.contact_name, prospect_email=opp.email,
                                  prospect_phone=opp.phone, prospect_timezone=None),
                           user=owner, db=db)
    except HTTPException as e:
        db.rollback()
        if e.status_code == 409:
            return {"status": "taken", "message": "That time was just taken. Please pick another one."}
        log.warning("[evosys-sales booking] %s not booked: %s", opp.id, e.detail)
        return {"status": "error", "message": "We couldn't book that time. Please try another one or text us back."}
    O.stop(db, opp.id, "booked")
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="intro_call_booked",
                            summary="Booked their intro call from the booking link",
                            detail=when.isoformat() + "Z", occurred_at=datetime.utcnow()))
    db.commit()
    appt_id = made.get("id") if isinstance(made, dict) else getattr(made, "id", None)
    _after_booking(db, opp, when, appt_id)
    a = db.get(SalesAppointment, appt_id) if appt_id else None
    if a is not None:
        return dict(booked_out(db, a), already_booked=False)
    return {"status": "booked", "already_booked": False, "starts_at": when.isoformat() + "Z", "zoom_url": zoom_link(db, appt_id)}


def _ct(when: datetime, spanish: bool = False) -> str:
    from zoneinfo import ZoneInfo
    t = when.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    if spanish:
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        return "el %s %d a las %s (hora del centro)" % (dias[t.weekday()], t.day, t.strftime("%-I:%M %p"))
    return t.strftime("%a %b %-d at %-I:%M %p") + " Central"


def zoom_link(db: Session, appt_id: Optional[str]) -> Optional[str]:
    """The Zoom join link the scheduler already created for this call (the
    join link only - never the host link)."""
    if not appt_id:
        return None
    from app.models.meeting_models import AppointmentMeeting
    m = (db.query(AppointmentMeeting).filter(AppointmentMeeting.appointment_id == appt_id)
         .order_by(AppointmentMeeting.created_at.desc()).first())
    return m.join_url if m is not None and m.join_url and m.status not in ("cancelled", "failed") else None


def is_spanish(db: Session, opp: Opportunity) -> bool:
    """The business talks to us in Spanish: their last text, or our last
    campaign text to them, is Spanish."""
    from app.models.evosys_sales_models import EvoSysOutreachMessage
    from app.services.evosys_sales.ai import looks_spanish
    last_in = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                      EvoSysOutreachMessage.channel == "text",
                                                      EvoSysOutreachMessage.direction == "in")
               .order_by(EvoSysOutreachMessage.created_at.desc()).first())
    if last_in is not None and len((last_in.body or "").split()) >= 3:
        return looks_spanish(last_in.body)
    first_out = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                        EvoSysOutreachMessage.channel == "text",
                                                        EvoSysOutreachMessage.direction == "out",
                                                        EvoSysOutreachMessage.step_no.isnot(None))
                 .order_by(EvoSysOutreachMessage.created_at).first())
    return bool(first_out is not None and looks_spanish(first_out.body))


def _after_booking(db: Session, opp: Opportunity, when: datetime, appt_id: Optional[str] = None,
                   moved: bool = False) -> None:
    """Confirmation text to the business; booking alert to the owner."""
    from app.models.evosys_sales_models import EvoSysOutreachMessage
    from app.services.evosys_sales import outreach as O
    from app.services.evosys_sales import sms_inbound as SI
    s = O.get_settings(db, opp.brand_sales_org_id)
    to = O.e164(opp.phone)
    if to and O.e164(s.sms_from_number):
        es = is_spanish(db, opp)
        z = zoom_link(db, appt_id)
        if es:
            body = ("%s Su llamada con Mike de EvoSys Pro sobre sus 3 citas gratis es %s. %s"
                    "¿Necesita otro horario? Solo responda este mensaje." %
                    ("¡Listo, la movimos!" if moved else "¡Listo!", _ct(when, True), ("Por Zoom: %s " % z) if z else ""))
        else:
            body = ("%s Your Business Growth Call with Mike from EvoSys Pro is %s. %s"
                    "Need a different time? Just text back." %
                    ("Done - your call is moved!" if moved else "You're booked!", _ct(when),
                     ("Join on Zoom: %s " % z) if z else ""))
        try:
            r = O._send_text(to, body, s)
        except Exception as e:                               # noqa: BLE001
            r = {"success": False, "error": str(e)}
        db.add(EvoSysOutreachMessage(brand_sales_org_id=opp.brand_sales_org_id, opportunity_id=opp.id,
                                     direction="out", channel="text", to_address=to, body=body,
                                     label="booking_confirmation",
                                     status="sent" if r.get("success") else "failed",
                                     error=None if r.get("success") else str(r.get("error") or "")[:500],
                                     provider_message_id=r.get("provider_message_id"), created_at=datetime.utcnow()))
        db.commit()
    who = opp.company_name or opp.contact_name or "A prospect"
    verb = "MOVED their call to" if moved else "BOOKED an intro call for"
    SI.notify_owner(s, "EvoSys Sales - %s: %s (%s) %s %s" % ("MOVED" if moved else "BOOKED", who, opp.phone or "", verb, _ct(when)),
                    "EvoSys Sales - %s %s" % (who, "moved their call" if moved else "booked an intro call"),
                    "<p><b>%s</b> (%s) booked an intro call for <b>%s</b> from their booking link.</p>"
                    "<p>It's on your calendar. Their automatic texts have stopped.</p>"
                    % (__import__("html").escape(who), __import__("html").escape(opp.phone or ""), _ct(when)))


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Business Growth Call - EvoSys Pro</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--navy:#0b1b38;--navy2:#13284d;--gold:#c9a24a;--gold2:#a8812c;--ivory:#f7f2e8;--card:#fffdf8;--ink:#13213b;--muted:#5d6a80;--line:#e6dcc7;--ok:#2f8f5b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;background:var(--ivory);color:var(--ink);min-height:100vh}
.serif{font-family:"Cormorant Garamond",Georgia,serif}
.top{background:var(--navy);color:#fff;padding:14px 22px;display:flex;align-items:center;gap:10px}
.logo{font-family:"Cormorant Garamond",Georgia,serif;font-size:30px;font-weight:700;color:var(--gold);letter-spacing:-1px}
.brand{font-family:"Cormorant Garamond",Georgia,serif;font-size:22px;font-weight:600}
.wrap{max-width:1040px;margin:28px auto;padding:0 16px}
.shell{display:grid;grid-template-columns:300px 1fr;border-radius:18px;overflow:hidden;box-shadow:0 18px 50px rgba(11,27,56,.16);background:var(--card)}
.side{background:linear-gradient(180deg,var(--navy) 0%,var(--navy2) 100%);color:#fff;padding:30px 26px}
.side .eyebrow{color:#d8c48f}
.eyebrow{font-size:11px;letter-spacing:.22em;text-transform:uppercase;font-weight:600;color:var(--gold2)}
.side h2{font-family:"Cormorant Garamond",Georgia,serif;font-size:36px;line-height:1.02;margin:8px 0 22px;font-weight:600}
.host{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.av{width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,#f0dca8,var(--gold));color:var(--navy);display:grid;place-items:center;font-weight:700}
.host b{font-family:"Cormorant Garamond",Georgia,serif;font-size:21px;font-weight:600}
.facts{list-style:none;border-bottom:1px solid rgba(255,255,255,.15);padding-bottom:16px;margin-bottom:16px}
.facts li{display:flex;gap:10px;align-items:center;font-size:14px;margin:9px 0;color:#e8edf6}
.facts svg,.chk svg{flex:none}
.talk h4{color:var(--gold);font-family:"Cormorant Garamond",Georgia,serif;font-size:18px;margin-bottom:8px;font-weight:600}
.chk{list-style:none}.chk li{display:flex;gap:9px;align-items:center;font-size:14px;margin:7px 0;color:#e8edf6}
.main{padding:30px 34px}
.main h1{font-family:"Cormorant Garamond",Georgia,serif;font-size:40px;line-height:1.05;font-weight:600;margin:8px 0 6px}
.prep{color:var(--ink);font-size:15px}.sub{color:var(--muted);font-size:15px;margin-top:4px}
.pick{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:26px}
.pick h3{font-family:"Cormorant Garamond",Georgia,serif;font-size:24px;font-weight:600;margin-bottom:12px}
.mnav{display:flex;align-items:center;justify-content:space-between;margin:4px 0 12px;font-weight:600}
.mnav button{background:none;border:0;font-size:20px;color:var(--ink);cursor:pointer;padding:2px 8px}.mnav button:disabled{opacity:.25;cursor:default}
.days{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.day{border:1px solid var(--line);background:#fff;border-radius:10px;padding:8px 0;text-align:center;cursor:pointer;color:var(--ink)}
.day small{display:block;font-size:13px;color:var(--muted)}.day b{font-family:"Cormorant Garamond",Georgia,serif;font-size:26px;font-weight:600}
.day.on{background:var(--navy);border-color:var(--navy);color:#fff}.day.on small{color:#cfd8ea}
.tz{margin-top:14px;width:100%;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:14px;background:#fff;color:var(--ink)}
.times{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.t{border:1px solid var(--line);background:#fff;border-radius:10px;padding:11px 0;font:inherit;font-weight:600;color:var(--ink);cursor:pointer}
.t.on{background:var(--navy);color:#fff;border-color:var(--navy)}.t.on::before{content:"\2713  "}
.go{margin-top:16px;width:100%;border:0;border-radius:12px;padding:15px;font-family:"Cormorant Garamond",Georgia,serif;font-size:22px;font-weight:600;color:#fff;background:linear-gradient(180deg,#d4ad57,var(--gold2));cursor:pointer;box-shadow:0 6px 16px rgba(168,129,44,.35)}
.go:disabled{opacity:.45;cursor:default;box-shadow:none}
.sum{text-align:center;color:var(--muted);font-size:13px;margin-top:8px}.msg{margin-top:10px;color:#a33a2a;font-size:14px}
.empty{color:var(--muted);font-size:15px;padding:12px 0}
.ok{text-align:center;padding:6px 0}
.ring{width:66px;height:66px;border-radius:50%;border:2px solid var(--gold);display:grid;place-items:center;margin:0 auto 6px}
.ok h1{margin-top:6px}.pill{display:inline-block;background:#dff1e6;color:var(--ok);font-weight:600;font-size:13px;padding:4px 14px;border-radius:999px;margin:6px 0 18px}
.det{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:12px;background:#fff;text-align:left}
.det div{padding:12px 14px;display:flex;gap:9px;align-items:center;font-size:14px;border-left:1px solid var(--line)}.det div:first-child{border-left:0}
.btn-zoom{display:flex;align-items:center;justify-content:center;gap:12px;text-decoration:none;margin:18px auto 10px;max-width:480px}
.row2{display:flex;gap:10px;max-width:480px;margin:0 auto}
.b2{flex:1;border-radius:10px;padding:11px;font:inherit;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;text-decoration:none}
.b2.dark{background:var(--navy);color:#fff;border:1px solid var(--navy)}.b2.light{background:#fff;color:var(--ink);border:1px solid var(--line)}
.foot{text-align:center;color:var(--muted);font-size:13px;margin-top:12px}.foot i{font-family:"Cormorant Garamond",Georgia,serif;font-size:17px}
@media (max-width:820px){
  .shell{grid-template-columns:1fr}.side{padding:20px 20px 14px}.side h2{font-size:28px;margin-bottom:12px}
  .talk{display:none}.facts{display:flex;flex-wrap:wrap;gap:4px 14px;border:0;margin:0;padding:0}.facts li{margin:2px 0;font-size:13px}
  .main{padding:22px 18px}.main h1{font-size:32px}.pick{grid-template-columns:1fr;gap:18px}
  .det{grid-template-columns:1fr}.det div{border-left:0;border-top:1px solid var(--line)}.det div:first-child{border-top:0}
  .wrap{margin:14px auto}
}
</style></head><body>
<div class="top"><span class="logo">EP</span><span class="brand">EvoSys Pro</span></div>
<div class="wrap"><div class="shell">
 <aside class="side">
  <div class="eyebrow">Business Growth</div>
  <h2>Business Growth Call</h2>
  <div class="host"><div class="av">MS</div><b>Mike Simmons</b></div>
  <ul class="facts">
   <li><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#d8c48f" stroke-width="2"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10l6-3v10l-6-3z"/></svg>Zoom video meeting</li>
   <li><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#d8c48f" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>__MINS__ minutes</li>
   <li><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#d8c48f" stroke-width="2"><path d="M20 12l-8 8-8-8V4h8z"/><circle cx="8" cy="8" r="1.5"/></svg>No cost. No obligation.</li>
  </ul>
  <div class="talk"><h4>We'll talk about:</h4><ul class="chk">
   <li><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#c9a24a" stroke-width="3"><path d="M4 12l5 5L20 6"/></svg>Your service area</li>
   <li><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#c9a24a" stroke-width="3"><path d="M4 12l5 5L20 6"/></svg>Your ideal customers</li>
   <li><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#c9a24a" stroke-width="3"><path d="M4 12l5 5L20 6"/></svg>How the pilot works</li></ul></div>
 </aside>
 <main class="main" id="app"><p class="empty">Loading open times&hellip;</p></main>
</div></div>
<script>
const T=__TOKEN__, U=__BASE__+'/'+T, COMPANY=__COMPANY__, MINS=__MINS__;
const app=document.getElementById('app');
const TZS=[['America/Chicago','Central Time (Chicago)'],['America/New_York','Eastern Time (New York)'],['America/Denver','Mountain Time (Denver)'],['America/Phoenix','Arizona Time (Phoenix)'],['America/Los_Angeles','Pacific Time (Los Angeles)']];
let tz=(()=>{try{const z=Intl.DateTimeFormat().resolvedOptions().timeZone;return TZS.some(x=>x[0]===z)?z:'America/Chicago'}catch(e){return 'America/Chicago'}})();
let slots=[], dayKey=null, sel=null, from=null, nextFrom=null, starts=[], resched=false;
const esc=s=>String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dk=d=>new Date(d).toLocaleDateString('en-CA',{timeZone:tz});
const fDay=(d,o)=>new Date(d).toLocaleDateString('en-US',Object.assign({timeZone:tz},o));
const fT=d=>new Date(d).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:tz});
const tzShort=()=>({'America/Chicago':'CT','America/New_York':'ET','America/Denver':'MT','America/Phoenix':'MST','America/Los_Angeles':'PT'})[tz]||'';
const head=(h1,sub)=>'<div class="eyebrow">Business Growth</div><h1>'+h1+'</h1>'+(COMPANY?'<p class="prep">Prepared for '+esc(COMPANY)+'</p>':'')+'<p class="sub">'+sub+'</p>';
async function load(start){
 const q=[]; if(start)q.push('from='+start); if(resched)q.push('reschedule=1');
 const r=await fetch(U+'/slots'+(q.length?'?'+q.join('&'):'')); const j=await r.json();
 if(j.booked&&!resched){done(j.booked);return}
 slots=j.slots||[]; nextFrom=j.next_from; if(start)starts.push(start); from=start||null;
 const days=[...new Set(slots.map(dk))]; if(!days.includes(dayKey))dayKey=days[0]||null; sel=null; render();
}
function render(){
 const days=[...new Set(slots.map(dk))];
 const month=slots.length?fDay(slots[0],{month:'long',year:'numeric'}):'';
 let h=head(resched?'Pick a new time.':'Let’s talk about your next stage of growth.','Explore your complimentary appointment-setting pilot.');
 h+='<div class="pick"><section><h3>Choose your day</h3>';
 h+='<div class="mnav"><button id="prev" '+(starts.length?'':'disabled')+' aria-label="Earlier">&#8249;</button><span>'+month+'</span><button id="next" aria-label="Later">&#8250;</button></div>';
 h+=days.length?'<div class="days">'+days.map(d=>{const x=slots.find(s=>dk(s)===d);return '<button class="day'+(d===dayKey?' on':'')+'" data-d="'+d+'"><small>'+fDay(x,{weekday:'short'})+'</small><b>'+fDay(x,{day:'numeric'})+'</b></button>'}).join('')+'</div>':'<p class="empty">No open times this week. Tap &#8250; for the next week, or just text us back.</p>';
 h+='<select class="tz" id="tz">'+TZS.map(z=>'<option value="'+z[0]+'"'+(z[0]===tz?' selected':'')+'>&#9906;&nbsp; '+z[1]+'</option>').join('')+'</select></section>';
 h+='<section>';
 if(dayKey){const ts=slots.filter(s=>dk(s)===dayKey);const x=ts[0];
  h+='<h3>'+fDay(x,{weekday:'long',month:'long',day:'numeric'})+'</h3><div class="times">'+ts.map(s=>'<button class="t'+(s===sel?' on':'')+'" data-t="'+s+'">'+fT(s)+'</button>').join('')+'</div>';}
 h+='<button class="go" id="go" '+(sel?'':'disabled')+'>'+(resched?'Move my call':'Confirm my call')+' &rarr;</button>';
 h+='<p class="sum">'+(sel?fDay(sel,{weekday:'short',month:'short',day:'numeric'})+' &middot; '+fT(sel)+' '+tzShort():'Your Zoom link will appear after booking.')+'</p><p class="msg" id="msg"></p></section></div>';
 app.innerHTML=h;
 app.querySelectorAll('.day').forEach(b=>b.onclick=()=>{dayKey=b.dataset.d;sel=null;render()});
 app.querySelectorAll('.t').forEach(b=>b.onclick=()=>{sel=b.dataset.t;render()});
 document.getElementById('tz').onchange=e=>{tz=e.target.value;dayKey=null;sel=null;const d=[...new Set(slots.map(dk))];dayKey=d[0]||null;render()};
 document.getElementById('next').onclick=()=>load(nextFrom);
 const p=document.getElementById('prev'); if(p)p.onclick=()=>{starts.pop();const s=starts.pop();load(s||null)};
 document.getElementById('go').onclick=book;
}
async function book(){
 const g=document.getElementById('go'); g.disabled=true; g.innerHTML='Booking&hellip;';
 const r=await fetch(U,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({starts_at:sel,reschedule:resched})}); const j=await r.json();
 if(j.status==='booked'){resched=false;done(j)} else {document.getElementById('msg').textContent=j.message||'Something went wrong. Please pick another time.'; setTimeout(()=>load(from),1200)}
}
function ics(b){
 const s=new Date(b.starts_at), e=new Date(b.ends_at||(s.getTime()+MINS*60000));
 const f=d=>d.toISOString().replace(/[-:]/g,'').replace(/\.\d{3}/,'');
 const z=b.zoom_url||'';
 const body=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//EvoSys Pro//Booking//EN','BEGIN:VEVENT','UID:'+T.slice(0,24)+'-'+f(s)+'@evosyspro.live','DTSTAMP:'+f(new Date()),'DTSTART:'+f(s),'DTEND:'+f(e),
  'SUMMARY:Business Growth Call with Mike Simmons (EvoSys Pro)','LOCATION:'+(z||'Zoom'),'DESCRIPTION:Your complimentary appointment-setting pilot.'+(z?'\\nJoin on Zoom: '+z:''),'END:VEVENT','END:VCALENDAR'].join('\r\n');
 return 'data:text/calendar;charset=utf-8,'+encodeURIComponent(body);
}
function done(b){
 if(typeof b==='string')b={starts_at:b};
 const s=b.starts_at, e=b.ends_at||new Date(new Date(s).getTime()+MINS*60000).toISOString();
 let h='<div class="ok"><div class="ring"><svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#c9a24a" stroke-width="2.4"><path d="M4 12l5 5L20 6"/></svg></div>';
 h+='<div class="eyebrow">Business Growth</div><h1>You’re on Mike’s calendar.</h1><span class="pill">Confirmed</span>';
 h+='<div class="det"><div>&#128197; Business Growth Call</div><div>&#128198; '+fDay(s,{weekday:'long',month:'long',day:'numeric'})+'</div><div>&#128339; '+fT(s)+'&ndash;'+fT(e)+' '+tzShort()+'</div><div>&#127909; Join Mike on Zoom</div></div>';
 if(COMPANY)h+='<p class="prep" style="margin-top:12px">Prepared for '+esc(COMPANY)+'</p>';
 h+=b.zoom_url?'<a class="go btn-zoom" href="'+esc(b.zoom_url)+'" target="_blank" rel="noopener">&#127909;&nbsp; Join Zoom Meeting</a>':'<p class="sum" style="margin:16px 0">Your Zoom link is on its way by text.</p>';
 h+='<div class="row2"><a class="b2 dark" href="'+ics(b)+'" download="evosys-pro-call.ics">&#128197; Add to calendar</a><button class="b2 light" id="rs">&#8635; Reschedule</button></div>';
 h+='<p class="foot">Zoom link included in your calendar event.</p><p class="foot"><i>No cost. No obligation.</i></p></div>';
 app.innerHTML=h;
 document.getElementById('rs').onclick=()=>{resched=true;starts=[];load(null)};
}
load(null);
</script></body></html>
"""


def render(token: str, company: Optional[str], mins: int = 30) -> str:
    import json as _json
    return (PAGE.replace("__TOKEN__", _json.dumps(token)).replace("__BASE__", _json.dumps(PATH))
            .replace("__COMPANY__", _json.dumps(company or "")).replace("__MINS__", str(int(mins))))



# ── text reminders (owner request 2026-11-18) ─────────────────────────────────

REMINDER_DAY_HOURS = (9, 19)        # day-before reminders only go 9 AM - 7 PM Central


def _local(dt: datetime):
    from zoneinfo import ZoneInfo
    return dt.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(ZoneInfo("America/Chicago"))


def send_reminders(db: Session, now: Optional[datetime] = None) -> dict:
    """Text each booked business a reminder the day before its intro call and
    another 1 hour before, from the team number. Each reminder is recorded on
    the lead and sent once per appointment time; a moved call gets new ones.
    A business that opted out or is on Do Not Contact gets nothing."""
    from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
    from app.models.scheduling_models import SalesAppointment
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales import outreach as O
    now = now or datetime.utcnow()
    out = {"reminders_sent": 0, "reminders_failed": 0}
    bso = O.brand_id(db)
    if not bso:
        return out
    s = O.get_settings(db, bso)
    if not O.e164(s.sms_from_number):
        return out
    appts = (db.query(SalesAppointment)
             .filter(SalesAppointment.brand_sales_org_id == bso, SalesAppointment.status == "scheduled",
                     SalesAppointment.opportunity_id.isnot(None),
                     SalesAppointment.starts_at > now, SalesAppointment.starts_at <= now + timedelta(hours=30))
             .all())
    for a in appts:
        opp = db.get(Opportunity, a.opportunity_id)
        to = O.e164(opp.phone) if opp is not None else None
        if not to:
            continue
        st = db.get(EvoSysOutreachState, opp.id)
        if (st is not None and st.opted_out_at) or SDNC.for_opportunity(db, opp) or SDNC.find(db, bso, opp.phone, opp.email):
            continue
        left = a.starts_at - now
        kind = None
        if left <= timedelta(minutes=75):
            kind = "reminder_1h"
        elif left <= timedelta(hours=28) and REMINDER_DAY_HOURS[0] <= _local(now).hour < REMINDER_DAY_HOURS[1] \
                and _local(a.starts_at).date() > _local(now).date():
            kind = "reminder_day"
        if kind is None:
            continue
        stamp = a.starts_at.strftime("%Y%m%d%H%M")
        tag = ("r1:" if kind == "reminder_1h" else "rd:") + stamp          # fits the 30-character label
        if db.query(EvoSysOutreachMessage.id).filter(EvoSysOutreachMessage.opportunity_id == opp.id,
                                                     EvoSysOutreachMessage.label == tag).first():
            continue
        if kind == "reminder_day" and db.query(EvoSysOutreachMessage.id).filter(
                EvoSysOutreachMessage.opportunity_id == opp.id,
                EvoSysOutreachMessage.label == "r1:" + stamp).first():
            continue
        t = _local(a.starts_at).strftime("%-I:%M %p")
        z = zoom_link(db, a.id)
        if is_spanish(db, opp):
            when = ("mañana a las %s (hora del centro)" % t) if kind == "reminder_day" else \
                ("en una hora, a las %s (hora del centro)" % t)
            body = ("Recordatorio: su llamada con Mike de EvoSys Pro sobre sus 3 citas gratis es %s. %s"
                    "¿Necesita otro horario? Solo responda." % (when, ("Por Zoom: %s " % z) if z else ""))
        else:
            when = ("tomorrow at %s Central" % t) if kind == "reminder_day" else ("in about an hour, at %s Central" % t)
            body = ("Reminder: your call with Mike from EvoSys Pro about your 3 free appointments is %s. %s"
                    "Need a different time? Just text back." % (when, ("Join on Zoom: %s " % z) if z else ""))
        try:
            r = O._send_text(to, body, s)
        except Exception as e:                               # noqa: BLE001
            r = {"success": False, "error": str(e)}
        ok = bool(r.get("success"))
        db.add(EvoSysOutreachMessage(brand_sales_org_id=bso, opportunity_id=opp.id, direction="out", channel="text",
                                     to_address=to, body=body, label=tag, status="sent" if ok else "failed",
                                     error=None if ok else str(r.get("error") or "")[:500],
                                     provider_message_id=r.get("provider_message_id"), created_at=now))
        db.commit()
        out["reminders_sent" if ok else "reminders_failed"] += 1
    return out
