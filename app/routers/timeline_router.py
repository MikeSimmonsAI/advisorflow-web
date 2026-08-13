"""
Lead Timeline Router
GET /leads/{lead_id}/timeline

Aggregates all activity for a lead into a single sorted event feed:
SMS sent, SMS received, email sent, booking links, appointments,
outcomes, cadence state changes. No new tables — pure query aggregation
over existing data.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from app.deps import get_db, get_current_user
from app.models.models import (
    Lead, Message, Reply, BookingLink, LeadOutcome,
    CadenceState, EmailMessage, User
)

router = APIRouter(prefix="/leads", tags=["timeline"])


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


@router.get("/{lead_id}/activity")
def get_lead_timeline(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Full activity log for a lead — all event types in one sorted feed.
    Separate from /timeline which returns the conversation bubble feed.
    Each event: { id, type, ts, label, body, meta }.
    """
    # Scope check — advisor sees only their org's leads
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(404, "Lead not found")

    events = []

    # ── Lead created ────────────────────────────────────────────────────
    events.append({
        "id": f"lead-created-{lead.id}",
        "type": "lead_created",
        "ts": _fmt(lead.created_at),
        "label": "Lead added",
        "body": f"Source: {lead.source or 'unknown'}"
            + (f" · List: {lead.import_list_name}" if lead.import_list_name else "")
            + (f" · Type: {lead.relationship_type}" if lead.relationship_type and lead.relationship_type != 'cold_lead' else ""),
        "meta": {
            "source": lead.source,
            "relationship_type": lead.relationship_type,
            "import_list_name": lead.import_list_name,
        },
    })

    # ── Outbound SMS (Message) ───────────────────────────────────────────
    sms_messages = db.query(Message).filter(Message.lead_id == lead_id).all()
    for m in sms_messages:
        # Look up sender name lazily
        sender = db.query(User).filter(User.id == m.sender_id).first()
        sender_name = f"{sender.first_name} {sender.last_name}".strip() if sender else "Advisor"
        events.append({
            "id": f"sms-out-{m.id}",
            "type": "sms_sent",
            "ts": _fmt(m.sent_at),
            "label": f"SMS sent by {sender_name}",
            "body": m.body,
            "meta": {
                "twilio_status": m.twilio_status,
                "sender_id": m.sender_id,
            },
        })

    # ── Inbound SMS (Reply) ──────────────────────────────────────────────
    replies = db.query(Reply).filter(Reply.lead_id == lead_id).all()
    for r in replies:
        label = "Reply received"
        if r.is_hot:
            label = "🔥 Hot reply received"
        events.append({
            "id": f"reply-{r.id}",
            "type": "sms_reply",
            "ts": _fmt(r.received_at),
            "label": label,
            "body": r.body,
            "meta": {
                "is_hot": r.is_hot,
                "hot_reason": r.hot_reason,
                "classification": str(r.classification) if r.classification else None,
                "reviewed_at": _fmt(r.reviewed_at),
                "source": r.source,
            },
        })

    # ── Outbound Email (EmailMessage) ────────────────────────────────────
    emails = db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id).all()
    for e in emails:
        sender = db.query(User).filter(User.id == e.sender_id).first()
        sender_name = f"{sender.first_name} {sender.last_name}".strip() if sender else "Advisor"
        events.append({
            "id": f"email-{e.id}",
            "type": "email_sent",
            "ts": _fmt(e.sent_at),
            "label": f"Email sent by {sender_name}",
            "body": e.subject,
            "meta": {
                "status": e.status,
                "provider_message_id": e.provider_message_id,
            },
        })

    # ── Booking Links ────────────────────────────────────────────────────
    bookings = db.query(BookingLink).filter(BookingLink.lead_id == lead_id).all()
    for b in bookings:
        # created event
        status_label = {
            "pending": "Booking link sent",
            "booked": "Appointment booked",
            "confirmed": "Appointment confirmed",
            "expired": "Booking link expired",
            "cancelled": "Appointment cancelled",
        }.get(b.status, f"Booking — {b.status}")

        events.append({
            "id": f"booking-{b.id}",
            "type": f"booking_{b.status}",
            "ts": _fmt(b.booked_time or b.created_at),
            "label": status_label,
            "body": _fmt(b.booked_time) if b.booked_time else "No time selected yet",
            "meta": {
                "status": b.status,
                "created_at": _fmt(b.created_at),
                "booked_time": _fmt(b.booked_time),
                "calendar_event_id": b.calendar_event_id,
            },
        })

    # ── Lead Outcomes ────────────────────────────────────────────────────
    outcomes = db.query(LeadOutcome).filter(LeadOutcome.lead_id == lead_id).all()
    for o in outcomes:
        recorder = db.query(User).filter(User.id == o.recorded_by_id).first()
        recorder_name = f"{recorder.first_name} {recorder.last_name}".strip() if recorder else "Advisor"
        sale_note = ""
        if o.resulted_in_sale:
            sale_note = f" · Sale: {o.sale_items or 'recorded'}"
            if o.sale_amount:
                sale_note += f" (${o.sale_amount})"
        events.append({
            "id": f"outcome-{o.id}",
            "type": "outcome_recorded",
            "ts": _fmt(o.created_at),
            "label": f"Outcome recorded by {recorder_name}",
            "body": (o.notes or "No notes") + sale_note,
            "meta": {
                "resulted_in_sale": o.resulted_in_sale,
                "sale_items": o.sale_items,
                "sale_amount": o.sale_amount,
                "appointment_date": _fmt(o.appointment_date),
                "has_funeral_arrangement": o.has_funeral_arrangement,
                "has_cemetery_property": o.has_cemetery_property,
                "has_marker": o.has_marker,
            },
        })

    # ── Cadence State ────────────────────────────────────────────────────
    cadence = db.query(CadenceState).filter(CadenceState.lead_id == lead_id).first()
    if cadence:
        events.append({
            "id": f"cadence-{cadence.id}",
            "type": "cadence_started",
            "ts": _fmt(cadence.cadence_started_at),
            "label": "Cadence started",
            "body": f"Touch {cadence.current_touch_number} of sequence · Status: {cadence.status}",
            "meta": {
                "status": cadence.status,
                "current_touch_number": cadence.current_touch_number,
                "next_touch_due_at": _fmt(cadence.next_touch_due_at),
                "last_touch_sent_at": _fmt(cadence.last_touch_sent_at),
                "completed_at": _fmt(cadence.completed_at),
            },
        })
        if cadence.completed_at:
            events.append({
                "id": f"cadence-done-{cadence.id}",
                "type": "cadence_completed",
                "ts": _fmt(cadence.completed_at),
                "label": "Cadence completed",
                "body": f"Finished {cadence.current_touch_number} touches",
                "meta": {},
            })

    # ── Status changes surfaced via current lead status ──────────────────
    if lead.status == "booked":
        pass  # covered by booking events above
    if lead.status == "dnc":
        # We don't have a dedicated timestamp for when DNC was set —
        # surface it as a synthetic event at updated_at if available
        updated = getattr(lead, "updated_at", None)
        if updated:
            events.append({
                "id": f"lead-dnc-{lead.id}",
                "type": "dnc_flagged",
                "ts": _fmt(updated),
                "label": "⛔ Marked Do Not Contact",
                "body": "This lead is on the DNC list.",
                "meta": {},
            })

    # ── Sort ascending by timestamp (None sorts to top) ─────────────────
    def sort_key(e):
        ts = e.get("ts")
        if ts is None:
            return ""
        return ts

    events.sort(key=sort_key)

    return {
        "lead_id": lead_id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
        "event_count": len(events),
        "events": events,
    }
