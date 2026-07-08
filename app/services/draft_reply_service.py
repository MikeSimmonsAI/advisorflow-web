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

Rules:
- Respond with ONLY JSON, no markdown and no preamble.
- JSON shape: {{"suggested_reply": "..."}}
- Keep it under 320 characters when possible.
- Sound human, respectful, and appointment-focused.
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


def _fallback_reply(lead: Lead, advisor: User, booking_url: str) -> str:
    name = lead.first_name or "there"
    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"
    return (
        f"Hi {name}, thanks for your reply. I can help with that. "
        f"When works for a quick call or file review? You can also pick a time here: {booking_url} - {advisor_name}"
    )


def _ensure_booking_link_in_text(text: str, lead: Lead, advisor: User, booking_url: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return _fallback_reply(lead, advisor, booking_url)
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


def draft_reply(db: Session, lead: Lead, advisor: User) -> dict[str, Any]:
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
        suggested = _fallback_reply(lead, advisor, booking_url)
        source = "fallback"

    return {
        "suggested_reply": suggested,
        "booking_url": booking_url,
        "booking_link_id": booking.id,
        "source": source,
    }
