"""
AI-drafted one-click reply suggestions for the Lead Detail conversation view.

This follows the same lazy OpenAI client + safe fallback pattern as the existing
AI services. The endpoint using this service must never fail just because the AI
provider is unavailable; advisors still need a usable reply draft.
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


DRAFT_REPLY_PROMPT = """You are drafting a short SMS reply from a cemetery/funeral-home sales advisor to a lead.

Advisor sending this message: {advisor_name}

{tone_instruction}

Rules:
- Respond with ONLY JSON, no markdown and no preamble.
- JSON shape: {{"suggested_reply": "..."}}
- Keep it under 320 characters when possible.
- Always respectful and appointment-focused, regardless of tone.
- Sign as {advisor_name} if the message includes a closing/sign-off - don't invent a different name or leave a placeholder.
- Do not claim anything not shown in the conversation.
- Include this booking link exactly once if it is relevant and not already in your draft: {booking_url}

Lead:
- First name: {first_name}
- Last name: {last_name}
- Phone: {phone}

Most recent inbound reply:
{latest_reply}

Conversation history, oldest to newest:
{history}
"""

VALID_TONES = ("soft", "standard", "urgent", "direct")

# Per Mike's explicit request: he wants control over how strongly the
# draft pushes for a follow-up, not just one fixed "polite and gentle"
# voice every time. Each entry here genuinely changes WHAT the model is
# instructed to do, not just a tone-word swap that produces the same
# message with a different adjective - e.g. "direct" actually instructs
# the model to ask for a specific commitment and name a real next step,
# not just to sound more clipped while saying the same thing as "soft."
TONE_GUIDANCE = {
    "soft": (
        "TONE: Soft and gentle. This may be a sensitive, emotional situation - "
        "lead with empathy, give them space, and avoid any pressure to respond "
        "quickly. Phrases like \"whenever you're ready\" or \"no rush at all\" are "
        "appropriate here. Do not push for a specific time or deadline."
    ),
    "standard": (
        "TONE: Standard and professional. Warm but business-appropriate. Ask a "
        "clear, simple question to move the conversation forward (e.g. what time "
        "works for a quick call), without urgency language and without being pushy."
    ),
    "urgent": (
        "TONE: Urgent. Convey that time matters here - e.g. limited availability, "
        "a pricing window, or that today/tomorrow would be ideal - while staying "
        "respectful and never sounding desperate or pressuring. Ask for a specific "
        "commitment (a day or time) rather than leaving it fully open-ended."
    ),
    "direct": (
        "TONE: Direct. Skip the soft framing entirely. Ask plainly for a specific "
        "next step and a specific time, the way a confident closer would - e.g. "
        "\"Does tomorrow at 2pm work, or would Thursday be better?\" rather than "
        "\"let me know what works for you.\" Still respectful and never rude, but "
        "give them a real decision to make right now, not an open invitation to "
        "respond whenever."
    ),
}


def _booking_url(token: str) -> str:
    return f"{BOOKING_BASE_URL}/book/{token}"


def get_or_create_booking_link(db: Session, lead: Lead, advisor: User) -> BookingLink:
    existing = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead.id)
        .order_by(BookingLink.created_at.desc())
        .first()
    )
    if existing:
        return existing
    return create_booking_link(db, lead, advisor)


def _conversation_history(db: Session, lead: Lead) -> tuple[list[dict[str, Any]], Reply | None]:
    messages = db.query(Message).filter(Message.lead_id == lead.id).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead.id).all()

    events: list[dict[str, Any]] = []
    for message in messages:
        events.append({
            "type": "advisor",
            "body": message.body,
            "timestamp": message.sent_at,
        })
    for reply in replies:
        events.append({
            "type": "lead",
            "body": reply.body,
            "timestamp": reply.received_at,
        })

    events.sort(key=lambda item: item.get("timestamp") or datetime.min)
    latest_reply = max(replies, key=lambda reply: reply.received_at or datetime.min, default=None)
    return events, latest_reply


FALLBACK_TEMPLATES = {
    "soft": "Hi {name}, thanks so much for your reply. Whenever you're ready, I'm here to help - no rush at all. You can pick a time here if that's easier: {booking_url} - {advisor_name}",
    "standard": "Hi {name}, thanks for your reply. I can help with that. When works for a quick call or file review? You can also pick a time here: {booking_url} - {advisor_name}",
    "urgent": "Hi {name}, thanks for getting back to me - I want to make sure we get this taken care of soon. Does today or tomorrow work for a quick call? You can also grab a time here: {booking_url} - {advisor_name}",
    "direct": "Hi {name}, thanks for the reply. Let's lock in a time - does tomorrow work, or would later this week be better? Pick a time here: {booking_url} - {advisor_name}",
}


def _fallback_reply(lead: Lead, advisor: User, booking_url: str, tone: str = "standard") -> str:
    name = lead.first_name or "there"
    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"
    template = FALLBACK_TEMPLATES.get(tone, FALLBACK_TEMPLATES["standard"])
    return template.format(name=name, booking_url=booking_url, advisor_name=advisor_name)


def _ensure_booking_link_in_text(text: str, lead: Lead, advisor: User, booking_url: str, tone: str = "standard") -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return _fallback_reply(lead, advisor, booking_url, tone)
    if booking_url in cleaned:
        return cleaned
    return f"{cleaned}\n\nYou can also pick a time here: {booking_url}"


def _safe_parse_json(raw: str) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Draft reply response was not a JSON object")
    return parsed


def draft_reply(db: Session, lead: Lead, advisor: User, tone: str = "standard") -> dict[str, Any]:
    if tone not in VALID_TONES:
        tone = "standard"  # defensive default - the router already validates this, but a service function should never trust its caller blindly

    booking = get_or_create_booking_link(db, lead, advisor)
    booking_url = _booking_url(booking.token)
    history, latest_reply = _conversation_history(db, lead)

    history_text = "\n".join(
        f"{item['type']}: {item['body']}" for item in history[-12:]
    ) or "No prior conversation."
    latest_reply_text = latest_reply.body if latest_reply else "No inbound reply yet."

    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"
    tone_instruction = TONE_GUIDANCE.get(tone, TONE_GUIDANCE["standard"])

    prompt = DRAFT_REPLY_PROMPT.format(
        advisor_name=advisor_name,
        tone_instruction=tone_instruction,
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
        suggested = _ensure_booking_link_in_text(parsed.get("suggested_reply", ""), lead, advisor, booking_url, tone)
        source = "ai"
    except Exception:
        suggested = _fallback_reply(lead, advisor, booking_url, tone)
        source = "fallback"

    return {
        "suggested_reply": suggested,
        "booking_url": booking_url,
        "booking_link_id": booking.id,
        "source": source,
        "tone": tone,
    }
