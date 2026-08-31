"""GATE 26 - Workspaces is a real doorway, and context is explicit.

Before this, a brand was a LABEL derived from whichever customer was selected -
never a place the owner could stand. So `/sales` returned EVERY brand's sales
org for a god_admin. With one brand seeded that looked like a sensible default;
the day a second brand existed it would have blended two companies' pipelines
onto one screen under one brand's name, silently.

This gate answers, with two brands actually present:

  1. Does entering a brand NARROW /sales to that brand alone?
  2. Does a neutral owner still see everything, so nothing was taken away?
  3. Does the context trail read AdvisorFlow -> Brand -> Customer?
  4. Does entering a brand create a membership? (It must not.)
  5. Does a neutral owner get a CLEAR REFUSAL instead of a phantom record?
  6. Are god cross-platform access, super_admin scoping and customer isolation
     all still intact?

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="wsdoor_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                             # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, Organization, User     # noqa: E402
from app.models.sales_models import BrandSalesOrg                    # noqa: E402
from app.services.auth_service import hash_password                  # noqa: E402

PW = "ProbeTest!2026"
FAILS, PASSED = [], []


def ok(label, good, detail=""):
    print("  %s %s%s" % ("ok  " if good else "FAIL", label,
                         ("\n         -> " + str(detail)[:200]) if detail else ""))
    (PASSED if good else FAILS).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro",
                         domain="app.evosyspro.live", accent_color="#087cff",
                         logo_initial="E"),
                Platform(id="plt-bb", name="BookaBoost", slug="bookaboost",
                         domain="app.bookaboost.live", accent_color="#c9973d",
                         logo_initial="BB")])
    db.flush()
    db.add_all([
        Organization(id="org-restland", name="Restland", slug="restland",
                     platform_id="plt-evo"),
        Organization(id="org-bbcust", name="Harbor Chapel", slug="harbor",
                     platform_id="plt-bb"),
    ])
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                      name="EvoSys Pro Sales", slug="evosyspro-sales"),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb",
                      name="BookaBoost Sales", slug="bookaboost-sales"),
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

    mk("u-god", "god@probe.test", "Owner", "god_admin")
    mk("u-sa-evo", "sa.evo@probe.test", "Super Evo", "super_admin",
       org="org-restland", platform="plt-evo")
    mk("u-oa", "oa@probe.test", "Org Admin", "org_admin", org="org-restland")
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def memberships(uid):
    from app.models.sales_models import Membership
    db = SessionLocal()
    try:
        return db.query(Membership).filter(Membership.user_id == uid).count()
    finally:
        db.close()


def main():
    print("=" * 78)
    print("GATE 26 - WORKSPACES DOORWAY AND EXPLICIT CONTEXT")
    print("=" * 78)
    build()

    from app.services.sales_access import sales_org_ids
    from app.services import platform_owner as po

    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        sa_evo = token(c, "sa.evo@probe.test")
        oa = token(c, "oa@probe.test")

        # ── 1. the brand context endpoint ───────────────────────────────────
        section("entering a brand")
        before = memberships("u-god")
        r = c.post("/god/platform/context/brand/plt-evo", headers=god, json={})
        ok("POST /context/brand/{id} succeeds", r.status_code == 200,
           "%s %s" % (r.status_code, r.text[:160]))
        body = r.json() if r.status_code == 200 else {}
        ok("   it returns the brand header, not the org header",
           body.get("header_name") == "X-Brand-Override"
           and body.get("header_value") == "plt-evo", body.get("header_name"))
        ok("   the level is 'brand', a place that did not exist before",
           (body.get("context") or {}).get("level") == "brand",
           (body.get("context") or {}).get("level"))
        ok("   the trail reads AdvisorFlow -> EvoSys Pro",
           (body.get("context") or {}).get("trail") == ["AdvisorFlow", "EvoSys Pro"],
           (body.get("context") or {}).get("trail"))
        ok("   NO MEMBERSHIP IS CREATED",
           body.get("memberships_before") == body.get("memberships_after")
           == before == memberships("u-god"),
           "%s -> %s" % (body.get("memberships_before"), body.get("memberships_after")))
        r = c.post("/god/platform/context/brand/plt-nonexistent", headers=god, json={})
        ok("   an unknown brand is a 404", r.status_code == 404, r.status_code)

        # ── 2. the trail grows to three, it does not replace ────────────────
        section("entering a customer inside that brand")
        r = c.get("/god/platform/context", headers={**god, "X-Brand-Override": "plt-evo",
                                                   "X-Org-Override": "org-restland"})
        ctx = r.json() if r.status_code == 200 else {}
        ok("trail reads AdvisorFlow -> EvoSys Pro -> Restland",
           ctx.get("trail") == ["AdvisorFlow", "EvoSys Pro", "Restland"], ctx.get("trail"))
        ok("   level is customer", ctx.get("level") == "customer", ctx.get("level"))
        ok("   and it is not neutral", ctx.get("is_neutral") is False, ctx.get("is_neutral"))

        r = c.get("/god/platform/context", headers={**god, "X-Brand-Override": "plt-bb"})
        ctx = r.json() if r.status_code == 200 else {}
        ok("a brand alone reads AdvisorFlow -> BookaBoost",
           ctx.get("trail") == ["AdvisorFlow", "BookaBoost"], ctx.get("trail"))
        ok("   and is NOT neutral - standing in a brand is a context",
           ctx.get("is_neutral") is False, ctx.get("is_neutral"))

        r = c.get("/god/platform/context", headers=god)
        ctx = r.json() if r.status_code == 200 else {}
        ok("with nothing selected the owner is neutral",
           ctx.get("is_neutral") is True and ctx.get("level") == "platform", ctx)

    # ── 3. THE HEADLINE: /sales narrows to the selected brand ───────────────
    section("two brands' pipelines cannot blend")
    db = SessionLocal()
    owner = db.query(User).filter(User.id == "u-god").first()

    all_ids = set(sales_org_ids(owner, db))
    ok("a NEUTRAL owner still sees every brand - nothing was taken away",
       all_ids == {"bso-evo", "bso-bb"}, all_ids)

    owner._selected_brand_id = "plt-evo"
    evo_only = set(sales_org_ids(owner, db))
    ok("inside EvoSys Pro, /sales sees ONLY EvoSys Pro's sales org",
       evo_only == {"bso-evo"}, evo_only)
    ok("   BookaBoost's pipeline is not in it", "bso-bb" not in evo_only, evo_only)

    owner._selected_brand_id = "plt-bb"
    bb_only = set(sales_org_ids(owner, db))
    ok("inside BookaBoost, /sales sees ONLY BookaBoost's sales org",
       bb_only == {"bso-bb"}, bb_only)
    ok("   EvoSys Pro's pipeline is not in it", "bso-evo" not in bb_only, bb_only)

    ok("selecting a brand NARROWS, never widens",
       evo_only < all_ids and bb_only < all_ids and not (evo_only & bb_only))
    db.close()

    # ── 4. a neutral owner is refused, not guessed at ───────────────────────
    section("no phantom records from a context-less owner")
    from fastapi import HTTPException
    neutral = User(id="u-god", email="god@probe.test", role="god_admin",
                   organization_id=None)
    try:
        po.tenant_write_org_id(neutral)
        ok("tenant_write_org_id refuses a neutral owner", False, "it returned a value")
    except HTTPException as e:
        ok("tenant_write_org_id refuses a neutral owner", e.status_code == 409, e.status_code)
        ok("   and the message says what to select",
           "customer" in str(e.detail).lower(), str(e.detail)[:120])
    try:
        po.brand_write_platform_id(neutral)
        ok("brand_write_platform_id refuses a neutral owner", False, "it returned a value")
    except HTTPException as e:
        ok("brand_write_platform_id refuses a neutral owner", e.status_code == 409, e.status_code)
        ok("   and names the brand as the missing context",
           "brand" in str(e.detail).lower(), str(e.detail)[:120])

    picked = User(id="u-god", email="g@p.test", role="god_admin", organization_id=None)
    picked._selected_brand_id = "plt-bb"
    ok("...but an owner who HAS picked a brand is allowed",
       po.brand_write_platform_id(picked) == "plt-bb")

    admin_src = open(os.path.join(REPO, "app/routers/admin_router.py"), encoding="utf-8").read()
    ok("POST /admin/users no longer invents an org-NULL identity",
       "organization_id=_tenant_write_org_id(current_user)," in admin_src)
    prop_src = open(os.path.join(REPO, "app/routers/proposal_router.py"), encoding="utf-8").read()
    ok("the two nullable proposal writes are guarded",
       prop_src.count("_tenant_write_org_id(current_user)") >= 2
       and "organization_id=current_user.organization_id" not in prop_src)

    # ── 5. nothing else moved ───────────────────────────────────────────────
    section("nothing was taken away")
    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        sa_evo = token(c, "sa.evo@probe.test")
        oa = token(c, "oa@probe.test")
        for org in ("org-restland", "org-bbcust"):
            r = c.get("/org-settings/", params={"org_id": org}, headers=god)
            ok("god still reaches %s across brands" % org, r.status_code == 200, r.status_code)
        r = c.get("/org-settings/", params={"org_id": "org-restland"}, headers=sa_evo)
        ok("same-brand super_admin still reaches their own customer",
           r.status_code == 200, r.status_code)
        r = c.get("/org-settings/", params={"org_id": "org-bbcust"}, headers=sa_evo)
        ok("cross-brand super_admin is still blocked", r.status_code == 404, r.status_code)
        r = c.get("/org-settings/", headers=oa)
        ok("a customer org_admin still reads their own org", r.status_code == 200, r.status_code)

    # ── 6. the doorway is data-driven ───────────────────────────────────────
    section("the selector is driven by platform records")
    ws = open(os.path.join(REPO, "frontend/src/pages/god/Workspaces.jsx"), encoding="utf-8").read()
    ok("brands come from the platform overview endpoint",
       "/god/platform/overview" in ws)
    ok("customers come from the per-brand endpoint",
       "/god/platform/brands/${b.id}/customers" in ws)
    ok("each brand renders in its OWN accent and mark",
       "b.accent_color" in ws and "b.logo_initial" in ws)
    # Strip comments first. Both files explain the hardcoding they REPLACED,
    # and naming a brand in that explanation is documentation, not a literal
    # the UI renders - a check that cannot tell those apart is a check that
    # punishes writing down why.
    def code_only(t):
        import re as _re
        t = _re.sub(r"/\*.*?\*/", "", t, flags=_re.S)
        return "\n".join(l for l in t.split("\n")
                          if not l.strip().startswith("//"))
    ok("no brand is named in the rendered code",
       "EvoSys" not in code_only(ws) and "BookaBoost" not in code_only(ws))
    shell = open(os.path.join(REPO, "frontend/src/pages/GodShell.jsx"), encoding="utf-8").read()
    ok("the hardcoded EvoSys sales jump is gone",
       "EvoSys Pro brand sales" not in code_only(shell))
    ok("   and no jump names a brand at all",
       "EvoSys" not in code_only(shell) and "BookaBoost" not in code_only(shell))
    ok("Workspaces is the doorway", "/god/workspaces" in shell)
    banner = open(os.path.join(REPO, "frontend/src/components/ContextBanner.jsx"),
                  encoding="utf-8").read()
    ok("the banner renders the server's trail", "ctx.trail" in banner)
    ok("   with Switch workspace and Return to God Mode",
       "Switch workspace" in banner and "Return to God Mode" in banner)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILS:
        print("\nFAILURES (%d):" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
    else:
        print("\nWORKSPACES DOORWAY HOLDS - context is explicit, nothing was removed.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
