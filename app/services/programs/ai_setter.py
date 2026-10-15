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
MAX_AI_TURNS = 8
FOLLOWUP_GAP = timedelta(hours=20)
ACTIONS = ("answer", "offer_times", "book", "callback", "send_guide", "handoff", "wait")

_BANNED = [
    (r"\$\s?\d|\b\d+\s?(dollars|per month|a month)\b", "mentions a price"),
    (r"\b(guarantee[ds]?|risk[- ]free|no obligation ever)\b", "makes a guarantee"),
    (r"\b(act now|limited time|only today|expires?|last chance|hurry|before it'?s too late|"
     r"prices? (are )?(going|go) up|running out)\b", "creates pressure"),
    (r"\b(service corporation|sci\b|dignity memorial)", "names the parent company"),
    (r"\bas an ai\b|\blanguage model\b", "talks about being an AI"),
]


def mode_of(prog) -> str:
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


def history(db: Session, lead, limit: int = 14) -> List[Dict]:
    """What the family saw and said, oldest first: our texts (any path) and
    their replies, plus any setter draft/simulated lines that never became a
    Message row."""
    from app.models.models import Message
    rows = []
    for m in db.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at.desc()).limit(limit):
        rows.append((m.sent_at, "us", m.body or ""))
    for r in (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
              .order_by(ProgramResponse.received_at.desc()).limit(limit)):
        rows.append((r.received_at, "family", r.body_excerpt or ""))
    conv = conversation_for(db, lead, create=False)
    if conv is not None:
        for t in (db.query(ProgramConversationTurn)
                  .filter(ProgramConversationTurn.conversation_id == conv.id, ProgramConversationTurn.role == "ai",
                          ProgramConversationTurn.delivery.in_(("simulated", "draft", "held")))
                  .order_by(ProgramConversationTurn.created_at.desc()).limit(limit)):
            rows.append((t.created_at, "us", t.text or ""))
    rows = [r for r in rows if r[0] is not None]
    rows.sort(key=lambda r: r[0])
    return [{"at": r[0].isoformat(), "who": r[1], "text": r[2]} for r in rows[-limit:]]


def _human_took_over(db: Session, lead, conv) -> bool:
    from app.models.models import Message
    from app.services import send_source as ss
    since = conv.last_ai_at or conv.created_at
    return db.query(Message.id).filter(Message.lead_id == lead.id, Message.send_source == ss.MANUAL,
                                       Message.sent_at > since).first() is not None


# ── safety check on every outgoing line ─────────────────────────────────────

def check_text(text: str, *, allowed_links: List[str]) -> List[str]:
    problems = []
    t = (text or "").strip()
    if not t:
        return ["empty"]
    if len(t) > 480:
        problems.append("too long for a text")
    low = t.lower()
    for pat, why in _BANNED:
        if re.search(pat, low):
            problems.append(why)
    for url in re.findall(r"https?://\S+", t):
        if url.rstrip(".,)") not in allowed_links:
            problems.append("contains a link that is not an approved one")
    if "{" in t or "}" in t:
        problems.append("unfilled placeholder")
    return problems


# ── the AI's decision ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are writing text messages for {contact}, the family-services representative for {location}, a funeral home / cemetery. A family replied to our planning outreach. Your job: have a warm, natural conversation that helps them, and when it fits, arrange a phone appointment or a personal visit with {contact}.

How to write:
- Plain, kind, local. 1-3 short sentences. At most ONE question per message. No emojis, no exclamation-heavy sales talk.
- Sign nothing; the family knows who is writing.
- Never mention prices, costs, discounts, guarantees, deadlines or urgency that is not real. Never invent facts about the family, their plan, benefits or eligibility.
- Never pretend to be a person if asked directly; say you're helping {contact} with scheduling and a person is glad to talk.
- Respect their choice and timing. If they're busy, offer to reconnect at a time that suits them. If they prefer to call themselves, give them that option warmly and don't push.
- If they ask for the planning guide, send it (action send_guide) and offer to answer questions.
- If they already have a plan with us, thank them for being part of the family here, and offer a plan review appointment. Do NOT assume what kind of review: unless they've said, ask whether they'd like to go over their prearrangement file or their flower / memorial care service. A timely review is reasonable (records stay current, wishes and contacts updated) - never invent urgency.
- When a call or visit fits, offer EXACTLY two times using the placeholder {{TIMES}} in your reply, e.g. "Would {{TIMES}} work for a quick call?" The system fills in two real open times. Never write a specific time yourself.
- If they pick one of the offered times, choose action "book" with slot_choice 1 or 2. If they name a different specific day/time, use action "book" with requested_time in YYYY-MM-DDTHH:MM (24h, Central) and the system checks it.
- If they want a person, are upset or grieving, ask something you cannot answer from what you know, or mention a reschedule/cancellation of a booked appointment: action "handoff" with a short kind reply that {contact} will reach out.
- If they ask to be called back at a time: action "callback" with callback_window in their words.
- If the message needs no answer (a simple "ok"/"thanks" after we already confirmed), action "wait".

Return ONLY JSON:
{{"intent": "<short>", "action": "answer|offer_times|book|callback|send_guide|handoff|wait", "reply": "<the text to send, or empty for wait>", "slot_choice": 0, "requested_time": "", "appointment_kind": "phone|visit|", "service_type": "", "callback_window": "", "reason": "<why, one line>"}}"""


def _context(db, prog, lead, conv, resp) -> Dict:
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    slots = A.offered(conv)
    now_c = A._today_central()
    return {
        "family_first_name": (lead.first_name or "").strip().title() or None,
        "location": prof.official_name if prof else "our family services team",
        "today": now_c.strftime("%A, %B %-d, %Y %-I:%M %p Central"),
        "conversation_state": conv.state,
        "times_already_offered": [{"choice": i + 1, "time": A.describe(s)} for i, s in enumerate(slots)],
        "booked": A.describe(conv.booked_time) if conv.booked_time else None,
        "reply_class": resp.response_class if resp is not None else None,
        "intents": json.loads(resp.intents) if (resp is not None and resp.intents) else [],
        "guide_available": True,
        "conversation": history(db, lead),
    }


def _get_client():                                       # test seam (see ai_gateway.chat_completion)
    return None


def decide(db, prog, lead, conv, resp, *, actor: Optional[str] = None) -> Dict:
    """The AI's one next step. Raises on any AI failure (caller hands off).
    A family's reply runs it as BACKGROUND AI (the platform switch must be
    on); a person running the simulator runs it as a MANUAL action of theirs."""
    from app.services import ai_gateway
    from app.services.programs import identity
    prof = identity.location_profile_for_lead(db, lead)
    system = SYSTEM_PROMPT.format(contact=(prog.primary_contact_name or "our representative"),
                                  location=(prof.official_name if prof else "our family services team"))
    ctx = _context(db, prog, lead, conv, resp)
    r = ai_gateway.chat_completion(
        feature="sci_appointment_setter", capability="sci_appointment_setter",
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

def _deliver(db, prog, lead, conv, resp, text, action, mode, *, now) -> ProgramConversationTurn:
    from app.services import contact_hours, send_source as ss
    if getattr(lead, "is_test", False) or not lead.phone:
        return _turn(db, conv, "ai", text, action=action, delivery="simulated",
                     response_id=resp.id if resp else None, reason="synthetic contact - nothing sent")
    if mode == DRAFT or (resp is not None and resp.channel == "email"):
        if resp is not None:
            resp.suggested_reply = text
        return _turn(db, conv, "ai", text, action=action, delivery="draft",
                     response_id=resp.id if resp else None,
                     reason="draft mode - a person sends it" if mode == DRAFT else "email reply - a person sends it")
    hours = contact_hours.check(lead, now)
    if not hours.get("permitted"):
        conv.next_followup_at = contact_hours.next_permitted_utc(lead, now)
        return _turn(db, conv, "ai", text, action=action, delivery="held",
                     response_id=resp.id if resp else None, reason="outside contact hours")
    try:
        from app.services.sms_service import send_sms
        advisor = A.program_advisor(db, prog)
        msg = send_sms(db, advisor, lead, text, include_booking_link=False, send_source=ss.SCI_SETTER)
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
            from app.models.program_models import ProgramAlert
            from app.services.programs import responses as R
            title = R.alert_title(prog)
            sms = "%s: A family needs a person. Please check your SCI Responses inbox." % title
            if getattr(lead, "is_test", False):
                sms = "TEST - " + sms
            targets = [("primary", "sms", prog.alert_phone), ("primary", "email", prog.alert_email)]
            for m in R._managers(prog):
                if (m.get("role") or "").lower() == "team":
                    targets += [("team", "sms", m.get("phone")), ("team", "email", m.get("email"))]
            who = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A family"
            for aud, ch, to in targets:
                if not to:
                    continue
                ok, why = R.staff_alert_gate(to, ch, aud, bool(prog.staff_sms_alerts_enabled))
                text = sms if ch == "sms" else "%s needs a person: %s" % (who, reason)
                rc: Dict = {}
                if ok:
                    ok, why = R._deliver_staff_alert(db, prog, ch, to, text, kind="handoff", receipt=rc)
                db.add(R.sms_alert_record(ProgramAlert(organization_id=prog.organization_id, kind="handoff",
                                                       audience=aud, channel=ch, recipient=to, delivered=bool(ok),
                                                       reason=why, message=text[:2000]), rc))
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
        conv.state = "waiting"
        conv.next_followup_at = now + FOLLOWUP_GAP
        _turn(db, conv, "system", None, action="wait",
              reason="automatic reply - not answered; follow-up after %s UTC" % conv.next_followup_at.strftime("%Y-%m-%d %H:%M"))
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
    if conv.ai_turns >= MAX_AI_TURNS:
        _handoff(db, prog, lead, conv, "long conversation - a person should take it from here", notify=True)
        db.commit()
        return dict(out, action="handoff", state=conv.state)

    try:
        d = decide(db, prog, lead, conv, resp, actor=actor)
    except Exception as exc:                             # noqa: BLE001
        log.warning("setter AI unavailable for lead %s: %s", lead.id, exc)
        _handoff(db, prog, lead, conv, "AI unavailable (%s) - a person answers" % type(exc).__name__, notify=False)
        _turn(db, conv, "system", None, action="handoff", reason="AI unavailable: %s" % str(exc)[:200])
        db.commit()
        return dict(out, action="handoff", state=conv.state, error=type(exc).__name__)

    action = d["action"]
    text = (d.get("reply") or "").strip()
    out.update(intent=d.get("intent"), action=action, reason=d.get("reason"))
    if d.get("appointment_kind") in (A.PHONE, A.VISIT):
        conv.appointment_kind = d["appointment_kind"]
    if d.get("service_type"):
        conv.service_type = str(d["service_type"])[:60]
    allowed_links: List[str] = []

    if action == "wait":
        _turn(db, conv, "system", None, action="wait", reason=d.get("reason") or "no answer needed")
        db.commit()
        return dict(out, state=conv.state)

    if action == "book":
        slot = None
        choices = A.offered(conv)
        k = int(d.get("slot_choice") or 0)
        if 1 <= k <= len(choices):
            slot = choices[k - 1]
        elif d.get("requested_time"):
            try:
                slot = datetime.fromisoformat(str(d["requested_time"])[:16])
            except ValueError:
                slot = None
        if slot is None:
            action = "offer_times"                      # unclear choice: offer again
        else:
            kind = conv.appointment_kind or A.VISIT
            res = A.book(db, prog, lead, slot, kind)
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
                A.notify_team_booked(db, prog, lead, slot, kind)
                # The booking path already sent the family its confirmation
                # text; a synthetic contact gets the same line, simulated.
                confirm = "You're all set for %s. If anything changes, just reply here." % A.describe(slot)
                if getattr(lead, "is_test", False) or not lead.phone:
                    _turn(db, conv, "ai", confirm, action="book", delivery="simulated",
                          reason="booking confirmed by the calendar")
                else:
                    _turn(db, conv, "system", confirm, action="book", delivery="sent",
                          reason="confirmation sent by the booking path")
                conv.ai_turns += 1
                conv.last_ai_at = now
                db.commit()
                return dict(out, state=conv.state, booked_time=slot.isoformat())
            if res.get("status") in ("taken", "unconfirmed", "already_booked"):
                action = "offer_times"
                text = "That time just filled up. Would {TIMES} work instead?"
            else:
                _handoff(db, prog, lead, conv, "booking failed: %s" % res.get("reason"), notify=True)
                text = "Thank you. %s will reach out shortly to set that up." % (prog.primary_contact_name or "Our team")
                action = "handoff"

    if action == "offer_times":
        slots, why = A.available_slots(db, prog)
        if not slots:
            _handoff(db, prog, lead, conv, "no times to offer: %s" % why, notify=True)
            text = ("Thank you. %s will reach out to find a time that works for you."
                    % (prog.primary_contact_name or "Our team"))
            action = "handoff"
        else:
            if "{TIMES}" not in text and "{{TIMES}}" not in text:
                text = (text + " Would {TIMES} work?").strip()
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
        allow = sms_programs.content_allowance(db, lead).get("allow_links", True)
        if allow and guide:
            allowed_links.append(guide)
            if guide not in text:
                text = "%s %s" % (text, guide)
        elif lead.email:
            text = text or "I'll email the planning guide to you now."
            out["guide_by_email"] = True
        out["guide"] = guide

    if action == "callback":
        conv.state = "callback_requested"
        conv.callback_window = (d.get("callback_window") or "")[:120]
        _handoff_state = conv.state
        _handoff(db, prog, lead, conv, "callback requested: %s" % (conv.callback_window or "no time given"), notify=True)
        conv.state = _handoff_state                     # stays visible as a callback request

    if action == "handoff" and conv.state != "human":
        _handoff(db, prog, lead, conv, d.get("reason") or "the family wants a person", notify=True)

    problems = check_text(text, allowed_links=allowed_links)
    if problems:
        _handoff(db, prog, lead, conv, "AI reply held: %s" % ", ".join(problems), notify=True)
        _turn(db, conv, "ai", text, action=action, delivery="blocked", reason="; ".join(problems),
              response_id=resp.id if resp else None)
        db.commit()
        return dict(out, action="handoff", state=conv.state, held=problems)

    t = _deliver(db, prog, lead, conv, resp, text, action, mode, now=now)
    conv.ai_turns = (conv.ai_turns or 0) + 1
    conv.last_ai_at = now
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
        try:
            from app.services.sms_service import send_sms
            msg = send_sms(db, A.program_advisor(db, prog), lead, t.text, include_booking_link=False,
                           send_source=ss.SCI_SETTER)
            t.delivery, t.message_id = "sent", getattr(msg, "id", None)
            sent += 1
        except Exception as exc:                         # noqa: BLE001
            db.rollback()
            t = db.merge(t)
            t.delivery, t.reason = "blocked", str(exc)[:240]
    db.commit()
    return {"sent": sent}
