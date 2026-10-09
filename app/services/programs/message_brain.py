"""The SCI message brain: WHO, WHY, WHERE, WHAT was said -> the next message.

Every automated program message is built in three steps, and the third one can
stop it:

    1. CONTEXT PACKET   everything verified about this contact, from the
                        database and the source row - nothing guessed.
    2. COMPOSE          the campaign family's PLAYBOOK picks the strategy for
                        this touch (touch 1..5, or a fresh reactivation angle)
                        and writes it as Kerry, from the family's own location.
    3. QUALITY GATE     a deterministic pre-send check. Any failure HOLDS the
                        message (it is never "sent anyway").

    LOCATION + ORIGINAL CAMPAIGN + STATUS + RECENCY + ACTIVITY
      + PREVIOUS COMMUNICATION + RESPONSE HISTORY + CURRENT STAGE
      = NEXT MESSAGE STRATEGY

Primary objective: GET A REPLY. Secondary: GET AN APPOINTMENT. So every touch
carries exactly one call to action, and it is "reply" - never "call us", never
"click here".

WHAT THE BRAIN MAY NOT DO. It never invents a personal fact (veteran status,
benefits eligibility, family circumstances, property ownership), a price, a
promise or an address. A playbook states the facts it may use ("approved")
and the claims it must never make ("prohibited"); the quality gate enforces
both. Copy is plain, local and personal: the facility first, Kerry as the
constant person, no corporate parent named.

A meaningful reply ends all of this - responses.on_inbound pauses the
sequence and a person takes it from there (the runner never sends to a
contact who has replied).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

MAX_TOUCHES = 5
REACTIVATION = "reactivation"

# ── playbooks ────────────────────────────────────────────────────────────────
#
# One per campaign family. Fields used in the copy:
#   {first_name} {contact} {location} {why} {flyer_line}
# "why" is the sentence fragment that says WHY this person is in the database,
# taken from the campaign they actually responded to - never a guess.

_COMMON_PROHIBITED = [
    (r"\$\s?\d", "names a price"),
    (r"\b(guarantee[sd]?|guaranteed)\b", "makes a guarantee"),
    (r"\b(free (burial|plot|cremation|funeral|space|headstone|marker))\b", "promises something free"),
    (r"\b(you (are|'re) (eligible|entitled|qualified)|you qualify|you will receive)\b",
     "asserts eligibility the source data does not show"),
    (r"\b(va|the government|uncle sam) (will|is going to) (pay|cover)\b", "promises benefits"),
    (r"\b(as a veteran|thank you for your service|your (military )?service)\b",
     "assumes veteran status"),
    (r"\b(your (husband|wife|spouse|son|daughter|mother|father|mom|dad|children|kids|family member)'?s?)\b",
     "invents a family circumstance"),
    (r"\b(you (own|bought|purchased|have) (a|your|the) (plot|space|lot|property|crypt|niche))\b",
     "assumes property ownership"),
    (r"\b(limited time|act now|last chance|special offer|expires (soon|today)|don'?t miss|hurry)\b",
     "uses pressure language"),
    (r"\b(service corporation international|sci\b|dignity memorial corporate)\b", "names the corporate parent"),
]

ROBOTIC = [
    r"\bjust (checking|touching) (in|base)\b", r"\btouching base\b", r"\bcircling back\b",
    r"\bper our records\b", r"\bvalued (customer|client)\b", r"\bdear (customer|valued)\b",
    r"\bwe are reaching out\b", r"\bto whom it may concern\b", r"\bkindly\b",
    r"\bdo not hesitate\b", r"\bat your earliest convenience\b", r"\bplease be advised\b",
    r"\bthis (email|message) is to inform\b", r"\bpursuant\b",
]

CTA_REPLY = r"\b(reply|write back|respond|just answer|send me a (quick )?(note|line))\b"
CTA_OTHER = [
    (r"\b(call (us|me|our office)|give (us|me) a call|phone (us|me))\b", "asks them to call instead of reply"),
    (r"\b(click (here|the link|below))\b", "asks them to click instead of reply"),
]

_TOUCH_SPACING_NOTE = ("Touches are FOLLOWUP_DAYS apart (default 4); touch 5 is the last active "
                       "touch. Reactivation is a separate, later campaign with a fresh angle.")


def _pb(key, label, why, topic, useful, question, approved, prohibited=(), subjects=None):
    return {
        "key": key, "label": label, "why": why, "topic": topic,
        "useful_info": useful, "common_question": question,
        "approved_facts": list(approved), "prohibited": list(prohibited),
        "cta": "reply", "tone": "warm, plain, first person from Kerry; local; short paragraphs",
        "subjects": subjects or {},
    }


PLAYBOOKS: Dict[str, Dict] = {
    "veteran_planning_guide": _pb(
        "veteran_planning_guide", "Veteran Planning Guide",
        "you requested information through our Veteran Planning Guide program", "veteran planning",
        "One thing many families tell us helps: keep a copy of the discharge papers (the DD-214) "
        "with your other important documents. It is one of the first things asked for when "
        "veteran arrangements are made, and it can be hard to find later.",
        "A question we hear often is what veteran benefits do and don't cover. The honest answer "
        "is that it depends on the person's service record and choices, so I'd rather go over "
        "your specific questions than give you a general list.",
        ["the Veteran Planning Guide exists and Kerry can send it",
         "the DD-214 is commonly requested for veteran arrangements",
         "benefits depend on the individual service record"],
        subjects={1: ["Your Veteran Planning Guide - {location}", "{first_name}, did you get the Veteran Planning Guide?"],
                  2: ["One thing worth keeping with your guide", "A quick tip from {location}"],
                  3: ["A question families often ask us", "About veteran benefits"],
                  4: ["Would a short visit help?", "A time to talk at {location}?"],
                  5: ["Should I keep this open for you?", "Close out your request?"],
                  REACTIVATION: ["Still here if you need us - {location}"]}),
    "veteran_official": _pb(
        "veteran_official", "Veteran Official",
        "you asked us for veteran planning information", "veteran planning",
        "One thing many families tell us helps: keep a copy of the discharge papers (the DD-214) "
        "with your other important documents - it is one of the first things asked for.",
        "A question we hear often is what veteran benefits do and don't cover. It depends on the "
        "individual service record, so I'd rather answer your specific questions than guess.",
        ["veteran planning information is available from Kerry",
         "the DD-214 is commonly requested", "benefits depend on the individual record"],
        subjects={1: ["Your veteran planning request - {location}"], 2: ["A quick tip from {location}"],
                  3: ["About veteran benefits"], 4: ["A time to talk at {location}?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
    "veteran_spanish": _pb(
        "veteran_spanish", "Veteran (Spanish callouts)",
        "you asked us for veteran planning information", "veteran planning",
        "One thing many families tell us helps: keep a copy of the discharge papers (the DD-214) "
        "with your other important documents.",
        "Many families ask whether information is available in Spanish. If that would help, just "
        "say so in your reply.",
        ["veteran planning information is available", "the DD-214 is commonly requested"],
        subjects={1: ["Your veteran planning request - {location}"], 2: ["A quick tip from {location}"],
                  3: ["Information in Spanish?"], 4: ["A time to talk at {location}?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
    "general_survey": _pb(
        "general_survey", "General Survey",
        "you returned our planning survey", "planning ahead",
        "Families who plan ahead usually tell us the biggest benefit is simple: their wishes are "
        "written down, so nobody has to guess at a hard time.",
        "A common question is where to even start. Usually it's just a short conversation about "
        "what matters to you - nothing has to be decided in that first talk.",
        ["the person returned a planning survey", "planning ahead records a person's wishes"],
        subjects={1: ["Thank you for your survey - {location}"], 2: ["Why families plan ahead"],
                  3: ["Where to start"], 4: ["A short conversation at {location}?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
    "life_story": _pb(
        "life_story", "Life Story",
        "you requested a Life Story from us", "capturing a life story",
        "Families often say the most valuable part of a life story is the small details - the "
        "names, places and stories that are easy to lose over time.",
        "A common question is how long it takes. It can be as simple as one conversation, and "
        "you decide how much to include.",
        ["the person requested a Life Story"],
        subjects={1: ["Your Life Story request - {location}"], 2: ["The details worth keeping"],
                  3: ["How a Life Story comes together"], 4: ["A time to get started?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
    "cemetery_x_sell": _pb(
        "cemetery_x_sell", "Cemetery Planning",
        "you showed interest in cemetery planning with us", "cemetery planning",
        "Many families find it helps to see the grounds before making any decision - it makes "
        "the options much easier to picture.",
        "A question we hear often is what the different options are - burial, mausoleum or "
        "cremation placement. I'm glad to walk you through whichever you're curious about.",
        ["visiting the grounds is possible", "options include burial, mausoleum and cremation placement"],
        subjects={1: ["Cemetery planning at {location}"], 2: ["Seeing the grounds first"],
                  3: ["The options, simply"], 4: ["A short visit to {location}?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
    "cremation": _pb(
        "cremation", "Cremation Information",
        "you asked us for cremation information", "cremation planning",
        "Planning ahead for cremation lets you put your wishes in writing - for example where the "
        "remains are placed - so your family doesn't have to decide later.",
        "A common question is what the options are after cremation. There are several, and I'm "
        "glad to explain whichever ones you'd like to know about.",
        ["the person asked for cremation information", "there are several placement options"],
        subjects={1: ["Your cremation information - {location}"], 2: ["Putting your wishes in writing"],
                  3: ["What happens after cremation"], 4: ["A short conversation at {location}?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
    "re_engagement": _pb(
        "re_engagement", "Re-engagement",
        "you were in touch with us about planning", "planning ahead",
        "Families who plan ahead usually tell us the biggest benefit is that their wishes are "
        "written down, so nobody has to guess at a hard time.",
        "A common question is where to start. Usually it's one short conversation about what "
        "matters to you.",
        ["the person was previously in touch with the location"],
        subjects={1: ["Following up from {location}"], 2: ["Why families plan ahead"],
                  3: ["Where to start"], 4: ["A short conversation at {location}?"],
                  5: ["Should I keep this open for you?"], REACTIVATION: ["Still here if you need us - {location}"]}),
}
FAMILY_FALLBACK = "re_engagement"

# Strategy per touch: what this touch is FOR. The copy below implements it.
TOUCH_STRATEGY = {
    1: "Reference what they requested and ask for a simple reply.",
    2: "Give one useful piece of information tied to the original campaign.",
    3: "Address a common question or concern for that campaign.",
    4: "Offer an easy appointment as the next step.",
    5: "Permission-based soft close: the last active touch.",
    REACTIVATION: "A fresh angle, later - never a recycled touch 1.",
}

_EMAIL = {
    1: ("Hi {first_name},\n\nThis is {contact} with {location}. {Why}, and I wanted to personally "
        "make sure you received what you needed.\n\n{flyer_line}"
        "Is there anything specific you'd like to know? Just reply to this email and I'll get back "
        "to you myself.\n\n{contact}\n{location}"),
    2: ("Hi {first_name},\n\n{useful}\n\nIf a question comes up as you look things over, just reply "
        "here - I read every reply myself.\n\n{contact}\n{location}"),
    3: ("Hi {first_name},\n\n{question}\n\nWhat would be most helpful to know? A one-line reply is "
        "plenty.\n\n{contact}\n{location}"),
    4: ("Hi {first_name},\n\nSometimes it's easier to talk things through in person. I'd be glad to "
        "set aside a few unhurried minutes with you at {location} - no decisions needed.\n\n"
        "If that would help, just reply with a day that tends to work for you.\n\n{contact}\n{location}"),
    5: ("Hi {first_name},\n\nI don't want to keep filling your inbox. Would you like me to keep your "
        "request open, or close it out for now? A one-word reply either way is perfect.\n\n"
        "{contact}\n{location}"),
    REACTIVATION: ("Hi {first_name},\n\nIt's {contact} at {location}. Some time has passed since "
                   "{why}, and I wanted you to know the offer to help still stands - whenever "
                   "the timing is right for you.\n\nIf there's anything you'd like to know, just "
                   "reply here.\n\n{contact}\n{location}"),
}
_SMS = {
    1: "Hi {first_name}, this is {contact} with {location}. {Why} - did you get what you needed? Just reply here.",
    2: "Hi {first_name}, {contact} at {location}. Quick tip on {topic}: reply and I'll send it over.",
    3: "Hi {first_name}, {contact} at {location}. Any questions about {topic} I can answer? Just reply.",
    4: "Hi {first_name}, {contact} at {location}. Would a short visit help? Reply with a day that works.",
    5: "Hi {first_name}, {contact} at {location}. Should I keep your request open or close it out? Just reply.",
    REACTIVATION: "Hi {first_name}, {contact} at {location}. Still glad to help whenever the time is right - just reply.",
}


def playbook(family_key: Optional[str]) -> Dict:
    return PLAYBOOKS.get(family_key or "", PLAYBOOKS[FAMILY_FALLBACK])


# ── 1. context packet ────────────────────────────────────────────────────────

def _raw(rec) -> Dict:
    try:
        return json.loads(rec.raw_json or "{}") if rec is not None else {}
    except ValueError:
        return {}


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.strip()[:19], fmt)
        except ValueError:
            continue
    return None


def context_packet(db: Session, lead, now: Optional[datetime] = None) -> Optional[Dict]:
    """Everything verified about this contact, or None outside a program."""
    from app.models.models import BookingLink, CadenceState, EmailMessage, Message, Reply
    from app.models.program_models import CampaignFamily, ProgramEmailTouch, ProgramResponse
    from app.services.programs import aliases, identity
    prog = identity.program_for_org(db, lead.organization_id)
    if prog is None:
        return None
    now = now or datetime.utcnow()
    recs = identity.records_for_lead(db, lead)
    rec = identity.source_record_for_lead(db, lead)
    prof = identity.location_profile_for_lead(db, lead)
    raw = _raw(rec)
    fam_key = (rec.campaign_family if rec else None) or FAMILY_FALLBACK
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == lead.organization_id,
                                           CampaignFamily.key == fam_key).first())
    outbound = (db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id)
                .order_by(EmailMessage.sent_at.desc()).limit(10).all())
    texts = (db.query(Message).filter(Message.lead_id == lead.id)
             .order_by(Message.sent_at.desc()).limit(10).all())
    replies = db.query(Reply).filter(Reply.lead_id == lead.id).count()
    responses = (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id)
                 .order_by(ProgramResponse.received_at.desc()).all())
    touches = (db.query(ProgramEmailTouch).filter(ProgramEmailTouch.lead_id == lead.id)
               .order_by(ProgramEmailTouch.touch_number).all())
    booked = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id).all())
    cad = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    last_activity = _parse_date(raw.get("Last Activity Date"))
    created = _parse_date(raw.get("Create Date"))
    from app.services.public_identity import sending_identity_for_org
    sender = aliases.sending_address(db, lead.organization_id)
    return {
        "lead_id": lead.id,
        "source_lead_id": rec.source_lead_id if rec else None,
        "first_name": (lead.first_name or "").strip().title() or None,
        "last_name": (lead.last_name or "").strip().title() or None,
        "location": prof.official_name if prof else None,
        "location_id": prof.location_id if prof else None,
        "location_alias": prof.email_alias if prof else None,
        "alias_receiving": bool(prof is not None and aliases.receiving(prog, prof)),
        "campaign_family": fam_key,
        "campaign_label": playbook(fam_key)["label"],
        "campaign_active": bool(fam is not None and fam.is_active),
        "original_campaign": rec.source_campaign if rec else None,
        "source_status": rec.source_status if rec else None,
        "source_channel": rec.source_channel if rec else None,
        "crm_stage": lead.status,
        "last_activity": raw.get("Last Activity") or None,
        "last_activity_date": last_activity.date().isoformat() if last_activity else None,
        "days_since_activity": (now - last_activity).days if last_activity else None,
        "source_created": created.date().isoformat() if created else None,
        "prior_outbound_email": [{"subject": m.subject, "status": m.status,
                                  "sent_at": m.sent_at.isoformat() if m.sent_at else None} for m in outbound],
        "prior_outbound_sms": len(texts),
        "prior_touch_numbers": [t.touch_number for t in touches if t.status == "sent"],
        "prior_inbound_replies": replies,
        "responses": [{"class": r.response_class, "intents": json.loads(r.intents or "[]"),
                       "status": r.handling_status} for r in responses[:5]],
        "appointments": [{"status": b.status, "time": b.booked_time.isoformat() if b.booked_time else None}
                         for b in booked],
        "appointment_booked": any(b.status in ("booked", "confirmed") for b in booked),
        "cadence_state": str(getattr(cad.status, "value", cad.status)) if cad else None,
        "suppressed": bool(lead.allow_email is False or (lead.status or "").lower() == "dnc"),
        "on_hold": any(getattr(r, "on_hold", False) for r in recs),
        "in_review": any(r.needs_data_review or r.duplicate_review_reason for r in recs),
        "kerry": prog.primary_contact_name,
        "display_name": identity.display_name(prog, prof) if prof else None,
        "facility": {"name": prof.official_name, "website": prof.website} if prof else None,
        "sender_address": sender,
        "sender_healthy": bool(sender),
        "postal": _postal(db, lead),
    }


def _postal(db: Session, lead) -> Optional[str]:
    from app.services.programs.unsubscribe import postal_line
    return postal_line(db, lead)


# ── 2. compose ───────────────────────────────────────────────────────────────

def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def compose(packet: Dict, touch, channel: str = "email", *, flyer_line: str = "",
            variant: int = 0) -> Dict:
    """Subject (email) and body for this touch, from the family's playbook."""
    pb = playbook(packet.get("campaign_family"))
    fields = {
        "first_name": packet.get("first_name") or "there",
        "contact": packet.get("kerry") or "",
        "location": packet.get("location") or "",
        "why": pb["why"], "Why": _cap(pb["why"]),
        "topic": pb["topic"], "useful": pb["useful_info"], "question": pb["common_question"],
        "flyer_line": (flyer_line.strip() + "\n\n") if flyer_line and flyer_line.strip() else (
            "If you'd like me to send the information again, just say so and I'll send it to you "
            "personally.\n\n" if touch == 1 and channel == "email" else ""),
    }
    from app.services.programs.identity import render
    tmpl = (_EMAIL if channel == "email" else _SMS).get(touch)
    if tmpl is None:
        raise ValueError("no strategy for touch %r" % (touch,))
    body = render(tmpl, fields)
    subject = None
    if channel == "email":
        options = pb["subjects"].get(touch) or ["A note from {location}"]
        subject = render(options[variant % len(options)], fields)
    return {"touch": touch, "channel": channel, "strategy": TOUCH_STRATEGY.get(touch),
            "subject": subject, "body": body, "family": pb["key"]}


# ── 3. quality gate ──────────────────────────────────────────────────────────

def _words(s: str) -> List[str]:
    return re.findall(r"[a-z']+", (s or "").lower())


def similarity(a: str, b: str) -> float:
    """Word-trigram Jaccard: 1.0 identical, ~0 unrelated."""
    def grams(s):
        w = _words(s)
        return {tuple(w[i:i + 3]) for i in range(max(0, len(w) - 2))}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(len(ga | gb))


def quality(packet: Dict, msg: Dict, *, prior_bodies: Optional[List[str]] = None,
            other_locations: Optional[List[str]] = None, expected_display_name: Optional[str] = None,
            require_campaign_active: bool = True) -> Dict:
    """Pass/hold verdict with every reason. Failures hold; warnings only score."""
    fails: List[str] = []
    warns: List[str] = []
    body = msg.get("body") or ""
    subject = msg.get("subject") or ""
    text = ("%s\n%s" % (subject, body)).lower()
    channel = msg.get("channel") or "email"
    pb = playbook(packet.get("campaign_family"))
    # eligibility gates - repeated here so a message can never pass on copy alone
    if packet.get("on_hold"):
        fails.append("contact is ON HOLD")
    if packet.get("in_review"):
        fails.append("contact has an open data/duplicate review")
    if not packet.get("location"):
        fails.append("no resolved location")
    if packet.get("suppressed"):
        fails.append("contact is suppressed / opted out")
    if packet.get("appointment_booked"):
        fails.append("an appointment is booked - nurture stops")
    if packet.get("responses"):
        fails.append("the contact has replied - a person handles the conversation")
    if require_campaign_active and not packet.get("campaign_active"):
        fails.append("campaign is not switched on")
    if not packet.get("sender_healthy"):
        fails.append("no verified sending identity")
    if channel == "email" and "postal" in packet and not packet.get("postal"):
        fails.append("no postal address on file for the location (commercial email must carry one)")
    touch = msg.get("touch")
    if touch not in list(range(1, MAX_TOUCHES + 1)) + [REACTIVATION]:
        fails.append("touch %r is outside the playbook" % (touch,))
    if channel not in ("email", "sms"):
        fails.append("unknown channel %r" % channel)
    # identity: Kerry Allan | <Location>, exactly
    kerry = (packet.get("kerry") or "").strip()
    loc = (packet.get("location") or "").strip()
    if not kerry:
        fails.append("no customer-facing person (Kerry Allan) configured")
    elif kerry.lower() not in body.lower():
        fails.append("does not identify %s" % kerry)
    if loc and loc.lower() not in body.lower():
        fails.append("does not name the location (%s)" % loc)
    if expected_display_name is not None and kerry and loc and expected_display_name != "%s | %s" % (kerry, loc):
        fails.append("sender display name is %r, not %r" % (expected_display_name, "%s | %s" % (kerry, loc)))
    own_removed = text.replace(loc.lower(), " ") if loc else text
    for other in sorted(other_locations or [], key=len, reverse=True):
        if other and other.lower() != loc.lower() and re.search(r"\b%s\b" % re.escape(other.lower()), own_removed):
            fails.append("names a different location (%s)" % other)
            break
    # grounding
    for pat, why in _COMMON_PROHIBITED + [(p, "playbook-prohibited claim") for p in pb["prohibited"]]:
        if re.search(pat, text, re.I):
            fails.append(why)
    if re.search(r"\{\w+\}", body + subject):
        fails.append("an unfilled placeholder is left in the copy")
    if touch == 1 and not any(w in text for w in _words(pb["label"]) if len(w) > 3) \
            and pb["why"].lower() not in text:
        fails.append("first touch does not say why they are hearing from us (%s)" % pb["label"])
    # one clear, reply-first call to action
    if not re.search(CTA_REPLY, text):
        fails.append("no reply call-to-action")
    for pat, why in CTA_OTHER:
        if re.search(pat, text):
            fails.append(why)
    if body.count("?") > 2:
        warns.append("more than two questions - pick one ask")
    # tone and length
    for pat in ROBOTIC:
        if re.search(pat, text):
            fails.append("robotic / corporate phrase: %s" % re.search(pat, text).group(0))
    n = len(_words(body))
    if channel == "email" and n > 170:
        fails.append("too long (%d words; keep under 170)" % n)
    if channel == "sms" and len(body) > 320:
        fails.append("text too long (%d chars; keep under 320)" % len(body))
    if channel == "email" and n < 25:
        warns.append("very short email")
    sentences = [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 20]
    if len(sentences) != len(set(sentences)):
        fails.append("repeats a sentence")
    for prev in prior_bodies or []:
        if similarity(body, prev) >= 0.5:
            fails.append("substantially repeats an earlier message to this contact")
            break
    score = max(0, 100 - 25 * len(fails) - 5 * len(warns))
    return {"ok": not fails, "score": score, "failures": fails, "warnings": warns}


def next_message(db: Session, lead, touch, channel: str = "email", *, flyer_line: str = "",
                 now: Optional[datetime] = None) -> Dict:
    """Packet -> compose -> quality, for one contact and touch. Never sends."""
    from app.models.models import EmailMessage
    from app.models.program_models import LocationProfile
    packet = context_packet(db, lead, now=now)
    if packet is None:
        return {"ok": False, "failures": ["not a program contact"], "packet": None, "message": None}
    msg = compose(packet, touch, channel, flyer_line=flyer_line)
    prior = [m.body_html or "" for m in db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id,
                                                                      EmailMessage.status != "failed")
             .order_by(EmailMessage.sent_at.desc()).limit(10).all()]
    prior = [re.sub(r"<[^>]+>", " ", p) for p in prior]
    others = [p.official_name for p in db.query(LocationProfile)
              .filter(LocationProfile.organization_id == lead.organization_id,
                      LocationProfile.is_review_bucket.is_(False)).all()]
    verdict = quality(packet, msg, prior_bodies=prior, other_locations=others,
                      expected_display_name=packet.get("display_name"))
    return dict(verdict, packet=packet, message=msg)
