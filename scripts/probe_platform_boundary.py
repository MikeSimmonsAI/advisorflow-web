"""GATE 23 - the platform boundary holds, and it still lets the right people through.

This started as an evidence-gathering probe for the Platform Separation mission
and it found six real leaks, including a total takeover: a super_admin on one
platform reset the god_admin's password and then logged in as the owner.

It is now a gate, which means it has to answer TWO questions, not one. A guard
that refuses everything passes a leak probe perfectly and destroys the product,
so every REFUSAL check below is paired with an ALLOW check proving the same
route still works for the person entitled to use it.

Fixture: two platforms, an organisation on each, a god_admin above both, a
super_admin per platform, a SECOND super_admin sharing Platform A's org (peer
takeover), a brand-sales identity with organization_id NULL, and a lead per org.

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="platboundary_")
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
from app.services.auth_service import hash_password                  # noqa: E402

PW = "ProbeTest!2026"
LEAKS = []
BROKEN = []
PASSED = []


def refused(label, ok, detail=""):
    """ok=True means the boundary held."""
    tag = "ok   " if ok else "LEAK "
    print("  %s %s%s" % (tag, label, ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else LEAKS).append(label)


def allowed(label, ok, detail=""):
    """ok=True means the legitimate operator can still do their job."""
    tag = "ok   " if ok else "BROKE"
    print("  %s %s%s" % (tag, label, ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 64 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-a", name="Platform A", slug="plat-a"),
                Platform(id="plt-b", name="Platform B", slug="plat-b")])
    db.flush()
    db.add_all([
        Organization(id="org-a", name="Alpha Cemetery", slug="alpha", platform_id="plt-a"),
        Organization(id="org-b", name="Bravo Funeral Home", slug="bravo", platform_id="plt-b"),
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
    mk("u-sa-a", "sa.a@probe.test", "Super A", "super_admin", org="org-a", platform="plt-a")
    mk("u-sa-a2", "sa.a2@probe.test", "Super A Peer", "super_admin", org="org-a", platform="plt-a")
    mk("u-sa-b", "sa.b@probe.test", "Super B", "super_admin", org="org-b", platform="plt-b")
    mk("u-adv-a", "adv.a@probe.test", "Advisor A", "advisor", org="org-a")
    mk("u-adv-b", "adv.b@probe.test", "Advisor B", "advisor", org="org-b")
    # Brand-sales identity: organization_id NULL as a positive assertion.
    mk("u-bs", "seller@probe.test", "Brand Seller", "advisor", org=None)
    db.flush()
    db.add_all([
        Lead(id="lead-a", organization_id="org-a", first_name="Alpha", last_name="Lead",
             phone="+15550000001", status="new", created_at=datetime.utcnow()),
        Lead(id="lead-b", organization_id="org-b", first_name="Bravo", last_name="Lead",
             phone="+15550000002", status="new", created_at=datetime.utcnow()),
    ])
    db.commit()
    db.close()


def token(c, email, pw=PW):
    r = c.post("/auth/login", data={"username": email, "password": pw})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def col(model, ident, field):
    db = SessionLocal()
    try:
        row = db.query(model).filter(model.id == ident).first()
        return getattr(row, field) if row else None
    finally:
        db.close()


def names_in(r, key="organizations"):
    if r.status_code != 200:
        return []
    body = r.json()
    rows = body if isinstance(body, list) else body.get(key, [])
    return [x.get("name") for x in rows] if isinstance(rows, list) else []


def main():
    print("=" * 78)
    print("GATE 23 - CROSS-PLATFORM super_admin BOUNDARY")
    print("=" * 78)
    build()

    if not hasattr(User, "platform_id"):
        print("\n!! User.platform_id is absent - get_platform_org_ids scopes a")
        print("!! super_admin by that column, so this gate cannot mean anything.")
        sys.exit(1)

    with TestClient(app) as c:
        sa_a = token(c, "sa.a@probe.test")
        god = token(c, "god@probe.test")

        section("READS - can Super A see Platform B's world?")
        r = c.get("/admin/orgs", headers=sa_a)
        refused("GET /admin/orgs hides another platform's organisation",
                not any("Bravo" in (n or "") for n in names_in(r)),
                "%s %s" % (r.status_code, names_in(r)))

        r = c.get("/admin/organizations", headers=sa_a)
        refused("GET /admin/organizations hides another platform's organisation",
                not any("Bravo" in (n or "") for n in names_in(r)),
                "%s %s" % (r.status_code, names_in(r)))

        r = c.get("/admin/platforms", headers=sa_a)
        rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
        refused("GET /admin/platforms shows only the caller's own platform",
                r.status_code == 200 and [x.get("id") for x in rows] == ["plt-a"],
                "%s %s" % (r.status_code, rows))

        # NOT len(rows) == 2: app startup seeds the real platform rows into this
        # throwaway DB too, so the fixture's two are a subset. Assert god sees
        # BOTH of them, which is the property that matters.
        r = c.get("/admin/platforms", headers=god)
        rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
        ids = {x.get("id") for x in rows}
        allowed("GET /admin/platforms still shows god_admin every platform",
                r.status_code == 200 and {"plt-a", "plt-b"} <= ids,
                "%s %s" % (r.status_code, sorted(ids)))

        r = c.get("/admin/users", headers=sa_a)
        emails = [x.get("email") for x in (r.json() if r.status_code == 200
                  and isinstance(r.json(), list) else [])]
        refused("GET /admin/users hides another platform's users",
                not any((e or "").startswith(("adv.b@", "sa.b@")) for e in emails),
                "%s %s" % (r.status_code, emails))

        r = c.get("/admin/users/u-adv-b/detail", headers=sa_a)
        refused("GET /admin/users/{other platform's user}/detail is refused",
                r.status_code == 404, "%s %s" % (r.status_code, r.text[:120]))

        section("WRITES - can Super A change Platform B's world?")
        r = c.put("/admin/organizations/org-b",
                  json={"name": "RENAMED BY SUPER A"}, headers=sa_a)
        refused("PUT /admin/organizations/{other platform's org} is refused",
                r.status_code == 404 and "RENAMED" not in (col(Organization, "org-b", "name") or ""),
                "%s name=%r" % (r.status_code, col(Organization, "org-b", "name")))

        r = c.put("/admin/organizations/org-a",
                  json={"name": "Alpha Cemetery Ltd"}, headers=sa_a)
        allowed("PUT /admin/organizations/{OWN org} still works",
                r.status_code == 200 and col(Organization, "org-a", "name") == "Alpha Cemetery Ltd",
                "%s name=%r" % (r.status_code, col(Organization, "org-a", "name")))

        r = c.patch("/admin/users/u-adv-b",
                    json={"full_name": "EDITED BY SUPER A"}, headers=sa_a)
        refused("PATCH /admin/users/{other platform's user} is refused",
                r.status_code == 404 and "EDITED" not in (col(User, "u-adv-b", "full_name") or ""),
                "%s %s" % (r.status_code, r.text[:120]))

        r = c.patch("/admin/users/u-adv-a",
                    json={"full_name": "Advisor A Renamed"}, headers=sa_a)
        allowed("PATCH /admin/users/{OWN org's advisor} still works",
                r.status_code == 200 and col(User, "u-adv-a", "full_name") == "Advisor A Renamed",
                "%s %s" % (r.status_code, r.text[:120]))

        r = c.patch("/admin/users/u-bs",
                    json={"full_name": "EDITED BY SUPER A"}, headers=sa_a)
        refused("PATCH /admin/users/{brand-sales identity} is refused",
                r.status_code == 404 and "EDITED" not in (col(User, "u-bs", "full_name") or ""),
                "%s %s" % (r.status_code, r.text[:120]))

        r = c.post("/admin/users/u-bs/force-logout", headers=sa_a)
        refused("POST force-logout on a brand-sales identity is refused",
                r.status_code == 404, "%s %s" % (r.status_code, r.text[:120]))

        section("PLATFORM ASSIGNMENT - can Super A redraw the boundary itself?")
        r = c.patch("/admin/orgs/org-b/platform", json={"platform_id": "plt-a"}, headers=sa_a)
        refused("PATCH /admin/orgs/{other org}/platform is refused",
                r.status_code == 403 and col(Organization, "org-b", "platform_id") == "plt-b",
                "%s platform=%r" % (r.status_code, col(Organization, "org-b", "platform_id")))

        r = c.patch("/admin/orgs/org-a/platform", json={"platform_id": "plt-b"}, headers=sa_a)
        refused("PATCH /admin/orgs/{own org}/platform is refused (god_admin only)",
                r.status_code == 403 and col(Organization, "org-a", "platform_id") == "plt-a",
                "%s platform=%r" % (r.status_code, col(Organization, "org-a", "platform_id")))

        r = c.patch("/admin/orgs/org-b/platform", json={"platform_id": "plt-b"}, headers=god)
        allowed("PATCH /admin/orgs/{any org}/platform still works for god_admin",
                r.status_code == 200, "%s %s" % (r.status_code, r.text[:120]))

        section("ESCALATION - can Super A take over an account above them?")
        before = col(User, "u-god", "password_hash")
        r = c.post("/admin/users/u-god/reset-password",
                   json={"new_password": "TakenOver!2026"}, headers=sa_a)
        refused("POST reset-password on the god_admin OWNER is refused",
                r.status_code == 404 and col(User, "u-god", "password_hash") == before,
                "%s %s" % (r.status_code, r.text[:120]))
        r2 = c.post("/auth/login", data={"username": "god@probe.test",
                                         "password": "TakenOver!2026"})
        refused("...and the attacker's password does NOT log in as the owner",
                r2.status_code != 200, r2.status_code)

        before = col(User, "u-sa-b", "password_hash")
        r = c.post("/admin/users/u-sa-b/reset-password",
                   json={"new_password": "TakenOver!2026"}, headers=sa_a)
        refused("POST reset-password on ANOTHER PLATFORM's super_admin is refused",
                r.status_code == 404 and col(User, "u-sa-b", "password_hash") == before,
                "%s %s" % (r.status_code, r.text[:120]))

        before = col(User, "u-sa-a2", "password_hash")
        r = c.post("/admin/users/u-sa-a2/reset-password",
                   json={"new_password": "TakenOver!2026"}, headers=sa_a)
        refused("POST reset-password on a PEER super_admin in the same org is refused",
                r.status_code == 404 and col(User, "u-sa-a2", "password_hash") == before,
                "%s %s" % (r.status_code, r.text[:120]))

        # A real TLD on purpose. EmailStr rejects `.test` with a 422 BEFORE the
        # route body runs, and a 422 proves nothing about the guard - the same
        # vacuous pass that hid three assertions in gate 22.
        r = c.patch("/admin/users/u-sa-a2", json={"email": "attacker@probe-corp.com"}, headers=sa_a)
        refused("PATCH email of a PEER super_admin is refused (reset-link takeover)",
                r.status_code == 404 and col(User, "u-sa-a2", "email") == "sa.a2@probe.test",
                "%s email=%r" % (r.status_code, col(User, "u-sa-a2", "email")))

        before = col(User, "u-adv-a", "password_hash")
        r = c.post("/admin/users/u-adv-a/reset-password",
                   json={"new_password": "LegitReset!2026"}, headers=sa_a)
        allowed("POST reset-password on OWN platform's advisor still works",
                r.status_code == 200 and col(User, "u-adv-a", "password_hash") != before,
                "%s %s" % (r.status_code, r.text[:120]))

        before = col(User, "u-sa-b", "password_hash")
        r = c.post("/admin/users/u-sa-b/reset-password",
                   json={"new_password": "OwnerReset!2026"}, headers=god)
        allowed("god_admin can still reset any platform's super_admin",
                r.status_code == 200 and col(User, "u-sa-b", "password_hash") != before,
                "%s %s" % (r.status_code, r.text[:120]))

        section("DESTRUCTIVE - can Super A wipe or seed Platform B?")
        leads_b = "lead-b"
        r = c.delete("/admin/demo/wipe/org-b", headers=sa_a)
        refused("DELETE /admin/demo/wipe/{other platform's org} is refused",
                r.status_code == 404 and col(Lead, leads_b, "id") == leads_b,
                "%s lead-b present=%s" % (r.status_code, col(Lead, leads_b, "id") == leads_b))

        r = c.post("/admin/demo/seed/org-b", json={"num_leads": 1, "days_span": 1}, headers=sa_a)
        refused("POST /admin/demo/seed/{other platform's org} is refused",
                r.status_code == 404, "%s %s" % (r.status_code, r.text[:120]))

        r = c.post("/admin/orgs/org-b/seed-industry-tiers", headers=sa_a)
        refused("POST seed-industry-tiers on another platform's org is refused",
                r.status_code == 404, "%s %s" % (r.status_code, r.text[:120]))

        r = c.post("/admin/orgs/org-a/seed-industry-tiers", headers=sa_a)
        allowed("POST seed-industry-tiers on the OWN org still works",
                r.status_code == 200, "%s %s" % (r.status_code, r.text[:120]))

        section("PROVISIONING - does a new customer land on the right platform?")
        r = c.post("/admin/provision-client", headers=sa_a, json={
            "org_name": "Charlie Chapel", "org_slug": "charlie",
            "industry": "funeral", "plan": "trial",
            "supervisor_full_name": "Charlie Boss",
            "supervisor_email": "boss@charlie-probe.com",
        })
        new_id = r.json().get("org_id") if r.status_code == 200 else None
        allowed("POST /admin/provision-client succeeds for a super_admin",
                r.status_code == 200 and bool(new_id), "%s %s" % (r.status_code, r.text[:160]))
        refused("...and the new org is stamped with the CALLER'S platform, not orphaned",
                new_id is not None and col(Organization, new_id, "platform_id") == "plt-a",
                "platform_id=%r" % (col(Organization, new_id, "platform_id") if new_id else None))
        r = c.get("/admin/orgs", headers=sa_a)
        allowed("...and it is visible in the caller's own scoped org list",
                any("Charlie" in (n or "") for n in names_in(r)),
                "%s %s" % (r.status_code, names_in(r)))

        r = c.post("/admin/provision-client", headers=sa_a, json={
            "org_name": "Delta Chapel", "org_slug": "delta",
            "industry": "funeral", "plan": "trial",
            "supervisor_full_name": "Delta Boss",
            "supervisor_email": "boss@delta-probe.com",
            "platform_id": "plt-b",
        })
        d_id = r.json().get("org_id") if r.status_code == 200 else None
        refused("...and a super_admin cannot provision ONTO ANOTHER PLATFORM",
                d_id is not None and col(Organization, d_id, "platform_id") == "plt-a",
                "platform_id=%r" % (col(Organization, d_id, "platform_id") if d_id else None))

        section("TENANT DATA - does org scoping still hold?")
        r = c.get("/leads", headers=sa_a)
        body = r.json() if r.status_code == 200 else {}
        rows = body if isinstance(body, list) else body.get("leads", [])
        ids = [x.get("id") for x in rows] if isinstance(rows, list) else []
        refused("GET /leads does not return another platform's lead rows",
                "lead-b" not in ids, "%s %s" % (r.status_code, ids))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if LEAKS:
        print("\nBOUNDARY LEAKS (%d):" % len(LEAKS))
        for f in LEAKS:
            print("  - %s" % f)
    if BROKEN:
        print("\nLEGITIMATE ACCESS BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not LEAKS and not BROKEN:
        print("\nPLATFORM BOUNDARY HOLDS - and every legitimate operation still works.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (LEAKS or BROKEN) else 0)


if __name__ == "__main__":
    main()
