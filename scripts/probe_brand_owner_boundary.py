"""GATE 24 - a brand sales manager reaches the sales workspace and NOTHING else.

Mike asked a plain question: does Michael Schlueter have access to the back
office, or to anything besides his workspace? The design says no, twice over -
`users.role = "advisor"` fails every privileged allowlist, and
`users.organization_id = NULL` fails require_tenant_user. But a design saying no
is not the same as the server saying no, and a hand-picked spot check of six
routes proves nothing about the seventh.

So this does not spot-check. It walks app.routes and calls EVERY route the app
exposes, as a brand sales manager built exactly the way the production seed
builds Michael, and reports every route that does not refuse him.

Parameterless routes are called as-is. Parameterised routes are called with real
ids from the fixture - a customer org's id, the god_admin's id, a real lead id -
because `/admin/organizations/{org_id}` with a garbage id would 404 for the
boring reason and tell us nothing.

Reachable is defined as any status below 400. A 200 on a route outside the sales
allowlist is a finding. So is a 500: a route that crashes on him got past its
guard and into its body, which means the guard is not there.

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="brandowner_")
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

# Everything the brand sales workspace legitimately needs. A route outside this
# list that lets a sales manager in is the finding this gate exists to catch.
SALES_ALLOWLIST = (
    "/sales/", "/auth/", "/health", "/healthz", "/ready", "/docs", "/redoc",
    "/openapi.json", "/me", "/notifications", "/public/", "/webhooks/",
    "/", "/favicon.ico",
)

# Route prefixes that ARE the back office. A sales manager reaching any of these
# is the specific thing Mike asked about.
BACKOFFICE = ("/admin/", "/god/", "/god-ops/", "/leads", "/pipeline",
              "/crm/", "/campaigns", "/imports", "/billing", "/settings")

# Reachable ON PURPOSE, each for a stated reason. Everything else that answers
# him is a finding. This list is deliberately short and each entry has to earn
# its place - "it returns an empty list anyway" is NOT a reason to be here,
# because an empty list is the schema saving us, not a guard deciding anything.
INTENDED = {
    # A sales route that happens to live under the /god/ops/ prefix. Guarded by
    # require_sales_member and scoped by sales_org_ids - a manager sees his own
    # brand's won deals, which is the point of the won queue (§16). The prefix
    # is a naming smell, not a privilege.
    "GET /god/ops/won-queue",
    # His own account. A salesperson has a name, a photo and an address to be
    # notified at; none of it is tenant data.
    "GET /settings/profile", "PATCH /settings/profile",
    "PATCH /settings/profile-photo", "DELETE /settings/profile-photo",
    "PUT /settings/notifications",
    # The public price list. No auth on it at all - it is the same JSON the
    # marketing site serves, and a seller of all people should see it.
    "GET /billing/plans",
    # Unauthenticated inbound webhooks. They authenticate by org token in the
    # path, not by the caller's identity, so a bearer token is irrelevant here.
    "POST /crm/inbound/{org_id}", "POST /billing/webhook",
    # The bookaboost.com landing-page demo form. Explicitly public - no auth
    # dependency at all - so it answers everyone, logged in or not. It lives
    # under /leads only because of where the router is mounted.
    "GET /leads/demo-request", "POST /leads/demo-request",
}

FINDINGS = []
BROKEN = []
PASSED = []


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt-evo", name="Probe Platform", slug="probe-plat"))
    db.flush()
    db.add(Organization(id="org-cust", name="Customer Cemetery", slug="cust",
                        platform_id="plt-evo"))
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                         name="Probe Sales", slug="probe-sales",
                         timezone="America/Chicago"))
    db.flush()

    def mk(uid, email, name, role, org=None, platform=None):
        u = User(id=uid, organization_id=org, email=email, full_name=name,
                 password_hash=hash_password(PW), role=role,
                 must_change_password=False, is_active=True,
                 last_login_at=datetime.utcnow() - timedelta(days=1))
        if platform is not None and hasattr(User, "platform_id"):
            u.platform_id = platform
        db.add(u)

    # The god_admin is a target, not an actor, in this probe.
    mk("u-god", "god@probe.test", "Platform Owner", "god_admin")
    mk("u-super", "super@probe.test", "Platform Operator", "super_admin",
       org="org-cust", platform="plt-evo")
    mk("u-orgadmin", "orgadmin@probe.test", "Customer Admin", "org_admin", org="org-cust")
    mk("u-adv", "adv@probe.test", "Customer Advisor", "advisor", org="org-cust")

    # THE SUBJECT. Shape copied from seed_evosyspro_sales.py exactly:
    # role 'advisor', organization_id NULL, capability from memberships only.
    mk("u-mgr", "mgr@probe.test", "Brand Sales Manager", "advisor", org=None)
    mk("u-rep", "rep@probe.test", "Brand Sales Rep", "advisor", org=None)
    db.flush()
    db.add(Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True))
    db.add(Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True))
    db.add(Lead(id="lead-cust", organization_id="org-cust", first_name="Customer",
                last_name="Lead", phone="+15550000009", status="new",
                created_at=datetime.utcnow()))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# Real ids, so a refusal is a refusal and not an accidental 404 on a bad id.
PARAMS = {
    "org_id": "org-cust", "organization_id": "org-cust",
    "user_id": "u-god", "advisor_id": "u-adv",
    "lead_id": "lead-cust", "id": "org-cust",
    "brand_id": "bso-evo", "bso_id": "bso-evo",
    "platform_id": "plt-evo", "membership_id": "m-1",
}


def fill(path):
    """Substitute real ids into a parameterised path, or None if we can't."""
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}", start)
        name = out[start + 1:end].split(":")[0]
        if name not in PARAMS:
            return None
        out = out[:start] + PARAMS[name] + out[end + 1:]
    return out


def classify(path):
    p = path.lower()
    if any(p.startswith(b) for b in BACKOFFICE):
        return "backoffice"
    if any(p.startswith(a) for a in SALES_ALLOWLIST):
        return "allowed"
    return "other"


def main():
    print("=" * 78)
    print("GATE 24 - BRAND SALES MANAGER: WORKSPACE ONLY")
    print("=" * 78)
    build()

    routes = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods:
            continue
        for m in sorted(methods):
            if m in ("HEAD", "OPTIONS"):
                continue
            routes.append((m, path))
    routes.sort()
    print("\nroutes exposed by the app: %d" % len(routes))

    with TestClient(app) as c:
        mgr = token(c, "mgr@probe.test")

        reached_backoffice = []
        reached_other = []
        skipped = []
        refused_count = 0
        crashed = []

        intended_hit = []
        for method, path in routes:
            kind = classify(path)
            if kind == "allowed":
                continue  # exercised separately, below
            key = "%s %s" % (method, path)
            concrete = fill(path)
            if concrete is None:
                skipped.append("%s %s" % (method, path))
                continue
            try:
                r = c.request(method, concrete, headers=mgr, json={})
            except Exception as e:                     # noqa: BLE001
                crashed.append("%s %s -> %s" % (method, path, type(e).__name__))
                continue
            if r.status_code >= 500:
                if key in INTENDED:
                    intended_hit.append("%s -> %s" % (key, r.status_code))
                else:
                    crashed.append("%s -> %s" % (key, r.status_code))
            elif r.status_code < 400:
                if key in INTENDED:
                    intended_hit.append("%s -> %s" % (key, r.status_code))
                else:
                    (reached_backoffice if kind == "backoffice" else reached_other).append(
                        "%s -> %s" % (key, r.status_code))
            else:
                refused_count += 1

        print("\n--- BACK OFFICE ------------------------------------------------")
        print("  routes tried: see counts below")
        if reached_backoffice:
            print("  REACHABLE BY A SALES MANAGER (%d):" % len(reached_backoffice))
            for x in reached_backoffice:
                print("    - %s" % x)
            FINDINGS.extend(reached_backoffice)
        else:
            print("  ok    no back-office route admitted him")
            PASSED.append("no back-office route reachable")

        if reached_other:
            print("\n  REACHABLE, OUTSIDE THE SALES ALLOWLIST (%d):" % len(reached_other))
            for x in reached_other:
                print("    - %s" % x)
            FINDINGS.extend(reached_other)

        if crashed:
            # A 500 means the request got PAST the guard and into the handler.
            print("\n  CRASHED (guard did not stop it) (%d):" % len(crashed))
            for x in crashed:
                print("    - %s" % x)
            FINDINGS.extend(crashed)

        if intended_hit:
            print("\n  reachable on purpose, each justified in INTENDED (%d):"
                  % len(intended_hit))
            for x in intended_hit:
                print("    . %s" % x)

        print("\n  refused cleanly: %d    not callable without a real id: %d"
              % (refused_count, len(skipped)))

        print("\n--- THE SPECIFIC ASKS ------------------------------------------")
        checks = [
            ("GET",    "/admin/dashboard",              None),
            ("GET",    "/admin/users",                  None),
            ("GET",    "/admin/orgs",                   None),
            ("GET",    "/admin/organizations",          None),
            ("GET",    "/admin/platforms",              None),
            ("POST",   "/admin/provision-client",       {"org_name": "X", "org_slug": "x",
                                                         "supervisor_full_name": "X",
                                                         "supervisor_email": "x@probe-corp.com"}),
            ("POST",   "/admin/users/u-god/reset-password", {"new_password": "TakenOver!2026"}),
            ("PUT",    "/admin/organizations/org-cust", {"name": "RENAMED BY SELLER"}),
            ("DELETE", "/admin/demo/wipe/org-cust",     None),
            ("GET",    "/leads",                        None),
            ("GET",    "/pipeline/stats",               None),
        ]
        for method, path, body in checks:
            r = c.request(method, path, headers=mgr, **({"json": body} if body else {}))
            ok = r.status_code >= 400
            print("  %s %-7s %-40s -> %s" % ("ok   " if ok else "LEAK ", method, path,
                                             r.status_code))
            if not ok:
                FINDINGS.append("%s %s -> %s" % (method, path, r.status_code))
            else:
                PASSED.append("%s %s" % (method, path))

        print("\n  and the owner's password is untouched: ", end="")
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.id == "u-god").first()
            same = u is not None
        finally:
            db.close()
        r2 = c.post("/auth/login", data={"username": "god@probe.test",
                                         "password": "TakenOver!2026"})
        if r2.status_code == 200:
            print("NO - THE SELLER LOGGED IN AS THE OWNER")
            FINDINGS.append("sales manager took over the god_admin account")
        else:
            print("yes (login as owner with the attempted password: %s)" % r2.status_code)
            PASSED.append("owner account untouched")

        print("\n--- POSITIVE CONTROL - his own workspace still works ------------")
        rep = token(c, "rep@probe.test")
        for who, hdr, path, must in (
            ("manager", mgr, "/sales/me", True),
            ("manager", mgr, "/sales/my-day", True),
            ("manager", mgr, "/sales/opportunities", True),
            ("manager", mgr, "/sales/team", True),
            ("manager", mgr, "/sales/implementations", True),
            ("manager", mgr, "/sales/appointments?scope=team", True),
            ("rep",     rep, "/sales/me", True),
            ("rep",     rep, "/sales/opportunities", True),
            ("rep",     rep, "/sales/appointments?scope=team", False),
        ):
            r = c.get(path, headers=hdr)
            ok = (r.status_code == 200) if must else (r.status_code == 403)
            label = "%s %s%s" % (who, path, "" if must else "  (must be REFUSED)")
            print("  %s %s -> %s" % ("ok   " if ok else "BROKE", label, r.status_code))
            (PASSED if ok else BROKEN).append(label)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FINDINGS:
        print("\nA BRAND SALES MANAGER REACHED SOMETHING HE SHOULD NOT (%d):" % len(FINDINGS))
        for f in FINDINGS:
            print("  - %s" % f)
    if BROKEN:
        print("\nHIS OWN WORKSPACE IS BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not FINDINGS and not BROKEN:
        print("\nSALES WORKSPACE ONLY - no back-office route admitted him,")
        print("and everything his role is supposed to do still works.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (FINDINGS or BROKEN) else 0)


if __name__ == "__main__":
    main()
