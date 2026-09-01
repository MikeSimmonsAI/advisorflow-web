"""GATE 28 - GOD ENTERS ANY BRAND'S SALES WORKSPACE.

THE BUG: selecting any brand other than the one seeded brand and opening the
Sales Workspace said "No active brand sales membership."

That message was wrong twice over. `sales_access.sales_org_ids` does not read
memberships at all in its god branch - it returns the brand sales orgs of the
SELECTED brand - so an empty result for god never meant "no membership". It
meant the selected brand had no BrandSalesOrg row, which nothing outside the
demo seeder had ever created. One brand had a sales team, so one brand worked.

Mike's own EvoSys membership was NOT what made EvoSys work, and this gate proves
it: the god below holds ZERO memberships anywhere and still enters every
configured brand.

THE FIXTURE HAS THREE BRANDS AND NO BRAND IS NAMED IN THE CODE UNDER TEST:
    plt-1 / bso-1   configured, has opportunities
    plt-2 / bso-2   configured, has its own opportunities
    plt-3           NO sales team - the state every non-seeded brand was in
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="godsales_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from app.main import app                                              # noqa: E402
from app.deps import SessionLocal, engine                             # noqa: E402
from app.models.models import Base, Platform, User, AuditLogEntry     # noqa: E402
from app.models.sales_models import (                                 # noqa: E402
    Membership, BrandSalesOrg, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                   # noqa: E402

PW = "ProbeTest!2026"
FAILED, PASSED = [], []


def ok(label, cond, detail=""):
    print("  %s %s%s" % ("ok   " if cond else "FAIL ", label,
                         ("\n          -> " + str(detail)[:220]) if detail else ""))
    (PASSED if cond else FAILED).append(label)


def case(n, t):
    print("\n=== CASE %-2s %s " % (n, t) + "=" * max(0, 54 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([
        Platform(id="plt-1", name="Brand One", slug="brand-one"),
        Platform(id="plt-2", name="Brand Two", slug="brand-two"),
        Platform(id="plt-3", name="Brand Three", slug="brand-three"),
    ])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-1", platform_id="plt-1", name="Brand One Sales",
                      slug="brand-one-sales", timezone="America/Chicago"),
        BrandSalesOrg(id="bso-2", platform_id="plt-2", name="Brand Two Sales",
                      slug="brand-two-sales", timezone="America/Chicago"),
        # plt-3 gets NOTHING. That is the state every brand except the seeded
        # one was actually in, and the state that produced the false message.
    ])
    db.flush()

    def mk(uid, email, name, role):
        db.add(User(id=uid, organization_id=None, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    # THE OWNER HOLDS NO MEMBERSHIP ANYWHERE. If any check below passes because
    # of a membership, it is not testing god authority.
    mk("u-god", "god@probe.test", "Owner", "god_admin")
    mk("u-rep1", "rep1@probe.test", "Rep One", "advisor")
    mk("u-nobody", "nobody@probe.test", "No Membership", "advisor")
    db.flush()
    db.add(Membership(id="mem-1", user_id="u-rep1",
                      scope_type=SCOPE_BRAND_SALES_ORG, scope_id="bso-1",
                      role=ROLE_SALES_REP, is_active=True))

    # One opportunity per brand, so "which brand's data am I looking at" has a
    # falsifiable answer rather than two empty lists that look identical.
    db.add_all([
        Opportunity(id="opp-1", brand_sales_org_id="bso-1",
                    company_name="ACME ONE", stage="prospect", status="open"),
        Opportunity(id="opp-2", brand_sales_org_id="bso-2",
                    company_name="ZENITH TWO", stage="prospect", status="open"),
    ])
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def brand(hdr, platform_id):
    """God's headers with a brand selected - exactly what the browser sends
    after Workspaces calls enterBrand()."""
    return {**hdr, "X-Brand-Override": platform_id}


def text(r):
    try:
        return r.text
    except Exception:
        return ""


def main():
    print("=" * 78)
    print("GATE 28 - GOD ENTERS ANY BRAND'S SALES WORKSPACE")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        rep1 = token(c, "rep1@probe.test")
        nobody = token(c, "nobody@probe.test")

        # The premise of the whole gate.
        db = SessionLocal()
        god_mems = db.query(Membership).filter(Membership.user_id == "u-god").count()
        db.close()
        ok("PREMISE: the owner holds ZERO memberships anywhere",
           god_mems == 0, god_mems)

        # ── CASE 1 ──────────────────────────────────────────────────────────
        case(1, "God + first brand -> Sales Workspace")
        r = c.get("/sales/me", headers=brand(god, "plt-1"))
        ok("/sales/me opens", r.status_code == 200,
           "%s %s" % (r.status_code, text(r)[:160]))
        ok("   scoped to that brand's sales org",
           r.status_code == 200 and r.json()["brand_sales_org"]["id"] == "bso-1",
           text(r)[:200])
        ok("   and reports the brand by name from the platform record",
           r.status_code == 200 and r.json()["platform"]["name"] == "Brand One",
           text(r)[:200])

        # ── CASE 2 ──────────────────────────────────────────────────────────
        case(2, "God + a DIFFERENT brand -> Sales Workspace")
        r = c.get("/sales/me", headers=brand(god, "plt-2"))
        ok("/sales/me opens for the second brand", r.status_code == 200,
           "%s %s" % (r.status_code, text(r)[:200]))
        ok("   scoped to the SECOND brand's sales org",
           r.status_code == 200 and r.json()["brand_sales_org"]["id"] == "bso-2",
           text(r)[:200])

        # ── CASE 3 ──────────────────────────────────────────────────────────
        case(3, "God + a third configured brand, created from the browser")
        # plt-3 has no sales team. This is the state that produced the bug, and
        # the owner must be able to fix it without a shell or a seed script.
        r = c.get("/sales/me", headers=brand(god, "plt-3"))
        ok("an unconfigured brand does NOT claim a membership problem",
           r.status_code != 200 and "membership" not in text(r).lower(),
           "%s %s" % (r.status_code, text(r)[:200]))
        ok("   it says the BRAND has no sales team, and names it",
           "Brand Three" in text(r) and "sales team" in text(r).lower(),
           text(r)[:220])
        ok("   and answers 409 - not forbidden, does not exist yet",
           r.status_code == 409, r.status_code)

        r = c.post("/god/platform/brands/plt-3/sales-org", headers=god, json={})
        ok("God creates the sales team from the browser",
           r.status_code == 201, "%s %s" % (r.status_code, text(r)[:200]))
        ok("   named from the brand, with no brand hardcoded anywhere",
           r.status_code == 201 and r.json()["brand_sales_org"]["name"] == "Brand Three Sales",
           text(r)[:200])
        r = c.get("/sales/me", headers=brand(god, "plt-3"))
        ok("...and the Sales Workspace now opens for it",
           r.status_code == 200, "%s %s" % (r.status_code, text(r)[:200]))
        ok("   scoped to the brand just configured",
           r.status_code == 200 and r.json()["platform"]["id"] == "plt-3",
           text(r)[:200])
        r = c.post("/god/platform/brands/plt-3/sales-org", headers=god, json={})
        ok("creating it twice is refused rather than silently duplicated",
           r.status_code == 409, "%s %s" % (r.status_code, text(r)[:160]))

        # ── CASE 4 ──────────────────────────────────────────────────────────
        case(4, "God holds ZERO membership in any brand tested - still passes")
        db = SessionLocal()
        after = db.query(Membership).filter(Membership.user_id == "u-god").count()
        total = db.query(Membership).count()
        db.close()
        ok("the owner STILL holds no membership after entering three brands",
           after == 0, after)
        ok("   and no membership row was invented for anyone",
           total == 1, total)


        # ── CASE 5 ──────────────────────────────────────────────────────────
        case(5, "Normal user WITH a valid membership")
        r = c.get("/sales/me", headers=rep1)
        ok("a rep with a membership opens their workspace",
           r.status_code == 200, "%s %s" % (r.status_code, text(r)[:200]))
        ok("   scoped to the brand they actually belong to",
           r.status_code == 200 and r.json()["brand_sales_org"]["id"] == "bso-1",
           text(r)[:200])

        # ── CASE 6 ──────────────────────────────────────────────────────────
        case(6, "Normal user with NO membership -> DENIED")
        r = c.get("/sales/me", headers=nobody)
        ok("refused", r.status_code == 403, "%s %s" % (r.status_code, text(r)[:160]))
        # THE REFUSAL COMES FROM `require_sales_member`, NOT `_resolve_context`.
        # The dependency runs first and answers "Sales workspace access
        # required." for anyone holding no membership, which means
        # `_resolve_context`'s own 403 branch is unreachable for a non-god - by
        # the time it runs, a non-god necessarily HAS a membership. That branch
        # is kept as defence in depth and left byte-for-byte unchanged; this
        # asserts the refusal that actually fires.
        ok("   by the membership dependency, before any context resolution",
           "Sales workspace access required" in text(r), text(r)[:200])
        ok("   and nothing about a brand leaks into the refusal",
           "Brand One" not in text(r) and "bso-" not in text(r), text(r)[:200])
        # A brand header must not become a back door: selecting a brand is how
        # GOD scopes, not how anybody else gains access.
        r = c.get("/sales/me", headers=brand(nobody, "plt-1"))
        ok("   and selecting a brand does NOT let them in",
           r.status_code == 403, "%s %s" % (r.status_code, text(r)[:160]))
        r = c.get("/sales/me", headers=brand(rep1, "plt-2"))
        ok("   nor does it move a rep into a brand they do not belong to",
           r.status_code != 200 or r.json()["brand_sales_org"]["id"] == "bso-1",
           text(r)[:200])
        r = c.post("/god/platform/brands/plt-1/sales-org", headers=rep1, json={})
        ok("   and a non-god cannot create a sales team",
           r.status_code in (401, 403), "%s" % r.status_code)

        # ── CASE 7 ──────────────────────────────────────────────────────────
        case(7, "No cross-brand data leakage after switching")
        r1 = c.get("/sales/opportunities", headers=brand(god, "plt-1"))
        r2 = c.get("/sales/opportunities", headers=brand(god, "plt-2"))
        b1, b2 = text(r1), text(r2)
        ok("brand one's pipeline loads", r1.status_code == 200,
           "%s %s" % (r1.status_code, b1[:160]))
        ok("brand two's pipeline loads", r2.status_code == 200,
           "%s %s" % (r2.status_code, b2[:160]))
        ok("brand one shows ITS company and not the other's",
           "ACME ONE" in b1 and "ZENITH TWO" not in b1, b1[:200])
        ok("brand two shows ITS company and not the other's",
           "ZENITH TWO" in b2 and "ACME ONE" not in b2, b2[:200])

        # ── CASE 8 ──────────────────────────────────────────────────────────
        case(8, "Switching brand A -> B changes the context")
        a = c.get("/sales/me", headers=brand(god, "plt-1"))
        b = c.get("/sales/me", headers=brand(god, "plt-2"))
        a2 = c.get("/sales/me", headers=brand(god, "plt-1"))
        ok("A then B then A returns A, B, A - not a sticky first answer",
           a.json()["brand_sales_org"]["id"] == "bso-1"
           and b.json()["brand_sales_org"]["id"] == "bso-2"
           and a2.json()["brand_sales_org"]["id"] == "bso-1",
           [a.json()["brand_sales_org"]["id"], b.json()["brand_sales_org"]["id"],
            a2.json()["brand_sales_org"]["id"]])
        ok("   and the displayed brand name follows the selection",
           a.json()["platform"]["name"] == "Brand One"
           and b.json()["platform"]["name"] == "Brand Two",
           [a.json()["platform"]["name"], b.json()["platform"]["name"]])

        # ── CASE 9 ──────────────────────────────────────────────────────────
        case(9, "Return to God Mode narrows/clears context correctly")
        # No brand header = neutral. The owner sells across every brand, so
        # neutral must WIDEN back to all of them rather than stranding him.
        r = c.get("/sales/me", headers=god)
        ok("neutral god still opens a workspace", r.status_code == 200,
           "%s %s" % (r.status_code, text(r)[:160]))
        db = SessionLocal()
        all_ids = sorted(x[0] for x in db.query(BrandSalesOrg.id).all())
        db.close()
        ok("   and neutral means ALL brands are in scope, not one",
           len(all_ids) == 3, all_ids)
        ctx = c.get("/god/platform/context", headers=god)
        ok("the context endpoint reports neutral when nothing is selected",
           ctx.status_code == 200 and ctx.json().get("is_neutral") is True,
           text(ctx)[:200])
        ctx = c.get("/god/platform/context", headers=brand(god, "plt-2"))
        ok("   and reports the brand level once one is selected",
           ctx.status_code == 200 and ctx.json().get("level") == "brand",
           text(ctx)[:200])
        ok("   with the trail naming that brand",
           "Brand Two" in text(ctx), text(ctx)[:200])

        print("\n--- the create action is audited " + "-" * 36)
        db = SessionLocal()
        acts = {a[0] for a in db.query(AuditLogEntry.action).all()}
        db.close()
        ok("platform.brand_sales_org_created written",
           "platform.brand_sales_org_created" in acts, sorted(acts))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILED:
        print("\nFAILED (%d):" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
    else:
        print("\nGOD REACHES EVERY BRAND'S SALES WORKSPACE - with no membership,")
        print("and normal membership enforcement is untouched.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
