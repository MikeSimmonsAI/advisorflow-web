"""Close Appointment Ticket (s16), Permission to Access / Planning Guide
(s21), attribution (s18) and family service requests (s30).

The appointment's Outcome and Sold? still go to the handoff sheet (and from
there into the app) through the one road every team edit takes, so the
sheet, the app and BookIT never disagree during the transition.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.bookit_models import (EVO6_COLLECTION, PPG_STATUSES, PRIORITIES, PTA_STATUSES, SALES_DISPOSITIONS,
                                      SERVICE_STATUSES, BookitAddCase, BookitDisposition, BookitPaperwork,
                                      BookitReferralContact, BookitServiceRequest)
from app.services.bookit.core import MANAGEMENT, BookitError, Who, audit, kick, notify, require_family, sheet_edit
from app.services.programs import restland_sheet as RS

OUTCOMES = ("Showed", "No-Show", "Rescheduled", "Cancelled")


def _d(v, label) -> Optional[date]:
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise BookitError("%s must be a date." % label)


def appointment(db: Session, prog, w: Who, appointment_id: str):
    """The appointment, its family, and whose it is - or 404."""
    from app.models.models import BookingLink, Lead
    from app.models.program_models import HandoffSheetRow as R
    org = prog.organization_id
    b = (db.query(BookingLink).join(Lead, Lead.id == BookingLink.lead_id)
         .filter(BookingLink.id == appointment_id, Lead.organization_id == org).first())
    if b is None:
        raise BookitError("Not found.", 404)
    rec = db.query(R).filter(R.organization_id == org, R.row_id == appointment_id).first()
    owner = (rec.assigned_to if rec else None)
    if not w.sees(owner):
        raise BookitError("Not found.", 404)
    return b, owner


# ── attribution ─────────────────────────────────────────────────────────────

def attribution(db: Session, prog, lead_id: Optional[str], appointment_id: Optional[str]) -> Dict[str, Any]:
    """Where this family came from - decided from the records, never from the
    request. A referral or At-Need 6 Block keeps that origin however they booked."""
    from app.models.bookit_models import BookitReferralBatch
    from app.models.models import Lead
    from app.models.program_models import HandoffSheetRow as R, ProgramSourceRecord
    org = prog.organization_id
    out: Dict[str, Any] = {"lead_id": lead_id, "appointment_id": appointment_id, "acquisition_source": "unknown",
                           "booking_source": None, "campaign_key": None}
    if not lead_id:
        return out
    c = (db.query(BookitReferralContact).filter(BookitReferralContact.organization_id == org,
                                                BookitReferralContact.lead_id == lead_id).first())
    if c is not None:
        batch = db.query(BookitReferralBatch).filter(BookitReferralBatch.id == c.batch_id).first()
        out.update(acquisition_source="add_lead" if batch and batch.kind == "add" else "evo6_block",
                   referral_batch_id=c.batch_id, referral_contact_id=c.id,
                   referring_advisor=batch.submitting_advisor if batch else None,
                   add_case_id=batch.add_case_id if batch else None)
    else:
        case = (db.query(BookitAddCase).filter(BookitAddCase.organization_id == org,
                                               BookitAddCase.contact_lead_id == lead_id).first())
        if case is not None:
            out.update(acquisition_source="add_lead", add_case_id=case.id, referring_advisor=case.created_by)
    rec = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org,
                                                ProgramSourceRecord.lead_id == lead_id).first())
    if rec is not None:
        out["campaign_key"] = rec.campaign_family
    fu = (db.query(R).filter(R.organization_id == org, R.lead_id == lead_id, R.tab == RS.FOLLOW_UPS).all())
    booked_from_fu = any((json.loads(r.staff_json or "{}").get("appt_date") or
                          (json.loads(r.staff_json or "{}").get("fu_result") or "").startswith("Talked - booked"))
                         for r in fu)
    out["booking_source"] = "follow_up" if booked_from_fu else ("booking_link" if appointment_id else None)
    if out["acquisition_source"] == "unknown":
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if rec is not None:
            out["acquisition_source"] = "evosys_follow_up" if booked_from_fu else "evosys_appointment"
        elif lead is not None and (lead.source or "") == RS.REFERRAL_SOURCE:
            out["acquisition_source"] = "evo6_block"      # entered on the sheet's 6 Block tab
        elif lead is not None:
            out["acquisition_source"] = "independent"
    if appointment_id:
        a = db.query(R).filter(R.organization_id == org, R.row_id == appointment_id).first()
        out["assigned_advisor"] = a.assigned_to if a else None
    return out


# ── paperwork ───────────────────────────────────────────────────────────────

def paperwork(db: Session, org: str, lead_id: str) -> BookitPaperwork:
    p = db.query(BookitPaperwork).filter(BookitPaperwork.organization_id == org,
                                         BookitPaperwork.lead_id == lead_id).first()
    if p is None:
        p = BookitPaperwork(organization_id=org, lead_id=lead_id)
        db.add(p)
        db.flush()
    return p


def paperwork_dict(p: Optional[BookitPaperwork]) -> Dict[str, Any]:
    if p is None:
        return {"pta_status": "not_reviewed", "ppg_status": "not_provided"}
    return {"pta_status": p.pta_status, "pta_completed_on": p.pta_completed_on.isoformat() if p.pta_completed_on
            else None, "pta_advisor": p.pta_advisor, "pta_appointment_id": p.pta_appointment_id,
            "pta_document_ref": p.pta_document_ref, "pta_notes": p.pta_notes, "ppg_status": p.ppg_status,
            "ppg_on": p.ppg_on.isoformat() if p.ppg_on else None}


def set_paperwork(db: Session, org: str, w: Who, lead_id: str, body: Dict[str, Any],
                  appointment_id: Optional[str] = None) -> Dict[str, Any]:
    p = paperwork(db, org, lead_id)
    if "pta_status" in body and body["pta_status"]:
        if body["pta_status"] not in PTA_STATUSES:
            raise BookitError("Permission to Access status isn't one of the choices.")
        if body["pta_status"] != p.pta_status:
            audit(db, org, w, "paperwork", lead_id, "update", "pta_status", p.pta_status, body["pta_status"])
        p.pta_status = body["pta_status"]
        if p.pta_status == "completed":
            p.pta_completed_on = _d(body.get("pta_completed_on"), "Completion date") or p.pta_completed_on \
                or date.today()
            p.pta_advisor = p.pta_advisor or w.name or w.user_name
            p.pta_appointment_id = appointment_id or p.pta_appointment_id
    for k in ("pta_document_ref", "pta_notes"):
        if k in body:
            setattr(p, k, (body[k] or "").strip()[:2000] or None)
    if "ppg_status" in body and body["ppg_status"]:
        if body["ppg_status"] not in PPG_STATUSES:
            raise BookitError("Planning Guide status isn't one of the choices.")
        if body["ppg_status"] != p.ppg_status:
            audit(db, org, w, "paperwork", lead_id, "update", "ppg_status", p.ppg_status, body["ppg_status"])
            p.ppg_on = date.today() if body["ppg_status"] != "not_provided" else None
        p.ppg_status = body["ppg_status"]
    p.updated_by = w.user_id
    db.flush()
    return paperwork_dict(p)


# ── close appointment ticket ────────────────────────────────────────────────

def disposition_dict(d: Optional[BookitDisposition]) -> Optional[Dict[str, Any]]:
    if d is None:
        return None
    return {"outcome": d.outcome, "sales_disposition": d.sales_disposition, "pta_status": d.pta_status,
            "ppg_status": d.ppg_status, "evo6_status": d.evo6_status, "evo6_reason": d.evo6_reason,
            "follow_up_on": d.follow_up_on.isoformat() if d.follow_up_on else None, "final_notes": d.final_notes,
            "is_draft": d.is_draft, "closed_at": d.closed_at.isoformat() + "Z" if d.closed_at else None,
            "closed_by": d.closed_by}


def close_ticket(db: Session, prog, w: Who, appointment_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Save the ticket as a draft, or close it (`close: true`). Closing an
    attended appointment requires the sales disposition, both paperwork
    statuses, the File Review 6 Block status (with a reason when not complete) and a
    follow-up date when one is needed. It never marks paperwork done because
    the family showed, never invents referrals, and never counts a pending
    decision as a sale."""
    org = prog.organization_id
    b, owner = appointment(db, prog, w, appointment_id)
    d = (db.query(BookitDisposition).filter(BookitDisposition.organization_id == org,
                                            BookitDisposition.appointment_id == appointment_id).first())
    if d is None:
        d = BookitDisposition(organization_id=org, appointment_id=appointment_id, lead_id=b.lead_id)
        db.add(d)
    if d.closed_at and not w.manager and not body.get("reopen"):
        raise BookitError("This ticket is closed. Ask management to correct it.", 403)
    outcome = body.get("outcome", d.outcome)
    if outcome and outcome not in OUTCOMES:
        raise BookitError("Outcome must be one of: " + ", ".join(OUTCOMES))
    sd = body.get("sales_disposition", d.sales_disposition)
    if sd and sd not in SALES_DISPOSITIONS:
        raise BookitError("Sales result isn't one of the choices.")
    ev = body.get("evo6_status", d.evo6_status)
    if ev and ev not in EVO6_COLLECTION:
        raise BookitError("File Review 6 Block status isn't one of the choices.")
    for k in ("pta_status", "ppg_status"):
        v = body.get(k, getattr(d, k))
        if v and v not in (PTA_STATUSES if k == "pta_status" else PPG_STATUSES):
            raise BookitError("Paperwork status isn't one of the choices.")
    d.outcome, d.sales_disposition, d.evo6_status = outcome, sd, ev
    d.pta_status = body.get("pta_status", d.pta_status)
    d.ppg_status = body.get("ppg_status", d.ppg_status)
    if "evo6_reason" in body:
        d.evo6_reason = (body["evo6_reason"] or "").strip()[:1000] or None
    if "final_notes" in body:
        d.final_notes = (body["final_notes"] or "").strip()[:4000] or None
    if "follow_up_on" in body:
        d.follow_up_on = _d(body["follow_up_on"], "Follow-up date")
    closing = bool(body.get("close"))
    if closing:
        missing = []
        if not d.outcome:
            missing.append("the outcome")
        if d.outcome == "Showed":
            if not d.sales_disposition:
                missing.append("the sales result")
            if not d.pta_status:
                missing.append("the Permission to Access status")
            if not d.ppg_status:
                missing.append("the Planning Guide status")
            if not d.evo6_status:
                missing.append("the File Review 6 Block status")
            elif d.evo6_status != "complete" and not d.evo6_reason:
                missing.append("why the File Review 6 Block isn't complete")
            if d.sales_disposition in ("pending_decision", "follow_up_needed") and not d.follow_up_on:
                missing.append("the follow-up date")
            if d.sales_disposition == "closed_sale" and not _has_closed_sale(db, org, appointment_id):
                missing.append("the sale's products and amounts (record the sale first)")
        if not d.final_notes and d.outcome in ("Showed", "No-Show"):
            missing.append("final notes")
        if missing:
            raise BookitError("To close the ticket, add " + ", ".join(missing) + ".")
        d.is_draft, d.closed_at, d.closed_by = False, datetime.utcnow(), w.name or w.user_name
    # the paperwork record follows what the advisor recorded
    if d.pta_status or d.ppg_status:
        set_paperwork(db, org, w, b.lead_id, {"pta_status": d.pta_status, "ppg_status": d.ppg_status},
                      appointment_id=appointment_id)
    # Outcome / Sold? / notes -> the sheet and the app (one road)
    if closing or "outcome" in body:
        if d.outcome:
            sheet_edit(db, org, w, RS.APPOINTMENTS, appointment_id, "outcome", d.outcome)
        if d.sales_disposition in ("closed_sale", "no_sale"):
            sheet_edit(db, org, w, RS.APPOINTMENTS, appointment_id, "sold",
                       "Yes" if d.sales_disposition == "closed_sale" else "No")
        if closing and d.final_notes:
            sheet_edit(db, org, w, RS.APPOINTMENTS, appointment_id, "notes", d.final_notes[:2000])
        kick(prog)
    audit(db, org, w, "disposition", appointment_id, "close" if closing else "draft", "outcome", None, d.outcome)
    db.flush()
    nxt = []
    if d.pta_status == "completed" and d.evo6_status != "complete":
        nxt.append("open_evo6")
    if d.sales_disposition == "closed_sale":
        nxt.append("open_sale")
    if d.sales_disposition in ("pending_decision", "follow_up_needed"):
        nxt.append("follow_up")
    return {"disposition": disposition_dict(d), "next": nxt}


def _has_closed_sale(db, org, appointment_id) -> bool:
    from app.models.bookit_models import BookitSale
    return db.query(BookitSale.id).filter(BookitSale.organization_id == org,
                                          BookitSale.appointment_id == appointment_id,
                                          BookitSale.status == "closed").first() is not None


# ── service requests ────────────────────────────────────────────────────────

def sr_dict(r: BookitServiceRequest) -> Dict[str, Any]:
    return {"id": r.id, "lead_id": r.lead_id, "family": r.family_name, "description": r.description,
            "received_at": r.received_at.isoformat() + "Z" if r.received_at else None,
            "assigned_to": r.assigned_to, "priority": r.priority, "status": r.status, "notes": r.notes,
            "resolution": r.resolution, "appointment_id": r.appointment_id,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None}


def save_service_request(db: Session, prog, w: Who, body: Dict[str, Any], rid: Optional[str] = None):
    org = prog.organization_id
    if rid:
        r = db.query(BookitServiceRequest).filter(BookitServiceRequest.organization_id == org,
                                                  BookitServiceRequest.id == rid).first()
        if r is None or not w.sees(r.assigned_to):
            raise BookitError("Not found.", 404)
    else:
        if not (body.get("description") or "").strip():
            raise BookitError("Describe what the family needs.")
        r = BookitServiceRequest(organization_id=org, created_by=w.user_id,
                                 assigned_to=w.name if not w.manager else (body.get("assigned_to") or w.name))
        db.add(r)
    for k in ("description", "notes", "family_name", "lead_id", "appointment_id"):
        if k in body:
            setattr(r, k, (body[k] or "").strip()[:4000] or None)
    if r.lead_id and not w.manager:
        from app.services.bookit.core import require_family
        require_family(db, prog, w, r.lead_id)
    if "priority" in body:
        if body["priority"] not in PRIORITIES:
            raise BookitError("Priority isn't one of the choices.")
        r.priority = body["priority"]
    if "assigned_to" in body and w.manager:
        r.assigned_to = body["assigned_to"] or None
        if r.status == "new" and r.assigned_to:
            r.status = "assigned"
    if "resolution" in body:
        r.resolution = (body["resolution"] or "").strip()[:4000] or None
    if "status" in body:
        st = body["status"]
        if st not in SERVICE_STATUSES:
            raise BookitError("Status isn't one of the choices.")
        if st in ("resolved", "closed") and not r.resolution:
            raise BookitError("Write down how it was resolved first.")
        r.status = st
    db.flush()
    if not rid and r.assigned_to:
        notify(db, org, r.assigned_to, "service_request", "New family service request",
               ref_type="service_request", ref_id=r.id)
    if not rid and r.priority == "urgent":
        notify(db, org, MANAGEMENT, "service_request_urgent", "Urgent family service request",
               ref_type="service_request", ref_id=r.id)
    audit(db, org, w, "service_request", r.id, "update" if rid else "create", "status", None, r.status)
    db.flush()
    return sr_dict(r)


# ── a new appointment booked from inside BookIT (owner, Nov 27) ─────────────

def new_appointment(db: Session, prog, w: Who, body: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Book a family the caller may work (advisors: their own; managers: any)
    through the same path as every booking: the time is re-checked against the
    real calendar, the calendar event is made, and the family gets the usual
    confirmation (unless the caller turns it off). Reported as booked only when
    the booking reads back booked at that exact time. A retry for the same
    family and time returns the same booking - never a second one."""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    from app.models.models import Lead
    from app.services.programs import appointments as A
    from app.services.programs import restland_sheet as RS
    if w.view_only:
        raise BookitError("This is a read-only preview.", 403)
    lead_id = body.get("lead_id")
    require_family(db, prog, w, lead_id)
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    try:
        slot = datetime.fromisoformat(str(body.get("slot") or "")[:16])
    except ValueError:
        raise BookitError("Pick a date and a time.", 422)
    now = now or datetime.utcnow()
    local_now = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Chicago")).replace(tzinfo=None)
    if slot <= local_now:
        raise BookitError("That date and time has already passed.", 422)
    how = (body.get("how") or "At Restland").strip()
    if how not in RS.HOW_CHOICES:
        raise BookitError("Pick how the appointment happens.", 422)
    key, kind = RS.HOW_CHOICES[how]
    addr = (body.get("address") or "").strip() or None
    if key == "home" and not addr:
        raise BookitError("A visit at their home needs the full home address.", 422)
    res = A.book(db, prog, lead, slot, kind, family_text=bool(body.get("notify", True)),
                 meeting_key=key, visit_address=addr if key == "home" else None)
    if not res.get("ok"):
        why = {"taken": "That time is already taken. Pick another time.",
               "already_booked": "This family already has an upcoming appointment (%s). Reschedule that one instead."
                                 % (res.get("booked_time") or "")[:16].replace("T", " "),
               "opted_out": "This family asked us to stop contacting them, so it can't be booked here.",
               "no_calendar": "No booking calendar is set up.",
               "needs_place": "For a home visit, put the full home address (street, city, state, ZIP).",
               "same_day": "Same-day appointments are turned off. Pick tomorrow or later."}
        raise BookitError(why.get(res.get("status"), "The calendar didn't confirm it: %s" % (res.get("reason") or "try again")), 409)
    if not res.get("already"):
        who = w.name or w.user_name
        RS._note(db, prog.organization_id, lead.id, "Restland BookIT (%s): booked %s, %s"
                 % (who, slot.strftime("%a %b %-d, %-I:%M %p"), how))
        audit(db, prog.organization_id, w, "appointment", res.get("booking_id"), "book", "booked_time", None,
              slot.isoformat())
        try:
            A.notify_team_booked(db, prog, lead, slot, kind)
        except Exception:                                # noqa: BLE001 - the booking stands either way
            pass
        from app.services.bookit.core import kick
        kick(prog)                                       # the board picks it up on its next pass
    return {"booked": True, "booking_id": res.get("booking_id"), "slot": slot.isoformat(),
            "simulated": bool(res.get("simulated")), "already": bool(res.get("already")),
            "family_notified": bool(body.get("notify", True)) and not res.get("simulated")}
