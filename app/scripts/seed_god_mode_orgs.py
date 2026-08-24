"""
seed_god_mode_orgs.py
Creates 5 demo organizations with varied health/activity profiles
so God Mode Organizations page looks like a real multi-tenant platform.

Profiles:
  - Sunrise Memorial Gardens   : Healthy, Enterprise, high activity
  - Rose Valley Cemetery       : Healthy, Standard, medium activity
  - Evergreen Funeral Home     : Attention, Standard, low recent activity
  - Peaceful Acres Memorial    : Critical, Trial, almost dormant
  - Blue Ridge Funeral Svcs    : Dormant (is_active=False)
"""
import os, sys, random
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.deps import SessionLocal
from app.models.models import Organization, User, Lead, Message, Reply, LeadOutcome, ReplyClassification
from app.services.auth_service import hash_password

random.seed(99)

FIRST = ["James","Maria","Robert","Patricia","Michael","Jennifer","William","Linda",
         "David","Barbara","Richard","Elizabeth","Joseph","Susan","Thomas","Jessica",
         "Charles","Sarah","Christopher","Karen","Daniel","Lisa","Matthew","Nancy",
         "Anthony","Betty","Mark","Margaret","Donald","Sandra","Paul","Ashley",
         "Kenneth","Dorothy","Steven","Kimberly","Edward","Emily","Brian","Donna"]
LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
         "Wilson","Martinez","Anderson","Taylor","Thomas","Hernandez","Moore",
         "Jackson","Martin","Lee","Thompson","White","Harris","Sanchez","Clark",
         "Robinson","Lewis","Walker","Hall","Allen","Young","King","Wright","Scott"]

TIERS   = ["pre_need"]*4 + ["at_need"]*3 + ["imminent"]*2 + ["contract_sold"] + ["email_only"]*2
STATUSES_HOT  = ["new"]*5 + ["sent"]*15 + ["replied"]*20 + ["hot"]*30 + ["booked"]*20 + ["dnc"]*5 + ["dead"]*5
STATUSES_COLD = ["new"]*30 + ["sent"]*30 + ["replied"]*10 + ["hot"]*5 + ["booked"]*5 + ["dnc"]*10 + ["dead"]*10

HOT_REPLIES = [
    "Yes I'm interested, when can we talk?",
    "Please call me, I need to get this taken care of soon.",
    "I've been meaning to reach out. What are the next steps?",
    "My husband and I have been thinking about this. Can we meet?",
    "We already have a policy. Can we update it?",
]
NEUTRAL_REPLIES = [
    "Thank you for reaching out. I'll think about it.",
    "We're not quite ready yet but may be interested later.",
    "Please send me some information.",
    "I'd like to learn more. What does this involve?",
]
NEG_REPLIES = [
    "Not interested, please remove me.",
    "I already have this handled.",
    "Please don't contact me again.",
]
MSG_TEMPLATES = [
    "Hi {name}, this is {adv} reaching out about pre-need planning. Would you be open to a quick conversation?",
    "Hello {name}, I'm {adv}. I help families plan ahead so there are no surprises. Do you have 10 minutes?",
    "Hi {name}, {adv} here. I know this isn't an easy topic, but a quick chat now can really help your family later.",
    "{name}, this is {adv}. Checking in -- have you had a chance to review your planning options?",
    "Hi {name}, just wanted to follow up and see if you had any questions I could answer. -- {adv}",
]

ORGS = [
    {
        "name": "Sunrise Memorial Gardens",
        "slug": "sunrise-memorial",
        "plan": "enterprise",
        "is_active": True,
        "industry": "funeral",
        "org_phone": "(214) 555-0181",
        "advisors": ["Margaret Collins","Thomas Rivera","Susan Park","David Chen","Jennifer Walsh"],
        "num_leads": 200,
        "status_pool": STATUSES_HOT,
        "days_since_msg": 1,
    },
    {
        "name": "Rose Valley Cemetery",
        "slug": "rose-valley",
        "plan": "standard",
        "is_active": True,
        "industry": "funeral",
        "org_phone": "(469) 555-0294",
        "advisors": ["Kevin Morris","Diana Foster","Marcus Bell"],
        "num_leads": 90,
        "status_pool": STATUSES_HOT,
        "days_since_msg": 5,
    },
    {
        "name": "Evergreen Funeral Home",
        "slug": "evergreen-funeral",
        "plan": "standard",
        "is_active": True,
        "industry": "funeral",
        "org_phone": "(817) 555-0372",
        "advisors": ["Patricia Ward","James Nguyen"],
        "num_leads": 55,
        "status_pool": STATUSES_COLD,
        "days_since_msg": 45,
    },
    {
        "name": "Peaceful Acres Memorial",
        "slug": "peaceful-acres",
        "plan": "trial",
        "is_active": True,
        "industry": "funeral",
        "org_phone": "(972) 555-0418",
        "advisors": ["Robert Chang"],
        "num_leads": 18,
        "status_pool": STATUSES_COLD,
        "days_since_msg": 120,
    },
    {
        "name": "Blue Ridge Funeral Services",
        "slug": "blue-ridge-funeral",
        "plan": "standard",
        "is_active": False,
        "industry": "funeral",
        "org_phone": "(940) 555-0553",
        "advisors": ["Linda Pearson","Charles Morgan"],
        "num_leads": 40,
        "status_pool": STATUSES_COLD,
        "days_since_msg": 200,
    },
]


def rdate(days_ago_max=365, days_ago_min=1):
    delta = random.randint(days_ago_min, days_ago_max)
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=delta)


def seed_org(db, cfg):
    existing = db.query(Organization).filter(Organization.slug == cfg["slug"]).first()
    if existing:
        print(f"  [skip] {cfg['name']} -- already exists")
        return existing.id

    org = Organization(
        name=cfg["name"],
        slug=cfg["slug"],
        plan=cfg["plan"],
        is_active=cfg["is_active"],
        industry=cfg.get("industry", "funeral"),
        org_phone=cfg.get("org_phone"),
    )
    db.add(org)
    db.flush()
    org_id = org.id
    print(f"  [create] {cfg['name']} ({org_id})")

    advisors = []
    for adv_name in cfg["advisors"]:
        slug_name = adv_name.lower().replace(" ", ".")
        adv = User(
            organization_id=org_id,
            email=f"{slug_name}@{cfg['slug']}-demo.com",
            password_hash=hash_password("Demo1234!"),
            full_name=adv_name,
            role="advisor",
            is_active=True,
            must_change_password=False,
        )
        db.add(adv)
        advisors.append(adv)
    db.flush()

    days_since = cfg.get("days_since_msg", 30)
    num_leads = cfg["num_leads"]
    status_pool = cfg["status_pool"]

    leads = []
    used_phones = set()
    for i in range(num_leads):
        fn = random.choice(FIRST)
        ln = random.choice(LAST)
        adv = random.choice(advisors)
        while True:
            phone = f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}"
            if phone not in used_phones:
                used_phones.add(phone)
                break
        lead = Lead(
            organization_id=org_id,
            first_name=fn, last_name=ln,
            phone=phone,
            email=f"{fn.lower()}.{ln.lower()}{random.randint(1,99)}@example.com",
            tier=random.choice(TIERS),
            status=random.choice(status_pool),
            source_year=random.choice(list(range(2018, 2026))),
            assigned_to_id=adv.id,
            created_at=rdate(400, 30),
        )
        db.add(lead)
        leads.append(lead)
    db.flush()

    contactable = [l for l in leads if l.status not in ("new", "dnc", "dead")]
    msg_count = 0
    for lead in contactable:
        adv = next((a for a in advisors if a.id == lead.assigned_to_id), advisors[0])
        num_msgs = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
        last_sent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=days_since + random.randint(0, 10)
        )
        sent_at = last_sent - timedelta(days=random.randint(0, 5) * (num_msgs - 1))
        for _ in range(num_msgs):
            body = random.choice(MSG_TEMPLATES).format(
                name=lead.first_name,
                adv=adv.full_name or "your advisor",
            )
            msg = Message(
                lead_id=lead.id,
                sender_id=adv.id,
                body=body,
                sent_at=sent_at,
                twilio_status="delivered",
            )
            db.add(msg)
            sent_at += timedelta(days=random.randint(3, 10))
            msg_count += 1
    db.flush()

    replied_leads = [l for l in leads if l.status in ("replied", "hot", "booked")]
    reply_count = 0
    for lead in replied_leads:
        is_hot = lead.status in ("hot", "booked") or random.random() < 0.25
        is_neg = not is_hot and random.random() < 0.15
        body = (
            random.choice(HOT_REPLIES) if is_hot else
            random.choice(NEG_REPLIES) if is_neg else
            random.choice(NEUTRAL_REPLIES)
        )
        classification = (
            ReplyClassification.INTERESTED if is_hot else
            ReplyClassification.NOT_INTERESTED if is_neg else
            ReplyClassification.NEUTRAL
        )
        r = Reply(
            lead_id=lead.id,
            body=body,
            source="sms",
            received_at=lead.created_at + timedelta(days=random.randint(2, 20)),
            is_hot=is_hot,
            classification=classification,
        )
        db.add(r)
        reply_count += 1
    db.flush()

    booked = [l for l in leads if l.status == "booked"]
    outcome_count = 0
    for lead in booked:
        adv = next((a for a in advisors if a.id == lead.assigned_to_id), advisors[0])
        outcome = LeadOutcome(
            lead_id=lead.id,
            recorded_by_id=adv.id,
            resulted_in_sale=random.random() < 0.6,
            has_funeral_arrangement=random.choice([True, False, None]),
            has_cemetery_property=random.choice([True, False, None]),
            has_marker=random.choice([True, False, None]),
            notes="Demo outcome -- seeded automatically.",
            appointment_date=lead.created_at + timedelta(days=random.randint(3, 20)),
        )
        db.add(outcome)
        outcome_count += 1
    db.flush()

    print(f"         advisors={len(advisors)} leads={num_leads} msgs={msg_count} replies={reply_count} outcomes={outcome_count}")
    return org_id


def main():
    db = SessionLocal()
    try:
        print("\n=== God Mode Demo Org Seeder ===\n")
        for cfg in ORGS:
            seed_org(db, cfg)
        db.commit()
        print("\nAll done. Restart the backend to see data in God Mode.\n")
    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
