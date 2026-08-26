"""Sales Workspace completion — deploy gate 21.

Proves the three things the completion work claimed, against a real fixture:

  1. ROLE BOUNDARIES. A manager reaches every MY TEAM surface; a rep reaches
     none of them, cannot reassign, cannot filter to another rep, cannot read
     another rep's deal, and gets 404 - not 403 - for another brand's records.

  2. THE HIDDEN BACKEND IS ACTUALLY WIRED. Every capability the audit found
     built-but-unrendered is now reachable AND its payload is non-empty where
     the fixture makes it non-empty: the team appointment list, the six
     proposal queues, the team roster, the reassign endpoint.

  3. TENANT ROUTES REFUSE A SELLER BY AUTHORIZATION. Not by returning an empty
     list because a schema happens to be NOT NULL. And the tenant user they
     were built for is not regressed.

The frontend half is asserted statically at the bottom: a screen that is not
routed, or a nav item pointing at a route that does not exist, is exactly the
class of bug this whole exercise was about.
"""
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="swcomplete_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, Organization, User      # noqa: E402
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, Membership, Opportunity, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                  # noqa: E402

PW = "SmokeTest!2026"
FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def read_src(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8-sig") as fh:
        return fh.read()


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"),
                Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                      name="EvoSys Pro Sales", slug="evosyspro-sales"),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb",
                      name="BookaBoost Sales", slug="bookaboost-sales"),
    ])
    db.add(Organization(id="org-cust", name="Greenland Cemetery",
                        slug="greenland", platform_id="plt-evo"))
    db.flush()

    def mk(uid, email, name, org=None, role="advisor"):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-mgr", "mgr@probe.test", "Team Manager")
    mk("u-rep", "rep@probe.test", "Working Rep")
    mk("u-rep2", "rep2@probe.test", "Second Rep")
    mk("u-bb", "bb@probe.test", "Other Brand Seller")
    mk("u-cust", "cust@probe.test", "Customer Advisor", org="org-cust")
    db.flush()
    db.add_all([
        Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-rep2", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bb", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_MANAGER, is_active=True),
    ])
    db.flush()

    now = datetime.utcnow()

    def opp(oid, owner, company, bso="bso-evo", stage="discovery"):
        db.add(Opportunity(id=oid, brand_sales_org_id=bso, owner_user_id=owner,
                           company_name=company, stage=stage, status="open",
                           created_at=now, stage_changed_at=now))

    opp("opp-rep", "u-rep", "Rep's Deal")
    opp("opp-rep2", "u-rep2", "Second Rep's Deal")
    opp("opp-mgr", "u-mgr", "Manager's Own Deal")
    opp("opp-unowned", None, "Nobody Picked This Up")
    opp("opp-foreign", None, "Other Brand's Deal", bso="bso-bb")
    # Write targets, one per actor, so no actor's write colours another's read.
    for a in ("mgr", "rep", "anon"):
        opp("opp-w-%s" % a, "u-rep", "Write Target %s" % a)
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# ── 1. role boundaries ──────────────────────────────────────────────────────

MANAGER_SURFACES = [
    ("/sales/manager/overview", "Team Command"),
    ("/sales/manager/approvals", "the approval queue"),
    ("/sales/manager/reps/u-rep", "a rep's book"),
    ("/sales/appointments?scope=team", "Team Calendar"),
    ("/sales/opportunities?owner_user_id=u-rep2", "Team Pipeline filtered to a rep"),
]


def test_manager_allowed(c, mgr):
    section("A MANAGER reaches every MY TEAM surface")
    for path, what in MANAGER_SURFACES:
        r = c.get(path, headers=mgr)
        check("manager may open %s" % what, r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:150]))
    r = c.get("/sales/team", headers=mgr)
    check("manager may read the Salespeople roster",
          r.status_code == 200 and len(r.json()) == 3, r.text[:150])
    r = c.get("/sales/implementations", headers=mgr)
    check("manager may read team Won / Onboarding",
          r.status_code == 200 and r.json().get("is_manager") is True, r.text[:200])


def test_rep_denied(c, rep):
    section("A REP reaches none of them")
    for path, what in MANAGER_SURFACES:
        r = c.get(path, headers=rep)
        check("rep is REFUSED %s" % what, r.status_code == 403,
              "%s %s" % (r.status_code, r.text[:150]))

    r = c.get("/sales/opportunities/opp-rep2", headers=rep)
    check("rep cannot open another rep's deal", r.status_code == 403, r.status_code)
    r = c.get("/sales/opportunities/opp-foreign", headers=rep)
    check("another BRAND's deal is 404, not 403 - no existence oracle",
          r.status_code == 404, r.status_code)
    r = c.post("/sales/opportunities/opp-w-rep/reassign",
               json={"owner_user_id": "u-mgr"}, headers=rep)
    check("rep cannot reassign", r.status_code == 403, r.text[:150])
    r = c.get("/sales/implementations", headers=rep)
    check("rep's Won view does not claim manager scope",
          r.status_code == 200 and r.json().get("is_manager") is False, r.text[:200])


def test_rep_own_work(c, rep):
    section("A REP still has their own workspace")
    for path, what in [("/sales/me", "workspace context"),
                       ("/sales/my-day", "My Day"),
                       ("/sales/opportunities", "My Pipeline / Prospects"),
                       ("/sales/opportunities/opp-rep", "their own deal"),
                       ("/sales/availability/me", "My Availability"),
                       ("/sales/availability/team", "Team Availability"),
                       ("/sales/appointments", "their own calendar"),
                       ("/sales/implementations", "their Sold / Onboarding"),
                       ("/sales/team", "who sells this brand")]:
        r = c.get(path, headers=rep)
        check("rep may open %s" % what, r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:120]))

    board = c.get("/sales/opportunities", headers=rep).json()
    ids = {o["id"] for s in board["stages"] for o in s["opportunities"]}
    check("rep's board carries their own deal", "opp-rep" in ids, sorted(ids))
    check("rep's board carries the UNOWNED prospect - never orphaned",
          "opp-unowned" in ids, sorted(ids))
    check("rep's board excludes another rep's deal", "opp-rep2" not in ids, sorted(ids))
    check("rep's board excludes another brand entirely",
          "opp-foreign" not in ids, sorted(ids))
    check("the board tells the UI the rep is not a manager",
          board["is_manager"] is False)
    check("the board names the viewer so My Pipeline can narrow without a 2nd call",
          board.get("viewer_user_id") == "u-rep", board.get("viewer_user_id"))


def test_manager_sells_too(c, mgr):
    section("A MANAGER is also an individual seller")
    board = c.get("/sales/opportunities", headers=mgr).json()
    ids = {o["id"] for s in board["stages"] for o in s["opportunities"]}
    check("manager's board carries the whole brand", {"opp-rep", "opp-rep2",
                                                      "opp-mgr"} <= ids, sorted(ids))
    check("...and still excludes another brand", "opp-foreign" not in ids)
    check("the manager's own id is returned, so My Pipeline can show only theirs",
          board.get("viewer_user_id") == "u-mgr", board.get("viewer_user_id"))
    own = [o["id"] for s in board["stages"] for o in s["opportunities"]
           if o["owner_user_id"] == board["viewer_user_id"]]
    check("narrowing by that id leaves exactly the manager's own deal",
          own == ["opp-mgr"], own)
    r = c.get("/sales/opportunities/opp-mgr", headers=mgr)
    check("manager can work their own deal", r.status_code == 200)


# ── 2. the hidden backend is actually wired ─────────────────────────────────

def test_hidden_now_reachable(c, mgr):
    section("What the audit found hidden is now reachable AND populated")
    r = c.get("/sales/appointments?scope=team", headers=mgr)
    body = r.json()
    check("the appointment list endpoint answers", r.status_code == 200)
    check("...and reports the scope it applied", body.get("scope") == "team", body.get("scope"))
    check("...and confirms manager scope", body.get("is_manager") is True)
    check("...and accepts a date range", c.get(
        "/sales/appointments?scope=team&date_from=2026-01-01&date_to=2026-12-31",
        headers=mgr).status_code == 200)

    ov = c.get("/sales/manager/overview", headers=mgr).json()
    check("the manager overview still carries proposal_queues",
          "proposal_queues" in ov)
    q = ov.get("proposal_queues") or {}
    for key in ("to_finish", "ready_to_send", "recently_viewed",
                "follow_up_required", "expiring", "counts"):
        check("...queue '%s' is present" % key, key in q, sorted(q.keys()))
    check("the overview carries the team roster the screen now reads",
          isinstance(ov.get("team"), list) and len(ov["team"]) == 3,
          ov.get("team"))
    check("...and the rep rollup Salespeople renders",
          isinstance(ov.get("reps"), list) and len(ov["reps"]) == 3)


def test_reassign_round_trip(c, mgr):
    section("Reassignment works end to end and is visible afterwards")
    before = c.get("/sales/opportunities/opp-w-mgr", headers=mgr).json()
    check("the record exposes can_reassign to a manager",
          before.get("can_reassign") is True, before.get("can_reassign"))
    check("...and names the current owner", before.get("owner_user_id") == "u-rep")

    r = c.post("/sales/opportunities/opp-w-mgr/reassign",
               json={"owner_user_id": "u-rep2"}, headers=mgr)
    check("the reassignment succeeds", r.status_code == 200, r.text[:200])

    after = c.get("/sales/opportunities/opp-w-mgr", headers=mgr).json()
    check("the new owner is returned immediately", after.get("owner_user_id") == "u-rep2",
          after.get("owner_user_id"))
    check("...with their name, so the UI can confirm it",
          after.get("owner_name") == "Second Rep", after.get("owner_name"))
    kinds = [e["event_type"] for e in after.get("timeline", [])]
    check("the move is on the permanent timeline, not just a toast",
          any("reassign" in str(k) for k in kinds), kinds[:6])

    # The guard that stops this becoming a way to hide a deal.
    r = c.post("/sales/opportunities/opp-w-mgr/reassign",
               json={"owner_user_id": "u-bb"}, headers=mgr)
    check("a seller from ANOTHER brand cannot be handed the deal",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:150]))


# ── 3. tenant routes refuse a seller by authorization ───────────────────────

def test_tenant_hardening(c, mgr, rep, cust):
    section("Tenant routes refuse a seller by AUTHORIZATION, not by empty result")
    for label, hdr in (("manager", mgr), ("rep", rep)):
        for path in ("/leads", "/pipeline/stats"):
            r = c.get(path, headers=hdr)
            check("a %s is refused %s" % (label, path), r.status_code == 403,
                  "%s %s" % (r.status_code, r.text[:150]))
            if r.status_code == 403:
                check("...and told why, in the seller's own terms" if path == "/leads" else
                      "...and told why (%s)" % path,
                      "brand sales" in r.text.lower(), r.text[:150])

    section("...and the tenant user those routes exist for is NOT regressed")
    for path in ("/leads", "/pipeline/stats"):
        r = c.get(path, headers=cust)
        check("a customer advisor still reaches %s" % path,
              r.status_code == 200, "%s %s" % (r.status_code, r.text[:150]))


# ── 4. the frontend is actually wired ───────────────────────────────────────

def test_frontend_wired():
    section("Every screen is routed, and every nav item points somewhere real")
    app_src = read_src("frontend/src/App.jsx")
    shell = read_src("frontend/src/pages/sales/SalesShell.jsx")

    SCREENS = {
        "TeamCalendar": "frontend/src/pages/sales/TeamCalendar.jsx",
        "TeamProposals": "frontend/src/pages/sales/TeamProposals.jsx",
        "Salespeople": "frontend/src/pages/sales/Salespeople.jsx",
        "Prospects": "frontend/src/pages/sales/Prospects.jsx",
        "ReassignControl": "frontend/src/pages/sales/ReassignControl.jsx",
    }
    for name, path in SCREENS.items():
        check("%s exists on disk" % name, os.path.exists(os.path.join(REPO, path)))
    for name in ("TeamCalendar", "TeamProposals", "Salespeople", "Prospects"):
        check("%s is imported by App.jsx" % name,
              re.search(r"import\s+%s\s+from" % name, app_src) is not None)

    ROUTES = ["/sales", "/sales/pipeline", "/sales/prospects", "/sales/availability",
              "/sales/onboarding", "/sales/team", "/sales/manager", "/sales/calendar",
              "/sales/team-pipeline", "/sales/proposals", "/sales/salespeople"]
    for r in ROUTES:
        check("route %s is registered" % r,
              ('path="%s"' % r) in app_src)

    # The bug this whole exercise was about: a nav item that goes nowhere, or a
    # screen nobody can reach. Assert the two lists agree.
    nav_targets = set(re.findall(r"to:\s*'(/sales[^']*)'", shell))
    for t in sorted(nav_targets):
        check("nav item %s has a route" % t,
              ('path="%s"' % t) in app_src or t == "/sales/reports",
              "nav points at a route App.jsx does not define")
    check("every manager surface appears in the nav",
          {"/sales/manager", "/sales/calendar", "/sales/team-pipeline",
           "/sales/proposals", "/sales/salespeople"} <= nav_targets,
          sorted(nav_targets))

    # Reports is deferred on purpose. It must be visibly deferred, not silently
    # missing and not quietly invented.
    check("Reports is present in the nav", "'Reports'" in shell)
    check("...marked deferred rather than linked to a real screen",
          re.search(r"label:\s*'Reports'[^}]*soon:\s*true", shell, re.S) is not None)
    check("...and no /sales/reports route was invented",
          'path="/sales/reports"' not in app_src)

    # The team pipeline must not have been forked into a second board.
    check("Team Pipeline reuses MyPipeline rather than forking it",
          'MyPipeline scope="team"' in app_src and 'MyPipeline scope="mine"' in app_src)
    check("...and there is no second pipeline component",
          not os.path.exists(os.path.join(REPO, "frontend/src/pages/sales/TeamPipeline.jsx")))

    # The queues must actually be read now.
    tp = read_src("frontend/src/pages/sales/TeamProposals.jsx")
    check("Demos / Proposals READS proposal_queues",
          "proposal_queues" in tp)
    for key in ("to_finish", "ready_to_send", "recently_viewed",
                "follow_up_required", "expiring"):
        check("...and renders the '%s' queue" % key, key in tp)

    tc = read_src("frontend/src/pages/sales/TeamCalendar.jsx")
    check("Team Calendar CALLS /sales/appointments", "/sales/appointments" in tc)
    check("...and uses wall-clock helpers, not raw Date on a naive string",
          "wallTime" in tc and "new Date(a.starts_at)" not in tc)

    sp = read_src("frontend/src/pages/sales/Salespeople.jsx")
    check("Salespeople reads the roster and the rollup",
          "/sales/team" in sp and "/sales/manager/overview" in sp)
    check("...and does not duplicate God user administration",
          "setup-link" not in sp and "/god/ops" not in sp)

    rc = read_src("frontend/src/pages/sales/ReassignControl.jsx")
    check("the reassign control posts to the real endpoint",
          "/reassign" in rc)
    check("...and renders nothing without the server's can_reassign",
          "if (!canReassign) return null" in rc)

    pr = read_src("frontend/src/pages/sales/Prospects.jsx")
    check("Prospects reads brand-sales opportunities", "/sales/opportunities" in pr)
    # Assert on the CALLS it makes, not on whether the word "lead" appears -
    # the first version of this check failed because the file's own comment
    # explains that a Lead is a different thing, which is the opposite of the
    # problem it was looking for.
    calls = re.findall(r"api\.\w+\(\s*['\"`]([^'\"`?]+)", pr)
    tenant = [c for c in calls if not c.startswith("/sales/")]
    check("...and calls nothing outside /sales/ - no tenant lead routes",
          not tenant, tenant)


def main():
    print("=" * 78)
    print("SALES WORKSPACE COMPLETION")
    print("=" * 78)
    build()
    with TestClient(app) as c:
        mgr = token(c, "mgr@probe.test")
        rep = token(c, "rep@probe.test")
        cust = token(c, "cust@probe.test")

        test_manager_allowed(c, mgr)
        test_rep_denied(c, rep)
        test_rep_own_work(c, rep)
        test_manager_sells_too(c, mgr)
        test_hidden_now_reachable(c, mgr)
        test_tenant_hardening(c, mgr, rep, cust)
        # Last: it mutates an owner.
        test_reassign_round_trip(c, mgr)

    test_frontend_wired()

    print("\n" + "=" * 78)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("ALL SALES WORKSPACE COMPLETION CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
