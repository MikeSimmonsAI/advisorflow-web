"""READ-ONLY PROBE: what can a sales REP reach, and what can a MANAGER reach?

Not a deploy gate. This answers one question for the Sales Workspace audit: for
every route the workspace exposes, does a `sales_rep` get a screen or a 403, and
does a `sales_manager` get more? It builds its own throwaway SQLite fixture, so
it touches nothing real and can be run any time.

A 404 on another brand's id is a PASS, not a miss: the brand-sales routes return
404 rather than 403 for records outside the caller's brand, deliberately, so a
manager of one brand cannot learn that a record in another brand exists.

ORDERING MATTERS AND IT BIT ME. The first version of this file ran the whole
probe list per actor, manager first. The manager's run included a reassignment,
which moved the rep's only deal to the manager - so the rep's run that followed
reported "0 opportunities" and a 403 on his own deal. That reads exactly like a
critical defect and was entirely my fixture. Reads and writes are now separated:
every read runs against identical state, and each write gets its own throwaway
opportunity so no actor's write can colour another actor's result.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="salesprobe_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, User                   # noqa: E402
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, Membership, Opportunity, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                  # noqa: E402

PW = "ProbeTest!2026"
ACTORS = ["MGR", "REP", "ANON"]


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"))
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                      name="EvoSys Pro Sales", slug="evosyspro-sales"),
        BrandSalesOrg(id="bso-other", platform_id="plt-evo",
                      name="Other Brand Sales", slug="other-sales"),
    ])
    db.flush()

    def mk(uid, email, name):
        db.add(User(id=uid, organization_id=None, email=email, full_name=name,
                    password_hash=hash_password(PW), role="advisor",
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-mgr", "mgr@probe.test", "Team Manager")
    mk("u-rep", "rep@probe.test", "Blake Stand-In")
    db.flush()
    db.add_all([
        Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
    ])
    db.flush()

    now = datetime.utcnow()

    def opp(oid, owner, company):
        db.add(Opportunity(id=oid, brand_sales_org_id="bso-evo",
                           owner_user_id=owner, company_name=company,
                           stage="discovery", status="open",
                           created_at=now, stage_changed_at=now))

    # Read fixtures: one the rep owns, one the manager owns. Never mutated.
    opp("opp-rep", "u-rep", "Rep's Deal")
    opp("opp-mgr", "u-mgr", "Manager's Deal")
    # A deal in a brand NEITHER of them sells, to prove the 404-not-403 rule.
    db.add(Opportunity(id="opp-foreign", brand_sales_org_id="bso-other",
                       owner_user_id=None, company_name="Other Brand's Deal",
                       stage="discovery", status="open",
                       created_at=now, stage_changed_at=now))
    # Write fixtures: one per actor, so a write by one cannot colour another.
    for a in ACTORS:
        opp("opp-w-%s" % a.lower(), "u-rep", "Write Target %s" % a)
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s %s" % (email, r.status_code, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


READS = [
    ("/sales/me", "workspace context"),
    ("/sales/my-day", "My Day"),
    ("/sales/opportunities", "pipeline board"),
    ("/sales/opportunities?owner_user_id=u-mgr", "pipeline filtered to ANOTHER rep"),
    ("/sales/opportunities/opp-rep", "the REP'S OWN deal"),
    ("/sales/opportunities/opp-mgr", "another rep's deal, by id"),
    ("/sales/opportunities/opp-foreign", "ANOTHER BRAND'S deal (404 = correct)"),
    ("/sales/team", "who sells this brand"),
    ("/sales/packages", "packages"),
    ("/sales/implementations", "Sold / Onboarding"),
    ("/sales/meeting-types", "meeting types"),
    ("/sales/appointments", "appointment list (NO UI CALLS THIS)"),
    ("/sales/availability/me", "My Availability"),
    ("/sales/availability/team", "Team Availability"),
    ("/sales/calendar/connections", "calendar connections"),
    ("/sales/video/status", "video provider status"),
    ("/sales/manager/overview", "TEAM COMMAND"),
    ("/sales/manager/approvals", "approval queue"),
    ("/sales/manager/reps/u-rep", "one rep's book"),
    ("/god/ops/brands", "god: all brands"),
    ("/god/ops/staff", "god: internal staff"),
    ("/god/ops/sales-operations", "god: sales operations"),
    ("/god/ops/customer-organizations", "god: customer tenants"),
    ("/god/ops/implementations", "god: implementations"),
    ("/god/ops/won-queue", "god: won queue"),
    ("/leads", "TENANT: leads"),
    ("/leads/stats", "TENANT: lead stats"),
    ("/reports/revenue-by-period", "TENANT: reports"),
    ("/admin/users", "TENANT: admin users"),
    ("/pipeline/stats", "TENANT: pipeline stats"),
]


def main():
    print("=" * 108)
    print("SALES WORKSPACE PERMISSION PROBE")
    print("=" * 108)
    build()
    with TestClient(app) as c:
        hdrs = {"MGR": token(c, "mgr@probe.test"),
                "REP": token(c, "rep@probe.test"),
                "ANON": {}}

        print("\n--- READS (identical state for every actor) ---")
        print("%-46s %5s %5s %5s   %s" % ("route", "MGR", "REP", "ANON", "what it is"))
        print("-" * 108)
        for path, desc in READS:
            codes = {a: c.get(path, headers=hdrs[a]).status_code for a in ACTORS}
            print("%-46s %5s %5s %5s   %s"
                  % (path[:46], codes["MGR"], codes["REP"], codes["ANON"], desc))

        print("\n--- WRITES (each actor gets its own target row) ---")
        print("%-46s %5s %5s %5s   %s" % ("route", "MGR", "REP", "ANON", "what it is"))
        print("-" * 108)
        for a in ACTORS:
            pass
        codes = {}
        for a in ACTORS:
            codes[a] = c.post("/sales/opportunities/opp-w-%s/reassign" % a.lower(),
                              json={"owner_user_id": "u-mgr"},
                              headers=hdrs[a]).status_code
        print("%-46s %5s %5s %5s   %s"
              % ("POST /sales/opportunities/{id}/reassign",
                 codes["MGR"], codes["REP"], codes["ANON"], "REASSIGN a deal"))

        codes = {a: c.post("/sales/availability/find",
                           json={"duration_minutes": 30},
                           headers=hdrs[a]).status_code for a in ACTORS}
        print("%-46s %5s %5s %5s   %s"
              % ("POST /sales/availability/find", codes["MGR"], codes["REP"],
                 codes["ANON"],
                 "Find team time (422 = reached handler, body incomplete)"))

        print("\n--- record-level scoping, not just status codes ---")
        m = c.get("/sales/opportunities", headers=hdrs["MGR"]).json()
        r = c.get("/sales/opportunities", headers=hdrs["REP"]).json()
        print("  manager sees %d opportunities   is_manager=%s" % (m["total"], m["is_manager"]))
        print("  rep     sees %d opportunities   is_manager=%s" % (r["total"], r["is_manager"]))
        print("  (the brand holds 5 open deals in bso-evo; the rep owns 4 of them)")

        print("\n--- what /sales/me tells the UI it may do ---")
        for a in ("MGR", "REP"):
            p = c.get("/sales/me", headers=hdrs[a]).json()
            print("  %-4s role=%-14s %s" % (a, p["role"], p["permissions"]))

        print("\n--- does a seller actually SEE tenant data, or just get 200 on an empty list? ---")
        for a in ("MGR", "REP"):
            resp = c.get("/leads", headers=hdrs[a])
            body = resp.json() if resp.status_code == 200 else None
            n = len(body) if isinstance(body, list) else (
                body.get("total") if isinstance(body, dict) else "?")
            print("  %-4s GET /leads -> %s, rows=%s" % (a, resp.status_code, n))

    shutil.rmtree(TMP, ignore_errors=True)
    print("\nProbe complete. Nothing outside the throwaway database was touched.")


if __name__ == "__main__":
    main()
