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
from typing import Any

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.models import Lead, Message, Reply, User, BookingLink, PipelineConversation, EmailMessage
from app.services.sms_service import BOOKING_BASE_URL, create_booking_link

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

SMART_SYSTEM_PROMPT = """You are an AI assistant helping a Family Service Advisor at Restland Cemetery & Funeral Home in Dallas, TX manage email conversations with families.

Your job: generate the next outbound email to move this family toward booking a {appt_label} appointment.

CRITICAL RULES:
- Be SMART not QUICK. Craft something genuinely thoughtful.
- Sound like a caring human advisor, never robotic or generic.
- This is a sensitive industry. Be compassionate, patient, never pushy.
- Never reveal you are AI.
- Vary your approach each time — do NOT repeat what was said before.
- Keep emails SHORT — 2-3 sentences max for the body. No filler.
- Reference the previous outreach naturally if this is a follow-up.
- Personalize using the lead's name, tier, and history.

TONE: {tone_instruction}
TOUCH ANGLE: {touch_angle_instruction}

ADVISOR: {advisor_name} at Restland Cemetery & Funeral Home
LEAD: {first_name} {last_name}
APPOINTMENT TYPE: {appt_label}
LEAD TIER: {tier}
SOURCE: {source} {source_year}

Respond ONLY with valid JSON (no markdown, no backticks):
{{"subject": "email subject line", "body": "2-3 sentence email body, no URLs", "should_stop": false, "stop_reason": "", "escalate": false, "escalate_reason": "", "confidence": 90}}
"""

REPLY_SYSTEM_PROMPT = """You are an AI assistant helping a Family Service Advisor at Restland Cemetery & Funeral Home respond to a lead's email reply.

The lead replied. Generate the ideal response to keep the conversation moving toward booking.

CRITICAL RULES:
- Read the lead's reply carefully. Respond directly to what they said.
- Be SMART — think about what this person actually needs right now.
- Compassionate and human. Never salesy.
- 2-3 sentences max. No filler.
- If they show ANY interest → offer to book immediately.
- If they ask a question → answer it and gently ask if they'd like to schedule.
- Never reveal you are AI.

ADVISOR: {advisor_name}
LEAD: {first_name} {last_name}
APPOINTMENT TYPE: {appt_label}

Respond ONLY with valid JSON (no markdown, no backticks):
{{"subject": "reply subject", "body": "your reply body, 2-3 sentences, no URLs", "should_book": false, "should_stop": false, "stop_reason": "", "escalate": false, "escalate_reason": "", "confidence": 90}}
"""

TONE_MAP = {
    "cold": "Soft and very low-pressure. Just opening a door. No ask yet.",
    "warm": "Friendly and conversational. Gently suggest meeting.",
    "hot": "Direct and confident. Clear ask for the appointment.",
    "urgent": "Compassionate urgency. They may need help now.",
}

TOUCH_ANGLE_MAP = {
    "warm_intro": "Introduce yourself warmly. Explain why you're reaching out specifically for them. Make it personal.",
    "value_proposition": "Focus on what Restland can do for them. What peace of mind looks like. Don't ask yet.",
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


def _get_booking_url(db: Session, lead: Lead, advisor: User) -> str:
    existing = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead.id, BookingLink.status == "pending")
        .order_by(BookingLink.created_at.desc())
        .first()
    )
    link = existing or create_booking_link(db, lead, advisor)
    return f"{BOOKING_BASE_URL}/book/{link.token}"


def _get_conversation_history(db: Session, lead: Lead) -> str:
    import re
    email_msgs = db.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id
    ).order_by(EmailMessage.sent_at.asc()).all()

    email_replies = db.query(Reply).filter(
        Reply.lead_id == lead.id,
        Reply.source == "email",
    ).order_by(Reply.received_at.asc()).all()

    events = []
    for m in email_msgs:
        body = re.sub(r'<[^>]+>', ' ', m.body_html or '').strip()[:200]
        events.append({"dir": "out", "subject": m.subject or "", "body": body, "ts": m.sent_at or datetime.min})
    for r in email_replies:
        events.append({"dir": "in", "body": (r.body or "")[:200], "ts": r.received_at or datetime.min})

    events.sort(key=lambda e: e["ts"])
    lines = []
    for e in events[-8:]:
        if e["dir"] == "out":
            lines.append(f"ADVISOR: [{e['subject']}] {e['body']}")
        else:
            lines.append(f"LEAD: {e['body']}")
    return "\n".join(lines) if lines else "No prior conversation."


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


def _send_email_via_graph(advisor: User, to_email: str, subject: str, body: str):
    import httpx
    from app.utils.crypto import decrypt_value

    resp = httpx.post(
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        data={
            "client_id": os.environ.get("MICROSOFT_CLIENT_ID"),
            "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET"),
            "refresh_token": decrypt_value(advisor.microsoft_oauth_refresh_token_encrypted),
            "grant_type": "refresh_token",
            "scope": "offline_access Mail.Read Mail.Send User.Read",
        },
        timeout=15,
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]

    send_resp = httpx.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body.replace('\n', '<br>')},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
            "saveToSentItems": True,
        },
        timeout=15,
    )
    if send_resp.status_code not in (200, 201, 202):
        raise Exception(f"Graph sendMail failed: {send_resp.status_code} {send_resp.text[:300]}")


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
<p style="color:#94a3b8;font-size:12px;margin-top:16px;">BookaBoost AI paused this conversation. Review and respond manually or click Resume AI on the lead page.</p>"""
        _send_email_via_graph(advisor, notification_email, subject, body)
    except Exception as e:
        logger.error("Escalation alert failed: %s", e)


def generate_touch_email(db: Session, lead: Lead, advisor: User, touch_number: int) -> dict:
    if touch_number >= len(TOUCH_ANGLES):
        return {"should_stop": True, "stop_reason": "Cadence complete"}

    angle = TOUCH_ANGLES[touch_number]
    tier = (lead.tier or "").lower()
    tone = "urgent" if tier in URGENT_TIERS else "warm"
    appt_label = _get_appt_label(lead)
    history = _get_conversation_history(db, lead)

    system = SMART_SYSTEM_PROMPT.format(
        appt_label=appt_label,
        tone_instruction=TONE_MAP.get(tone, TONE_MAP["warm"]),
        touch_angle_instruction=TOUCH_ANGLE_MAP.get(angle, ""),
        advisor_name=advisor.full_name or "Your Advisor",
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        tier=lead.tier or "unknown",
        source=lead.source_file or "",
        source_year=str(lead.source_year or ""),
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

    system = REPLY_SYSTEM_PROMPT.format(
        advisor_name=advisor.full_name or "Your Advisor",
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        appt_label=appt_label,
    )
    user_msg = f"Conversation history:\n{history}\n\nLead's latest reply:\n{reply_body}\n\nGenerate your response now."

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=0.5,
            max_tokens=300,
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

        _send_email_via_graph(advisor, lead.email, email_data["subject"], email_data["body"])

        msg = EmailMessage(
            id=str(uuid.uuid4()),
            lead_id=lead.id,
            sender_id=advisor.id,
            subject=email_data["subject"],
            body_html=email_data["body"].replace('\n', '<br>'),
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
    if lead.status in ("dnc", "booked") or lead.is_duplicate:
        return {"success": False, "error": "Lead is DNC, booked, or duplicate"}

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


def process_scheduled_touches(db: Session) -> dict:
    now = datetime.utcnow()
    due = db.query(PipelineConversation).filter(
        PipelineConversation.next_send_at <= now,
        PipelineConversation.paused == False,
        PipelineConversation.flagged == False,
        PipelineConversation.stage.notin_(["stopped", "completed", "booked"]),
    ).all()

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


def handle_inbound_reply(db: Session, lead: Lead, advisor: User, reply_body: str) -> dict:
    conv = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead.id,
        PipelineConversation.advisor_id == advisor.id,
    ).first()

    if not conv or conv.paused or conv.stage in ("stopped", "completed", "booked"):
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
        body_with_booking = result["body"] + f"\n\nHere's my booking link to pick a time: {booking_url}"
        try:
            _send_email_via_graph(advisor, lead.email, result["subject"], body_with_booking)
            conv.stage = "booking_sent"
            conv.booking_link_sent_at = datetime.utcnow()
            db.commit()
            return {"action": "booking_sent"}
        except Exception as e:
            logger.error("handle_inbound_reply booking error: %s", e)
            return {"action": "error", "error": str(e)}

    try:
        _send_email_via_graph(advisor, lead.email, result["subject"], result["body"])
        msg = EmailMessage(
            id=str(uuid.uuid4()),
            lead_id=lead.id,
            sender_id=advisor.id,
            subject=result["subject"],
            body_html=result["body"].replace('\n', '<br>'),
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
def generate_auto_reply(db: Session, lead: Lead, advisor: User, tone: str = "warm") -> dict:
    result = generate_touch_email(db, lead, advisor, touch_number=0)
    booking_url = _get_booking_url(db, lead, advisor)
    return {
        "reply": result.get("body", ""),
        "subject": result.get("subject", ""),
        "should_stop": result.get("should_stop", False),
        "reason": result.get("stop_reason", ""),
        "source": result.get("source", "ai"),
        "booking_url": booking_url,
    }
