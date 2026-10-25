"""Seller qualification - what is actually KNOWN about this seller, who said
it, and the next question worth asking.

Ten questions. Each answer comes only from a stored fact:
    seller_stated   the owner's own words (reply reader)
    human_entered   what a rep recorded after a call
A question with no fact is UNKNOWN - nothing is inferred from the property's
distress. Facing foreclosure is NOT "wants to sell"; a public notice is not a
deadline the seller confirmed.

Three fact readers live here (they add facts; they never change a reply's
outcome): an existing offer, specific repairs named, and the person saying
they can sell (owner / authorized).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.evosense import common as C

EXTRA_FACTS = (
    ("existing_offer", re.compile(r"\b((got|have|had|received) (an|another|a) (offer|cash offer)|"
                                  r"(another|other) (investor|buyer|company) (offered|made an offer)|"
                                  r"already (have|got) an offer|under contract with)\b", re.I)),
    ("known_repairs", re.compile(r"\b((new|bad|leaking|old) (roof|hvac|ac|a/c|furnace|water heater|plumbing|"
                                 r"wiring|foundation)|foundation (issue|issues|problem|problems|crack)|"
                                 r"(needs?|need) (a )?(new )?(roof|hvac|ac|a/c|plumbing|wiring|windows|kitchen|"
                                 r"bathroom|flooring|paint)|water damage|mold|termite|fire damage)\b", re.I)),
    ("authority_to_sell", re.compile(r"\b(i('m| am) the (owner|executor|executrix|administrator|trustee)|"
                                     r"(it'?s|it is|the house is|home is) (mine|in my name)|"
                                     r"i own (it|the (house|home|property))|power of attorney|i have poa)\b", re.I)),
)

QUESTIONS = [
    # key, label, fact types that answer it, the question a person / the AI asks
    ("authority", "Owner or authorized to sell?", ("authority_to_sell",),
     "Just to confirm - are you the owner, or someone who can make decisions about the property?"),
    ("open_to_selling", "Open to selling?", ("willing_to_sell", "offer_request", "listed_with_agent"),
     "Would you be open to talking about selling it, now or later?"),
    ("timeline", "Timeline", ("wants_quick_close", "timeline", "nurture_timing"),
     "If you did sell, is there a timeframe that would work best for you?"),
    ("condition", "Condition", ("condition",),
     "How would you describe the condition of the house?"),
    ("occupancy", "Occupied?", ("occupancy", "tenant_issue"),
     "Is anyone living there right now?"),
    ("repairs", "Known repairs", ("known_repairs",),
     "Are there any repairs you know it needs - roof, foundation, HVAC, plumbing?"),
    ("existing_offer", "Existing offer?", ("existing_offer",),
     "Have you had any other offers on it?"),
    ("deadline", "Deadline the seller confirmed", ("auction_status", "wants_quick_close"),
     "Is there a date you are working against?"),
    ("wants_person", "Wants a person?", ("callback_request",),
     "Would it help to talk it through with someone on our team?"),
    ("appointment", "Willing to meet?", ("appointment_request",),
     "Would you be open to us taking a quick look at the property?"),
]


def extra_facts(text: str) -> List[Dict[str, Any]]:
    """Facts the main reader does not extract. The quote is the matched words."""
    out = []
    for ftype, rx in EXTRA_FACTS:
        m = rx.search(text or "")
        if m:
            neg = re.findall(r"[\w']+", (text or "")[:m.start()])[-3:]
            if ftype != "existing_offer" and re.search(r"\b(no|not|never|don'?t|doesn'?t)\b", " ".join(neg), re.I):
                continue
            out.append({"fact_type": ftype, "value": m.group(0).lower(), "quote": m.group(0)})
    return out


def checklist(facts, *, eng_outcome: Optional[str] = None, handoff_codes=()) -> Dict[str, Any]:
    """The ten questions, each answered (with who said it) or unknown."""
    live = [f for f in facts if not getattr(f, "superseded", False)]
    by_type: Dict[str, Any] = {}
    for f in sorted(live, key=lambda f: getattr(f, "created_at", None) or C.now()):
        if f.truth_state in (C.T_SELLER_STATED, C.T_HUMAN):
            by_type[f.fact_type] = f
    rows = []
    for key, label, types, ask in QUESTIONS:
        f = next((by_type[t] for t in types if t in by_type), None)
        answer = None
        if f is not None:
            answer = {"value": f.value, "quote": f.quote, "fact_type": f.fact_type,
                      "who": "seller said" if f.truth_state == C.T_SELLER_STATED else "rep recorded"}
        if key == "wants_person" and answer is None and "HUMAN_REQUESTED" in handoff_codes:
            answer = {"value": "yes", "quote": None, "fact_type": "handoff", "who": "seller asked"}
        if key == "open_to_selling" and answer is None and eng_outcome in (C.O_HARD_NO, C.O_NOT_NOW):
            answer = {"value": "not now" if eng_outcome == C.O_NOT_NOW else "no", "quote": None,
                      "fact_type": "outcome", "who": "seller said"}
        rows.append({"key": key, "label": label, "answer": answer, "ask": ask})
    known = [r for r in rows if r["answer"]]
    nxt = next((r for r in rows if not r["answer"]), None)
    return {"questions": rows, "known": len(known), "of": len(rows),
            "next_question": nxt["ask"] if nxt else None,
            "note": "Answers come only from the seller's own words or a rep's call notes. Distress is never "
                    "read as motivation."}


def for_property(db, prop) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseEngagement, EvoSenseFact, EvoSenseHandoff
    facts = db.query(EvoSenseFact).filter(EvoSenseFact.organization_id == prop.organization_id,
                                          EvoSenseFact.property_id == prop.id).all()
    eng = (db.query(EvoSenseEngagement).filter(EvoSenseEngagement.organization_id == prop.organization_id,
                                               EvoSenseEngagement.property_id == prop.id)
           .order_by(EvoSenseEngagement.updated_at.desc()).first())
    codes = set()
    for (reasons,) in db.query(EvoSenseHandoff.reasons).filter(
            EvoSenseHandoff.organization_id == prop.organization_id, EvoSenseHandoff.property_id == prop.id,
            EvoSenseHandoff.status.in_(("open", "acknowledged"))).all():
        codes |= {r.get("code") for r in (C.jload(reasons, []) or [])}
    return checklist(facts, eng_outcome=getattr(eng, "last_outcome", None), handoff_codes=codes)
