"""GATE 32 - a demo mockup is hosted on the brand's own domain, and revocable.

WHAT THIS GUARDS. The proposal upload path refuses HTML on purpose: a
customer-supplied page served same-origin with an app that keeps its session
token in localStorage is stored XSS. Demo hosting stores HTML deliberately, so
the two properties that keep it safe have to be asserted, not assumed:

  1. the HTML can only be AUTHORED through the authenticated sales API, scoped
     to a brand the author actually belongs to;
  2. the public page renders it sandboxed with no same-origin access.

(2) lives in the frontend, so this gate reads the component and fails if the
sandbox attribute is ever weakened - the one change that would silently undo
the whole protection.

The rest is link hygiene: one live link per deal, revocation that takes effect
immediately, expiry honoured, and no oracle that tells a stranger whether a
token they guessed was merely expired.

Nothing here touches production. Every id below is invented.
"""
import io
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="demosite_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                            # noqa: E402
from app.deps import SessionLocal, engine                           # noqa: E402
from app.models.models import Base, Platform, User                  # noqa: E402
from app.models.sales_models import (                               # noqa: E402
    BrandSalesOrg, Membership, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.demo_site_models import DemoSite                    # noqa: E402
from app.services.auth_service import hash_password                 # noqa: E402

PW = "ProbeTest!2026"
FAIL, PASSED = [], []
PAGE = "<h1>Concept</h1><script>document.title='live'</script>"


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt", name="EvoSys Pro", slug="evosyspro"))
    db.add(Platform(id="plt2", name="BookaBoost", slug="bookaboost"))
    db.flush()
    db.add(BrandSalesOrg(id="bso", platform_id="plt", name="EvoSys Pro Sales",
                         slug="evo-demo-gate", timezone="America/Chicago"))
    db.add(BrandSalesOrg(id="bso2", platform_id="plt2", name="BookaBoost Sales",
                         slug="bab-demo-gate", timezone="America/Chicago"))
    db.flush()
    for uid, email in (("u-rep", "rep@probe.test"), ("u-mgr", "mgr@probe.test"),
                       ("u-other", "other@probe.test")):
        db.add(User(id=uid, organization_id=None, email=email, full_name=uid,
                    password_hash=hash_password(PW), role="advisor",
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))
    db.flush()
    db.add(Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso", role=ROLE_SALES_REP, is_active=True))
    db.add(Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso", role=ROLE_SALES_MANAGER, is_active=True))
    # Belongs to a DIFFERENT brand entirely.
    db.add(Membership(user_id="u-other", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso2", role=ROLE_SALES_MANAGER, is_active=True))
    db.flush()
    db.add(Opportunity(id="opp-1", brand_sales_org_id="bso", owner_user_id="u-rep",
                       company_name="Prospect One", stage="demo_build", status="open"))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def row(demo_id):
    db = SessionLocal()
    try:
        return db.query(DemoSite).filter(DemoSite.id == demo_id).first()
    finally:
        db.close()


def opp():
    db = SessionLocal()
    try:
        return db.query(Opportunity).filter(Opportunity.id == "opp-1").first()
    finally:
        db.close()


def main():
    print("=" * 78)
    print("GATE 32 - DEMO MOCKUPS: OUR DOMAIN, SANDBOXED, REVOCABLE")
    print("=" * 78)
    build()

    section("THE PUBLIC PAGE RENDERS IT SANDBOXED - the load-bearing check")
    src = io.open(os.path.join(REPO, "frontend/src/pages/portal/DemoSite.jsx"),
                  encoding="utf-8").read()
    m = re.search(r'sandbox=["\']([^"\']+)["\']', src)
    check("the frame declares a sandbox at all", m is not None, m and m.group(1))
    flags = (m.group(1) if m else "").split()
    check("scripts run, so the mockup stays interactive",
          "allow-scripts" in flags, flags)
    check("ALLOW-SAME-ORIGIN IS ABSENT - it cannot read our token",
          "allow-same-origin" not in flags, flags)
    check("...and the srcDoc is the only content path",
          "srcDoc" in src and "dangerouslySetInnerHTML" not in src)

    with TestClient(app) as c:
        rep = token(c, "rep@probe.test")
        other = token(c, "other@probe.test")

        section("A REP PUBLISHES A DEMO AND GETS A LINK ON OUR DOMAIN")
        r = c.post("/sales/opportunities/opp-1/demo-site",
                   json={"title": "Prospect One - site concept", "html": PAGE},
                   headers=rep)
        check("published", r.status_code == 201, (r.status_code, r.text[:160]))
        body = r.json()
        url = body["url"]
        demo_id = body["demo"]["id"]
        check("the link is on the BRAND's domain, not a third party's",
              "evosyspro" in url and "claude.ai" not in url, url)
        check("...and carries a token, not a guessable id",
              "/demo/" in url and len(url.rsplit("/", 1)[-1]) >= 32,
              url.rsplit("/", 1)[-1][:12] + "...")
        check("the deal now points at it",
              opp().demo_url == url, opp().demo_url)
        check("...and Demo Build says ready",
              opp().demo_status == "ready" and opp().demo_ready_at is not None,
              opp().demo_status)
        check("the list never carries the markup",
              "html" not in body["demo"], sorted(body["demo"].keys()))

        tok = url.rsplit("/", 1)[-1]

        section("A PROSPECT OPENS IT WITH NO ACCOUNT")
        pr = c.get("/public/demo/" + tok)
        check("no JWT required", pr.status_code == 200, pr.status_code)
        check("the page comes back whole", pr.json()["html"] == PAGE)
        check("the view is counted", row(demo_id).view_count == 1,
              row(demo_id).view_count)
        check("...and first-seen is stamped once",
              row(demo_id).first_viewed_at is not None)
        c.get("/public/demo/" + tok)
        d = row(demo_id)
        check("a second view increments but does not restamp first-seen",
              d.view_count == 2 and d.first_viewed_at != d.last_viewed_at,
              [d.view_count])

        section("PUBLISHING AGAIN RETIRES THE OLD LINK")
        r2 = c.post("/sales/opportunities/opp-1/demo-site",
                    json={"title": "Version two", "html": "<h1>v2</h1>"},
                    headers=rep)
        check("a second demo publishes", r2.status_code == 201, r2.status_code)
        check("THE FIRST LINK IS DEAD - no two live versions of one pitch",
              c.get("/public/demo/" + tok).status_code == 404)
        tok2 = r2.json()["url"].rsplit("/", 1)[-1]
        check("...and the new one opens", c.get("/public/demo/" + tok2).status_code == 200)

        section("SLOTS - A DEAL CAN CARRY TWO DIFFERENT MOCKUPS AT ONCE")
        rs1 = c.post("/sales/opportunities/opp-1/demo-site",
                     json={"title": "Website concept", "html": "<h1>site</h1>",
                           "slot": "website"}, headers=rep)
        check("a website-slot demo publishes", rs1.status_code == 201, rs1.status_code)
        toks1 = rs1.json()["url"].rsplit("/", 1)[-1]
        check("...and the platform demo IS STILL LIVE - a different shelf",
              c.get("/public/demo/" + tok2).status_code == 200)
        check("...and the website demo opens too",
              c.get("/public/demo/" + toks1).status_code == 200)

        rs2 = c.post("/sales/opportunities/opp-1/demo-site",
                     json={"title": "Website concept v2", "html": "<h1>site2</h1>",
                           "slot": "website"}, headers=rep)
        check("republishing the same slot retires only that slot",
              rs2.status_code == 201 and c.get("/public/demo/" + toks1).status_code == 404,
              [rs2.status_code, c.get("/public/demo/" + toks1).status_code])
        check("...the platform demo is STILL untouched",
              c.get("/public/demo/" + tok2).status_code == 200)

        check("a website demo does NOT claim the deal's demo_url",
              tok2 in (opp().demo_url or ""), opp().demo_url)

        rs3 = c.post("/sales/opportunities/opp-1/demo-site",
                     json={"title": "Junk slot", "html": "<h1>x</h1>",
                           "slot": "../../etc/passwd"}, headers=rep)
        check("AN UNRECOGNISABLE SLOT FALLS BACK TO THE DEFAULT, never a new shelf",
              rs3.status_code == 201 and rs3.json()["demo"]["slot"] == "platform",
              rs3.json().get("demo", {}).get("slot"))
        # Hand the next section a live platform demo: the fallback publish above
        # already retired r2's, so carrying r2 forward would test a dead link.
        r2 = rs3
        tok2 = rs3.json()["url"].rsplit("/", 1)[-1]
        c.post("/sales/demo-sites/%s/revoke" % rs2.json()["demo"]["id"], headers=rep)

        section("REVOCATION IS IMMEDIATE")
        did2 = r2.json()["demo"]["id"]
        rv = c.post("/sales/demo-sites/%s/revoke" % did2, headers=rep)
        check("the rep can revoke", rv.status_code == 200, rv.status_code)
        check("the link stops working at once",
              c.get("/public/demo/" + tok2).status_code == 404)
        check("...and the deal stops advertising a dead link",
              opp().demo_url is None, opp().demo_url)

        section("AN EXPIRED LINK IS CLOSED, AND SAYS NOTHING EXTRA")
        r3 = c.post("/sales/opportunities/opp-1/demo-site",
                    json={"title": "Third", "html": "<p>three</p>"}, headers=rep)
        tok3 = r3.json()["url"].rsplit("/", 1)[-1]
        db = SessionLocal()
        d3 = db.query(DemoSite).filter(DemoSite.token == tok3).first()
        d3.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit(); db.close()
        exp = c.get("/public/demo/" + tok3)
        check("an expired link is refused", exp.status_code == 404, exp.status_code)
        missing = c.get("/public/demo/" + "z" * 43)
        check("...with the SAME answer a never-existed token gets",
              missing.status_code == 404
              and missing.json()["detail"] == exp.json()["detail"],
              exp.json()["detail"])

        section("BRAND BOUNDARY - another brand cannot touch this deal")
        r4 = c.post("/sales/opportunities/opp-1/demo-site",
                    json={"title": "Not yours", "html": "<p>x</p>"}, headers=other)
        check("a manager of another brand cannot publish to it",
              r4.status_code == 404, r4.status_code)
        r5 = c.post("/sales/opportunities/opp-1/demo-site",
                    json={"title": "Fourth", "html": "<p>four</p>"}, headers=rep)
        did4 = r5.json()["demo"]["id"]
        r6 = c.post("/sales/demo-sites/%s/revoke" % did4, headers=other)
        check("...and cannot revoke another brand's demo",
              r6.status_code == 404, r6.status_code)
        check("...and the demo is still live afterwards",
              row(did4).is_live() is True)
        r7 = c.get("/sales/opportunities/opp-1/demo-sites", headers=other)
        check("...and cannot list them", r7.status_code == 404, r7.status_code)

        section("EMPTY AND OVERSIZE ARE REFUSED, NOT STORED")
        for label, payload, why in (
                ("empty html", {"title": "T", "html": "   "}, "no content"),
                ("empty title", {"title": " ", "html": "<p>x</p>"}, "title"),
                ("oversize", {"title": "T", "html": "x" * (2 * 1024 * 1024 + 10)}, "larger")):
            rr = c.post("/sales/opportunities/opp-1/demo-site", json=payload, headers=rep)
            check("%s is refused" % label, rr.status_code == 400,
                  (rr.status_code, rr.text[:110]))

        section("UNKNOWN DEAL, UNKNOWN DEMO")
        check("publishing to a deal that does not exist 404s",
              c.post("/sales/opportunities/nope/demo-site",
                     json={"title": "T", "html": "<p>x</p>"},
                     headers=rep).status_code == 404)
        check("revoking a demo that does not exist 404s",
              c.post("/sales/demo-sites/nope/revoke", headers=rep).status_code == 404)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nHOSTED ON OUR DOMAIN, SANDBOXED FROM OUR SESSION, KILLABLE ON DEMAND.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
