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
CALL_TITLE = "Business Growth Call"
ZOOM_RETRY_MAX = 4                 # lazy re-tries of a failed Zoom room, from the page
ZOOM_RETRY_EVERY = timedelta(minutes=2)
ZOOM_GIVE_UP_AFTER = timedelta(minutes=10)   # e.g. Zoom not connected: stop saying "being prepared"


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
    # a full week of 15-minute starts (40 cut the week to ~3 days)
    return {"slots": out[:400], "duration_minutes": (mt.duration_minutes if mt else 30) or 30,
            "timezone": "America/Chicago"}


def current(db: Session, opp: Opportunity):
    from app.models.scheduling_models import SalesAppointment
    return (db.query(SalesAppointment).filter(SalesAppointment.opportunity_id == opp.id,
                                              SalesAppointment.status == "scheduled",
                                              SalesAppointment.starts_at >= datetime.utcnow())
            .order_by(SalesAppointment.starts_at).first())


def booked_out(db: Session, a) -> dict:
    """The confirmation the page shows, read back from the saved appointment."""
    out = {"status": "booked", "starts_at": a.starts_at.isoformat() + "Z", "ends_at": a.ends_at.isoformat() + "Z",
           "duration_minutes": int((a.ends_at - a.starts_at).total_seconds() // 60), "title": CALL_TITLE,
           "confirmed": a.confirmation_status == "confirmed"}
    out.update(zoom_state(db, a))
    return out


def reschedule(db: Session, opp: Opportunity, starts_at: datetime) -> dict:
    """The business moves its own call from the booking page. The appointment
    MOVES (same Zoom meeting, same calendar event, updated) - never cancelled
    and recreated."""
    from fastapi import HTTPException
    from app.routers.sales_scheduling_router import RescheduleIn, reschedule_appointment
    _lock(db, opp)
    a = current(db, opp)
    owner = _owner(db, opp)
    if a is None or owner is None:
        return book(db, opp, starts_at)
    when = starts_at if starts_at.tzinfo is None else \
        starts_at.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)
    if when == a.starts_at:
        return dict(booked_out(db, a), already_booked=True)
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
    _lock(db, opp)
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
                                  title=("%s - %s" % (CALL_TITLE, opp.company_name)) if opp.company_name else CALL_TITLE,
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
    log.warning("[evosys-sales booking] %s booked but appointment %s not found after save", opp.id, appt_id)
    return {"status": "error", "message": "We couldn't confirm your booking. Please refresh this page or text us back."}


def _ct(when: datetime, spanish: bool = False) -> str:
    from zoneinfo import ZoneInfo
    t = when.replace(tzinfo=__import__("datetime").timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    if spanish:
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        return "el %s %d a las %s (hora del centro)" % (dias[t.weekday()], t.day, t.strftime("%-I:%M %p"))
    return t.strftime("%a %b %-d at %-I:%M %p") + " Central"


def zoom_state(db: Session, appt, now: Optional[datetime] = None) -> dict:
    """What the public page may show about the Zoom room for THIS appointment.

    Only the attendee join link ever leaves here (never the host link, which is
    stored encrypted on another column). A room that failed is retried a few
    times, spaced out, when the business looks at its page; the reason it failed
    stays in the logs and on the appointment, never on the page.
    """
    from app.models.meeting_models import AppointmentMeeting
    from app.services import appointment_meetings as apmeet
    now = now or datetime.utcnow()
    m = (db.query(AppointmentMeeting).filter(AppointmentMeeting.appointment_id == appt.id)
         .order_by(AppointmentMeeting.created_at.desc()).first())
    ok = ("created", "updated")
    if m is not None and m.status in ok and m.join_url:
        return {"zoom_status": "ready", "zoom_url": m.join_url}
    stale = m is None or (m.updated_at or m.created_at or now) <= now - ZOOM_RETRY_EVERY
    if (m is None or m.status in ("pending", "failed")) and stale and (m is None or (m.attempts or 0) < ZOOM_RETRY_MAX):
        try:
            r = apmeet.ensure_meeting(db, appt, now=now)
            if r.get("ok") and r.get("join_url"):
                return {"zoom_status": "ready", "zoom_url": r["join_url"]}
            log.warning("[evosys-sales booking] zoom not ready for %s: %s", appt.id, r.get("reason") or r.get("status"))
        except Exception:                                            # noqa: BLE001
            log.exception("[evosys-sales booking] zoom retry crashed for %s", appt.id)
        m = (db.query(AppointmentMeeting).filter(AppointmentMeeting.appointment_id == appt.id)
             .order_by(AppointmentMeeting.created_at.desc()).first())
    gave_up = m is not None and m.status == "failed" and (
        (m.attempts or 0) >= ZOOM_RETRY_MAX or (m.created_at or now) <= now - ZOOM_GIVE_UP_AFTER)
    if gave_up:
        _zoom_failed_alert(db, appt)
        return {"zoom_status": "unavailable", "zoom_url": None}
    return {"zoom_status": "preparing", "zoom_url": None}


def _zoom_failed_alert(db: Session, appt) -> None:
    """Once per appointment: tell Mike (and everyone on alert_also) that this
    business has no Zoom link, since its page now promises one before the call."""
    from app.services.evosys_sales import sms_inbound as SI
    from app.models.evosys_sales_models import EvoSysOutreachSettings
    tag = "zoom-failed:%s" % appt.id
    if db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == appt.opportunity_id,
                                         OpportunityEvent.event_type == "zoom_link_failed",
                                         OpportunityEvent.detail == tag).first() is not None:
        return
    db.add(OpportunityEvent(opportunity_id=appt.opportunity_id, event_type="zoom_link_failed",
                            summary="Zoom link could not be created for the booked call - send one by hand",
                            detail=tag, occurred_at=datetime.utcnow()))
    db.commit()
    opp = db.get(Opportunity, appt.opportunity_id)
    s = db.query(EvoSysOutreachSettings).filter(
        EvoSysOutreachSettings.brand_sales_org_id == appt.brand_sales_org_id).first()
    if opp is None or s is None:
        return
    try:
        who = opp.company_name or opp.phone or "A business"
        SI.notify_owner(s, "EvoSys Sales - ZOOM LINK FAILED: %s's call %s has no Zoom link. Please send them one."
                        % (who, _ct(appt.starts_at)),
                        "EvoSys Sales - Zoom link failed for %s" % who,
                        "<p>%s's call on %s has no Zoom link (the Zoom room could not be created). "
                        "Please send them a link before the call.</p>" % (who, _ct(appt.starts_at)))
    except Exception:                                                # noqa: BLE001
        log.exception("[evosys-sales booking] zoom-failed alert not sent for %s", appt.id)


def ics_for(db: Session, opp: Opportunity, a, now: Optional[datetime] = None) -> str:
    """The business's own copy of its call, as a calendar file.

    Same UID as the emailed invitation (ics_uid of the appointment and the
    prospect's email), and SEQUENCE rises with each move - so opening it after
    the invite, or after a reschedule, updates the one event instead of adding
    a second. No internal notes, no host link."""
    from app.services.ics_builder import build_ics, ics_uid
    from app.services.evosys_sales.outreach import text_company_name
    z = zoom_state(db, a, now).get("zoom_url")
    who = text_company_name(opp.company_name)
    desc = "Your complimentary appointment-setting pilot call with Mike Simmons, EvoSys Pro."
    if who:
        desc += "\nPrepared for %s." % who
    desc += ("\nJoin on Zoom: %s" % z) if z else "\nYour Zoom link will be on your booking page."
    desc += "\nNeed a different time? Use your booking link or text us back."
    return build_ics(uid=ics_uid(a.id, a.prospect_email or ""), starts_at=a.starts_at, ends_at=a.ends_at,
                     summary="%s with Mike Simmons (EvoSys Pro)" % CALL_TITLE, description=desc,
                     location=z or "Zoom video meeting", method="PUBLISH",
                     sequence=a.rescheduled_count or 0, dtstamp=now, url=z or "")


def _lock(db: Session, opp: Opportunity) -> None:
    """One booking at a time per lead: a double-click or a retried request
    waits here, then sees the first one's appointment."""
    try:
        db.query(Opportunity).filter(Opportunity.id == opp.id).with_for_update().first()
    except Exception:                                                # noqa: BLE001
        pass


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
:root{--navy:#0b1b38;--navy2:#13284d;--gold:#c9a24a;--gold2:#a8812c;--ivory:#f7f2e8;--card:#fffdf8;--ink:#13213b;--muted:#5d6a80;--line:#e6dcc7;--ok:#2f8f5b;--warn:#8a5a12}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;background:var(--ivory);color:var(--ink);min-height:100vh;-webkit-text-size-adjust:100%}
.ic{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;vertical-align:-4px}
.gold{color:var(--gold)}.soft{color:#d8c48f}
.top{background:var(--navy);color:#fff;padding:14px 22px;display:flex;align-items:center;gap:10px}
.logo{font-family:"Cormorant Garamond",Georgia,serif;font-size:30px;font-weight:700;color:var(--gold);letter-spacing:-1px;line-height:1}
.brand{font-family:"Cormorant Garamond",Georgia,serif;font-size:22px;font-weight:600}
.wrap{max-width:1040px;margin:28px auto;padding:0 16px}
.shell{display:grid;grid-template-columns:300px minmax(0,1fr);border-radius:18px;overflow:hidden;box-shadow:0 18px 50px rgba(11,27,56,.16);background:var(--card)}
.side{background:linear-gradient(180deg,var(--navy) 0%,var(--navy2) 100%);color:#fff;padding:30px 26px}
.side .eyebrow{color:#d8c48f}
.eyebrow{font-size:11px;letter-spacing:.22em;text-transform:uppercase;font-weight:600;color:var(--gold2)}
.side h2{font-family:"Cormorant Garamond",Georgia,serif;font-size:36px;line-height:1.02;margin:8px 0 22px;font-weight:600}
.host{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.av{width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,#f0dca8,var(--gold));color:var(--navy);display:grid;place-items:center;font-weight:700;flex:none}
.host b{font-family:"Cormorant Garamond",Georgia,serif;font-size:21px;font-weight:600}
.facts{list-style:none;border-bottom:1px solid rgba(255,255,255,.15);padding-bottom:16px;margin-bottom:16px}
.facts li{display:flex;gap:10px;align-items:center;font-size:14px;margin:9px 0;color:#e8edf6}
.talk h4{color:var(--gold);font-family:"Cormorant Garamond",Georgia,serif;font-size:18px;margin-bottom:8px;font-weight:600}
.chk{list-style:none}.chk li{display:flex;gap:9px;align-items:center;font-size:14px;margin:7px 0;color:#e8edf6}
.main{padding:30px 34px;min-width:0}
.main h1{font-family:"Cormorant Garamond",Georgia,serif;font-size:40px;line-height:1.05;font-weight:600;margin:8px 0 6px}
.prep{color:var(--ink);font-size:15px}.sub{color:var(--muted);font-size:15px;margin-top:4px}
.cover{display:none}
.pick{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:28px;margin-top:26px}
.pick h3{font-family:"Cormorant Garamond",Georgia,serif;font-size:24px;font-weight:600;margin-bottom:12px}
.mnav{display:flex;align-items:center;justify-content:space-between;margin:4px 0 12px;font-weight:600}
.mnav button{background:none;border:0;font-size:22px;color:var(--ink);cursor:pointer;width:44px;height:44px;border-radius:10px}.mnav button:disabled{opacity:.25;cursor:default}
.days{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.day{border:1px solid var(--line);background:#fff;border-radius:10px;padding:8px 0;text-align:center;cursor:pointer;color:var(--ink);font:inherit;min-height:56px}
.day small{display:block;font-size:13px;color:var(--muted)}.day b{font-family:"Cormorant Garamond",Georgia,serif;font-size:26px;font-weight:600;line-height:1.1}
.day.on{background:var(--navy);border-color:var(--navy);color:#fff}.day.on small{color:#cfd8ea}
.tzw{position:relative;margin-top:14px}.tzw .ic{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--gold2);pointer-events:none}
.tz{width:100%;border:1px solid var(--line);border-radius:10px;padding:11px 12px 11px 38px;font:inherit;font-size:14px;background:#fff;color:var(--ink);min-height:44px}
.times{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.t{border:1px solid var(--line);background:#fff;border-radius:10px;padding:11px 0;font:inherit;font-weight:600;color:var(--ink);cursor:pointer;min-height:44px;display:flex;align-items:center;justify-content:center;gap:6px}
.t.on{background:var(--navy);color:#fff;border-color:var(--navy)}
.go{margin-top:16px;width:100%;border:0;border-radius:12px;padding:15px;font-family:"Cormorant Garamond",Georgia,serif;font-size:22px;font-weight:600;color:#fff;background:linear-gradient(180deg,#d4ad57,var(--gold2));cursor:pointer;box-shadow:0 6px 16px rgba(168,129,44,.35);display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none;min-height:52px}
.go:disabled{opacity:.45;cursor:default;box-shadow:none}
.sum{text-align:center;color:var(--muted);font-size:13px;margin-top:8px}.msg{margin-top:10px;color:#a33a2a;font-size:14px;min-height:1px}
.empty{color:var(--muted);font-size:15px;padding:12px 0}
.note{border:1px solid var(--line);background:#fff;border-radius:12px;padding:12px 14px;font-size:14px;display:flex;gap:10px;align-items:flex-start;margin-top:14px}
.note b{display:block}.note .row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.lnk{background:none;border:0;color:var(--navy);font:inherit;font-weight:600;text-decoration:underline;cursor:pointer;min-height:44px}
.ok{text-align:center;padding:6px 0}
.ring{width:66px;height:66px;border-radius:50%;border:2px solid var(--gold);display:grid;place-items:center;margin:0 auto 6px;color:var(--gold)}.ring .ic{width:34px;height:34px;stroke-width:2.4}
.ok h1{margin:12px 0 18px}.pill{display:inline-flex;gap:6px;align-items:center;background:#dff1e6;color:var(--ok);font-weight:600;font-size:13px;padding:4px 14px;border-radius:999px;margin:6px 0 18px}
.det{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);border-radius:12px;background:#fff;text-align:left}
.det div{padding:12px 14px;display:flex;gap:9px;align-items:center;font-size:14px;border-left:1px solid var(--line)}.det div:first-child{border-left:0}
.det .ic{color:var(--gold2)}
.zoomwrap{max-width:480px;margin:18px auto 10px}
.prepping{display:flex;gap:10px;align-items:center;justify-content:center;border:1px dashed var(--gold);border-radius:12px;padding:14px;color:var(--warn);font-weight:600;font-size:15px;background:#fffaf0}
.spin{animation:sp 1s linear infinite}@keyframes sp{to{transform:rotate(360deg)}}
.row2{display:flex;gap:10px;max-width:480px;margin:0 auto}
.b2{flex:1;white-space:nowrap;border-radius:10px;padding:11px 10px;font:inherit;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px;text-decoration:none;min-height:46px}
.b2.dark{background:var(--navy);color:#fff;border:1px solid var(--navy)}.b2.light{background:#fff;color:var(--ink);border:1px solid var(--line)}
.foot{text-align:center;color:var(--muted);font-size:13px;margin-top:12px}.foot i{font-family:"Cormorant Garamond",Georgia,serif;font-size:17px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media (max-width:820px){
  .top{padding:10px 16px}.logo{font-size:24px}.brand{font-size:19px}
  .wrap{margin:12px auto;padding:0 12px}
  .shell{grid-template-columns:1fr;border-radius:16px}
  .side{padding:16px 18px 14px}.side .eyebrow,.talk{display:none}
  .side h2{font-size:26px;margin:0 0 10px}
  .host{gap:8px;margin:0 0 6px}.av{width:30px;height:30px;font-size:12px}.host b{font-size:18px}
  .facts{display:flex;flex-wrap:wrap;gap:2px 14px;border:0;margin:0;padding:0}.facts li{margin:2px 0;font-size:13px;gap:6px}
  .main{padding:16px 16px 20px}.main .lead{display:none}
  .cover{display:block;margin-top:6px;font-size:14px}.cover summary{cursor:pointer;color:var(--gold2);font-weight:600;min-height:40px;display:flex;align-items:center;gap:6px;list-style:none}.cover summary::-webkit-details-marker{display:none}.cover[open] .chev{transform:rotate(180deg)}
  .cover ul{list-style:none;padding:2px 0 4px}.cover li{display:flex;gap:8px;align-items:center;margin:4px 0;color:var(--muted)}
  .pick{grid-template-columns:minmax(0,1fr);gap:16px;margin-top:14px}.pick h3{font-size:20px;margin-bottom:8px}
  .mnav{margin:0 0 8px}
  .ring,.ok .eyebrow,.det .m-hide,.ok .d-only{display:none}
  .ok h1{font-size:30px;margin:4px 0 12px}.pill{margin:0}
  .det{grid-template-columns:1fr}.det div{border-left:0;border-top:1px solid var(--line);font-size:15px}.det div.m-first{border-top:0}
}
@media (max-width:420px){.b2{font-size:15px;gap:6px}}
@media (max-width:360px){.days{grid-template-columns:repeat(3,minmax(0,1fr))}.row2{flex-direction:column}}
</style></head><body>
<div class="top"><span class="logo" aria-hidden="true">EP</span><span class="brand">EvoSys Pro</span></div>
<div class="wrap"><div class="shell">
 <aside class="side">
  <div class="eyebrow">Business Growth</div>
  <h2>Business Growth Call</h2>
  <div class="host"><div class="av" aria-hidden="true">MS</div><b>Mike Simmons</b></div>
  <ul class="facts">
   <li><svg class="ic soft" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>Zoom video meeting</li>
   <li><svg class="ic soft" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>__MINS__ minutes</li>
   <li><svg class="ic soft" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r=".5"/></svg>No cost. No obligation.</li>
  </ul>
  <div class="talk"><h4>We'll talk about:</h4><ul class="chk">
   <li><svg class="ic gold" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>Your service area</li>
   <li><svg class="ic gold" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>Your ideal customers</li>
   <li><svg class="ic gold" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>How the pilot works</li></ul></div>
 </aside>
 <main class="main" id="app" aria-live="polite"><p class="empty">Loading open times&hellip;</p></main>
</div></div>
<script>
const T=__TOKEN__, U=__BASE__+'/'+T, COMPANY=__COMPANY__, MINS=__MINS__;
const app=document.getElementById('app');
const P={cal:'<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
 calplus:'<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18M12 14v6M9 17h6"/>',
 clock:'<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
 video:'<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/><rect x="2" y="6" width="14" height="12" rx="2"/>',
 resched:'<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
 check:'<path d="M20 6 9 17l-5-5"/>', chev:'<path d="m6 9 6 6 6-6"/>',
 brief:'<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>', pin:'<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
 load:'<path d="M21 12a9 9 0 1 1-6.219-8.56"/>', info:'<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>'};
const ic=(n,c)=>'<svg class="ic'+(c?' '+c:'')+'" aria-hidden="true" focusable="false" viewBox="0 0 24 24">'+P[n]+'</svg>';
const TZS=[['America/Chicago','Central Time (Chicago)'],['America/New_York','Eastern Time (New York)'],['America/Denver','Mountain Time (Denver)'],['America/Phoenix','Arizona Time (Phoenix)'],['America/Los_Angeles','Pacific Time (Los Angeles)']];
let tz=(()=>{try{const z=Intl.DateTimeFormat().resolvedOptions().timeZone;return TZS.some(x=>x[0]===z)?z:'America/Chicago'}catch(e){return 'America/Chicago'}})();
let slots=[], dayKey=null, sel=null, from=null, nextFrom=null, starts=[], resched=false, cur=null, busy=false, polls=0, pollT=null, last=null;
const esc=s=>String(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dk=d=>new Date(d).toLocaleDateString('en-CA',{timeZone:tz});
const fDay=(d,o)=>new Date(d).toLocaleDateString('en-US',Object.assign({timeZone:tz},o));
const fT=d=>new Date(d).toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZone:tz});
const zAbbr=d=>{try{return new Intl.DateTimeFormat('en-US',{timeZone:tz,timeZoneName:'short'}).formatToParts(new Date(d)).find(p=>p.type==='timeZoneName').value}catch(e){return ''}};
const intro=()=>'<div class="lead"><div class="eyebrow">Business Growth</div><h1>'+(resched?'Pick a new time.':'Let’s talk about your next stage of growth.')+'</h1></div>'+
 (COMPANY?'<p class="prep">Prepared for '+esc(COMPANY)+'</p>':'')+'<p class="sub lead">Explore your complimentary appointment-setting pilot.</p>'+
 '<details class="cover"><summary>What we’ll cover'+ic('chev','chev')+'</summary><ul><li>'+ic('check','gold')+'Your service area</li><li>'+ic('check','gold')+'Your ideal customers</li><li>'+ic('check','gold')+'How the pilot works</li></ul></details>';
function problem(){app.innerHTML=intro()+'<div class="note">'+ic('info','gold')+'<div><b>We can’t load open times right now.</b>Please try again in a few minutes, or just text us back and we’ll set a time.<div class="row"><button class="lnk" id="retry">Try again</button></div></div></div>';
 document.getElementById('retry').onclick=()=>load(from)}
async function load(start){
 const q=[]; if(start)q.push('from='+start); if(resched)q.push('reschedule=1');
 let j; try{const r=await fetch(U+'/slots'+(q.length?'?'+q.join('&'):'')); if(!r.ok)throw 0; j=await r.json()}catch(e){problem();return}
 if(j.booked&&!resched){done(j.booked);return}
 if(j.problem){problem();return}
 cur=j.current||null; slots=j.slots||[]; nextFrom=j.next_from; from=start||null;
 const days=[...new Set(slots.map(dk))]; if(!days.includes(dayKey))dayKey=days[0]||null; sel=null; render();
}
function render(){
 const days=[...new Set(slots.map(dk))];
 const month=slots.length?fDay(slots[0],{month:'long',year:'numeric'}):'';
 let h=intro();
 if(resched&&cur)h+='<div class="note">'+ic('cal','gold')+'<div><b>Your current call: '+fDay(cur.starts_at,{weekday:'short',month:'short',day:'numeric'})+' · '+fT(cur.starts_at)+' '+zAbbr(cur.starts_at)+'</b>It stays booked until you confirm a new time.<div class="row"><button class="lnk" id="keep">Keep my current time</button></div></div></div>';
 h+='<div class="pick"><section aria-label="Choose your day"><h3>Choose your day</h3>';
 h+='<div class="mnav"><button id="prev" '+(starts.length?'':'disabled')+' aria-label="Earlier week">&#8249;</button><span>'+month+'</span><button id="next" aria-label="Later week">&#8250;</button></div>';
 h+=days.length?'<div class="days">'+days.map(d=>{const x=slots.find(s=>dk(s)===d);return '<button class="day'+(d===dayKey?' on':'')+'" data-d="'+d+'" aria-pressed="'+(d===dayKey)+'" aria-label="'+fDay(x,{weekday:'long',month:'long',day:'numeric'})+'"><small>'+fDay(x,{weekday:'short'})+'</small><b>'+fDay(x,{day:'numeric'})+'</b></button>'}).join('')+'</div>':'<p class="empty">No open times this week. Tap &#8250; for the next week, or just text us back.</p>';
 h+='<div class="tzw">'+ic('pin')+'<select class="tz" id="tz" aria-label="Time zone">'+TZS.map(z=>'<option value="'+z[0]+'"'+(z[0]===tz?' selected':'')+'>'+z[1]+'</option>').join('')+'</select></div></section>';
 h+='<section aria-label="Choose your time">';
 if(dayKey){const ts=slots.filter(s=>dk(s)===dayKey);
  h+='<h3>'+fDay(ts[0],{weekday:'long',month:'long',day:'numeric'})+'</h3><div class="times">'+ts.map(s=>'<button class="t'+(s===sel?' on':'')+'" data-t="'+s+'" aria-pressed="'+(s===sel)+'">'+(s===sel?ic('check'):'')+fT(s)+'</button>').join('')+'</div>';}
 h+='<button class="go" id="go" '+(sel&&!busy?'':'disabled')+'>'+(busy?ic('load','spin')+(resched?'Moving your call…':'Booking your call…'):(resched?'Move my call':'Confirm my call')+' &rarr;')+'</button>';
 h+='<p class="sum">'+(sel?fDay(sel,{weekday:'short',month:'short',day:'numeric'})+' · '+fT(sel)+' '+zAbbr(sel)+' · '+MINS+' min':'Times shown in '+esc(TZS.find(z=>z[0]===tz)[1])+'.')+'</p><p class="msg" id="msg" role="alert"></p></section></div>';
 app.innerHTML=h;
 app.querySelectorAll('.day').forEach(b=>b.onclick=()=>{dayKey=b.dataset.d;sel=null;render()});
 app.querySelectorAll('.t').forEach(b=>b.onclick=()=>{if(busy)return;sel=b.dataset.t;render()});
 document.getElementById('tz').onchange=e=>{tz=e.target.value;const d=[...new Set(slots.map(dk))];dayKey=d[0]||null;sel=null;render()};
 document.getElementById('next').onclick=()=>{starts.push(from||'');load(nextFrom)};
 const p=document.getElementById('prev'); if(p)p.onclick=()=>{const s=starts.pop();load(s||null)};
 const k=document.getElementById('keep'); if(k)k.onclick=()=>{resched=false;starts=[];load(null)};
 document.getElementById('go').onclick=book;
}
async function book(){
 if(busy||!sel)return; busy=true; render();
 let j; try{const r=await fetch(U,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({starts_at:sel,reschedule:resched})}); j=await r.json()}catch(e){j={status:'error',message:'We couldn’t reach the booking system. Please try again.'}}
 busy=false;
 if(j.status==='booked'){const moved=resched;resched=false;done(j,moved,true);return}
 const keepDay=dayKey; render(); document.getElementById('msg').textContent=j.message||'Something went wrong. Please pick another time.';
 if(j.status==='taken')setTimeout(()=>{dayKey=keepDay;load(from).then(()=>{const m=document.getElementById('msg');if(m)m.textContent=j.message})},900);
}
function done(b,moved,fresh){
 last=b; clearTimeout(pollT);
 const s=b.starts_at, e=b.ends_at, ready=b.zoom_status==='ready'&&b.zoom_url;
 let h='<div class="ok"><div class="ring">'+ic('check')+'</div><div class="eyebrow">Business Growth</div>';
 h+='<span class="pill">'+ic('check')+(b.confirmed?'You confirmed · see you then':moved?'Booked · moved to your new time':'Booked')+'</span>';
 h+='<h1>You’re on Mike’s calendar.</h1>';
 if(!b.confirmed&&!fresh)h+='<button class="go" id="cf" style="margin:0 0 16px">'+ic('check')+'I’ll be there</button><p class="msg" id="cfm" role="alert"></p>';
 h+='<div class="det"><div class="m-hide">'+ic('brief')+'Business Growth Call</div><div class="m-first">'+ic('cal')+fDay(s,{weekday:'long',month:'long',day:'numeric'})+'</div><div>'+ic('clock')+fT(s)+'–'+fT(e)+' '+zAbbr(s)+'</div><div class="m-hide">'+ic('video')+'Zoom video meeting</div></div>';
 h+='<div class="zoomwrap">';
 if(ready)h+='<a class="go" href="'+esc(b.zoom_url)+'" target="_blank" rel="noopener noreferrer">'+ic('video')+'Join Zoom Meeting</a>';
 else if(b.zoom_status==='unavailable')h+='<div class="prepping" role="status">'+ic('info')+'Your call is booked. We’ll send your Zoom link before the call.</div>';
 else h+='<div class="prepping" role="status">'+ic('load','spin')+'Your Zoom link is being prepared…</div>';
 h+='</div><div class="row2"><a class="b2 dark" href="'+U+'/event.ics" download="business-growth-call.ics">'+ic('calplus')+'Add to calendar</a><button class="b2 light" id="rs">'+ic('resched')+'Reschedule</button></div>';
 h+=ready?'<p class="foot">Zoom link included in your calendar event.</p>':'';
 if(COMPANY)h+='<p class="foot">Prepared for '+esc(COMPANY)+'</p>';
 h+='<p class="foot d-only"><i>No cost. No obligation.</i></p></div>';
 app.innerHTML=h;
 document.getElementById('rs').onclick=()=>{resched=true;starts=[];clearTimeout(pollT);load(null)};
 const cf=document.getElementById('cf'); if(cf)cf.onclick=async()=>{cf.disabled=true;cf.innerHTML=ic('load','spin')+'Confirming…';let j;try{const r=await fetch(U+'/confirm',{method:'POST'});j=await r.json()}catch(e){j=null}
  if(j&&j.status==='booked'){done(j,false)}else{cf.disabled=false;cf.innerHTML=ic('check')+'I’ll be there';document.getElementById('cfm').textContent='We couldn’t save that. Please try again or text us back.'}};
 if(!ready&&b.zoom_status!=='unavailable'&&polls<16){polls++;pollT=setTimeout(async()=>{try{const r=await fetch(U+'/slots');const j=await r.json();if(j.booked&&!resched)done(j.booked,moved,fresh)}catch(e){}},15000)}
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
        body = reminder_body(db, opp, a, kind)
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


def reminder_body(db: Session, opp: Opportunity, a, kind: str) -> str:
    """The reminder text. Every reminder carries the business's own link, where
    one tap confirms they'll be there or picks a new time (owner, 2026-11-24)."""
    t = _local(a.starts_at).strftime("%-I:%M %p")
    z = zoom_link(db, a.id)
    link = short_url_for(opp)
    if is_spanish(db, opp):
        when = {"reminder_day": "mañana a las %s (hora del centro)" % t,
                "reminder_1h": "en una hora, a las %s (hora del centro)" % t}.get(kind, _ct(a.starts_at, True))
        return ("Recordatorio: su llamada con Mike de EvoSys Pro sobre sus 3 citas gratis es %s. %s"
                "Confirme o cambie el horario aquí: %s" % (when, ("Por Zoom: %s " % z) if z else "", link))
    when = {"reminder_day": "tomorrow at %s Central" % t,
            "reminder_1h": "in about an hour, at %s Central" % t}.get(kind, _ct(a.starts_at))
    return ("Reminder: your call with Mike from EvoSys Pro about your 3 free appointments is %s. %s"
            "Tap to confirm or pick a new time: %s" % (when, ("Join on Zoom: %s " % z) if z else "", link))


REMINDER_LABELS = {"rd": "day_before", "r1": "hour_before", "rm": "manual"}


def reminder_status(db: Session, a) -> dict:
    """What reminders went to this business for this call's CURRENT time
    (a moved call starts fresh), for the owner's Appointments screen."""
    from app.models.evosys_sales_models import EvoSysOutreachMessage as M
    out = {"day_before": None, "hour_before": None, "manual": None, "failed": []}
    if not a.opportunity_id:
        return out
    stamp = a.starts_at.strftime("%Y%m%d%H%M")
    rows = (db.query(M).filter(M.opportunity_id == a.opportunity_id,
                               M.label.in_([k + ":" + stamp for k in REMINDER_LABELS]))
            .order_by(M.created_at).all())
    for m in rows:
        key = REMINDER_LABELS[m.label.split(":", 1)[0]]
        if m.status == "sent":
            out[key] = m.created_at.isoformat() + "Z"
        else:
            out["failed"].append({"which": key, "error": (m.error or "")[:200]})
    return out


def send_reminder_now(db: Session, a) -> dict:
    """Owner taps "Send reminder now": the same reminder text, with the
    confirm / new-time link, sent right away from the Sales number."""
    from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales import outreach as O
    opp = db.get(Opportunity, a.opportunity_id) if a.opportunity_id else None
    if opp is None:
        return {"ok": False, "error": "This call isn't tied to a lead, so there is no number to text."}
    to = O.e164(opp.phone)
    if not to:
        return {"ok": False, "error": "No mobile number on this lead."}
    st = db.get(EvoSysOutreachState, opp.id)
    if (st is not None and st.opted_out_at) or SDNC.for_opportunity(db, opp) \
            or SDNC.find(db, opp.brand_sales_org_id, opp.phone, opp.email):
        return {"ok": False, "error": "This business opted out or is on Do Not Contact."}
    s = O.get_settings(db, opp.brand_sales_org_id)
    if not O.e164(s.sms_from_number):
        return {"ok": False, "error": "The Sales texting number isn't set."}
    left = a.starts_at - datetime.utcnow()
    kind = "reminder_1h" if left <= timedelta(minutes=75) else \
        "reminder_day" if _local(a.starts_at).date() == (_local(datetime.utcnow()) + timedelta(days=1)).date() else "manual"
    body = reminder_body(db, opp, a, kind)
    try:
        r = O._send_text(to, body, s)
    except Exception as e:                                   # noqa: BLE001
        r = {"success": False, "error": str(e)}
    ok = bool(r.get("success"))
    db.add(EvoSysOutreachMessage(brand_sales_org_id=opp.brand_sales_org_id, opportunity_id=opp.id,
                                 direction="out", channel="text", to_address=to, body=body,
                                 label="rm:" + a.starts_at.strftime("%Y%m%d%H%M"),
                                 status="sent" if ok else "failed",
                                 error=None if ok else str(r.get("error") or "")[:500],
                                 provider_message_id=r.get("provider_message_id"), created_at=datetime.utcnow()))
    db.commit()
    return {"ok": ok, "error": None if ok else (r.get("error") or "The text didn't go through.")}


def confirm(db: Session, opp: Opportunity) -> dict:
    """The business taps "I'll be there" on its own page. Recorded on the
    call (source: their link) and the owner gets the same text + email alert
    as a booking."""
    from app.services.evosys_sales import outreach as O
    from app.services.evosys_sales import sms_inbound as SI
    a = current(db, opp)
    if a is None:
        return {"status": "none", "message": "We couldn't find an upcoming call. Pick a time below."}
    if a.confirmation_status != "confirmed":
        a.confirmation_status = "confirmed"
        a.confirmation_source = "prospect_link"
        a.confirmed_at = datetime.utcnow()
        a.confirmed_by = None
        db.add(OpportunityEvent(opportunity_id=opp.id, event_type="intro_call_confirmed",
                                summary="Confirmed they'll be on their intro call",
                                detail=a.starts_at.isoformat() + "Z", occurred_at=datetime.utcnow()))
        db.commit()
        who = opp.company_name or opp.contact_name or "A prospect"
        SI.notify_owner(O.get_settings(db, opp.brand_sales_org_id),
                        "EvoSys Sales - CONFIRMED: %s confirmed their call for %s" % (who, _ct(a.starts_at)),
                        "EvoSys Sales - %s confirmed their call" % who,
                        "<p><b>%s</b> confirmed they'll be on their call <b>%s</b>.</p>"
                        % (__import__("html").escape(who), _ct(a.starts_at)))
    return booked_out(db, a)
