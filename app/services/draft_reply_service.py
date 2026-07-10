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

DRAFT_REPLY_PROMPT = """You are drafting a short SMS reply from a service business advisor to a lead.

Advisor: {advisor_name}
Tone instruction: {tone_instruction}
Lead type: {lead_type}
AI direction: {ai_direction}

Rules:
- Respond with ONLY JSON, no markdown and no preamble.
- JSON shape: {{"suggested_reply": "..."}}
- Keep it under 320 characters.
- Sound human and respectful.
- Sign as {advisor_name} if the message includes a sign-off.
- Do not claim anything not shown in the conversation.
- If AI direction is provided, use it to shape the message purpose and context.
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


def draft_reply(db: Session, lead: Lead, advisor: User, tone: str = "warm", ai_direction: str = None) -> dict[str, Any]:
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
        lead_type=lead.message_track or lead.tier or "not specified",
        ai_direction=ai_direction or "general reconnection outreach",
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


# ── Email draft with talking points + 3 options ───────────────────────────────

EMAIL_DRAFT_PROMPT = """You are helping a service business advisor write a cold outreach email to a lead.

Advisor: {advisor_name}
Organization: {org_name}
Tone: {tone_desc}
Lead type / context: {lead_type}
AI direction: {ai_direction}

Lead profile:
- Name: {first_name} {last_name}
- Tier: {tier}
- Source year: {source_year}
- Last action on file: {last_action}
- Last contact date: {last_contact}
- Status reason: {status_reason}
- Notes: {notes}

Rules:
- This is likely a COLD contact — they may not remember us
- Use the lead's history (last action, source year, status reason) to make it personal and relevant
- Keep emails under 150 words
- Sound like a real person, not a mass marketing template
- Each option should have a different angle/hook
- Never be pushy or desperate
- Always give them an easy out

Respond ONLY with valid JSON, no markdown:
{{
  "talking_points": ["Point 1 about this specific lead", "Point 2", "Point 3"],
  "options": [
    {{
      "label": "Warm & personal",
      "subject": "Subject line here",
      "body": "Full email body here"
    }},
    {{
      "label": "Direct & clear",
      "subject": "Subject line here",
      "body": "Full email body here"
    }},
    {{
      "label": "Value-first",
      "subject": "Subject line here",
      "body": "Full email body here"
    }}
  ]
}}"""


def draft_email_options(
    db,
    lead,
    advisor,
    tone: str = "warm",
    ai_direction: str = None,
) -> dict:
    """
    Generate talking points + 3 email draft options for a lead.
    Uses full lead context (tier, source year, last action, etc.) to
    personalize the message rather than using a generic template.
    """
    from openai import OpenAI
    import json, os

    tone_map = {
        "cold": "soft, low-pressure, just a gentle introduction",
        "warm": "friendly and inviting, suggest a conversation without being pushy",
        "hot": "direct and confident, clear call to action",
        "urgent": "brief and to the point, create gentle urgency",
    }
    tone_desc = tone_map.get(tone, tone_map["warm"])

    # Pull org name
    try:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
        org_name = org.name if org else "our organization"
    except Exception:
        org_name = "our organization"

    # Format last contact date
    last_contact = "unknown"
    if lead.last_contact_date:
        try:
            last_contact = lead.last_contact_date.strftime("%B %Y")
        except Exception:
            last_contact = str(lead.last_contact_date)

    prompt = EMAIL_DRAFT_PROMPT.format(
        advisor_name=advisor.full_name or "your advisor",
        org_name=org_name,
        tone_desc=tone_desc,
        lead_type=lead.message_track or lead.tier or "not specified",
        ai_direction=ai_direction or "general reconnection outreach",
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        tier=lead.tier or "unknown",
        source_year=lead.source_year or "unknown",
        last_action=lead.last_action_raw or "none on file",
        last_contact=last_contact,
        status_reason=lead.status_reason_raw or "none on file",
        notes=(lead.notes or "none")[:200],
    )

    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return {
            "talking_points": result.get("talking_points", []),
            "options": result.get("options", []),
            "lead_context": {
                "tier": lead.tier,
                "source_year": lead.source_year,
                "last_action": lead.last_action_raw,
                "last_contact": last_contact,
            }
        }
    except Exception as e:
        # Fallback — generic options
        first = lead.first_name or "there"
        advisor_name = advisor.full_name or "your advisor"
        return {
            "talking_points": [
                f"{first_name} was last contacted in {last_contact}" if last_contact != "unknown" else f"Re-engaging {first} after a gap",
                f"Tier: {lead.tier or 'unassigned'} — tailor the message to their situation",
                "Keep it short, personal, and low pressure",
            ],
            "options": [
                {
                    "label": "Warm & personal",
                    "subject": f"Checking in, {first}",
                    "body": f"Hi {first},\n\nThis is {advisor_name} with {org_name}. I wanted to personally reach out and see if there's anything I can help you with.\n\nNo pressure at all — just here when you're ready.\n\n{advisor_name}",
                },
                {
                    "label": "Direct & clear",
                    "subject": f"Quick question, {first}",
                    "body": f"Hi {first},\n\n{advisor_name} here from {org_name}. I had a chance to look at your file and wanted to connect.\n\nWould you have 10 minutes this week?\n\n{advisor_name}",
                },
                {
                    "label": "Value-first",
                    "subject": f"Something I think could help, {first}",
                    "body": f"Hi {first},\n\nI work with families at {org_name} and I've found that a short conversation can save a lot of stress later.\n\nI'd love to share some options with you — no obligation.\n\n{advisor_name}",
                },
            ],
            "lead_context": {
                "tier": lead.tier,
                "source_year": lead.source_year,
                "last_action": lead.last_action_raw,
                "last_contact": last_contact,
            }
        }
