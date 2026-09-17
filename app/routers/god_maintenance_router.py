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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import BookingLink, Lead, Message, User, VoiceCall

router = APIRouter(prefix="/god/maintenance", tags=["god-maintenance"])


# ── PIPELINE CONSISTENCY ────────────────────────────────────────────────────
#
# READ-ONLY, and it lives under /god/maintenance because "which of my
# customers' AI conversations are claiming sends that never happened" is a
# platform question, not a tenant one. Nothing here repairs anything: the
# scan returns its own proposed cleanup plan alongside the evidence, for a
# human to approve separately.

@router.get("/pipeline-consistency")
def pipeline_consistency(
    organization_id: str = Query(default=None,
                                 description="Narrow to one customer. Omit to scan the platform."),
    verdict: str = Query(default=None,
                         description="definitely_inconsistent | suspicious | valid"),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """Classify AI conversations against authoritative communication history.

    The counters were advanced before the send for the whole period before the
    ordering fix, so `ai_responses_sent > messages_sent` is an arithmetic
    fingerprint of an attempt that was recorded and never left. See
    app/services/pipeline_consistency.py for what each verdict means and why
    there are three of them rather than two.
    """
    from app.services import pipeline_consistency as pc
    if verdict and verdict not in pc.ALL_VERDICTS:
        raise HTTPException(
            status_code=400,
            detail="Unknown verdict %r. Valid: %s" % (verdict, ", ".join(pc.ALL_VERDICTS)))
    return pc.scan(db, organization_id=organization_id, limit=limit, verdict=verdict)


# ── READINESS: THE THREE QUESTIONS THAT HAVE TO BE ANSWERED FROM PRODUCTION ─
#
# ALL READ-ONLY. Every one of these was written as a service function first and
# could be run from a shell, which is not the same as being available. A switch
# that cannot be inspected, and a diagnostic nobody can reach, are both just
# code - and the decisions below are the ones with real families on the other
# side of them, so the numbers behind them should take one request.


@router.get("/cadence-backlog")
def cadence_backlog(
    organization_id: str = Query(default=None,
                                 description="Narrow to one customer. Omit to scan the platform."),
    include_leads: bool = Query(default=False,
                                description="Also list the individual enrollments."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """WHAT WOULD HAPPEN IF CADENCE SMS WERE SWITCHED ON, counted.

    The engine never sent a message - it raised TypeError on the first touch of
    every run, after the counter had already advanced and committed. The repair
    ships disabled because turning it on begins real SMS to whatever has
    accumulated since.

    This answers that with numbers rather than a feeling: active enrollments,
    touches the counters claim with no recorded send behind them, what would be
    due the instant the switch flips, and how each of those would end - stopped,
    blocked by compliance, blocked by permitted contact hours, skipped, or
    actually sent - by organization and by touch number.

    Sends nothing. Modifies nothing. Re-dates nothing.
    """
    from app.services import cadence_backlog as cb
    return cb.scan(db, organization_id=organization_id, include_leads=include_leads)


@router.get("/cadence-activation-plan")
def cadence_activation_plan(
    organization_id: str = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """A safe way to turn the cadence on, with this deployment's own numbers.

    Describes; does not execute. `executed` is False and there is no argument
    that makes it True. The sentence it exists to make unavoidable: the backlog
    is not the first thing you send, and sending it is what happens by itself
    if the switch is simply flipped.
    """
    from app.services import cadence_backlog as cb
    return cb.activation_plan(db, organization_id=organization_id)


@router.get("/outbound-switches")
def outbound_switches(
    organization_id: str = Query(default=None,
                                 description="Omit for the deployment-wide state."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """EVERY OUTBOUND SWITCH IN ONE PLACE, for the whole deployment.

    The per-organization half was already visible on the customer record. The
    deployment half was only an environment variable, so "is anything on" could
    not be answered without shell access to the host - which is the question
    somebody asks in a hurry, and the wrong moment to be reading env vars over
    somebody's shoulder.
    """
    from app.models.models import Organization
    from app.services import cadence_service, outbound_email_gate as gate

    org = None
    if organization_id:
        org = db.query(Organization).filter(
            Organization.id == organization_id).first()
        if org is None:
            raise HTTPException(status_code=404, detail="No such organization.")

    email = gate.effective_report(org) if org is not None else {
        "deployment": {s: gate.source_enabled(s) for s in gate.GATED_SOURCES},
        "organization": None,
        "note": ("Deployment switches only. Pass organization_id for the "
                 "combined answer - BOTH halves must say yes to send."),
    }
    return {
        "read_only": True,
        "organization_id": organization_id,
        "email": email,
        "cadence_sms": {
            "deployment_enabled": cadence_service._sending_enabled(),
            "variable": "CADENCE_SMS_SENDING",
            "per_customer": "the `cadences` feature entitlement",
            "note": "Both must say yes. A missing switch means no.",
        },
    }


@router.get("/lifecycle-readiness")
def lifecycle_readiness(
    organization_id: str = Query(...,
                                 description="Required: this reads one customer's book."),
    limit: int = Query(default=2000, ge=1, le=20000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """HOW MUCH IS THE ONE-COLUMN STATUS MODEL COSTING, on real rows.

    Counts the leads whose derived state cannot be expressed by any single
    `Lead.status` value, and names which dimension is being destroyed. This is
    the number the SS7 decision should be made from - not an argument about
    vocabulary, a count of families whose record cannot say two true things at
    once.
    """
    from app.services import lead_lifecycle
    leads = (db.query(Lead)
             .filter(Lead.organization_id == organization_id)
             .limit(limit).all())
    return lead_lifecycle.migration_readiness(db, leads)

log = logging.getLogger(__name__)


def _digits(value: Optional[str]) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())


def _same_number(a: Optional[str], b: Optional[str]) -> bool:
    """+14695537417, 4695537417 and (469) 553-7417 are one number."""
    da, dbb = _digits(a), _digits(b)
    if not da or not dbb:
        return False
    return da[-10:] == dbb[-10:]


def _event_id_provider(event_id: Optional[str]) -> Optional[str]:
    """Which calendar an event id belongs to, read off the id itself.

    This matters because the booking being cleaned up was written BEFORE the
    provider fix, when Microsoft won by being first in a preference tuple. Its
    `calendar_event_id` is a Graph id, and deleting it through the Google
    client would 404 — which the Google path reports as "already deleted", so
    the tool would claim success while the appointment sat on the advisor's
    real Outlook calendar on the day in question.

    Graph ids are long, base64-ish and begin AAMk/AQMk. Google ids are short
    and lowercase-alphanumeric. Nothing is deleted on the strength of this
    guess alone: it only chooses which client to ask, and the cancel is
    id-exact in both.
    """
    if not event_id:
        return None
    e = str(event_id)
    if e[:4] in ("AAMk", "AQMk") or ("=" in e and len(e) > 60):
        return "microsoft"
    return "google"


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
    event_provider = _event_id_provider(booking.calendar_event_id)
    found["calendar_event_provider"] = event_provider

    plan = []
    if booking.status != "cancelled":
        plan.append("mark booking %s cancelled (currently %r)"
                    % (booking.id, booking.status))
    if booking.calendar_event_id:
        plan.append("delete calendar event %s from the advisor's %s calendar"
                    % (booking.calendar_event_id[:24] + "…", event_provider))
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

    # THE CALENDAR ARTIFACT, THROUGH THE CALENDAR THAT ACTUALLY HOLDS IT.
    #
    # `cancel_calendar_event` only speaks Google. This booking's event was
    # written to Outlook, back when Microsoft won by being first in a
    # preference tuple, so the Google client would have 404'd — and reported
    # that as "already deleted". The booking row would say cancelled while the
    # appointment stayed on the advisor's real calendar.
    #
    # The provider registry already knows how to cancel by id on either side,
    # so the event is removed through the one its id belongs to.
    event_id = booking.calendar_event_id
    if event_id:
        try:
            from app.services import calendar_providers as reg
            provider = reg.get_provider(db, advisor, prefer=event_provider)
            resolved = getattr(provider, "resolved_key", None)
            if resolved != event_provider:
                done.append(
                    "calendar event NOT deleted: the event is in %s but that "
                    "calendar is not currently connected (resolved to %r). "
                    "The event is still on the advisor's calendar."
                    % (event_provider, resolved))
            else:
                res = provider.cancel_event(event_id)
                if getattr(res, "ok", False):
                    booking.calendar_event_id = None
                    done.append("calendar event deleted from %s" % event_provider)
                else:
                    done.append("calendar event NOT deleted from %s: %s"
                                % (event_provider,
                                   getattr(res, "error_message", None)
                                   or getattr(res, "error_code", "unknown")))
        except Exception as e:
            log.exception("booking-cleanup: calendar delete failed")
            done.append("calendar event NOT deleted (%s)" % e)

    # The booking row itself. `cancel_calendar_event` is still used for the
    # status change because it is the existing, communication-free helper - but
    # the event id above has already been cleared when the delete succeeded, so
    # it will not try Google a second time.
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
