"""
ONE NORMALIZED VIEW OF EVERYTHING THAT WAS EVER SAID TO A FAMILY.

A READ MODEL, NOT A NEW WRITE SYSTEM. Nothing here stores anything. It reads
the tables that a provider call, a webhook or a scheduler already writes, and
projects them into one shape sorted by time. That is a deliberate choice over
the alternative - a new authoritative `communication_events` table that every
sender dual-writes - for two reasons:

  * Every existing row would be absent from it. There is no backfill that can
    invent provenance for messages sent before the column existed, so a new
    write table would answer "what happened to this family" with a history
    that starts the day it shipped.
  * The platform already has four tables claiming to be that record
    (`messages`, `email_messages`, `ai_communications`, `auto_send_queue`).
    A fifth is not consolidation.

If a single write model is ever built, this module is the contract it has to
satisfy, and every caller keeps working.

WHAT WAS WRONG

`/leads/{id}/timeline` took the 200 most recent rows per channel and had NO
offset, cursor or page parameter on its signature. Past 200 SMS - which a
nine-touch cadence plus bulk sends reaches - the older ones were unreachable by
any request the API could express. That is the literal "can't pull full
history".

It also unioned five sources while `/leads/{id}/activity`, rendered on the same
page, unioned seven and scoped them differently. Neither included cadence step
history or queued-but-unsent items, because until recently neither existed.

PAGINATION IS A CURSOR, NOT AN OFFSET. Events come from several tables merged
in memory, so an OFFSET would re-read and re-merge everything skipped, and a
row arriving mid-scroll would shift the window. `before` is a timestamp: give
back the newest events older than it, and the page boundary is stable no
matter what arrives.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.models import (
    BookingLink, CadenceTouchLog, EmailMessage, LeadOutcome, Message, Reply,
    VoiceCall,
)

# Event kinds. Deliberately coarse: a consumer wants to render a bubble, a
# system note or a call, and the `channel` says which pipe it used.
OUTBOUND = "outbound"
INBOUND = "inbound"
SYSTEM = "system"

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _event(kind, channel, ts, *, id, body=None, subject=None, status=None,
           source=None, actor_user_id=None, sender_id=None, meta=None):
    return {
        "id": id,
        "kind": kind,
        "channel": channel,
        "timestamp": ts,
        "body": body,
        "subject": subject,
        "status": status,
        # Who caused it and which automation, where the row knows. NULL is a
        # real answer meaning the row predates attribution - never a guess.
        "send_source": source,
        "sent_by_user_id": actor_user_id,
        "sender_id": sender_id,
        "meta": meta or {},
    }


def _before(query, column, before):
    return query.filter(column < before) if before else query


def fetch(db: Session, lead_id: str, *, limit: int = DEFAULT_LIMIT,
          before: Optional[datetime] = None,
          channels: Optional[List[str]] = None) -> Dict[str, Any]:
    """The newest `limit` events for one lead, older than `before`.

    ORG SCOPE IS NOT THIS FUNCTION'S JOB and it deliberately does not guess at
    one. Every caller resolves the lead through lead_scope first; a read model
    that re-derived authorization from a lead id would be a fifth copy of a
    rule that already has one home.
    """
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    want = None if not channels else set(channels)
    # Over-fetch per source: after merging, only `limit` survive, and a source
    # that dominates the window must not starve the others out of the page.
    per_source = limit + 1

    events: List[Dict[str, Any]] = []

    if want is None or "sms" in want:
        from app.services.message_state import describe as _describe
        rows = _before(db.query(Message).filter(Message.lead_id == lead_id),
                       Message.sent_at, before) \
            .order_by(desc(Message.sent_at)).limit(per_source).all()
        for m in rows:
            events.append(_event(
                OUTBOUND, "sms", m.sent_at, id=m.id, body=m.body,
                status=(m.send_state or m.delivery_status or m.twilio_status),
                source=getattr(m, "send_source", None),
                actor_user_id=getattr(m, "sent_by_user_id", None),
                sender_id=m.sender_id,
                meta={"delivery": _describe(m)}))

    if want is None or "email" in want:
        rows = _before(db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id),
                       EmailMessage.sent_at, before) \
            .order_by(desc(EmailMessage.sent_at)).limit(per_source).all()
        for e in rows:
            events.append(_event(
                OUTBOUND, "email", e.sent_at, id=e.id, subject=e.subject,
                body=e.body_html, status=e.status,
                source=getattr(e, "send_source", None),
                actor_user_id=getattr(e, "sent_by_user_id", None),
                sender_id=e.sender_id,
                meta={"provider_message_id": e.provider_message_id,
                      "opened_at": getattr(e, "opened_at", None),
                      "click_count": getattr(e, "click_count", 0)}))

    # INBOUND. `replies` carries both SMS and email inbound - its own `source`
    # column says which - so one query covers both directions of both channels.
    rows = _before(db.query(Reply).filter(Reply.lead_id == lead_id),
                   Reply.received_at, before) \
        .order_by(desc(Reply.received_at)).limit(per_source).all()
    for r in rows:
        channel = (r.source or "sms")
        if want is not None and channel not in want:
            continue
        events.append(_event(
            INBOUND, channel, r.received_at, id=r.id, body=r.body,
            status=r.classification,
            meta={"reviewed_at": getattr(r, "reviewed_at", None)}))

    if want is None or "voice" in want:
        rows = _before(db.query(VoiceCall).filter(VoiceCall.lead_id == lead_id),
                       VoiceCall.created_at, before) \
            .order_by(desc(VoiceCall.created_at)).limit(per_source).all()
        for c in rows:
            events.append(_event(
                OUTBOUND if (c.direction or "outbound") == "outbound" else INBOUND,
                "voice", c.created_at, id=c.id, status=c.status,
                body=c.summary or c.transcript,
                meta={"outcome": c.outcome, "duration_seconds": c.duration_seconds,
                      "recording_url": c.recording_url,
                      "provider_call_id": c.provider_call_id,
                      "call_number": c.call_number}))

    # CADENCE STEPS. New, and the reason this module exists as much as the
    # pagination is: a failed or suppressed touch produced no message row, so
    # it could not appear in any history assembled from messages alone.
    if want is None or "cadence" in want:
        rows = _before(
            db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id == lead_id),
            CadenceTouchLog.created_at, before) \
            .order_by(desc(CadenceTouchLog.created_at)).limit(per_source).all()
        for t in rows:
            events.append(_event(
                SYSTEM, "cadence", t.attempted_at or t.created_at, id=t.id,
                body=t.body_preview, status=t.outcome,
                meta={"touch_number": t.touch_number, "attempt": t.attempt_seq,
                      "reason": t.reason, "scheduled_for": t.scheduled_for,
                      "provider_error_code": t.provider_error_code,
                      "provider_error_message": t.provider_error_message,
                      "message_id": t.message_id}))

    # APPOINTMENTS AND OUTCOMES. Not communication, but the events a history is
    # read to explain - "they booked" is the answer to "why did the cadence
    # stop".
    if want is None or "appointment" in want:
        rows = _before(db.query(BookingLink).filter(BookingLink.lead_id == lead_id),
                       BookingLink.created_at, before) \
            .order_by(desc(BookingLink.created_at)).limit(per_source).all()
        for b in rows:
            events.append(_event(
                SYSTEM, "appointment", b.created_at, id=b.id, status=b.status,
                body=None,
                meta={"booked_time": b.booked_time,
                      "calendar_event_id": b.calendar_event_id,
                      "expires_at": b.expires_at}))

        rows = _before(db.query(LeadOutcome).filter(LeadOutcome.lead_id == lead_id),
                       LeadOutcome.created_at, before) \
            .order_by(desc(LeadOutcome.created_at)).limit(per_source).all()
        for o in rows:
            events.append(_event(
                SYSTEM, "outcome", o.created_at, id=o.id,
                body=getattr(o, "notes", None),
                meta={"recorded_by_id": getattr(o, "recorded_by_id", None)}))

    # Newest first, with NULL timestamps last rather than crashing the sort.
    events.sort(key=lambda e: (e["timestamp"] is None,
                               e["timestamp"] or datetime.min), reverse=True)
    # `reverse=True` would float the NULL group to the top, so pull it back.
    dated = [e for e in events if e["timestamp"] is not None]
    undated = [e for e in events if e["timestamp"] is None]
    ordered = dated + undated

    page = ordered[:limit]
    has_more = len(ordered) > limit
    next_before = None
    if has_more:
        last = page[-1]["timestamp"] if page else None
        next_before = last

    return {
        "lead_id": lead_id,
        "events": page,
        "has_more": has_more,
        "next_before": next_before,
        "limit": limit,
        "counts": _counts(page),
    }


def _counts(events) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in events:
        out[e["channel"]] = out.get(e["channel"], 0) + 1
    return out
