"""GATE 30 - THE OWNER CONSOLE AND THE CUSTOMER WORKSPACE.

The acceptance test for the Aug 27 2026 redesign of God Mode and the customer
Overview. It exists because the two failures that redesign was correcting are
both invisible to a screenshot:

  A CONTROL THAT GOES NOWHERE. Ten of seventeen God Mode nav entries pointed at
  routes that did not exist, "Enter Organization" from the Command Center used a
  route that established no context, and the whole /god/organizations screen read
  `res.data.orgs` from an API client that returns parsed JSON - so it rendered an
  empty platform no matter how many customers existed. Every one of those looked
  fine and did nothing. So section 3 walks EVERY navigation target in the two
  redesigned surfaces and requires it to resolve to a registered route or to an
  element id on the page it links within.

  A NUMBER WITH NO SOURCE. The approved mockups show an MRR figure, an average
  first-touch time and a "no contact" pipeline stage. None of those exist in this
  schema. Section 1 requires the subsystems with no telemetry to say so rather
  than render green, and section 4 requires the customer Overview to contain no
  hardcoded colour at all - it is white-labelled, and a literal hex would make
  one brand's palette permanent for every brand.

Nothing here touches production. Every id below is invented.
"""
import glob
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="ownerconsole_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "frontend", "src")
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine, get_platform_org_ids      # noqa: E402
from app.models.models import (                                      # noqa: E402
    Base, Platform, Organization, User, Lead, Message,
)
from app.services.auth_service import hash_password                  # noqa: E402
from app.services import platform_owner as po                        # noqa: E402

PW = "ProbeTest!2026"
FAIL, PASSED = [], []
GOD_ORG = po.GOD_PLATFORM_ORG_ID


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def read(*parts):
    p = os.path.join(SRC, *parts)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def strip_comments(code):
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"^\s*//.*$", "", code, flags=re.M)


# ══ fixture ═════════════════════════════════════════════════════════════════

def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evo-oc"),
                Platform(id="plt-bab", name="BookaBoost", slug="bab-oc")])
    db.flush()
    db.add(Organization(id=GOD_ORG, name="AdvisorFlow Platform",
                        slug="advisorflow-platform", plan="god", is_active=True))
    db.add_all([
        Organization(id="org-a", name="Customer A", slug="cust-a",
                     platform_id="plt-evo", is_active=True, plan="trial"),
        Organization(id="org-b", name="Customer B", slug="cust-b",
                     platform_id="plt-bab", is_active=True, plan="trial"),
    ])
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(hours=2)))

    mk("u-god", "god@probe.test", "Platform Owner", "god_admin", org=None)
    mk("u-a-admin", "a-admin@probe.test", "A Admin", "org_admin", org="org-a")
    mk("u-a-adv", "a-adv@probe.test", "A Advisor", "advisor", org="org-a")
    mk("u-b-adv", "b-adv@probe.test", "B Advisor", "advisor", org="org-b")
    db.flush()

    db.add_all([
        Lead(id="lead-a1", organization_id="org-a", first_name="Alpha", last_name="One",
             phone="+15550000021", status="new", assigned_to_id="u-a-adv",
             created_at=datetime.utcnow()),
        Lead(id="lead-a2", organization_id="org-a", first_name="Alpha", last_name="Two",
             phone="+15550000022", status="dnc", assigned_to_id="u-a-adv",
             created_at=datetime.utcnow()),
        Lead(id="lead-b1", organization_id="org-b", first_name="Bravo", last_name="One",
             phone="+15550000023", status="new", assigned_to_id="u-b-adv",
             created_at=datetime.utcnow()),
    ])
    db.flush()
    # One delivered and one failed message, so the messaging health section has
    # a real delivery rate to report rather than an empty window.
    db.add_all([
        Message(id="msg-ok", lead_id="lead-a1", sender_id="u-a-adv", body="hi",
                delivery_status="delivered", sent_at=datetime.utcnow() - timedelta(days=1)),
        Message(id="msg-bad", lead_id="lead-a1", sender_id="u-a-adv", body="hi",
                delivery_status="failed", sent_at=datetime.utcnow() - timedelta(days=1)),
    ])
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def main():
    print("=" * 78)
    print("GATE 30 - THE OWNER CONSOLE AND THE CUSTOMER WORKSPACE")
    print("=" * 78)
    build()
    c = TestClient(app)
    god = token(c, "god@probe.test")
    a_admin = token(c, "a-admin@probe.test")
    b_adv = token(c, "b-adv@probe.test")

    # ══ 1. PLATFORM HEALTH TELLS THE TRUTH ══════════════════════════════════
    section("PLATFORM HEALTH - real conditions, and honest gaps")
    r = c.get("/god/platform-health", headers=god)
    check("the owner can read platform health", r.status_code == 200, r.status_code)
    body = r.json() if r.status_code == 200 else {}
    secs = {s["key"]: s for s in body.get("sections", [])}
    for key in ("messaging", "billing", "jobs", "integrations",
                "customer_activity", "security"):
        check("section present: %s" % key, key in secs)
    allowed = {"ok", "warn", "bad", "off", "no_source"}
    check("every status is one of the allowed values",
          all(s.get("status") in allowed for s in secs.values()),
          [s.get("status") for s in secs.values()])

    # THE LOAD-BEARING ONE. There is no job table. A green tick here would be a
    # lie about the subsystem whose silent failure nobody would notice.
    check("background jobs report NO SOURCE, not health",
          secs.get("jobs", {}).get("status") == "no_source",
          secs.get("jobs", {}).get("status"))
    check("...and name what would have to be built",
          bool(secs.get("jobs", {}).get("needs")),
          secs.get("jobs", {}).get("needs"))
    check("every no_source section names what it needs",
          all(s.get("needs") for s in secs.values() if s.get("status") == "no_source"))

    # And the sections that DO have data must actually use it.
    msg = secs.get("messaging", {})
    check("messaging reports a real delivery figure from receipts",
          msg.get("status") in ("ok", "warn", "bad") and "delivered" in (msg.get("detail") or "")
          or "%" in (msg.get("headline") or ""),
          msg.get("headline"))
    check("...and counts the failed message rather than hiding it",
          "1 failed" in (msg.get("detail") or ""), msg.get("detail"))

    # SILENCE IS NOT HEALTH. Production shipped with 3,589 messages sent and
    # 3,589 still pending — every one awaiting a receipt the status-callback
    # webhook never delivered — and the tile rendered GREEN, because the failure
    # count was zero. It was zero because nothing had been reported, not because
    # nothing failed. A separate fixture proves that case is a warning now.
    db2 = SessionLocal()
    try:
        for m in db2.query(Message).all():
            m.delivery_status = "pending"
        db2.commit()
    finally:
        db2.close()
    m2 = {s["key"]: s for s in c.get("/god/platform-health",
                                     headers=god).json()["sections"]}["messaging"]
    check("messages with NO delivery receipt at all are not scored healthy",
          m2.get("status") != "ok", "%s / %s" % (m2.get("status"), m2.get("headline")))
    check("...and the tile says whether they arrived is unknown",
          "unknown" in (m2.get("detail") or "") and bool(m2.get("needs")),
          m2.get("detail"))
    # Put the fixture back so later sections read the state they were written for.
    db2 = SessionLocal()
    try:
        db2.query(Message).filter(Message.id == "msg-ok").first().delivery_status = "delivered"
        db2.query(Message).filter(Message.id == "msg-bad").first().delivery_status = "failed"
        db2.commit()
    finally:
        db2.close()
    bil = secs.get("billing", {})
    check("billing reports that nothing can be charged",
          bil.get("status") == "bad", bil.get("headline"))

    section("PLATFORM HEALTH is owner-only")
    check("a customer admin is refused",
          c.get("/god/platform-health", headers=a_admin).status_code == 403)
    check("an advisor is refused",
          c.get("/god/platform-health", headers=b_adv).status_code == 403)

    # ══ 2. ONE HUMAN IS ONE ROW ═════════════════════════════════════════════
    section("IDENTITY - one row per human, contexts on the row")
    r = c.get("/god/users?scope=all", headers=god)
    check("the identity list answers the owner", r.status_code == 200, r.status_code)
    users = r.json().get("users", []) if r.status_code == 200 else []
    ids = [u["id"] for u in users]
    emails = [u["email"] for u in users]
    check("no identity appears twice by id", len(ids) == len(set(ids)), ids)
    check("no identity appears twice by email", len(emails) == len(set(emails)))
    check("every row carries a memberships array",
          all(isinstance(u.get("memberships"), list) for u in users))
    a_admin_row = next((u for u in users if u["email"] == "a-admin@probe.test"), None)
    check("a tenant user names their organization",
          a_admin_row and a_admin_row.get("organization_name") == "Customer A",
          a_admin_row and a_admin_row.get("organization_name"))
    check("...and their brand",
          a_admin_row and a_admin_row.get("platform_name") == "EvoSys Pro",
          a_admin_row and a_admin_row.get("platform_name"))
    god_row = next((u for u in users if u["email"] == "god@probe.test"), None)
    check("the owner is marked as control-plane, not as a tenant user",
          god_row and god_row.get("is_internal") is True)
    check("scope=tenant excludes control-plane identities",
          all(u.get("organization_id") for u in
              c.get("/god/users?scope=tenant", headers=god).json()["users"]))
    check("the identity list is owner-only",
          c.get("/god/users?scope=all", headers=a_admin).status_code == 403)

    # ══ 3. A SELECTED CUSTOMER NARROWS THE OWNER ════════════════════════════
    section("CONTEXT - entering a customer narrows what the owner is shown")
    # TWO SEPARATE SESSIONS ON PURPOSE. SQLAlchemy's identity map returns the
    # SAME python object for a second query of the same row inside one session,
    # so setting `_god_all_orgs` for the neutral case would still be sitting on
    # the object used for the entered case, and this check would pass for the
    # wrong reason.
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.id == "u-god").first()
        owner._god_all_orgs = True                 # what get_current_user sets
        wide = get_platform_org_ids(owner, db)
        check("a NEUTRAL owner still scopes to every organization",
              len(wide) >= 3, wide)
    finally:
        db.close()

    db = SessionLocal()
    try:
        # Exactly what get_current_user does when X-Org-Override is present:
        # detach, then assign. No _god_all_orgs flag is set in that branch.
        entered = db.query(User).filter(User.id == "u-god").first()
        db.expunge(entered)
        entered.organization_id = "org-a"
        narrow = get_platform_org_ids(entered, db)
        check("an owner INSIDE a customer scopes to that customer only",
              narrow == ["org-a"], narrow)
    finally:
        db.close()

    # The same claim through the API the customer Overview actually calls.
    in_a = dict(god); in_a["X-Org-Override"] = "org-a"
    r = c.get("/admin/dashboard/funnel", headers=in_a)
    check("the tenant funnel answers an owner inside a customer",
          r.status_code == 200, r.status_code)
    check("...and counts only that customer's leads",
          r.status_code == 200 and r.json().get("total_leads") == 2,
          r.json().get("total_leads") if r.status_code == 200 else r.text[:120])
    r = c.get("/admin/dashboard/funnel", headers=god)
    check("a neutral owner still sees the whole platform",
          r.status_code == 200 and r.json().get("total_leads") >= 3,
          r.json().get("total_leads") if r.status_code == 200 else r.text[:120])

    # ══ 4. EVERY VISIBLE CONTROL GOES SOMEWHERE ═════════════════════════════
    section("NO DEAD CONTROLS - every target resolves")
    app_src = read("App.jsx")
    routes = set(re.findall(r'<Route\s+path="([^"]+)"', app_src))
    check("routes were found in App.jsx", len(routes) > 20, len(routes))

    def route_exists(path):
        """A concrete path matches a registered route, including :params."""
        path = path.split("?")[0].split("#")[0]
        if not path.startswith("/"):
            return True                       # a template literal we cannot resolve
        if path in routes:
            return True
        want = [p for p in path.split("/") if p]
        for r_ in routes:
            have = [p for p in r_.split("/") if p]
            if r_.endswith("/*") and want[:len(have) - 1] == have[:-1]:
                return True
            if len(have) != len(want):
                continue
            if all(h.startswith(":") or h == w for h, w in zip(have, want)):
                return True
        return False

    # Every God Mode surface plus the customer Overview. Targets are collected
    # from navigate(), the tool/module tables and the KPI definitions alike,
    # because a dead button is dead whichever of those produced it.
    SURFACES = [
        ("pages", "GodShell.jsx"), ("pages", "GodCommandCenter.jsx"),
        ("pages", "GodOrganizations.jsx"), ("pages", "GodControlAudit.jsx"),
        ("pages", "god", "GodTools.jsx"), ("pages", "god", "ProductStatus.jsx"),
        ("pages", "god", "OrgCommandTable.jsx"), ("pages", "god", "GodUsers.jsx"),
        ("pages", "god", "ExceptionQueue.jsx"), ("pages", "god", "ExecutiveSummary.jsx"),
        ("pages", "Overview.jsx"),
    ]
    # Anchors the two redesigned pages link within themselves.
    anchors = set()
    for parts in SURFACES:
        anchors |= set(re.findall(r'id="([\w-]+)"', read(*parts)))

    dead = []
    for parts in SURFACES:
        code = strip_comments(read(*parts))
        if not code:
            dead.append("%s MISSING" % parts[-1])
            continue
        targets = set()
        targets |= set(re.findall(r"""navigate\(\s*['"](/[^'"]*)['"]""", code))
        targets |= set(re.findall(r"""go\(\s*['"]([/#][^'"]*)['"]""", code))
        targets |= set(re.findall(r"""to:\s*['"]([/#][^'"]*)['"]""", code))
        targets |= set(re.findall(r"""path:\s*['"](/[^'"]*)['"]""", code))
        for t in targets:
            if t.startswith("#"):
                if t[1:] not in anchors:
                    dead.append("%s -> %s (no such element id)" % (parts[-1], t))
            elif not route_exists(t):
                dead.append("%s -> %s (no route)" % (parts[-1], t))
    check("no control targets a route or anchor that does not exist",
          not dead, "; ".join(dead[:6]) or "none")

    # ══ 5. THE CUSTOMER WORKSPACE STAYS WHITE-LABEL ═════════════════════════
    section("WHITE LABEL - the customer app has no hardcoded palette")
    # Comments are stripped first — the file explains WHY a literal hex would be
    # wrong, and a check that failed on its own rationale would be pressure to
    # delete the explanation.
    ov_css = re.sub(r"/\*.*?\*/", "", read("pages", "Overview.css"), flags=re.S)
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", ov_css)
    check("Overview.css contains no literal colour",
          not hexes,
          "the same markup renders EvoSys Pro dark and BookaBoost cream: " + str(hexes[:6]))
    ov = strip_comments(read("pages", "Overview.jsx"))
    js_hexes = re.findall(r"['\"]#[0-9a-fA-F]{3,8}['\"]", ov)
    check("Overview.jsx contains no literal colour", not js_hexes, js_hexes[:6])

    # The tokens those files depend on must actually exist. 148 references to
    # four undefined variables is why the old dashboard had no borders at all:
    # an unresolved var() invalidates the whole declaration.
    index_css = read("index.css")
    for tok in ("--border:", "--bg-hover:", "--accent:", "--text-muted:"):
        check("token defined: %s" % tok.rstrip(":"), tok in index_css)

    # ══ 6. NO NUMBER WITHOUT A SOURCE ═══════════════════════════════════════
    section("NO INVENTED METRICS")
    # The mockups show these. The schema has no source for either, so neither
    # may appear as a rendered figure.
    for banned, why in (
        ("avg first touch", "no first-touch timestamp is recorded"),
        ("AVG FIRST TOUCH", "no first-touch timestamp is recorded"),
    ):
        check("the Overview does not show '%s'" % banned, banned not in ov, why)
    cc = strip_comments(read("pages", "GodCommandCenter.jsx"))
    check("the Command Center renders no currency figure",
          not re.search(r"['\"]\$[\d,]", cc),
          "MRR has no source until the invoices table exists")
    es = read("pages", "god", "ExecutiveSummary.jsx")
    check("...and the MRR tile explicitly renders NoSource",
          "MRR" in es and "NoSource" in es)

    # ══ 7. THE DESIGN SYSTEM WAS EXTENDED, NOT DUPLICATED ═══════════════════
    section("ONE DESIGN SYSTEM")
    god_css = read("pages", "god", "GodStyles.jsx")
    for cls in (".gm-stat", ".gm-health", ".gm-q-item", "table.gm-table",
                ".gm-pill", ".gm-chip", ".gm-act"):
        check("the God sheet defines %s" % cls, cls in god_css)
    stray = [os.path.basename(p) for p in glob.glob(os.path.join(SRC, "pages", "god", "*.css"))
             if os.path.basename(p) != "GodOps.css"]
    check("no second God Mode stylesheet was added", not stray, stray)
    check("the retired hierarchy tree is gone, not left dead",
          not os.path.exists(os.path.join(SRC, "pages", "god", "HierarchyTree.jsx")))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
        print("=" * 78)
        return 1
    print("\nTHE OWNER CONSOLE IS HONEST - every control resolves, every number has a source.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
