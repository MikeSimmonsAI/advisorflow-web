"""Seller conversation intelligence.

A reply is read into:
    * ONE outcome from a fixed vocabulary of 17 (common.OUTCOMES)
    * FACTS, each with the seller's own words (quote), the message it came
      from, who extracted it, and a truth state of SELLER STATED — never
      blended with provider data or system estimates
    * a Seller Intent score (scoring.seller_intent, deterministic)

WHO DECIDES. The outcome and the facts come from the deterministic reader
below. The platform's AI reader (`wholesale_ai.extract_from_message`, which
runs through ai_gateway and never sees a STOP) is consulted only when the
rules cannot place a message; its reading is stored and shown, and it can
never downgrade an opt-out. No model decides DNC, compliance, a score, or
money.

WHAT EACH OUTCOME DOES (automatic, and nothing beyond it):
    DO_NOT_CONTACT / opt-out words  Lead -> DNC, suppression entry (REPLY_STOP),
                                    contact point opted_out, cadence stopped
    WRONG_PERSON                    contact point wrong_party (Contact Confidence
                                    drops), engagement stopped; with STOP also
                                    suppressed. No second message, ever.
    NOT_NOW / CALL_LATER / FAMILY / MAYBE
                                    nurture until a date, context retained
    ALREADY_SOLD / NOT_OWNER / LISTED / HARD_NO
                                    stopped, closed out
    INTERESTED / WANTS_OFFER / APPOINTMENT / ESTATE / TENANT / PRICE_TOO_HIGH
                                    responded; NEEDS YOU when judgment matters
    UNKNOWN                         NEEDS YOU (a person reads it)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseEngagement, EvoSenseFact,
                                        EvoSenseMessage, EvoSenseProperty)
from app.models.models import Lead, Reply
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import handoff as HO
from app.services.evosense import scoring as SC
from app.services.evosense import strategy as ST

# ── the deterministic reader ────────────────────────────────────────────────

_OPT_OUT = re.compile(r"\b(stop|stopall|unsubscribe|opt\s*out|remove me|take me off|"
                      r"do not (text|contact|call|message)|don'?t (text|contact|call|message) me|"
                      r"quit (texting|messaging|calling)|lose (this|my) number)\b", re.I)
_WRONG_PERSON = re.compile(r"\b(wrong (number|person)|you have the wrong|not me|no one by that name|"
                           r"nobody by that name|don'?t know (who|what) (that|you)|who is this)\b", re.I)
_NOT_OWNER = re.compile(r"\b(i don'?t own|not the owner|i'?m (just )?(a|the) tenant|i (just )?rent (it|here)|"
                        r"never owned)\b", re.I)
_SOLD = re.compile(r"\b(already sold|it sold|we sold|sold it|sold last|under contract|no longer own)\b", re.I)
_LISTED = re.compile(r"\b(listed (it )?with|my (realtor|agent)|with an agent|on the market|listing agent)\b", re.I)
_NOT_NOW = re.compile(r"\b(not (interested )?(right now|at (the|this) (moment|time)|yet)|maybe (after|later|next)|"
                      r"after the holidays|after (christmas|new year)|next year|in the spring|"
                      r"not this year|some ?day|down the road)\b", re.I)
_CALL_LATER = re.compile(r"\b(call me (later|tomorrow|next week|back|in)|busy right now|text me later|"
                         r"reach out (later|next week|in))\b", re.I)
_HARD_NO = re.compile(r"\b(not interested|no thanks?|no thank you|not selling|never (sell|selling)|"
                      r"not for sale|leave me alone)\b", re.I)
_FAMILY = re.compile(r"\b(talk (to|with) my (wife|husband|family|kids|siblings|brother|sister|mom|dad)|"
                     r"(family|siblings|we) (are |is )?(still )?deciding|need to (discuss|talk) it over)\b", re.I)
_TENANT = re.compile(r"\b(tenant (won'?t|isn'?t|is not|hasn'?t) (pay|paying|leav)|evict|bad tenant|"
                     r"tenant (problem|issue)|squatter)\b", re.I)
_PRICE_HIGH = re.compile(r"\b(not (for|at) less than|firm (on|at)|won'?t take less|bottom line is)\b", re.I)
_APPOINTMENT = re.compile(r"\b(come (by|see|look)|walk ?through|meet (you|me|there)|appointment|"
                          r"see (it|the house|inside)|show you)\b", re.I)
_OFFER = re.compile(r"\b(make (me )?an offer|what would you (offer|pay|give)|send (me )?(an |your )?offer|"
                    r"what'?s your offer|how much would you)\b", re.I)
_CALLBACK = re.compile(r"\b(call me|give me a call|can you call|phone me)\b", re.I)
_WILLING = re.compile(r"\b((i'?d|i would|i will|i'?ll|we'?d|we would|would|might)\s+(probably\s+|likely\s+|definitely\s+|"
                      r"consider\s+)?sell(ing)?|willing to sell|want(s)? to sell|looking to sell|ready to sell|"
                      r"open to selling|thinking (about|of) selling)\b", re.I)
_INTEREST = re.compile(r"\b(yes|yeah|yep|interested|tell me more|sure)\b", re.I)
_MAYBE = re.compile(r"\b(maybe|possibly|depends|might consider|not sure)\b", re.I)
_QUICK = re.compile(r"\b(close (quickly|fast|soon|quick)|quick (close|sale)|asap|as soon as possible|"
                    r"fast sale|sell (it )?fast|right away)\b", re.I)
_ESTATE = re.compile(r"\b(inherited|inherit|estate|probate|passed away|late (mother|father|mom|dad|husband|wife))\b", re.I)
_PRICE_CTX = re.compile(r"\b(?:around|about|roughly|for|at|near|close to|asking|get|want)\s+\$?\s?"
                        r"([0-9]{2,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(k|thousand|grand)?\b", re.I)
_PRICE_ANY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|thousand)?|\b([0-9]{2,3})\s?(k|thousand|grand)\b", re.I)

_OCCUPANCY = ((re.compile(r"\b(vacant|empty|nobody lives|no one lives|sitting empty)\b", re.I), "vacant"),
              (re.compile(r"\b(tenant|renter|rented|leased)\b", re.I), "tenant"),
              (re.compile(r"\b(i live there|we live there|my home|our home)\b", re.I), "owner_occupied"))
_CONDITION = ((re.compile(r"\b(gutted|condemned|fire damage|falling apart|tear ?down|needs everything)\b", re.I), "distressed"),
              (re.compile(r"\b(bad shape|rough shape|needs a lot|needs work|poor condition|fixer)\b", re.I), "poor"),
              (re.compile(r"\b(needs some work|dated|could use|okay shape|ok shape)\b", re.I), "fair"),
              (re.compile(r"\b(good (shape|condition)|well maintained|move in ready)\b", re.I), "good"))


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _quote(text: str, match_span: Tuple[int, int]) -> str:
    start = 0
    for s in _sentences(text):
        i = text.find(s, start)
        if i <= match_span[0] < i + len(s) + 1:
            return s[:240]
        start = i + len(s)
    return text[max(0, match_span[0] - 60): match_span[1] + 60].strip()[:240]


def parse_price(text: str) -> Optional[Tuple[int, str, Tuple[int, int]]]:
    """(dollars, note, span). Shorthand like "around 150" is read as $150,000
    and says so — the note travels with the fact."""
    m = _PRICE_ANY.search(text)
    if m:
        if m.group(3):
            return int(m.group(3)) * 1000, "“%s” read as $%s" % (m.group(0).strip(), format(int(m.group(3)) * 1000, ",")), m.span()
        v = float(m.group(1).replace(",", ""))
        if (m.group(2) or "").lower() in ("k", "thousand"):
            v *= 1000
        if v < 1000:
            v *= 1000
        if v >= 10000:
            return int(v), "stated as “%s”" % m.group(0).strip(), m.span()
    m = _PRICE_CTX.search(text)
    if m:
        v = float(m.group(1).replace(",", ""))
        if (m.group(2) or "") or v < 1000:
            v = v * 1000 if v < 1000 else v
            return int(v), "seller said “%s” — read as $%s (shorthand for thousands)" % (
                m.group(0).strip(), format(int(v), ",")), m.span()
        if v >= 10000:
            return int(v), "stated as “%s”" % m.group(0).strip(), m.span()
    return None


def nurture_date(text: str, when: datetime, default_days: int) -> Tuple[datetime, str]:
    t = (text or "").lower()
    y = when.year
    if re.search(r"after (the )?holidays|after christmas|after new year", t):
        d = datetime(y, 1, 6) if when < datetime(y, 1, 6) else datetime(y + 1, 1, 6)
        return d, "after the holidays"
    if "next year" in t:
        return datetime(y + 1, 1, 15), "next year"
    for word, month in (("spring", 3), ("summer", 6), ("fall", 9), ("autumn", 9)):
        if word in t:
            d = datetime(y, month, 15)
            return (d if d > when else datetime(y + 1, month, 15)), "in the %s" % word
    m = re.search(r"in (a|one|two|three|four|six|\d+) (week|month)s?", t)
    if m:
        n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6}.get(m.group(1))
        n = n or int(m.group(1))
        return when + (timedelta(weeks=n) if m.group(2) == "week" else timedelta(days=30 * n)), m.group(0)
    if "next month" in t:
        return when + timedelta(days=30), "next month"
    if "next week" in t:
        return when + timedelta(days=7), "next week"
    if "tomorrow" in t:
        return when + timedelta(days=1), "tomorrow"
    return when + timedelta(days=default_days), "no date given — strategy default %s days" % default_days


def classify(text: str) -> Dict[str, Any]:
    """Deterministic reading. Reports only what it matched."""
    t = text or ""
    facts: List[Dict[str, Any]] = []

    def fact(ftype, value, m, note=None):
        facts.append({"fact_type": ftype, "value": value, "quote": _quote(t, m.span()),
                      "note": note})

    # An opt-out is recognised by EvoSense's reader OR the platform's own
    # (wholesale_ai._OPT_OUT, the stricter of the two). Erring toward honouring
    # a STOP is the only acceptable direction to be wrong in.
    from app.services.wholesale_ai import _OPT_OUT as PLATFORM_OPT_OUT
    opt_out = bool(_OPT_OUT.search(t) or PLATFORM_OPT_OUT.search(t))
    if _WRONG_PERSON.search(t):
        outcome = C.O_WRONG_PERSON
    elif opt_out:
        outcome = C.O_DNC
    elif _NOT_OWNER.search(t):
        outcome = C.O_NOT_OWNER
    elif _SOLD.search(t):
        outcome = C.O_ALREADY_SOLD
    elif _LISTED.search(t):
        outcome = C.O_LISTED
    elif _NOT_NOW.search(t):
        outcome = C.O_NOT_NOW
    elif _CALL_LATER.search(t):
        outcome = C.O_CALL_LATER
    elif _HARD_NO.search(t):
        outcome = C.O_HARD_NO
    elif _TENANT.search(t):
        outcome = C.O_TENANT
    elif _FAMILY.search(t):
        outcome = C.O_FAMILY
    elif _APPOINTMENT.search(t):
        outcome = C.O_APPOINTMENT
    elif _OFFER.search(t):
        outcome = C.O_WANTS_OFFER
    elif _PRICE_HIGH.search(t):
        outcome = C.O_PRICE_TOO_HIGH
    elif _WILLING.search(t) or _INTEREST.search(t):
        outcome = C.O_INTERESTED
    elif _ESTATE.search(t):
        outcome = C.O_ESTATE
    elif _MAYBE.search(t):
        outcome = C.O_MAYBE
    else:
        outcome = C.O_UNKNOWN

    closing = outcome in (C.O_DNC, C.O_WRONG_PERSON, C.O_NOT_OWNER, C.O_ALREADY_SOLD)
    if not closing:
        for rx, ftype, value in ((_WILLING, "willing_to_sell", "yes"),
                                 (_QUICK, "wants_quick_close", "yes"),
                                 (_APPOINTMENT, "appointment_request", "yes"),
                                 (_OFFER, "offer_request", "yes"),
                                 (_CALLBACK, "callback_request", "yes"),
                                 (_FAMILY, "decision_makers", "family deciding"),
                                 (_LISTED, "listed_with_agent", "yes"),
                                 (_TENANT, "tenant_issue", "yes")):
            m = rx.search(t)
            if m and not (ftype == "callback_request" and outcome == C.O_CALL_LATER):
                fact(ftype, value, m)
        m = _ESTATE.search(t)
        if m:
            when = re.search(r"\b(last|this) (year|month)\b|\b(19|20)\d{2}\b", t, re.I)
            fact("estate_context", "inherited" + (" (%s)" % when.group(0) if when else ""), m)
        p = parse_price(t)
        if p:
            facts.append({"fact_type": "asking_price", "value": str(p[0]),
                          "quote": _quote(t, p[2]), "note": p[1]})
        for rx, value in _OCCUPANCY:
            m = rx.search(t)
            if m:
                fact("occupancy", value, m)
                break
        for rx, value in _CONDITION:
            m = rx.search(t)
            if m:
                fact("condition", value, m)
                break
        if outcome in (C.O_NOT_NOW, C.O_CALL_LATER, C.O_MAYBE, C.O_FAMILY):
            m = _NOT_NOW.search(t) or _CALL_LATER.search(t) or _MAYBE.search(t) or _FAMILY.search(t)
            if m:
                fact("nurture_timing", m.group(0), m)
    return {"outcome": outcome, "opt_out": opt_out, "facts": facts, "reader": "rules",
            "confident": outcome != C.O_UNKNOWN}


_AI_TO_OUTCOME = {"interested": C.O_INTERESTED, "maybe_later": C.O_NOT_NOW,
                  "not_interested": C.O_HARD_NO, "wrong_person": C.O_WRONG_PERSON,
                  "already_sold": C.O_ALREADY_SOLD, "do_not_contact": C.O_DNC,
                  "qualified_opportunity": C.O_INTERESTED}


# ── applying a reply ────────────────────────────────────────────────────────

def _same_value_points(db, org_id, cp, owner_only=True):
    q = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.organization_id == org_id,
                                              EvoSenseContactPoint.kind == cp.kind,
                                              EvoSenseContactPoint.value == cp.value)
    if owner_only:
        q = q.filter(EvoSenseContactPoint.owner_id == cp.owner_id)
    return q.all()


def _opt_out(db, org_id, cp, lead, reason):
    from app.models.models import SuppressionSource
    from app.services import cadence_service, compliance_service
    if lead is not None:
        lead.status = "dnc"
    if cp is not None and cp.kind == "phone":
        try:
            compliance_service.add_suppression_entry(db, org_id, cp.value, reason,
                                                     source=SuppressionSource.REPLY_STOP)
        except ValueError:
            C.log.warning("evosense: opt-out number not usable for suppression: %s", cp.id)
    if cp is not None:
        for other in _same_value_points(db, org_id, cp, owner_only=False):
            if other.status in ("active", "wrong_party", "suppressed"):
                other.status = "opted_out" if other.status != "wrong_party" else other.status
                other.status_reason = reason
    if lead is not None:
        cadence_service.stop_cadence_for_lead(db, lead.id, "opted_out")


def record_inbound(db, org_id: str, engagement: EvoSenseEngagement, text: str, *,
                   delivery: str = "received", reply=None, user=None):
    """STEP 1 — keep the seller's words. Nothing is interpreted here.

    `reply` is the platform `Reply` the inbound SMS webhook already persisted
    (Phase 7.1): the EvoSense message points at it, and a second delivery of
    the same provider message finds it and creates nothing. For a reply typed
    in by a person (manual_entry) the platform copy is created here instead.
    Returns (message, created)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty message")
    if reply is not None:
        existing = (db.query(EvoSenseMessage)
                    .filter(EvoSenseMessage.organization_id == org_id,
                            EvoSenseMessage.direction == "inbound",
                            EvoSenseMessage.platform_ref == reply.id).first())
        if existing is not None:
            return existing, False
    lead = db.query(Lead).filter(Lead.id == engagement.lead_id,
                                 Lead.organization_id == org_id).first() if engagement.lead_id else None
    msg = EvoSenseMessage(organization_id=org_id, engagement_id=engagement.id,
                          property_id=engagement.property_id, direction="inbound",
                          channel=engagement.channel or "sms", body=text, delivery=delivery,
                          platform_ref=reply.id if reply is not None else None)
    db.add(msg)
    db.flush()
    if reply is None and lead is not None:
        # The words also live where the platform keeps replies, against the
        # seller Lead — one conversation history, not two.
        r = Reply(lead_id=lead.id, body=text, source=engagement.channel or "sms")
        db.add(r)
        db.flush()
        msg.platform_ref = r.id
    C.log_event(db, org_id, "reply.received", property_id=engagement.property_id,
                is_test=engagement.is_test, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION,
                summary="Seller reply saved (%s)" % ("SMS" if delivery == "received" else
                                                    delivery.replace("_", " ")),
                details={"message": msg.id, "platform_reply": msg.platform_ref})
    return msg, True


def receive(db, org_id: str, engagement: EvoSenseEngagement, text: str, *, user=None,
            delivery: str = "received", reply=None) -> Dict[str, Any]:
    """Keep the words, then read them. (Both steps; see record_inbound / evaluate.)"""
    msg, created = record_inbound(db, org_id, engagement, text, delivery=delivery, reply=reply,
                                  user=user)
    db.commit()                      # the message survives whatever the reading does
    if not created and msg.outcome:
        return {"message_id": msg.id, "outcome": msg.outcome, "duplicate": True, "actions": [],
                "facts": [], "status": None}
    return evaluate(db, org_id, msg, user=user)


AI_REVIEW_PENDING = "AI_REVIEW_PENDING"


def _pending(db, org_id, msg, engagement, prop, reason_code, sentence, reading, user=None):
    """The message is kept, nothing is guessed, a person can read it, and it
    will be read again (scheduler pass or the Retry button)."""
    from app.services.evosense import evaluate as EV
    msg.outcome = None
    msg.reading = C.jdump({"pending": reason_code, "why": sentence,
                           "rules": {"outcome": reading["outcome"], "confident": reading["confident"]}})
    if reason_code == "ai_failed":
        HO.open_handoff(db, prop, [AI_REVIEW_PENDING], priority=60,
                        next_action="Read the seller's message")
    C.log_event(db, org_id, "reply.ai_failed" if reason_code == "ai_failed" else "reply.held",
                property_id=prop.id, is_test=prop.is_test, user=user,
                actor_type=C.ACTOR_AUTOMATION, summary=sentence, details={"message": msg.id})
    EV.refresh_status(db, prop)
    db.commit()
    return {"message_id": msg.id, "outcome": None, "pending": reason_code, "why": sentence,
            "facts": [], "actions": [], "status": prop.status}


def evaluate(db, org_id: str, msg: EvoSenseMessage, *, user=None) -> Dict[str, Any]:
    """STEP 2 — read a saved seller message: hard stops first, then outcome,
    facts with provenance, Seller Intent, and the next action. Runs at most
    once per message (an evaluated message has an outcome)."""
    from app.services.evosense import evaluate as EV
    if msg.outcome:
        return {"message_id": msg.id, "outcome": msg.outcome, "duplicate": True, "actions": [],
                "facts": [], "status": None}
    engagement = db.query(EvoSenseEngagement).filter(EvoSenseEngagement.id == msg.engagement_id,
                                                     EvoSenseEngagement.organization_id == org_id).first()
    text = msg.body
    prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == engagement.property_id,
                                             EvoSenseProperty.organization_id == org_id).first()
    strategy = EV.strategy_for(db, prop)
    cp = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.id == engagement.contact_point_id,
                                               EvoSenseContactPoint.organization_id == org_id).first()
    lead = db.query(Lead).filter(Lead.id == engagement.lead_id,
                                 Lead.organization_id == org_id).first() if engagement.lead_id else None

    # HARD STOPS FIRST — deterministic, never sent to a model, never paused.
    reading = classify(text)
    outcome = reading["outcome"]
    hard = reading["opt_out"] or outcome in (C.O_DNC, C.O_WRONG_PERSON)
    ctl = C.controls(db, org_id)
    if ctl.paused_all and not hard:
        return _pending(db, org_id, msg, engagement, prop, "held",
                        "EvoSense is paused. The seller's message is saved and will be read when "
                        "EvoSense resumes; opt-outs are still honoured immediately.", reading, user)
    if engagement.status in ("stopped", "promoted") and not hard:
        msg.outcome = outcome
        msg.reading = C.jdump({"rules": reading, "note": "conversation already %s" % engagement.status})
        C.log_event(db, org_id, "reply.after_close", property_id=prop.id, is_test=prop.is_test,
                    actor_type=C.ACTOR_AUTOMATION,
                    summary="Reply on a %s conversation saved; nothing automatic follows"
                    % engagement.status)
        EV.refresh_status(db, prop)
        db.commit()
        return {"message_id": msg.id, "outcome": outcome, "facts": [], "actions": [],
                "status": prop.status}
    ai = None
    if not hard and not reading["confident"] and not ctl.paused_ai_replies and not prop.is_test:
        from app.services import wholesale_ai
        try:
            ai = wholesale_ai.extract_from_message(text, org_id=org_id, actor="evosense",
                                                   mode="background")
        except Exception as exc:  # noqa: BLE001
            ai = {"source": "rules", "ai_unavailable_reason": "%s: %s" % (type(exc).__name__, exc)}
        if ai.get("ai_unavailable_reason"):
            return _pending(db, org_id, msg, engagement, prop, "ai_failed",
                            "AI review pending — the reply could not be read automatically "
                            "(%s). The message is saved; nothing was guessed."
                            % str(ai["ai_unavailable_reason"])[:120], reading, user)
        mapped = _AI_TO_OUTCOME.get(ai.get("intent"))
        if mapped and (ai.get("confidence") or 0) >= 50 and mapped != C.O_DNC:
            outcome = mapped
            reading["reader"] = "ai (rules could not place it)"
    if reading["opt_out"] and outcome not in (C.O_DNC, C.O_WRONG_PERSON):
        outcome = C.O_DNC               # a STOP anywhere wins, whatever else was said
    msg.outcome = outcome
    msg.reading = C.jdump({"rules": reading, "ai": ai and {k: ai.get(k) for k in
                                                           ("intent", "summary", "confidence", "source")}})

    # facts, with provenance. A newer statement of the same kind supersedes.
    made = []
    for f in reading["facts"]:
        for old in (db.query(EvoSenseFact)
                    .filter(EvoSenseFact.organization_id == org_id, EvoSenseFact.property_id == prop.id,
                            EvoSenseFact.fact_type == f["fact_type"],
                            EvoSenseFact.superseded.is_(False)).all()):
            old.superseded = True
        row = EvoSenseFact(organization_id=org_id, property_id=prop.id, engagement_id=engagement.id,
                           message_id=msg.id, fact_type=f["fact_type"], value=f["value"],
                           truth_state=C.T_SELLER_STATED,
                           quote=(f["quote"] + (" — " + f["note"] if f.get("note") else ""))[:250],
                           extracted_by="rules", confidence=80)
        db.add(row)
        made.append(row)
    db.flush()

    engagement.last_outcome = outcome
    actions: List[str] = []
    if cp is not None:
        cp.last_response_at = C.now()
    if outcome == C.O_WRONG_PERSON:
        for p in _same_value_points(db, org_id, cp) if cp else []:
            p.status = "wrong_party"
            p.status_reason = "Replied: wrong person (%s)" % C.now().strftime("%b %d")
        actions.append("Contact marked WRONG PARTY — no strategy will use it for this owner again")
        if reading["opt_out"]:
            _opt_out(db, org_id, cp, lead, "Wrong person, asked to stop (EvoSense reply)")
            if cp is not None:
                # wrong party stays the recorded status; suppression is on the platform list
                cp.status = "wrong_party"
            actions.append("Number added to the suppression list; lead marked DNC")
        elif lead is not None:
            from app.services import cadence_service
            cadence_service.stop_cadence_for_lead(db, lead.id, "wrong_person")
        engagement.status = "stopped"
        engagement.blocked_reason = "wrong_party"
    elif outcome == C.O_DNC:
        _opt_out(db, org_id, cp, lead, "Opt-out reply to EvoSense")
        engagement.status = "stopped"
        engagement.blocked_reason = "opted_out"
        actions.append("Opted out: lead DNC, suppression entry, contact opted out, cadence stopped")
    elif outcome in (C.O_NOT_NOW, C.O_CALL_LATER, C.O_FAMILY, C.O_MAYBE):
        pol = ST.nurture_policy(strategy) if strategy else {"allow_nurture": True, "default_days": 60}
        until, why = nurture_date(text, C.now(), int(pol.get("default_days") or 60))
        set_nurture(db, prop, engagement, lead, until, "%s — seller said “%s”" % (
            outcome.replace("_", " ").title(), _sentences(text)[-1][:120] if _sentences(text) else text[:120]))
        actions.append("Nurture until %s (%s)" % (until.strftime("%b %d, %Y"), why))
    elif outcome in (C.O_ALREADY_SOLD, C.O_NOT_OWNER, C.O_LISTED, C.O_HARD_NO):
        engagement.status = "stopped"
        if lead is not None:
            from app.services import cadence_service
            cadence_service.stop_cadence_for_lead(db, lead.id, outcome.lower())
        if outcome == C.O_NOT_OWNER and cp is not None:
            cp.status = "wrong_party"
            cp.status_reason = "Replied: not the owner"
        actions.append("Stopped: %s" % outcome.replace("_", " ").lower())
    else:
        engagement.status = "responded"
        if cp is not None:
            cp.positive_response = True
    db.flush()

    # Seller Intent, from seller-stated facts only.
    facts = (db.query(EvoSenseFact).filter(EvoSenseFact.organization_id == org_id,
                                           EvoSenseFact.property_id == prop.id).all())
    inbound = (db.query(EvoSenseMessage).filter(EvoSenseMessage.organization_id == org_id,
                                                EvoSenseMessage.engagement_id == engagement.id,
                                                EvoSenseMessage.direction == "inbound").count())
    si = SC.seller_intent(facts, outcome, inbound)
    SC.record(db, prop, "seller_intent", si, subject_type="engagement", subject_id=engagement.id,
              strategy_id=getattr(strategy, "id", None))
    prop.seller_intent = si["value"]
    if cp is not None:
        CT.score_contact_point(db, prop, cp)

    if engagement.status == "responded":
        reasons = []
        threshold = getattr(strategy, "handoff_intent_threshold", 70) if strategy else 70
        kinds = {f.fact_type for f in made}
        if si["value"] is not None and si["value"] >= threshold:
            reasons.append("INTENT_THRESHOLD")
        if "asking_price" in kinds:
            reasons.append("PRICE_STATED")
        if outcome == C.O_WANTS_OFFER or "offer_request" in kinds:
            reasons.append("WANTS_OFFER")
        if outcome == C.O_APPOINTMENT or "appointment_request" in kinds:
            reasons.append("APPOINTMENT")
        if "callback_request" in kinds:
            reasons.append("CALLBACK")
        if outcome == C.O_ESTATE or "estate_context" in kinds:
            reasons.append("ESTATE")
        if outcome == C.O_TENANT:
            reasons.append("TENANT_ISSUE")
        if outcome == C.O_UNKNOWN:
            reasons.append("UNCLEAR")
        if reasons:
            HO.open_handoff(db, prop, reasons, engagement=engagement,
                            priority=min(100, 40 + (si["value"] or 0) // 2),
                            next_action="Call the seller" if "PRICE_STATED" in reasons or
                            "INTENT_THRESHOLD" in reasons else "Read the reply and decide")
            if lead is not None and not lead.is_test:
                from app.services import cadence_service
                cadence_service.stop_cadence_for_lead(db, lead.id, "handed_off")
            engagement.cadence_paused_by_evosense = True
            actions.append("NEEDS YOU: " + ", ".join(reasons))

    C.log_event(db, org_id, "reply.read", property_id=prop.id, is_test=prop.is_test,
                actor_type=C.ACTOR_AUTOMATION if not user else C.ACTOR_USER, user=user,
                summary="Reply read as %s%s" % (outcome, ("; " + "; ".join(actions)) if actions else ""),
                details={"message": msg.id, "facts": [f.fact_type for f in made],
                         "seller_intent": si["value"]})
    _clear_ai_pending(db, prop)
    EV.rescore(db, prop, strategy)
    db.commit()
    return {"message_id": msg.id, "outcome": outcome, "reader": reading["reader"],
            "facts": [{"fact_type": f.fact_type, "value": f.value, "quote": f.quote} for f in made],
            "seller_intent": si, "actions": actions, "status": prop.status}


def _clear_ai_pending(db, prop):
    """A reply that was pending AI review has now been read: the review
    reason goes, and a hand-off that existed only for it is closed."""
    from app.models.evosense_models import EvoSenseHandoff
    for h in (db.query(EvoSenseHandoff)
              .filter(EvoSenseHandoff.organization_id == prop.organization_id,
                      EvoSenseHandoff.property_id == prop.id,
                      EvoSenseHandoff.status.in_(("open", "acknowledged"))).all()):
        reasons = [r for r in (C.jload(h.reasons, []) or []) if r.get("code") != AI_REVIEW_PENDING]
        if len(reasons) == len(C.jload(h.reasons, []) or []):
            continue
        h.reasons = C.jdump(reasons)
        if not reasons:
            h.status = "dismissed"
            h.resolved_at = C.now()
            h.resolution_note = "AI review resolved: the reply was read"
    db.flush()


def pending_messages(db, org_id: Optional[str] = None):
    q = db.query(EvoSenseMessage).filter(EvoSenseMessage.direction == "inbound",
                                         EvoSenseMessage.outcome.is_(None))
    if org_id:
        q = q.filter(EvoSenseMessage.organization_id == org_id)
    return q.order_by(EvoSenseMessage.created_at.asc())


def retry_pending(db, org_id: Optional[str] = None, limit: int = 200) -> int:
    """Read again every saved seller reply whose reading is pending (the AI
    was unavailable, or EvoSense was paused). Safe to call any number of
    times: a message that gets an outcome is never read twice."""
    done = 0
    for msg in pending_messages(db, org_id).limit(limit).all():
        ctl = C.controls(db, msg.organization_id)
        if ctl.paused_all:
            continue
        try:
            res = evaluate(db, msg.organization_id, msg)
            if res.get("outcome"):
                done += 1
        except Exception:  # noqa: BLE001 - one bad message never blocks the others
            db.rollback()
            C.log.exception("evosense: retry of message %s failed", msg.id)
    return done


def set_nurture(db, prop, engagement, lead, until: datetime, reason: str, *, user=None):
    engagement.status = "nurture"
    engagement.nurture_until = until
    engagement.nurture_reason = reason[:250]
    engagement.nurture_resumed_at = None
    if lead is not None:
        from app.services import cadence_service
        cadence_service.stop_cadence_for_lead(db, lead.id, "nurture")
    engagement.cadence_paused_by_evosense = True
    C.log_event(db, prop.organization_id, "nurture.set", property_id=prop.id, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=prop.is_test,
                summary="Nurture until %s: %s" % (until.strftime("%Y-%m-%d"), reason))


NURTURE_PRESETS = {"30_days": 30, "60_days": 60, "90_days": 90, "6_months": 182}


def nurture_until_from(choice: str, custom_date: Optional[str]) -> datetime:
    if choice in NURTURE_PRESETS:
        return C.now() + timedelta(days=NURTURE_PRESETS[choice])
    if choice in ("date", "custom") and custom_date:
        d = datetime.strptime(custom_date[:10], "%Y-%m-%d")
        if d <= C.now():
            raise ValueError("Pick a date in the future.")
        return d
    raise ValueError("Choose 30_days, 60_days, 90_days, 6_months, or a date.")


def resume_due(db, org_id: str) -> int:
    """Nurture engagements whose date has come back to the ready queue, context kept."""
    from app.services.evosense import evaluate as EV
    db.flush()
    due = (db.query(EvoSenseEngagement)
           .filter(EvoSenseEngagement.organization_id == org_id,
                   EvoSenseEngagement.status == "nurture",
                   EvoSenseEngagement.nurture_until <= C.now()).all())
    for eng in due:
        eng.status = "pending"
        eng.nurture_resumed_at = C.now()
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == eng.property_id).first()
        C.log_event(db, org_id, "nurture.due", property_id=eng.property_id,
                    is_test=eng.is_test, summary="Nurture date reached — back in the ready queue. "
                    "Context: %s" % (eng.nurture_reason or ""))
        if prop is not None:
            EV.rescore(db, prop)
    return len(due)
