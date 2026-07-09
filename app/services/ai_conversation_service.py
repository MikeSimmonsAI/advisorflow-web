"""
AI Auto-Conversation Service
Reads the latest inbound reply for a lead and generates an appropriate
outbound response. The advisor can either approve before send or enable
auto-send mode.

This is NOT a background daemon — it runs on-demand when:
1. An advisor clicks "Auto-reply selected" in the UI
2. A new inbound reply arrives via Twilio webhook (future hook)

The service is deliberately simple: one lead at a time, one reply at a time.
It stops automatically when the lead books.
"""

import json
import os
from datetime import datetime
from typing import Any

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.models import Lead, Message, Reply, User, BookingLink
from app.services.sms_service import BOOKING_BASE_URL, create_booking_link

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


AUTO_CONV_PROMPT = """You are an AI assistant helping a cemetery/funeral home advisor manage SMS conversations with leads.

Your job: read the conversation history and generate the single best next outbound SMS message to move the lead toward booking an appointment.

Advisor: {advisor_name}
Tone: {tone_instruction}

Rules:
- Respond ONLY with JSON: {{"reply": "...", "should_stop": false, "reason": "..."}}
- "reply" is the SMS text to send (under 320 characters).
- "should_stop" is true ONLY if the lead has already booked, said they're not interested, or sent a STOP/DNC keyword.
- "reason" is a 1-sentence explanation of your choice.
- Sound human. Never reveal you are AI.
- Include the booking link once if not already in conversation: {booking_url}

Lead name: {first_name} {last_name}

Conversation history (oldest to newest):
{history}

Latest inbound message:
{latest_inbound}
"""

TONE_MAP = {
    "cold": "Soft and low-pressure. Just keeping the door open.",
    "warm": "Friendly and conversational. Gently suggest meeting.",
    "hot": "Direct and confident. Ask for the appointment clearly.",
    "urgent": "Brief and urgent. Make a specific time-sensitive ask.",
}


def _get_history(db: Session, lead: Lead) -> tuple[list, str]:
    messages = db.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at.asc()).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead.id).order_by(Reply.received_at.asc()).all()

    events = []
    for m in messages:
        events.append({"dir": "out", "body": m.body, "ts": m.sent_at})
    for r in replies:
        events.append({"dir": "in", "body": r.body, "ts": r.received_at})
    events.sort(key=lambda e: e["ts"] or datetime.min)

    latest_inbound = ""
    for e in reversed(events):
        if e["dir"] == "in":
            latest_inbound = e["body"]
            break

    history_text = "\n".join(f"{'Advisor' if e['dir'] == 'out' else 'Lead'}: {e['body']}" for e in events[-10:])
    return history_text, latest_inbound


def _get_booking_url(db: Session, lead: Lead, advisor: User) -> str:
    existing = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead.id, BookingLink.status == "pending")
        .order_by(BookingLink.created_at.desc())
        .first()
    )
    link = existing or create_booking_link(db, lead, advisor)
    return f"{BOOKING_BASE_URL}/book/{link.token}"


def generate_auto_reply(
    db: Session,
    lead: Lead,
    advisor: User,
    tone: str = "warm",
) -> dict[str, Any]:
    """
    Generates an AI reply for one lead. Returns:
    {
      "reply": str,
      "should_stop": bool,
      "reason": str,
      "source": "ai" | "fallback",
      "booking_url": str,
    }
    """
    if lead.status in ("booked", "dnc") or lead.is_duplicate:
        return {"reply": "", "should_stop": True, "reason": "Lead is booked, DNC, or duplicate.", "source": "skip", "booking_url": ""}

    tone = tone if tone in TONE_MAP else "warm"
    booking_url = _get_booking_url(db, lead, advisor)
    history_text, latest_inbound = _get_history(db, lead)
    advisor_name = advisor.full_name or "your advisor"

    prompt = AUTO_CONV_PROMPT.format(
        advisor_name=advisor_name,
        tone_instruction=TONE_MAP[tone],
        booking_url=booking_url,
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        history=history_text or "No prior conversation.",
        latest_inbound=latest_inbound or "No inbound reply yet.",
    )

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        try:
            data = json.loads(raw)
        except Exception:
            clean = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(clean)
        return {
            "reply": data.get("reply", ""),
            "should_stop": bool(data.get("should_stop", False)),
            "reason": data.get("reason", ""),
            "source": "ai",
            "booking_url": booking_url,
        }
    except Exception:
        fallback = f"Hi {lead.first_name or 'there'}, just following up. Happy to answer any questions — here's my booking link: {booking_url}"
        return {
            "reply": fallback,
            "should_stop": False,
            "reason": "AI unavailable, using fallback.",
            "source": "fallback",
            "booking_url": booking_url,
        }
