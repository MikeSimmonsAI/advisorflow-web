"""
Voice Router
Handles:
- POST /voice/call/{lead_id}         — initiate outbound AI call
- POST /voice/twiml/{lead_id}        — Twilio fetches TwiML for call
- WS   /voice/stream                 — Twilio media stream WebSocket
- POST /voice/status                 — Twilio call status callbacks
- POST /voice/recording              — Twilio recording callbacks
- GET  /voice/calls                  — list calls for current advisor
- GET  /voice/calls/{call_id}        — get single call detail
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, VoiceCall
from app.services.voice_service import (
    build_twilio_twiml_outbound,
    build_twilio_twiml_voicemail,
    build_voice_system_prompt,
    initiate_outbound_call,
    handle_realtime_session,
)
from app.services.sms_service import BOOKING_BASE_URL, create_booking_link
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/voice", tags=["voice"])
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "https://advisorflow-backend.onrender.com")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://advisorflow-frontend.onrender.com")

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
    "veteran": "Veterans Benefits Consultation",
    "insurance": "Insurance & Benefits Review",
    "web_lead": "General Consultation",
    "referral": "Family Services Consultation",
}


def _get_appt_label(lead: Lead) -> str:
    return APPT_LABEL_MAP.get((lead.tier or "").lower(), "Family Services Appointment")


def _get_call_number(db: Session, lead_id: str, advisor_id: str) -> int:
    """How many calls have already been made to this lead?"""
    count = db.query(VoiceCall).filter(
        VoiceCall.lead_id == lead_id,
        VoiceCall.advisor_id == advisor_id,
    ).count()
    return count + 1


class InitiateCallRequest(BaseModel):
    lead_id: str


@router.post("/call/{lead_id}")
def initiate_call(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Initiate an outbound AI voice call to a lead.
    Creates VoiceCall record, then calls Twilio to start the call.
    Twilio fetches /voice/twiml/{lead_id} to get the call instructions.
    """
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.status == "dnc":
        raise HTTPException(status_code=400, detail="Lead is DNC")
    if not lead.phone:
        raise HTTPException(status_code=400, detail="Lead has no phone number")
    if not current_user.twilio_account_sid:
        raise HTTPException(status_code=400, detail="Twilio not connected. Go to Settings to connect.")

    # Check max 3 call attempts
    call_number = _get_call_number(db, lead_id, current_user.id)
    if call_number > 3:
        raise HTTPException(status_code=400, detail="Maximum 3 call attempts reached for this lead.")

    # Create VoiceCall record
    call = VoiceCall(
        id=str(uuid.uuid4()),
        lead_id=lead_id,
        advisor_id=current_user.id,
        organization_id=current_user.organization_id,
        to_phone=lead.phone,
        from_phone=current_user.twilio_phone_number,
        call_number=call_number,
        status="initiating",
        created_at=datetime.utcnow(),
    )
    db.add(call)
    db.commit()

    from app.utils.crypto import decrypt_value
    auth_token = decrypt_value(current_user.twilio_auth_token_encrypted)

    twiml_url = f"{BACKEND_URL}/voice/twiml/{lead_id}?call_id={call.id}&advisor_id={current_user.id}"
    status_url = f"{BACKEND_URL}/voice/status?call_id={call.id}"

    result = initiate_outbound_call(
        advisor_twilio_sid=current_user.twilio_account_sid,
        advisor_twilio_token=auth_token,
        advisor_phone=current_user.twilio_phone_number,
        lead_phone=lead.phone,
        twiml_url=twiml_url,
        status_callback_url=status_url,
    )

    if not result["success"]:
        call.status = "failed"
        call.error_message = result.get("error")
        db.commit()
        raise HTTPException(status_code=500, detail=result.get("error", "Call failed"))

    call.call_sid = result["call_sid"]
    call.status = "ringing"
    db.commit()

    log_action(db, current_user.organization_id, current_user.id,
               action="voice.call_initiated", target_type="lead", target_id=lead_id)

    return {
        "success": True,
        "call_id": call.id,
        "call_sid": call.call_sid,
        "call_number": call_number,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
    }


@router.post("/twiml/{lead_id}")
async def get_twiml(
    lead_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Public endpoint — Twilio fetches this to get call instructions.
    Returns TwiML that connects the call to our WebSocket media stream.
    No auth — Twilio validates via signature.
    """
    call_id = request.query_params.get("call_id", "")
    advisor_id = request.query_params.get("advisor_id", "")

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    advisor = db.query(User).filter(User.id == advisor_id).first() if advisor_id else None

    if not lead:
        twiml = build_twilio_twiml_voicemail("Sorry, we were unable to connect this call. Goodbye.")
        return Response(content=twiml, media_type="application/xml")

    # WebSocket URL for the media stream
    # Render Web Services support WebSockets natively
    ws_url = f"wss://{BACKEND_URL.replace('https://', '').replace('http://', '')}/voice/stream?call_id={call_id}&lead_id={lead_id}&advisor_id={advisor_id}"

    twiml = build_twilio_twiml_outbound(lead.phone, ws_url)
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/stream")
async def voice_stream(
    websocket: WebSocket,
    db: Session = Depends(get_db),
):
    """
    WebSocket endpoint — Twilio Media Streams connects here.
    Bridges audio between Twilio and OpenAI Realtime API.
    """
    await websocket.accept()

    # Get params from query string
    call_id = websocket.query_params.get("call_id", "")
    lead_id = websocket.query_params.get("lead_id", "")
    advisor_id = websocket.query_params.get("advisor_id", "")

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    advisor = db.query(User).filter(User.id == advisor_id).first()
    call = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()

    if not lead or not advisor:
        await websocket.close()
        return

    # Build lead and advisor info for prompt
    booking_link = create_booking_link(db, lead, advisor)
    booking_url = f"{BOOKING_BASE_URL}/book/{booking_link.token}"

    lead_info = {
        "id": lead.id,
        "first_name": lead.first_name or "",
        "last_name": lead.last_name or "",
        "tier": lead.tier or "",
        "appt_label": _get_appt_label(lead),
        "booking_url": booking_url,
    }

    advisor_info = {
        "name": advisor.full_name or "Mike Simmons",
        "org": "Restland Cemetery and Funeral Home",
        "phone": advisor.twilio_phone_number or "",
    }

    call_number = call.call_number if call else 1

    async def on_booking_detected():
        """Lead agreed to book — send confirmation email/SMS."""
        logger.info("Booking detected on call for lead=%s", lead_id)
        if call:
            call.outcome = "booking_requested"
            call.booking_url_sent = True
            db.commit()

        # Send booking link via email
        if lead.email and advisor.microsoft_365_connected:
            try:
                from app.services.ai_conversation_service import _send_email_via_graph
                subject = f"Your booking link — {_get_appt_label(lead)}"
                body = (
                    f"Hi {lead.first_name or 'there'},\n\n"
                    f"As promised during our call, here's your booking link to schedule your "
                    f"{_get_appt_label(lead)} with {advisor.full_name} at Restland Cemetery & Funeral Home:\n\n"
                    f"{booking_url}\n\n"
                    f"We look forward to connecting with you.\n\n"
                    f"Best,\n{advisor.full_name}"
                )
                _send_email_via_graph(advisor, lead.email, subject, body)
            except Exception as e:
                logger.error("on_booking_detected email error: %s", e)

    async def on_escalation_detected(phrase: str):
        """Lead said something requiring human review."""
        logger.info("Escalation detected on call: %s for lead=%s", phrase, lead_id)
        if call:
            call.outcome = "escalated"
            call.escalation_reason = f"Phrase detected: '{phrase}'"
            db.commit()

        # Notify advisor
        notification_email = getattr(advisor, 'notification_email', None) or "michael.simmons@nsmg.com"
        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip()
        try:
            from app.services.ai_conversation_service import _send_email_via_graph
            _send_email_via_graph(
                advisor, notification_email,
                f"⚠️ Voice Call Escalated — {lead_name}",
                f"<p>AI voice call with <strong>{lead_name}</strong> was escalated.</p>"
                f"<p><strong>Trigger phrase:</strong> '{phrase}'</p>"
                f"<p>The call was ended gracefully. You may want to follow up manually.</p>"
                f"<p><a href='{FRONTEND_URL}/leads/{lead_id}'>View lead →</a></p>"
            )
        except Exception as e:
            logger.error("on_escalation_detected email error: %s", e)

    if call:
        call.status = "in_progress"
        call.started_at = datetime.utcnow()
        db.commit()

    try:
        result = await handle_realtime_session(
            websocket=websocket,
            lead_info=lead_info,
            advisor_info=advisor_info,
            call_number=call_number,
            on_booking_detected=on_booking_detected,
            on_escalation_detected=on_escalation_detected,
        )

        if call:
            call.transcript = result.get("transcript", "")
            call.status = "completed"
            call.ended_at = datetime.utcnow()
            if call.started_at:
                call.duration_seconds = int((call.ended_at - call.started_at).total_seconds())
            if not call.outcome:
                call.outcome = "completed"
            db.commit()

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for call=%s", call_id)
        if call and call.status == "in_progress":
            call.status = "completed"
            call.ended_at = datetime.utcnow()
            if not call.outcome:
                call.outcome = "no_answer"
            db.commit()
    except Exception as e:
        logger.error("voice_stream error: %s", e)
        if call:
            call.status = "failed"
            call.error_message = str(e)
            db.commit()


@router.post("/status")
async def call_status_callback(request: Request, db: Session = Depends(get_db)):
    """
    Twilio calls this when call status changes.
    Handles: no-answer → leave voicemail, completed, failed.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    call_id = request.query_params.get("call_id", "")

    call = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()
    if not call:
        # Try by call_sid
        call = db.query(VoiceCall).filter(VoiceCall.call_sid == call_sid).first()

    logger.info("Call status: call_id=%s call_sid=%s status=%s", call_id, call_sid, call_status)

    if call:
        call.twilio_status = call_status

        if call_status == "no-answer" or call_status == "busy":
            call.outcome = "no_answer"
            call.status = "completed"
            # Leave voicemail on next attempt via TwiML redirect
            # Twilio will retry with the voicemail TwiML
        elif call_status == "completed":
            if not call.outcome:
                call.outcome = "completed"
            call.status = "completed"
        elif call_status == "failed" or call_status == "canceled":
            call.outcome = "failed"
            call.status = "failed"

        call.ended_at = datetime.utcnow()
        db.commit()

    # For no-answer, return TwiML to leave voicemail
    if call_status == "no-answer":
        lead = db.query(Lead).filter(Lead.id == call.lead_id).first() if call else None
        advisor = db.query(User).filter(User.id == call.advisor_id).first() if call else None

        if lead and advisor:
            appt_label = _get_appt_label(lead)
            booking_link = create_booking_link(db, lead, advisor)
            booking_url = f"{BOOKING_BASE_URL}/book/{booking_link.token}"

            voicemail_msg = (
                f"Hi {lead.first_name or 'there'}, this is an AI assistant calling on behalf of "
                f"{advisor.full_name or 'Mike Simmons'} at Restland Cemetery and Funeral Home. "
                f"I'm reaching out regarding a {appt_label}. "
                f"Please feel free to give us a call back or visit our website to schedule a convenient time. "
                f"We look forward to connecting with you. Have a wonderful day."
            )
            twiml = build_twilio_twiml_voicemail(voicemail_msg)
            return Response(content=twiml, media_type="application/xml")

    return Response(content="<?xml version='1.0'?><Response/>", media_type="application/xml")


@router.post("/recording")
async def recording_callback(request: Request, db: Session = Depends(get_db)):
    """Twilio calls this when recording is available."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    recording_url = form.get("RecordingUrl", "")
    recording_sid = form.get("RecordingSid", "")
    recording_duration = form.get("RecordingDuration", "0")

    call = db.query(VoiceCall).filter(VoiceCall.call_sid == call_sid).first()
    if call:
        call.recording_url = recording_url + ".mp3" if recording_url else None
        call.recording_sid = recording_sid
        if recording_duration:
            call.duration_seconds = int(recording_duration)
        db.commit()
        logger.info("Recording saved for call=%s url=%s", call.id, call.recording_url)

    return Response(content="<?xml version='1.0'?><Response/>", media_type="application/xml")


@router.get("/calls")
def list_calls(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all voice calls for current advisor."""
    calls = db.query(VoiceCall).filter(
        VoiceCall.advisor_id == current_user.id,
    ).order_by(VoiceCall.created_at.desc()).limit(100).all()

    result = []
    for call in calls:
        lead = db.query(Lead).filter(Lead.id == call.lead_id).first()
        result.append({
            "id": call.id,
            "lead_id": call.lead_id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Unknown",
            "to_phone": call.to_phone,
            "call_number": call.call_number,
            "status": call.status,
            "outcome": call.outcome,
            "duration_seconds": call.duration_seconds,
            "recording_url": call.recording_url,
            "transcript": call.transcript,
            "created_at": call.created_at.isoformat() if call.created_at else None,
        })

    return result


@router.get("/calls/{call_id}")
def get_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single voice call with full transcript."""
    call = db.query(VoiceCall).filter(
        VoiceCall.id == call_id,
        VoiceCall.advisor_id == current_user.id,
    ).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    lead = db.query(Lead).filter(Lead.id == call.lead_id).first()
    return {
        "id": call.id,
        "lead_id": call.lead_id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Unknown",
        "to_phone": call.to_phone,
        "from_phone": call.from_phone,
        "call_number": call.call_number,
        "call_sid": call.call_sid,
        "status": call.status,
        "outcome": call.outcome,
        "duration_seconds": call.duration_seconds,
        "recording_url": call.recording_url,
        "transcript": call.transcript,
        "escalation_reason": call.escalation_reason,
        "booking_url_sent": call.booking_url_sent,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "created_at": call.created_at.isoformat() if call.created_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# INBOUND CALL HANDLER
# When a lead calls +14692241155 back, Twilio hits this endpoint.
# AI looks up caller by phone number and continues the conversation.
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/inbound")
async def handle_inbound_call(request: Request, db: Session = Depends(get_db)):
    """
    Public endpoint — Twilio calls this when someone calls our number.
    Looks up caller by phone number, finds their advisor, connects to AI.
    """
    form = await request.form()
    caller_phone = form.get("From", "").strip()
    call_sid = form.get("CallSid", "")

    logger.info("Inbound call from %s sid=%s", caller_phone, call_sid)

    # Look up lead by phone number
    lead = None
    advisor = None

    if caller_phone:
        # Try exact match first, then without country code
        clean_phone = caller_phone.lstrip("+1").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        from app.models.models import Lead as LeadModel, User as UserModel
        lead = db.query(LeadModel).filter(
            LeadModel.phone.contains(clean_phone)
        ).first()

        if lead:
            advisor = db.query(UserModel).filter(UserModel.id == lead.user_id).first()
            if not advisor:
                # Try organization's first advisor
                advisor = db.query(UserModel).filter(
                    UserModel.organization_id == lead.organization_id,
                    UserModel.role.in_(["advisor", "org_admin", "super_admin"])
                ).first()

    if not lead or not advisor:
        # Unknown caller — greet generically and offer to connect
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna" rate="95%">
        Thank you for calling. This is an AI assistant with BookaBoost.
        We were not able to locate your record.
        Please hold while we connect you, or call back during business hours.
    </Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Create inbound VoiceCall record
    call = VoiceCall(
        id=str(uuid.uuid4()),
        lead_id=lead.id,
        advisor_id=advisor.id,
        organization_id=advisor.organization_id,
        to_phone=advisor.twilio_phone_number or TWILIO_FROM_NUMBER,
        from_phone=caller_phone,
        call_number=1,
        status="ringing",
        created_at=datetime.utcnow(),
    )
    db.add(call)
    db.commit()

    # Connect to AI via WebSocket
    ws_url = f"wss://{BACKEND_URL.replace('https://', '').replace('http://', '')}/voice/stream?call_id={call.id}&lead_id={lead.id}&advisor_id={advisor.id}&direction=inbound"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}">
            <Parameter name="direction" value="inbound"/>
            <Parameter name="lead_id" value="{lead.id}"/>
            <Parameter name="advisor_id" value="{advisor.id}"/>
        </Stream>
    </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


# ══════════════════════════════════════════════════════════════════════════════
# BULK CALL CAMPAIGNS
# ══════════════════════════════════════════════════════════════════════════════

from app.models.models import VoiceCallCampaign
import json as _json
import threading


class CreateCampaignRequest(BaseModel):
    name: str
    lead_ids: list[str]
    description: Optional[str] = None
    concurrent_calls: int = 5
    scheduled_at: Optional[str] = None  # ISO datetime string, null = run now
    call_window_start: str = "09:00"
    call_window_end: str = "17:00"


@router.post("/campaigns")
def create_campaign(
    req: CreateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a bulk outbound call campaign.
    If scheduled_at is null, starts immediately.
    """
    if not req.lead_ids:
        raise HTTPException(status_code=400, detail="No leads selected")

    # Validate leads belong to this org
    from app.models.models import Lead as LeadModel
    valid_leads = db.query(LeadModel).filter(
        LeadModel.id.in_(req.lead_ids),
        LeadModel.organization_id == current_user.organization_id,
        LeadModel.status != "dnc",
        LeadModel.phone != None,
    ).all()

    if not valid_leads:
        raise HTTPException(status_code=400, detail="No valid leads with phone numbers found")

    valid_ids = [l.id for l in valid_leads]
    skipped = len(req.lead_ids) - len(valid_ids)

    scheduled = None
    if req.scheduled_at:
        try:
            scheduled = datetime.fromisoformat(req.scheduled_at.replace("Z", "+00:00"))
        except Exception:
            pass

    campaign = VoiceCallCampaign(
        organization_id=current_user.organization_id,
        advisor_id=current_user.id,
        name=req.name,
        description=req.description,
        lead_ids=_json.dumps(valid_ids),
        total_leads=len(valid_ids),
        concurrent_calls=min(req.concurrent_calls, 10),  # cap at 10 for safety
        call_window_start=req.call_window_start,
        call_window_end=req.call_window_end,
        scheduled_at=scheduled,
        status="pending" if scheduled else "running",
        created_at=datetime.utcnow(),
    )
    db.add(campaign)
    db.commit()

    log_action(db, current_user.organization_id, current_user.id,
               action="voice.campaign_created", target_type="campaign", target_id=campaign.id)

    # If no schedule — fire immediately in background thread
    if not scheduled:
        campaign.started_at = datetime.utcnow()
        db.commit()
        thread = threading.Thread(
            target=_run_campaign_background,
            args=(campaign.id, current_user.id, current_user.organization_id),
            daemon=True
        )
        thread.start()

    return {
        "success": True,
        "campaign_id": campaign.id,
        "total_leads": len(valid_ids),
        "skipped": skipped,
        "status": campaign.status,
        "scheduled_at": scheduled.isoformat() if scheduled else None,
        "message": f"Campaign started — {len(valid_ids)} calls queued" if not scheduled else f"Campaign scheduled for {scheduled}",
    }


def _run_campaign_background(campaign_id: str, advisor_id: str, org_id: str):
    """
    Background thread — runs bulk call campaign.
    Fires concurrent_calls at a time, waits for completion, fires next batch.
    """
    from app.deps import SessionLocal
    from app.models.models import Lead as LeadModel, User as UserModel, VoiceCallCampaign as VCCModel
    import time

    db = SessionLocal()
    try:
        campaign = db.query(VCCModel).filter(VCCModel.id == campaign_id).first()
        advisor = db.query(UserModel).filter(UserModel.id == advisor_id).first()
        if not campaign or not advisor:
            return

        lead_ids = _json.loads(campaign.lead_ids or "[]")
        concurrency = campaign.concurrent_calls or 5
        from app.utils.crypto import decrypt_value
        from twilio.rest import Client as TwilioClient

        auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
        twilio = TwilioClient(advisor.twilio_account_sid, auth_token)

        active_calls = {}  # call_sid -> lead_id

        for i, lead_id in enumerate(lead_ids):
            # Check if campaign was cancelled or paused
            db.refresh(campaign)
            if campaign.status in ("cancelled", "paused"):
                logger.info("Campaign %s %s — stopping", campaign_id, campaign.status)
                break

            # Check call window (CST = UTC-6)
            now_cst_hour = (datetime.utcnow().hour - 6) % 24
            window_start = int(campaign.call_window_start.split(":")[0])
            window_end = int(campaign.call_window_end.split(":")[0])
            if not (window_start <= now_cst_hour < window_end):
                logger.info("Campaign %s outside call window — waiting", campaign_id)
                time.sleep(300)  # wait 5 minutes and check again
                continue

            # Wait if at concurrency limit
            while len(active_calls) >= concurrency:
                time.sleep(3)
                # Check which calls completed
                completed = []
                for sid in list(active_calls.keys()):
                    try:
                        call_info = twilio.calls(sid).fetch()
                        if call_info.status in ("completed", "failed", "busy", "no-answer", "canceled"):
                            completed.append(sid)
                            campaign.calls_completed += 1
                            if call_info.status == "completed":
                                campaign.calls_answered += 1
                            db.commit()
                    except Exception:
                        completed.append(sid)
                for sid in completed:
                    active_calls.pop(sid, None)

            # Get lead
            lead = db.query(LeadModel).filter(LeadModel.id == lead_id).first()
            if not lead or not lead.phone or lead.status == "dnc":
                campaign.calls_failed += 1
                db.commit()
                continue

            # Create VoiceCall record
            call_record = VoiceCall(
                id=str(uuid.uuid4()),
                lead_id=lead.id,
                advisor_id=advisor_id,
                organization_id=org_id,
                to_phone=lead.phone,
                from_phone=TWILIO_FROM_NUMBER,
                call_number=1,
                status="initiating",
                created_at=datetime.utcnow(),
            )
            db.add(call_record)
            db.flush()

            # Fire Twilio call
            twiml_url = f"{BACKEND_URL}/voice/twiml/{lead.id}?call_id={call_record.id}&advisor_id={advisor_id}"
            status_url = f"{BACKEND_URL}/voice/status?call_id={call_record.id}"

            try:
                phone = lead.phone.strip()
                if not phone.startswith("+"):
                    phone = f"+1{phone.lstrip('1')}" if len(phone) == 10 else f"+{phone}"

                call = twilio.calls.create(
                    to=phone,
                    from_=TWILIO_FROM_NUMBER,
                    url=twiml_url,
                    status_callback=status_url,
                    status_callback_method="POST",
                    record=True,
                    recording_status_callback=f"{BACKEND_URL}/voice/recording",
                    timeout=30,
                    machine_detection="DetectMessageEnd",
                )
                call_record.call_sid = call.sid
                call_record.status = "ringing"
                active_calls[call.sid] = lead_id
                campaign.calls_initiated += 1
                db.commit()
                logger.info("Campaign %s fired call %s/%s to %s", campaign_id, i+1, len(lead_ids), phone)

            except Exception as e:
                call_record.status = "failed"
                call_record.error_message = str(e)
                campaign.calls_failed += 1
                db.commit()
                logger.error("Campaign %s call failed for lead %s: %s", campaign_id, lead_id, e)

            time.sleep(1)  # small delay between initiations

        # Wait for remaining active calls to complete
        while active_calls:
            time.sleep(5)
            completed = []
            for sid in list(active_calls.keys()):
                try:
                    call_info = twilio.calls(sid).fetch()
                    if call_info.status in ("completed", "failed", "busy", "no-answer", "canceled"):
                        completed.append(sid)
                        campaign.calls_completed += 1
                        db.commit()
                except Exception:
                    completed.append(sid)
            for sid in completed:
                active_calls.pop(sid, None)

        campaign.status = "completed"
        campaign.completed_at = datetime.utcnow()
        db.commit()
        logger.info("Campaign %s completed — %s/%s calls", campaign_id, campaign.calls_completed, campaign.total_leads)

    except Exception as e:
        logger.error("Campaign %s background error: %s", campaign_id, e)
        try:
            campaign.status = "failed"
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.get("/campaigns")
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all voice campaigns for current advisor's org."""
    campaigns = db.query(VoiceCallCampaign).filter(
        VoiceCallCampaign.organization_id == current_user.organization_id,
    ).order_by(VoiceCallCampaign.created_at.desc()).limit(50).all()

    return [{
        "id": c.id,
        "name": c.name,
        "status": c.status,
        "total_leads": c.total_leads,
        "calls_initiated": c.calls_initiated,
        "calls_completed": c.calls_completed,
        "calls_answered": c.calls_answered,
        "calls_voicemail": c.calls_voicemail,
        "calls_failed": c.calls_failed,
        "bookings_detected": c.bookings_detected,
        "concurrent_calls": c.concurrent_calls,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    } for c in campaigns]


@router.get("/campaigns/{campaign_id}")
def get_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get campaign details with real-time progress."""
    c = db.query(VoiceCallCampaign).filter(
        VoiceCallCampaign.id == campaign_id,
        VoiceCallCampaign.organization_id == current_user.organization_id,
    ).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")

    progress_pct = round((c.calls_completed / c.total_leads * 100) if c.total_leads > 0 else 0)

    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "status": c.status,
        "total_leads": c.total_leads,
        "calls_initiated": c.calls_initiated,
        "calls_completed": c.calls_completed,
        "calls_answered": c.calls_answered,
        "calls_voicemail": c.calls_voicemail,
        "calls_failed": c.calls_failed,
        "bookings_detected": c.bookings_detected,
        "progress_pct": progress_pct,
        "concurrent_calls": c.concurrent_calls,
        "call_window_start": c.call_window_start,
        "call_window_end": c.call_window_end,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
    }


@router.post("/campaigns/{campaign_id}/pause")
def pause_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(VoiceCallCampaign).filter(
        VoiceCallCampaign.id == campaign_id,
        VoiceCallCampaign.organization_id == current_user.organization_id,
    ).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    c.status = "paused"
    db.commit()
    return {"success": True}


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = db.query(VoiceCallCampaign).filter(
        VoiceCallCampaign.id == campaign_id,
        VoiceCallCampaign.organization_id == current_user.organization_id,
    ).first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    c.status = "cancelled"
    db.commit()
    return {"success": True}


TWILIO_FROM_NUMBER = "+14692241155"
