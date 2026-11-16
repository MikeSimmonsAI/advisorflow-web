"""EvoSys Sales program API (owner request 2026-11-16).

/sales/evosys/*        the EvoSys Pro sales team only (managers see every lead,
                       reps their own); outreach settings, approval and the
                       switch are managers only.
/evosys-sales-public/* the opt-out link in every email. No login; the token
                       names one lead and cannot be forged.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
from app.models.models import User
from app.models.sales_models import STAGE_LABELS, Opportunity, OpportunityEvent
from app.services import sales_access
from app.services.evosys_sales import outreach as O

router = APIRouter(prefix="/sales/evosys", tags=["evosys-sales"])
public_router = APIRouter(prefix="/evosys-sales-public", tags=["evosys-sales-public"])

STOP_LABELS = {"replied": "Replied", "booked": "Booked a call", "opted_out": "Opted out",
               "no_email": "No email address", "stage": "Moved on in the pipeline",
               "manual": "Stopped by you", "email_failed": "Email bounced"}


class Ctx:
    def __init__(self, user, bso, manager):
        self.user, self.bso, self.manager = user, bso, manager


def ctx(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Ctx:
    bso = O.brand_id(db)
    if not bso or not sales_access.is_sales_member(user, db, bso):
        raise HTTPException(403, "EvoSys Sales is for the EvoSys Pro sales team.")
    return Ctx(user, bso, sales_access.is_sales_manager(user, db, bso))


def _manager(c: Ctx):
    if not c.manager:
        raise HTTPException(403, "Only an EvoSys sales manager can change outreach.")


def _leads_q(db: Session, c: Ctx):
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == c.bso)
    if not c.manager:
        q = q.filter(Opportunity.owner_user_id == c.user.id)
    return q


def _opp(db: Session, c: Ctx, opp_id: str) -> Opportunity:
    o = _leads_q(db, c).filter(Opportunity.id == opp_id).first()
    if o is None:
        raise HTTPException(404, "Lead not found.")
    return o


def _status(st: Optional[EvoSysOutreachState], opp: Opportunity) -> dict:
    if st is None:
        if opp.stage not in O.WORKING_STAGES:
            return {"key": "not_in", "label": "Not in follow-up"}
        if not O._has_email(opp):
            return {"key": "no_email", "label": "No email - call or text"}
        return {"key": "waiting", "label": "Starts when outreach is on"}
    if st.status == "active":
        return {"key": "active", "label": "In follow-up - step %d next" % (st.next_step + 1),
                "next_due_at": st.next_due_at.isoformat() + "Z" if st.next_due_at else None}
    if st.status == "finished":
        return {"key": "finished", "label": "All steps sent"}
    return {"key": st.stop_reason or "stopped", "label": STOP_LABELS.get(st.stop_reason, "Stopped")}


def _row(o: Opportunity, st, last=None, booked=False) -> dict:
    return {"id": o.id, "company": o.company_name, "contact": o.contact_name, "phone": o.phone,
            "email": o.email, "website": o.website, "stage": o.stage,
            "stage_label": STAGE_LABELS.get(o.stage, o.stage), "source": o.source,
            "created_at": o.created_at.isoformat() + "Z" if o.created_at else None,
            "outreach": _status(st, o), "booked": booked,
            "last_message": last}


def _msg(m: EvoSysOutreachMessage) -> dict:
    return {"id": m.id, "direction": m.direction, "channel": m.channel, "step": m.step_no,
            "to": m.to_address, "subject": m.subject, "body": m.body, "status": m.status,
            "error": m.error, "at": m.created_at.isoformat() + "Z"}


def _booked_ids(db: Session, bso: str) -> set:
    from app.models.scheduling_models import SalesAppointment
    return {r[0] for r in db.query(SalesAppointment.opportunity_id)
            .filter(SalesAppointment.brand_sales_org_id == bso,
                    SalesAppointment.opportunity_id.isnot(None)).all()}


def _settings_out(s, c: Ctx) -> dict:
    return {"steps": s.steps or [], "offer": s.offer, "booking_url": s.booking_url,
            "sender_name": s.sender_name, "from_email": s.from_email,
            "reply_to_email": s.reply_to_email, "mailing_address": s.mailing_address,
            "daily_limit": s.daily_limit, "sending_on": bool(s.sending_on),
            "approved": s.approved_at is not None,
            "approved_at": s.approved_at.isoformat() + "Z" if s.approved_at else None,
            "problems": O.settings_problems(s), "can_manage": c.manager,
            "texting_ready": False,
            "sending_hours": "Weekdays 8:00 AM - 6:00 PM Central"}


# ── overview ──────────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = O.get_settings(db, c.bso)
    db.commit()
    leads = _leads_q(db, c).all()
    ids = [o.id for o in leads]
    states = {x.opportunity_id: x for x in db.query(EvoSysOutreachState)
              .filter(EvoSysOutreachState.opportunity_id.in_(ids)).all()} if ids else {}
    booked = _booked_ids(db, c.bso)
    now = datetime.utcnow()
    week = now - timedelta(days=7)
    replies = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id.in_(ids),
                                                     EvoSysOutreachMessage.direction == "in",
                                                     EvoSysOutreachMessage.created_at >= week)
               .order_by(EvoSysOutreachMessage.created_at.desc()).limit(20).all()) if ids else []
    by_id = {o.id: o for o in leads}
    working = [o for o in leads if o.stage in O.WORKING_STAGES]
    to_call = [o for o in working if not O._has_email(o) and o.id not in booked
               and not (states.get(o.id) and states[o.id].status == "stopped")]
    sent_today = O.sent_today(db, c.bso, now)
    from app.models.scheduling_models import SalesAppointment
    upcoming = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                                  SalesAppointment.starts_at >= now - timedelta(hours=2))
                .order_by(SalesAppointment.starts_at).limit(10).all())
    return {
        "counts": {"leads": len(leads), "working": len(working),
                   "in_followup": sum(1 for x in states.values() if x.status == "active"),
                   "with_email": sum(1 for o in working if O._has_email(o)),
                   "replied": sum(1 for x in states.values() if x.replied_at),
                   "booked": sum(1 for o in leads if o.id in booked),
                   "sent_today": sent_today, "to_call": len(to_call)},
        "replies": [dict(_msg(m), lead=_row(by_id[m.opportunity_id], states.get(m.opportunity_id)))
                    for m in replies if m.opportunity_id in by_id],
        "to_call": [_row(o, states.get(o.id)) for o in to_call[:15]],
        "appointments": [_appt(a, by_id) for a in upcoming],
        "outreach": {"sending_on": bool(s.sending_on), "approved": s.approved_at is not None,
                     "problems": O.settings_problems(s),
                     "in_window": O.in_send_window(now)},
        "can_manage": c.manager,
    }


def _appt(a, by_id) -> dict:
    o = by_id.get(a.opportunity_id)
    return {"id": a.id, "title": a.title, "status": a.status,
            "starts_at": a.starts_at.isoformat() + "Z", "ends_at": a.ends_at.isoformat() + "Z",
            "timezone": a.timezone, "lead_id": a.opportunity_id,
            "company": o.company_name if o else None, "contact": o.contact_name if o else None}


# ── leads ─────────────────────────────────────────────────────────────────────

@router.get("/leads")
def leads(view: str = "all", q: str = "", c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    rows = _leads_q(db, c).order_by(Opportunity.created_at.desc()).all()
    ids = [o.id for o in rows]
    states = {x.opportunity_id: x for x in db.query(EvoSysOutreachState)
              .filter(EvoSysOutreachState.opportunity_id.in_(ids)).all()} if ids else {}
    booked = _booked_ids(db, c.bso)
    needle = q.strip().lower()
    out = []
    for o in rows:
        r = _row(o, states.get(o.id), booked=o.id in booked)
        k = r["outreach"]["key"]
        if view == "followup" and k != "active":
            continue
        if view == "no_email" and k != "no_email":
            continue
        if view == "replied" and k != "replied":
            continue
        if view == "booked" and not r["booked"]:
            continue
        if view == "stopped" and k in ("active", "waiting", "no_email", "not_in"):
            continue
        if needle and needle not in " ".join(str(x or "") for x in (o.company_name, o.contact_name, o.phone, o.email)).lower():
            continue
        out.append(r)
    return {"leads": out, "total": len(rows)}


@router.get("/leads/{opp_id}")
def lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    msgs = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == o.id)
            .order_by(EvoSysOutreachMessage.created_at).all())
    events = (db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id)
              .order_by(OpportunityEvent.occurred_at.desc()).limit(30).all())
    s = O.get_settings(db, c.bso)
    nxt = None
    if st and st.status == "active" and st.next_step < len(s.steps or []):
        step = (s.steps or [])[st.next_step]
        nxt = {"step": st.next_step + 1, "channel": step.get("channel"),
               "subject": O.fill(step.get("subject"), o, s), "body": O.fill(step.get("body"), o, s),
               "due_at": st.next_due_at.isoformat() + "Z" if st.next_due_at else None}
    db.commit()
    return {"lead": _row(o, st, booked=o.id in _booked_ids(db, c.bso)),
            "messages": [_msg(m) for m in msgs], "next": nxt,
            "timeline": [{"summary": e.summary, "detail": e.detail, "type": e.event_type,
                          "at": e.occurred_at.isoformat() + "Z"} for e in events]}


class Note(BaseModel):
    note: str = ""
    channel: str = "email"


@router.post("/leads/{opp_id}/reply")
def log_reply(opp_id: str, body: Note, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    O.log_reply(db, o, c.user.id, body.note, body.channel)
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/call")
def log_call(opp_id: str, body: Note, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    now = datetime.utcnow()
    db.add(EvoSysOutreachMessage(brand_sales_org_id=o.brand_sales_org_id, opportunity_id=o.id, direction="out",
                                 channel="call", to_address=o.phone, body=(body.note or "Called").strip()[:5000],
                                 status="logged", actor_user_id=c.user.id, created_at=now))
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="call_logged", summary="Call logged",
                            detail=(body.note or "")[:2000], actor_user_id=c.user.id, occurred_at=now))
    if o.stage == "prospect":
        o.stage, o.stage_changed_at = "contacted", now
    o.contacted_at = o.contacted_at or now
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/stop")
def stop_lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    O.stop(db, o.id, "manual")
    db.commit()
    return {"ok": True}


@router.post("/leads/{opp_id}/restart")
def restart_lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    if O.restart(db, o.id) is None:
        raise HTTPException(409, "This lead opted out or has not started follow-up yet.")
    db.commit()
    return {"ok": True}


# ── conversations, appointments, results ──────────────────────────────────────

@router.get("/conversations")
def conversations(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    leads_ = {o.id: o for o in _leads_q(db, c).all()}
    if not leads_:
        return {"conversations": []}
    last = (db.query(EvoSysOutreachMessage.opportunity_id, func.max(EvoSysOutreachMessage.created_at),
                     func.count(EvoSysOutreachMessage.id))
            .filter(EvoSysOutreachMessage.opportunity_id.in_(list(leads_)))
            .group_by(EvoSysOutreachMessage.opportunity_id).all())
    states = {x.opportunity_id: x for x in db.query(EvoSysOutreachState)
              .filter(EvoSysOutreachState.opportunity_id.in_(list(leads_))).all()}
    out = []
    for opp_id, at, n in sorted(last, key=lambda r: r[1], reverse=True):
        m = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == opp_id,
                                                    EvoSysOutreachMessage.created_at == at).first())
        r = _row(leads_[opp_id], states.get(opp_id), last=_msg(m) if m else None)
        r["message_count"] = n
        out.append(r)
    return {"conversations": out}


@router.get("/appointments")
def appointments(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.scheduling_models import SalesAppointment
    by_id = {o.id: o for o in _leads_q(db, c).all()}
    now = datetime.utcnow()
    rows = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == c.bso,
                                              SalesAppointment.starts_at >= now - timedelta(days=30))
            .order_by(SalesAppointment.starts_at).all())
    if not c.manager:
        rows = [a for a in rows if a.opportunity_id in by_id]
    up = [_appt(a, by_id) for a in rows if a.starts_at >= now - timedelta(hours=2)]
    past = [_appt(a, by_id) for a in rows if a.starts_at < now - timedelta(hours=2)][::-1]
    return {"upcoming": up, "recent": past}


@router.get("/results")
def results(days: int = 14, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    days = max(1, min(days, 90))
    ids = [o.id for o in _leads_q(db, c).all()]
    since = datetime.utcnow() - timedelta(days=days)
    base = db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id.in_(ids),
                                                  EvoSysOutreachMessage.created_at >= since) if ids else None
    msgs = base.all() if base is not None else []
    states = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.opportunity_id.in_(ids)).all() if ids else []
    from zoneinfo import ZoneInfo
    per_day = {}
    for m in msgs:
        d = m.created_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(O.CENTRAL).date().isoformat()
        e = per_day.setdefault(d, {"date": d, "sent": 0, "replies": 0, "calls": 0, "failed": 0})
        if m.direction == "out" and m.channel == "email" and m.status == "sent":
            e["sent"] += 1
        elif m.direction == "in":
            e["replies"] += 1
        elif m.channel == "call":
            e["calls"] += 1
        elif m.status == "failed":
            e["failed"] += 1
    sent = sum(1 for m in msgs if m.direction == "out" and m.status == "sent")
    replied = sum(1 for s in states if s.replied_at)
    booked = len(_booked_ids(db, c.bso) & set(ids))
    return {"days": days,
            "totals": {"emails_sent": sent, "replied": replied, "booked": booked,
                       "opted_out": sum(1 for s in states if s.opted_out_at),
                       "bounced": sum(1 for s in states if s.stop_reason == "email_failed"),
                       "calls_logged": sum(1 for m in msgs if m.channel == "call"),
                       "in_followup": sum(1 for s in states if s.status == "active"),
                       "reply_rate": round(100.0 * replied / max(1, len(states)), 1)},
            "by_day": sorted(per_day.values(), key=lambda e: e["date"])}


# ── outreach settings ─────────────────────────────────────────────────────────

class SettingsIn(BaseModel):
    steps: Optional[list] = None
    offer: Optional[str] = None
    booking_url: Optional[str] = None
    sender_name: Optional[str] = None
    from_email: Optional[str] = None
    reply_to_email: Optional[str] = None
    mailing_address: Optional[str] = None
    daily_limit: Optional[int] = None


@router.get("/outreach")
def get_outreach(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = O.get_settings(db, c.bso)
    db.commit()
    return _settings_out(s, c)


@router.put("/outreach")
def put_outreach(body: SettingsIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = O.get_settings(db, c.bso)
    O.save_settings(db, s, body.model_dump(exclude_none=True), c.user.id)
    db.commit()
    return _settings_out(s, c)


@router.post("/outreach/approve")
def approve(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = O.get_settings(db, c.bso)
    p = O.settings_problems(s)
    if p:
        raise HTTPException(400, " ".join(p))
    s.approved_at, s.approved_by = datetime.utcnow(), c.user.id
    db.commit()
    return _settings_out(s, c)


class Switch(BaseModel):
    on: bool


@router.post("/outreach/switch")
def switch(body: Switch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    s = O.get_settings(db, c.bso)
    if body.on and (s.approved_at is None or O.settings_problems(s)):
        raise HTTPException(400, "Approve the messages before turning outreach on.")
    s.sending_on = bool(body.on)
    s.updated_at, s.updated_by = datetime.utcnow(), c.user.id
    db.commit()
    return _settings_out(s, c)


@router.get("/outreach/preview")
def preview(lead_id: Optional[str] = None, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = O.get_settings(db, c.bso)
    o = _opp(db, c, lead_id) if lead_id else (
        _leads_q(db, c).filter(Opportunity.email.isnot(None), Opportunity.email != "")
        .order_by(Opportunity.created_at.desc()).first())
    if o is None:
        return {"lead": None, "steps": []}
    db.commit()
    return {"lead": {"id": o.id, "company": o.company_name, "contact": o.contact_name, "email": o.email},
            "steps": [dict(O.render_email(o, s, st), day=st.get("day"), channel=st.get("channel"))
                      for st in (s.steps or [])]}


class TestSend(BaseModel):
    to: str
    step: int = 1


@router.post("/outreach/test")
def test_send(body: TestSend, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Sends one step, filled in with a real lead's details, to an address the
    manager types (their own). Never to the lead."""
    _manager(c)
    s = O.get_settings(db, c.bso)
    steps = s.steps or []
    if not (1 <= body.step <= len(steps)) or steps[body.step - 1].get("channel") != "email":
        raise HTTPException(400, "Pick an email step.")
    if "@" not in (body.to or ""):
        raise HTTPException(400, "Enter your email address.")
    o = (_leads_q(db, c).filter(Opportunity.email.isnot(None), Opportunity.email != "")
         .order_by(Opportunity.created_at.desc()).first())
    if o is None:
        raise HTTPException(400, "No lead with an email to fill the test in with.")
    msg = O.render_email(o, s, steps[body.step - 1])
    msg = dict(msg, subject="[TEST] " + msg["subject"])
    r = O._send(body.to.strip(), msg, s, o.id)
    if not r.get("success"):
        raise HTTPException(502, "The test email did not send: %s" % (r.get("error") or "unknown error"))
    return {"ok": True, "sent_to": body.to.strip()}


# ── public opt-out ────────────────────────────────────────────────────────────

_PAGE = ('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">'
         '<title>EvoSys Pro</title><body style="font-family:system-ui,sans-serif;max-width:520px;'
         'margin:15vh auto;padding:0 20px;color:#16233a"><h2>%s</h2><p>%s</p></body>')


def _opt_out(token: str, db: Session) -> bool:
    opp_id = O.read_opt_out(token)
    if not opp_id or db.get(Opportunity, opp_id) is None:
        return False
    O.stop(db, opp_id, "opted_out")
    db.add(OpportunityEvent(opportunity_id=opp_id, event_type="outreach_opt_out",
                            summary="Opted out of EvoSys emails", occurred_at=datetime.utcnow()))
    db.commit()
    return True


@public_router.get("/stop/{token}", response_class=HTMLResponse)
def opt_out_page(token: str, db: Session = Depends(get_db)):
    if not _opt_out(token, db):
        return HTMLResponse(_PAGE % ("Link not recognized", "This link is not valid."), status_code=404)
    return HTMLResponse(_PAGE % ("You're unsubscribed", "You won't get any more emails from EvoSys Pro."))


@public_router.post("/stop/{token}")
def opt_out_one_click(token: str, db: Session = Depends(get_db)):
    if not _opt_out(token, db):
        raise HTTPException(404, "Link not recognized.")
    return {"ok": True}
