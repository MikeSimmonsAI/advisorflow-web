"""Run the 100 evaluation conversations through the REAL setter.

Each scenario uses its own SYNTHETIC contact (is_test, no phone, no email -
it can never be messaged; bookings are simulated against the live calendar)
and runs as the requesting owner's MANUAL AI action. Every turn goes through
the real reply path (classification, rules, memory, validation, slots) and is
scored by the scenario's expectations plus universal rules.
"""
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.programs.setter_eval_scenarios import REGRESSIONS, SCENARIOS

_FILLED_TIME = re.compile(r"\b(am|pm)\b", re.I)


def universal_checks(turn: Dict, contact: Optional[str] = None) -> List[str]:
    from app.services.programs import ai_setter
    bad = []
    text = turn.get("reply") or ""
    # VOICE (Oct 16): every reply is signed by the advisor, so it must speak as
    # the advisor. The old suite never checked this, which is how 96/100 passed
    # while live texts said "Kerry Allan would be glad to help" - signed Kerry Allan.
    if contact and ai_setter.third_person(text, contact):
        bad.append("speaks about %s in the third person" % contact)
    if text.count("?") > 1:
        bad.append("more than one question")
    for pat, why in ai_setter._BANNED:
        if re.search(pat, text.lower()):
            bad.append(why)
    if ai_setter._CLAIM_BOOKED.search(text) and not turn.get("booked"):
        bad.append("claims a booking that did not happen")
    if ai_setter._CLAIM_MAILED.search(text):
        bad.append("claims something was mailed")
    if re.search(r"https?://", text) and turn.get("action") != "send_guide":
        bad.append("link outside send_guide")
    if _FILLED_TIME.search(text) and turn.get("action") not in ("offer_times", "book", "answer", "callback",
                                                                 "follow_up_later") and not turn.get("offered"):
        bad.append("mentions a time without offering real ones")
    return bad


def score_turn(exp: Dict, turn: Dict, contact: Optional[str] = None) -> List[str]:
    bad = universal_checks(turn, contact)
    acts = exp.get("acts")
    if acts and turn.get("action") not in acts.split(","):
        bad.append("action %s not in %s" % (turn.get("action"), acts))
    for pat in exp.get("must") or []:
        if not re.search(pat, turn.get("reply") or "", re.I):
            bad.append("missing /%s/" % pat)
    for pat in exp.get("must_not") or []:
        if re.search(pat, turn.get("reply") or "", re.I):
            bad.append("has forbidden /%s/" % pat)
    if exp.get("booked") is True and not turn.get("booked"):
        bad.append("should have booked")
    if exp.get("booked") is False and turn.get("booked"):
        bad.append("booked when it should not")
    if exp.get("states") and turn.get("state") not in exp["states"]:
        bad.append("state %s not in %s" % (turn.get("state"), exp["states"]))
    return bad


def _lead(db, prog, user, sid: str, first: str, run_tag: str, location_id: Optional[str]):
    from app.models.models import Lead
    from app.models.program_models import ProgramSourceRecord
    key = "SIM-EVAL-%s-%s" % (sid, run_tag)
    lead = Lead(organization_id=prog.organization_id, first_name=first, last_name=key, phone=None, email=None,
                status="sent", is_test=True, assigned_to_id=getattr(prog, "booking_user_id", None) or user.id)
    db.add(lead)
    db.flush()
    if location_id:
        db.add(ProgramSourceRecord(organization_id=prog.organization_id, lead_id=lead.id, source_lead_id=key,
                                   location_id=location_id, location_status="mapped", raw_json="{}"))
    db.commit()
    return lead


def _last_reason(db, conv) -> Optional[str]:
    """The setter's own recorded reason for its latest step (system or AI turn)."""
    if conv is None:
        return None
    from app.models.program_models import ProgramConversationTurn
    t = (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == conv.id)
         .order_by(ProgramConversationTurn.created_at.desc()).first())
    return (t.reason or "")[:300] if t is not None else None


def run_scenario(db: Session, prog, user, scenario: Dict, *, run_tag: str,
                 location_id: Optional[str] = None, capability: Optional[str] = None) -> Dict:
    from app.services.programs import ai_setter
    from app.services.programs import responses as R
    lead = _lead(db, prog, user, scenario["id"], scenario["first_name"], run_tag, location_id)
    if scenario.get("opening"):
        # The message the family is answering (#234: a "No" answers OUR last
        # question). A synthetic lead's simulated line stands in for a sent one.
        from app.models.models import Message
        db.add(Message(lead_id=lead.id, sender_id=user.id, body=scenario["opening"], delivery_status="simulated",
                       sent_at=datetime.utcnow() - timedelta(minutes=30)))
        db.commit()
    turns, failures = [], []
    token = ai_setter._CAPABILITY.set(capability or "sci_appointment_setter")
    try:
        for i, exp in enumerate(scenario["turns"]):
            t0 = datetime.utcnow()
            resp = R.on_inbound(db, lead, exp["text"], "sms", staff_alerts=False, run_setter=False)
            res = ai_setter.on_family_reply(db, prog, lead, resp, exp["text"], force_mode="auto",
                                            actor=user.id) if resp is not None else None
            conv = ai_setter.conversation_for(db, lead, create=False)
            res = res or {}
            turn = {"n": i + 1, "family": exp["text"], "class": resp.response_class if resp else None,
                    "action": res.get("action"), "reply": res.get("text") or "",
                    "state": conv.state if conv else None, "offered": res.get("offered"),
                    "booked": bool(res.get("booked_time")), "corrected": res.get("corrected"),
                    "confidence": res.get("confidence"), "objective": res.get("objective"),
                    "held": res.get("held"), "ms": int((datetime.utcnow() - t0).total_seconds() * 1000),
                    # WHY a turn went to a person (AI error / limit / held) - no secrets in these strings.
                    "error": res.get("error"), "why": _last_reason(db, conv)}
            if not turn["reply"] and conv is not None:
                last = ai_setter._last_ai_text(conv)
                if res.get("action") == "book" and last:
                    turn["reply"] = last
            bad = score_turn(exp, turn, getattr(prog, "primary_contact_name", None))
            turn["problems"] = bad
            if bad:
                failures.append({"turn": i + 1, "problems": bad})
            turns.append(turn)
    finally:
        ai_setter._CAPABILITY.reset(token)
    return {"id": scenario["id"], "category": scenario["category"], "passed": not failures,
            "failures": failures, "turns": turns,
            "memory": ai_setter.memory_of(ai_setter.conversation_for(db, lead, create=False) or object())}


def scenario(sid: str) -> Optional[Dict]:
    return next((s for s in list(SCENARIOS) + list(REGRESSIONS) if s["id"] == sid), None)
