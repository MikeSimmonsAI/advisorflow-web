"""Local-only run of the real backend for Lane F verification.

* Throwaway SQLite file in the scratchpad — never a real database.
* Startup hooks are skipped (uvicorn --lifespan off), so no schedulers run.
* Every outbound path is replaced by a recorder that only logs:
  video meetings, calendar sync, prospect invitations, email, Twilio.
* Synthetic people only: example.test emails, 555-01xx phone numbers.
"""
import os, sys, json, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = sys.argv[1]
DB = os.path.join(HERE, "local.db")
OUTBOX = os.path.join(HERE, "outbox.log")
os.environ.update({
    "DATABASE_URL": "sqlite:///" + DB,
    "JWT_SECRET": "local-only-secret-not-used-anywhere-32chars!!",
    "BOOKING_BASE_URL": "http://127.0.0.1:4173",
    "CORS_EXTRA_ORIGINS": "http://127.0.0.1:4173,http://localhost:4173,http://127.0.0.1:4174",
    "WEB_PUSH_AUTO_KEYS": "0",
    "RELAY_ENABLED": "0",
    "PUBLIC_BASE_URL": "http://127.0.0.1:4173",
})
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, REPO)
if os.path.exists(DB):
    os.remove(DB)
open(OUTBOX, "w").close()


def record(kind):
    def _rec(*a, **k):
        with open(OUTBOX, "a") as f:
            f.write(json.dumps({"at": dt.datetime.utcnow().isoformat(), "blocked": kind,
                                "args": [str(x)[:80] for x in a[1:3]]}) + "\n")
        return None
    return _rec


import app.models.registry  # noqa
from app.models.models import Base
from app.deps import engine, SessionLocal
Base.metadata.create_all(bind=engine)

# ── outbound: recorders only ──────────────────────────────────────────────
import app.services.appointment_invites as apinvite
import app.services.email_service as email_service
for name in dir(apinvite):
    if name.startswith("send_"):
        setattr(apinvite, name, record("appointment_invites." + name))
for name in dir(email_service):
    if name.startswith("send"):
        setattr(email_service, name, record("email_service." + name))
from app.routers import sales_scheduling_router as ssr
ssr.apmeet.ensure_meeting = record("video.ensure_meeting")
ssr.apsync.sync_appointment = record("calendar.sync_appointment")
try:
    import twilio.rest
    def _refuse(*a, **k):
        raise RuntimeError("Twilio blocked in local run")
    twilio.rest.Client = _refuse
except Exception:
    pass

# ── synthetic data ────────────────────────────────────────────────────────
from app.models.models import Platform, User
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity, OpportunityEvent
from app.services.auth_service import hash_password
from app.services.meeting_roles import ensure_meeting_types

db = SessionLocal()
plat = Platform(name="EvoSys Pro", slug="evosys-pro-local")
db.add(plat); db.commit()
brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosys-sales-local",
                      timezone="America/Chicago")
db.add(brand); db.commit()


def person(email, name, role):
    u = User(organization_id=None, email=email, password_hash=hash_password("LocalTest!1"),
             full_name=name, role="advisor", must_change_password=False)
    db.add(u); db.commit()
    db.add(Membership(user_id=u.id, scope_type="brand_sales_org", scope_id=brand.id,
                      role=role, is_active=True))
    db.commit()
    return u


rep = person("rep@example.test", "Riley Testrep", "sales_rep")
mgr = person("manager@example.test", "Morgan Testmanager", "sales_manager")
other = person("rep2@example.test", "Casey Otherrep", "sales_rep")
ensure_meeting_types(db, brand.id)

today = dt.date.today()
def due(days):
    d = today + dt.timedelta(days=days)
    return dt.datetime(d.year, d.month, d.day, 12, 0, 0)

PEOPLE = [
    ("Testfamily Alvarez", "Alvarez Test Roofing", "prospect", -2, "Call to introduce EvoSys Pro"),
    ("Testfamily Brooks", "Brooks Test Realty", "prospect", 0, "Initial introduction"),
    ("Testfamily Chen", "Chen Test Insurance", "contacted", 0, "Follow up on discussion"),
    ("Testfamily Diaz", "Diaz Test Mortgage", "discovery", 0, "Discovery follow up"),
    ("Testfamily Evans", "Evans Test Holdings", "contacted", 3, "Check interest"),
    ("Testfamily Foster", "Foster Test Partners", "demo_build", 1, "Prepare product demo"),
    ("Testfamily Garcia", "Garcia Test Group", "demo_proposal", 0, "Review proposal"),
    ("Testfamily Hughes", "Hughes Test Ventures", "closing", 5, "Send agreement"),
    ("Testfamily Ito", "Ito Test Logistics", "prospect", None, None),
    ("Testfamily Jones", "Jones Test Services", "won", None, None),
]
for i, (contact, company, stage, d, nxt) in enumerate(PEOPLE):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=rep.id, company_name=company,
                    contact_name=contact, phone="(214) 555-01%02d" % (10 + i),
                    email="%s@example.test" % contact.split()[1].lower(), stage=stage,
                    status="won" if stage == "won" else "open",
                    next_action=nxt, next_action_due_at=due(d) if d is not None else None,
                    won_at=dt.datetime.utcnow() if stage == "won" else None)
    db.add(o); db.commit()
    db.add(OpportunityEvent(opportunity_id=o.id, event_type="created",
                            summary="Lead added (local test record)", actor_user_id=rep.id))
    db.commit()
# A deal owned by someone else — must never appear on Riley's screens.
db.add(Opportunity(brand_sales_org_id=brand.id, owner_user_id=other.id, company_name="Other Rep Test Co",
                   contact_name="Testfamily Zed", phone="(214) 555-0199", email="zed@example.test",
                   stage="prospect", status="open", next_action="Other rep's call", next_action_due_at=due(-1)))
db.commit()
# The manager's own book: one deal due today, so their personal numbers differ
# visibly from the team's.
db.add(Opportunity(brand_sales_org_id=brand.id, owner_user_id=mgr.id, company_name="Manager Own Test Co",
                   contact_name="Testfamily Manager", phone="(214) 555-0188", email="mgrdeal@example.test",
                   stage="discovery", status="open", next_action="Manager's own call", next_action_due_at=due(0)))
db.commit()
db.close()
print("SEEDED", flush=True)

import uvicorn
from app.main import app
uvicorn.run(app, host="127.0.0.1", port=8000, lifespan="off", log_level="warning")
