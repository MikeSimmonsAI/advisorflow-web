"""THE SCI APPOINTMENT SETTER - a family replies, the setter answers in
conversation and works toward a phone appointment or a personal visit.

How a reply flows (called from responses.on_inbound, after the reply is
recorded and the team alerted - nothing here replaces that):

  1. RULES FIRST, NO AI. Opt-out ends the conversation. Wrong person / bad
     data go to a person. An automatic reply (driving / away) is never
     answered - no loops - and the regular spaced follow-up continues. A
     conversation a person has taken over (a staff text after the setter's
     last turn, or an explicit hand-off) is left to the person.
  2. THE AI READS the whole conversation and decides ONE next step, returned
     as JSON: answer, offer_times, book, callback, send_guide, handoff, wait.
  3. CODE DOES THE REAL THINGS. Times come only from the calendar engine
     (appointments.available_slots); a booking only counts when the calendar
     confirms it (appointments.book); the guide is the location's approved
     guide; every outgoing line passes a safety check (no prices, pressure,
     invented facts) or goes to a person instead.
  4. DELIVERY by the program's mode: "draft" - the text waits as the reply's
     suggested reply for a person to send; "auto" - sent from the SCI line
     inside the family's contact hours (held until then otherwise);
     synthetic test contacts are always "simulated" - nothing leaves.

Production stays "off" until the owner switches it on. A failure anywhere
hands the conversation to a person and is recorded; it never sends a guess.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.program_models import ProgramConversation, ProgramConversationTurn, ProgramResponse
from app.services.programs import appointments as A

log = logging.getLogger(__name__)

OFF, DRAFT, AUTO = "off", "draft", "auto"
# Operational limits instead of a blunt eight-turn cut-off: a long, healthy
# conversation keeps going; a runaway one (or a family texting all day) is
# handed to a person. Confidence and risk decide hand-offs, not turn counts.
MAX_AI_PER_DAY = 6            # AI messages to one family in one Central day
MAX_AI_TOTAL = 30             # AI messages to one family, ever
MAX_FOLLOWUPS = 2             # unanswered follow-ups the setter may send
LOW_CONFIDENCE = 0.4          # below this the setter does not send; a person reviews
OFFERED_FOLLOWUP = timedelta(days=2)     # times offered, no answer: one gentle check-in
INFO_FOLLOWUP = timedelta(days=3)        # guide sent, no answer: one check-in
FOLLOWUP_GAP = timedelta(hours=20)
ACTIONS = ("answer", "offer_times", "book", "callback", "send_guide", "mail_guide", "follow_up_later",
           "handoff", "close", "wait")
MAX_AI_TURNS = MAX_AI_TOTAL   # kept for older imports

_BANNED = [
    (r"\$\s?\d|\b\d+\s?(dollars|per month|a month)\b", "mentions a price"),
    (r"\b(guarantee[ds]?|risk[- ]free|no obligation ever)\b", "makes a guarantee"),
    (r"\b(act now|limited time|only today|expires?|last chance|hurry|before it'?s too late|"
     r"prices? (are )?(going|go) up|running out)\b", "creates pressure"),
    (r"\b(service corporation|sci\b|dignity memorial)", "names the parent company"),
    (r"\b(it'?s|is|are|completely|totally|100%) free\b|\bcomplimentary\b|\bno[- ]cost\b|\bfree of charge\b|\bat no charge\b",
     "calls something free"),
    (r"\bas an ai\b|\blanguage model\b", "talks about being an AI"),
]


def killed() -> bool:
    """EMERGENCY OFF for the SCI setter alone: AI_SCI_SETTER_ENABLED=false
    stops every answer, send and held reply at once, whatever the program's
    mode says. Unset = not killed (the program's own mode decides, default off)."""
    import os
    return (os.environ.get("AI_SCI_SETTER_ENABLED") or "").strip().lower() in ("0", "false", "no", "off")


def mode_of(prog) -> str:
    if killed():
        return OFF
    m = (getattr(prog, "ai_setter_mode", None) or OFF).strip().lower()
    return m if m in (OFF, DRAFT, AUTO) else OFF


# ── conversation record ──────────────────────────────────────────────────────

def conversation_for(db: Session, lead, *, create: bool = True) -> Optional[ProgramConversation]:
    conv = (db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id)
            .order_by(ProgramConversation.created_at.desc()).first())
    if conv is None and create:
        conv = ProgramConversation(organization_id=lead.organization_id, lead_id=lead.id, state="ai_active",
                                   is_test=bool(getattr(lead, "is_test", False)))
        db.add(conv)
        db.flush()
    return conv


def _turn(db, conv, role, text, *, action=None, delivery=None, reason=None, message_id=None, response_id=None):
    t = ProgramConversationTurn(conversation_id=conv.id, organization_id=conv.organization_id,
                                lead_id=conv.lead_id, role=role, text=text, action=action,
                                delivery=delivery, reason=reason, message_id=message_id,
                                response_id=response_id)
    db.add(t)
    db.flush()
    return t


def history(db: Session, lead, limit: int = 40) -> List[Dict]:
    """What the family ACTUALLY saw and said, oldest first: texts that went to
    them by any path and their replies. A draft or a held reply was never
    received and is NOT in here (see unsent_drafts). For a synthetic contact
    nothing is ever really sent, so its simulated lines stand in for sent ones
    and are marked as such."""
    from app.models.models import Message
    from app.models.models import EmailMessage
    from app.services import send_source as _ss
    from app.services.programs import email_reply as _er
    rows = []
    for m in db.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at.desc()).limit(limit):
        if (m.delivery_status or "") in ("failed", "undelivered", "simulated") and not _synthetic(lead):
            continue
        rows.append((m.sent_at, "us", m.body or "", "manual" if m.send_source == _ss.MANUAL else None, "text", None))
    for e in (db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id, EmailMessage.status == "sent")
              .order_by(EmailMessage.sent_at.desc()).limit(limit)):
        rows.append((e.sent_at, "us", _er.plain_text(e.body_html or "")[:1200],
                     "manual" if e.send_source == _ss.MANUAL else None, "email",
                     {"to": getattr(e, "to_email", None), "subject": e.subject}))
    for r in (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
              .order_by(ProgramResponse.received_at.desc()).limit(limit)):
        meta = _er.inbound_meta(db, r) if r.channel == "email" else {}
        rows.append((r.received_at, "family", r.body_excerpt or "", r.response_class,
                     "email" if r.channel == "email" else "text",
                     {"from": meta.get("sender_email"), "from_name": meta.get("sender_name"),
                      "subject": meta.get("subject")} if meta else None))
    conv = conversation_for(db, lead, create=False)
    if conv is not None and _synthetic(lead):
        for t in (db.query(ProgramConversationTurn)
                  .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                          ProgramConversationTurn.delivery == "simulated")
                  .order_by(ProgramConversationTurn.created_at.desc()).limit(limit)):
            rows.append((t.created_at, "us", t.text or "", "simulated", "text", None))
    rows = [r for r in rows if r[0] is not None]
    rows.sort(key=lambda r: r[0])
    out = []
    for at, who, text, tag, channel, extra in rows[-limit:]:
        item = {"at": at.strftime("%Y-%m-%d %H:%M UTC"), "who": who, "channel": channel, "text": text}
        if tag == "auto_reply":
            item["note"] = "automatic reply from their phone"
        if tag == "manual":
            item["note"] = "written and sent by a person on our team"
        if extra:
            item.update({k: v for k, v in extra.items() if v})
        out.append(item)
    return out


def unsent_drafts(db: Session, conv) -> List[str]:
    """Replies the setter WROTE that the family never received (draft, held,
    blocked). Shown to the model as unsent, so it never refers to them."""
    if conv is None:
        return []
    rows = (db.query(ProgramConversationTurn)
            .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                    ProgramConversationTurn.delivery.in_(("draft", "held", "blocked")))
            .order_by(ProgramConversationTurn.created_at.desc()).limit(3).all())
    return [t.text for t in reversed(rows) if t.text]


# ── family memory ────────────────────────────────────────────────────────────

MEMORY_LISTS = ("wants", "reasons", "questions_open", "questions_answered", "concerns", "service_interests",
                "participants", "info_provided", "commitments", "notes")
MEMORY_TEXT = ("existing_arrangements", "preferred_contact", "best_times", "readiness", "next_step")
READINESS = ("unknown", "not_ready", "considering", "ready", "booked", "closed")


def memory_of(conv) -> Dict:
    try:
        m = json.loads(getattr(conv, "memory", None) or "{}")
        return m if isinstance(m, dict) else {}
    except (ValueError, TypeError):
        return {}


def merge_memory(conv, update: Optional[Dict]) -> Dict:
    """Fold the model's notes into the family's memory. Lists are merged
    without duplicates and capped; text fields are replaced; an answered
    question leaves the open list. Unknown keys are ignored."""
    mem = memory_of(conv)
    if not isinstance(update, dict):
        return mem
    for k in MEMORY_LISTS:
        v = update.get(k)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            continue
        cur = [x for x in mem.get(k, []) if isinstance(x, str)]
        for item in v:
            item = str(item).strip()[:160]
            if item and item.lower() not in {c.lower() for c in cur}:
                cur.append(item)
        mem[k] = cur[-12:]
    answered = {a.lower() for a in mem.get("questions_answered", [])}
    if answered:
        mem["questions_open"] = [q for q in mem.get("questions_open", []) if q.lower() not in answered]
    for k in MEMORY_TEXT:
        v = update.get(k)
        if isinstance(v, str) and v.strip():
            mem[k] = v.strip()[:200]
    if mem.get("readiness") not in READINESS:
        mem.pop("readiness", None)
    if isinstance(update.get("mail_request"), dict) or update.get("mail_request") is True:
        mr = update.get("mail_request") if isinstance(update.get("mail_request"), dict) else {}
        mem["mail_request"] = {"requested": True, "what": str(mr.get("what") or "printed planning guide")[:80]}
    conv.memory = json.dumps(mem)
    return mem


def _facts(db, prog, lead, conv) -> Dict:
    """What the CODE knows for certain - never taken from the model."""
    from app.models.models import BookingLink
    sent_ai = (db.query(ProgramConversationTurn.id)
               .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                       ProgramConversationTurn.delivery.in_(("sent", "simulated"))).count())
    booking = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                            BookingLink.status.in_(("booked", "confirmed")))
               .order_by(BookingLink.booked_time.desc()).first())
    return {
        "ai_messages_delivered": sent_ai,
        "times_offered_now": [A.describe(s) for s in A.offered(conv)],
        "times_offered_count": _offers_made(db, conv),
        "confirmed_booking": A.describe(booking.booked_time.replace(tzinfo=None))
        if booking is not None and booking.booked_time else None,
        "callback_requested": conv.callback_window if conv.state == "callback_requested" else None,
        "guide_sent_by_text": _guide_sent(db, conv),
        "mail_task_open": bool(memory_of(conv).get("mail_request")),
        "followups_sent": conv.followups_sent or 0,
        "personal_delivery_available": personal_delivery_available(db, lead),
        "decline": getattr(conv, "_decline", None),
    }


def personal_delivery_available(db, lead) -> bool:
    """May I offer to bring a printed guide to the family personally? Only when
    the family's location says so (LocationProfile.brand_settings
    {"personal_delivery": true}); otherwise I invite them in instead."""
    try:
        from app.services.programs import identity
        prof = identity.location_profile_for_lead(db, lead)
        bs = getattr(prof, "brand_settings", None) if prof is not None else None
        if isinstance(bs, str):
            bs = json.loads(bs or "{}")
        return bool((bs or {}).get("personal_delivery"))
    except Exception:                                    # noqa: BLE001
        return False


def _offers_made(db, conv) -> int:
    return (db.query(ProgramConversationTurn.id)
            .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                    ProgramConversationTurn.action == "offer_times",
                    ProgramConversationTurn.delivery.in_(("sent", "simulated"))).count())


def _guide_sent(db, conv) -> bool:
    return (db.query(ProgramConversationTurn.id)
            .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                    ProgramConversationTurn.action == "send_guide",
                    ProgramConversationTurn.delivery.in_(("sent", "simulated"))).first()) is not None


def _human_took_over(db: Session, lead, conv) -> bool:
    """A person texted or emailed this family by hand AFTER the AI last
    spoke AND after the conversation was last handed to the AI. A manual
    message sent before someone switched AI Handling on (or used "Send & let
    AI continue") never blocks the AI - ownership is the explicit setting,
    not an old timestamp."""
    from app.models.models import EmailMessage, Message
    from app.services import send_source as ss
    marks = [t for t in (conv.last_ai_at, conv.created_at, getattr(conv, "owner_changed_at", None)) if t]
    since = max(marks) if marks else datetime.min
    if db.query(Message.id).filter(Message.lead_id == lead.id, Message.send_source == ss.MANUAL,
                                   Message.sent_at > since).first() is not None:
        return True
    return db.query(EmailMessage.id).filter(EmailMessage.lead_id == lead.id, EmailMessage.send_source == ss.MANUAL,
                                            EmailMessage.status == "sent",
                                            EmailMessage.sent_at > since).first() is not None


AI_OWNER, HUMAN_OWNER = "ai", "human"


def owner_of(conv) -> str:
    return HUMAN_OWNER if conv is not None and conv.state in ("human", "stopped") else AI_OWNER


def set_owner(db: Session, lead, owner: str, *, actor: Optional[str], note: Optional[str] = None,
              at: Optional[datetime] = None) -> ProgramConversation:
    """Hand the conversation to the AI or to a person. Nothing is sent and no
    old reply is processed again: the AI takes the NEXT reply that arrives.
    Human: AI stops at once - scheduled follow-ups are cancelled and replies
    held for contact hours are dropped. AI: never for a family that opted
    out or is Do Not Contact."""
    if owner not in (AI_OWNER, HUMAN_OWNER):
        raise ValueError("owner must be ai or human")
    conv = conversation_for(db, lead)
    now = at or datetime.utcnow()
    if owner == HUMAN_OWNER:
        if conv.state != "stopped":
            conv.state = "human"
        conv.handoff_reason = (note or "a person is handling this conversation")[:250]
        conv.next_followup_at = None
        for t in (db.query(ProgramConversationTurn)
                  .filter(ProgramConversationTurn.conversation_id == conv.id,
                          ProgramConversationTurn.delivery == "held")):
            t.delivery = "dropped"
    else:
        if conv.state == "stopped" or (getattr(lead, "status", None) or "") == "dnc":
            raise ValueError("This family opted out - the AI cannot take this conversation.")
        if conv.state in ("human", "callback_requested", "closed"):
            conv.state = "ai_active"
        conv.handoff_reason = None
    conv.owner_changed_at = now
    conv.owner_changed_by = actor
    conv.owner_note = (note or ("AI handling on" if owner == AI_OWNER else "Human handling on"))[:250]
    _turn(db, conv, "system", None, action="owner_" + owner,
          reason=conv.owner_note)
    db.commit()
    return conv


# ── safety check on every outgoing line ─────────────────────────────────────

def check_text(text: str, *, allowed_links: List[str], channel: str = "sms",
               contact: Optional[str] = None) -> List[str]:
    """THE FINAL GATE on the assembled outgoing text - after the model's reply
    has been changed by the app (times filled in, a guide link added, or a
    fallback sentence swapped in). Voice is checked here too, so app-built
    text can't reintroduce "Kerry Allan would be happy to..." (Oct 16)."""
    problems = []
    t = (text or "").strip()
    if not t:
        return ["empty"]
    if len(t) > (1500 if channel == "email" else 480):
        problems.append("too long for an email" if channel == "email" else "too long for a text")
    low = t.lower()
    for pat, why in _BANNED:
        if re.search(pat, low):
            problems.append(why)
    for url in re.findall(r"https?://\S+", t):
        if url.rstrip(".,)") not in allowed_links:
            problems.append("contains a link that is not an approved one")
    if "{" in t or "}" in t:
        problems.append("unfilled placeholder")
    if contact and third_person(t, contact):
        problems.append("talks about %s in the third person" % contact)
    return problems


# ── the AI's decision ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You write as {contact}, the family-services advisor at {location} (a funeral home / cemetery), in {contact}'s own voice. A family replied to our planning outreach. You are a skilled, compassionate, confident Family Service Advisor, not a script, not an answering bot and not a fulfillment clerk: read what THIS family said, in the context of everything they've said before, notice the opportunity in it, and choose the single most helpful next step.

FIRST PERSON, ALWAYS. Every message goes out signed by {contact}, so write as {contact}: "I", "me", "my", "I'd be glad to". NEVER refer to {contact} by name or in the third person ("{contact} would be glad to help", "{contact} can go over it", "so {contact} can answer") - that reads as two different people. "We" is fine for the team at {location}.

YOUR PRIMARY GOAL is to help THIS family, which MAY involve an IN-PERSON VISIT at {location} - the family sitting down with me, so I can answer their questions, explain what applies to their situation and go over their options or existing file. An existing customer who already bought a plan is NOT a new-plan prospect: acknowledgement and an answer can be the finish line. Never push scheduling before understanding their message. Work in this order: understand exactly what they said; answer their question or concern directly; give one genuine, specific reason a visit would help THEM (their guide reviewed with them, their questions answered in person, their existing file and contacts checked); invite them in with two real times ({{TIMES}}); when they pick one, book it. Honour a decline, a "just mail it", a timing request or a preferred channel without pressure - never make information conditional on a visit, and never ask the same question twice.

WORK IN THIS ORDER (fill every part of the JSON):
1. UNDERSTAND. What do they actually mean, including indirect or casual wording ("maybe after the holidays" = not now, later; "my daughter handles that" = involve the daughter; "ok" after times were offered is NOT a choice)? Their mood? Any questions, worries or objections? Did they change their mind or return to an earlier question? Read FAMILY MEMORY first - never ask for something they already told us.
2. OBJECTIVE. Pick the one goal for this message: answer their question / build confidence / move toward a call or visit / confirm a time they chose / honour their timing / fulfil a request (guide, mail) / close politely / bring in a person.
3. ACTION. Exactly one: answer, offer_times, book, callback, send_guide, mail_guide, follow_up_later, handoff, close, wait.
4. COMPOSE the reply: plain, warm, local, 1-3 short sentences, usually ONE purposeful question (never two). No emojis, no sales talk, no signature. Vary your wording - never repeat a sentence we already sent. Use their first name at most occasionally.
5. CHECK it yourself against the rules below before returning.

ANSWERING QUESTIONS. When APPROVED INFORMATION answers the question, ANSWER IT in plain words first - do not dodge to an appointment. Then, only if it fits naturally, one gentle next step. Questions about price, eligibility for benefits (veterans, insurance), or the details of their own plan/property are best gone over with me personally: acknowledge kindly and offer a short call or visit to go over it ("offer_times"). Never invent facts, numbers, benefits, timelines or anything about their family or plan.

MOVING TOWARD AN APPOINTMENT - without pressure:
- A call or visit is offered when it genuinely helps (they're interested, have questions only I can answer in person, or asked). Never offer times twice in a row if they didn't engage with the first offer; respond to what they said instead. Never invent urgency.
- To offer times, write the placeholder {{TIMES}} where two times belong ("Would {{TIMES}} work for a short visit?"). That sentence IS your one question - do not ask anything else in the same message. The system fills in two REAL open times. NEVER write a day or clock time yourself (repeating times already offered is fine). If they mentioned when they're free ("afternoons", "next Tuesday"), put it in best_times so times can match.
- Visit = about an hour (60 minutes) in person; a phone appointment is about 30 minutes. Family members are welcome - if a spouse or adult child should be included, say so warmly.
- BOOK only when their words clearly pick ONE of the times we offered ("the first one", "Thursday at 10 works") -> slot_choice 1 or 2. If they name a different specific day AND time, use requested_time YYYY-MM-DDTHH:MM (Central); the system checks it. "Friday works" when Friday has more than one possibility, "sure", "ok", "sounds good" -> do NOT book; ask which time.
- Never say an appointment is booked, set or confirmed - the system confirms bookings itself.

HONOUR THEIR TIMING:
- Busy right now / will get back to us / call me later: acknowledge warmly. If they gave a time to be called -> "callback" with callback_window in their words. If they said "later" with a horizon ("after the holidays", "next month", "in a couple weeks") -> "follow_up_later" with follow_up_date (YYYY-MM-DD, Central) and follow_up_about (what to pick up next time). Otherwise one gentle question about a better time.
- Not interested (but not STOP): thank them, leave the door open in one sentence -> "close".
- ARRIVING NOW. They say they are on the way, will "run by", "stop in", "come by" today or within a few hours ("I'll run by there in about 15 minutes"): this is a live visit - the best outcome there is. Action "handoff" (the advisor is alerted at once to greet them). Reply warmly in one or two sentences: glad they're coming, and I'll be watching for them / the team will be ready for them. Do not offer {{TIMES}}, do not ask them to pick a time, and never say an appointment is booked or confirmed.
- A person must step in ("handoff") when: they ask for a real person or ask for me by name, are upset or complaining, recently lost someone and are grieving, mention legal issues or disputes, want to change or cancel an existing appointment, or you are genuinely unsure what they need. Reply briefly that I'll reach out to them personally. Do NOT hand off ordinary questions.
- Skeptical ("is this a scam?", "how did you get my number?", "are you a robot?"): be honest and calm - it's the family services team at {location} following up on planning information; if asked whether this is a real person, a robot, AI or automated, ANSWER THAT QUESTION HONESTLY in the first sentence - never dodge it: say an automated assistant helps me keep up with messages and scheduling, that I see every reply, and that I'm glad to talk by phone. Offer to stop if they prefer.
- Planning guide request -> "send_guide": say you'll get the guide to them AND invite them to sit down with you so you can go over it together and answer what applies to them, with {{TIMES}} as your one question (e.g. "...and I'd be glad to go over it with you in person - would {{TIMES}} work for a short visit?"). The system adds the guide or, by text, arranges it.
- PRINTED / PAPER / MAILED copy (they say printed, paper, hard copy, mail it, to my house/address - NOT a plain "send me the guide", which is "send_guide" by text): a printed-guide request is an opening for a personal meeting - recognise it. If PERSONAL DELIVERY is available (facts_known_for_certain.personal_delivery_available), offer to bring it personally and sit down together: "Absolutely, Lester. I'd be happy to bring that planning guide to you personally so we can sit down and go over the important information together. What's a good day and time for me to stop by?" (action "mail_guide"; no {{TIMES}}; put any day/time they give in best_times). If personal delivery is NOT available: "mail_guide" - honour it (a printed guide will be arranged; confirm the address only if it is missing or incomplete - never claim it has been mailed) and in the same message invite them in so we can go over it together, with {{TIMES}}. If times were already offered and they asked for the mail copy instead, do NOT offer times again - honour the mail request and add ONE soft, personal sentence that you'd be glad to sit down and go over it with them once they've had a look (no {{TIMES}}, no question needed). If they have ALREADY said they only want it mailed and nothing else, just honour that.
- UNCLEAR "already got it / them / that": it may mean the guide, their benefits, or arrangements. If FAMILY MEMORY and the conversation don't settle which, ask ONE short, warm clarifying question first (e.g. "Glad to hear it, Ronald - do you mean you already have your veteran benefits paperwork, or the planning guide?") - no {{TIMES}} in that message.
- "We already have plots / a plan / property" WITHOUT saying where: it may well be with us - don't close. Thank them and ask one warm question (is that here at {location}?) or offer a review of their file.
- Plan with ANOTHER funeral home / cemetery, or "already took care of all that": thank them warmly and respect it - no times, no push. At most one optional sentence that we're here if they ever want anything reviewed ("answer" or "close").
- Existing plan WITH US / "we purchased a plan from you last December" -> treat them as an EXISTING CUSTOMER, not a new planning lead. Specifically acknowledge the purchase claim WITHOUT pretending to have verified their contract or file. If they are simply explaining they already purchased, use action "answer", ask briefly whether they need help with their existing arrangements, and do NOT offer {{TIMES}} or push a meeting. A FILE REVIEW is a valid service when the family asks for a meeting or review; only then offer times. A person checks the plan details.
- Wants to CHANGE an existing plan (contact person, burial to cremation, wishes): that is exactly what a review with me is for - offer times ("offer_times"); not a hand-off.
- "I need to talk to my husband / wife / kids first": NOT a no. Acknowledge warmly, say they're welcome to join, and ask one gentle question (or "follow_up_later" if they give a horizon). Never "close".
- "When are you open?" / hours: there are no posted office hours in APPROVED INFORMATION - never invent any. Say I meet families by appointment and offer {{TIMES}} ("offer_times").
- PERSONAL DELIVERY is an internal setting: never tell a family it is or isn't "available". If they ask whether someone can come to them and it isn't available, simply and warmly invite them in with {{TIMES}}.
- THEY ASK US TO COME TO THEM (facts_known_for_certain.decline.family_asks_for_home_visit) and home_visits_offered_by_this_location is true: say yes warmly in one or two sentences and that I'll reach out personally to find a time that works ("handoff"). Never {{TIMES}}, never book it, never say it is scheduled. If home visits are not offered, invite them in with {{TIMES}} instead.
- "Who is this?" / "What is this about?": answer plainly - this is {contact} with the family services team at {location}, following up on the planning information they asked about.
- If asked whether the guide or a visit costs anything, do not use the words free, complimentary or no cost; say you're glad to send the guide and you can answer any cost questions.
- "wait" only for a closing "ok"/"thanks" after an appointment is confirmed, or a message that needs no reply.
- A "No" ANSWERS THE LAST QUESTION WE ASKED (facts_known_for_certain.decline). If what_was_declined is "declined_venue" or "cant_get_there", they turned down COMING IN, not every kind of help: offer the ONE alternative named in alternative_to_offer, once, short and warm, as your one question, then respect their answer:
  * "home_visit": "No problem - I can come to you instead, if that's easier. Would that be more convenient?" (action "answer"; never {{TIMES}}, never book it - if they say yes, action "handoff" and say I'll reach out personally to find a time).
  * "phone": offer a short phone appointment instead (action "offer_times", appointment_kind "phone").
  * "guide": "No problem at all. Would it help if I sent you the planning guide so you have it handy?" (action "answer").
  * "none", or alternative_already_offered is set and they say no again: thank them and close warmly ("close").
  If what_was_declined is "declined_time": the times don't suit, the visit might - ask what week or time of day works, or "follow_up_later"; never repeat the same days. "declined_home": never suggest coming to their home. "stop": thank them briefly and "close" - no alternative, no times. "unclear_no" (a bare no to a general question): one short clarifying question or a warm close, never a pitch. NEVER mention any setting or what is or isn't available.
- "close" ONLY when they clearly decline (not interested, no thanks, all set, already taken care of). A bare "k", "maybe", "idk", "lol" or an emoji is NOT a decline: reply warmly and ask one easy question (e.g. whether they'd like the planning guide).
- They say they'll come in / want to meet / ask for times: offer times now ("offer_times"), unless times are already on the table - then ask which one.
- They insist on a price before meeting: don't hand off. Acknowledge it plainly, say you can go over costs by phone, and offer a short phone call ("offer_times" with appointment_kind "phone", or "callback" if they name a time).
- Our first text offered the planning guide, so a bare "k" / "ok" / "yes" / thumbs-up to it means yes: "send_guide" and say it's on its way - don't also ask whether they want it.

NEVER: prices, costs, discounts, "free"/"complimentary"/"no cost", guarantees, deadlines, pressure, the parent company's name, links (the system adds approved ones), questions about Social Security numbers or private documents by text, promises a person hasn't made.

{knowledge}

Return ONLY JSON:
{{"understanding": {{"meaning": "<one line>", "mood": "positive|neutral|hesitant|negative|grieving|upset|confused", "questions": ["..."], "objections": ["..."], "changed_mind": false}},
 "objective": "<one line>",
 "action": "answer|offer_times|book|callback|send_guide|mail_guide|follow_up_later|handoff|close|wait",
 "reply": "<the text to send, or empty for wait>",
 "slot_choice": 0, "requested_time": "", "appointment_kind": "phone|visit|", "service_type": "",
 "callback_window": "", "follow_up_date": "", "follow_up_about": "",
 "memory_update": {{"wants": [], "reasons": [], "questions_open": [], "questions_answered": [], "concerns": [], "service_interests": [], "participants": [], "info_provided": [], "commitments": [], "existing_arrangements": "", "preferred_contact": "", "best_times": "", "readiness": "unknown|not_ready|considering|ready", "next_step": "", "mail_request": false}},
 "confidence": <number from 0 to 1: how sure you are this is the right next step>,
 "reason": "<why this action, one line>"}}"""


def _context(db, prog, lead, conv, resp, *, follow_up: Optional[str] = None) -> Dict:
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    now_c = A._today_central()
    ctx = {
        "family_first_name": (lead.first_name or "").strip().title() or None,
        "location": prof.official_name if prof else "our family services team",
        "today": now_c.strftime("%A, %B %-d, %Y %-I:%M %p Central"),
        "conversation_state": conv.state,
        "family_memory": memory_of(conv),
        "facts_known_for_certain": _facts(db, prog, lead, conv),
        "latest_reply_class": resp.response_class if resp is not None else None,
        "latest_reply_intents": json.loads(resp.intents) if (resp is not None and resp.intents) else [],
        "conversation_family_actually_saw": history(db, lead),
        "replies_written_but_NOT_received_by_the_family": unsent_drafts(db, conv),
    }
    channel = (resp.channel if resp is not None else None) or "sms"
    ctx["reply_channel"] = "email" if channel == "email" else "text message"
    if channel == "email":
        from app.services.programs import email_reply as _er
        meta = _er.inbound_meta(db, resp)
        record = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title()
        writer = meta.get("sender_email")
        ctx["email"] = {
            "written_by": meta.get("sender_name") or writer,
            "writer_address": writer,
            "subject": meta.get("subject"),
            "person_on_our_record": record or None,
            "writer_is_the_record_person": bool(writer and (lead.email or "").strip().lower() == writer
                                                and not meta.get("sender_name")) or None,
            "rules": ("This reply came by EMAIL and your answer goes back by email, in the same thread, to the "
                      "writer. The writer may be a relative or someone acting for the person on our record "
                      "(read their words: 'my dad', 'my mother', 'on behalf of'). Address the WRITER, not the record "
                      "name, unless they are clearly the same person. Never assume they hold authority, paperwork "
                      "or arrangements they did not mention. You may use 2-5 short sentences and a greeting line. "
                      "No signature (the system adds it)."),
        }
    if follow_up:
        ctx["task"] = ("FOLLOW-UP: the family has not written since our last message. %s Write ONE short, "
                       "friendly follow-up that picks up exactly where things were left (use FAMILY MEMORY), "
                       "not a new introduction. If a follow-up would not help, return action \"wait\"." % follow_up)
    return ctx


_CLOCK = re.compile(r"\b\d{1,2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])|\b\d{1,2}:\d{2}\b|\bnoon\b", re.I)
_CLAIM_BOOKED = re.compile(r"\b(you'?re (all )?set for|you are (all )?set for|i'?ve booked|i booked|is booked|"
                           r"has been booked|is confirmed|has been confirmed|i'?ve scheduled|scheduled you|"
                           r"see you (on|at) )", re.I)
_CLAIM_MAILED = re.compile(r"\b(i'?ve|we'?ve|i have|we have|has been|have been|already) (mailed|sent it in the mail|"
                           r"put it in the mail)\b|\bwe mailed\b|\bit'?s in the mail\b", re.I)
_CLAIM_GUIDE = re.compile(r"\b(here'?s|here is) (the|your) (planning )?guide\b", re.I)


def third_person(reply: str, contact: Optional[str]) -> bool:
    """Does the reply talk about the signer in the third person ("Kerry Allan
    would be glad to help" - signed Kerry Allan)? "This is Kerry" is fine."""
    names = [n for n in {(contact or "").strip(), (contact or "").strip().split(" ")[0]} if len(n) >= 3]
    for n in sorted(names, key=len, reverse=True):
        for m in re.finditer(r"\b%s\b" % re.escape(n), reply or "", re.I):
            before = (reply[:m.start()] or "").lower()
            if re.search(r"(this is|i'?m|i am|it'?s)\s*$", before.rstrip()):
                continue
            return True
    return False


# ── WHAT WAS DECLINED (#234, Oct 19) ─────────────────────────────────────────
# A bare "No" answers the LAST QUESTION WE ASKED. "Could you stop by this
# week?" -> "No" declines coming in, not every kind of help; the setter used to
# close with a goodbye. Decided here in code from the last message the family
# actually saw - never by the model alone - and STOP / not interested always
# wins. At most ONE alternative, only one the location really offers.
DECLINED_STOP, DECLINED_VENUE, DECLINED_TIME, DECLINED_HOME, CANT_GET_THERE, UNCLEAR_NO = (
    "stop", "declined_venue", "declined_time", "declined_home", "cant_get_there", "unclear_no")
ALT_HOME, ALT_PHONE, ALT_GUIDE, ALT_NONE = "home_visit", "phone", "guide", "none"

# "stop" ONLY as a request to stop contact - never "I can STOP BY today" / "stop
# in" / "stop over", which is a family coming in (Oct 19 overnight: the bare word
# made an arriving family look like an opt-out). A real STOP is caught earlier by
# the opt-out rules and never reaches the setter at all.
_HARD_NO = re.compile(r"(\bstop\b(?!\s+(by|in|over|at|out|on)\b)|\b(unsubscribe|not interested|no thanks|no thank you|"
                      r"don'?t (text|contact|call|message|email|bother)|do not (text|contact|call|message|email)|"
                      r"remove me|take me off|leave me alone|wrong (number|person)|not for me|lose my number|"
                      r"quit (texting|messaging))\b)", re.I)
_BARE_NO = re.compile(r"^\s*(no+|nope|nah|no sir|no ma'?am|no,? i can'?t|i can'?t|can'?t|not really|negative)"
                      r"\s*[.!]*\s*$", re.I)
_CANT_GET_THERE = re.compile(r"(can'?t (get|make it|drive|come)( (there|in|out|by|over))?\b|no (ride|transportation|car)\b|"
                             r"don'?t drive|(too|kind of|pretty) far|hard (for me )?to get (there|out)|"
                             r"not able to (get|come) (there|out|in))", re.I)
_NOT_THESE_TIMES = re.compile(r"\b(not this week|this week (is|isn'?t|won'?t|doesn'?t)|neither (one|of those|works|time)|"
                              r"none of (those|them|these)|those (times|days) (don'?t|won'?t)|not (those|that) "
                              r"(days?|times?))\b", re.I)
_NO_HOME = re.compile(r"(don'?t (want|need) (anyone|anybody|someone|somebody|people|you|a home visit|home visits)|"
                      r"(do not|don'?t|please don'?t|no need to) (come|visit|stop by)( out)?( to| at)? (my|our|the) "
                      r"(house|home|place)|(nobody|no one|no-one) (should|needs to|can|is to) come|not (at|to) my (house|home)|"
                      r"no home visits?|not a home visit|no visits? at (my|our) (house|home))", re.I)
_INVITED_IN = re.compile(r"(stop by|come (in|by|out|see)|swing by|drop by|short visit|for a visit|visit (with )?us|"
                         r"sit down (with (me|us) )?(here|at)|meet (with )?(me|us) (here|at)|in person)", re.I)
_WANTS_HOME = re.compile(r"(come (to|out to|over to) (me|us|my|our|her|his|their)\b(?! office)|"
                         r"come (out|over) (here|to (me|us|my|our|her|his))|\bcome over\b(?! to (the|your|restland))|"
                         r"(rather|prefer|easier if|better if) (you|someone|somebody) (came|come|visit)|"
                         r"visit (me|us|her|him|them) at ((my|our|her|his|their) )?(home|house|place)|"
                         r"(meet|see) (me|us) at (my|our) (house|home|place)|"
                         r"(can'?t|cannot|unable to|doesn'?t|does not) (leave|get out of) the house|homebound|"
                         r"\bhome visit\b)", re.I)
HOME_VISIT_REASON = "Home visit requested - call the family to set a time"
_OFFERS_HOME = re.compile(r"(come to (you|your (home|house|place))|(to|at) your (home|house|place)|stop by your|"
                          r"visit you at|bring it (by|over) to you|meet you at your)", re.I)


def _track_alternative(conv, dc: Optional[Dict], action: Optional[str]) -> None:
    """Record, without PII, the path declined -> alternative offered ->
    accepted / declined, in the family's memory (reported apart from an
    opt-out or a lost family)."""
    if not dc:
        return
    mem = memory_of(conv)
    declined, alt, already = dc.get("what_was_declined"), dc.get("alternative_to_offer"), dc.get("alternative_already_offered")
    changed = False
    if already and not mem.get("alternative_outcome"):
        if action == "close" or declined == DECLINED_STOP:
            mem["alternative_outcome"] = "declined"
            changed = True
        elif action in ("offer_times", "book", "handoff", "send_guide", "mail_guide", "callback"):
            mem["alternative_outcome"] = "accepted"
            changed = True
    elif declined in (DECLINED_VENUE, CANT_GET_THERE) and alt not in (None, ALT_NONE) and action != "close":
        mem.update(objection=declined, alternative_offered=alt)
        changed = True
    elif declined == DECLINED_STOP:
        mem["objection"] = DECLINED_STOP
        changed = True
    if changed:
        conv.memory = json.dumps(mem)


def _last_outbound(db, lead) -> str:
    """The last message the family actually received from us."""
    for item in reversed(history(db, lead)):
        if item.get("who") == "us":
            return item.get("text") or ""
    return ""


def _plain(text: Optional[str]) -> str:
    return (text or "").replace("\u2019", "'").replace("\u2018", "'").strip()


def what_was_declined(body: str, last_outbound: str, times_on_table: bool) -> Optional[str]:
    b = _plain(body)
    if not b:
        return None
    if _HARD_NO.search(b):
        return DECLINED_STOP
    if _NO_HOME.search(b):
        return DECLINED_HOME
    if _NOT_THESE_TIMES.search(b):
        return DECLINED_TIME
    if _CANT_GET_THERE.search(b):
        return CANT_GET_THERE
    if _BARE_NO.search(b):
        if times_on_table:
            return DECLINED_TIME
        if _INVITED_IN.search(last_outbound or ""):
            return DECLINED_VENUE
        return UNCLEAR_NO
    return None


def _location_settings(db, lead) -> Dict:
    try:
        from app.services.programs import identity
        prof = identity.location_profile_for_lead(db, lead)
        bs = getattr(prof, "brand_settings", None) if prof is not None else None
        if isinstance(bs, str):
            bs = json.loads(bs or "{}")
        return bs if isinstance(bs, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def alternative_for(db, lead, conv, declined: Optional[str]) -> str:
    """The ONE alternative this location really offers, in order: a visit at
    the family's home (only when the location's own setting "home_visits" is
    on - the printed-guide delivery flag is NOT a home-appointment
    permission), a phone appointment (setting "offer_phone_alternative"), the
    planning guide if they don't have it yet, else none."""
    bs = _location_settings(db, lead)
    if bs.get("home_visits") and declined != DECLINED_HOME:
        return ALT_HOME
    if bs.get("offer_phone_alternative"):
        return ALT_PHONE
    if conv is not None and not _guide_sent(db, conv):
        return ALT_GUIDE
    return ALT_NONE


def decline_context(db, lead, conv, body: str) -> Dict:
    """Code-made facts about a decline, for the prompt and the validator."""
    mem = memory_of(conv) if conv is not None else {}
    declined = what_was_declined(body, _last_outbound(db, lead), bool(conv is not None and A.offered(conv)))
    already = mem.get("alternative_offered")
    alt = alternative_for(db, lead, conv, declined) if declined in (DECLINED_VENUE, CANT_GET_THERE, DECLINED_HOME) \
        else ALT_NONE
    home_ok = bool(_location_settings(db, lead).get("home_visits"))
    b = _plain(body)
    blocked = declined in (DECLINED_HOME, DECLINED_STOP)
    asks_home = bool(_WANTS_HOME.search(b)) and not blocked
    # A plain yes to OUR home-visit offer - not "ok, but I'd rather come in".
    accepted_home = (already == ALT_HOME and not blocked
                     and bool(re.search(r"^\s*(yes|yeah|yep|sure|ok(ay)?|that would|please|that works|sounds good|"
                                        r"definitely|absolutely)\b", b, re.I))
                     and not re.search(r"\b(but|rather|instead|come in|come there|stop by there|not)\b", b, re.I))
    return {"what_was_declined": declined, "alternative_to_offer": ALT_NONE if already else alt,
            "alternative_already_offered": already or None,
            "home_visits_offered_by_this_location": home_ok,
            "family_asks_for_home_visit": asks_home or accepted_home}


def validate_decision(d: Dict, body: str, conv, contact: Optional[str] = None) -> List[str]:
    """Rules the model's answer must meet before anything is sent. Returned
    to the model once as a correction; if they still fail, a person decides."""
    problems: List[str] = []
    action = d.get("action")
    reply = (d.get("reply") or "").strip()
    if action not in ACTIONS:
        return ["action must be exactly one of: %s" % ", ".join(ACTIONS)]
    if action not in ("wait", "book") and not reply:
        problems.append("the reply is empty")
    if _question_count(reply) > 1:
        problems.append("it asks more than one question - ask exactly one")
    if action not in ("book", "callback", "follow_up_later") and _CLOCK.search(_strip_offered(reply, conv)):
        problems.append("it writes a clock time itself - use {TIMES} so the system offers real open times")
    if action == "offer_times" and "{TIMES}" not in reply and not A.offered(conv):
        problems.append("action offer_times needs the {TIMES} placeholder in the reply (and no other question)")
    if action != "book" and _CLAIM_BOOKED.search(reply):
        problems.append("it says an appointment is set or confirmed - only the system confirms bookings")
    if _CLAIM_MAILED.search(reply):
        problems.append("it says something was mailed - a person mails it; say it will be arranged")
    if action == "close" and _EXISTING_PLAN.search(body or "") and not _ELSEWHERE.search(body or ""):
        problems.append("they said they already have plots / a plan but not WHERE - it may be with us. Don't "
                        "close: thank them and ask warmly whether it's with us, or offer a review of their file")
    if existing_plan_purchased_with_us(body):
        # "We bought a plan from you" is an existing-customer statement,
        # not permission to invent an appointment or a new sales opportunity.
        if action in ("offer_times", "book") and not _REQUESTS_EXISTING_PLAN_VISIT.search(body or ""):
            problems.append("EXISTING CUSTOMER: they already purchased a plan FROM US but did not "
                            "request an appointment. Use answer: acknowledge their existing "
                            "arrangements and ask whether they need help with anything.")
        if action == "close":
            problems.append("EXISTING CUSTOMER: a previous purchase is not a refusal. "
                            "Acknowledge their plan instead of closing them as uninterested.")
        if action != "wait" and not re.search(
                r"\b(plan|arrangements?|contract|plots?|property|purchas(?:e|ed)|bought|prepaid|pre-?need)\b",
                reply, re.I):
            problems.append("EXISTING CUSTOMER: the reply must acknowledge the existing "
                            "plan or arrangements; generic scheduling filler is not an answer.")
    if (action == "mail_guide" and (body or "").strip() and not _PRINTED.search(body or "")   # not a follow-up
            and not (memory_of(conv) or {}).get("mail_request")):
        problems.append("they asked for the guide, not a printed or mailed copy - use send_guide (the system "
                        "texts or emails it, or offers a printed copy if a link can't go by text)")
    dc = getattr(conv, "_decline", None) or {}
    declined, alt = dc.get("what_was_declined"), dc.get("alternative_to_offer") or ALT_NONE
    if declined == DECLINED_STOP and (action in ("offer_times", "book", "send_guide", "mail_guide", "callback")
                                      or "{TIMES}" in reply or _OFFERS_HOME.search(reply)):
        problems.append("they said stop / not interested - honour it: thank them briefly and close, with no "
                        "alternative, no times and no offer")
    if action == "close" and declined in (DECLINED_VENUE, CANT_GET_THERE) and alt != ALT_NONE:
        problems.append("their \"no\" answered our invitation to COME IN - it is not a no to every kind of help. "
                        "Don't close yet: offer the ONE alternative in facts (%s), once, warmly, as your one "
                        "question" % alt)
    if action == "close" and declined == DECLINED_TIME:
        problems.append("they declined the times, not the visit - ask what week or time of day suits them "
                        "(or follow_up_later), without repeating the same days")
    if _OFFERS_HOME.search(reply) and (alt != ALT_HOME or declined == DECLINED_HOME) \
            and not dc.get("home_visits_offered_by_this_location"):
        problems.append("it offers to come to the family's home - this location does not offer home visits; "
                        "never promise one")
    if declined == DECLINED_HOME and _OFFERS_HOME.search(reply):
        problems.append("they said they don't want anyone coming to their home - never offer that")
    if dc.get("family_asks_for_home_visit") and dc.get("home_visits_offered_by_this_location") \
            and action in ("book", "offer_times", "close"):
        problems.append("they want someone to come to them and this location does home visits: say yes warmly and "
                        "use handoff (I'll reach out personally to find a time) - never offer or book a visit at the "
                        "location, never close")
    if action == "book" and dc.get("alternative_already_offered") == ALT_HOME:
        problems.append("a home visit is arranged personally by the advisor, never booked as a visit at the "
                        "location - use handoff so I confirm the time myself")
    if action == "close" and not _DECLINE.search(body or ""):
        problems.append("they did not decline - a short or unclear reply ('k', 'maybe', an emoji) is not a no; "
                        "answer it or ask one gentle question instead of closing")
    if action == "send_guide" and _ASKS_IF_WANTS_GUIDE.search(reply):
        problems.append("it sends the guide AND asks whether they want it - either send it (say it's attached) "
                        "or ask first with action answer")
    if action != "send_guide" and _CLAIM_GUIDE.search(reply):
        problems.append("it presents the guide without action send_guide")
    if re.search(r"https?://", reply):
        problems.append("it contains a link - the system adds approved links itself")
    for pat, why in _BANNED:
        if re.search(pat, reply.lower()):
            problems.append(why)
    if _ASKS_IF_HUMAN.search(body or "") and not re.search(r"\b(assistant|automated|automation|\bai\b)", reply, re.I):
        problems.append("they asked whether this is a real person or automated - say plainly that an automated "
                        "assistant helps me with messages and scheduling, and that I see every reply")
    if (action == "mail_guide" and not _PERSONAL_STEP.search(reply)
            and not re.search(r"\baddress\b", reply, re.I)     # still collecting where to mail it
            and not _MAIL_ONLY.search(body or "") and not _DECLINE.search(body or "")):
        problems.append("a printed-guide request is an opening for a personal meeting - keep honouring the mail "
                        "request and add ONE soft sentence that I'd be glad to sit down and go over it with them "
                        "(or, if personal delivery is available, offer to bring it by); no new times")
    if contact and third_person(reply, contact):
        problems.append("it talks about %s in the third person - the message is signed by %s, so write in "
                        "first person (\"I'd be glad to...\")" % (contact, contact))
    last = _last_ai_text(conv)
    if last and reply and _norm(reply) == _norm(last):
        problems.append("it repeats our last message word for word - respond to what they just said")
    return problems


_PRINTED = re.compile(r"(print|paper|hard ?copy|\bmail|post office|address|to my (house|home)|"
                      r"\b\d{2,6}\s+\w+.*\b(rd|road|st|street|ave|avenue|dr|drive|ln|lane|blvd|way|ct|hwy|highway)\b)", re.I)
_ELSEWHERE = re.compile(r"\b(another|other|different|elsewhere|somewhere else)\b", re.I)
_PERSONAL_STEP = re.compile(r"(sit down|go over (it|this|that|the guide)? ?(with you|together)|in person|stop by|"
                            r"bring (it|the guide|that)|come (in|by)|visit)", re.I)
_MAIL_ONLY = re.compile(r"\b((just|only) (mail|send)|mail (it )?only|don'?t (want|need) (to )?(meet|visit|come|talk)|"
                        r"no (visit|meeting|calls?)|not (looking|wanting) to meet)", re.I)
_ASKS_IF_HUMAN = re.compile(r"\b(robot|a bot|bot\b|\bai\b|automated|auto.?reply|real person|a real human|"
                            r"are you (a )?(human|person|real|computer|machine)|is this (a )?(person|human|real|computer))", re.I)
_PLAN_PURCHASE_WITH_US = re.compile(
    r"\b(?:bought|purchased?|paid for|signed up for)\b.{0,85}?"
    r"\b(?:plan|arrangements?|contract|plots?|spaces?|cemetery property)\b.{0,65}?"
    r"\b(?:from|with|through)\s+(?:you|your|this (?:funeral home|cemetery|location))\b", re.I)
_REQUESTS_EXISTING_PLAN_VISIT = re.compile(
    r"\b(?:can|could|would|may) (?:i|we)\b.{0,60}\b(?:meet|come in|visit|review|schedule|book)|"
    r"\b(?:want|need|like|looking) to\b.{0,50}\b(?:meet|review|schedule|book|come in)|"
    r"\b(?:appointment|schedule (?:a|an) (?:meeting|review|visit))\b", re.I)


def existing_plan_purchased_with_us(body: str) -> bool:
    """Family's claim about an existing plan, not a verified contract status."""
    text = (body or "").strip()
    if not _PLAN_PURCHASE_WITH_US.search(text):
        return False
    if re.search(r"\b(?:never|didn'?t|did not|haven'?t|have not)\s+"
                 r"(?:already\s+)?(?:buy|bought|purchase|purchased|pay|paid|have)\b",
                 text, re.I):
        return False
    return True


_EXISTING_PLAN = re.compile(r"\b(already (have|got|bought|took care)|have a plan|prearrang|bought (plots?|"
                            r"spaces?|property))", re.I)
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")
_ASKS_IF_WANTS_GUIDE = re.compile(r"would you like (me to send |to receive |to get |to have )?(you )?(the |our |a )?"
                                  r"(copy of (the |our )?)?(planning )?guide", re.I)
_DECLINE = re.compile(r"\b((have|has|got) (a|our) plan (with|at) (another|a different|other)|another funeral home|"
                      r"(plan|arrangements|plot|plots) (elsewhere|somewhere else)|not interested|no thanks|no thank you|all set|we'?re (all )?set|already (have|took care|"
                      r"handled|done)|(is|are|been|already) (all )?taken care of|already (made|has|have) (arrangements|plans)|don'?t (want|need)|not (right )?now|not at this time|no longer|pass\b|"
                      r"changed my mind|leave me alone|not for me|nope|no$)", re.I)


def _strip_offered(reply: str, conv) -> str:
    """Times we actually offered may be repeated back ("Thursday at 9 AM or
    Friday at 1 PM?"); only times the model invented count."""
    out = reply.replace("{TIMES}", "")
    for s in (A.offered(conv) if conv is not None else []):
        for form in (A.describe(s), s.strftime("%-I:%M %p"), s.strftime("%-I %p"), s.strftime("%-I%p").lower(),
                     s.strftime("%-I %p").lower()):
            out = out.replace(form, "")
    return out


def _one_question(text: str) -> str:
    """Keep every statement and only the LAST question."""
    parts = [p.strip() for p in _SENTENCE.findall(text or "") if p.strip()]
    qs = [i for i, p in enumerate(parts) if p.endswith("?")]
    if len(qs) <= 1:
        return text
    keep = [p for i, p in enumerate(parts) if not p.endswith("?") or i == qs[-1]]
    return " ".join(keep)


def _ensure_times_question(text: str) -> str:
    """An offer always ends in ONE question that carries {TIMES}."""
    if "{TIMES}" in text or "{{TIMES}}" in text:
        return _one_question(text.replace("{{TIMES}}", "{TIMES}"))
    parts = [p.strip() for p in _SENTENCE.findall(text or "") if p.strip() and not p.strip().endswith("?")]
    return (" ".join(parts) + " Would {TIMES} work for you?").strip()


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def _last_ai_text(conv) -> Optional[str]:
    from sqlalchemy.orm import object_session
    db = object_session(conv)
    if db is None or conv.id is None:
        return None
    t = (db.query(ProgramConversationTurn)
         .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                 ProgramConversationTurn.delivery.in_(("sent", "simulated")))
         .order_by(ProgramConversationTurn.created_at.desc()).first())
    return t.text if t else None


def _over_limit(db, conv, now) -> Optional[str]:
    """Operational limits that replace the old eight-turn cut-off."""
    delivered = (db.query(ProgramConversationTurn)
                 .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                         ProgramConversationTurn.delivery.in_(("sent", "simulated", "held"))))
    total = delivered.count()
    if total >= MAX_AI_TOTAL:
        return "very long conversation (%d AI messages) - a person should take it from here" % total
    day_start = A._today_central().replace(hour=0, minute=0, second=0, microsecond=0)
    from zoneinfo import ZoneInfo
    day_start_utc = day_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    today = delivered.filter(ProgramConversationTurn.created_at >= day_start_utc).count()
    if today >= MAX_AI_PER_DAY:
        return "%d AI messages to this family today - a person should take it from here" % today
    return None


def _parse_follow_up(raw, now) -> datetime:
    """The family's own horizon, clamped to 1-90 days; 14 days if unclear.
    A date further out than 90 days is brought in to 90 (#14) - "next year"
    must not become two weeks. Returned as UTC at 15:00 (10 AM Central)."""
    try:
        day = datetime.strptime(str(raw or "")[:10], "%Y-%m-%d")
    except ValueError:
        day = None
    today = A._today_central().date()
    if day is not None and (day.date() - today).days > 90:
        day = datetime.combine(today + timedelta(days=90), datetime.min.time())
    if day is None or not (1 <= (day.date() - today).days <= 90):
        day = datetime.combine(today + timedelta(days=14), datetime.min.time())
    return day.replace(hour=15, minute=0)


def _schedule_follow_up(conv, action, now) -> None:
    if (conv.followups_sent or 0) >= MAX_FOLLOWUPS:
        return
    if conv.state == "offered":
        conv.next_followup_at = now + OFFERED_FOLLOWUP
        conv.followup_reason = "two times were offered and they have not answered"
    elif action == "send_guide" and conv.state in ("ai_active", "info_requested"):
        conv.state = "info_requested"
        conv.next_followup_at = now + INFO_FOLLOWUP
        conv.followup_reason = "the planning guide was sent; see if they have questions or would like to talk"


def _mail_task(db, prog, lead, conv) -> None:
    """A person must mail the printed guide: an in-app task for the team (not
    a page) and a line the family record keeps until it is marked done."""
    try:
        from app.models.program_models import ProgramAlert
        if _synthetic(lead):
            return
        who = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A family"
        db.add(ProgramAlert(organization_id=prog.organization_id, kind="task", audience="team", channel="in_app",
                            delivered=True, reason="mail_guide",
                            message="%s asked for a printed planning guide by mail. Confirm the address and "
                                    "mail it, then mark it done." % who))
    except Exception:                                     # noqa: BLE001
        log.exception("could not record the mail task for lead %s", lead.id)


import contextvars as _cv
# Which capability the setter calls. Normally "sci_appointment_setter"; the
# owner's evaluation run can benchmark "sci_appointment_setter_mini".
_CAPABILITY = _cv.ContextVar("sci_setter_capability", default="sci_appointment_setter")


def _get_client():                                       # test seam (see ai_gateway.chat_completion)
    return None


def _question_count(text: str) -> int:
    return (text or "").count("?")


_ORD = {0: r"\b(first|1st|earlier|former|either|whichever|option (1|one)|#1)\b",
        1: r"\b(second|2nd|later|latter|option (2|two)|#2)\b"}


# A turn-down attached to the day/hour itself ("Monday doesn't work", "10 is
# bad"), not one later in the sentence ("Monday works but not Tuesday") and
# not a reassurance ("not a problem", "not bad", "can't wait").
_NEG_AFTER = re.compile(r"^\s*(at \S+\s*|is\s+|would\s+|will\s+|just\s+|really\s+)*"
                        r"(doesn'?t|does not|won'?t|will not|isn'?t|is not|not|can'?t|cannot|no good|bad|busy)\b"
                        r"(?!\s+(a |an |be a |too )?(problem|issue|bad|wait))")
_NEG_BEFORE = re.compile(r"\b(not|no|can'?t|cannot|won'?t|couldn'?t|except)\b(?:(?!\b(but|and|or)\b)[^.,;!?]){0,12}$")


def _picked(pattern: str, t: str) -> bool:
    """The pattern appears in a clause that is not turning it down
    ("Monday doesn't work", "not Monday", "can't do 10")."""
    for m in re.finditer(pattern, t):
        if _NEG_AFTER.search(t[m.end():]) or _NEG_BEFORE.search(t[:m.start()]):
            continue
        return True
    return False


def clock_named(body: str, slot: datetime) -> bool:
    """The family's words name this slot's clock hour ("2", "2:00 pm", "noon")."""
    t = (body or "").lower()
    hour = int(slot.strftime("%I"))
    if slot.hour == 12 and _picked(r"\bnoon\b", t):
        return True
    return _picked(r"\b%d(:%02d)?\s*(am|pm|a\.m\.|p\.m\.|o'?clock)?\b" % (hour, slot.minute), t) if slot.minute \
        else _picked(r"\b%d(:00)?\s*(am|pm|a\.m\.|p\.m\.|o'?clock)?\b" % hour, t)


_SAYS_MORNING = r"(?<!good )\b(morning|mornings)\b|\b(la|las) ma[ñn]anas?\b"
_SAYS_LATER = r"(?<!good )\b(afternoon|afternoons|evening|evenings)\b|(?<!buenas )\btarde\b"


def _part_contradicts(t: str, slot: datetime) -> bool:
    """The family named ONE part of the day and this slot is in the other."""
    am, pm = _picked(_SAYS_MORNING, t), _picked(_SAYS_LATER, t)
    if am and not pm:
        return slot.hour >= 12
    if pm and not am:
        return slot.hour < 12
    return False


def choice_evidence(body: str, slot: datetime, index: Optional[int]) -> bool:
    """Did the family actually pick THIS time? Their words must name its day,
    date, hour or position - an AI's guess never books an appointment, and a
    time they turned down ("Monday doesn't work") is never a pick."""
    t = (body or "").lower()
    if not t:
        return False
    if _part_contradicts(t, slot) and not clock_named(body, slot):
        return False                                    # "Friday morning" never picks Friday 2 PM (#12)
    if _picked(r"\b%s\b" % slot.strftime("%A").lower(), t) or _picked(r"\b%s\b" % slot.strftime("%a").lower(), t):
        return True
    if _picked(r"\b%s\b" % slot.strftime("%B").lower(), t) and str(slot.day) in t:
        return True
    if clock_named(body, slot):
        return True
    if index is not None and index in _ORD and _picked(_ORD[index], t):
        return True
    if any(_picked(r"\b(%s|%s)\b" % (d, d[:3]), t) for d in _DAYS):
        return False                                    # they named a different day: a day part alone isn't a pick
    if _picked(r"\bmorning\b", t) and slot.hour < 12:
        return True
    if _picked(r"\b(afternoon|evening)\b", t) and slot.hour >= 12:
        return True
    return False


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


_WANTS_PHONE = re.compile(r"\b(phone call|by phone|over the phone|call me|a call\b|phone instead)", re.I)


def day_part(body: str) -> Optional[str]:
    """'morning' / 'afternoon' when the family said which part of the day suits them."""
    low = (body or "").lower()
    if re.search(r"\b(morning|mornings|early|before (noon|lunch)|\bam\b)", low):
        return "morning"
    if re.search(r"\b(afternoon|afternoons|after (noon|lunch)|later in the day|\bpm\b|evening)", low):
        return "afternoon"
    return None


def day_named(body: str, today) -> Optional[object]:
    """The next date for a weekday the family named ("Can you do Friday?")."""
    t = (body or "").lower()
    for i, name in enumerate(_DAYS):
        if re.search(r"\b(%s|%s)\b" % (name, name[:3]), t):
            ahead = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    return None


def decide(db, prog, lead, conv, resp, *, actor: Optional[str] = None, note: Optional[str] = None,
           follow_up: Optional[str] = None) -> Dict:
    """The AI's one next step. Raises on any AI failure (caller hands off).
    A family's reply runs it as BACKGROUND AI (the platform switch must be
    on); a person running the simulator runs it as a MANUAL action of theirs."""
    from app.services import ai_gateway
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    from app.services.programs import sci_knowledge
    system = SYSTEM_PROMPT.format(contact=(prog.primary_contact_name or "our representative"),
                                  location=(prof.official_name if prof else "our family services team"),
                                  knowledge=sci_knowledge.as_prompt_block())
    ctx = _context(db, prog, lead, conv, resp, follow_up=follow_up)
    if note:
        ctx["correction"] = note
    from contextlib import nullcontext
    # A family's reply runs as BACKGROUND AI. It no longer needs the platform-
    # wide background switch: the gateway grants THIS feature for THIS
    # organization only, and only while the program's own mode is draft/auto
    # and the SCI emergency switch is not thrown. Every cap still applies.
    from app.services.test_records import is_owner_test_lead
    live = mode_of(prog) in (DRAFT, AUTO) or (is_owner_test_lead(lead) and not killed())
    grant = (ai_gateway.scoped_grant("sci_appointment_setter", prog.organization_id)
             if (not actor and live) else nullcontext())
    with grant:
        r = ai_gateway.chat_completion(
            feature="sci_appointment_setter", capability=_CAPABILITY.get(),
            mode=ai_gateway.MANUAL if actor else ai_gateway.BACKGROUND, actor=actor,
            org_id=prog.organization_id, client=_get_client(),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": json.dumps(ctx, default=str)}],
            temperature=0.4, response_format={"type": "json_object"})
    raw = r.choices[0].message.content
    d = json.loads(raw)
    if d.get("action") not in ACTIONS:
        raise ValueError("unknown action %r" % d.get("action"))
    return d


# ── delivery ─────────────────────────────────────────────────────────────────

def _synthetic(lead) -> bool:
    """A test contact or a made-up staging demo family: nothing is ever sent.
    The owner's live test contact is the one exception - it goes through the
    real send path, which itself checks it is the owner's own number."""
    from app.services.sci_staging_demo import is_demo_lead
    from app.services.test_records import OWNER_TEST_LAST_NAME
    if getattr(lead, "is_test", False) and getattr(lead, "last_name", None) == OWNER_TEST_LAST_NAME:
        return False
    return bool(getattr(lead, "is_test", False)) or is_demo_lead(lead)


def _answered(resp, now) -> None:
    """The AI's reply went out: the family's message is answered (SCI Live
    stops listing it under Needs reply). Email marks it in send_reply."""
    if resp is not None and (resp.handling_status or "new") in ("new", "opened"):
        resp.handling_status = "responded"
        resp.responded_at = resp.responded_at or now


def _deliver(db, prog, lead, conv, resp, text, action, mode, *, now) -> ProgramConversationTurn:
    from app.services import contact_hours, send_source as ss
    is_email = resp is not None and resp.channel == "email"
    if _synthetic(lead) or (not lead.phone and not is_email):
        return _turn(db, conv, "ai", text, action=action, delivery="simulated",
                     response_id=resp.id if resp else None, reason="synthetic contact - nothing sent")
    if mode == DRAFT:
        if resp is not None:
            resp.suggested_reply = text
        return _turn(db, conv, "ai", text, action=action, delivery="draft",
                     response_id=resp.id if resp else None, reason="draft mode - a person sends it")
    if is_email:
        # AUTO, and this conversation is the AI's: answer the email in its own
        # thread, to the person who wrote it, from the mailbox it came to.
        from app.services.programs import email_reply
        res = email_reply.send_reply(db, prog, lead, resp, text, send_source=ss.SCI_SETTER,
                                     idempotency_key="ai:%s:%s" % (resp.id, action))
        if res.get("ok"):
            return _turn(db, conv, "ai", text, action=action, delivery="sent",
                         response_id=resp.id, reason="email reply in thread to %s"
                         % ((res.get("email") or {}).get("to") or "the sender"))
        resp.suggested_reply = text
        _handoff(db, prog, lead, conv, "the email reply could not be sent: %s" % (res.get("error") or "")[:160],
                 notify=True)
        return _turn(db, conv, "ai", text, action=action, delivery="blocked",
                     reason=(res.get("error") or "email not sent")[:240], response_id=resp.id)
    hours = contact_hours.check(lead, now)
    if not hours.get("permitted"):
        # The held reply itself is released by sweep(). next_followup_at only
        # moves when nothing is set or it is earlier than the next allowed
        # hour - an agreed later date (follow_up_later: "check back in 35
        # days") is never pulled forward (#4).
        nxt = contact_hours.next_permitted_utc(lead, now)
        if conv.next_followup_at is None or (nxt is not None and conv.next_followup_at < nxt):
            conv.next_followup_at = nxt
        return _turn(db, conv, "ai", text, action=action, delivery="held",
                     response_id=resp.id if resp else None, reason="outside contact hours")
    try:
        from app.services.sms_service import send_sms
        advisor = A.program_advisor(db, prog)
        msg = send_sms(db, advisor, lead, text, include_booking_link=False, send_source=ss.SCI_SETTER)
        _answered(resp, now)
        return _turn(db, conv, "ai", text, action=action, delivery="sent", message_id=getattr(msg, "id", None),
                     response_id=resp.id if resp else None)
    except Exception as exc:                             # noqa: BLE001 - a refusal is recorded, never retried blind
        db.rollback()
        conv = db.merge(conv)
        _handoff(db, prog, lead, conv, "the text was refused: %s" % str(exc)[:160], notify=True)
        return _turn(db, conv, "ai", text, action=action, delivery="blocked", reason=str(exc)[:240],
                     response_id=resp.id if resp else None)


def _handoff(db, prog, lead, conv, reason, *, notify: bool):
    conv.state = "human"
    conv.handoff_reason = reason[:250]
    if getattr(lead, "is_test", False):
        notify = False                  # a synthetic contact never pages the team
    if notify:
        try:
            from app.services.programs import alert_ledger as L
            # ONE REPLY, ONE ALERT (Oct 19): during a family's reply the
            # hand-off is folded into that reply's single page.
            if L.defer({"type": "handoff", "reason": (reason or "")[:250]}):
                return
            from app.services.programs import responses as R
            import html as _html
            who = L.first_last(lead)
            home = reason == HOME_VISIT_REASON
            sms = L.sms_line("home_visit" if home else "handoff", who,
                             detail=("asked for a visit at home - call to set a time" if home
                                     else "needs a person - %s" % (reason or "")),
                             link=L.family_link(lead.id))
            subject = "%s - %s needs a person" % (R.alert_title(prog), who)
            body = "<p>%s needs a person: %s</p>" % (_html.escape(who), _html.escape(reason or ""))
            L.page(db, prog, lead_id=lead.id, key="handoff:%s:%s" % (conv.id, datetime.utcnow().strftime("%Y%m%d%H%M")),
                   kind="handoff", sms_text=sms, subject=subject, html=body, management=True,
                   audit_message="%s needs a person: %s" % (who, reason))
        except Exception:                                # noqa: BLE001
            log.exception("handoff alert failed for lead %s", lead.id)


def _fill_times(text: str, slots: List[datetime]) -> str:
    if len(slots) >= 2:
        times = "%s or %s" % (A.describe(slots[0]), A.describe(slots[1]))
    else:
        times = A.describe(slots[0])
    return text.replace("{TIMES}", times).replace("{{TIMES}}", times)


# ── the entry point ──────────────────────────────────────────────────────────

def on_family_reply(db: Session, prog, lead, resp, body: str, *, now: Optional[datetime] = None,
                    force_mode: Optional[str] = None, actor: Optional[str] = None) -> Optional[Dict]:
    """Answer one family reply. Returns what happened, or None when the setter
    is off for this program."""
    from app.services.programs.reply_rules import AUTO_REPLY, BAD_DATA, OPT_OUT, WRONG_PERSON
    mode = force_mode or mode_of(prog)
    if mode == OFF:
        return None
    now = now or datetime.utcnow()
    conv = conversation_for(db, lead)
    conv.last_family_at = now
    _turn(db, conv, "family", body, response_id=resp.id if resp else None)
    cls = resp.response_class if resp is not None else None
    out = {"conversation_id": conv.id, "mode": mode}

    if cls == OPT_OUT:
        conv.state = "stopped"
        _turn(db, conv, "system", None, action="stop", reason="opted out - no further messages")
        db.commit()
        return dict(out, action="stop", state=conv.state)
    if cls in (BAD_DATA, WRONG_PERSON):
        _handoff(db, prog, lead, conv, "wrong person / wrong number - check the record", notify=False)
        db.commit()
        return dict(out, action="handoff", state=conv.state)
    if cls == AUTO_REPLY:
        # Only a conversation the AI is running moves to "waiting" (#5). A
        # person's conversation (human / callback), offered times, a booking,
        # an agreed later date or a stop are left exactly as they are.
        if conv.state == "ai_active":
            conv.state = "waiting"
            conv.next_followup_at = now + FOLLOWUP_GAP
            why = "automatic reply - not answered; follow-up after %s UTC" % conv.next_followup_at.strftime(
                "%Y-%m-%d %H:%M")
        else:
            why = "automatic reply - not answered; conversation left as it was (%s)" % conv.state
        _turn(db, conv, "system", None, action="wait", reason=why)
        db.commit()
        return dict(out, action="wait", state=conv.state)
    if conv.state in ("human", "stopped"):
        _turn(db, conv, "system", None, action="none", reason="a person has this conversation")
        db.commit()
        return dict(out, action="none", state=conv.state)
    if _human_took_over(db, lead, conv):
        _handoff(db, prog, lead, conv, "a person is texting this family", notify=False)
        db.commit()
        return dict(out, action="none", state=conv.state)
    limit = _over_limit(db, conv, now)
    if limit:
        _handoff(db, prog, lead, conv, limit, notify=True)
        db.commit()
        return dict(out, action="handoff", state=conv.state)
    if conv.state in ("waiting_future", "closed", "info_requested", "offered", "waiting"):
        # The family wrote again: any scheduled follow-up is superseded.
        conv.next_followup_at = None
        conv.followup_reason = None

    # What a "No" declined is decided in code from the last message the family
    # saw (#234); the prompt and the validator both read it.
    try:
        conv._decline = decline_context(db, lead, conv, body)
    except Exception:                                    # noqa: BLE001 - never blocks a reply
        log.exception("decline context failed for lead %s", lead.id)
        conv._decline = None
    out["decline"] = conv._decline
    try:
        try:
            d = decide(db, prog, lead, conv, resp, actor=actor)
        except (ValueError, KeyError, TypeError):
            # A malformed answer gets ONE correction; a second failure hands off.
            d = decide(db, prog, lead, conv, resp, actor=actor,
                       note="Your last answer was not valid. Return the JSON with action set to exactly one of: "
                            + ", ".join(ACTIONS) + ".")
        if d.get("action") == "callback" and not (d.get("reply") or "").strip():
            d["reply"] = "Thank you - I'll give you a call then."
        problems = validate_decision(d, body, conv, prog.primary_contact_name)
        if problems:
            # ONE chance to fix it, told exactly what was wrong.
            d = decide(db, prog, lead, conv, resp, actor=actor,
                       note="Your last answer broke these rules: %s. Fix them and return the full JSON again."
                            % "; ".join(problems))
            out["corrected"] = problems
    except Exception as exc:                             # noqa: BLE001
        log.warning("setter AI unavailable for lead %s: %s", lead.id, exc)
        _handoff(db, prog, lead, conv, "AI unavailable (%s) - a person answers" % type(exc).__name__, notify=False)
        _turn(db, conv, "system", None, action="handoff", reason="AI unavailable: %s" % str(exc)[:200])
        db.commit()
        return dict(out, action="handoff", state=conv.state, error=type(exc).__name__)

    action = d["action"]
    text = (d.get("reply") or "").strip()
    und = d.get("understanding") if isinstance(d.get("understanding"), dict) else {}
    out.update(intent=d.get("intent") or und.get("meaning"), action=action, reason=d.get("reason"),
               objective=d.get("objective"), mood=und.get("mood"))
    merge_memory(conv, d.get("memory_update"))
    left = validate_decision(d, body, conv, prog.primary_contact_name)
    if left and all("more than one question" in p for p in left) and text:
        text = _one_question(text)                       # deterministic, safe trim
        d["reply"] = text
        left = validate_decision(d, body, conv, prog.primary_contact_name)
    try:
        confidence = float(d.get("confidence")) if d.get("confidence") not in (None, "") else 1.0
    except (TypeError, ValueError):
        confidence = 1.0
    if not 0.0 <= confidence <= 1.0:
        confidence = max(0.0, min(1.0, confidence / 100.0 if confidence > 1 else confidence))
    out["confidence"] = confidence
    if left or (confidence < LOW_CONFIDENCE and action not in ("wait", "handoff")):
        why = ("unsure (confidence %.2f)" % confidence) if not left else "; ".join(left)
        if resp is not None and text:
            resp.suggested_reply = text
        _handoff(db, prog, lead, conv, "AI reply needs a person: %s" % why, notify=True)
        _turn(db, conv, "ai", text or None, action=action, delivery="blocked", reason=why[:240],
              response_id=resp.id if resp else None)
        db.commit()
        return dict(out, action="handoff", state=conv.state, held=left or [why])
    _track_alternative(conv, getattr(conv, "_decline", None), action)   # only for a reply that goes out
    if d.get("appointment_kind") in (A.PHONE, A.VISIT):
        conv.appointment_kind = d["appointment_kind"]
    if d.get("service_type"):
        conv.service_type = str(d["service_type"])[:60]
    allowed_links: List[str] = []

    if action == "wait":
        _turn(db, conv, "system", None, action="wait", reason=d.get("reason") or "no answer needed")
        db.commit()
        return dict(out, state=conv.state)

    preferred_day = None
    if action == "book":
        slot = None
        choices = A.offered(conv)
        k = int(d.get("slot_choice") or 0)
        if 1 <= k <= len(choices):
            slot = choices[k - 1]
            if not choice_evidence(body, slot, k - 1):
                slot = None                             # the family did not pick a time
        elif d.get("requested_time"):
            try:
                slot = datetime.fromisoformat(str(d["requested_time"])[:16])
            except ValueError:
                slot = None
            if slot is not None:
                preferred_day = slot.date()
                # A day without a clock time ("Can you do Friday?") is a
                # request for times THAT day, not a booking.
                # A day part ("Thursday morning?") is a request for times
                # too: only the family's own clock hour books an unoffered time.
                if not clock_named(body, slot) or not choice_evidence(body, slot, None):
                    slot = None
                    action = "offer_times"
                    text = "Yes - would {TIMES} work for you?"
        if slot is None and choices and action == "book":
            # Their words may still pick exactly one offered time ("the afternoon one").
            picked = [c for i, c in enumerate(choices) if choice_evidence(body, c, i)]
            if len(picked) == 1:
                slot = picked[0]
        if slot is None and action == "offer_times":
            pass
        elif slot is None and choices and _WANTS_PHONE.search(body or "") and (conv.appointment_kind or A.VISIT) != A.PHONE:
            # "ok let's do a phone call" while in-person times are on the table:
            # switch to phone times, don't re-ask about the visit times (s094).
            conv.appointment_kind = A.PHONE
            action = "offer_times"
            text = "Of course - would {TIMES} work for a short call?"
        elif slot is None and choices and day_part(body) and not all(
                (c.hour < 12) == (day_part(body) == "morning") for c in choices):
            # "Mornings are better" while an afternoon time is on the table:
            # offer times that fit what they said, not the same two again (s002).
            action = "offer_times"
            text = "Of course - would {TIMES} work for you?"
        elif slot is None and choices:
            # Never book on a guess: ask which of the offered times suits them.
            action = "answer"
            text = ("Thank you. Which works better for you: %s or %s?" % (A.describe(choices[0]), A.describe(choices[1]))
                    if len(choices) >= 2 else "Thank you. Would %s work for you?" % A.describe(choices[0]))
        elif slot is None:
            action = "offer_times"                      # unclear choice: offer again
        else:
            kind = conv.appointment_kind or A.VISIT
            by_email = resp is not None and resp.channel == "email"
            res = A.book(db, prog, lead, slot, kind, family_text=not by_email)
            out["booking"] = res
            if res.get("ok"):
                conv.state = "booked"
                conv.booked_time = slot
                conv.booking_link_id = res.get("booking_id")
                if res.get("simulated"):
                    # Production test contact: the time was checked free on the
                    # live calendar; nothing was written and nobody was paged.
                    _turn(db, conv, "ai", "You're all set for %s. If anything changes, just reply here."
                          % A.describe(slot), action="book", delivery="simulated",
                          reason="SIMULATED booking - time verified free, calendar not written")
                    conv.ai_turns += 1
                    conv.last_ai_at = now
                    db.commit()
                    return dict(out, state=conv.state, booked_time=slot.isoformat(), simulated=True)
                if res.get("already"):
                    # Their time was already booked: reassure them in our own
                    # words; the team was paged when it was booked.
                    t = _deliver(db, prog, lead, conv, resp, "You're already set for %s. If anything changes, "
                                 "just reply here." % A.describe(slot), "book", mode, now=now)
                    conv.ai_turns += 1
                    conv.last_ai_at = now
                    db.commit()
                    return dict(out, state=conv.state, booked_time=slot.isoformat(), delivery=t.delivery)
                A.notify_team_booked(db, prog, lead, slot, kind)
                # The booking path already sent the family its confirmation
                # text; a synthetic contact gets the same line, simulated.
                confirm = "You're all set for %s. If anything changes, just reply here." % A.describe(slot)
                if by_email and not _synthetic(lead):
                    # The confirmation goes back to the person who wrote, in the email
                    # thread - never as a text to the record's phone.
                    t = _deliver(db, prog, lead, conv, resp, "Thank you. " + confirm, "book", mode, now=now)
                    conv.ai_turns += 1
                    conv.last_ai_at = now
                    db.commit()
                    return dict(out, state=conv.state, booked_time=slot.isoformat(), delivery=t.delivery)
                if _synthetic(lead) or not lead.phone:
                    _turn(db, conv, "ai", confirm, action="book", delivery="simulated",
                          reason="booking confirmed by the calendar")
                else:
                    _turn(db, conv, "system", confirm, action="book", delivery="sent",
                          reason="confirmation sent by the booking path")
                conv.ai_turns += 1
                conv.last_ai_at = now
                db.commit()
                return dict(out, state=conv.state, booked_time=slot.isoformat())
            if res.get("status") == "opted_out":
                conv.state = "stopped"
                _turn(db, conv, "system", None, action="stop", reason="opted out - not booked, no further messages")
                db.commit()
                return dict(out, action="stop", state=conv.state)
            if res.get("status") == "already_booked":
                # They already have a visit and want a different time: a person
                # moves it (the setter never reschedules). Never "filled up".
                try:
                    old = A.describe(datetime.fromisoformat(str(res.get("booked_time"))[:16]))
                except (TypeError, ValueError):
                    old = "their current time"
                _handoff(db, prog, lead, conv, "wants to move their appointment from %s to %s"
                         % (old, A.describe(slot)), notify=True)
                text = ("Thank you. You're currently set for %s. I'll have someone reach out "
                        "shortly to move it to %s if that time is still open." % (old, A.describe(slot)))
                action = "handoff"
            elif res.get("status") in ("taken", "unconfirmed"):
                action = "offer_times"
                was_offered = slot in choices
                text = ("That time just filled up. Would {TIMES} work instead?" if was_offered
                        else "I don't have that exact time open. Would {TIMES} work?")
            else:
                _handoff(db, prog, lead, conv, "booking failed: %s" % res.get("reason"), notify=True)
                text = "Thank you. I'll reach out to you shortly to set that up."
                action = "handoff"

    if action == "offer_times":
        slots, why = ([], None)
        if preferred_day is None:
            preferred_day = day_named(body, A._today_central().date())
        if preferred_day is not None:
            slots, why = A.slots_on_day(db, prog, preferred_day, kind=conv.appointment_kind or A.VISIT)
        if not slots and day_part(body):
            slots, why = A.available_slots(db, prog, kind=conv.appointment_kind or A.VISIT, part=day_part(body))
        if not slots:
            slots, why = A.available_slots(db, prog, kind=conv.appointment_kind or A.VISIT)
        if not slots:
            # No calendar slots is an operational block, not a reason to replace
            # the family's contextual reply with a canned appointment sentence.
            # Hand to the advisor; do not send fabricated filler to a customer.
            _handoff(db, prog, lead, conv,
                     "no times to offer: %s; read the family's message before replying"
                     % (why or "calendar unavailable"), notify=True)
            if resp is not None:
                # Inbound triage may have generated a generic suggested_reply.
                # Do not leave a scheduling promise in the advisor's send box
                # when the calendar cannot produce a real appointment.
                resp.suggested_reply = None
            _turn(db, conv, "system", None, action="handoff",
                  reason="calendar unavailable; generic automatic reply suppressed",
                  response_id=resp.id if resp else None)
            db.commit()
            return dict(out, action="handoff", state=conv.state,
                        held=["calendar unavailable: human response required"])
        else:
            text = _ensure_times_question(text)
            text = _fill_times(text, slots)
            conv.offered_slots = json.dumps([s.isoformat() for s in slots])
            conv.offered_at = now
            conv.state = "offered"
            out["offered"] = [s.isoformat() for s in slots]

    if action == "send_guide":
        from app.services import sms_programs
        from app.services.programs import identity
        prof = identity.location_profile_for_lead(db, lead)
        guide = identity.planning_guide_link(prof, db=db) if prof else identity.default_planning_guide()
        by_email = resp is not None and resp.channel == "email"
        allow = by_email or (sms_programs.content_allowance(db, lead).get("allow_links", True)
                             and sms_programs.sms_links_enabled())
        if allow and guide:
            allowed_links.append(guide)
            if guide not in text:
                text = "%s %s" % (text, guide)
        else:
            # No link by text (carriers filter link texts from this line - Twilio
            # 30007 on Oct 15). Never promise an email nothing sends: offer a
            # printed copy by mail; their address makes it a mail task.
            # Get the guide to them by mail (a person sends it) and invite them in
            # to go over it - the visit is the goal. Real times only.
            slots, _why = A.available_slots(db, prog, kind=conv.appointment_kind or A.VISIT)
            if slots:
                text = ("I'd be glad to get the planning guide to you, and I'd be happy to go over it with you "
                        "in person and answer any questions. Would {TIMES} work for a short visit? If you'd rather "
                        "just have a printed copy mailed, reply with your address.")
                text = _fill_times(text, slots)
                conv.offered_slots = json.dumps([s.isoformat() for s in slots])
                conv.offered_at = now
                conv.state = "offered"
                out["offered"] = [s.isoformat() for s in slots]
            else:
                text = ("I'd be glad to get the planning guide to you. Would you like a printed copy mailed to you? "
                        "If so, just reply with your mailing address.")
                conv.state = "info_requested"
            merge_memory(conv, {"commitments": ["guide to be arranged; invited to go over it in person"]})
            out["guide_offered_by_mail"] = True
        out["guide"] = guide

    if action == "mail_guide":
        # A PERSON mails it. The setter records the task and never claims it went.
        mem = merge_memory(conv, {"mail_request": {"what": "printed planning guide"},
                                  "commitments": ["printed planning guide to be mailed by the team"]})
        conv.state = "info_requested"
        conv.followup_reason = "mail the printed planning guide"
        _mail_task(db, prog, lead, conv)
        out["mail_task"] = True

    if action == "follow_up_later":
        when = _parse_follow_up(d.get("follow_up_date"), now)
        conv.state = "waiting_future"
        conv.next_followup_at = when
        conv.followup_reason = (d.get("follow_up_about") or "pick up where we left off")[:250]
        merge_memory(conv, {"commitments": ["we'll check back around %s" % when.strftime("%B %-d")],
                            "readiness": "not_ready"})
        out["follow_up_at"] = when.isoformat()

    if action == "close":
        conv.state = "closed"
        conv.next_followup_at = None
        merge_memory(conv, {"readiness": "closed"})

    if action == "callback":
        conv.state = "callback_requested"
        conv.callback_window = (d.get("callback_window") or "")[:120]
        _handoff_state = conv.state
        _handoff(db, prog, lead, conv, "callback requested: %s" % (conv.callback_window or "no time given"), notify=True)
        conv.state = _handoff_state                     # stays visible as a callback request

    if action == "handoff" and conv.state != "human":
        dc_ = getattr(conv, "_decline", None) or {}
        home = bool(dc_.get("family_asks_for_home_visit") and dc_.get("home_visits_offered_by_this_location"))
        if home:
            merge_memory(conv, {"wants": ["home visit"], "next_step": HOME_VISIT_REASON})
            _m = memory_of(conv)                      # a NEW ask reopens the task even if an earlier one was arranged
            _m["home_visit"] = {"done": False, "requested_at": datetime.utcnow().isoformat() + "Z"}
            conv.memory = json.dumps(_m)
        _handoff(db, prog, lead, conv, HOME_VISIT_REASON if home else (d.get("reason") or "the family wants a person"),
                 notify=True)

    if "{TIMES}" in text and action != "offer_times":
        # Times written into another kind of reply (e.g. guide + a visit
        # offer): fill them from the calendar too, or drop the offer.
        slots, _why = A.available_slots(db, prog, kind=conv.appointment_kind or A.VISIT)
        if slots:
            text = _fill_times(text, slots)
            conv.offered_slots = json.dumps([s.isoformat() for s in slots])
            conv.offered_at = now
            conv.state = "offered"
            out["offered"] = [s.isoformat() for s in slots]
    problems = check_text(text, allowed_links=allowed_links,
                          channel=(resp.channel if resp is not None else "sms") or "sms",
                          contact=prog.primary_contact_name)
    if problems:
        _handoff(db, prog, lead, conv, "AI reply held: %s" % ", ".join(problems), notify=True)
        _turn(db, conv, "ai", text, action=action, delivery="blocked", reason="; ".join(problems),
              response_id=resp.id if resp else None)
        db.commit()
        return dict(out, action="handoff", state=conv.state, held=problems)

    out["action"] = action                               # what was actually done, not only what was proposed
    t = _deliver(db, prog, lead, conv, resp, text, action, mode, now=now)
    conv.ai_turns = (conv.ai_turns or 0) + 1
    conv.last_ai_at = now
    if t.delivery in ("sent", "simulated", "held"):
        _schedule_follow_up(conv, action, now)
    if conv.state == "ai_active" and action == "answer":
        pass
    db.commit()
    return dict(out, state=conv.state, text=text, delivery=t.delivery)


def sweep(db: Session, now: Optional[datetime] = None) -> Dict:
    """Send replies held for contact hours once the family's hours open."""
    from app.services import contact_hours, send_source as ss
    from app.models.models import Lead
    from app.services.programs import identity
    now = now or datetime.utcnow()
    sent = 0
    held = (db.query(ProgramConversationTurn)
            .filter(ProgramConversationTurn.delivery == "held").limit(50).all())
    for t in held:
        lead = db.query(Lead).filter(Lead.id == t.lead_id).first()
        conv = db.query(ProgramConversation).filter(ProgramConversation.id == t.conversation_id).first()
        prog = identity.program_for_org(db, lead.organization_id) if lead else None
        if not (lead and conv and prog) or mode_of(prog) != AUTO or conv.state in ("human", "stopped"):
            t.delivery = "dropped"
            continue
        if not contact_hours.check(lead, now).get("permitted"):
            continue
        from app.services.sci_staging_demo import is_demo_lead
        from app.services.test_records import is_owner_test_lead
        if A._opted_out(db, lead) or is_demo_lead(lead) or is_owner_test_lead(lead):
            t.delivery, t.reason = "dropped", "family opted out / test or demo - not sent"
            db.commit()
            continue
        try:
            from app.services.sms_service import send_sms
            msg = send_sms(db, A.program_advisor(db, prog), lead, t.text, include_booking_link=False,
                           send_source=ss.SCI_SETTER)
            t.delivery, t.message_id = "sent", getattr(msg, "id", None)
            sent += 1
            # The held reply answered the family's message(s) it was written for:
            # SCI Live stops listing them as waiting (the live path does this in _deliver).
            for _r in (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id,
                                                        ProgramResponse.organization_id == lead.organization_id,
                                                        ProgramResponse.handling_status.in_(("new", "opened")),
                                                        ProgramResponse.received_at <= t.created_at).all()):
                _answered(_r, now)
            db.commit()                                  # a later failure must never un-mark this send
        except Exception as exc:                         # noqa: BLE001
            db.rollback()
            t = db.merge(t)
            t.delivery, t.reason = "blocked", str(exc)[:240]
            # The family never got our answer because of a FAILURE: a person
            # picks it up. A deliberate block (campaign off, DNC, review hold,
            # test) is not a failure and pages nobody.
            if not _POLICY_BLOCK.search(str(exc)):
                try:
                    conv = db.merge(conv)
                    _handoff(db, prog, lead, conv, "held reply could not be sent: %s" % str(exc)[:120], notify=True)
                except Exception:                        # noqa: BLE001
                    log.exception("handoff after a refused held reply failed for lead %s", lead.id)
            db.commit()
    db.commit()
    return {"sent": sent}


_POLICY_BLOCK = re.compile(r"(DNC|CAMPAIGN OFF|ON HOLD|DATA REVIEW|DUPLICATE REVIEW|LOCATION REVIEW|opted|"
                           r"suppress|consent|\btest\b|\bdemo\b|PLAN CAPACITY|capacity)", re.I)


FOLLOW_UP_STATES = ("offered", "info_requested", "waiting_future")


def followup_sweep(db: Session, now: Optional[datetime] = None, limit: int = 20) -> Dict:
    """Due follow-ups: a family that has not answered since our last message,
    in a state where one gentle, CONTEXTUAL check-in helps (times offered,
    guide sent, or they asked us to come back later). At most MAX_FOLLOWUPS
    per conversation, inside contact hours, through the spacing backstop, and
    never for a family that opted out, was handed to a person, or wrote back."""
    from app.models.models import Lead
    from app.services.programs import identity
    now = now or datetime.utcnow()
    done = {"sent": 0, "drafted": 0, "skipped": 0}
    due = (db.query(ProgramConversation)
           .filter(ProgramConversation.next_followup_at.isnot(None),
                   ProgramConversation.next_followup_at <= now,
                   ProgramConversation.state.in_(FOLLOW_UP_STATES))
           .order_by(ProgramConversation.next_followup_at).limit(limit).all())
    for conv in due:
        lead = db.query(Lead).filter(Lead.id == conv.lead_id).first()
        prog = identity.program_for_org(db, lead.organization_id) if lead else None
        mode = mode_of(prog) if prog else OFF
        if mode == OFF:
            continue                                     # stays due; nothing happens while off
        from app.services.test_records import is_owner_test_lead as _owner_test
        # The owner's live test contact never gets a follow-up: a test proves
        # one exchange and is then cleaned up; it must not keep texting the owner.
        stop = (lead is None or getattr(lead, "status", None) == "dnc" or _owner_test(lead)
                or (conv.followups_sent or 0) >= MAX_FOLLOWUPS
                or (conv.last_family_at and conv.last_ai_at and conv.last_family_at > conv.last_ai_at))
        if stop:
            conv.next_followup_at = None
            done["skipped"] += 1
            continue
        reason = conv.followup_reason or "pick up where we left off"
        conv.next_followup_at = None                     # one attempt, whatever happens
        conv.followups_sent = (conv.followups_sent or 0) + 1
        try:
            d = decide(db, prog, lead, conv, None, follow_up=reason)
            if validate_decision(d, "", conv, prog.primary_contact_name):
                d = decide(db, prog, lead, conv, None, follow_up=reason,
                           note="Fix: %s" % "; ".join(validate_decision(d, "", conv, prog.primary_contact_name)))
        except Exception as exc:                         # noqa: BLE001
            log.warning("follow-up AI unavailable for lead %s: %s", conv.lead_id, exc)
            db.commit()
            continue
        action = d.get("action")
        text = (d.get("reply") or "").strip()
        merge_memory(conv, d.get("memory_update"))
        if action == "wait" or not text or validate_decision(d, "", conv, prog.primary_contact_name) or action not in (
                "answer", "offer_times", "send_guide", "mail_guide", "close"):
            _turn(db, conv, "system", None, action="wait", reason="follow-up not needed: %s" % (d.get("reason") or action))
            db.commit()
            done["skipped"] += 1
            continue
        allowed: List[str] = []
        if "{TIMES}" in text or action == "offer_times":
            slots, _why = A.available_slots(db, prog)
            if not slots:
                db.commit()
                done["skipped"] += 1
                continue
            text = _ensure_times_question(text)
            text = _fill_times(text, slots)
            conv.offered_slots = json.dumps([x.isoformat() for x in slots])
            conv.offered_at = now
            conv.state = "offered"
        if action == "close":
            conv.state = "closed"
        if check_text(text, allowed_links=allowed, contact=prog.primary_contact_name):
            db.commit()
            done["skipped"] += 1
            continue
        t = _deliver(db, prog, lead, conv, None, text, "follow_up", mode, now=now)
        conv.last_ai_at = now
        conv.ai_turns = (conv.ai_turns or 0) + 1
        done["sent" if t.delivery in ("sent", "simulated") else "drafted"] += 1
        db.commit()
    return done
