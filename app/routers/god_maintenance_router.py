"""Narrow, auditable cleanup of ONE known record. God-only.

Why this exists rather than the ordinary cancel button.

A test booking was created by accident during a controlled voice call. The
obvious way to remove it - POST /calendar/cancel-booking/{id} - does three
things beyond cancelling: it fires `on_booking_cancelled`, which TEXTS THE LEAD
that their appointment is cancelled, emails the advisor, and reopens the
cadence. For a real family that is correct behaviour. For clearing up our own
test it would send a real message about an appointment nobody made, to a phone
number, from a Twilio account that is genuinely configured to send.

So the requirement is not "cancel a booking". It is: remove one specific
record, touch nothing else, and communicate with nobody. That is a different
operation and it deserves its own name.

Every endpoint here:
  * names ONE record by id - there is no "cleanup all", no pattern, no filter
    that could widen with a typo
  * is scoped to an organization the caller must state, and refuses if the
    record belongs to a different one, so a mistyped id cannot reach another
    tenant's data
  * DEFAULTS TO A DRY RUN. `apply` must be sent explicitly and separately, and
    the dry run returns exactly what the apply would change
  * sends NOTHING - no SMS, no email, no calendar invitation to an attendee
  * restarts NOTHING - the cadence is left in whatever state it is in
  * writes an audit entry naming the god admin who ran it

What is deliberately NOT here: deletion. Rows are marked, never removed. The
audit history of a test booking is the evidence that the test happened, and
`DO NOT delete real production data without explicit owner approval` applies to
the platform's own records too. If a row genuinely must disappear, that is a
conversation, not an endpoint.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import BookingLink, Lead, Message, User, VoiceCall

router = APIRouter(prefix="/god/maintenance", tags=["god-maintenance"])

log = logging.getLogger(__name__)


def _digits(value: Optional[str]) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


def _same_number(a: Optional[str], b: Optional[str]) -> bool:
    """+14695537417, 4695537417 and (469) 553-7417 are one number."""
    da, dbb = _digits(a), _digits(b)
    if not da or not dbb:
        return False
    return da[-10:] == dbb[-10:]


# ── booking cleanup ─────────────────────────────────────────────────────────

class BookingCleanup(BaseModel):
    booking_id: str
    organization_id: str
    apply: bool = False          # dry run unless explicitly told otherwise
    reason: str = ""


@router.post("/booking-cleanup")
def booking_cleanup(req: BookingCleanup,
                    god: User = Depends(require_god),
                    db: Session = Depends(get_db)):
    """Inspect, and optionally retire, ONE booking. Communicates with nobody."""
    booking = db.query(BookingLink).filter(BookingLink.id == req.booking_id).first()
    if booking is None:
        raise HTTPException(404, "Booking not found.")

    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    advisor = db.query(User).filter(User.id == booking.user_id).first()

    # TENANT SCOPE. The caller must state the organization, and it must match.
    # An id typed one character wrong should hit this, not another customer's
    # appointment.
    owning_org = (lead.organization_id if lead is not None
                  else (advisor.organization_id if advisor is not None else None))
    if owning_org != req.organization_id:
        raise HTTPException(
            409,
            "This booking belongs to organization %r, not %r. Refusing."
            % (owning_org, req.organization_id),
        )

    # Everything attached to it, so the decision is made with the whole picture.
    messages = (db.query(Message)
                .filter(Message.booking_link_id == booking.id).all())
    voice_calls = (db.query(VoiceCall)
                   .filter(VoiceCall.lead_id == booking.lead_id).all()
                   if booking.lead_id else [])

    case_files = []
    try:
        rows = db.execute(sa_text(
            "SELECT id, case_status, appointment_date FROM appointment_case_files "
            "WHERE booking_link_id = :b"), {"b": booking.id}).fetchall()
        case_files = [{"id": r[0], "case_status": r[1],
                       "appointment_date": str(r[2])} for r in rows]
    except Exception:
        log.warning("booking-cleanup: could not read case files", exc_info=True)

    cadence = None
    try:
        row = db.execute(sa_text(
            "SELECT id, status, current_touch_number FROM cadence_states "
            "WHERE lead_id = :l"), {"l": booking.lead_id}).fetchone()
        if row:
            cadence = {"id": row[0], "status": row[1], "current_touch": row[2]}
    except Exception:
        log.warning("booking-cleanup: could not read cadence state", exc_info=True)

    found = {
        "booking": {
            "id": booking.id,
            "status": booking.status,
            "booked_time": str(booking.booked_time) if booking.booked_time else None,
            "calendar_event_id": booking.calendar_event_id,
            "confirmation_sent": bool(getattr(booking, "confirmation_sent", False)),
        },
        "lead": ({"id": lead.id,
                  "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip(),
                  "phone": lead.phone, "status": lead.status,
                  "organization_id": lead.organization_id} if lead else None),
        "advisor": ({"id": advisor.id, "name": advisor.full_name,
                     "organization_id": advisor.organization_id} if advisor else None),
        "messages_referencing_this_link": len(messages),
        "voice_calls_for_this_lead": len(voice_calls),
        "case_files": case_files,
        "cadence_state": cadence,
    }

    # What an apply WOULD do - written once and reported identically in both
    # modes, so the dry run cannot describe something different from the run.
    plan = []
    if booking.status != "cancelled":
        plan.append("mark booking %s cancelled (currently %r)"
                    % (booking.id, booking.status))
    if booking.calendar_event_id:
        plan.append("delete calendar event %s from the advisor's calendar"
                    % booking.calendar_event_id)
    for cf in case_files:
        if cf["case_status"] != "void":
            plan.append("mark case file %s void" % cf["id"])
    if lead is not None and (lead.status or "") == "booked":
        plan.append("reset lead status from 'booked' to 'replied' "
                    "(it was set by this booking)")
    if not plan:
        plan.append("nothing to change - already clean")

    never = [
        "no SMS or email to the lead, the advisor, or anyone else",
        "no cadence restart and no cadence state change",
        "no other booking, lead, message or call touched",
        "no row deleted - everything is marked, so the audit trail survives",
    ]

    if not req.apply:
        log.info("AUDIT: GOD_BOOKING_CLEANUP_DRYRUN | admin=%s | org=%s | booking=%s",
                 god.email, req.organization_id, booking.id)
        return {"dry_run": True, "found": found, "would_do": plan,
                "will_never": never}

    # ── apply ───────────────────────────────────────────────────────────────
    #
    # `cancel_calendar_event` is the ONLY existing helper used here, and it is
    # used because it communicates with nobody: it deletes the calendar event
    # and marks the booking cancelled, and that is all it does. The messaging
    # lives in `on_booking_cancelled` in the calendar router, which this
    # endpoint deliberately does not call.
    done = []
    from app.services.calendar_service import cancel_calendar_event
    result = cancel_calendar_event(db, booking)
    done.append("booking marked cancelled (%s)" % result.get("note"))

    for cf in case_files:
        if cf["case_status"] != "void":
            db.execute(sa_text(
                "UPDATE appointment_case_files SET case_status = 'void', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = :i"), {"i": cf["id"]})
            done.append("case file %s marked void" % cf["id"])

    if lead is not None and (lead.status or "") == "booked":
        lead.status = "replied"
        done.append("lead status reset to 'replied'")

    db.commit()

    log.info("AUDIT: GOD_BOOKING_CLEANUP_APPLIED | admin=%s | org=%s | booking=%s "
             "| reason=%s | actions=%s",
             god.email, req.organization_id, booking.id, req.reason or "-",
             "; ".join(done))
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, req.organization_id, god.id,
                   action="maintenance.booking_cleanup",
                   target_type="booking_link", target_id=booking.id)
    except Exception:
        log.warning("booking-cleanup: audit row failed", exc_info=True)

    return {"dry_run": False, "found": found, "did": done, "never_did": never}


# ── phone audit ─────────────────────────────────────────────────────────────

class PhoneAudit(BaseModel):
    numbers: list[str]
    organization_id: Optional[str] = None


@router.post("/phone-audit")
def phone_audit(req: PhoneAudit,
                god: User = Depends(require_god),
                db: Session = Depends(get_db)):
    """WHO owns these numbers? Read-only. Changes nothing, ever.

    Written for the question "which records use +14695537417, and which of them
    are test data?" - and written as a read because the answer decides what may
    safely be touched. Matching is on the last ten digits, so +14695537417 and
    4695537417 are recognised as one number rather than as two records.

    Ownership is REPORTED, never inferred and never merged. Two people can
    share a phone number and two users with the same name can be different
    people; this endpoint says what it found and stops there.
    """
    wanted = [n for n in (req.numbers or []) if _digits(n)]
    if not wanted:
        raise HTTPException(400, "Give at least one number.")

    out = {}
    for number in wanted:
        leads_q = db.query(Lead)
        if req.organization_id:
            leads_q = leads_q.filter(Lead.organization_id == req.organization_id)
        matched_leads = [l for l in leads_q.all() if _same_number(l.phone, number)]

        matched_users = [u for u in db.query(User).all()
                         if _same_number(getattr(u, "twilio_phone_number", None), number)
                         or _same_number(getattr(u, "notification_phone", None), number)]

        out[number] = {
            "leads": [{
                "id": l.id,
                "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip(),
                "phone": l.phone,
                "email": getattr(l, "email", None),
                "status": l.status,
                "organization_id": l.organization_id,
                "assigned_to_id": getattr(l, "assigned_to_id", None),
                "is_duplicate": bool(getattr(l, "is_duplicate", False)),
                "source_file": getattr(l, "source_file", None),
                "created_at": str(getattr(l, "created_at", "")) or None,
            } for l in matched_leads],
            "users_sending_from_it": [{
                "id": u.id, "email": u.email, "full_name": u.full_name,
                "organization_id": u.organization_id, "role": u.role,
                "twilio_phone_number": getattr(u, "twilio_phone_number", None),
            } for u in matched_users],
        }

    log.info("AUDIT: GOD_PHONE_AUDIT | admin=%s | numbers=%s | org=%s",
             god.email, ",".join(wanted), req.organization_id or "-")
    return {"read_only": True, "results": out}
