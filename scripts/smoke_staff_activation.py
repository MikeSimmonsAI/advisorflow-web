"""Brand-sales login activation: security, isolation and single-use.

THE SITUATION THIS WAS BUILT FOR, REPRODUCED EXACTLY.
Two people with correct identities, `organization_id = NULL`, correct ACTIVE
EvoSys Pro memberships, a password hash from a one-off seed, and
`must_change_password = true`. One has signed in before; one never has. Neither
could get in, and no supported path existed to fix it.

The fixture below is that shape, so the tests are about the real problem rather
than a convenient one.

WHAT MATTERS MOST HERE is what activation does NOT do. A person's user id,
their `organization_id = NULL`, their membership row and their sales role must
come out the far side byte-identical. Those are asserted field by field, before
and after.

No test contacts anything external.

    python scripts/smoke_staff_activation.py
"""
import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="staffact_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import (                                      # noqa: E402
    Base, Platform, Organization, User, AuditLogEntry,
)
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.staff_models import (                                # noqa: E402
    StaffActivation, STAFF_INVITE_PENDING, STAFF_INVITE_ACCEPTED,
    STAFF_INVITE_REVOKED,
)
from app.services.auth_service import hash_password, verify_password  # noqa: E402

PW = "SmokeTest!2026"
FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def read_src(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8-sig") as fh:
        return fh.read()


def token_for(c, email, password=PW):
    r = c.post("/auth/login", data={"username": email, "password": password})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def snapshot(uid):
    """Everything about this identity that activation must not change."""
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        mem = (db.query(Membership)
                 .filter(Membership.user_id == uid,
                         Membership.scope_type == SCOPE_BRAND_SALES_ORG)
                 .order_by(Membership.created_at).all())
        return {
            "id": u.id, "email": u.email, "organization_id": u.organization_id,
            "role": u.role, "is_active": u.is_active,
            "memberships": [(m.id, m.scope_id, m.role, m.is_active) for m in mem],
        }
    finally:
        db.close()


def build():
    """Production's real shape: two brands, correct memberships, NULL orgs."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"),
                Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                      slug="evosyspro-sales"),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb", name="BookaBoost Sales",
                      slug="bookaboost-sales"),
    ])
    db.flush()
    db.add(Organization(id="org-cust", name="Greenland Cemetery", slug="greenland",
                        platform_id="plt-evo"))
    db.flush()

    def mk(uid, email, name, role="advisor", org=None, last_login=None,
           must_change=False):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=must_change, is_active=True,
                    last_login_at=last_login))

    # The two real cases, carrying production's actual state - including
    # must_change_password=True, which is not cosmetic: get_current_user REFUSES
    # every authenticated route while it is set, and clearing it needs
    # /auth/change-password, which needs the current password. Neither of them
    # knows one. So both are locked out, including the one who has signed in
    # before, and neither can be the person who issues their own link.
    mk("u-mgr", "manager@example.test", "Michael Schlueter", must_change=True,
       last_login=datetime.utcnow() - timedelta(days=40))   # has signed in
    mk("u-rep", "rep@example.test", "Blake Rehani", must_change=True)  # never has
    # Everyone else needed to prove the boundaries.
    mk("u-god", "god@example.test", "Owner", role="god_admin")
    mk("u-bbmgr", "bbmgr@example.test", "Other Brand Manager")
    mk("u-custadmin", "cust@example.test", "Customer Admin",
       role="org_admin", org="org-cust")
    mk("u-nomem", "nomem@example.test", "No Membership")
    # A stand-in for the lockout test. Blake must stay untouched: signing in as
    # him would set last_login_at and destroy the "never signed in" case, and
    # single-session auth would invalidate the token other tests are holding.
    mk("u-locked", "locked@example.test", "Locked Out Rep", must_change=True)
    # A working rep, used to prove "a rep may not issue links". It has to be a
    # DIFFERENT person from Blake: the authority tests sign in, and signing in
    # as Blake would set last_login_at and destroy the never-signed-in case his
    # whole scenario rests on.
    mk("u-rep2", "rep2@example.test", "Working Rep",
       last_login=datetime.utcnow() - timedelta(days=3))
    db.flush()
    db.add_all([
        Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bbmgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-locked", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-rep2", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
    ])
    db.commit()
    db.close()


def issue(c, hdr, user_id, purpose="setup", brand="bso-evo"):
    return c.post("/god/ops/sales-users/%s/setup-link" % user_id,
                  json={"brand_sales_org_id": brand, "purpose": purpose,
                        "base_url": "https://app.evosyspro.live"}, headers=hdr)


# ── who may issue ───────────────────────────────────────────────────────────

def test_locked_out_by_must_change(c):
    section("must_change_password locks them out of everything")
    hdr = token_for(c, "locked@example.test")
    r = c.get("/sales/opportunities", headers=hdr)
    check("a must_change_password user is refused authenticated routes",
          r.status_code == 403 and "change" in r.text.lower(),
          "%s %s" % (r.status_code, r.text[:150]))
    check("which is why they cannot issue their own access link",
          issue(c, hdr, "u-locked").status_code == 403)


def test_authority(c, god, rep, bbmgr, cust):
    section("Only god or that brand's manager may issue")
    r = issue(c, rep, "u-rep")
    check("a sales REP cannot generate a link", r.status_code == 403, r.text[:200])

    r = issue(c, bbmgr, "u-rep")
    check("another brand's manager cannot generate a link",
          r.status_code == 403, r.text[:200])

    r = issue(c, cust, "u-rep")
    check("a CUSTOMER admin cannot reach the endpoint at all",
          r.status_code == 403, "%s %s" % (r.status_code, r.text[:150]))

    r = c.post("/god/ops/sales-users/u-rep/setup-link",
               json={"brand_sales_org_id": "bso-evo"})
    check("anonymous cannot generate a link", r.status_code == 401, r.status_code)

    r = issue(c, god, "u-nomem")
    check("a user with no membership here is refused - a link unlocks, never grants",
          r.status_code == 409, r.text[:200])

    r = issue(c, god, "u-bbmgr")
    check("a user whose membership is in ANOTHER brand is refused",
          r.status_code == 409, r.text[:200])

    r = c.get("/god/ops/brands/bso-evo/sales-team", headers=rep)
    check("a rep cannot read the sales-team access view", r.status_code == 403, r.status_code)
    r = c.get("/god/ops/brands/bso-evo/sales-team", headers=bbmgr)
    check("another brand's manager cannot read it either", r.status_code == 403, r.status_code)
    r = c.get("/god/ops/brands/bso-evo/sales-team", headers=god)
    check("god can read it", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        team = r.json()["team"]
        check("the team lists every membership holder", len(team) == 4, len(team))
        rep_row = [t for t in team if t["user_id"] == "u-rep"][0]
        mgr_row = [t for t in team if t["user_id"] == "u-mgr"][0]
        check("it reports the rep has never signed in",
              rep_row["access"]["has_signed_in"] is False, rep_row["access"])
        check("it reports the manager HAS signed in",
              mgr_row["access"]["has_signed_in"] is True, mgr_row["access"])
        check("it exposes no password material",
              "password_hash" not in json.dumps(team), "leak")


# ── first-time setup (Blake's case) ─────────────────────────────────────────

def test_first_time_setup(c, god):
    section("First-time setup for a user who has never signed in")
    before = snapshot("u-rep")

    r = issue(c, god, "u-rep", purpose="setup")
    check("god issues a setup link", r.status_code == 200, r.text[:250])
    body = r.json()
    url = body.get("setup_url", "")
    token = url.split("token=")[-1] if "token=" in url else ""

    flat = json.dumps(body).lower()
    for bad in ('"password"', '"temp_password"', '"new_password"', '"password_hash"'):
        check("the response carries no %s" % bad, bad not in flat, flat[:250])
    check("the token is staff-scoped, not a customer token",
          token.startswith("stf_"), token[:8])

    db = SessionLocal()
    row = (db.query(StaffActivation)
             .filter(StaffActivation.user_id == "u-rep",
                     StaffActivation.status == STAFF_INVITE_PENDING).first())
    check("only a hash is stored, never the token",
          row is not None and token not in row.token_hash and len(row.token_hash) == 64)
    check("the activation row carries NO organization column",
          not hasattr(row, "organization_id"), "organization_id present")
    check("it records which brand it was issued for", row.brand_sales_org_id == "bso-evo")
    db.close()

    r = c.get("/auth/staff-activation", params={"token": token})
    check("the link previews", r.status_code == 200, r.text[:200])
    prev = r.json() if r.status_code == 200 else {}
    check("preview names the workspace, not a customer org",
          prev.get("workspace") == "EvoSys Pro Sales", prev)
    check("preview leaks no ids",
          "user_id" not in prev and "organization_id" not in prev, list(prev))

    r = c.post("/auth/staff-activation/accept",
               json={"token": token, "new_password": "short"})
    check("a weak password is refused", r.status_code == 400, r.text[:150])

    r = c.post("/auth/staff-activation/accept",
               json={"token": token, "new_password": "BlakeChosen!2026"})
    check("the user sets their own password", r.status_code == 200, r.text[:200])
    check("accept returns no session token", "access_token" not in r.text, r.text[:150])

    r = c.post("/auth/login", data={"username": "rep@example.test",
                                    "password": "BlakeChosen!2026"})
    check("they can now sign in", r.status_code == 200, r.text[:200])

    r = c.post("/auth/login", data={"username": "rep@example.test", "password": PW})
    check("the OLD seeded password no longer works", r.status_code != 200, r.status_code)

    after = snapshot("u-rep")
    check("user id unchanged", before["id"] == after["id"])
    check("organization_id is STILL NULL", after["organization_id"] is None,
          after["organization_id"])
    check("membership rows are byte-identical",
          before["memberships"] == after["memberships"],
          (before["memberships"], after["memberships"]))
    check("still a sales_rep", after["memberships"][0][2] == ROLE_SALES_REP)

    db = SessionLocal()
    u = db.query(User).filter(User.id == "u-rep").first()
    check("must_change_password was cleared", u.must_change_password is False)
    check("the account is not locked out", not u.lockout_until)
    db.close()
    return token


# ── reset (Michael's case) ──────────────────────────────────────────────────

def test_reset_preserves_manager(c, god):
    section("Reset for a manager who has signed in before")
    before = snapshot("u-mgr")
    r = issue(c, god, "u-mgr", purpose="reset")
    check("god issues a reset link", r.status_code == 200, r.text[:250])
    token = r.json()["setup_url"].split("token=")[-1]
    check("the reset is recorded as a reset",
          r.json()["activation"]["purpose"] == "reset", r.json()["activation"])

    prev = c.get("/auth/staff-activation", params={"token": token}).json()
    check("preview says it is a reset", prev.get("purpose") == "reset", prev)

    r = c.post("/auth/staff-activation/accept",
               json={"token": token, "new_password": "MichaelChosen!2026"})
    check("the manager sets a new password", r.status_code == 200, r.text[:200])
    r = c.post("/auth/login", data={"username": "manager@example.test",
                                    "password": "MichaelChosen!2026"})
    check("the manager can sign in", r.status_code == 200, r.text[:200])

    after = snapshot("u-mgr")
    check("user id unchanged", before["id"] == after["id"])
    check("organization_id is STILL NULL", after["organization_id"] is None)
    check("membership rows are byte-identical",
          before["memberships"] == after["memberships"])
    check("STILL a sales_manager", after["memberships"][0][2] == ROLE_SALES_MANAGER,
          after["memberships"])

    # Manager authority genuinely survives - proven by using it, not by reading a column.
    hdr = token_for(c, "manager@example.test", "MichaelChosen!2026")
    r = c.get("/god/ops/brands/bso-evo/sales-team", headers=hdr)
    check("they still hold manager authority after the reset",
          r.status_code == 200, r.text[:200])

    # Only now can the brand's own manager issue links - before activation
    # must_change_password refused him every authenticated route.
    r = issue(c, hdr, "u-rep", purpose="reset")
    check("the brand's OWN manager may now generate a link",
          r.status_code == 200, r.text[:250])
    r = issue(c, hdr, "u-bbmgr", purpose="reset")
    check("...but still not for another brand's user", r.status_code == 409, r.text[:200])
    r = c.post("/god/ops/sales-users/u-bbmgr/setup-link",
               json={"brand_sales_org_id": "bso-bb"}, headers=hdr)
    check("...and not by naming the other brand either", r.status_code == 403, r.text[:200])


# ── token lifecycle ─────────────────────────────────────────────────────────

def test_token_lifecycle(c, god, used_token):
    section("Single use, expiry, wrong token, revoke")
    r = c.post("/auth/staff-activation/accept",
               json={"token": used_token, "new_password": "Another!Password1"})
    check("a used token cannot be used again", r.status_code == 400, r.text[:150])
    r = c.get("/auth/staff-activation", params={"token": used_token})
    check("a used token no longer previews", r.status_code == 400, r.status_code)

    r = c.get("/auth/staff-activation", params={"token": "stf_" + "x" * 40})
    check("a wrong token fails closed", r.status_code == 400, r.status_code)
    wrong_body = r.text
    r2 = c.get("/auth/staff-activation", params={"token": used_token})
    check("wrong and used give the SAME response - no oracle",
          wrong_body == r2.text, (wrong_body[:80], r2.text[:80]))

    r = c.get("/auth/staff-activation", params={"token": ""})
    check("an empty token fails closed", r.status_code in (400, 422), r.status_code)

    # Expiry, forced by ageing the row rather than waiting.
    r = issue(c, god, "u-rep", purpose="reset")
    tok = r.json()["setup_url"].split("token=")[-1]
    db = SessionLocal()
    row = (db.query(StaffActivation)
             .filter(StaffActivation.status == STAFF_INVITE_PENDING,
                     StaffActivation.user_id == "u-rep").first())
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    db.close()
    r = c.get("/auth/staff-activation", params={"token": tok})
    check("an expired token is refused", r.status_code == 400, r.status_code)
    r = c.post("/auth/staff-activation/accept",
               json={"token": tok, "new_password": "Expired!Password1"})
    check("an expired token cannot set a password", r.status_code == 400, r.text[:150])
    db = SessionLocal()
    row = (db.query(StaffActivation)
             .filter(StaffActivation.token_prefix == tok[:12]).first())
    check("the expired row is marked expired, not left pending",
          row.status == "expired", row.status)
    db.close()

    # Issuing supersedes.
    r1 = issue(c, god, "u-rep", purpose="reset")
    t1 = r1.json()["setup_url"].split("token=")[-1]
    r2 = issue(c, god, "u-rep", purpose="reset")
    t2 = r2.json()["setup_url"].split("token=")[-1]
    check("the two links differ", t1 != t2)
    check("issuing a new link kills the previous one",
          c.get("/auth/staff-activation", params={"token": t1}).status_code == 400)
    check("the new link works",
          c.get("/auth/staff-activation", params={"token": t2}).status_code == 200)

    aid = r2.json()["activation"]["id"]
    r = c.post("/god/ops/staff-activations/%s/revoke" % aid, headers=god)
    check("a link can be revoked", r.status_code == 200 and
          r.json()["activation"]["status"] == STAFF_INVITE_REVOKED, r.text[:200])
    check("a revoked link is dead",
          c.get("/auth/staff-activation", params={"token": t2}).status_code == 400)


# ── isolation and audit ─────────────────────────────────────────────────────

def test_isolation_and_audit(c, god):
    section("No tenant is ever assigned; every action is audited")
    db = SessionLocal()
    for uid in ("u-mgr", "u-rep"):
        u = db.query(User).filter(User.id == uid).first()
        check("%s organization_id is still NULL" % uid,
              u.organization_id is None, u.organization_id)
    check("no staff activation row references an organisation",
          not any(hasattr(r, "organization_id")
                  for r in db.query(StaffActivation).all()))

    actions = {a.action for a in db.query(AuditLogEntry).all()}
    check("link issuance is audited", "sales_access_link_issued" in actions, sorted(actions))
    check("activation is audited", "sales_access_activated" in actions, sorted(actions))
    check("revocation is audited", "sales_access_link_revoked" in actions, sorted(actions))

    entries = db.query(AuditLogEntry).filter(
        AuditLogEntry.action.in_(["sales_access_link_issued", "sales_access_activated"])).all()
    blob = " ".join((e.details or "") + (e.after_state or "") + (e.before_state or "")
                    for e in entries)
    import re
    long_tokens = [m for m in re.findall(r"stf_[A-Za-z0-9_\-]{9,}", blob) if len(m) > 12]
    check("no audit row contains a full token", not long_tokens, long_tokens[:2])
    check("audit rows carry the brand",
          all(e.brand_sales_org_id == "bso-evo" for e in entries),
          [e.brand_sales_org_id for e in entries])
    check("audit rows carry NO organisation - a control-plane act has no tenant",
          all(e.organization_id is None for e in entries),
          [e.organization_id for e in entries])
    db.close()

    section("Static guarantees")
    svc = read_src("app/services/staff_activation.py")
    code = "\n".join(l for l in svc.splitlines() if not l.strip().startswith("#"))
    check("the service never writes organization_id",
          ".organization_id =" not in code)
    check("the service never constructs a Membership", "Membership(" not in code)
    check("the service never assigns a role", ".role =" not in code)
    check("the service never returns a generated password",
          "temp_password" not in code and "_generate_temp_password" not in code)
    check("the service sends no email or SMS",
          "send_email" not in code and "send_sms" not in code)
    # Assert on a real column definition, not on the word appearing anywhere -
    # this module's docstring explains at length why the column is absent, and a
    # substring scan cannot tell an explanation from the thing it explains.
    import ast as _ast
    tree = _ast.parse(read_src("app/models/staff_models.py"))
    cls = [n for n in tree.body
           if isinstance(n, _ast.ClassDef) and n.name == "StaffActivation"][0]
    cols = [t.id for stmt in cls.body if isinstance(stmt, _ast.Assign)
            for t in stmt.targets if isinstance(t, _ast.Name)]
    check("the staff table defines no organization_id column",
          "organization_id" not in cols, cols)
    check("...and does define the columns it needs",
          {"user_id", "token_hash", "token_prefix", "expires_at",
           "status"} <= set(cols), cols)
    check("the ORM class has no organization_id attribute either",
          not hasattr(StaffActivation, "organization_id"))


def main():
    print("=" * 70)
    print("BRAND-SALES LOGIN ACTIVATION")
    print("=" * 70)
    build()
    with TestClient(app) as c:
        god = token_for(c, "god@example.test")
        rep = token_for(c, "rep2@example.test")
        bbmgr = token_for(c, "bbmgr@example.test")
        cust = token_for(c, "cust@example.test")

        test_locked_out_by_must_change(c)
        test_authority(c, god, rep, bbmgr, cust)
        used = test_first_time_setup(c, god)
        test_reset_preserves_manager(c, god)
        test_token_lifecycle(c, god, used)
        test_isolation_and_audit(c, god)

    print("\n" + "=" * 70)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("ALL BRAND-SALES ACTIVATION CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
