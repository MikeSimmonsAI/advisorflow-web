"""
seed_staging.py — populate staging DB with realistic test data.
Run from Render Shell: python seed_staging.py
"""
import os, uuid, bcrypt
from datetime import datetime, timedelta
import random
import psycopg2
from psycopg2.extras import execute_values

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

def uid(): return str(uuid.uuid4())
def pw(p='Newpc!1me'): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
def ago(days=0, hours=0): return datetime.utcnow() - timedelta(days=days, hours=hours)

print("Seeding staging database...")

# ── 1. Organizations ──────────────────────────────────────────────────────────
orgs = [
    (uid(), 'Restland Funeral Home',   'restland-funeral',        'professional', True),
    (uid(), 'Harmony Life Insurance',  'harmony-life-insurance',  'professional', True),
    (uid(), 'EvoSys Pro Demo Org',     'evosys-pro-demo',         'starter',      True),
]
cur.executemany(
    "INSERT INTO organizations (id, name, slug, plan, is_active) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (slug) DO NOTHING",
    orgs
)

cur.execute("SELECT id, name FROM organizations WHERE slug IN ('restland-funeral','harmony-life-insurance','evosys-pro-demo')")
org_rows = {row[1]: row[0] for row in cur.fetchall()}
restland_id = org_rows['Restland Funeral Home']
harmony_id  = org_rows['Harmony Life Insurance']
evosys_id   = org_rows['EvoSys Pro Demo Org']
print(f"  Orgs: {list(org_rows.keys())}")

# ── 2. Advisors ───────────────────────────────────────────────────────────────
advisors = [
    (uid(), restland_id, 'james.hull@restland.test',   'James Hull',     pw(), 'advisor',   True, False, 0, '+14695551001'),
    (uid(), restland_id, 'taffiney.m@restland.test',   'Taffiney Moore', pw(), 'advisor',   True, False, 0, '+14695551002'),
    (uid(), restland_id, 'admin@restland.test',        'Dana Restland',  pw(), 'org_admin', True, False, 0, '+14695551003'),
    (uid(), harmony_id,  'ct@harmonylife.test',        'CT Wilson',      pw(), 'advisor',   True, False, 0, '+14695552001'),
    (uid(), harmony_id,  'meava@harmonylife.test',     'Meava Jackson',  pw(), 'advisor',   True, False, 0, '+14695552002'),
    (uid(), harmony_id,  'admin@harmonylife.test',     'Sam Harmony',    pw(), 'org_admin', True, False, 0, '+14695552003'),
    (uid(), evosys_id,   'demo@evosyspro.test',        'Demo Advisor',   pw(), 'advisor',   True, False, 0, '+14695553001'),
]
cur.executemany(
    """INSERT INTO users
       (id, organization_id, email, full_name, password_hash, role, is_active,
        must_change_password, failed_login_attempts, phone)
       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (email) DO NOTHING""",
    advisors
)

emails = [a[2] for a in advisors]
cur.execute("SELECT id, email, organization_id FROM users WHERE email = ANY(%s)", (emails,))
advisor_map = {row[1]: (row[0], row[2]) for row in cur.fetchall()}
print(f"  Advisors: {len(advisor_map)}")

# ── 3. Leads ─────────────────────────────────────────────────────────────────
first_names = ['Marcus','Diane','Robert','Patricia','Kevin','Sandra','Tyrone','Gloria',
               'Andre','Brenda','Jerome','Cheryl','Darius','Yvonne','Michael','Latoya',
               'James','Shirley','David','Tamika','Carlos','Felicia','Brian','Monique']
last_names  = ['Johnson','Williams','Brown','Davis','Miller','Wilson','Moore','Taylor',
               'Anderson','Thomas','Jackson','White','Harris','Martin','Thompson','Garcia']
tiers   = ['A','A','A','B','B','B','C','C','D']
temps   = ['hot','hot','warm','warm','warm','cold','cold']
statuses= ['new','contacted','appointment_set','appointment_kept','sale','no_answer','not_interested']

def rphone(): return f"+1469555{random.randint(1000,9999)}"
def remail(fn,ln): return f"{fn.lower()}.{ln.lower()}{random.randint(10,99)}@testlead.example"

leads_data = []
for org_id, count in [(restland_id,40),(harmony_id,35),(evosys_id,15)]:
    org_advisors = [v[0] for v in advisor_map.values() if v[1] == org_id]
    if not org_advisors: continue
    for _ in range(count):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        created = ago(days=random.randint(0,90))
        leads_data.append((
            uid(), org_id, fn, ln, rphone(), remail(fn,ln),
            random.choice(tiers), random.choice(temps), random.choice(statuses),
            random.choice(org_advisors), created, created,
        ))

execute_values(cur,
    """INSERT INTO leads
       (id, organization_id, first_name, last_name, phone, email,
        tier, engagement_temperature, status, assigned_to_id, created_at, updated_at)
       VALUES %s ON CONFLICT DO NOTHING""",
    leads_data
)
print(f"  Leads: {len(leads_data)}")

# ── 4. Messages ───────────────────────────────────────────────────────────────
cur.execute("SELECT id, organization_id, assigned_to_id FROM leads WHERE assigned_to_id IS NOT NULL LIMIT 60")
lead_rows = cur.fetchall()

bodies = [
    "Hi, this is James from Restland. Just following up on your pre-arrangement inquiry. Do you have a few minutes this week?",
    "Good morning! We have some new options available that might be a great fit. Would love to connect.",
    "Following up from our conversation last week. Let me know if you have any questions!",
    "Hi there, wanted to make sure you received the information we sent over. Happy to answer any questions.",
    "Hope you're doing well! Just checking in to see if now is a good time to chat.",
]

messages = []
for lead_id, org_id, assigned_to_id in lead_rows:
    for _ in range(random.randint(1,4)):
        messages.append((
            uid(), lead_id, assigned_to_id,
            random.choice(bodies),
            random.choice(['delivered','delivered','delivered','sent','failed']),
            ago(days=random.randint(0,30), hours=random.randint(0,23)),
        ))

execute_values(cur,
    "INSERT INTO messages (id, lead_id, sender_id, body, twilio_status, sent_at) VALUES %s ON CONFLICT DO NOTHING",
    messages
)
print(f"  Messages: {len(messages)}")

conn.commit()
conn.close()
print("\n✅ Done! Staging seeded:")
print("  3 orgs | 7 advisors (password: Newpc!1me) | 90 leads | messages added")
