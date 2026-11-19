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


def slots(db: Session, opp: Opportunity, start: Optional[date] = None, days: int = 7) -> dict:
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
                                      brand_sales_org_id=opp.brand_sales_org_id), user=owner, db=db)
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
        AppointmentParticipant.busy_start_at <= datetime.combine(start, datetime.min.time()) + timedelta(days=days + 1)).all()]
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
        return {"status": "booked", "already_booked": True, "starts_at": prior.starts_at.isoformat() + "Z"}
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
    return {"status": "booked", "already_booked": False, "starts_at": when.isoformat() + "Z"}


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


def _after_booking(db: Session, opp: Opportunity, when: datetime, appt_id: Optional[str] = None) -> None:
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
            body = ("¡Listo! Su llamada con Mike de EvoSys Pro sobre sus 3 citas gratis es %s. %s"
                    "¿Necesita otro horario? Solo responda este mensaje." %
                    (_ct(when, True), ("Por Zoom: %s " % z) if z else ""))
        else:
            body = ("You're booked! Your call with Mike from EvoSys Pro about your 3 free appointments is %s. %s"
                    "Need a different time? Just text back." % (_ct(when), ("Join on Zoom: %s " % z) if z else ""))
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
    SI.notify_owner(s, "EvoSys Sales - BOOKED: %s (%s) booked an intro call for %s" % (who, opp.phone or "", _ct(when)),
                    "EvoSys Sales - %s booked an intro call" % who,
                    "<p><b>%s</b> (%s) booked an intro call for <b>%s</b> from their booking link.</p>"
                    "<p>It's on your calendar. Their automatic texts have stopped.</p>"
                    % (__import__("html").escape(who), __import__("html").escape(opp.phone or ""), _ct(when)))


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Book your call - EvoSys Pro</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f0f4f8;color:#14233b;padding:20px}
.card{max-width:520px;margin:0 auto;background:#fff;border-radius:16px;box-shadow:0 2px 16px rgba(0,0,0,.08);overflow:hidden}
.hd{background:#0b2a55;color:#fff;padding:22px}.hd b{font-size:13px;letter-spacing:.08em;opacity:.8}.hd h1{font-size:22px;margin-top:6px}.hd p{opacity:.9;margin-top:6px;font-size:15px}
.bd{padding:20px}.day{margin-bottom:16px}.day h3{font-size:14px;color:#46576e;margin-bottom:8px}.grid{display:flex;flex-wrap:wrap;gap:8px}
button.t{border:1px solid #c7d4e6;background:#f7fafd;color:#0b2a55;border-radius:10px;padding:10px 14px;font-size:15px;font-weight:600;cursor:pointer}
button.t.on{background:#0b62d6;color:#fff;border-color:#0b62d6}.go{width:100%%;margin-top:10px;background:#0b62d6;color:#fff;border:0;border-radius:12px;padding:14px;font-size:17px;font-weight:700;cursor:pointer}
.go:disabled{opacity:.45}.msg{margin-top:14px;font-size:15px}.ok{text-align:center;padding:30px 20px}.ok h2{font-size:22px;margin:10px 0}
.more{background:none;border:0;color:#0b62d6;font-weight:600;margin-top:4px;cursor:pointer;font-size:15px}.ft{text-align:center;color:#6b7c93;font-size:13px;margin-top:16px}
</style></head><body><div class="card"><div class="hd"><b>EVOSYS PRO</b><h1>Pick a time to talk</h1>
<p>%(company)sA quick %(mins)s-minute call with Mike about your <b>3 free appointments</b> &mdash; where you work and the customers you want.</p></div>
<div class="bd" id="app">Loading open times&hellip;</div></div><div class="ft">Times shown in Central Time. No cost, no obligation.</div>
<script>
const T=%(token)s, U=%(base)s+'/'+T; let sel=null, from=null;
const fmtDay=d=>new Date(d).toLocaleDateString('en-US',{weekday:'long',month:'short',day:'numeric',timeZone:'America/Chicago'});
const fmtT=d=>new Date(d).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:'America/Chicago'});
const app=document.getElementById('app');
async function load(start){const r=await fetch(U+'/slots'+(start?'?from='+start:''));const j=await r.json();
 if(j.booked){done(j.booked,true);return}
 const s=j.slots||[]; if(!s.length){app.innerHTML='<p>No open times this week. <button class="more" onclick="next()">See next week &rarr;</button></p><p class="msg">Or just text us back and we\\'ll find a time.</p>';from=j.next_from;return}
 from=j.next_from; const by={}; s.forEach(x=>{const k=fmtDay(x);(by[k]=by[k]||[]).push(x)});
 app.innerHTML=Object.entries(by).map(([d,xs])=>'<div class="day"><h3>'+d+'</h3><div class="grid">'+xs.map(x=>'<button class="t" data-t="'+x+'">'+fmtT(x)+'</button>').join('')+'</div></div>').join('')+
 '<button class="more" onclick="next()">See later times &rarr;</button><button class="go" id="go" disabled>Book this time</button><div class="msg" id="msg"></div>';
 app.querySelectorAll('button.t').forEach(b=>b.onclick=()=>{app.querySelectorAll('button.t').forEach(x=>x.classList.remove('on'));b.classList.add('on');sel=b.dataset.t;const g=document.getElementById('go');g.disabled=false;g.textContent='Book '+fmtDay(sel)+' at '+fmtT(sel)});
 document.getElementById('go').onclick=book}
function next(){load(from)}
async function book(){const g=document.getElementById('go');g.disabled=true;g.textContent='Booking\\u2026';
 const r=await fetch(U,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({starts_at:sel})});const j=await r.json();
 if(j.status==='booked'){done(j.starts_at,j.already_booked)}else{document.getElementById('msg').textContent=j.message||'Something went wrong. Please try another time.';load()}}
function done(t,already){app.innerHTML='<div class="ok"><div style="font-size:44px">&#9989;</div><h2>'+(already?'You\\'re already booked':'You\\'re booked!')+'</h2><p>'+fmtDay(t)+' at '+fmtT(t)+' Central.</p><p class="msg">Mike will call you then. We\\'ll text you a confirmation. Need a different time? Just text us back.</p></div>'}
load();
</script></body></html>"""


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
