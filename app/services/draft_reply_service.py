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

# WHAT THE MESSAGE MUST ACCOMPLISH — not how it should sound.
#
# Temperature used to change only adjectives. "Cold" produced the same message
# as "warm" in a softer voice, which for a stranger is the wrong message however
# gently it is phrased: someone who has never heard of the business does not
# need a friendlier follow-up, they need an introduction. Each entry below is a
# brief for a different message, and the length budget differs with it, because
# an introduction that has to name a person, name a business, say why the text
# arrived and offer a way to respond does not fit in one 160-character segment.
TONE_STRATEGY = {
    "cold": {
        "goal": (
            "STRATEGY — INTRODUCTION TO A STRANGER.\n"
            "This person does not know the advisor and does not know the business. "
            "The message must:\n"
            "  1. Introduce the advisor BY NAME and say which business they are with.\n"
            "  2. Offer themselves as a resource for the family - not sell anything.\n"
            "  3. Say plainly why the message might be useful to them.\n"
            "  4. State explicitly that there is no pressure and no obligation.\n"
            "  5. End with an easy, optional way to talk (the booking link).\n"
            "It must NOT: imply any prior conversation, contract, enquiry, visit or "
            "relationship; use 'following up', 'checking back', 'as we discussed', "
            "'reaching out again', or anything else that assumes shared history; "
            "reference a file, an account or a record unless the lead data shows one."
        ),
        "max_chars": 330,      # ~2 SMS segments; an introduction needs the room
    },
    "warm": {
        "goal": (
            "STRATEGY — RE-OPEN A CONVERSATION THAT ALREADY EXISTS.\n"
            "There is real prior contact. Reference only what the conversation "
            "history or lead data actually shows, never an invented interaction. "
            "Move it forward with one light, specific suggestion."
        ),
        "max_chars": 250,
    },
    "hot": {
        "goal": (
            "STRATEGY — CONVERT STATED INTEREST INTO A TIME.\n"
            "They have shown interest. Do not re-introduce anyone. Acknowledge what "
            "they said, then ask directly for the appointment and make saying yes "
            "the easiest thing in the message."
        ),
        "max_chars": 200,
    },
    "urgent": {
        "goal": (
            "STRATEGY — ONE SPECIFIC ASK, NOW.\n"
            "Time matters. One sentence of context at most, then the ask. Gentle "
            "urgency, never pressure, and never manufactured scarcity. This is a "
            "funeral and cemetery context: urgency is about the family's need, not "
            "about a deadline we invented."
        ),
        "max_chars": 200,
    },
}

RELATIONSHIP_TYPE_CONTEXT = {
    "cold_lead": (
        "COLD LEAD — NO PRIOR RELATIONSHIP OF ANY KIND. This person has never "
        "spoken to the advisor, has never contacted the business, and may never "
        "have heard of it. Introduce the advisor by name and the business by "
        "name. Offer to be a resource; do not sell. Do not imply a previous "
        "conversation, enquiry, appointment, file or account. Do not say "
        "'following up', 'checking back', 'circling back', 'as we discussed' or "
        "'reaching out again' — there is nothing to follow up on."
    ),
    "warm_lead": (
        "WARM LEAD — Showed prior interest or is a referral. "
        "Some familiarity is appropriate. Soft CTA is fine."
    ),
    "previous_prospect": (
        "PREVIOUS PROSPECT — Was in the pipeline before, didn't close. "
        "They know us. Acknowledge the gap ('it's been a while'). Pick up naturally."
    ),
    "existing_customer": (
        "EXISTING CUSTOMER — Active customer. Full familiarity. "
        "Value-add conversation, not cold outreach. They are valued."
    ),
    "past_customer": (
        "PAST CUSTOMER — Was a customer, relationship lapsed. They know us. "
        "Don't treat them like a stranger. Make reconnecting feel easy."
    ),
    "re_engagement": (
        "RE-ENGAGEMENT — We contacted them before; they went quiet. "
        "They've heard of us. Brief, low-pressure, give them an easy out."
    ),
}

DRAFT_REPLY_PROMPT = """You are drafting a short SMS message from a service business advisor to a lead.

━━━ BINDING CONSTRAINTS — READ THESE FIRST ━━━
{tone_strategy}

Relationship context: {relationship_context}

The advisor's own direction for this message. It OVERRIDES the defaults above
wherever they disagree, and it describes THIS lead specifically — if it says the
lead is cold with no connection to the business, that is the truth about them
regardless of anything else in this prompt:
{ai_direction}
{sample_message_section}
━━━ CONTEXT ━━━
Advisor: {advisor_name}
Organization: {org_name}
Tone: {tone_instruction}
Lead type: {lead_type}
Booking link: {booking_url}

Lead:
- First name: {first_name}
- Last name: {last_name}
- Phone: {phone}

Most recent inbound reply:
{latest_reply}

Conversation history, oldest to newest:
{history}

━━━ RULES ━━━
- Respond with ONLY JSON: {{"suggested_reply": "..."}}
- LENGTH: at most {max_chars} characters. Use the room the strategy needs and no
  more. An introduction to a stranger is allowed to be two segments; a reply to
  someone who already said yes should be one.
- Sound human and respectful. This is a funeral and cemetery context: never
  cheerful, never salesy, never a marketing voice.
- Use ONLY "{advisor_name}" and "{org_name}" when signing or introducing.
- Do not claim anything not shown in conversation or lead data.
- The strategy above defines WHAT the message must accomplish; the tone defines
  only how it sounds. Satisfy the strategy first.
- The relationship context defines what familiarity is appropriate — respect it.
- If a sample message is provided above, use it as the FOUNDATION and fill in variables (name, booking link, etc.). Do NOT rewrite it from scratch.
"""


def _booking_url(db: Session, organization_id: str, token: str) -> str:
    """Branded, resolved per organization.

    Takes the organization rather than reading a module constant: one host for
    every tenant is what put a Vercel domain in front of a funeral home's
    families.
    """
    from app.services.public_identity import booking_url as public_booking_url
    return public_booking_url(db, organization_id, token)


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


def _fallback_reply(lead: Lead, advisor: User, booking_url: str,
                    tone: str = "warm", org_name: str = "") -> str:
    """Used when the model is unavailable. Follows the same strategy.

    The cold variant used to introduce the advisor and stop, naming no business
    at all - so the one message that most needed to say who was texting said the
    least. It now carries the same five things the cold strategy asks for, minus
    the link, which the composer appends.
    """
    name = lead.first_name or "there"
    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"
    where = (" with %s" % org_name) if org_name else ""
    # Do NOT include the booking URL here — the frontend's "Include booking link"
    # checkbox appends it at send time. Including it here causes a double-link.
    if tone == "urgent":
        return (f"Hi {name}, this is {advisor_name}{where}. If your family needs "
                f"arrangements handled soon, I can help today — just let me know a "
                f"time that works.")
    if tone == "hot":
        return (f"Hi {name}, thanks for getting back to me. I'd be glad to set up a "
                f"time to talk — you can pick whatever suits you.")
    if tone == "cold":
        return (f"Hi {name}, this is {advisor_name}{where}. I wanted to introduce "
                f"myself as a resource if you or your family ever have questions "
                f"about cemetery, funeral, cremation or advance planning. There's no "
                f"pressure or obligation — if you'd ever like to talk, you can choose "
                f"a time that works for you.")
    return (f"Hi {name}, this is {advisor_name}{where}. I'd be glad to connect and "
            f"walk you through the options whenever it suits you.")


def draft_reply(
    db: Session,
    lead: Lead,
    advisor: User,
    tone: str = "warm",
    ai_direction: str = None,
    sample_message: str = None,
) -> dict[str, Any]:
    tone = tone if tone in TONE_INSTRUCTIONS else "warm"
    booking = get_or_create_booking_link(db, lead, advisor)
    booking_url = _booking_url(db, lead.organization_id, booking.token)
    history, latest_reply = _conversation_history(db, lead)

    history_text = "\n".join(
        f"{item['type']}: {item['body']}" for item in history[-12:]
    ) or "No prior conversation."
    latest_reply_text = latest_reply.body if latest_reply else "No inbound reply yet."
    advisor_name = advisor.full_name if advisor and advisor.full_name else "your advisor"

    # The name the FAMILY knows the business by, resolved through the same
    # identity the confirmation email and Taffiney's greeting use. `org.name` is
    # the account name and stays as the fallback; a customer trading under a
    # different name would otherwise be introduced by a name nobody uses.
    try:
        from app.models.models import Organization
        from app.services.public_identity import identity_for_org
        org = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
        _ident = identity_for_org(db, str(advisor.organization_id))
        org_name = (_ident.customer_facing_name
                    or (org.name if org else None)
                    or "our organization")
    except Exception:
        org_name = "our organization"

    # Relationship context is the PRIMARY AI guardrail
    rel_type = getattr(lead, "relationship_type", None) or "cold_lead"
    relationship_context = RELATIONSHIP_TYPE_CONTEXT.get(rel_type, RELATIONSHIP_TYPE_CONTEXT["cold_lead"])

    direction = ai_direction.strip() if ai_direction and ai_direction.strip() else "(none — follow relationship context and tone)"

    # If user provided a sample message, use it as the foundation
    sample_section = ""
    if sample_message and sample_message.strip():
        sample_section = (
            f"\nSample message (USE THIS AS YOUR FOUNDATION — fill in the lead's name, "
            f"booking link, and any personalization. Do NOT rewrite it from scratch):\n"
            f"{sample_message.strip()}\n"
        )

    strategy = TONE_STRATEGY.get(tone, TONE_STRATEGY["warm"])
    max_chars = strategy["max_chars"]

    prompt = DRAFT_REPLY_PROMPT.format(
        relationship_context=relationship_context,
        tone_strategy=strategy["goal"],
        max_chars=max_chars,
        advisor_name=advisor_name,
        org_name=org_name,
        tone_instruction=TONE_INSTRUCTIONS[tone],
        lead_type=lead.message_track or lead.tier or "not specified",
        ai_direction=direction,
        sample_message_section=sample_section,
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
            # Was 120, which truncated a cold introduction mid-sentence before
            # the cap below ever saw it. The budget now follows the strategy.
            max_tokens=320,
        )
        raw = response.choices[0].message.content
        parsed = _safe_parse_json(raw)
        # Strip any URLs the AI included — the frontend "Include booking link"
        # checkbox appends the clean link at send time.
        import re as _re
        suggested = _re.sub(r'https?://\S+', '', parsed.get("suggested_reply", "")).strip()
        if not suggested:
            suggested = _fallback_reply(lead, advisor, booking_url, tone, org_name)
        source = "ai"
    except Exception:
        suggested = _fallback_reply(lead, advisor, booking_url, tone, org_name)
        source = "fallback"

    # THE CAP FOLLOWS THE STRATEGY.
    #
    # This was a flat 155 characters for every message. An introduction to a
    # stranger has to name the advisor, name the business, say why the text
    # arrived, say there is no obligation and offer a way to reply - which does
    # not fit, so the draft was cut at the last space before 155 and the advisor
    # was shown a sentence that stopped halfway. That truncation, not the model,
    # is what made cold outreach read as generic and strategically weak.
    if len(suggested) > max_chars:
        cut = suggested[:max_chars]
        # Prefer a sentence boundary; fall back to a word boundary.
        for stop in (". ", "! ", "? "):
            idx = cut.rfind(stop)
            if idx > max_chars * 0.6:
                cut = cut[:idx + 1]
                break
        else:
            cut = cut.rsplit(" ", 1)[0]
        suggested = cut.strip()

    return {
        "suggested_reply": suggested,
        "booking_url": booking_url,
        "booking_link_id": booking.id,
        "source": source,
    }


# ── Email draft with talking points + 3 options ───────────────────────────────

EMAIL_DRAFT_PROMPT = """You are helping a service business advisor write an outreach email to a lead.

━━━ #1 MANDATORY — ADVISOR'S SPECIFIC INSTRUCTION ━━━
The advisor has given you explicit direction. This is your PRIMARY task. Do not write about anything else.
Direction: {ai_direction}

If the direction says "file review and permission form" — write about THAT specifically.
If it says something else entirely — write about THAT. Do NOT fall back to generic relationship content.
{sample_message_section}
━━━ #2 Relationship context (secondary — tone only, do not override direction) ━━━
{relationship_context}

━━━ CONTEXT ━━━
Advisor: {advisor_name}
Organization: {org_name}
Tone: {tone_desc}
Lead type / context: {lead_type}
{offer_hook_line}

Lead profile:
- Name: {first_name} {last_name}
- Tier: {tier}
- Source year: {source_year}
- Last action on file: {last_action}
- Last contact date: {last_contact}
- Status reason: {status_reason}
- Notes: {notes}

━━━ RULES ━━━
- The relationship context above defines EXACTLY how familiar you should sound — respect it strictly.
- Use the lead's history (last action, source year, status reason) to personalize.
- Keep emails under 150 words.
- Sound like a real person, not a mass marketing template.
- Each option should have a different angle/hook.
- Never be pushy or desperate. Always give them an easy out.
- CRITICAL: Use the EXACT advisor name "{advisor_name}" — NEVER write [Your Name], [Name], or any bracket placeholder.
- If a sample message is provided above, use it as the FOUNDATION for at least one option — fill in variables (name, etc.). Do NOT rewrite it from scratch.

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
    sample_message: str = None,
) -> dict:
    """
    Generate talking points + 3 email draft options for a lead.
    Uses full lead context (tier, source year, last action, etc.) to
    personalize the message rather than using a generic template.
    Respects relationship_type as the primary AI constraint.
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

    # Relationship context — primary AI guardrail
    rel_type = getattr(lead, "relationship_type", None) or "cold_lead"
    relationship_context = RELATIONSHIP_TYPE_CONTEXT.get(rel_type, RELATIONSHIP_TYPE_CONTEXT["cold_lead"])

    direction = ai_direction.strip() if ai_direction and ai_direction.strip() else "(none — follow relationship context and tone)"

    # Sample message injection
    sample_section = ""
    if sample_message and sample_message.strip():
        sample_section = (
            f"\nSample message (USE THIS AS YOUR FOUNDATION for at least one option — "
            f"fill in the lead's name and any personalization. Do NOT rewrite it from scratch):\n"
            f"{sample_message.strip()}\n"
        )

    # Pull offer hook from lead's custom_fields if present
    _OFFER_HOOK_LABELS = {
        "lunch_and_learn": "Invite to a free Lunch & Learn event (low-pressure, educational)",
        "free_tour": "Offer a free funeral home tour (casual, no obligation)",
        "free_space": "Offer a complimentary cemetery space consultation",
        "family_service_consult": "Offer a free Family Service consultation",
    }
    try:
        _cf = json.loads(lead.custom_fields or "{}") if lead.custom_fields else {}
    except Exception:
        _cf = {}
    _offer_hook = _cf.get("offer_hook")
    _offer_label = _OFFER_HOOK_LABELS.get(_offer_hook) if _offer_hook else None
    if _offer_label:
        offer_hook_line = f"OFFER HOOK: Weave this naturally into at least one option — {_offer_label}. Don't make it the entire email; offer it as a gentle, low-pressure option."
    elif _offer_hook and _offer_hook not in ("none", "custom"):
        offer_hook_line = f"OFFER HOOK: {_offer_hook}"
    else:
        offer_hook_line = ""

    prompt = EMAIL_DRAFT_PROMPT.format(
        relationship_context=relationship_context,
        advisor_name=advisor.full_name or "your advisor",
        org_name=org_name,
        tone_desc=tone_desc,
        lead_type=lead.message_track or lead.tier or "not specified",
        ai_direction=direction,
        sample_message_section=sample_section,
        offer_hook_line=offer_hook_line,
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        tier=lead.tier or "unknown",
        source_year=lead.source_year or "unknown",
        last_action=lead.last_action_raw or "none on file",
        last_contact=last_contact,
        status_reason=lead.status_reason_raw or "none on file",
        notes=(lead.notes or "none")[:200],
    )

    def _clean_body(body: str, real_name: str) -> str:
        """Replace any [Your Name] / [Name] bracket placeholders with the real advisor name."""
        import re
        body = re.sub(r'\[Your Name\]', real_name, body, flags=re.IGNORECASE)
        body = re.sub(r'\[Name\]', real_name, body, flags=re.IGNORECASE)
        body = re.sub(r'\[Advisor Name\]', real_name, body, flags=re.IGNORECASE)
        body = re.sub(r'\[[^\]]*name[^\]]*\]', real_name, body, flags=re.IGNORECASE)
        return body

    advisor_name_str = advisor.full_name or "your advisor"

    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        sys_msg = (
            "You are drafting outreach emails for service business advisors. "
            "When the advisor provides explicit direction, follow it LITERALLY and specifically — "
            "it overrides all other guidance. If they say 'file review', write about file review. "
            "Never substitute generic content when specific direction is given."
        )
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        cleaned_options = [
            {**opt, "body": _clean_body(opt.get("body", ""), advisor_name_str)}
            for opt in result.get("options", [])
        ]
        return {
            "talking_points": result.get("talking_points", []),
            "options": cleaned_options,
            "lead_context": {
                "tier": lead.tier,
                "source_year": lead.source_year,
                "last_action": lead.last_action_raw,
                "last_contact": last_contact,
            }
        }
    except Exception:
        # Fallback — generic options
        first = lead.first_name or "there"
        advisor_name = advisor.full_name or "your advisor"
        return {
            "talking_points": [
                f"{first} was last contacted in {last_contact}" if last_contact != "unknown" else f"Re-engaging {first} after a gap",
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
