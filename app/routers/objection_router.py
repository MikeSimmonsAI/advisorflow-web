"""
AI Objection Library — POST /ai/objection-reply/{reply_id}

Classifies the objection type in a lead's reply and returns a scripted,
tone-adjusted response the advisor can review and send.

Objection types:
  not_interested   — "I'm not interested"
  need_to_think    — "I need to think about it" / "let me discuss with family"
  too_expensive    — price / cost concerns
  wrong_time       — bad timing, call back later
  callback_request — explicit request to call/talk
  question         — genuine question needing an answer
  already_have     — already have a plan / already covered
  interested       — positive reply, wants to proceed
  general          — anything else
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import json

from app.database import get_db
from app.auth import get_current_user
from app.models import SMSReply, Lead, User
from app.services.ai_service import call_claude

router = APIRouter(prefix="/ai", tags=["ai"])


class ObjectionReplyRequest(BaseModel):
    tone: Optional[str] = "standard"  # soft | standard | direct | urgent


class ObjectionReplyResponse(BaseModel):
    objection_type: str
    objection_reasoning: str
    confidence: float
    suggested_response: str
    talking_points: list[str]


TONE_INSTRUCTIONS = {
    "soft": "warm, empathetic, low pressure — give them space while keeping the door open",
    "standard": "professional and friendly — direct but not pushy",
    "direct": "clear and confident — address the objection head-on without being aggressive",
    "urgent": "create gentle urgency — acknowledge the objection but emphasize why acting sooner is better",
}

OBJECTION_SCRIPTS = {
    "not_interested": [
        "I completely understand — I never want to pressure anyone. Many families I work with felt the same way until they saw how much peace of mind it brought. Would it be okay if I shared just one thing that might change your perspective?",
        "No worries at all — I respect that. If you ever have questions about what we offer or want to explore options at your own pace, I'm always here. No pressure, no obligation.",
    ],
    "need_to_think": [
        "Of course — this is an important decision and you should take the time you need. Can I follow up in a few days to answer any questions that come up?",
        "Absolutely, take all the time you need. I'll check back in with you next week — and if anything comes up before then, don't hesitate to reach out.",
    ],
    "too_expensive": [
        "I hear you — cost is always a real consideration. The good news is we have options at several price points, and locking in today's pricing protects you from future increases. Could we find 10 minutes to look at what fits your budget?",
        "That's a fair concern and I want to make sure we find something that works for you. We have flexible payment options that might surprise you. Would it help to see what's available at different price ranges?",
    ],
    "wrong_time": [
        "Absolutely, I'll reach back out at a better time. When would work best for you — would next week be okay?",
        "No problem at all — I'll give you some space. When's a good time to reconnect? I want to make sure we connect when it's convenient for you.",
    ],
    "callback_request": [
        "Happy to call you! What's the best time to reach you, and is this the best number?",
        "Of course — I'd love to chat. What time works best for you? Morning or afternoon?",
    ],
    "question": [
        "Great question — I want to make sure I give you the right answer. Can we schedule a quick call so I can walk you through it properly?",
        "Happy to answer that. The short answer is [address question] — but there's more I'd love to share. Would a quick call work for you?",
    ],
    "already_have": [
        "That's great — it's wonderful that you've planned ahead. I just want to make sure what you have is still current and covers everything you'd want. Would you be open to a quick review?",
        "Glad to hear it! Plans can sometimes have gaps families don't realize until later. Would a quick review be something you'd be open to — no obligation?",
    ],
    "interested": [
        "That's wonderful to hear! Let's find a time to connect so I can walk you through everything. What works best for you — a call or an in-person visit?",
        "Great news! I'd love to help you take the next step. When are you available for a quick conversation?",
    ],
    "general": [
        "Thanks for getting back to me! I'd love to connect and answer any questions. What's the best way to reach you?",
        "Appreciate you responding! I'm here to help however I can. Would it be okay to set up a quick call?",
    ],
}


@router.post("/objection-reply/{reply_id}", response_model=ObjectionReplyResponse)
async def get_objection_reply(
    reply_id: str,
    request: ObjectionReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Load the reply
    reply = db.query(SMSReply).filter(SMSReply.id == reply_id).first()
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")

    # Load lead context
    lead = db.query(Lead).filter(Lead.id == reply.lead_id).first()
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "the lead"
    lead_tier = lead.tier if lead else None

    tone_desc = TONE_INSTRUCTIONS.get(request.tone, TONE_INSTRUCTIONS["standard"])

    prompt = f"""You are an expert funeral home sales advisor assistant. Analyze this reply from a lead and generate the best response.

Lead name: {lead_name}
Lead tier: {lead_tier or 'unknown'}
Their reply: "{reply.body}"

Your job:
1. Classify the objection type (choose exactly one):
   - not_interested: they explicitly don't want to engage
   - need_to_think: need more time or want to discuss with family
   - too_expensive: price or cost concern
   - wrong_time: bad timing, busy, call later
   - callback_request: wants a phone call
   - question: asking a specific question
   - already_have: says they already have a plan
   - interested: positive, wants to proceed
   - general: anything that doesn't fit above

2. Write a response in this tone: {tone_desc}
   - Keep it under 3 sentences
   - Sound like a real person, not a script
   - Use {lead_name.split()[0] if lead_name else 'their first name'} if you address them by name
   - Never be pushy or desperate
   - Always leave the door open

3. Identify 2-3 key talking points the advisor should keep in mind

Respond ONLY with valid JSON in this exact format, no markdown, no explanation:
{{
  "objection_type": "not_interested",
  "objection_reasoning": "One sentence explaining why you classified it this way",
  "confidence": 0.92,
  "suggested_response": "The actual message to send",
  "talking_points": ["Point 1", "Point 2", "Point 3"]
}}"""

    try:
        raw = await call_claude(prompt, max_tokens=600)
        # Strip any markdown fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        result = json.loads(clean.strip())

        return ObjectionReplyResponse(
            objection_type=result.get("objection_type", "general"),
            objection_reasoning=result.get("objection_reasoning", ""),
            confidence=float(result.get("confidence", 0.8)),
            suggested_response=result.get("suggested_response", ""),
            talking_points=result.get("talking_points", []),
        )

    except Exception as e:
        # Fallback to scripted response if AI call fails
        objection_type = _classify_simple(reply.body)
        scripts = OBJECTION_SCRIPTS.get(objection_type, OBJECTION_SCRIPTS["general"])
        return ObjectionReplyResponse(
            objection_type=objection_type,
            objection_reasoning="Classified by keyword matching (AI unavailable).",
            confidence=0.6,
            suggested_response=scripts[0],
            talking_points=["Stay warm and low pressure", "Keep the door open", "Ask for a specific next step"],
        )


def _classify_simple(body: str) -> str:
    """Keyword fallback classifier when AI is unavailable."""
    text = body.lower()
    if any(w in text for w in ["not interested", "don't want", "no thank", "remove me", "unsubscribe", "stop"]):
        return "not_interested"
    if any(w in text for w in ["think about", "discuss", "family", "talk it over", "let me"]):
        return "need_to_think"
    if any(w in text for w in ["expensive", "cost", "price", "afford", "money", "cheap"]):
        return "too_expensive"
    if any(w in text for w in ["busy", "bad time", "call later", "not now", "right now"]):
        return "wrong_time"
    if any(w in text for w in ["call me", "phone", "talk", "speak", "reach me"]):
        return "callback_request"
    if any(w in text for w in ["?", "what", "how", "when", "where", "who", "which"]):
        return "question"
    if any(w in text for w in ["already have", "already set", "covered", "taken care"]):
        return "already_have"
    if any(w in text for w in ["yes", "interested", "tell me more", "love to", "sounds good"]):
        return "interested"
    return "general"
