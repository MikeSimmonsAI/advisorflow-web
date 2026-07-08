"""
AI Objection Library — POST /ai/objection-reply/{reply_id}

Classifies the objection type in a lead's reply and returns a scripted,
tone-adjusted response the advisor can review and send.

Uses OpenAI (same client as ai_analysis_service) with keyword fallback
if the API call fails.
"""

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.deps import get_db, get_current_user
from app.models.models import User

router = APIRouter(prefix="/ai", tags=["ai"])

# ── Lazy OpenAI client (same pattern as ai_analysis_service) ─────────────
_client = None

def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


# ── Schemas ───────────────────────────────────────────────────────────────

class ObjectionReplyRequest(BaseModel):
    tone: Optional[str] = "standard"  # soft | standard | direct | urgent


class ObjectionReplyResponse(BaseModel):
    objection_type: str
    objection_reasoning: str
    confidence: float
    suggested_response: str
    talking_points: list


# ── Tone descriptions ─────────────────────────────────────────────────────

TONE_INSTRUCTIONS = {
    "soft":     "warm, empathetic, low pressure — give them space while keeping the door open",
    "standard": "professional and friendly — direct but not pushy",
    "direct":   "clear and confident — address the objection head-on without being aggressive",
    "urgent":   "create gentle urgency — acknowledge the objection but emphasize why acting sooner is better",
}

# ── Scripted fallbacks ────────────────────────────────────────────────────

OBJECTION_SCRIPTS = {
    "not_interested":   "I completely understand — I never want to pressure anyone. If you ever have questions or want to explore options at your own pace, I'm always here.",
    "need_to_think":    "Of course — this is an important decision and you should take the time you need. Can I follow up in a few days to answer any questions that come up?",
    "too_expensive":    "That's a fair concern. We have options at several price points, and locking in today's pricing protects you from future increases. Could we find 10 minutes to look at what fits your budget?",
    "wrong_time":       "No problem at all — I'll give you some space. When's a good time to reconnect?",
    "callback_request": "Happy to call you! What's the best time to reach you, and is this the best number?",
    "question":         "Great question — I want to make sure I give you the right answer. Can we schedule a quick call so I can walk you through it properly?",
    "already_have":     "That's great — it's wonderful that you've planned ahead. Would you be open to a quick review to make sure everything is still current?",
    "interested":       "That's wonderful to hear! Let's find a time to connect so I can walk you through everything. What works best for you?",
    "general":          "Thanks for getting back to me! I'd love to connect and answer any questions. What's the best way to reach you?",
}


# ── Simple keyword classifier (fallback) ─────────────────────────────────

def _classify_simple(body: str) -> str:
    text = body.lower()
    if any(w in text for w in ["not interested", "don't want", "no thank", "remove me", "unsubscribe", "stop"]):
        return "not_interested"
    if any(w in text for w in ["think about", "discuss", "family", "talk it over", "let me"]):
        return "need_to_think"
    if any(w in text for w in ["expensive", "cost", "price", "afford", "money"]):
        return "too_expensive"
    if any(w in text for w in ["busy", "bad time", "call later", "not now"]):
        return "wrong_time"
    if any(w in text for w in ["call me", "phone", "talk", "speak", "reach me"]):
        return "callback_request"
    if "?" in text or any(w in text for w in ["what", "how", "when", "where", "who"]):
        return "question"
    if any(w in text for w in ["already have", "already set", "covered", "taken care"]):
        return "already_have"
    if any(w in text for w in ["yes", "interested", "tell me more", "sounds good"]):
        return "interested"
    return "general"


# ── Route ─────────────────────────────────────────────────────────────────

@router.post("/objection-reply/{reply_id}", response_model=ObjectionReplyResponse)
def get_objection_reply(
    reply_id: str,
    request: ObjectionReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.models import SMSReply, Lead

    reply = db.query(SMSReply).filter(SMSReply.id == reply_id).first()
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")

    lead = db.query(Lead).filter(Lead.id == reply.lead_id).first()
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "the lead"
    first_name = lead_name.split()[0] if lead_name else "there"
    lead_tier = lead.tier if lead and lead.tier else "unknown"

    tone_desc = TONE_INSTRUCTIONS.get(request.tone, TONE_INSTRUCTIONS["standard"])

    prompt = f"""You are an expert sales advisor assistant for a service business. Analyze this reply from a lead and generate the best response.

Lead name: {lead_name}
Lead tier: {lead_tier}
Their reply: "{reply.body}"

Classify the objection type (choose exactly one):
- not_interested: they explicitly don't want to engage
- need_to_think: need more time or want to discuss with family
- too_expensive: price or cost concern
- wrong_time: bad timing, busy, call later
- callback_request: wants a phone call
- question: asking a specific question
- already_have: says they already have a plan
- interested: positive, wants to proceed
- general: anything else

Write a response in this tone: {tone_desc}
- Keep it under 3 sentences
- Sound like a real person, not a script
- Address {first_name} by name if appropriate
- Never be pushy or desperate
- Always leave the door open

Respond ONLY with valid JSON, no markdown:
{{
  "objection_type": "not_interested",
  "objection_reasoning": "One sentence explaining why you classified it this way",
  "confidence": 0.92,
  "suggested_response": "The actual message to send",
  "talking_points": ["Point 1", "Point 2", "Point 3"]
}}"""

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        return ObjectionReplyResponse(
            objection_type=result.get("objection_type", "general"),
            objection_reasoning=result.get("objection_reasoning", ""),
            confidence=float(result.get("confidence", 0.8)),
            suggested_response=result.get("suggested_response", ""),
            talking_points=result.get("talking_points", []),
        )

    except Exception as e:
        # Fallback to keyword classifier + scripted response
        objection_type = _classify_simple(reply.body)
        return ObjectionReplyResponse(
            objection_type=objection_type,
            objection_reasoning="Classified by keyword matching (AI unavailable).",
            confidence=0.6,
            suggested_response=OBJECTION_SCRIPTS.get(objection_type, OBJECTION_SCRIPTS["general"]),
            talking_points=["Stay warm and low pressure", "Keep the door open", "Ask for a specific next step"],
        )
