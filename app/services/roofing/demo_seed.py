"""The SAMPLE roofing workspace - for demonstrating, never for sending.

  * The organization is flagged is_demo and named "(SAMPLE)"; every screen
    that reads it shows the sample-data label (metrics.compute -> sample_data).
  * Every homeowner is is_test=True with an example.com email and a 555-01xx
    phone number, so every existing send guard refuses them.
  * Messages, replies and bookings are WRITTEN AS ROWS - no provider is ever
    called. The feature set has no sms / email / cadences, so nothing in the
    workspace can send even by hand.
  * Users get unusable random passwords unless a presenter password is given.

Idempotent: a second run tops nothing up twice (homeowners are keyed on their
example.com email).
"""
import random
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, Message, Organization, Reply, ReplyClassification, User
from app.services.roofing import common as C
from app.services.roofing import journey as J
from app.services.roofing import setup as S

SLUG = "evosys-roofing-sample"
NAME = "Summit Peak Roofing (SAMPLE)"
DEMO_FEATURES = ("leads", "imports", "booking", "calendar", "availability", "reports", "users",
                 "branding_settings", C.FEATURE)

TEAM = [("demo.roof.owner@example.com", "Dana Whitaker", "org_admin"),
        ("demo.roof.alex@example.com", "Alex Moreno", "advisor"),
        ("demo.roof.jordan@example.com", "Jordan Pierce", "advisor"),
        ("demo.roof.casey@example.com", "Casey Lin", "advisor")]
FIRST = ["James", "Maria", "Robert", "Linda", "Michael", "Patricia", "David", "Jennifer", "William", "Elizabeth",
         "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy",
         "Matthew", "Lisa", "Anthony", "Betty", "Mark", "Sandra", "Steven", "Ashley", "Paul", "Kimberly"]
LAST = ["Carter", "Nguyen", "Brooks", "Hayes", "Patel", "Reed", "Foster", "Kim", "Bennett", "Ortiz",
        "Coleman", "Price", "Sanders", "Ross", "Hughes", "Flores", "Butler", "Simmons", "Long", "Bryant"]
STREETS = ["Oak Hollow Dr", "Ridgecrest Ln", "Cedar Bend", "Willow Creek Rd", "Maple Ridge Ct", "Stonegate Dr",
           "Sunset Ridge", "Prairie View Ln", "Timberline Dr", "Lakeshore Blvd"]
CITIES = [("Plano", "75024"), ("Frisco", "75034"), ("McKinney", "75070"), ("Allen", "75013"),
          ("Richardson", "75080"), ("Garland", "75044")]
ISSUES = ["Hail damage after the April storm", "Leak over the kitchen", "Missing shingles on the north side",
          "Granules in the gutters", "Sagging section near the chimney", "Roof is 22 years old",
          "Water stain on the bedroom ceiling", "Wind damage - flashing lifted", "Insurance adjuster visit pending"]
SOURCES = ["2025 storm canvass list", "Website estimate requests", "Past customers - referrals", "Home show leads"]
REPLY_YES = ["Yes, I'd like someone to take a look.", "Yes please - we had hail last month.",
             "Sure, what times do you have?", "Yes. The leak is getting worse."]
REPLY_Q = ["Is the inspection really free?", "Do you work with insurance claims?",
           "Can you call me after 5? I have questions about a full replacement."]
REPLY_NO = ["We already had it fixed, thanks.", "Not right now."]


def _phone(i: int) -> str:
    return "+1214555%04d" % (100 + (i % 100))


def _team(org) -> list:
    """The sample salespeople. In the default SAMPLE org they keep their
    original addresses; seeded into another roofing workspace they get an
    address of their own (still example.com, still unusable passwords)."""
    if org.slug == SLUG:
        return TEAM
    return [(e.replace("@example.com", "+%s@example.com" % org.slug), n, r) for e, n, r in TEAM]


def seed(db: Session, by_user_id: Optional[str] = None, presenter_password: Optional[str] = None,
         now: Optional[datetime] = None, organization_id: Optional[str] = None,
         homeowners: int = 96) -> Dict[str, Any]:
    """Seed the SAMPLE roofing story.

    organization_id: seed an existing roofing workspace (its Roofing program
    must already be on) instead of the default "Summit Peak Roofing (SAMPLE)".
    That workspace is flagged is_demo, which labels every screen as sample data
    and makes every outbound provider refuse it (demo_guard) - nothing in it
    can text, email or touch a real calendar.
    """
    from app.services import workspace_access
    from app.services.auth_service import hash_password
    now = now or datetime.utcnow()
    rnd = random.Random(20261020)
    homeowners = max(1, min(int(homeowners or 96), 99))
    created = False
    if organization_id:
        org = db.query(Organization).filter(Organization.id == organization_id).first()
        if org is None:
            raise ValueError("Organization not found.")
        if not C.explicitly_enabled(org):
            raise ValueError("Turn the Roofing program on for this workspace before adding sample data.")
        org.is_demo = True
    else:
        org = db.query(Organization).filter(Organization.slug == SLUG).first()
    if org is None:
        org = Organization(name=NAME, slug=SLUG, plan="enterprise", industry="roofing", is_active=True,
                           is_demo=True, brand_name="Summit Peak Roofing", timezone="America/Chicago",
                           org_phone="(214) 555-0100")
        db.add(org)
        db.flush()
        created = True
    elif not org.is_demo:
        raise ValueError("The sample slug belongs to a real organization; refusing to touch it.")
    if not organization_id:
        keys = C.jload(org.enabled_features, [])
        keys = keys if isinstance(keys, list) else []
        org.enabled_features = C.jdump(sorted(set(keys) | set(DEMO_FEATURES)))
    team = _team(org)
    company = "Summit Peak Roofing" if org.slug == SLUG else (org.brand_name or org.name)
    S.setup(db, org, by_user_id, grant_features=False)
    prof = S.ensure_profile(db, org)

    users = {}
    for email, name, role in team:
        u = db.query(User).filter(User.email == email).first()
        if u is not None and u.organization_id != org.id:
            raise ValueError("Sample user %s belongs to another organization." % email)
        if u is None:
            u = User(organization_id=org.id, email=email, full_name=name, role=role, is_active=True,
                     password_hash=hash_password(presenter_password or secrets.token_urlsafe(24)),
                     must_change_password=not presenter_password, available_start_time="08:00",
                     available_end_time="17:00", available_days="0,1,2,3,4,5", booking_timezone="America/Chicago",
                     buffer_minutes=30, max_bookings_per_day=5)
            db.add(u)
            db.flush()
        try:
            workspace_access.grant_workspace_membership(db, u.id, org.id, role=role, commit=False,
                                                        check_capacity=False)
        except Exception:  # noqa: BLE001 - membership is a convenience for entering the sample
            pass
        users[email] = u
    reps = [users[e] for e, _, r in team if r == "advisor"]
    owner = users[team[0][0]]
    prof.company_name = company
    prof.color_primary, prof.color_accent = "#0b1f3a", "#f97316"
    prof.service_areas = C.jdump(["Plano", "Frisco", "McKinney", "Allen", "Richardson"])
    prof.default_salesperson_id = reps[0].id
    prof.notify_user_ids = C.jdump([owner.id])

    made = 0
    from app.models.conversation_models import ConversationState
    from app.services import send_source as SS
    for i in range(homeowners):
        email = "homeowner%02d@example.com" % i
        if db.query(Lead).filter(Lead.organization_id == org.id, Lead.email == email).first():
            continue
        made += 1
        city, zc = CITIES[i % len(CITIES)]
        rep = reps[i % len(reps)]
        created_at = now - timedelta(days=rnd.randint(1, 44), hours=rnd.randint(0, 10))
        lead = Lead(organization_id=org.id, assigned_to_id=rep.id, first_name=FIRST[i % 30],
                    last_name=LAST[(i * 7 + i // 30) % 20], email=email, phone=_phone(i), is_test=True,
                    street_address="%d %s" % (1200 + i * 37, STREETS[i % 10]), city=city, state="TX",
                    zip_code=zc, source="csv", source_detail=SOURCES[i % 4], status="new",
                    sms_consent=True, created_at=created_at,
                    custom_fields=C.jdump({"roof_issue": ISSUES[i % len(ISSUES)],
                                           "service_type": ["repair", "replacement", "not_sure"][i % 3],
                                           "urgency": ["high", "normal", "emergency", "low"][i % 4],
                                           "inspection_interest": "unknown",
                                           "roof_age": 8 + (i * 3) % 20}))
        db.add(lead)
        db.flush()
        # The journey this sample homeowner takes: 0 untouched ... 8 won, 9 lost.
        roll = rnd.random()
        depth = (0 if roll < 0.10 else 1 if roll < 0.40 else 2 if roll < 0.55 else 3 if roll < 0.65
                 else 4 if roll < 0.80 else 5 if roll < 0.87 else 6 if roll < 0.93 else 7)
        if depth == 0:
            continue
        touches = 1 + rnd.randint(0, 3)
        t = created_at + timedelta(hours=2)
        sent = []
        for k in range(touches):
            tmpl = C.SEQUENCE_TOUCHES[min(k, len(C.SEQUENCE_TOUCHES) - 1)]
            body = (tmpl["message_template"].replace("{first_name}", lead.first_name)
                    .replace("{advisor_name}", rep.full_name).replace("{org_name}", company))
            if t > now:
                break
            db.add(Message(lead_id=lead.id, sender_id=rep.id, body=body, send_state="delivered",
                           delivery_status="delivered", send_source="cadence", sent_at=t))
            sent.append(t)
            t = t + timedelta(days=tmpl["day_offset"] + 2)
        if not sent:
            continue
        if depth == 1:
            # Contacted, no reply yet: the sequence is still running for them.
            from app.models.models import CadenceState
            if not db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first():
                nxt = sent[-1] + timedelta(days=C.SEQUENCE_TOUCHES[min(len(sent), 4)]["day_offset"] + 1)
                db.add(CadenceState(lead_id=lead.id, status="active", current_touch_number=len(sent),
                                    cadence_started_at=sent[0], last_touch_sent_at=sent[-1],
                                    next_touch_due_at=max(nxt, now + timedelta(hours=rnd.randint(1, 40)))))
            if rnd.random() < 0.12:
                db.add(Reply(lead_id=lead.id, body="STOP", source="sms",
                             classification=ReplyClassification.DNC, received_at=sent[-1] + timedelta(hours=1),
                             reviewed_at=sent[-1] + timedelta(hours=3)))
                lead.status = "dnc"
                db.flush()
                for cs in db.query(CadenceState).filter(CadenceState.lead_id == lead.id).all():
                    cs.status, cs.next_touch_due_at = "stopped_dnc", None
            continue
        at = sent[-1] + timedelta(hours=rnd.randint(1, 20))
        if at > now:
            at = now - timedelta(minutes=rnd.randint(5, 90))
        recent = (now - at) < timedelta(days=2)
        if depth == 2:
            kind = rnd.random()
            cls, txt = ((ReplyClassification.QUESTION, rnd.choice(REPLY_Q)) if kind < 0.45 else
                        (ReplyClassification.INTERESTED, rnd.choice(REPLY_YES)) if kind < 0.8 else
                        (ReplyClassification.NOT_INTERESTED, rnd.choice(REPLY_NO)))
        else:
            cls, txt = ReplyClassification.INTERESTED, rnd.choice(REPLY_YES)
        db.add(Reply(lead_id=lead.id, body=txt, source="sms", classification=cls, received_at=at,
                     is_hot=cls == ReplyClassification.INTERESTED,
                     reviewed_at=None if (recent or depth == 2) else at + timedelta(hours=2)))
        lead.status = "replied"
        # THE AI'S SIDE OF THE CONVERSATION. A question gets answered by the AI;
        # some homeowners then say yes, some still want a person.
        mode, priority, intent, human_reason = "ai_active", "low_priority", "interested", None
        last_out = None
        if cls == ReplyClassification.QUESTION:
            answer = ("Yes - the inspection is completely free and takes about 45 minutes. We work with "
                      "insurance claims every week and can meet your adjuster. Would you like me to find a time?")
            last_out = at + timedelta(minutes=3)
            db.add(Message(lead_id=lead.id, sender_id=rep.id, body=answer, send_state="delivered",
                           delivery_status="delivered", send_source=SS.AI_CONVERSATION, sent_at=last_out))
            if rnd.random() < 0.5 and last_out + timedelta(hours=1) < now:
                db.add(Reply(lead_id=lead.id, body="Okay, that helps. Yes, let's set it up.", source="sms",
                             classification=ReplyClassification.INTERESTED, is_hot=True,
                             received_at=last_out + timedelta(hours=1)))
                intent = "ready_to_book"
                priority = "needs_human"
                mode = "waiting_on_human"
                human_reason = "Said yes after the AI answered - offer inspection times"
            else:
                intent, mode = "question", "waiting_on_customer"
        elif cls == ReplyClassification.NOT_INTERESTED:
            intent, mode = "not_interested", "ai_paused"
        elif depth == 2:
            intent, priority, mode = "ready_to_book", "needs_human", "waiting_on_human"
            human_reason = "Wants an inspection - confirm a time"
        if depth >= 3:
            offer_link = None
            if depth == 3:
                # Offered times, homeowner hasn't picked one: an open (pending)
                # booking link with no time - nothing to remind, nothing booked.
                from app.services.sms_service import _encode_booking_token
                offer_link = BookingLink(lead_id=lead.id, user_id=rep.id, status="pending",
                                         token=_encode_booking_token(lead, rep), appt_label="Roof Inspection",
                                         appt_duration=60, created_at=at + timedelta(minutes=12))
                db.add(offer_link)
                db.flush()
            last_out = at + timedelta(minutes=12)
            mode, priority, intent, human_reason = (("waiting_on_customer", "low_priority", "times_offered", None)
                                                    if depth == 3 else ("ai_active", "low_priority", "booked", None))
            db.add(Message(booking_link_id=offer_link.id if offer_link else None, lead_id=lead.id,
                           sender_id=rep.id, send_state="delivered", delivery_status="delivered",
                           send_source=SS.AI_CONVERSATION, sent_at=last_out,
                           body="Great, %s! I have Tuesday at 10:00 AM, Wednesday at 1:00 PM or Thursday at "
                                "9:00 AM open - which works best?" % lead.first_name))
        if depth >= 4:
            when = at + timedelta(days=rnd.randint(1, 6))
            when = when.replace(hour=rnd.choice([9, 10, 13, 15]), minute=0, second=0, microsecond=0)
            from app.services.sms_service import _encode_booking_token
            bl = BookingLink(lead_id=lead.id, user_id=rep.id, status="booked", booked_time=when,
                             appt_label="Roof Inspection", appt_duration=60,
                             token=_encode_booking_token(lead, rep), created_at=at + timedelta(minutes=40),
                             confirmation_sent=True, reminder_24hr_sent=True, reminder_1hr_sent=True)
            db.add(bl)
            lead.status = "booked"
        if not db.query(ConversationState).filter(ConversationState.lead_id == lead.id).first():
            db.add(ConversationState(organization_id=org.id, lead_id=lead.id, state="active", mode=mode,
                                     priority=priority, current_intent=intent, needs_human_reason=human_reason,
                                     has_open_question=intent == "question", last_inbound_at=at,
                                     last_outbound_at=last_out, last_meaningful_at=last_out or at,
                                     last_meaningful_event="Homeowner replied", updated_at=now))
        db.flush()
        if depth >= 5:
            opp = J.sync(db, org.id, [lead])[lead.id]
            stamp = at + timedelta(days=rnd.randint(2, 7))
            J.set_stage(db, opp, C.INSPECTED, rep.id, "Inspection completed", now=min(stamp, now))
            if depth >= 6:
                opp.estimate_amount = float(rnd.choice([8400, 11250, 14600, 17900, 21300]))
                J.set_stage(db, opp, C.ESTIMATE_SENT, rep.id, "Estimate emailed",
                            now=min(stamp + timedelta(days=1), now))
            if depth >= 7:
                if rnd.random() < 0.7:
                    opp.won_amount = opp.estimate_amount
                    J.set_stage(db, opp, C.WON, rep.id, "Contract signed", now=min(stamp + timedelta(days=3), now))
                else:
                    J.set_stage(db, opp, C.LOST, rep.id, "Went with a cheaper bid",
                                now=min(stamp + timedelta(days=3), now))
    db.commit()
    leads = db.query(Lead).filter(Lead.organization_id == org.id).all()
    J.sync(db, org.id, leads)
    # Field inspections on top of the story: completed reports with sample
    # photos for everyone already past the inspection, a draft for a visit
    # whose time just passed (issue #283).
    from app.services.roofing import demo_inspections
    insp = demo_inspections.seed(db, org, now)
    return {"organization_id": org.id, "organization_name": org.name, "created": created,
            "homeowners_added": made, "homeowners_total": len(leads),
            "users": [{"email": u.email, "name": u.full_name, "role": u.role} for u in users.values()],
            "presenter_password_set": bool(presenter_password), "inspections_added": insp["inspections"],
            "inspection_photos_added": insp["photos"]}
