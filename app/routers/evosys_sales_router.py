"""EvoSys Sales program API (owner request 2026-11-16).

/sales/evosys/*        the EvoSys Pro sales team only (managers see every lead,
                       reps their own); outreach settings, approval and the
                       switch are managers only.
/evosys-sales-public/* the opt-out link in every email. No login; the token
                       names one lead and cannot be forged.
"""
from __future__ import annotations

import re
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
from app.services.evosys_sales import logos as _logos

router = APIRouter(prefix="/sales/evosys", tags=["evosys-sales"])
public_router = APIRouter(prefix="/evosys-sales-public", tags=["evosys-sales-public"])

STOP_LABELS = {"replied": "Replied", "booked": "Booked a call", "opted_out": "Opted out - Do Not Contact",
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


def _on_dnc(db: Session, o: Opportunity) -> bool:
    from app.services import sales_suppression as SDNC
    return bool(SDNC.for_opportunity(db, o) or SDNC.find(db, o.brand_sales_org_id, o.phone, o.email))


def _row(o: Opportunity, st, last=None, booked=False) -> dict:
    return {"id": o.id, "company": o.company_name, "contact": o.contact_name, "phone": o.phone,
            "email": o.email, "website": o.website, "stage": o.stage,
            "stage_label": STAGE_LABELS.get(o.stage, o.stage), "source": o.source,
            "created_at": o.created_at.isoformat() + "Z" if o.created_at else None,
            "outreach": _status(st, o), "booked": booked,
            "last_message": last,
            "logo_url": _logos.view_url(o), "logo_quality": o.logo_quality,
            "logo_in_email": _logos.usable(o)}


def _msg(m: EvoSysOutreachMessage) -> dict:
    return {"id": m.id, "direction": m.direction, "channel": m.channel, "step": m.step_no,
            "to": m.to_address, "subject": m.subject, "body": m.body, "status": m.status,
            "error": m.error, "how": m.match_method, "label": m.label, "campaign_id": m.campaign_id,
            "translation": getattr(m, "translation", None),
            "at": m.created_at.isoformat() + "Z"}


def _booked_ids(db: Session, bso: str) -> set:
    from app.models.scheduling_models import SalesAppointment
    return {r[0] for r in db.query(SalesAppointment.opportunity_id)
            .filter(SalesAppointment.brand_sales_org_id == bso,
                    SalesAppointment.opportunity_id.isnot(None)).all()}


def _settings_out(s, c: Ctx, db: Session) -> dict:
    return {"steps": s.steps or [], "offer": s.offer, "booking_url": s.booking_url,
            "sender_name": s.sender_name, "from_email": s.from_email,
            "reply_to_email": s.reply_to_email, "mailing_address": s.mailing_address,
            "daily_limit": s.daily_limit, "sending_on": bool(s.sending_on),
            "approved": s.approved_at is not None,
            "approved_at": s.approved_at.isoformat() + "Z" if s.approved_at else None,
            "problems": O.settings_problems(s), "can_manage": c.manager,
            "sms_from_number": s.sms_from_number,
            "alert_phone": s.alert_phone, "alert_email": s.alert_email,
            "text_auto_reply": bool(s.text_auto_reply),
            "alert_also": s.alert_also or [], "reply_examples": s.reply_examples or [],
            "texting_ready": bool(O.e164(s.sms_from_number)),
            "reply_tracking": _reply_tracking(db, s),
            "sending_hours": "Weekdays 8:00 AM - 6:00 PM Central"}


def _reply_tracking(db: Session, s) -> dict:
    """Where replies are read, in plain words. A reply address on the same
    domain as a connected mailbox is delivered into it (info@ and plus-tagged
    addresses arrive in support@evosyspro.live: checked 2026-11-16 with real
    mail), and the mailbox poller reads it every 5 minutes."""
    from app.models.inbound_mailbox_models import InboundMailbox
    addr = (s.reply_to_email or "").strip().lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    boxes = [b for b in db.query(InboundMailbox).filter(InboundMailbox.is_active.is_(True),
                                                        InboundMailbox.purpose.is_(None)).all()]
    box = next((b for b in boxes if b.address.lower() == addr), None) or \
        next((b for b in boxes if domain and b.address.lower().endswith("@" + domain)), None)
    last = (db.query(func.max(EvoSysOutreachMessage.created_at))
            .filter(EvoSysOutreachMessage.direction == "in",
                    EvoSysOutreachMessage.match_method.in_(("reply_address", "sender"))).scalar())
    if box is None:
        return {"automatic": False, "mailbox": None, "last_auto_reply_at": None,
                "text": ("Replies to %s are not read automatically: no connected mailbox receives "
                         "that domain. Use \"They replied\" until one is connected." % (addr or "-"))}
    ok = box.last_status == "ok"
    return {"automatic": ok, "mailbox": box.address, "last_polled_at": iso(box.last_polled_at),
            "last_auto_reply_at": iso(last),
            "text": ("Replies to %s arrive in %s, which is read every 5 minutes. A reply stops that "
                     "lead's emails by itself; out-of-office replies are ignored." % (addr, box.address))
                    if ok else ("The %s mailbox needs attention (%s), so replies are not being read right now."
                                % (box.address, box.last_status or "unknown"))}


def iso(d):
    return d.isoformat() + "Z" if d else None


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
    from app.services.evosys_sales import inbox as IB
    opted = {oid for oid, x in states.items() if x.opted_out_at}
    groups = IB.group(db, ids, opted, week)
    replies = [m for m, _ in groups["needs_you"]]
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
                   "sent_today": sent_today, "to_call": len(to_call),
                   "needs_you": len(groups["needs_you"]), "handled": len(groups["handled"]),
                   "opted_out": len(groups["opted_out"]),
                   "upcoming_calls": sum(1 for a in upcoming if a.status == "scheduled" and a.starts_at >= now)},
        # one row per business; STOPs and already-answered texts never land here
        "replies": [dict(_msg(m), lead=_row(by_id[m.opportunity_id], states.get(m.opportunity_id)))
                    for m in replies if m.opportunity_id in by_id],
        "handled": [dict(_msg(m), lead=_row(by_id[m.opportunity_id], states.get(m.opportunity_id)),
                         answer=dict(_msg(a), sent_by=IB.who_sent(a)))
                    for m, a in groups["handled"][:30] if m.opportunity_id in by_id],
        "opted_out": [dict(_msg(m), lead=_row(by_id[m.opportunity_id], states.get(m.opportunity_id)))
                      for m, _ in groups["opted_out"][:30] if m.opportunity_id in by_id],
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
def leads(view: str = "all", q: str = "", group: str = "", c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    rows = _leads_q(db, c).order_by(Opportunity.created_at.desc()).all()
    ids = [o.id for o in rows]
    states = {x.opportunity_id: x for x in db.query(EvoSysOutreachState)
              .filter(EvoSysOutreachState.opportunity_id.in_(ids)).all()} if ids else {}
    booked = _booked_ids(db, c.bso)
    from app.models.evosys_sales_models import EvoSysLead
    from app.services.evosys_sales import grouping as G
    s = O.get_settings(db, c.bso)
    G.backfill(db, s, c.bso)
    leads_ = {l.opportunity_id: l for l in db.query(EvoSysLead).filter(EvoSysLead.opportunity_id.in_(ids)).all()} if ids else {}
    needle = q.strip().lower()
    out = []
    for o in rows:
        r = _row(o, states.get(o.id), booked=o.id in booked)
        l = leads_.get(o.id)
        r.update({"industry": G.label_of(G.industries(s), l.industry_key) if l else None,
                  "market": G.label_of(G.markets(s), l.market_key) if l else None,
                  "industry_key": l.industry_key if l else None, "market_key": l.market_key if l else None,
                  "city": l.city if l else None, "state": l.state if l else None,
                  "review_status": l.review_status if l else None,
                  "review_label": G.REVIEW_LABEL.get(l.review_status) if l else None,
                  "source_label": G.SOURCE_LABEL.get(l.source_channel or "", "Other") if l else None,
                  "source_record_id": o.source_record_id})
        k = r["outreach"]["key"]
        rv = r["review_status"]
        if view == "needs_review" and rv not in ("unclear_market", "possible_duplicate"):
            continue
        if view in ("held", "excluded", "possible_duplicate", "unclear_market") and rv != view:
            continue
        if group and "%s|%s" % (r["industry_key"] or "", r["market_key"] or "") != group:
            continue
        if view == "followup" and k != "active":
            continue
        if view == "no_email" and k != "no_email":
            continue
        if view == "replied" and k != "replied":
            continue
        if view == "booked" and not r["booked"]:
            continue
        if view in ("dnc", "stopped") and not (k == "opted_out" or _on_dnc(db, o)):
            # Do Not Contact: only businesses that opted out (STOP, the email
            # link) or are on the Do Not Contact list - NOT every lead whose
            # automatic follow-up stopped because they replied or booked.
            continue
        if needle and needle not in " ".join(str(x or "") for x in (o.company_name, o.contact_name, o.phone, o.email)).lower():
            continue
        out.append(r)
    db.commit()
    return {"leads": out, "total": len(rows)}


@router.get("/leads/{opp_id}")
def lead(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    msgs = (db.query(EvoSysOutreachMessage).filter(EvoSysOutreachMessage.opportunity_id == o.id)
            .order_by(EvoSysOutreachMessage.created_at).all())
    try:
        from app.services.evosys_sales import ai as _AIT
        _AIT.fill_translations(db, msgs, c.bso, c.user.id)
        db.commit()
    except Exception:                                         # noqa: BLE001 - English is a convenience
        db.rollback()
    events = (db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id)
              .order_by(OpportunityEvent.occurred_at.desc()).limit(30).all())
    from app.models.evosys_sales_models import EvoSysCampaign
    from app.services.evosys_sales import campaigns as CA
    s = O.get_settings(db, c.bso)
    camp = db.get(EvoSysCampaign, st.campaign_id) if st and st.campaign_id else None
    nxt = None
    if st and camp and st.status == "active" and st.next_step < len(camp.steps or []):
        step = (camp.steps or [])[st.next_step]
        nxt = {"step": st.next_step + 1, "channel": step.get("channel"), "campaign": camp.name,
               "paused": not CA.can_send(camp, s),
               "subject": O.fill(step.get("subject"), o, s, camp), "body": O.fill(step.get("body"), o, s, camp),
               "due_at": st.next_due_at.isoformat() + "Z" if st.next_due_at else None}
    from app.services.evosys_sales import ai as _AI
    last_in = next((m for m in reversed(msgs) if m.direction == "in"), None)
    reply_ai = (_AI.payload(_AI.latest(db, "text_reply" if last_in.channel == "text" else "reply_assist", last_in.id))
                if last_in else None)
    db.commit()
    return {"lead": _row(o, st, booked=o.id in _booked_ids(db, c.bso)), "reply_ai": reply_ai,
            "messages": [_msg(m) for m in msgs], "next": nxt,
            "timeline": [{"summary": e.summary, "detail": e.detail, "type": e.event_type,
                          "at": e.occurred_at.isoformat() + "Z"} for e in events]}


class LogoIn(BaseModel):
    image_base64: str
    quality: str = "clean"


@router.put("/leads/{opp_id}/logo")
def set_logo(opp_id: str, body: LogoIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Save the business's logo on the lead. Managers only. Sends nothing."""
    _manager(c)
    o = _opp(db, c, opp_id)
    try:
        png = _logos.normalize(_logos.decode_upload(body.image_base64))
        _logos.store(o, png, body.quality, datetime.utcnow())
    except _logos.LogoError as e:
        raise HTTPException(400, str(e))
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                            summary="Logo saved (%s)" % body.quality.replace("_", " ")))
    db.commit()
    return {"ok": True, "logo_url": _logos.view_url(o), "logo_quality": o.logo_quality,
            "logo_in_email": _logos.usable(o)}


@router.delete("/leads/{opp_id}/logo")
def remove_logo(opp_id: str, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    _manager(c)
    o = _opp(db, c, opp_id)
    if _logos.has_logo(o):
        _logos.clear(o)
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                                summary="Logo removed"))
        db.commit()
    return {"ok": True}


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
    """Restart a stopped follow-up. An explicit choice by a person; refused when
    the lead opted out, is on Do Not Contact, has a booked call or is excluded."""
    from app.services import sales_suppression as SDNC
    from app.models.evosys_sales_models import EvoSysLead
    o = _opp(db, c, opp_id)
    st = db.get(EvoSysOutreachState, o.id)
    lead = db.get(EvoSysLead, o.id)
    if SDNC.for_opportunity(db, o) or SDNC.find(db, c.bso, o.phone, o.email):
        raise HTTPException(409, "This lead is on Do Not Contact.")
    if o.id in _booked_ids(db, c.bso):
        raise HTTPException(409, "This lead has a booked call; the follow-up stays stopped.")
    if lead is not None and lead.review_status == "excluded":
        raise HTTPException(409, "This lead is excluded.")
    if st is not None and st.campaign_id is None:
        from app.services.evosys_sales import campaigns as CA
        st.campaign_id = CA.ensure_default(db, O.get_settings(db, c.bso)).id
    if O.restart(db, o.id) is None:
        raise HTTPException(409, "This lead opted out or has not started follow-up yet.")
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="note", actor_user_id=c.user.id,
                            summary="Follow-up restarted by a person", occurred_at=datetime.utcnow()))
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


# ── outreach settings (the team's send settings + the default campaign) ──────
#
# Kept for the existing Outreach screen: the team-wide sender fields live on the
# settings row; the copy, offer, link and limit are the DEFAULT campaign's
# (campaigns.ensure_default carried them over unchanged). Per-campaign work is
# in evosys_sales_work_router.py.

TEAM_KEYS = ("sender_name", "from_email", "reply_to_email", "mailing_address", "sms_from_number")
CAMPAIGN_KEYS = ("steps", "offer", "booking_url", "daily_limit")


class SettingsIn(BaseModel):
    steps: Optional[list] = None
    offer: Optional[str] = None
    booking_url: Optional[str] = None
    sender_name: Optional[str] = None
    from_email: Optional[str] = None
    reply_to_email: Optional[str] = None
    mailing_address: Optional[str] = None
    sms_from_number: Optional[str] = None
    alert_phone: Optional[str] = None
    alert_email: Optional[str] = None
    text_auto_reply: Optional[bool] = None
    alert_also: Optional[list] = None
    reply_examples: Optional[list] = None
    daily_limit: Optional[int] = None


def _default_out(db: Session, s, c: Ctx) -> dict:
    from app.services.evosys_sales import campaigns as CA
    d = CA.ensure_default(db, s)
    out = _settings_out(s, c, db)
    out.update({"steps": d.steps or [], "offer": d.offer, "booking_url": d.booking_url, "daily_limit": d.daily_limit,
                "sending_on": bool(d.sending_on), "approved": CA.is_approved(d, s),
                "approved_at": iso(d.approved_at) if CA.is_approved(d, s) else None,
                "problems": CA.problems(d, s), "campaign_id": d.id, "version": d.version,
                "sending_hours": _hours_text(d)})
    return out


def _hours_text(camp) -> str:
    from app.services.evosys_sales import campaigns as CA
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ds = CA.days(camp)
    dtxt = "Weekdays" if ds == [0, 1, 2, 3, 4] else "Every day" if ds == list(range(7)) else ", ".join(names[d] for d in ds)

    def h(x):
        x = int(x)
        return "12 AM" if x in (0, 24) else "12 PM" if x == 12 else ("%d AM" % x if x < 12 else "%d PM" % (x - 12))
    tz = {"America/Chicago": "Central", "America/New_York": "Eastern", "America/Denver": "Mountain",
          "America/Los_Angeles": "Pacific"}.get(camp.timezone, camp.timezone)
    return "%s %s - %s %s" % (dtxt, h(camp.send_start_hour), h(camp.send_end_hour), tz)


def save_team_settings(db: Session, s, data: dict, user_id: str) -> bool:
    """Team-wide send settings. A change stops every campaign: each needs
    approving again (its approved version no longer matches what would go out)."""
    from app.services.evosys_sales import campaigns as CA
    changed = False
    for k in TEAM_KEYS:
        if k in data and data[k] is not None:
            v = data[k]
            if k == "sms_from_number":
                v = O.e164(v) or (str(v).strip()[:20] or None)
            if getattr(s, k) != v:
                setattr(s, k, v)
                changed = True
    # Who gets reply alerts is not part of what prospects receive: saving it
    # never stops a campaign.
    if data.get("alert_phone") is not None:
        s.alert_phone = O.e164(data["alert_phone"]) or None
    if data.get("alert_also") is not None:
        cleaned = []
        for r in data["alert_also"][:10]:
            if not isinstance(r, dict):
                continue
            ph = O.e164(r.get("phone")) if r.get("phone") else None
            em = str(r.get("email") or "").strip().lower()
            em = em if re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", em) else None
            if ph or em:
                cleaned.append({"name": str(r.get("name") or "")[:80], "phone": ph, "email": em})
        s.alert_also = cleaned
    if data.get("reply_examples") is not None:
        s.reply_examples = [str(x).strip()[:1500] for x in data["reply_examples"][:10] if str(x).strip()]
    if data.get("text_auto_reply") is not None:
        s.text_auto_reply = bool(data["text_auto_reply"])
    if data.get("alert_email") is not None:
        e = str(data["alert_email"]).strip().lower()
        s.alert_email = e if re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+$", e) else None
    if changed:
        s.approved_at, s.approved_by, s.sending_on = None, None, False
        for camp in CA.all_for(db, s):
            camp.sending_on = False
    s.updated_at, s.updated_by = datetime.utcnow(), user_id
    db.flush()
    return changed


@router.get("/outreach")
def get_outreach(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    s = O.get_settings(db, c.bso)
    out = _default_out(db, s, c)
    db.commit()
    return out


@router.put("/outreach")
def put_outreach(body: SettingsIn, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.services.evosys_sales import campaigns as CA
    _manager(c)
    s = O.get_settings(db, c.bso)
    data = body.model_dump(exclude_none=True)
    save_team_settings(db, s, data, c.user.id)
    CA.save(db, CA.ensure_default(db, s), {k: data[k] for k in CAMPAIGN_KEYS if k in data}, c.user.id)
    out = _default_out(db, s, c)
    db.commit()
    return out


@router.get("/texting/check")
def texting_check(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """READ ONLY: is the team's texting number on the platform Twilio account,
    and where do its incoming texts go? Nothing is sent or changed."""
    _manager(c)
    from app.services.evosys_sales import sms_setup
    return sms_setup.check(O.get_settings(db, c.bso))


@router.post("/texting/connect-replies")
def texting_connect(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Point the team number's incoming texts at this system's inbound webhook,
    so STOP and replies reach the lead. Changes only that one number."""
    _manager(c)
    from app.services.evosys_sales import sms_setup
    out = sms_setup.connect_replies(O.get_settings(db, c.bso))
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@router.post("/outreach/approve")
def approve(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.services.evosys_sales import campaigns as CA
    _manager(c)
    s = O.get_settings(db, c.bso)
    try:
        CA.approve(db, CA.ensure_default(db, s), s, c.user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    s.approved_at, s.approved_by = datetime.utcnow(), c.user.id
    out = _default_out(db, s, c)
    db.commit()
    return out


class Switch(BaseModel):
    on: bool


@router.post("/outreach/switch")
def switch(body: Switch, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.services.evosys_sales import campaigns as CA
    _manager(c)
    s = O.get_settings(db, c.bso)
    try:
        CA.switch(db, CA.ensure_default(db, s), s, body.on, c.user.id)
    except ValueError:
        raise HTTPException(400, "Approve the messages before turning outreach on.")
    s.sending_on = bool(body.on)
    out = _default_out(db, s, c)
    db.commit()
    return out


AD_CLAIM = __import__("re").compile(r"\b(ad|ads|advert\w*|facebook|instagram|tiktok|post)\b", __import__("re").I)
AD_SOURCES = ("facebook_ads", "facebook_groups", "instagram_meta", "tiktok")


def claim_warnings(text: str, source_channel: Optional[str]) -> list:
    """Plain checks on the rendered copy for THIS lead: a line that mentions an
    ad or a social post when the lead did not come from one."""
    out = []
    if AD_CLAIM.search(text or "") and source_channel not in AD_SOURCES:
        out.append("This email mentions an ad or a post, but this lead came from %s." %
                   (source_channel or "an unknown source").replace("_", " "))
    return out


@router.get("/outreach/preview")
def preview(lead_id: Optional[str] = None, campaign_id: Optional[str] = None,
            c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    from app.models.evosys_sales_models import EvoSysCampaign, EvoSysLead
    from app.services.evosys_sales import campaigns as CA
    s = O.get_settings(db, c.bso)
    camp = (db.get(EvoSysCampaign, campaign_id) if campaign_id else None) or CA.ensure_default(db, s)
    if camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    q = _leads_q(db, c).filter(Opportunity.email.isnot(None), Opportunity.email != "")
    if not lead_id and (camp.industry_key or camp.market_key):
        q = q.join(EvoSysLead, EvoSysLead.opportunity_id == Opportunity.id)
        if camp.industry_key:
            q = q.filter(EvoSysLead.industry_key == camp.industry_key)
        if camp.market_key:
            q = q.filter(EvoSysLead.market_key == camp.market_key)
    o = _opp(db, c, lead_id) if lead_id else q.order_by(Opportunity.created_at.desc()).first()
    if o is None:
        db.commit()
        return {"lead": None, "steps": [], "campaign_id": camp.id}
    lead = db.get(EvoSysLead, o.id)
    ch = lead.source_channel if lead else None
    steps = []
    for st in (camp.steps or []):
        r = O.render_email(o, s, st, camp)
        steps.append(dict(r, day=st.get("day"), channel=st.get("channel"),
                          warnings=claim_warnings(r["subject"] + " " + r["text"], ch)))
    db.commit()
    return {"lead": {"id": o.id, "company": o.company_name, "contact": o.contact_name, "email": o.email,
                     "source_channel": ch}, "steps": steps, "campaign_id": camp.id}


class TestSend(BaseModel):
    to: str
    step: int = 1
    campaign_id: Optional[str] = None


@router.post("/outreach/test")
def test_send(body: TestSend, c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    """Sends one step, filled in with a real lead's details, to an address the
    manager types (their own). Never to the lead."""
    from app.models.evosys_sales_models import EvoSysCampaign
    from app.services.evosys_sales import campaigns as CA
    _manager(c)
    s = O.get_settings(db, c.bso)
    camp = (db.get(EvoSysCampaign, body.campaign_id) if body.campaign_id else None) or CA.ensure_default(db, s)
    if camp.brand_sales_org_id != c.bso:
        raise HTTPException(404, "Campaign not found.")
    steps = camp.steps or []
    if not (1 <= body.step <= len(steps)) or steps[body.step - 1].get("channel") != "email":
        raise HTTPException(400, "Pick an email step.")
    if "@" not in (body.to or ""):
        raise HTTPException(400, "Enter your email address.")
    o = (_leads_q(db, c).filter(Opportunity.email.isnot(None), Opportunity.email != "")
         .order_by(Opportunity.created_at.desc()).first())
    if o is None:
        raise HTTPException(400, "No lead with an email to fill the test in with.")
    msg = O.render_email(o, s, steps[body.step - 1], camp)
    msg = dict(msg, subject="[TEST] " + msg["subject"])
    r = O._send(body.to.strip(), msg, s, o.id)
    db.commit()
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


@public_router.get("/logo/{token}.png")
def logo_png(token: str, db: Session = Depends(get_db)):
    """The lead's logo, for the emails we send them. No login: the token names
    one lead and cannot be forged."""
    from fastapi.responses import Response
    opp_id = _logos.read_token(token)
    o = db.get(Opportunity, opp_id) if opp_id else None
    data = o.logo_png if o is not None and _logos.has_logo(o) else None
    if not data:
        raise HTTPException(404, "Not found.")
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@public_router.get("/book/{token}", response_class=HTMLResponse)
def booking_page(token: str, db: Session = Depends(get_db)):
    """A lead's own booking page (no login; the token names one lead)."""
    import json as _json
    from app.services.evosys_sales import booking_page as BP
    oid = BP.read(token)
    o = db.get(Opportunity, oid) if oid else None
    if o is None:
        return HTMLResponse(_PAGE % ("Link not recognized", "This booking link is not valid. Text us back and we'll set a time."),
                            status_code=404)
    from app.services.evosys_sales.outreach import text_company_name
    return HTMLResponse(BP.render(token, text_company_name(o.company_name), 30),
                        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})


@public_router.get("/b/{code}")
def booking_short(code: str, db: Session = Depends(get_db)):
    """The short booking link used in texts: sends the business to its page."""
    from fastapi.responses import RedirectResponse
    from app.services.evosys_sales import booking_page as BP
    o = BP.by_code(db, code)
    if o is None:
        return HTMLResponse(_PAGE % ("Link not recognized", "This booking link is not valid. Text us back and we'll set a time."),
                            status_code=404)
    db.commit()
    return RedirectResponse(BP.url_for(o.id).replace(BP.base_url(), ""), status_code=302)


@public_router.get("/book/{token}/slots")
def booking_slots(token: str, request: Request, db: Session = Depends(get_db)):
    from datetime import date as _date
    from app.models.scheduling_models import SalesAppointment
    from app.services.evosys_sales import booking_page as BP
    oid = BP.read(token)
    o = db.get(Opportunity, oid) if oid else None
    if o is None:
        raise HTTPException(404, "Not found.")
    prior = BP.current(db, o)
    moving = request.query_params.get("reschedule") == "1"
    if prior is not None and not moving:
        return {"booked": BP.booked_out(db, prior), "slots": []}
    start = None
    f = request.query_params.get("from")
    if f:
        try:
            start = _date.fromisoformat(f[:10])
        except ValueError:
            start = None
    start = start or datetime.utcnow().date()
    out = BP.slots(db, o, start, 7, exclude_appt_id=prior.id if (prior is not None and moving) else None)
    db.commit()
    res = {"slots": out.get("slots", []), "next_from": (start + timedelta(days=7)).isoformat(),
           "duration_minutes": out.get("duration_minutes", 30), "timezone": "America/Chicago"}
    if out.get("problem"):
        res["problem"] = "unavailable"          # customer sees a plain message; the reason is logged
    if prior is not None and moving:
        res["current"] = {"starts_at": prior.starts_at.isoformat() + "Z", "ends_at": prior.ends_at.isoformat() + "Z"}
    return res


class BookPublicIn(BaseModel):
    starts_at: datetime
    reschedule: bool = False


@public_router.get("/book/{token}/event.ics")
def booking_ics(token: str, db: Session = Depends(get_db)):
    """The business's own call as a calendar file (its OWN appointment only -
    the signed token names the lead; no appointment id is ever taken)."""
    from fastapi.responses import Response
    from app.services.evosys_sales import booking_page as BP
    oid = BP.read(token)
    o = db.get(Opportunity, oid) if oid else None
    a = BP.current(db, o) if o is not None else None
    if a is None:
        raise HTTPException(404, "Not found.")
    return Response(BP.ics_for(db, o, a), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="business-growth-call.ics"',
                             "Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@public_router.post("/book/{token}")
def booking_book(token: str, body: BookPublicIn, db: Session = Depends(get_db)):
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales import booking_page as BP
    oid = BP.read(token)
    o = db.get(Opportunity, oid) if oid else None
    if o is None:
        raise HTTPException(404, "Not found.")
    if SDNC.for_opportunity(db, o):
        return {"status": "error", "message": "Please text us back to set a time."}
    return BP.reschedule(db, o, body.starts_at) if body.reschedule else BP.book(db, o, body.starts_at)


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
