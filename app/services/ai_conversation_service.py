"""
AI Auto-Conversation Service
Manages fully automated email back-and-forth between AI and leads.

Cadence: Day 1 (x2), Day 2, Day 4, Day 6, Day 8, Day 10, Day 12, Day 14
= 8 messages total over 14 days.

Each message is unique — varies angle, tone, and content.
References previous attempts naturally. Compassionate tone for funeral home.
AI responds to replies 24/7 within 2 minutes via email poller.
Escalates to advisor on anger/grief/legal/confusion.
"""

import json
import os
import logging
import uuid
from datetime import datetime, timedelta

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.models import Lead, Reply, User, BookingLink, PipelineConversation, EmailMessage, Organization
from app.services.sms_service import BOOKING_BASE_URL, create_booking_link
from app.services.platform_utils import get_brand_name

logger = logging.getLogger(__name__)

_client = None

# Cadence schedule — offsets in hours from conversation start
# Day 1 AM, Day 1 PM, Day 2, Day 4, Day 6, Day 8, Day 10, Day 12, Day 14
CADENCE_HOURS = [9, 18, 33, 81, 129, 177, 225, 273, 321]

TOUCH_ANGLES = [
    "warm_intro",
    "value_proposition",
    "soft_reference",
    "checkin",
    "useful_info",
    "social_proof",
    "personal",
    "final_soft",
]

ESCALATION_KEYWORDS = [
    "attorney", "lawyer", "lawsuit", "sue", "legal",
    "angry", "furious", "terrible", "awful", "disgusting",
    "remove me", "stop contacting", "harassment", "harassing",
    "never call", "stop texting", "unsubscribe",
    "devastated", "traumatized", "falling apart",
]

URGENT_TIERS = {"at_need", "atneed", "at-need", "imminent", "urgent"}

RELATIONSHIP_TYPE_CONTEXT = {
    "cold_lead": (
        "COLD LEAD — No prior relationship. Do NOT act familiar. "
        "Introduce yourself naturally. First message should just open a door."
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

SMART_SYSTEM_PROMPT = """You are an AI assistant helping a Family Service Advisor manage email conversations with families on behalf of {org_name}.

Your job: generate the next outbound email to move this family toward booking a {appt_label} appointment.

━━━ BINDING CONSTRAINTS — READ THESE FIRST ━━━
Relationship context: {relationship_context}
User's AI direction (FOLLOW THIS EXACTLY — it overrides your defaults): {ai_direction}

CRITICAL RULES:
- Be SMART not QUICK. Craft something genuinely thoughtful.
- Sound like a caring human advisor, never robotic or generic.
- This is a sensitive industry. Be compassionate, patient, never pushy.
- Never reveal you are AI.
- The relationship context above defines EXACTLY how familiar you should sound — respect it strictly.
- Vary your approach each time — do NOT repeat what was said before.
- Keep emails SHORT — 2-3 sentences max for the body. No filler.
- Reference the previous outreach naturally if this is a follow-up.
- Personalize using the lead's name, tier, and history.
- CRITICAL: Do NOT include any sign-off, closing, or signature whatsoever. No "Best regards", "Take care", "Sincerely", no name, no company name. Write ONLY the 2-3 sentence body and STOP.

HOW TO INTRODUCE THE ADVISOR:
{advisor_intro_instruction}

TONE: {tone_instruction}
TOUCH ANGLE: {touch_angle_instruction}
{offer_hook_line}

ADVISOR: {advisor_name}
ORGANIZATION: {org_name}
LEAD: {first_name} {last_name}
APPOINTMENT TYPE: {appt_label}
LEAD TIER: {tier}
SOURCE: {source} {source_year}
ADDITIONAL LEAD CONTEXT:
{lead_context}

Respond ONLY with valid JSON (no markdown, no backticks):
{{"subject": "email subject line", "body": "2-3 sentence email body only, no sign-off, no URLs", "should_stop": false, "stop_reason": "", "escalate": false, "escalate_reason": "", "confidence": 90}}
"""

REPLY_SYSTEM_PROMPT = """You are an AI assistant helping {advisor_name} at {org_name} respond to a lead's email reply.

The lead replied. Generate the smartest, most human response to move this conversation forward.

CRITICAL RULES:
- Read the lead's reply carefully. Respond DIRECTLY to what they actually said — not to a generic script.
- Compassionate and human. Never salesy, never robotic.
- 2-3 sentences max. No filler.
- Objection handling — respond with care, not pressure:
  * "already have one / covered" → Acknowledge sincerely. Offer a no-pressure second-opinion or free review angle.
  * "send info / not ready" → Offer one genuinely useful fact, then ask one easy question.
  * "too busy / not a good time" → Respect it. Offer to follow up when convenient.
  * "not interested" → Set should_stop=true. Close gracefully: acknowledge and leave the door open.
  * "how much / price" → Don't quote numbers. Invite a conversation to understand their situation.
- If they show clear interest → offer to schedule, include should_book=true.
- If they ask a question → answer it specifically, then gently ask if they'd like to schedule.
- Never reveal you are AI.
- CRITICAL: Do NOT include any sign-off, closing, or signature. Write ONLY the 2-3 sentence body and STOP.

Relationship context: {relationship_context}
ADVISOR: {advisor_name}  |  ORGANIZATION: {org_name}
LEAD: {first_name} {last_name}  |  APPOINTMENT TYPE: {appt_label}
ADDITIONAL LEAD CONTEXT:
{lead_context}

Respond ONLY with valid JSON (no markdown, no backticks):
{{"subject": "reply subject", "body": "2-3 sentence reply, no sign-off, no URLs", "should_book": false, "should_stop": false, "stop_reason": "", "escalate": false, "escalate_reason": "", "confidence": 90}}
"""

TONE_MAP = {
    "cold": "Soft and very low-pressure. Just opening a door. No ask yet.",
    "warm": "Friendly and conversational. Gently suggest meeting.",
    "hot": "Direct and confident. Clear ask for the appointment.",
    "urgent": "Compassionate urgency. They may need help now.",
}

TOUCH_ANGLE_MAP = {
    "warm_intro": (
        "First contact. Use the HOW TO INTRODUCE THE ADVISOR instruction above precisely. "
        "Be warm and specific — mention why you're reaching out for THIS person (their tier, source, or context). "
        "One clear, simple ask at the end. No pressure. No filler. Sound like a person, not a template."
    ),
    "value_proposition": "Focus on what your organization can do for them. What peace of mind looks like. Don't ask yet.",
    "soft_reference": "Reference your previous email naturally ('I reached out a few days ago...'). Try a completely different angle.",
    "checkin": "Simple, low-pressure check-in. Just making sure they got your message. No ask.",
    "useful_info": "Share something genuinely useful — a question to think about, something families often don't know. Build trust.",
    "social_proof": "Mention (without names) how other families felt after their appointment. Make it relatable.",
    "personal": "More personal and empathetic. Acknowledge this is a lot to think about. Show you understand.",
    "final_soft": "This is the last reach-out. Keep it gracious and leave the door open. No pressure at all.",
}

APPT_LABEL_MAP = {
    "pre_need": "Pre-Need Planning Consultation",
    "preneed": "Pre-Need Planning Consultation",
    "at_need": "At-Need Arrangement Conference",
    "atneed": "At-Need Arrangement Conference",
    "imminent": "Immediate Need Consultation",
    "urgent": "Urgent Arrangement Consultation",
    "file_check": "Family File Review",
    "code_lead": "Family File Review",
    "property": "Property Ownership Review",
    "marker": "Marker & Memorial Consultation",
    "memorial": "Memorial Planning Consultation",
    "veteran": "Veterans Benefits Consultation",
    "insurance": "Insurance & Benefits Review",
    "web_lead": "General Consultation",
    "referral": "Family Services Consultation",
}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


def _get_appt_label(lead: Lead) -> str:
    tier = (lead.tier or "").lower().strip()
    return APPT_LABEL_MAP.get(tier, "Family Services Appointment")

def _get_org_name(db: Session, advisor: User) -> str:
    """Look up the organization name from DB — never hardcoded."""
    org = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
    return org.name if org else "our organization"


def _build_email_html(body: str, advisor_name: str, org_name: str, extra_html: str = "") -> str:
    """Wrap AI body text with a clean HTML signature block."""
    body_html = body.replace("\n", "<br>")
    return f"""{body_html}{extra_html}<br><br>
<span style="color:#555;font-size:14px;line-height:1.6;">
Best regards,<br>
<strong>{advisor_name}</strong><br>
{org_name}
</span>"""

def _strip_signoff(body: str) -> str:
    """Remove any AI-generated sign-off from the email body.
    GPT sometimes adds closings despite being told not to — strip them here.
    Handles both newline-separated and inline sign-offs."""
    import re
    # Match sign-off phrases whether they follow a newline OR inline punctuation/space
    signoff_patterns = [
        r'[\n,.]?\s*(best regards|warm regards|kind regards|sincerely|take care|'
        r'thanks|thank you|looking forward|yours truly|respectfully|with care|'
        r'cordially|cheers)[,.]?.*',
    ]
    for pat in signoff_patterns:
        body = re.sub(pat, '', body, flags=re.IGNORECASE | re.DOTALL)
    # Strip trailing [Your Name], [Name], placeholder lines
    body = re.sub(r'[\n,.]?\s*\[.*?\].*', '', body, flags=re.DOTALL)
    return body.strip()






def _get_booking_url(db: Session, lead: Lead, advisor: User) -> str:
    # Create a fresh link for this AI touch.
    # IMPORTANT: do NOT expire previous pending links — if the lead already received
    # an earlier email with a booking button, that link must still work when they click it.
    # Each AI touch gets its own link; all remain valid until the lead books.
    link = create_booking_link(db, lead, advisor)
    return f"{BOOKING_BASE_URL}/book/{link.token}"


def _get_conversation_history(db: Session, lead: Lead) -> str:
    """
    Returns the last 10 messages across ALL channels (email + SMS)
    in chronological order so the AI has full context of the relationship.
    """
    import re
    from app.models.models import Message as OutboundSMS

    events = []

    # Outbound emails
    email_msgs = db.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id
    ).order_by(EmailMessage.sent_at.asc()).all()
    for m in email_msgs:
        body = re.sub(r'<[^>]+>', ' ', m.body_html or '').strip()[:250]
        events.append({"dir": "out", "channel": "email", "subject": m.subject or "", "body": body, "ts": m.sent_at or datetime.min})

    # All replies (email + SMS)
    all_replies = db.query(Reply).filter(
        Reply.lead_id == lead.id,
    ).order_by(Reply.received_at.asc()).all()
    for r in all_replies:
        sentiment = ""
        if r.classification:
            cls_val = r.classification.value if hasattr(r.classification, 'value') else str(r.classification)
            if cls_val not in ("neutral", "unknown"):
                sentiment = f" [{cls_val}]"
        is_hot_marker = " 🔥" if r.is_hot else ""
        events.append({
            "dir": "in",
            "channel": r.source or "sms",
            "body": (r.body or "")[:250] + sentiment + is_hot_marker,
            "ts": r.received_at or datetime.min,
        })

    # Outbound SMS
    sms_msgs = db.query(OutboundSMS).filter(
        OutboundSMS.lead_id == lead.id
    ).order_by(OutboundSMS.sent_at.asc()).all()
    for m in sms_msgs:
        events.append({"dir": "out", "channel": "sms", "body": (m.body or "")[:250], "ts": m.sent_at or datetime.min})

    events.sort(key=lambda e: e["ts"])
    lines = []
    for e in events[-10:]:  # last 10 across all channels
        channel_tag = f"[{e['channel'].upper()}] " if e.get("channel") else ""
        if e["dir"] == "out":
            subject_part = f"[{e['subject']}] " if e.get("subject") else ""
            lines.append(f"ADVISOR {channel_tag}{subject_part}{e['body']}")
        else:
            lines.append(f"LEAD {channel_tag}{e['body']}")
    return "\n".join(lines) if lines else "No prior conversation."


def _build_lead_context(lead: Lead) -> str:
    """
    Returns a richer lead context block for injection into AI prompts.
    Includes notes, location, custom fields summary, and source info.
    """
    parts = []

    # Location for personalization
    location_parts = [lead.city, lead.state] if hasattr(lead, 'city') and (lead.city or lead.state) else []
    if hasattr(lead, 'city') and lead.city:
        location_parts = [lead.city]
        if hasattr(lead, 'state') and lead.state:
            location_parts.append(lead.state)
    if location_parts:
        parts.append(f"Location: {', '.join(location_parts)}")

    # Source year gives age-of-record context
    if lead.source_year:
        parts.append(f"Record from: {lead.source_year}")

    # Advisor notes (surface key facts without dumping everything)
    notes = (lead.notes or "").strip()
    if notes:
        parts.append(f"Advisor notes: {notes[:300]}")

    # Custom fields — any campaign hooks, offer context, etc.
    try:
        cf = __import__('json').loads(lead.custom_fields or "{}") if lead.custom_fields else {}
    except Exception:
        cf = {}
    # Filter out internal keys already handled elsewhere
    skip_keys = {"offer_hook", "campaign_purpose"}
    extra_cf = {k: v for k, v in cf.items() if k not in skip_keys and v}
    if extra_cf:
        cf_lines = "; ".join(f"{k}: {v}" for k, v in list(extra_cf.items())[:5])
        parts.append(f"Extra context: {cf_lines}")

    return "\n".join(parts) if parts else "No additional context."


def _get_or_create_conversation(db: Session, lead: Lead, advisor: User, channel: str = "email") -> PipelineConversation:
    conv = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead.id,
        PipelineConversation.advisor_id == advisor.id,
    ).first()
    if not conv:
        conv = PipelineConversation(
            organization_id=advisor.organization_id,
            lead_id=lead.id,
            advisor_id=advisor.id,
            channel=channel,
            stage="outreach_sent",
            auto_respond=True,
            touch_number=0,
            started_at=datetime.utcnow(),
        )
        db.add(conv)
        db.flush()
    return conv


def _next_send_time(touch_number: int, started_at: datetime) -> datetime:
    if touch_number >= len(CADENCE_HOURS):
        return None
    hours_offset = CADENCE_HOURS[touch_number]
    send_time = started_at + timedelta(hours=hours_offset)
    # Keep within 9am-5pm CST (UTC-6)
    cst_hour = (send_time.hour - 6) % 24
    if cst_hour < 9:
        send_time = send_time.replace(hour=15, minute=0, second=0, microsecond=0)
    elif cst_hour >= 17:
        next_day = send_time + timedelta(days=1)
        send_time = next_day.replace(hour=15, minute=0, second=0, microsecond=0)
    return send_time


def _check_escalation(text: str) -> tuple:
    text_lower = text.lower()
    for keyword in ESCALATION_KEYWORDS:
        if keyword in text_lower:
            return True, f"Detected: '{keyword}'"
    return False, ""


def _send_email_resend(db: Session, advisor: User, to_email: str, subject: str, body: str):
    """Send via Resend using the org's configured API key / from address.
    Raises on failure so callers can catch and handle."""
    from app.services.email_service import send_email_via_provider
    from app.models.models import Organization

    org = db.query(Organization).filter_by(id=advisor.organization_id).first()
    result = send_email_via_provider(to_email, subject, body, org=org)
    if not result["success"]:
        raise Exception(f"Resend send failed: {result.get('error', 'unknown error')}")


def _escalate_conversation(db: Session, conv: PipelineConversation, lead: Lead, advisor: User, reason: str, reply_body: str):
    conv.flagged = True
    conv.flag_reason = reason
    conv.flagged_reply_body = reply_body
    conv.flagged_at = datetime.utcnow()
    conv.paused = True
    conv.paused_reason = "Escalated — human review needed"
    conv.stage = "flagged"
    db.commit()

    try:
        notification_email = getattr(advisor, 'notification_email', None) or "michael.simmons@nsmg.com"
        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
        frontend_url = os.environ.get("FRONTEND_URL", "https://advisorflow-frontend.onrender.com")
        subject = f"⚠️ Human Response Needed — {lead_name}"
        body = f"""<p><strong>AI paused on lead: {lead_name}</strong></p>
<p><strong>Reason:</strong> {reason}</p>
{'<p><strong>Their message:</strong> ' + reply_body[:500] + '</p>' if reply_body else ''}
<br><a href="{frontend_url}/leads/{lead.id}" style="background:#1a5fa8;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;display:inline-block;">View Lead & Respond →</a>
<p style="color:#94a3b8;font-size:12px;margin-top:16px;">{get_brand_name(db, str(advisor.organization_id))} AI paused this conversation. Review and respond manually or click Resume AI on the lead page.</p>"""
        _send_email_resend(db, advisor, notification_email, subject, body)
    except Exception as e:
        logger.error("Escalation alert failed: %s", e)


def _build_advisor_intro_instruction(advisor_name: str, org_name: str) -> str:
    """
    Generates a clear directive for how the AI should introduce the advisor in the email.
    Handles the common case where advisor full_name == org_name (e.g. both "MDG Testing")
    so the AI never writes "My name is X with X."
    """
    # Normalize for comparison (lowercase, strip extra whitespace)
    adv_norm = (advisor_name or "").strip().lower()
    org_norm = (org_name or "").strip().lower()

    if not advisor_name or advisor_name.strip() == "Your Advisor":
        return (
            f"Introduce yourself as a Family Service Advisor at {org_name}. "
            f"Do NOT say your name — just reference the organization."
        )

    if adv_norm == org_norm:
        # Advisor name IS the org name — introducing with "with" would be redundant
        return (
            f"Your name is {advisor_name} and you represent {org_name}. "
            f"IMPORTANT: Do NOT write 'My name is {advisor_name} with {org_name}' — they are the same. "
            f"Instead just say something like 'I'm {advisor_name}' or 'I'm reaching out from {org_name}' — pick one, not both."
        )

    # Normal case — advisor has a distinct name from the org
    return (
        f"Your name is {advisor_name} and you work at {org_name}. "
        f"When introducing yourself for the first time, say something like "
        f"'My name is {advisor_name}, a Family Service Advisor at {org_name}' — natural and specific."
    )


def generate_touch_email(
    db: Session,
    lead: Lead,
    advisor: User,
    touch_number: int,
    ai_direction: str = None,
    relationship_type: str = None,
) -> dict:
    if touch_number >= len(TOUCH_ANGLES):
        return {"should_stop": True, "stop_reason": "Cadence complete"}

    angle = TOUCH_ANGLES[touch_number]
    tier = (lead.tier or "").lower()
    tone = "urgent" if tier in URGENT_TIERS else "warm"
    appt_label = _get_appt_label(lead)
    history = _get_conversation_history(db, lead)

    # Resolve relationship context — caller override > lead field > default cold
    rel_type = relationship_type or getattr(lead, "relationship_type", None) or "cold_lead"
    relationship_context = RELATIONSHIP_TYPE_CONTEXT.get(rel_type, RELATIONSHIP_TYPE_CONTEXT["cold_lead"])

    direction = ai_direction.strip() if ai_direction and ai_direction.strip() else "(none — follow relationship context and tone)"

    # Pull offer/campaign context from lead's custom_fields if present
    cf = {}
    try:
        cf = json.loads(lead.custom_fields or "{}") if lead.custom_fields else {}
    except Exception:
        cf = {}
    offer_hook = cf.get("offer_hook")
    campaign_purpose = cf.get("campaign_purpose")
    OFFER_HOOK_LABELS = {
        "lunch_and_learn": "Lunch & Learn event (invite them, no pressure)",
        "free_tour": "Free funeral home tour (low-commitment, educational visit)",
        "free_space": "Complimentary cemetery space consultation",
        "family_service_consult": "Free Family Service consultation",
        "custom": None,
    }
    offer_desc = OFFER_HOOK_LABELS.get(offer_hook) if offer_hook else None
    if offer_desc:
        offer_hook_line = f"OFFER HOOK: Weave this into the message naturally — {offer_desc}. Don't make it the entire email; just offer it as a low-pressure option."
    elif offer_hook and offer_hook != "none":
        offer_hook_line = f"OFFER HOOK: {offer_hook}"
    else:
        offer_hook_line = ""

    org_name = _get_org_name(db, advisor)
    advisor_name = advisor.full_name or "Your Advisor"
    lead_context = _build_lead_context(lead)
    advisor_intro = _build_advisor_intro_instruction(advisor_name, org_name)
    system = SMART_SYSTEM_PROMPT.format(
        relationship_context=relationship_context,
        ai_direction=direction,
        appt_label=appt_label,
        advisor_intro_instruction=advisor_intro,
        tone_instruction=TONE_MAP.get(tone, TONE_MAP["warm"]),
        touch_angle_instruction=TOUCH_ANGLE_MAP.get(angle, ""),
        offer_hook_line=offer_hook_line,
        advisor_name=advisor_name,
        org_name=org_name,
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        tier=lead.tier or "unknown",
        source=lead.source_file or "",
        source_year=str(lead.source_year or ""),
        lead_context=lead_context,
    )
    user_msg = f"Conversation history:\n{history}\n\nThis is touch #{touch_number + 1} of 8. Generate the email now."

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.6,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()
        clean = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(clean)
        return {
            "subject": data.get("subject", f"Following up, {lead.first_name or 'there'}"),
            "body": data.get("body", ""),
            "should_stop": bool(data.get("should_stop", False)),
            "stop_reason": data.get("stop_reason", ""),
            "escalate": bool(data.get("escalate", False)),
            "escalate_reason": data.get("escalate_reason", ""),
            "confidence": data.get("confidence", 80),
            "touch_number": touch_number,
            "angle": angle,
            "source": "ai",
        }
    except Exception as e:
        logger.error("generate_touch_email error: %s", e)
        return {
            "subject": f"Following up, {lead.first_name or 'there'}",
            "body": f"Hi {lead.first_name or 'there'}, I wanted to follow up regarding your {appt_label}. I'd love to connect at your convenience.",
            "should_stop": False,
            "escalate": False,
            "source": "fallback",
            "touch_number": touch_number,
        }


def generate_reply_response(db: Session, lead: Lead, advisor: User, reply_body: str) -> dict:
    should_escalate, escalate_reason = _check_escalation(reply_body)
    if should_escalate:
        return {"escalate": True, "escalate_reason": escalate_reason, "should_stop": False, "source": "escalation_detected"}

    appt_label = _get_appt_label(lead)
    history = _get_conversation_history(db, lead)

    org_name = _get_org_name(db, advisor)
    lead_context = _build_lead_context(lead)
    rel_type = getattr(lead, "relationship_type", None) or "cold_lead"
    relationship_context = RELATIONSHIP_TYPE_CONTEXT.get(rel_type, RELATIONSHIP_TYPE_CONTEXT["cold_lead"])
    system = REPLY_SYSTEM_PROMPT.format(
        advisor_name=advisor.full_name or "Your Advisor",
        org_name=org_name,
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        appt_label=appt_label,
        relationship_context=relationship_context,
        lead_context=lead_context,
    )
    user_msg = (
        f"Full conversation history (read this carefully — it is your context):\n{history}\n\n"
        f"Lead's latest reply:\n{reply_body}\n\n"
        f"Now generate your response, addressing exactly what they said."
    )

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.65,
            max_tokens=350,
        )
        raw = response.choices[0].message.content.strip()
        clean = raw.lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(clean)
        return {
            "subject": data.get("subject", f"Re: Following up, {lead.first_name or 'there'}"),
            "body": data.get("body", ""),
            "should_book": bool(data.get("should_book", False)),
            "should_stop": bool(data.get("should_stop", False)),
            "stop_reason": data.get("stop_reason", ""),
            "escalate": bool(data.get("escalate", False)),
            "escalate_reason": data.get("escalate_reason", ""),
            "confidence": data.get("confidence", 80),
            "source": "ai",
        }
    except Exception as e:
        logger.error("generate_reply_response error: %s", e)
        return {
            "subject": f"Re: Following up, {lead.first_name or 'there'}",
            "body": f"Thank you for getting back to me, {lead.first_name or 'there'}. I'd love to connect — would any of the times on my booking link work for you?",
            "should_book": False,
            "should_stop": False,
            "escalate": False,
            "source": "fallback",
        }


def _send_touch(db: Session, lead: Lead, advisor: User, conv: PipelineConversation, touch_number: int) -> dict:
    email_data = generate_touch_email(db, lead, advisor, touch_number)

    if email_data.get("should_stop"):
        conv.stage = "stopped"
        conv.completed_at = datetime.utcnow()
        db.commit()
        return {"success": False, "error": email_data.get("stop_reason", "AI decided to stop")}

    if email_data.get("escalate"):
        _escalate_conversation(db, conv, lead, advisor, email_data.get("escalate_reason", ""), "")
        return {"success": False, "error": "Escalated to advisor"}

    try:
        if not lead.email:
            return {"success": False, "error": "Lead has no email address"}

        org_name = _get_org_name(db, advisor)
        clean_body = _strip_signoff(email_data["body"])
        # Booking button: touch 0 (first), and touches 6+ (personal, final_soft).
        # Middle touches are pure conversation — no CTA, let the relationship build.
        _BUTTON_TOUCHES = {0, 6, 7, 8}
        extra_html = ""
        if touch_number in _BUTTON_TOUCHES:
            booking_url = _get_booking_url(db, lead, advisor)
            extra_html = (
                f'<br><br>'
                f'<a href="{booking_url}" '
                f'style="display:inline-block;background:#1a5fa8;color:#ffffff;padding:12px 28px;'
                f'border-radius:6px;text-decoration:none;font-weight:700;font-size:15px;">'
                f'Schedule a Time &rarr;</a>'
            )
        html_body = _build_email_html(clean_body, advisor.full_name or "Your Advisor", org_name, extra_html=extra_html)
        _send_email_resend(db, advisor, lead.email, email_data["subject"], html_body)

        msg = EmailMessage(
            id=str(uuid.uuid4()),
            lead_id=lead.id,
            sender_id=advisor.id,
            subject=email_data["subject"],
            body_html=html_body,
            status="sent",
            sent_at=datetime.utcnow(),
        )
        db.add(msg)

        conv.messages_sent = (conv.messages_sent or 0) + 1
        conv.ai_responses_sent = (conv.ai_responses_sent or 0) + 1
        conv.last_outbound_at = datetime.utcnow()

        if lead.status == "new":
            lead.status = "sent"

        db.commit()
        return {"success": True, "subject": email_data["subject"]}

    except Exception as e:
        logger.error("_send_touch error: %s", e)
        return {"success": False, "error": str(e)}


def start_ai_conversation(db: Session, lead: Lead, advisor: User, channel: str = "email") -> dict:
    # "booked" is intentionally NOT blocked here — a booked lead may still need
    # AI follow-up if they want to reschedule or have pre-appointment questions.
    if lead.status == "dnc" or lead.is_duplicate:
        return {"success": False, "error": "Lead is DNC or duplicate"}

    if not lead.email:
        return {"success": False, "error": "Lead has no email address"}

    conv = _get_or_create_conversation(db, lead, advisor, channel)

    if conv.touch_number > 0 and not conv.paused and conv.stage not in ("stopped", "completed", "flagged"):
        return {"success": False, "error": "AI conversation already active for this lead", "already_active": True}

    conv.paused = False
    conv.paused_reason = None
    conv.touch_number = 0
    conv.started_at = datetime.utcnow()
    conv.stage = "outreach_sent"
    conv.flagged = False
    conv.flag_reason = None

    result = _send_touch(db, lead, advisor, conv, touch_number=0)
    if not result.get("success"):
        return result

    conv.touch_number = 1
    next_time = _next_send_time(1, conv.started_at)
    conv.next_send_at = next_time
    db.commit()

    return {
        "success": True,
        "message": f"AI conversation started. Touch 1 sent to {lead.email}.",
        "next_touch_at": next_time.isoformat() if next_time else None,
        "conversation_id": conv.id,
    }


def pause_ai_conversation(db: Session, lead_id: str, advisor_id: str, reason: str = "Advisor paused") -> dict:
    conv = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead_id,
        PipelineConversation.advisor_id == advisor_id,
    ).first()
    if not conv:
        return {"success": False, "error": "No active conversation"}
    conv.paused = True
    conv.paused_reason = reason
    db.commit()
    return {"success": True}


def resume_ai_conversation(db: Session, lead_id: str, advisor_id: str) -> dict:
    conv = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead_id,
        PipelineConversation.advisor_id == advisor_id,
    ).first()
    if not conv:
        return {"success": False, "error": "No conversation found"}
    conv.paused = False
    conv.paused_reason = None
    conv.flagged = False
    conv.stage = "ai_responding"
    conv.next_send_at = datetime.utcnow() + timedelta(minutes=5)
    db.commit()
    return {"success": True}


def get_conversation_status(db: Session, lead_id: str, advisor_id: str) -> dict:
    conv = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead_id,
        PipelineConversation.advisor_id == advisor_id,
    ).first()
    if not conv:
        return {"active": False, "status": "not_started"}
    return {
        "active": not conv.paused and conv.stage not in ("stopped", "completed"),
        "paused": conv.paused,
        "flagged": conv.flagged,
        "flag_reason": conv.flag_reason,
        "stage": conv.stage,
        "touch_number": conv.touch_number,
        "messages_sent": conv.messages_sent or 0,
        "next_send_at": conv.next_send_at.isoformat() if conv.next_send_at else None,
        "started_at": conv.started_at.isoformat() if conv.started_at else None,
        "conversation_id": conv.id,
        "paused_reason": conv.paused_reason,
    }


def process_scheduled_touches(db: Session, org_id: str = None) -> dict:
    """Process due AI touches for leads.

    Args:
        db: Database session for this call.
        org_id: If provided, only process conversations for leads belonging
                to this org. The main loop calls this once per org so a
                failure in one org cannot stall another.
    """
    now = datetime.utcnow()
    query = db.query(PipelineConversation).filter(
        PipelineConversation.next_send_at <= now,
        PipelineConversation.paused == False,
        PipelineConversation.flagged == False,
        PipelineConversation.stage.notin_(["stopped", "completed", "booked"]),
    )

    if org_id:
        # Scope to a single org: join through Lead to filter by organization_id
        query = query.join(Lead, Lead.id == PipelineConversation.lead_id).filter(
            Lead.organization_id == org_id
        )

    due = query.all()

    sent = 0
    errors = 0
    skipped = 0

    for conv in due:
        try:
            lead = db.query(Lead).filter(Lead.id == conv.lead_id).first()
            advisor = db.query(User).filter(User.id == conv.advisor_id).first()

            if not lead or not advisor:
                skipped += 1
                continue

            if lead.status in ("booked", "dnc") or lead.is_duplicate:
                conv.stage = "stopped"
                conv.next_send_at = None
                db.commit()
                skipped += 1
                continue

            touch_num = conv.touch_number
            if touch_num >= len(CADENCE_HOURS):
                conv.stage = "completed"
                conv.completed_at = now
                conv.next_send_at = None
                lead.status = "cold"
                db.commit()
                skipped += 1
                continue

            result = _send_touch(db, lead, advisor, conv, touch_num)
            if result.get("success"):
                next_touch = touch_num + 1
                conv.touch_number = next_touch
                if next_touch < len(CADENCE_HOURS):
                    conv.next_send_at = _next_send_time(next_touch, conv.started_at)
                else:
                    conv.next_send_at = None
                    conv.stage = "completed"
                    conv.completed_at = now
                    lead.status = "cold"
                db.commit()
                sent += 1
            else:
                errors += 1

        except Exception as e:
            logger.error("process_scheduled_touches error conv=%s: %s", conv.id, e)
            errors += 1

    return {"processed": len(due), "sent": sent, "skipped": skipped, "errors": errors}


POST_BOOKING_SYSTEM_PROMPT = """You are {advisor_name} at {org_name}, personally responding to a message from {first_name}, who has already booked a {appt_label} with you.

Your role is appointment concierge — warm, calm, and reassuring. This person is already committed. Your job is to make them feel great about their upcoming visit.

INTENT — classify the lead's message as exactly one of:
- "reschedule"  — they want to change or move the appointment time
- "cancel"      — they explicitly want to cancel or not come
- "question"    — they have a logistical or informational question
- "confirm"     — just confirming, saying thanks, or expressing they'll be there
- "emotional"   — they sound nervous, sad, overwhelmed, or are sharing grief
- "other"       — anything that doesn't fit above

RULES (non-negotiable):
- NEVER ask them to schedule, book, or set a time — they are already booked
- NEVER use placeholders like [Your Name] — use your name directly: {advisor_name}
- Keep replies SHORT (2-4 sentences max) — warm, direct, human
- No sign-off or closing — the system adds one automatically
- If intent is "reschedule" or "cancel": set escalate=true so the human advisor can handle it personally
- If intent is "emotional": open with empathy first, practicalities second

HOW TO ANSWER COMMON QUESTIONS:
- What should I bring? → Bring any existing policies, contracts, or paperwork you have. No pressure if you don't — we'll go through everything together.
- Can someone come with me? → Absolutely — family is always welcome. Bring anyone you'd like.
- How long does it take? → Usually about an hour, sometimes a little less depending on what we cover.
- Where do I go / what's the address? → Check the booking confirmation you received for the location. If you need it resent, just let me know.
- Who will I be meeting? → You'll be meeting directly with me, {advisor_name} — a personal, one-on-one conversation.

Respond ONLY with valid JSON, no markdown, no backticks:
{{"intent": "question", "subject": "Re: Your Upcoming Appointment", "body": "2-4 sentence response here", "escalate": false, "escalate_reason": ""}}"""


def _handle_post_booking_reply(db: Session, lead: Lead, advisor: User, reply_body: str, conv) -> dict:
    """
    Respond to a reply from a lead who has already booked.
    Concierge mode: answers questions, detects reschedule/cancel and escalates,
    handles emotional replies with extra care. Never re-engages the sales pipeline.
    """
    # Step 1 — hard escalation keywords (legal, anger, harassment) always win
    should_escalate, esc_reason = _check_escalation(reply_body)
    if should_escalate:
        _escalate_conversation(db, conv, lead, advisor, esc_reason, reply_body)
        return {"action": "escalated", "reason": esc_reason}

    org_name = _get_org_name(db, advisor)
    advisor_name = advisor.full_name or "Your Advisor"
    appt_label = _get_appt_label(lead)
    first_name = lead.first_name or "there"
    history = _get_conversation_history(db, lead)

    system = POST_BOOKING_SYSTEM_PROMPT.format(
        advisor_name=advisor_name,
        org_name=org_name,
        first_name=first_name,
        appt_label=appt_label,
    )
    user_msg = (
        f"Conversation so far:\n{history}\n\n"
        f"{first_name}'s latest message: \"{reply_body}\"\n\n"
        f"Classify the intent and write your response now."
    )

    intent = "other"
    subject = f"Re: Your {appt_label}"
    body = (
        f"Hi {first_name}, thanks for reaching out! I'm looking forward to our appointment. "
        f"Don't hesitate to ask if you need anything else before we meet."
    )
    ai_wants_escalate = False
    escalate_reason = ""

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=350,
        )
        raw = response.choices[0].message.content.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        result = json.loads(raw)
        intent = result.get("intent", "other")
        body = result.get("body", body)
        subject = result.get("subject", subject)
        ai_wants_escalate = bool(result.get("escalate", False))
        escalate_reason = result.get("escalate_reason", "")
    except Exception as e:
        logger.error("_handle_post_booking_reply AI error: %s", e)

    # Step 2 — escalate reschedule / cancel to advisor; do NOT auto-reply
    if ai_wants_escalate or intent in ("reschedule", "cancel"):
        reason = escalate_reason or f"Lead sent a '{intent}' request for their booked appointment"
        _escalate_conversation(db, conv, lead, advisor, reason, reply_body)
        return {"action": "escalated", "reason": reason, "intent": intent}

    # Step 3 — send the concierge reply
    try:
        clean_body = _strip_signoff(body)
        html_body = _build_email_html(clean_body, advisor_name, org_name)
        _send_email_resend(db, advisor, lead.email, subject, html_body)

        msg = EmailMessage(
            id=str(uuid.uuid4()),
            lead_id=lead.id,
            sender_id=advisor.id,
            subject=subject,
            body_html=html_body,
            status="sent",
            sent_at=datetime.utcnow(),
        )
        db.add(msg)
        conv.ai_responses_sent = (conv.ai_responses_sent or 0) + 1
        conv.last_outbound_at = datetime.utcnow()
        db.commit()
        logger.info("Post-booking concierge reply sent (intent=%s) to lead %s", intent, lead.id)
        return {"action": "post_booking_replied", "intent": intent, "subject": subject}
    except Exception as e:
        logger.error("_handle_post_booking_reply send error: %s", e)
        return {"action": "error", "error": str(e)}


def handle_inbound_reply(db: Session, lead: Lead, advisor: User, reply_body: str) -> dict:
    conv = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead.id,
        PipelineConversation.advisor_id == advisor.id,
    ).first()

    # ── Post-booking concierge ──────────────────────────────────────────────
    # lead.status is the authoritative source of truth — conv.stage may lag.
    if lead.status == "booked":
        # Sync conv stage so future checks are consistent
        if conv and conv.stage not in ("booked", "stopped", "completed"):
            conv.stage = "booked"
            conv.next_send_at = None   # halt any pending cadence touches
            db.commit()

        if not conv:
            # No conv record yet — create a minimal one so logging works
            conv = _get_or_create_conversation(db, lead, advisor)
            conv.stage = "booked"
            db.commit()

        if not lead.email:
            logger.warning("Post-booking reply from lead %s but no email on file — escalating", lead.id)
            _escalate_conversation(db, conv, lead, advisor, "Lead replied but has no email address on file", reply_body)
            return {"action": "escalated", "reason": "no_email"}

        return _handle_post_booking_reply(db, lead, advisor, reply_body, conv)
    # ────────────────────────────────────────────────────────────────────────

    if not conv or conv.paused or conv.stage in ("stopped", "completed"):
        return {"action": "no_active_conversation"}

    conv.replies_received = (conv.replies_received or 0) + 1
    conv.last_inbound_at = datetime.utcnow()

    result = generate_reply_response(db, lead, advisor, reply_body)

    if result.get("escalate"):
        _escalate_conversation(db, conv, lead, advisor, result.get("escalate_reason", ""), reply_body)
        return {"action": "escalated", "reason": result.get("escalate_reason")}

    if result.get("should_stop"):
        conv.stage = "stopped"
        conv.next_send_at = None
        lead.status = "cold"
        db.commit()
        return {"action": "stopped", "reason": result.get("stop_reason")}

    if result.get("should_book"):
        booking_url = _get_booking_url(db, lead, advisor)
        org_name = _get_org_name(db, advisor)
        clean_booking_body = _strip_signoff(result["body"])
        booking_btn = (
            f'<br><br>'
            f'<a href="{booking_url}" '
            f'style="display:inline-block;background:#1a5fa8;color:#ffffff;padding:12px 28px;'
            f'border-radius:6px;text-decoration:none;font-weight:700;font-size:15px;">'
            f'Schedule Your Appointment &rarr;</a>'
        )
        html_booking = _build_email_html(clean_booking_body, advisor.full_name or "Your Advisor", org_name, extra_html=booking_btn)
        try:
            _send_email_resend(db, advisor, lead.email, result["subject"], html_booking)
            conv.stage = "booking_sent"
            conv.booking_link_sent_at = datetime.utcnow()
            db.commit()
            return {"action": "booking_sent"}
        except Exception as e:
            logger.error("handle_inbound_reply booking error: %s", e)
            return {"action": "error", "error": str(e)}

    try:
        org_name = _get_org_name(db, advisor)
        clean_reply = _strip_signoff(result["body"])
        html_reply = _build_email_html(clean_reply, advisor.full_name or "Your Advisor", org_name)
        _send_email_resend(db, advisor, lead.email, result["subject"], html_reply)
        msg = EmailMessage(
            id=str(uuid.uuid4()),
            lead_id=lead.id,
            sender_id=advisor.id,
            subject=result["subject"],
            body_html=html_reply,
            status="sent",
            sent_at=datetime.utcnow(),
        )
        db.add(msg)
        conv.ai_responses_sent = (conv.ai_responses_sent or 0) + 1
        conv.last_outbound_at = datetime.utcnow()
        conv.stage = "ai_responding"
        conv.next_send_at = None  # Pause cadence during active back-and-forth
        db.commit()
        return {"action": "replied", "subject": result["subject"]}
    except Exception as e:
        logger.error("handle_inbound_reply send error: %s", e)
        return {"action": "error", "error": str(e)}


# Legacy compatibility
def generate_auto_reply(
    db: Session,
    lead: Lead,
    advisor: User,
    tone: str = "warm",
    ai_direction: str = None,
    relationship_type: str = None,
) -> dict:
    result = generate_touch_email(
        db, lead, advisor, touch_number=0,
        ai_direction=ai_direction,
        relationship_type=relationship_type,
    )
    booking_url = _get_booking_url(db, lead, advisor)
    return {
        "reply": result.get("body", ""),
        "subject": result.get("subject", ""),
        "should_stop": result.get("should_stop", False),
        "reason": result.get("stop_reason", ""),
        "source": result.get("source", "ai"),
        "booking_url": booking_url,
    }
