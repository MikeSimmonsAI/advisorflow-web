"""GATE 28 - two real customers, and neither can reach the other.

The acceptance scenario the mission specifies: an SCI-style test customer and an
unrelated one, both provisioned through the supported flow, then every boundary
attacked from both sides.

TESTED BY DIRECT URL, NOT BY NAVIGATION. Hiding a nav item proves nothing, so
every attempt below is an API call made with a valid token for the wrong person,
against a REAL id belonging to the other customer. A guessed id would 404 for
the boring reason and tell us nothing.

404 WHERE EXISTENCE SHOULD NOT BE DISCLOSED. A 403 on another customer's record
confirms that record exists, which is how you enumerate a competitor's customer
list one id at a time. Where the answer is "not yours", the answer is 404.

Nothing here touches production. Every id below is invented, and the SCI records
are fictional test data - no real family, advisor or funeral home appears.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="isolation_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, Organization, User, Lead  # noqa: E402
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                  # noqa: E402

PW = "ProbeTest!2026"
FAIL, PASSED = [], []
IDS = {}


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:220]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def lead_ids(c, headers):
    """Lead ids from GET /leads/.

    The list endpoint returns {"items": [...]}, not {"leads": [...]}. An earlier
    version of this gate looked only for "leads", got [] every time, and
    reported an isolation failure that was really a typo in the test. Checking
    the status and the key explicitly stops that being mistaken for a finding.
    """
    r = c.get("/leads/", headers=headers)
    if r.status_code != 200:
        return r.status_code, None
    body = r.json()
    if isinstance(body, list):
        rows = body
    else:
        rows = body.get("items")
        if rows is None:
            rows = body.get("leads")
        if rows is None:
            raise SystemExit("GET /leads/ shape changed: %s" % list(body)[:8])
    return 200, [x.get("id") for x in rows if isinstance(x, dict)]


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evo-iso"),
                Platform(id="plt-bab", name="BookaBoost", slug="bab-iso")])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                      slug="evo-sales-iso", timezone="America/Chicago"),
        BrandSalesOrg(id="bso-bab", platform_id="plt-bab", name="BookaBoost Sales",
                      slug="bab-sales-iso", timezone="America/Chicago"),
    ])
    db.flush()

    def mk(uid, email, name, role, org=None, platform=None):
        u = User(id=uid, organization_id=org, email=email, full_name=name,
                 password_hash=hash_password(PW), role=role,
                 must_change_password=False, is_active=True,
                 last_login_at=datetime.utcnow() - timedelta(days=1))
        if platform is not None and hasattr(User, "platform_id"):
            u.platform_id = platform
        db.add(u)

    mk("u-god", "god@probe.test", "Platform Owner", "god_admin")
    # Brand-scoped administrator for EvoSys Pro only.
    mk("u-mgr-evo", "mgr.evo@probe.test", "EvoSys Sales Manager", "advisor")
    mk("u-rep-evo", "rep.evo@probe.test", "EvoSys Sales Rep", "advisor")
    db.flush()
    db.add(Membership(user_id="u-mgr-evo", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True))
    db.add(Membership(user_id="u-rep-evo", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def main():
    print("=" * 78)
    print("GATE 28 - TWO CUSTOMERS, FULLY ISOLATED")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")

        section("PROVISION TWO CUSTOMERS THROUGH THE SUPPORTED FLOW")
        for key, name, plat, loc in (
            ("sci", "SCI Test (fictional)", "plt-evo", "Riverside Chapel"),
            ("b", "Unrelated Test Customer", "plt-bab", "Bravo Main Office"),
        ):
            r = c.post("/god/customers", headers=god, json={
                "name": name, "platform_id": plat,
                "primary_location": {"name": loc, "city": "Austin", "state": "TX",
                                     "timezone": "America/Chicago"}})
            IDS[key] = r.json()["customer"]["id"]
            IDS[key + "_loc"] = r.json()["primary_location"]["id"]
            check("provisioned %s" % name, r.status_code == 201, r.status_code)

            r = c.post("/god/customers/%s/users" % IDS[key], headers=god, json={
                "email": "admin.%s@probe-corp.com" % key, "full_name": "%s Admin" % key,
                "role": "org_admin", "location_ids": [IDS[key + "_loc"]]})
            IDS[key + "_admin"] = r.json()["user"]["id"]
            check("...with an admin", r.status_code == 201, r.status_code)

            c.put("/god/customers/%s/features" % IDS[key], headers=god,
                  json={"enabled": ["leads", "booking", "calendar", "campaigns"]})

        # Give each admin a password we know, so they can actually log in.
        db = SessionLocal()
        try:
            for key in ("sci", "b"):
                u = db.query(User).filter(User.id == IDS[key + "_admin"]).first()
                u.password_hash = hash_password(PW)
                u.must_change_password = False
                db.add(Lead(id="lead-%s" % key, organization_id=IDS[key],
                            first_name=key.upper(), last_name="Family",
                            phone="+1555010%04d" % (1 if key == "sci" else 2),
                            status="new", created_at=datetime.utcnow()))
            db.commit()
        finally:
            db.close()

        sci = token(c, "admin.sci@probe-corp.com")
        cb = token(c, "admin.b@probe-corp.com")

        section("CUSTOMER A CANNOT REACH CUSTOMER B (direct URL, real ids)")
        probes = [
            ("GET", "/god/customers/%s" % IDS["b"], None),
            ("GET", "/god/customers/%s/locations" % IDS["b"], None),
            ("GET", "/god/customers/%s/features" % IDS["b"], None),
            ("PUT", "/god/customers/%s/features" % IDS["b"], {"enabled": ["leads"]}),
            ("POST", "/god/customers/%s/users" % IDS["b"],
             {"email": "x@probe-corp.com", "full_name": "X"}),
            ("POST", "/god/customers/%s/activate" % IDS["b"], {}),
            ("POST", "/god/customers/%s/deactivate" % IDS["b"], None),
        ]
        for method, path, body in probes:
            r = c.request(method, path, headers=sci, **({"json": body} if body else {}))
            check("SCI admin refused: %s %s" % (method, path.split("/")[-1]),
                  r.status_code in (401, 403, 404), r.status_code)

        st, ids = lead_ids(c, sci)
        check("SCI admin sees only SCI's leads",
              st == 200 and "lead-sci" in ids and "lead-b" not in ids, "%s %s" % (st, ids))

        st, ids = lead_ids(c, cb)
        check("Customer B admin sees only B's leads",
              st == 200 and "lead-b" in ids and "lead-sci" not in ids, "%s %s" % (st, ids))

        section("CUSTOMER B CANNOT REACH CUSTOMER A")
        for method, path, body in [
            ("GET", "/god/customers/%s" % IDS["sci"], None),
            ("POST", "/god/customers/%s/users" % IDS["sci"],
             {"email": "y@probe-corp.com", "full_name": "Y"}),
        ]:
            r = c.request(method, path, headers=cb, **({"json": body} if body else {}))
            check("B admin refused: %s %s" % (method, path.split("/")[-1]),
                  r.status_code in (401, 403, 404), r.status_code)

        section("A CUSTOMER ADMIN CANNOT REACH BRAND-SALES OPERATIONS")
        for path in ("/sales/me", "/sales/opportunities", "/sales/team",
                     "/god/ops/won-queue", "/god/ops/customer-organizations"):
            r = c.get(path, headers=sci)
            check("customer admin refused: %s" % path,
                  r.status_code in (401, 403, 404), r.status_code)

        section("A SALES REP CANNOT REACH THE CUSTOMER BACK OFFICE")
        rep = token(c, "rep.evo@probe.test")
        for method, path in (("GET", "/god/customers"),
                             ("GET", "/god/customers/%s" % IDS["sci"]),
                             ("GET", "/admin/users"),
                             ("GET", "/admin/orgs"),
                             ("GET", "/leads/"),
                             ("GET", "/god/platform/overview")):
            r = c.request(method, path, headers=rep)
            check("sales rep refused: %s" % path, r.status_code in (401, 403, 404),
                  r.status_code)

        section("A BRAND-SCOPED ADMIN CANNOT REACH AN UNRELATED BRAND")
        mgr = token(c, "mgr.evo@probe.test")
        r = c.get("/sales/opportunities", headers=mgr)
        check("the EvoSys manager can use their OWN brand workspace",
              r.status_code == 200, r.status_code)
        for path in ("/god/customers", "/god/platform/overview",
                     "/god/customers/%s" % IDS["b"]):
            r = c.get(path, headers=mgr)
            check("...but is refused platform/customer authority: %s" % path,
                  r.status_code in (401, 403, 404), r.status_code)

        section("EXISTENCE IS NOT DISCLOSED")
        r = c.get("/god/customers/%s" % IDS["b"], headers=sci)
        r2 = c.get("/god/customers/does-not-exist-at-all", headers=sci)
        check("a real other-customer id and a fake one answer identically",
              r.status_code == r2.status_code, "%s vs %s" % (r.status_code, r2.status_code))

        section("THE OWNER CAN ENTER, OPERATE, AND LEAVE")
        r = c.post("/god/platform/context/customer/%s" % IDS["sci"], headers=god)
        check("owner enters SCI", r.status_code == 200, r.status_code)
        check("...creating no membership",
              r.json()["memberships_before"] == r.json()["memberships_after"],
              r.json())
        in_sci = dict(god, **{"X-Org-Override": IDS["sci"]})
        st, ids = lead_ids(c, in_sci)
        check("...and inside SCI sees SCI's leads only",
              st == 200 and "lead-sci" in ids and "lead-b" not in ids, "%s %s" % (st, ids))
        r = c.post("/god/platform/context/exit", headers=in_sci)
        check("owner exits", r.status_code == 200, r.status_code)
        r = c.get("/god/platform/context", headers=god)
        check("...and is neutral again", r.json()["is_neutral"] is True, r.json())
        db = SessionLocal()
        try:
            n = db.query(Membership).filter(Membership.user_id == "u-god").count()
        finally:
            db.close()
        check("...having gained no membership anywhere", n == 0, "memberships=%s" % n)

        section("FEATURE ENTITLEMENT IS ENFORCED ON THE SERVER")
        c.put("/god/customers/%s/features" % IDS["sci"], headers=god,
              json={"enabled": ["leads"]})
        r = c.get("/campaigns", headers=sci)
        check("a customer without 'campaigns' is refused the campaigns API",
              r.status_code in (402, 403), "%s %s" % (r.status_code, r.text[:140]))
        c.put("/god/customers/%s/features" % IDS["sci"], headers=god,
              json={"enabled": ["leads", "campaigns"]})
        r = c.get("/campaigns", headers=sci)
        check("...and allowed once entitled", r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:140]))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nTWO CUSTOMERS, FULLY ISOLATED - proven by direct URL, not by navigation.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
