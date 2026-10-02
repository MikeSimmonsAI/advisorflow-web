"""One lead's conversation, across every channel it happened on, as one ordered list.

SOURCES (each read with the lead's OWN organization in the filter):
    replies          inbound SMS / email (Reply.source)
    messages         outbound SMS (Message.send_source says who/what sent it)
    email_messages   outbound email
    lead_notes       staff notes - shown on the timeline, NOT read as customer speech
    evosense_messages  wholesale seller conversation before/after promotion

Identity continuity is by LEAD, never by matching names or numbers across
leads: two records are one conversation only when the platform already made
them one lead. Consent stays per channel - an email reply never makes SMS
allowed (see decide.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import EmailMessage, Lead, Message, Reply

_TAG = re.compile(r"<[^>]+>")
_QUOTED = re.compile(r"(\n|^)(on .{3,80} wrote:|-----original message-----|from: .+\nsent: ).*", re.I | re.S)

AUTOMATED_SOURCES = {"cadence", "ai_conversation", "pipeline_auto_reply", "appointment_followup",
                     "voice_booking_link", "ai_employee", "wholesale_program", "campaign", "bulk_ai",
                     "auto_send", "demo"}


@dataclass
class Event:
    at: datetime
    direction: str            # inbound | outbound | note
    channel: str              # sms | email | note | web
    text: str
    source_type: str          # reply | message | email | note | evosense
    source_id: str
    actor: str = "customer"   # customer | human | automation | staff_note


def strip_html(html: Optional[str]) -> str:
    text = unescape(_TAG.sub(" ", (html or "").replace("<br>", "\n").replace("<br/>", "\n")
                             .replace("</p>", "\n")))
    text = _QUOTED.sub("", text)
    return " ".join(text.split())


def load_events(db: Session, lead: Lead, since: Optional[datetime] = None,
                limit: int = 400) -> List[Event]:
    org_id = lead.organization_id
    out: List[Event] = []

    q = db.query(Reply).filter(Reply.lead_id == lead.id)
    if since is not None:
        q = q.filter(Reply.received_at >= since)
    for r in q.order_by(Reply.received_at.desc()).limit(limit).all():
        out.append(Event(r.received_at or datetime.utcnow(), "inbound", (r.source or "sms").lower(),
                         r.body or "", "reply", r.id, "customer"))

    q = db.query(Message).filter(Message.lead_id == lead.id)
    if since is not None:
        q = q.filter(Message.sent_at >= since)
    for m in q.order_by(Message.sent_at.desc()).limit(limit).all():
        src = (getattr(m, "send_source", None) or "").lower()
        out.append(Event(m.sent_at or datetime.utcnow(), "outbound", "sms", m.body or "", "message", m.id,
                         "automation" if src in AUTOMATED_SOURCES else "human"))

    q = db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id)
    if since is not None:
        q = q.filter(EmailMessage.sent_at >= since)
    for e in q.order_by(EmailMessage.sent_at.desc()).limit(limit).all():
        src = (getattr(e, "send_source", None) or "").lower()
        out.append(Event(e.sent_at or datetime.utcnow(), "outbound", "email",
                         ((e.subject or "") + ". " + strip_html(e.body_html)).strip(". "),
                         "email", e.id, "automation" if src in AUTOMATED_SOURCES else "human"))

    try:
        from app.models.work_models import LeadNote
        q = db.query(LeadNote).filter(LeadNote.lead_id == lead.id, LeadNote.organization_id == org_id)
        if since is not None:
            q = q.filter(LeadNote.created_at >= since)
        for n in q.order_by(LeadNote.created_at.desc()).limit(limit).all():
            out.append(Event(n.created_at or datetime.utcnow(), "note", "note", n.body or "", "note", n.id,
                             "staff_note"))
    except Exception:                                            # noqa: BLE001
        pass

    try:
        from app.models.evosense_models import EvoSenseEngagement, EvoSenseMessage
        eng_ids = [e.id for e in db.query(EvoSenseEngagement.id).filter(
            EvoSenseEngagement.organization_id == org_id, EvoSenseEngagement.lead_id == lead.id).all()]
        if eng_ids:
            q = db.query(EvoSenseMessage).filter(EvoSenseMessage.organization_id == org_id,
                                                 EvoSenseMessage.engagement_id.in_(eng_ids))
            if since is not None:
                q = q.filter(EvoSenseMessage.created_at >= since)
            for m in q.order_by(EvoSenseMessage.created_at.desc()).limit(limit).all():
                inbound = (m.direction or "") == "inbound"
                out.append(Event(m.created_at or datetime.utcnow(), "inbound" if inbound else "outbound",
                                 (m.channel or "sms"), m.body or "", "evosense", m.id,
                                 "customer" if inbound else ("human" if m.delivery == "manual_entry" else "automation")))
    except Exception:                                            # noqa: BLE001
        pass

    out.sort(key=lambda e: (e.at, 0 if e.direction == "inbound" else 1, e.source_id))
    return out[-limit:]
