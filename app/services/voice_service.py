"""
Voice Service — OpenAI Realtime + Twilio Media Streams
Bridges Twilio's real-time audio WebSocket to OpenAI Realtime API.
Handles: outbound calls, voicemail detection, booking confirmation.

Architecture:
  Twilio call → WebSocket /voice/stream → this service
  → OpenAI Realtime API (bidirectional audio)
  → AI speaks, listens, books if lead says yes

OpenAI Realtime API: wss://api.openai.com/v1/realtime
Model: gpt-4o-realtime-preview
"""

import asyncio
import json
import logging
import os
import base64
from typing import Optional

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

# Escalation triggers for voice
ESCALATION_PHRASES = [
    "stop calling", "don't call", "remove me", "attorney", "lawyer",
    "harassment", "police", "sue", "wrong number",
]

URGENT_TIERS = {"at_need", "atneed", "at-need", "imminent", "urgent"}


def build_voice_system_prompt(lead_info: dict, advisor_info: dict, call_number: int = 1) -> str:
    """Build the AI system prompt for the voice call."""
    first_name = lead_info.get("first_name", "there")
    last_name = lead_info.get("last_name", "")
    tier = (lead_info.get("tier") or "").lower()
    appt_label = lead_info.get("appt_label", "Family Services Appointment")
    advisor_name = advisor_info.get("name", "Mike Simmons")
    org_name = advisor_info.get("org", "Restland Cemetery and Funeral Home")
    booking_url = lead_info.get("booking_url", "")

    is_urgent = tier in URGENT_TIERS

    if call_number == 1:
        opening_context = f"This is your first call to {first_name}."
    elif call_number == 2:
        opening_context = f"You called {first_name} once before but didn't reach them. This is your second attempt."
    else:
        opening_context = f"This is your third and final call attempt to {first_name}."

    urgency_note = (
        "This is an at-need or urgent situation. Be compassionate and move with care — they may be in crisis."
        if is_urgent else
        "This is a pre-need or non-urgent situation. Be warm, patient, and low-pressure."
    )

    return f"""You are an AI assistant making a phone call on behalf of {advisor_name} at {org_name} in Dallas, Texas.

CRITICAL DISCLOSURE: You MUST disclose you are an AI at the start of every call. Texas law requires this.

YOUR OPENING (say this exactly, then adapt):
"Hi, is this {first_name}? ... Hi {first_name}, I'm an AI assistant calling on behalf of {advisor_name} at {org_name}. Is it alright if I speak with you in English for just a moment?"

If they say yes or seem receptive, continue naturally.
If they say no or seem confused, ask "Would you prefer I call back at a better time?"

CALL CONTEXT:
{opening_context}
{urgency_note}

YOUR GOAL: Briefly introduce why you're calling regarding a {appt_label}, and ask if they'd be open to scheduling a quick appointment with {advisor_name}.

RULES:
- Sound warm, human, and natural. Not robotic.
- Be BRIEF — this is a phone call, not a presentation.
- 2-3 sentences max before asking a question.
- Never be pushy or high pressure.
- If they say yes to meeting → say "Wonderful! I'll send a booking link to your email or phone right now." Then trigger booking.
- If they say no → "I completely understand. Would it be okay if we reached out another time?" Then end gracefully.
- If they seem angry or mention legal action → apologize and end the call immediately.
- If voicemail detected → leave a brief, friendly voicemail (under 30 seconds).

VOICEMAIL SCRIPT (if no answer):
"Hi {first_name}, this is an AI assistant calling on behalf of {advisor_name} at {org_name}. I'm reaching out regarding a {appt_label}. Please give us a call back or visit {booking_url} to schedule a time that works for you. We look forward to connecting. Have a wonderful day."

BOOKING URL: {booking_url}

Respond naturally and conversationally. Keep responses SHORT — this is voice, not text."""


def build_twilio_twiml_outbound(lead_phone: str, stream_url: str, call_sid_placeholder: str = "") -> str:
    """Generate TwiML for outbound call with media stream."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url}">
      <Parameter name="direction" value="outbound"/>
    </Stream>
  </Connect>
</Response>"""


def build_twilio_twiml_voicemail(message: str) -> str:
    """Generate TwiML to leave a voicemail using TTS."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Joanna" rate="95%">{message}</Say>
  <Hangup/>
</Response>"""


async def handle_realtime_session(
    websocket,
    lead_info: dict,
    advisor_info: dict,
    call_number: int,
    on_booking_detected=None,
    on_escalation_detected=None,
):
    """
    Main WebSocket handler for OpenAI Realtime session.
    Called when Twilio connects the media stream to our WebSocket endpoint.
    
    websocket: the Twilio media stream WebSocket connection
    on_booking_detected: async callback when lead agrees to book
    on_escalation_detected: async callback when escalation detected
    """
    import websockets

    system_prompt = build_voice_system_prompt(lead_info, advisor_info, call_number)
    stream_sid = None
    latest_transcript = ""
    booking_detected = False
    escalation_detected = False

    try:
        async with websockets.connect(
            REALTIME_URL,
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            }
        ) as openai_ws:
            logger.info("OpenAI Realtime connected for lead=%s", lead_info.get("id"))

            # Configure the session
            await openai_ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad"},
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "voice": "alloy",
                    "instructions": system_prompt,
                    "modalities": ["text", "audio"],
                    "temperature": 0.7,
                }
            }))

            # Trigger AI to speak first
            await openai_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "The call just connected. Begin with your opening."}]
                }
            }))
            await openai_ws.send(json.dumps({"type": "response.create"}))

            async def receive_from_twilio():
                nonlocal stream_sid
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        event = data.get("event")

                        if event == "start":
                            stream_sid = data["start"]["streamSid"]
                            logger.info("Twilio stream started: %s", stream_sid)

                        elif event == "media":
                            audio_payload = data["media"]["payload"]
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": audio_payload,
                            }))

                        elif event == "stop":
                            logger.info("Twilio stream stopped")
                            break

                except Exception as e:
                    logger.error("receive_from_twilio error: %s", e)

            async def receive_from_openai():
                nonlocal latest_transcript, booking_detected, escalation_detected
                try:
                    async for message in openai_ws:
                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "response.audio.delta" and data.get("delta"):
                            if stream_sid:
                                await websocket.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": data["delta"]}
                                }))

                        elif msg_type == "response.audio_transcript.delta":
                            latest_transcript += data.get("delta", "")

                        elif msg_type == "response.audio_transcript.done":
                            transcript = data.get("transcript", "")
                            latest_transcript = transcript
                            logger.info("AI transcript: %s", transcript[:100])

                        elif msg_type == "conversation.item.input_audio_transcription.completed":
                            user_text = data.get("transcript", "").lower()
                            logger.info("Lead said: %s", user_text[:100])

                            # Check for booking
                            booking_words = ["yes", "sure", "sounds good", "okay", "i'm interested",
                                           "let's do it", "set it up", "schedule", "book"]
                            if any(w in user_text for w in booking_words) and not booking_detected:
                                booking_detected = True
                                if on_booking_detected:
                                    await on_booking_detected()

                            # Check for escalation
                            for phrase in ESCALATION_PHRASES:
                                if phrase in user_text and not escalation_detected:
                                    escalation_detected = True
                                    if on_escalation_detected:
                                        await on_escalation_detected(phrase)
                                    break

                        elif msg_type == "response.done":
                            logger.info("OpenAI response complete")

                        elif msg_type == "error":
                            logger.error("OpenAI Realtime error: %s", data)

                except Exception as e:
                    logger.error("receive_from_openai error: %s", e)

            await asyncio.gather(receive_from_twilio(), receive_from_openai())

    except Exception as e:
        logger.error("handle_realtime_session error: %s", e)

    return {
        "transcript": latest_transcript,
        "booking_detected": booking_detected,
        "escalation_detected": escalation_detected,
        "stream_sid": stream_sid,
    }


def initiate_outbound_call(
    advisor_twilio_sid: str,
    advisor_twilio_token: str,
    advisor_phone: str,
    lead_phone: str,
    twiml_url: str,
    status_callback_url: str,
) -> dict:
    """Make an outbound call via Twilio REST API."""
    from twilio.rest import Client

    client = Client(advisor_twilio_sid, advisor_twilio_token)
    try:
        call = client.calls.create(
            to=lead_phone,
            from_=advisor_phone,
            url=twiml_url,
            status_callback=status_callback_url,
            status_callback_method="POST",
            record=True,
            recording_status_callback=status_callback_url.replace("/status", "/recording"),
        )
        logger.info("Outbound call created: SID=%s to=%s", call.sid, lead_phone)
        return {"success": True, "call_sid": call.sid, "status": call.status}
    except Exception as e:
        logger.error("initiate_outbound_call error: %s", e)
        return {"success": False, "error": str(e)}
