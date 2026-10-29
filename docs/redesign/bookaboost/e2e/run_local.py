"""LOCAL ONLY - the real backend on a throwaway SQLite file, synthetic data.

Used to take the BookaBoost Program Access mockup screenshots of the real
built UI. Nothing here can reach a real database or person:
  * the database is a new SQLite file next to this script, deleted first;
  * startup hooks are off (uvicorn --lifespan off), so no schedulers run;
  * email / SMS / calendar / video senders are replaced by a recorder.
All logins are @example.com, all names are synthetic.

Usage: python -I run_local.py /path/to/advisorflow-web [port]
Password for every login: LocalTest!1
  bb-admin@example.com  BookaBoost admin: BookaBoost Wholesale workspace admin
                        + BookaBoost Back Office manager + a God-granted preview
  bb-empty@example.com  BookaBoost login with nothing assigned (empty state)
  ops@example.com       EvoSys operator (for the EvoSys before/after check)
  owner@example.com     god_admin
"""
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8010
DB = os.path.join(HERE, "local.db")
os.environ.update({
    "DATABASE_URL": "sqlite:///" + DB,
    "JWT_SECRET": "local-only-secret-not-used-anywhere-32chars!!",
    # the app already allows http://localhost:5173 and :5174 (frontends below)
    "WEB_PUSH_AUTO_KEYS": "0",
    "RELAY_ENABLED": "0",
    "PUBLIC_BASE_URL": "http://localhost:5173",
})
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
sys.path.insert(0, REPO)
if os.path.exists(DB):
    os.remove(DB)


def _blocked(kind):
    def _rec(*a, **k):
        print("[blocked outbound]", kind, flush=True)
        return None
    return _rec


import app.models.registry  # noqa: E402,F401
from app.models.models import Base  # noqa: E402
from app.deps import engine, SessionLocal  # noqa: E402
Base.metadata.create_all(bind=engine)

import app.services.appointment_invites as apinvite  # noqa: E402
import app.services.email_service as email_service  # noqa: E402
for _n in dir(apinvite):
    if _n.startswith("send_"):
        setattr(apinvite, _n, _blocked("invite." + _n))
for _n in dir(email_service):
    if _n.startswith("send"):
        setattr(email_service, _n, _blocked("email." + _n))
try:
    import twilio.rest

    def _refuse(*a, **k):
        raise RuntimeError("Twilio blocked in local run")
    twilio.rest.Client = _refuse
except Exception:
    pass

from app.models.demo_site_models import DemoSite  # noqa: E402
from app.models.models import Organization, Platform, User  # noqa: E402
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402

db = SessionLocal()
evo = Platform(name="EvoSys Pro", slug="evosyspro")
bab = Platform(name="BookaBoost", slug="bookaboost")
db.add_all([evo, bab])
db.commit()
evo_sales = BrandSalesOrg(platform_id=evo.id, name="EvoSys Pro Sales", slug="evosys-sales-local", timezone="America/Chicago")
bab_sales = BrandSalesOrg(platform_id=bab.id, name="BookaBoost Sales", slug="bookaboost-sales-local", timezone="America/Chicago")
db.add_all([evo_sales, bab_sales])
db.commit()


def org(name, slug, industry, plat, feats):
    o = Organization(name=name, slug=slug, plan="trial", industry=industry, is_active=True,
                     platform_id=plat.id, enabled_features=json.dumps(feats))
    db.add(o)
    db.commit()
    return o


def user(email, name, role="advisor", home=None):
    u = User(organization_id=home.id if home else None, email=email, password_hash=hash_password("LocalTest!1"),
             full_name=name, role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def m(u, st, sid, role):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


evo_host = org("EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", "generic", evo,
               ["leads", "reports", "users", "wholesale_real_estate"])
energy = org("Example Light & Power", "example-light-power", "energy", evo, ["leads", "reports", "users"])
funeral = org("Example Memorial Park", "example-memorial", "funeral", evo, ["leads", "reports", "users"])
bab_ws = org("BookaBoost Wholesale", "bookaboost-wholesale-local", "real_estate", bab,
             ["wholesale_workspace", "wholesale_real_estate", "leads", "users"])

# EvoSys operator (before/after check of the EvoSys launcher)
ops = user("ops@example.com", "Pat Operator", role="org_admin", home=evo_host)
m(ops, "brand_sales_org", evo_sales.id, "sales_manager")
for o in (evo_host, energy, funeral):
    m(ops, "customer_org", o.id, "org_admin")
m(ops, "product", "wholesale", "member")

# BookaBoost admin
bb = user("bb-admin@example.com", "Jordan Example", role="org_admin", home=bab_ws)
m(bb, "customer_org", bab_ws.id, "org_admin")
m(bb, "brand_sales_org", bab_sales.id, "sales_manager")

# BookaBoost login with nothing assigned yet (empty state)
empty = user("bb-empty@example.com", "Casey Example")
m(empty, "platform", bab.id, "member")   # belongs to BookaBoost, no business yet

god = user("owner@example.com", "Owner Example", role="god_admin")

COUNTRYSIDE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Countryside demo (local test)</title>
<style>body{margin:0;font-family:Georgia,serif;background:#1f3b2a;color:#f3ead2}
header{padding:28px 40px;border-bottom:2px solid #c8a24a;display:flex;justify-content:space-between;align-items:center}
h1{color:#d8b25a;margin:0;font-size:30px}main{padding:40px;max-width:900px}
button{background:#c8a24a;color:#1f3b2a;border:0;padding:12px 20px;font-weight:bold;border-radius:6px;cursor:pointer}
.card{background:#264a35;border:1px solid #3f6b50;border-radius:10px;padding:20px;margin:16px 0}</style></head>
<body><header><h1>Countryside Land Partners</h1><button onclick="document.getElementById('m').textContent='Thanks - this is a local test page.'">Book a walk</button></header>
<main><p>LOCAL TEST PAGE - stands in for the stored demo. It keeps its own green and gold look.</p>
<div class="card">Acreage listings · Owner outreach · Closings</div><p id="m"></p></main></body></html>"""

deal = Opportunity(brand_sales_org_id=evo_sales.id, owner_user_id=ops.id, company_name="Countryside Land Partners",
                   contact_name="Sam Example", stage="demo_proposal", status="open", proposal_status="sent",
                   phone="(214) 555-0150", email="deal@example.com")
db.add(deal)
db.commit()
import secrets  # noqa: E402
db.add(DemoSite(opportunity_id=deal.id, brand_sales_org_id=evo_sales.id, slot="platform",
                title="Countryside Land Partners - platform demo", html=COUNTRYSIDE_HTML,
                token=secrets.token_urlsafe(32), is_active=True,
                expires_at=datetime.utcnow() + timedelta(days=45)))
db.commit()

# The God-granted preview - only if this checkout has the preview-grant table
# (feature/countryside-preview-grant). LOCAL THROWAWAY DATABASE ONLY.
try:
    from app.models.demo_site_models import DealPreviewGrant
    db.add(DealPreviewGrant(opportunity_id=deal.id, grantee_user_id=bb.id, grantee_platform_id=bab.id,
                            granted_by=god.id, reason="Local mockup only",
                            expires_at=datetime.utcnow() + timedelta(days=30)))
    db.commit()
    print("local grant added", flush=True)
except ImportError:
    print("no preview-grant table in this checkout", flush=True)
db.close()
print("SEEDED", flush=True)

import uvicorn  # noqa: E402
from app.main import app  # noqa: E402
uvicorn.run(app, host="127.0.0.1", port=PORT, lifespan="off", log_level="warning")
