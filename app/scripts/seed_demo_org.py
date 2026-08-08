"""
seed_demo_org.py  —  Seed realistic demo data into a sub-account.

USAGE:
    python app/scripts/seed_demo_org.py --org-id <ORG_ID>
    python app/scripts/seed_demo_org.py --list-orgs        # show org IDs

WHAT IT CREATES:
  - Up to 5 advisors
  - 120 leads across tiers/statuses
  - ~80 outbound messages
  - ~35 replies (hot / neutral / negative)
  - ~12 booked outcomes
"""
import os, sys, random, argparse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.deps import SessionLocal
from app.models.models import User, Lead, Message, Reply, LeadOutcome, Organization
from app.services.auth_service import hash_password

random.seed(42)

FIRST = ["James","Maria","Robert","Patricia","Michael","Jennifer","William","Linda",
         "David","Barbara","Richard","Elizabeth","Joseph","Susan","Thomas","Jessica",
         "Charles","Sarah","Christopher","Karen","Daniel","Lisa","Matthew","Nancy",
         "Anthony","Betty","Mark","Margaret","Donald","Sandra","Paul","Ashley"]
LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
         "Wilson","Martinez","Anderson","Taylor","Thomas","Hernandez","Moore",
         "Jackson","Martin","Lee","Thompson","White","Harris","Sanchez","Clark"]

TIERS = ["pre_need"]*4 + ["at_need"]*3 + ["imminent"]*2 + ["contract_sold"]*1 + ["email_only"]*2
STATUSES = ["new"]*20 + ["sent"]*30 + ["replied"]*20 + ["hot"]*12 + ["booked"]*12 + ["dnc"]*3 + ["dead"]*3

HOT_REPLIES = [
    "Yes I'm interested, when can we talk?",
    "Please call me, I need to get this taken care of soon.",
    "I've been meaning to reach out. What are the next steps?",
    "My husband and I have been thinking about this. Can we meet?",
    "We already have a policy. Can we update it?",
    "This is perfect timing. I was just talking to my family about this.",
]
NEUTRAL_REPLIES = [
    "Thank you for reaching out. I'll think about it.",
    "We're not quite ready yet but may be interested later.",
    "Please send me some information.",
    "I'd like to learn more. What does this involve?",
    "We already have something in place but happy to review.",
    "Maybe in the spring when things calm down.",
]
NEG_REPLIES = [
    "Not interested, please remove me.",
    "I already have this handled.",
    "Please don't contact me again.",
]
MESSAGES = [
    "Hi {name}, this is {adv} reaching out about pre-need planning. Would you be open to a quick conversation?",
    "Hello {name}, I'm {adv}. I help families plan ahead so there are no surprises. Do you have 10 minutes?",
    "Hi {name}, {adv} here. I know this isn't an easy topic, but a quick chat now can really help your family later.",
    "{name}, this is {adv}. Checking in — have you had a chance to review your planning options?",
    "Hi {name}, just wanted to follow up and see if you had any questions I could answer for you. — {adv}",
]
ADVISOR_NAMES = ["Marcus Johnson","Diana Reyes","Kevin Park","Alicia Thompson","Brandon Wells"]


def rdate(days_ago_max=365, days_ago_min=1):
    delta = random.randint(days_ago_min, days_ago_max)
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=delta)

def list_orgs(db):
    orgs = db.query(Organization).all()
    print(f"\n{'ID':<38} {'Name'}")
    print("-"*70)
    for o in orgs:
        print(f"{o.id:<38} {o.name}")
    print()

def seed(db, org_id: str, num_leads: int = 120):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        print(f"ERROR: org {org_id} not found.")
        return

    print(f"\nSeeding demo data into: {org.name} ({org_id})")

    # ── Advisors ────────────────────────────────────────────────────────
    existing = db.query(User).filter(User.organization_id == org_id, User.role == "advisor").all()
    advisors = list(existing)
    needed = max(0, 5 - len(existing))
    for i in range(needed):
        name = ADVISOR_NAMES[i] if i < len(ADVISOR_NAMES) else f"Advisor {i+1}"
        fn, ln = name.split(" ", 1)
        email = f"demo.{fn.lower()}.{ln.lower()}@demo-advisorflow.com"
        adv = User(
            organization_id=org_id, email=email,
            password_hash=hash_password("Demo1234!"),
            full_name=name, role="advisor", is_active=True, must_change_password=False,
        )
        db.add(adv)
        db.flush()
        advisors.append(adv)
    print(f"  Advisors: {len(advisors)} total ({needed} created)")

    # ── Leads ───────────────────────────────────────────────────────────
    leads = []
    years = list(range(2018, 2026))
    phones = [f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}" for _ in range(num_leads)]
    for i in range(num_leads):
        fn = random.choice(FIRST)
        ln = random.choice(LAST)
        adv = random.choice(advisors)
        lead = Lead(
            organization_id=org_id,
            first_name=fn, last_name=ln,
            phone=phones[i],
            email=f"{fn.lower()}.{ln.lower()}{random.randint(1,99)}@example.com",
            tier=random.choice(TIERS),
            status=random.choice(STATUSES),
            source_year=random.choice(years),
            assigned_to_id=adv.id,
            created_at=rdate(400, 30),
        )
        db.add(lead)
        leads.append(lead)
    db.flush()
    print(f"  Leads: {num_leads} created")

    # ── Messages ────────────────────────────────────────────────────────
    contactable = [l for l in leads if l.status not in ("new", "dnc", "dead")]
    msg_count = 0
    for lead in contactable:
        adv = next((a for a in advisors if a.id == lead.assigned_to_id), advisors[0])
        num_msgs = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
        sent_at = lead.created_at + timedelta(days=random.randint(1, 10))
        for _ in range(num_msgs):
            body = random.choice(MESSAGES).format(
                name=lead.first_name,
                adv=adv.full_name or "your advisor",
            )
            msg = Message(
                lead_id=lead.id, sender_id=adv.id,
                body=body, channel="sms",
                sent_at=sent_at,
                status="delivered",
            )
            db.add(msg)
            sent_at += timedelta(days=random.randint(3, 14))
            msg_count += 1
    db.flush()
    print(f"  Messages: {msg_count} created")

    # ── Replies ─────────────────────────────────────────────────────────
    replied_leads = [l for l in leads if l.status in ("replied", "hot", "booked")]
    reply_count = 0
    for lead in replied_leads:
        is_hot = lead.status in ("hot", "booked") or random.random() < 0.3
        is_neg = not is_hot and random.random() < 0.15
        if is_hot:
            body = random.choice(HOT_REPLIES)
        elif is_neg:
            body = random.choice(NEG_REPLIES)
        else:
            body = random.choice(NEUTRAL_REPLIES)
        r = Reply(
            lead_id=lead.id, body=body,
            source="sms",
            received_at=lead.created_at + timedelta(days=random.randint(2, 20)),
            is_hot=is_hot,
        )
        db.add(r)
        reply_count += 1
    db.flush()
    print(f"  Replies: {reply_count} created")

    # ── Outcomes ────────────────────────────────────────────────────────
    booked_leads = [l for l in leads if l.status == "booked"]
    outcome_count = 0
    for lead in booked_leads:
        sold = random.random() < 0.6
        outcome = LeadOutcome(
            lead_id=lead.id,
            resulted_in_sale=sold,
            notes="Demo outcome — seeded automatically.",
            created_at=lead.created_at + timedelta(days=random.randint(5, 30)),
        )
        db.add(outcome)
        outcome_count += 1
    db.flush()
    print(f"  Outcomes: {outcome_count} created ({sum(1 for l in booked_leads)} booked leads)")

    db.commit()
    print(f"\n✅ Done! Seeded {org.name} with {num_leads} leads, {msg_count} messages, {reply_count} replies.")
    print("   Log in as one of the org advisors to see the data in charts and reports.\n")


def main():
    parser = argparse.ArgumentParser(description="Seed demo data into a BookaBoost sub-account.")
    parser.add_argument("--org-id", help="Organization ID to seed")
    parser.add_argument("--list-orgs", action="store_true", help="List available org IDs and exit")
    parser.add_argument("--leads", type=int, default=120, help="Number of leads to create (default 120)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list_orgs:
            list_orgs(db)
        elif args.org_id:
            seed(db, args.org_id, args.leads)
        else:
            parser.print_help()
    finally:
        db.close()

if __name__ == "__main__":
    main()
