"""
AI-drafted reply suggestions for Lead Detail.
Now supports a tone parameter: cold, warm, hot, urgent.
"""

import json
import os
from typing import Any
from datetime import datetime

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, Message, Reply, User
from app.services.sms_service import BOOKING_BASE_URL, create_booking_link

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


TONE_INSTRUCTIONS = {
    "cold": "Use a soft, low-pressure, friendly tone. This is an early touch — don't push for an appointment yet. Just introduce yourself and leave the door open.",
    "warm": "Use a warm, conversational tone. Express genuine interest and suggest a meeting, but don't be pushy. A light call-to-action is appropriate.",
    "hot": "Be direct and confident. The lead has shown interest — match their energy, confirm next steps, and clearly ask for the appointment.",
    "urgent": "Be brief and urgent. Time is a factor. Get straight to the point, make a specific ask, and create a sense of gentle urgency without being aggressive.",
}

DRAFT_REPLY_PROMPT = """You are drafting a short SMS reply from a cemetery/funeral-home sales advisor to a lead.

Advisor: {advisor_name}
Tone instruction: {tone_instruction}

Rules:
- Respond with ONLY JSON, no markdown and no preamble.
- JSON shape: {{"suggested_reply": "..."}}
- Keep it under 320 characters.
- Sound human and respectful.
- Sign as {advisor_name} if the message includes a sign-off.
- Do not claim anything not shown in the conversation.
- Include this booking link exactly once if relevant and not already in your draft: {booking_url}

Lead:
- First name: {first_name}
- Last name: {last_name}
- Phone: {phone}

Most recent inbound reply:
{latest_reply}

Conversation history, oldest to newest:
{history}
"""


def _booking_url(token: str) -> str:
    return f"{BOOKING_BASE_URL}/book/{token}"


def get_or_create_booking_link(db: Session, lead: Lead, advisor: User) -> BookingLink:
    existing = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead.id, BookingLink.status == "pending")
        .order_by(BookingLink.created_at.desc())
        .first()
    )
    if existing:
        return existing
    return create_booking_link(db, lead, advisor)


def _conversation_history(db: Session, lead: Lead):
    messages = db.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at.asc()).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead.id).order_by(Reply.received_at.asc()).all()

    events = []
    for m in messages:
        events.append({"type": "outbound", "body": m.body, "ts": m.sent_at})
    for r in replies:
        events.append({"type": "inbound", "body": r.body, "ts": r.received_at})

    events.sort(key=lambda e: e["ts"] or datetime.min)

    latest_reply = None
    for r in sorted(replies, key=lambda r: r.received_at or datetime.min, reverse=True):
        latest_reply = r
        break

    return events, latest_reply


def _safe_parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        try:
            return json.loads(clean)
        except Exception:
            return {}


def _ensure_booking_link_in_text(text: str, lead: Lead, advisor: User, booking_url: str) -> str:
    if booking_url and booking_url not in text:
        return f"{text.rstrip()} {booking_url}".strip()
    return text


def _fallback_reply(lead: Lead, advisor: User, booking_url: str, tone: str = "warm") -> str:
    name = lead.first_name or "there"
    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"
    if tone == "urgent":
        return f"Hi {name}, I wanted to reach out one more time. Please let me know if you'd like to connect — I have time this week. {booking_url}"
    if tone == "hot":
        return f"Hi {name}, great hearing from you. I'd love to set up a time to talk — here's my booking link: {booking_url}"
    if tone == "cold":
        return f"Hi {name}, this is {advisor_name}. Just wanted to introduce myself and let you know I'm here whenever you're ready. {booking_url}"
    return f"Hi {name}, this is {advisor_name}. I'd love to connect and walk you through your options. {booking_url}"


def draft_reply(db: Session, lead: Lead, advisor: User, tone: str = "warm") -> dict[str, Any]:
    tone = tone if tone in TONE_INSTRUCTIONS else "warm"
    booking = get_or_create_booking_link(db, lead, advisor)
    booking_url = _booking_url(booking.token)
    history, latest_reply = _conversation_history(db, lead)

    history_text = "\n".join(
        f"{item['type']}: {item['body']}" for item in history[-12:]
    ) or "No prior conversation."
    latest_reply_text = latest_reply.body if latest_reply else "No inbound reply yet."
    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"

    prompt = DRAFT_REPLY_PROMPT.format(
        advisor_name=advisor_name,
        tone_instruction=TONE_INSTRUCTIONS[tone],
        booking_url=booking_url,
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        phone=lead.phone or "",
        latest_reply=latest_reply_text,
        history=history_text,
    )

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=220,
        )
        raw = response.choices[0].message.content
        parsed = _safe_parse_json(raw)
        suggested = _ensure_booking_link_in_text(parsed.get("suggested_reply", ""), lead, advisor, booking_url)
        source = "ai"
    except Exception:
        suggested = _fallback_reply(lead, advisor, booking_url, tone)
        source = "fallback"

    return {
        "suggested_reply": suggested,
        "booking_url": booking_url,
        "booking_link_id": booking.id,
        "source": source,
    }
