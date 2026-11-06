"""AI receptionist - TEST MODE ONLY. Answers a staff member's typed question.

Nothing here talks to a customer: there is no inbound channel wired to it,
and `profile.ai_mode` has no live value. Each question is checked in order:

  1. emergency stop / AI off             -> refused, logged as "blocked"
  2. prices, valuations, payouts, offers -> fixed hand-off answer, needs_human
  3. the owner's approved answers        -> that answer, verbatim
  4. the platform AI gateway (manual mode, the asking staff member as actor),
     told to use ONLY the approved business facts
  5. if the gateway is off or fails      -> a plain fallback that offers an
                                            appointment or a person

Every answer is post-checked: a dollar figure or a price per weight in an AI
answer replaces it with the hand-off. Every exchange is stored.
"""
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.jewelry_models import JewelryAiExchange, JewelryProfile
from app.models.models import Lead
from app.services.jewelry import common as C

PRICE_RE = re.compile(r"\b(how much|prices?|priced|pricing|worth|value|valuations?|apprais\w*|pay (me|for|you)|"
                      r"payouts?|offers?|quotes?|per gram|per ounce|per oz|spot price|melt value|guarantee\w*)\b|"
                      r"what (will|would|can|do) (i|you) (get|give|pay)", re.I)
MONEY_RE = re.compile(r"(\$\s?\d|\d+\s?(dollars|usd)\b|per (gram|ounce|oz|pennyweight|dwt))", re.I)
HANDOFF = ("Every item is different, so our team evaluates it in person and explains any offer face to face. "
           "I can't give prices, valuations or offers here, but I can set up a free in-store evaluation or have "
           "a team member contact you. Which would you prefer?")


def _facts(org, p: JewelryProfile) -> Dict[str, Any]:
    hours = C.jload(p.business_hours, {}) if p else {}
    return {"name": (p.business_name if p else None) or org.name,
            "address": ", ".join(x for x in ((p.street_address if p else None), (p.city_state_zip if p else None)) if x),
            "phone": p.business_phone if p else None,
            "hours": hours if (p and p.hours_confirmed) else {},
            "services": C.jload(p.services, []) if p else [],
            "appointment_types": C.jload(p.appointment_types, C.DEFAULT_APPOINTMENT_TYPES) if p else C.DEFAULT_APPOINTMENT_TYPES,
            "answers": C.jload(p.approved_answers, []) if p else []}


def _approved_match(q: str, answers: List[Dict[str, str]]) -> Optional[str]:
    ql = set(re.findall(r"[a-z]{3,}", q.lower()))
    best, score = None, 0
    for a in answers:
        if not isinstance(a, dict) or not a.get("q") or not a.get("a"):
            continue
        words = set(re.findall(r"[a-z]{3,}", str(a["q"]).lower()))
        if not words:
            continue
        s = len(ql & words) / len(words)
        if s > score:
            best, score = a["a"], s
    return best if score >= 0.6 else None


def _intent(q: str) -> str:
    ql = q.lower()
    for key, words in (("pricing", ["price", "worth", "value", "pay", "offer", "quote"]),
                       ("hours", ["open", "hours", "close", "sunday", "saturday", "today"]),
                       ("appointment", ["appointment", "schedule", "book", "evaluation", "come in"]),
                       ("coins", ["coin", "silver dollar", "proof", "bullion", "collect"]),
                       ("selling_gold", ["sell", "buy gold", "scrap", "broken", "gold"]),
                       ("jewelry", ["ring", "necklace", "bracelet", "earring", "jewelry", "gift"]),
                       ("location", ["where", "address", "located", "directions"])):
        if any(w in ql for w in words):
            return key
    return "general"


def _fallback(intent: str, f: Dict[str, Any]) -> (str, bool):
    name = f["name"]
    if intent == "location" and f["address"]:
        return "%s is at %s. Would you like to set up a time to stop by?" % (name, f["address"]), False
    if intent == "hours":
        if f["hours"]:
            lines = "; ".join("%s %s" % (k.capitalize(), v) for k, v in f["hours"].items())
            return "Our hours are: %s. Would you like to book a time?" % lines, False
        return ("I want to make sure you get the correct hours, so a team member will confirm them with you. "
                "Would you like me to request an appointment time while I have you?"), True
    if intent in ("appointment", "selling_gold", "coins", "jewelry"):
        types = ", ".join(f["appointment_types"][:4])
        return ("I can help you set up a visit (%s). What day works for you? A team member confirms every "
                "request." % types), False
    return ("Thanks for asking. I'll pass this to a team member so you get the right answer. "
            "Is a call or text best for you?"), True


def _ask_gateway(ctx: C.Ctx, q: str, f: Dict[str, Any], lead: Optional[Lead]) -> Optional[str]:
    from app.services import ai_gateway
    system = ("You are the receptionist assistant for %s, a gold, jewelry and coin business. Answer in 1-3 short, "
              "warm sentences using ONLY these approved facts: %s. Rules: never state prices, valuations, gold or "
              "silver rates, payouts, guarantees or offers - say the team evaluates items in person. Never invent "
              "hours, policies, promotions or services not listed. If you don't know, offer an appointment or a "
              "team member. Always offer to help book a visit when relevant."
              % (f["name"], {k: v for k, v in f.items() if k != "answers" and v}))
    if lead is not None:
        system += " The customer's first name is %s." % ((lead.first_name or "").strip() or "unknown")
    try:
        resp = ai_gateway.chat_completion(
            feature="jewelry.receptionist_test", capability="concierge_chat", mode=ai_gateway.MANUAL,
            actor=ctx.user.id, org_id=ctx.org_id,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": q[:1000]}],
            temperature=0.3, max_tokens=180)
        return (resp.choices[0].message.content or "").strip() or None
    except Exception:  # noqa: BLE001 - refused or failed: the fallback answers instead
        return None


def ask(ctx: C.Ctx, p: JewelryProfile, question: str, session_id: Optional[str],
        lead: Optional[Lead]) -> Dict[str, Any]:
    q = (question or "").strip()
    if not q:
        raise HTTPException(400, "Type a question first")
    sid = (session_id or "")[:40] or uuid.uuid4().hex[:16]
    f = _facts(ctx.org, p)
    intent = _intent(q)
    source, needs_human, answer, answered_by = "rule", False, None, "assistant"
    if p.ai_paused or p.ai_mode == "off":
        answer, answered_by, source = ("The AI receptionist is stopped for this business. A team member will "
                                       "answer instead."), "blocked", "stopped"
        needs_human = True
    elif PRICE_RE.search(q):
        answer, needs_human, intent, source = HANDOFF, True, "pricing", "guardrail"
    else:
        approved = _approved_match(q, f["answers"])
        if approved:
            answer, source = approved, "approved_answer"
        else:
            ai = _ask_gateway(ctx, q, f, lead)
            if ai and not MONEY_RE.search(ai):
                answer, source = ai, "ai"
                needs_human = intent == "general" and "team member" in ai.lower()
            elif ai:
                answer, needs_human, source = HANDOFF, True, "guardrail"
            else:
                answer, needs_human = _fallback(intent, f)
                source = "fallback"
    ex = JewelryAiExchange(organization_id=ctx.org_id, session_id=sid, mode="test", question=q[:2000],
                           answer=answer, answered_by=answered_by, intent=intent, needs_human=needs_human,
                           lead_id=lead.id if lead else None, actor_user_id=ctx.user.id)
    ctx.db.add(ex)
    ctx.db.flush()
    return dict(exchange_json(ex), source=source)


def override(ctx: C.Ctx, ex_id: str, text: str) -> Dict[str, Any]:
    ex = ctx.db.query(JewelryAiExchange).filter(JewelryAiExchange.id == ex_id,
                                                JewelryAiExchange.organization_id == ctx.org_id).first()
    if ex is None:
        raise HTTPException(404, "Conversation turn not found")
    t = (text or "").strip()
    if not t:
        raise HTTPException(400, "Write the reply first")
    new = JewelryAiExchange(organization_id=ctx.org_id, session_id=ex.session_id, mode="test",
                            question=ex.question, answer=t[:2000], answered_by="operator", intent=ex.intent,
                            needs_human=False, lead_id=ex.lead_id, actor_user_id=ctx.user.id)
    ctx.db.add(new)
    ctx.db.flush()
    return exchange_json(new)


def exchange_json(e: JewelryAiExchange) -> Dict[str, Any]:
    return {"id": e.id, "session_id": e.session_id, "mode": e.mode, "question": e.question, "answer": e.answer,
            "answered_by": e.answered_by, "intent": e.intent, "needs_human": bool(e.needs_human),
            "lead_id": e.lead_id, "at": C.iso(e.created_at)}


def history(ctx: C.Ctx, limit: int = 100) -> List[Dict[str, Any]]:
    rows = (ctx.db.query(JewelryAiExchange).filter(JewelryAiExchange.organization_id == ctx.org_id)
            .order_by(JewelryAiExchange.created_at.desc()).limit(limit).all())
    return [exchange_json(r) for r in rows]
