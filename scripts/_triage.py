"""What do the reachable tenant routes actually RETURN to a brand-sales user?

A 200 is not automatically a leak. If organization_id is NULL and the query
renders IS NULL against a NOT NULL column, the body is empty and the route is
useless-but-harmless. If the body has rows in it, that is a different story.
This prints the bodies so the difference is visible rather than assumed.
"""
import os, shutil, sys, tempfile, json
from datetime import datetime

TMP = tempfile.mkdtemp(prefix="triage_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient
from app.main import app
from app.deps import SessionLocal, engine
from app.models.models import Base, Platform, Organization, User, Lead
from app.models.sales_models import (BrandSalesOrg, Membership,
                                     SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER)
from app.services.auth_service import hash_password

PW = "ProbeTest!2026"
Base.metadata.create_all(bind=engine)
db = SessionLocal()
db.add(Platform(id="plt", name="P", slug="p")); db.flush()
db.add(Organization(id="org-cust", name="Customer Cemetery", slug="cust", platform_id="plt"))
db.add(BrandSalesOrg(id="bso", platform_id="plt", name="S", slug="s", timezone="America/Chicago"))
db.flush()
for uid, email, role, org in (("u-mgr", "mgr@probe.test", "advisor", None),
                              ("u-adv", "adv@probe.test", "advisor", "org-cust")):
    db.add(User(id=uid, organization_id=org, email=email, full_name=uid,
                password_hash=hash_password(PW), role=role,
                must_change_password=False, is_active=True))
db.flush()
db.add(Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                  scope_id="bso", role=ROLE_SALES_MANAGER, is_active=True))
db.add(Lead(id="lead-cust", organization_id="org-cust", first_name="Customer",
            last_name="Lead", phone="+15550000009", status="new",
            created_at=datetime.utcnow()))
db.commit(); db.close()

with TestClient(app) as c:
    t = c.post("/auth/login", data={"username": "mgr@probe.test", "password": PW})
    h = {"Authorization": "Bearer " + t.json()["access_token"]}
    for m, p in [("GET", "/billing/plans"), ("GET", "/campaigns"),
                 ("GET", "/campaigns/history"), ("GET", "/campaigns/purposes"),
                 ("GET", "/campaigns/builder/preview"),
                 ("GET", "/crm/contacts"), ("GET", "/leads/demo-request"),
                 ("GET", "/settings/profile"), ("GET", "/settings/appointment-types"),
                 ("GET", "/god/ops/won-queue")]:
        r = c.request(m, p, headers=h)
        body = r.text
        try:
            j = r.json()
            if isinstance(j, list):
                shape = "list len=%d" % len(j)
            elif isinstance(j, dict):
                shape = "dict keys=%s" % list(j.keys())[:8]
                for k, v in j.items():
                    if isinstance(v, list):
                        shape += "  %s:len=%d" % (k, len(v))
            else:
                shape = type(j).__name__
        except Exception:
            shape = "non-json"
        print("%-6s %-34s %s   %s" % (m, p, r.status_code, shape))
        print("        body: %s" % body[:220].replace("\n", " "))

    # Does the seller see the CUSTOMER's lead anywhere?
    print("\ncustomer lead 'lead-cust' visible in any of the above:",
          any("lead-cust" in c.request(m, p, headers=h).text
              for m, p in [("GET", "/crm/contacts"), ("GET", "/campaigns"),
                           ("GET", "/leads/demo-request")]))

    # Writes: what happens to a settings PATCH from an org-less user?
    r = c.patch("/settings/profile", headers=h, json={"full_name": "SELLER EDITED"})
    print("\nPATCH /settings/profile ->", r.status_code, r.text[:200])
    d = SessionLocal()
    try:
        u = d.query(User).filter(User.id == "u-mgr").first()
        o = d.query(User).filter(User.id == "u-adv").first()
        print("  seller's own name now:", u.full_name)
        print("  customer advisor's name untouched:", o.full_name)
    finally:
        d.close()

shutil.rmtree(TMP, ignore_errors=True)
