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


def seed(db: Session, by_user_id: Optional[str] = None, presenter_password: Optional[str] = None,
         now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.services import workspace_access
    from app.services.auth_service import hash_password
    now = now or datetime.utcnow()
    rnd = random.Random(20261020)
    org = db.query(Organization).filter(Organization.slug == SLUG).first()
    created = False
    if org is None:
        org = Organization(name=NAME, slug=SLUG, plan="enterprise", industry="roofing", is_active=True,
                           is_demo=True, brand_name="Summit Peak Roofing", timezone="America/Chicago",
                           org_phone="(214) 555-0100")
        db.add(org)
        db.flush()
        created = True
    elif not org.is_demo:
        raise ValueError("The sample slug belongs to a real organization; refusing to touch it.")
    keys = C.jload(org.enabled_features, [])
    keys = keys if isinstance(keys, list) else []
    org.enabled_features = C.jdump(sorted(set(keys) | set(DEMO_FEATURES)))
    S.setup(db, org, by_user_id, grant_features=False)
    prof = S.ensure_profile(db, org)

    users = {}
    for email, name, role in TEAM:
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
    reps = [users[e] for e, _, r in TEAM if r == "advisor"]
    owner = users[TEAM[0][0]]
    prof.company_name = "Summit Peak Roofing"
    prof.color_primary, prof.color_accent = "#0b1f3a", "#f97316"
    prof.service_areas = C.jdump(["Plano", "Frisco", "McKinney", "Allen", "Richardson"])
    prof.default_salesperson_id = reps[0].id
    prof.notify_user_ids = C.jdump([owner.id])

    made = 0
    for i in range(64):
        email = "homeowner%02d@example.com" % i
        if db.query(Lead).filter(Lead.organization_id == org.id, Lead.email == email).first():
            continue
        made += 1
        city, zc = CITIES[i % len(CITIES)]
        rep = reps[i % len(reps)]
        created_at = now - timedelta(days=rnd.randint(1, 29), hours=rnd.randint(0, 10))
        lead = Lead(organization_id=org.id, assigned_to_id=rep.id, first_name=FIRST[i % 30],
                    last_name=LAST[(i * 7) % 20], email=email, phone=_phone(i), is_test=True,
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
        touches = 1 + (rnd.randint(0, 3) if depth == 1 else rnd.randint(0, 1))
        t = created_at + timedelta(hours=2)
        sent = []
        for k in range(touches):
            tmpl = C.SEQUENCE_TOUCHES[min(k, len(C.SEQUENCE_TOUCHES) - 1)]
            body = (tmpl["message_template"].replace("{first_name}", lead.first_name)
                    .replace("{advisor_name}", rep.full_name).replace("{org_name}", "Summit Peak Roofing"))
            if t > now:
                break
            db.add(Message(lead_id=lead.id, sender_id=rep.id, body=body, send_state="delivered",
                           delivery_status="delivered", send_source="cadence", sent_at=t))
            sent.append(t)
            t = t + timedelta(days=tmpl["day_offset"] + 2)
        if not sent:
            continue
        if depth == 1:
            if rnd.random() < 0.12:
                db.add(Reply(lead_id=lead.id, body="STOP", source="sms",
                             classification=ReplyClassification.DNC, received_at=sent[-1] + timedelta(hours=1),
                             reviewed_at=sent[-1] + timedelta(hours=3)))
                lead.status = "dnc"
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
        if depth >= 3:
            db.add(Message(lead_id=lead.id, sender_id=rep.id, send_state="delivered", delivery_status="delivered",
                           send_source="manual", sent_at=at + timedelta(minutes=12),
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
    return {"organization_id": org.id, "organization_name": org.name, "created": created,
            "homeowners_added": made, "homeowners_total": len(leads),
            "users": [{"email": u.email, "name": u.full_name, "role": u.role} for u in users.values()],
            "presenter_password_set": bool(presenter_password)}
