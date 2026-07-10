"""
BookaBoost Pipeline Service
Full AI conversation pipeline — from first outreach to confirmed appointment.

Flow:
  1. Advisor launches pipeline for selected leads with context
  2. AI sends first outreach message
  3. Lead replies → AI reads it with confidence scoring
  4. High confidence (≥85%) → AI responds automatically after 2-5 min delay
  5. Low confidence (<85%) → flagged for advisor review with suggested response
  6. Lead books → confirmation fires → FSA notified via SMS
  7. Reminders fire 24hr and 2hr before appointment
  8. Appointment kept → outcome recorded → pipeline closes
"""

import json
import logging
import os
import random
from datetime import datetime
from typing import Any

from openai import OpenAI
from sqlalchemy.orm import Session

from app.models.models import (
    Lead, User, Reply, Message, BookingLink,
    PipelineConversation, Notification
)
from app.services.sms_service import BOOKING_BASE_URL, create_booking_link

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 85  # below this → flag for human review
MIN_DELAY_SECONDS = 120    # 2 minutes
MAX_DELAY_SECONDS = 300    # 5 minutes

_client = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


# ── Notification helpers ──────────────────────────────────────────────────────

def _notify_fsa_sms(advisor: User, message: str) -> None:
    """Send an SMS alert to the FSA's registered phone number."""
    try:
        phone = getattr(advisor, 'notification_phone', None) or getattr(advisor, 'twilio_phone_number', None)
        if not phone:
            logger.warning("FSA %s has no notification phone set", advisor.id)
            return
        from twilio.rest import Client
        from app.utils.crypto import decrypt_value
        if not advisor.twilio_account_sid or not advisor.twilio_auth_token_encrypted:
            return
        auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
        client = Client(advisor.twilio_account_sid, auth_token)
        client.messages.create(body=message, from_=advisor.twilio_phone_number, to=phone)
    except Exception as e:
        logger.error("FSA notification SMS failed: %s", e)


# ── Conversation history ──────────────────────────────────────────────────────

def _get_conversation(db: Session, lead_id: str) -> tuple[str, str]:
    """Returns (history_text, latest_inbound_reply)"""
    messages = db.query(Message).filter(Message.lead_id == lead_id).order_by(Message.sent_at.asc()).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead_id).order_by(Reply.received_at.asc()).all()

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

    history = "\n".join(
        f"{'Advisor' if e['dir'] == 'out' else 'Lead'}: {e['body']}"
        for e in events[-12:]
    )
    return history or "No prior conversation.", latest_inbound


# ── Core AI response generator ────────────────────────────────────────────────

PIPELINE_PROMPT = """You are an AI assistant managing SMS conversations for a service business advisor.
Your goal is to move leads toward booking an appointment through natural, human conversation.

Advisor: {advisor_name}
Business: {org_name}
Lead type/context: {lead_type}
Tone instruction: {tone}
AI direction: {ai_direction}

Lead: {first_name} {last_name}
Booking link: {booking_url}

Conversation history:
{history}

Latest message from lead:
{latest_inbound}

Analyze this conversation and respond with JSON only:
{{
  "reply": "The exact message to send (under 320 chars, sound human)",
  "confidence": 92,
  "should_stop": false,
  "stop_reason": null,
  "intent": "interested",
  "reasoning": "One sentence explaining your response choice",
  "include_booking_link": true,
  "stage": "booking_sent"
}}

Intent options: interested | objection | callback_request | question | not_interested | dnc | booked | unknown
Stage options: outreach_sent | replied | ai_responding | booking_sent | booked | stopped | dnc

Rules:
- confidence: 0-100. Be honest. Complex objections should score 60-75. Clear interest scores 85-95.
- should_stop: true ONLY if lead is booked, explicitly said DNC/STOP, or is clearly not interested after multiple attempts
- Never reveal you are AI
- Always move toward booking — include the booking link when it feels natural
- Keep messages under 320 characters
- Sound like a real person, not a script"""


def analyze_and_respond(
    db: Session,
    lead: Lead,
    advisor: User,
    pipeline: PipelineConversation,
) -> dict[str, Any]:
    """
    Core function — analyzes latest reply and generates response with confidence score.
    Returns full analysis dict.
    """
    try:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
        org_name = org.name if org else "our organization"
    except Exception:
        org_name = "our organization"

    existing_booking = db.query(BookingLink).filter(
        BookingLink.lead_id == lead.id, BookingLink.status == "pending"
    ).order_by(BookingLink.created_at.desc()).first()
    booking = existing_booking or create_booking_link(db, lead, advisor)
    booking_url = f"{BOOKING_BASE_URL}/book/{booking.token}"

    history, latest_inbound = _get_conversation(db, lead.id)

    tone_map = {
        "cold": "Soft, low-pressure, just opening the door",
        "warm": "Friendly, suggest a conversation, low-key CTA",
        "hot": "Direct and confident, clear ask for the appointment",
        "urgent": "Brief, time-sensitive, gentle urgency",
    }
    tone_desc = tone_map.get(pipeline.tone or "warm", tone_map["warm"])

    prompt = PIPELINE_PROMPT.format(
        advisor_name=advisor.full_name or "your advisor",
        org_name=org_name,
        lead_type=pipeline.lead_type or lead.message_track or lead.tier or "general outreach",
        tone=tone_desc,
        ai_direction=pipeline.ai_direction or "move conversation toward booking appointment",
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        booking_url=booking_url,
        history=history,
        latest_inbound=latest_inbound or "No reply yet — send initial outreach",
    )

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        return {
            "reply": data.get("reply", ""),
            "confidence": int(data.get("confidence", 70)),
            "should_stop": bool(data.get("should_stop", False)),
            "stop_reason": data.get("stop_reason"),
            "intent": data.get("intent", "unknown"),
            "reasoning": data.get("reasoning", ""),
            "include_booking_link": bool(data.get("include_booking_link", False)),
            "stage": data.get("stage", "ai_responding"),
            "booking_url": booking_url,
            "source": "ai",
        }
    except Exception as e:
        logger.error("Pipeline AI analysis failed: %s", e)
        fallback = f"Hi {lead.first_name or 'there'}, just checking in. Ready to connect when you are — {booking_url}"
        return {
            "reply": fallback,
            "confidence": 50,
            "should_stop": False,
            "stop_reason": None,
            "intent": "unknown",
            "reasoning": "AI unavailable, using fallback",
            "include_booking_link": True,
            "stage": "ai_responding",
            "booking_url": booking_url,
            "source": "fallback",
        }


def process_inbound_reply(
    db: Session,
    lead: Lead,
    advisor: User,
    reply: Reply,
) -> dict[str, Any]:
    """
    Called when a new inbound reply arrives.
    Finds or creates pipeline, analyzes reply, decides auto-send vs flag.
    Returns action taken.
    """
    # Find active pipeline for this lead
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead.id,
        PipelineConversation.stage.notin_(["stopped", "dnc", "sale", "kept"]),
    ).order_by(PipelineConversation.created_at.desc()).first()

    if not pipeline:
        # No active pipeline — create one automatically
        pipeline = PipelineConversation(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            advisor_id=advisor.id,
            stage="replied",
            tone="warm",
            auto_respond=True,
            confidence_threshold=CONFIDENCE_THRESHOLD,
            response_delay_seconds=random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS),
        )
        db.add(pipeline)

    # Update engagement stats
    pipeline.replies_received = (pipeline.replies_received or 0) + 1
    pipeline.last_inbound_at = datetime.utcnow()
    pipeline.stage = "replied"

    # Analyze the reply
    analysis = analyze_and_respond(db, lead, advisor, pipeline)

    if analysis["should_stop"]:
        pipeline.stage = "stopped" if analysis["intent"] != "dnc" else "dnc"
        db.commit()
        return {"action": "stopped", "reason": analysis["stop_reason"], "pipeline_id": pipeline.id}

    confidence = analysis["confidence"]
    threshold = pipeline.confidence_threshold or CONFIDENCE_THRESHOLD

    if analysis.get("intent") == "booked":
        pipeline.stage = "booked"
        pipeline.booked_at = datetime.utcnow()
        db.commit()
        _notify_fsa_sms(
            advisor,
            f"📅 BookaBoost: {lead.first_name or 'A lead'} {lead.last_name or ''} just booked! Check your calendar. — BookaBoost"
        )
        return {"action": "booked", "pipeline_id": pipeline.id}

    if confidence >= threshold and pipeline.auto_respond:
        # Auto-send with delay
        pipeline.stage = "ai_responding"
        pipeline.ai_responses_sent = (pipeline.ai_responses_sent or 0) + 1
        pipeline.last_outbound_at = datetime.utcnow()
        db.commit()

        # Send the response
        try:
            from app.services.sms_service import send_sms
            send_sms(
                db=db,
                lead=lead,
                advisor=advisor,
                template=analysis["reply"],
                include_booking_link=analysis["include_booking_link"],
            )
            pipeline.messages_sent = (pipeline.messages_sent or 0) + 1
            if analysis["stage"]:
                pipeline.stage = analysis["stage"]
            db.commit()
            return {
                "action": "auto_sent",
                "confidence": confidence,
                "reply": analysis["reply"],
                "pipeline_id": pipeline.id,
            }
        except Exception as e:
            logger.error("Pipeline auto-send failed: %s", e)
            return {"action": "error", "error": str(e), "pipeline_id": pipeline.id}
    else:
        # Flag for human review
        pipeline.flagged = True
        pipeline.flag_reason = f"Confidence {confidence}% below threshold {threshold}% — needs review"
        pipeline.flagged_reply_body = reply.body
        pipeline.flagged_suggested_response = analysis["reply"]
        pipeline.flagged_at = datetime.utcnow()
        pipeline.stage = "replied"
        pipeline.ai_responses_flagged = (pipeline.ai_responses_flagged or 0) + 1
        db.commit()

        # Notify FSA about flagged reply
        _notify_fsa_sms(
            advisor,
            f"⚠️ BookaBoost: {lead.first_name or 'A lead'} replied and needs your attention. Log in to review. — BookaBoost"
        )

        return {
            "action": "flagged",
            "confidence": confidence,
            "flag_reason": pipeline.flag_reason,
            "suggested_response": analysis["reply"],
            "pipeline_id": pipeline.id,
        }


def launch_pipeline(
    db: Session,
    leads: list[Lead],
    advisor: User,
    lead_type: str,
    tone: str,
    ai_direction: str,
    channel: str = "sms",
    auto_respond: bool = True,
) -> dict[str, Any]:
    """
    Launch the pipeline for a list of leads.
    Sends first outreach and creates pipeline records.
    """
    from app.services.sms_service import send_sms

    launched = 0
    skipped = 0
    errors = 0

    for lead in leads:
        try:
            if lead.status == "dnc" or lead.is_duplicate:
                skipped += 1
                continue

            # Create pipeline record
            pipeline = PipelineConversation(
                organization_id=lead.organization_id,
                lead_id=lead.id,
                advisor_id=advisor.id,
                stage="outreach_sent",
                lead_type=lead_type,
                channel=channel,
                tone=tone,
                ai_direction=ai_direction,
                auto_respond=auto_respond,
                confidence_threshold=CONFIDENCE_THRESHOLD,
                response_delay_seconds=random.randint(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS),
                messages_sent=1,
                last_outbound_at=datetime.utcnow(),
            )
            db.add(pipeline)
            db.flush()

            # Generate first message
            analysis = analyze_and_respond(db, lead, advisor, pipeline)

            if channel in ("sms", "both") and lead.phone:
                send_sms(
                    db=db,
                    lead=lead,
                    advisor=advisor,
                    template=analysis["reply"],
                    include_booking_link=True,
                )
            launched += 1

        except Exception as e:
            logger.error("Pipeline launch error for lead %s: %s", lead.id, e)
            errors += 1

    db.commit()
    return {"launched": launched, "skipped": skipped, "errors": errors}


def get_pipeline_stats(db: Session, organization_id: str) -> dict:
    """Returns pipeline engagement stats for the overview dashboard."""
    from sqlalchemy import func

    pipelines = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == organization_id
    ).all()

    stage_counts = {}
    for p in pipelines:
        stage_counts[p.stage] = stage_counts.get(p.stage, 0) + 1

    flagged = [p for p in pipelines if p.flagged and not p.reviewed_at]
    total_messages = sum(p.messages_sent or 0 for p in pipelines)
    total_replies = sum(p.replies_received or 0 for p in pipelines)
    total_booked = stage_counts.get("booked", 0) + stage_counts.get("confirmed", 0) + stage_counts.get("kept", 0) + stage_counts.get("sale", 0)

    return {
        "total_in_pipeline": len(pipelines),
        "by_stage": stage_counts,
        "flagged_count": len(flagged),
        "flagged": [
            {
                "pipeline_id": p.id,
                "lead_id": p.lead_id,
                "flag_reason": p.flag_reason,
                "flagged_reply": p.flagged_reply_body,
                "suggested_response": p.flagged_suggested_response,
                "flagged_at": p.flagged_at,
            }
            for p in flagged[:10]
        ],
        "total_messages_sent": total_messages,
        "total_replies_received": total_replies,
        "total_booked": total_booked,
        "ai_auto_sent": sum(p.ai_responses_sent or 0 for p in pipelines),
        "ai_flagged": sum(p.ai_responses_flagged or 0 for p in pipelines),
    }


def get_ai_forecast(db: Session, organization_id: str) -> dict:
    """
    AI-powered forecast for the overview dashboard.
    Analyzes current pipeline to predict upcoming appointments and surface alerts.
    """
    stats = get_pipeline_stats(db, organization_id)
    stage_counts = stats["by_stage"]

    alerts = []
    
    if stats["flagged_count"] > 0:
        alerts.append({
            "type": "urgent",
            "message": f"{stats['flagged_count']} conversations need your review — AI wasn't confident enough to auto-respond",
            "action": "Review now",
            "path": "/pipeline",
        })

    booking_sent = stage_counts.get("booking_sent", 0)
    if booking_sent > 0:
        alerts.append({
            "type": "opportunity",
            "message": f"{booking_sent} leads have your booking link but haven't scheduled yet — follow up today",
            "action": "View leads",
            "path": "/pipeline",
        })

    replied_count = stage_counts.get("replied", 0)
    if replied_count > 0:
        alerts.append({
            "type": "info",
            "message": f"{replied_count} leads replied and are in active conversation",
            "action": "View pipeline",
            "path": "/pipeline",
        })

    # Simple forecast
    active = stats["total_in_pipeline"] - stage_counts.get("stopped", 0) - stage_counts.get("dnc", 0) - stage_counts.get("sale", 0)
    reply_rate = (stats["total_replies_received"] / max(1, stats["total_messages_sent"])) * 100
    projected_bookings = round(active * (reply_rate / 100) * 0.3)  # ~30% of replies convert

    return {
        "alerts": alerts,
        "active_conversations": active,
        "flagged_count": stats["flagged_count"],
        "reply_rate": round(reply_rate, 1),
        "projected_bookings_this_week": projected_bookings,
        "booking_sent_count": booking_sent,
        "stage_counts": stage_counts,
    }
