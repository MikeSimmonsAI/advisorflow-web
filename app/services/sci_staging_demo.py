"""SCI STAGING ONLY: made-up families so the SCI screens have something to show.

Staging has its own database and no real families, so SCI Command, the
dashboard and the inbox were empty. This fills the staging SCI workspace with
about thirty INVENTED families spread over the real location list: replies of
every kind (new, hot, review, low, driving auto-reply), our texts back, the
appointment setter's drafts, a callback request and booked visits.

It FAILS CLOSED and does nothing unless:
  * the process is APP_ENV=staging, AND
  * DATABASE_URL is positively the SCI staging database
    (sci_staging_bootstrap.database_is_sci_staging), AND
  * the SCI organization and its outreach program exist.

NOTHING HERE CAN REACH A PERSON:
  * every phone is a 555-01xx number (reserved for fiction) and sms_service
    refuses any text to a demo family, every email is @example.com;
  * no cadence is started, no SLA clock is set (sla_due_at stays empty, so the
    SLA sweep never alerts on them), no setter follow-up is scheduled and every
    AI turn is a draft;
  * booked visits are marked confirmation / reminder / review already sent, so
    the reminder jobs skip them;
  * nothing is emailed, texted or alerted while seeding.

Built once. After that, once per Central day (at deploy and from the
background loop) every demo timestamp is MOVED FORWARD by the days that have
passed - nothing is deleted - so "replies today" always has today's replies
and anything a tester did (notes, assignments, simulated texts, closes) is
kept. A full rebuild happens only on request (force=True). Only rows this
module created (Lead.source == DEMO_SOURCE) are ever removed.

Demo identity is ONE flag, Lead.source == DEMO_SOURCE, checked at every path
that could reach the outside: sms_service, email_service, the booking webhook
(no calendar event), appointments.book (always simulated), the appointment
setter (simulated turns), staff alerts (never) and the outreach gate (source
rows are on hold). Lead.is_test stays false on purpose: the SCI staging
database holds nothing else, and is_test would hide them from every SCI
screen. They exist only in the SCI staging database.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DEMO_SOURCE = "sci_staging_demo"
ORG_NAME = "Service Corporation International"
CAMPAIGN = "planning_guide_outreach"

# (first, last, class, handling, minutes ago, channel, what they wrote, intents, setter)
# setter: None | (state, ai_draft_text) | ("callback_requested", window) | ("booked", days_ahead, hour)
FAMILIES: List[tuple] = [
    ("Walter", "Pruitt", "active", "new", 12, "sms",
     "Would really like to receive the guide. If I have questions after reading it can I call you?",
     ["information_request"], ("ai_active", "I'm sending the guide your way. If you'd like, someone from our team can walk "
                                            "through it with you and answer any questions. Would you prefer a short call or a visit?")),
    ("Dolores", "Whitfield", "bad_data", "new", 95, "sms",
     "I already have a plan with you all from years ago.", [], None),
    ("Curtis", "Langford", "low", "new", 140, "sms",
     "Will get back with you later, I'm on a house.", [], ("waiting", None)),
    ("Raymond", "Doyle", "auto_reply", "new", 70, "sms",
     "I'm driving with notifications silenced. I'll see your message when I get where I'm going.", [], None),
    ("Bernice", "Holloway", "hot", "new", 25, "sms",
     "Yes I want to come in and talk about plots for me and my husband. What times do you have?",
     ["appointment_intent"], ("offered", "Thank you, Bernice. I can do {T1} or {T2}. Which works better?")),
    ("Harold", "Kincaid", "hot", "new", 48, "sms",
     "Can someone call me tomorrow afternoon? Best after 2.", ["appointment_intent"], ("callback_requested", "tomorrow after 2 PM")),
    ("Geraldine", "Moss", "active", "opened", 190, "email",
     "Thank you for reaching out. What does the guide cover? My mother is 88 and we have not planned anything.",
     ["information_request", "general_planning"], ("ai_active", "Thank you for writing back. The guide walks through the choices a family "
                                                                 "makes ahead of time. Would a short call or a visit help most?")),
    ("Leon", "Barrett", "hot", "responded", 260, "sms",
     "Friday morning works for me.", ["appointment_intent"], ("booked", 2, 10)),
    ("Mildred", "Cates", "active", "new", 33, "sms",
     "How much is a cremation package? Just want an idea.", ["pricing_question"], None),
    ("Otis", "Fairbanks", "low", "opened", 320, "sms",
     "Not right now, maybe after the holidays.", [], ("waiting", None)),
    ("Vivian", "Ashby", "active", "new", 58, "sms",
     "Is this about the cemetery property my dad left me? I have the deed.", ["general_planning"], None),
    ("Clarence", "Odom", "bad_data", "new", 400, "sms",
     "Wrong number. I don't know who this is.", [], None),
    ("Loretta", "Pike", "hot", "responded", 600, "email",
     "We would like a visit next week. My son will come with me.", ["appointment_intent"], ("booked", 5, 14)),
    ("Eugene", "Marsh", "active", "new", 8, "sms",
     "Got the guide in the mail. Do you do veterans burials there?", ["information_request"], None),
    ("Ruby", "Delacroix", "low", "new", 210, "sms",
     "Ok thanks.", [], None),
    ("Floyd", "Henley", "auto_reply", "new", 300, "sms",
     "This is an automated reply. I am out of the office until Monday.", [], None),
    ("Nadine", "Croft", "active", "opened", 150, "sms",
     "Please send it to my email instead, I don't text much.", ["information_request"], None),
    ("Amos", "Whitaker", "hot", "new", 18, "sms",
     "My wife passed last month and I need to finish what we started. Can I come in this week?",
     ["appointment_intent"], ("human", None)),
    ("Irene", "Sutton", "active", "closed", 900, "sms",
     "Thank you, the guide was helpful.", [], None),
    ("Wendell", "Grimes", "low", "new", 75, "sms",
     "Maybe. Send me something to read first.", ["information_request"], None),
]
# Families we texted today who have not answered (fills "Contacted today").
QUIET = [("Marjorie", "Bell"), ("Lionel", "Ford"), ("Hazel", "Quinn"), ("Arthur", "Sloan"), ("Edna", "Rowe"),
         ("Virgil", "Kemp"), ("Opal", "Nash"), ("Roscoe", "Hale"), ("Pearl", "Dunn"), ("Homer", "Vance")]

OUR_FIRST_TEXT = ("Hi {first}, this is {loc}. We have a free family planning guide for you. "
                  "Would you like us to send it? Reply STOP to opt out.")
OUR_FOLLOWUP = "Thank you, {first}. Someone from {loc} will follow up with you shortly."


def _now() -> datetime:
    return datetime.utcnow()


def _central_today():
    from datetime import timezone
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/Chicago")).date()


def allowed(db: Session) -> Dict[str, Any]:
    from app.models.models import Organization
    from app.models.program_models import OutreachProgram
    from app.services import environment, sci_staging_bootstrap
    if environment.current() != environment.ENV_STAGING:
        return {"ok": False, "reason": "not_staging_environment"}
    if not sci_staging_bootstrap.database_is_sci_staging():
        return {"ok": False, "reason": "database_not_sci_staging"}
    org = db.query(Organization).filter(Organization.name == ORG_NAME).first()
    if org is None:
        return {"ok": False, "reason": "no_sci_org"}
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    if prog is None:
        return {"ok": False, "reason": "no_outreach_program"}
    return {"ok": True, "org": org, "prog": prog}


def _demo_leads(db, org_id):
    from app.models.models import Lead
    return db.query(Lead).filter(Lead.organization_id == org_id, Lead.source == DEMO_SOURCE)


def _fresh(db, org_id) -> bool:
    """True when today's (Central) set is already there."""
    from app.models.models import Lead
    from app.models.program_models import ProgramResponse
    newest = (db.query(ProgramResponse.received_at)
              .filter(ProgramResponse.lead_id.in_(_demo_leads(db, org_id).with_entities(Lead.id)))
              .order_by(ProgramResponse.received_at.desc()).first())
    if not newest:
        return False
    from zoneinfo import ZoneInfo
    got = newest[0].replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Chicago")).date()
    return got == _central_today()


def clear(db: Session, org_id: str) -> int:
    from app.models.models import BookingLink, EmailMessage, Lead, Message
    from app.models.program_models import (ProgramConversation, ProgramConversationTurn, ProgramResponse,
                                           ProgramSourceRecord)
    ids = [i for (i,) in _demo_leads(db, org_id).with_entities(Lead.id).all()]
    if not ids:
        return 0
    for model in (ProgramConversationTurn, ProgramConversation, ProgramResponse, Message, EmailMessage,
                  BookingLink, ProgramSourceRecord):
        db.query(model).filter(model.lead_id.in_(ids)).delete(synchronize_session=False)
    db.query(Lead).filter(Lead.id.in_(ids), Lead.source == DEMO_SOURCE).delete(synchronize_session=False)
    db.flush()
    return len(ids)


def _sender(db, prog, org_id):
    from app.models.models import User
    from app.services.programs import appointments as A
    u = A.program_advisor(db, prog)
    if u is None:
        u = db.query(User).filter(User.organization_id == org_id).first()
    if u is None:
        from app.models.sales_models import Membership
        m = (db.query(Membership).filter(Membership.scope_id == org_id, Membership.is_active.is_(True))
             .order_by(Membership.created_at).first())
        u = db.query(User).filter(User.id == m.user_id).first() if m else None
    return u


def build(db: Session, org, prog) -> Dict[str, Any]:
    from app.models.models import BookingLink, Lead, Message
    from app.models.program_models import (LocationProfile, ProgramConversation, ProgramConversationTurn,
                                           ProgramResponse, ProgramSourceRecord)
    removed = clear(db, org.id)
    locs = [p for p in db.query(LocationProfile).filter(LocationProfile.organization_id == org.id,
                                                       LocationProfile.is_review_bucket.isnot(True))
            .order_by(LocationProfile.official_name).all() if p.location_id]
    if not locs:
        db.rollback()
        return {"ok": False, "reason": "no_locations"}
    sender = _sender(db, prog, org.id)
    if sender is None:
        db.rollback()
        return {"ok": False, "reason": "no_user_to_send_as"}
    rnd = random.Random(str(_central_today()))
    now = _now()
    made, n = 0, 0

    def lead_for(first, last, loc, email_ok):
        nonlocal n
        n += 1
        lead = Lead(organization_id=org.id, first_name=first, last_name=last, source=DEMO_SOURCE,
                    phone="+1214555%04d" % (100 + n),            # 555-01xx: fictional
                    email=("%s.%s@example.com" % (first, last)).lower() if email_ok else None,
                    status="replied")
        db.add(lead)
        db.flush()
        db.add(ProgramSourceRecord(organization_id=org.id, source_lead_id="DEMO-%03d" % n,
                                   raw_json=json.dumps({"demo": True}), location_id=loc.location_id, location_status="mapped",
                                   on_hold=True,
                                   lead_id=lead.id, campaign_family=CAMPAIGN))
        return lead

    def text(lead, body, at, source="cadence"):
        db.add(Message(lead_id=lead.id, sender_id=sender.id, body=body, sent_at=at, send_source=source,
                       delivery_status="delivered", twilio_status="delivered"))

    for i, (first, last, cls, handling, ago, channel, body, intents, setter) in enumerate(FAMILIES):
        loc = locs[(i * 7) % len(locs)]
        lead = lead_for(first, last, loc, channel == "email" or i % 3 == 0)
        got = now - timedelta(minutes=ago)
        text(lead, OUR_FIRST_TEXT.format(first=first, loc=loc.official_name),
             got - timedelta(minutes=rnd.randint(30, 300)))
        resp = ProgramResponse(organization_id=org.id, lead_id=lead.id, channel=channel,
                               location_id=loc.location_id, campaign_family=CAMPAIGN,
                               source_lead_id="DEMO-%03d" % n, response_class=cls, body_excerpt=body,
                               summary=body[:140], intents=json.dumps(intents), received_at=got,
                               handling_status=handling, sla_due_at=None, sla_alert_count=0,
                               urgency={"hot": "HOT", "active": "ACTIVE"}.get(cls, "LOW"),
                               opened_at=got + timedelta(minutes=5) if handling != "new" else None,
                               closed_at=got + timedelta(minutes=20) if handling == "closed" else None)
        db.add(resp)
        if handling in ("responded", "closed"):
            text(lead, OUR_FOLLOWUP.format(first=first, loc=loc.official_name), got + timedelta(minutes=9),
                 source="manual")
        if setter:
            state = setter[0]
            conv = ProgramConversation(organization_id=org.id, lead_id=lead.id, state=state,
                                       appointment_kind="visit", ai_turns=1, last_family_at=got, is_test=False)
            draft = None
            if state == "offered":
                d1 = (now + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
                d2 = (now + timedelta(days=2)).replace(hour=20, minute=0, second=0, microsecond=0)
                slots = [d1 - timedelta(hours=5), d2 - timedelta(hours=5)]   # naive Central
                conv.offered_slots = json.dumps([s.isoformat() for s in slots])
                conv.offered_at = got + timedelta(minutes=1)
                from app.services.programs import appointments as A
                draft = setter[1].replace("{T1}", A.describe(slots[0])).replace("{T2}", A.describe(slots[1]))
            elif state == "callback_requested":
                conv.callback_window = setter[1]
            elif state == "booked":
                when = (now + timedelta(days=setter[1])).replace(hour=setter[2], minute=0, second=0, microsecond=0)
                while when.weekday() >= 5:
                    when += timedelta(days=1)
                conv.booked_time = when
                db.add(BookingLink(lead_id=lead.id, user_id=sender.id, status="booked", booked_time=when,
                                   appt_label="Planning Visit (demo)", appt_duration=60, confirmation_sent=True,
                                   reminder_24hr_sent=True, reminder_1hr_sent=True,
                                   review_request_sent_at=now, confirmed_at=got + timedelta(minutes=3)))
                text(lead, "You're set for %s. We look forward to seeing you." % when.strftime("%A at %-I %p"),
                     got + timedelta(minutes=2), source="sci_appointment_setter")
            elif state == "human":
                conv.handoff_reason = "grief - a person should answer"
            else:
                draft = setter[1]
            db.add(conv)
            db.flush()
            if draft:
                db.add(ProgramConversationTurn(conversation_id=conv.id, organization_id=org.id, lead_id=lead.id,
                                               role="ai", text=draft, action="answer", delivery="draft",
                                               created_at=got + timedelta(minutes=1)))
        made += 1

    for j, (first, last) in enumerate(QUIET):
        loc = locs[(j * 5 + 3) % len(locs)]
        lead = lead_for(first, last, loc, False)
        lead.status = "sent"
        text(lead, OUR_FIRST_TEXT.format(first=first, loc=loc.official_name),
             now - timedelta(minutes=rnd.randint(20, 400)))
        made += 1
    db.commit()
    return {"ok": True, "families": made, "replaced": removed}


def _seed_day(db, org_id):
    """The Central date of the newest SEEDED reply (DEMO-* rows only)."""
    from zoneinfo import ZoneInfo
    from app.models.models import Lead
    from app.models.program_models import ProgramResponse
    row = (db.query(ProgramResponse.received_at)
           .filter(ProgramResponse.lead_id.in_(_demo_leads(db, org_id).with_entities(Lead.id)),
                   ProgramResponse.source_lead_id.like("DEMO-%"))
           .order_by(ProgramResponse.received_at.desc()).first())
    if not row:
        return None
    return row[0].replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/Chicago")).date()


SHIFT = {
    "ProgramResponse": ("received_at", "opened_at", "responded_at", "active_at", "closed_at", "created_at"),
    "ProgramConversation": ("offered_at", "booked_time", "last_family_at", "last_ai_at", "next_followup_at",
                            "created_at", "updated_at"),
    "ProgramConversationTurn": ("created_at",),
    "Message": ("sent_at", "delivery_status_at"),
    "EmailMessage": ("sent_at", "opened_at"),
    "BookingLink": ("booked_time", "confirmed_at", "created_at"),
}


def roll_forward(db: Session, org_id: str, days: int) -> int:
    """Move every demo timestamp forward by `days`. Keeps everything testers did."""
    from app.models import models as M, program_models as P
    from app.models.models import Lead
    ids = [i for (i,) in _demo_leads(db, org_id).with_entities(Lead.id).all()]
    if not ids or days <= 0:
        return 0
    delta = timedelta(days=days)
    moved = 0
    for name, cols in SHIFT.items():
        model = getattr(M, name, None) or getattr(P, name)
        for row in db.query(model).filter(model.lead_id.in_(ids)).all():
            for col in cols:
                v = getattr(row, col, None)
                if isinstance(v, datetime):
                    setattr(row, col, v + delta)
            if name == "ProgramConversation" and row.offered_slots:
                try:
                    row.offered_slots = json.dumps([(datetime.fromisoformat(x) + delta).isoformat()
                                                    for x in json.loads(row.offered_slots)])
                except (ValueError, TypeError):
                    pass
            moved += 1
    db.commit()
    return moved


def _hold_all(db, org_id) -> None:
    """Demo source rows are always on hold (never promoted, batched or sent)."""
    from app.models.models import Lead
    from app.models.program_models import ProgramSourceRecord as R
    n = (db.query(R).filter(R.lead_id.in_(_demo_leads(db, org_id).with_entities(Lead.id)),
                            R.on_hold.isnot(True))
         .update({R.on_hold: True}, synchronize_session=False))
    if n:
        db.commit()


def ensure(db: Session, force: bool = False) -> Dict[str, Any]:
    """Build the demo set once; afterwards move it forward a day at a time."""
    gate = allowed(db)
    if not gate["ok"]:
        return {"ok": False, "reason": gate["reason"]}
    org_id = gate["org"].id
    _hold_all(db, org_id)
    seeded = _seed_day(db, org_id)
    if not force and seeded is not None:
        gap = (_central_today() - seeded).days
        if gap <= 0:
            return {"ok": True, "skipped": "already_today"}
        try:
            return {"ok": True, "rolled_forward_days": gap, "rows": roll_forward(db, org_id, gap)}
        except Exception as exc:                         # noqa: BLE001
            db.rollback()
            log.exception("sci staging demo roll-forward failed")
            return {"ok": False, "reason": type(exc).__name__}
    try:
        out = build(db, gate["org"], gate["prog"])
    except Exception as exc:                             # noqa: BLE001
        db.rollback()
        log.exception("sci staging demo build failed")
        return {"ok": False, "reason": type(exc).__name__}
    log.info("sci staging demo: %s", out)
    return out


def is_demo_lead(lead) -> bool:
    """A family this module made up. Never texted (sms_service refuses it)."""
    return getattr(lead, "source", None) == DEMO_SOURCE
