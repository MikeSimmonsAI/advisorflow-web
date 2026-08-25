"""
Regression harness for scripts/seed_evosyspro_sales.py.

Builds a throwaway SQLite database shaped like production, runs the seed
DRY -> APPLY -> APPLY (again), and asserts the six behaviours Mike required:

  1. Sales users are created with organization_id = NULL.
  2. Mike Simmons is reused, never duplicated, and his own org is untouched.
  3. Michael Schlueter and Mike Simmons stay two separate user rows.
  4. A lead is flagged is_test ONLY on a verified email or exact phone match.
     A same-surname decoy with a different phone and email is NOT flagged.
  5. Re-running is idempotent: no second org, package, user, or membership.
  6. A dry run writes nothing and prints no credential.

    python scripts/test_seed_evosyspro_sales.py

Nothing here touches production. DATABASE_URL is forced to a temp SQLite file
before any app module is imported.
"""
import os
import sys
import shutil
import sqlite3
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(ROOT, "scripts", "seed_evosyspro_sales.py")

TMP = tempfile.mkdtemp(prefix="seedtest_")
DB_FILE = os.path.join(TMP, "test.db")
DB_URL = "sqlite:///" + DB_FILE.replace("\\", "/")

# Forced BEFORE app imports. Never inherit a real DATABASE_URL into this test.
os.environ["DATABASE_URL"] = DB_URL
os.environ["JWT_SECRET"] = "test" + "0" * 60
os.environ["SECRET_KEY"] = "test" + "0" * 60

sys.path.insert(0, ROOT)

from app.models.models import Base, Platform, Organization, User, Lead   # noqa: E402
import app.models.sales_models  # noqa: E402,F401  (registers sales tables)
from app.deps import engine, SessionLocal                                # noqa: E402
from app.services.auth_service import hash_password                      # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def build_fixture():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    plat = Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro")
    god_org = Organization(id="org-god", name="AdvisorFlow HQ", slug="advisorflow-hq",
                           platform_id=plat.id)
    cust_org = Organization(id="org-greenland", name="Greenland Cemetery and Funeral Home",
                            slug="greenland", platform_id=plat.id)
    db.add_all([plat, god_org, cust_org])

    # Mike already exists as god_admin inside his own org.
    db.add(User(id="usr-mike", organization_id=god_org.id,
                email="mike@simmonsstrong.com", full_name="Mike Simmons",
                password_hash=hash_password("x"), role="god_admin",
                must_change_password=False, is_active=True))

    # Michael Schlueter's real QA lead: email differs from his login address,
    # phone is stored E.164 with a leading 1. Must match on PHONE.
    db.add(Lead(id="lead-mps", organization_id=cust_org.id,
                first_name="Michael", last_name="Schlueter",
                phone="15403927776", phone_raw="540-392-7776",
                email=None, tier="pre_need"))

    # Blake's QA lead: matches on EMAIL only, phone stored differently.
    db.add(Lead(id="lead-blake", organization_id=cust_org.id,
                first_name="Blake", last_name="Rehani",
                phone="19995551234", phone_raw="999-555-1234",
                email="BlakeRehani@gmail.com", tier="pre_need"))

    # THE DECOY. Same surname as a team member, real customer, different phone
    # and email. Flagging this would be the exact mistake Mike forbade.
    db.add(Lead(id="lead-decoy", organization_id=cust_org.id,
                first_name="Michelle", last_name="Schlueter",
                phone="12145550000", phone_raw="214-555-0000",
                email="michelle.schlueter@example.com", tier="pre_need"))

    db.commit()
    db.close()


def run_seed(*args):
    env = dict(os.environ)
    env["DATABASE_URL"] = DB_URL
    p = subprocess.run([sys.executable, SEED] + list(args), cwd=ROOT, env=env,
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        raise SystemExit("seed exited %d with args %s" % (p.returncode, args))
    return p.stdout


def q(sql, *params):
    con = sqlite3.connect(DB_FILE)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def main():
    build_fixture()

    # ── DRY RUN ────────────────────────────────────────────────────────────
    print("\n[1] DRY RUN writes nothing and leaks no credential")
    dry = run_seed()
    check("dry run created no users",
          q("SELECT COUNT(*) FROM users")[0][0] == 1)
    check("dry run created no brand sales org",
          q("SELECT COUNT(*) FROM brand_sales_orgs")[0][0] == 0)
    check("dry run created no packages",
          q("SELECT COUNT(*) FROM brand_packages")[0][0] == 0)
    check("dry run flagged no leads",
          q("SELECT COUNT(*) FROM leads WHERE is_test = 1")[0][0] == 0)
    check("dry run printed no TEMPORARY PASSWORDS block",
          "TEMPORARY PASSWORDS" not in dry)

    # ── APPLY ──────────────────────────────────────────────────────────────
    print("\n[2] APPLY creates the sales domain")
    out = run_seed("--apply")
    check("brand sales org created",
          q("SELECT COUNT(*) FROM brand_sales_orgs")[0][0] == 1)
    check("four packages created",
          q("SELECT COUNT(*) FROM brand_packages")[0][0] == 4)
    check("multi_tenant package has NULL price",
          q("SELECT price FROM brand_packages WHERE key='multi_tenant'")[0][0] is None)
    check("no package linked to a Stripe plan",
          q("SELECT COUNT(*) FROM brand_packages WHERE billing_plan_key IS NOT NULL")[0][0] == 0)

    print("\n[3] Test 1 - sales users carry organization_id = NULL")
    rows = q("SELECT email, organization_id, role, must_change_password, is_active "
             "FROM users WHERE email IN (?,?)",
             "michaelpschlueter@gmail.com", "blakerehani@gmail.com")
    check("both sales users created", len(rows) == 2, str(rows))
    check("both have organization_id NULL",
          all(r[1] is None for r in rows), str(rows))
    check("both are role=advisor (lowest privilege)",
          all(r[2] == "advisor" for r in rows), str(rows))
    check("both must_change_password", all(r[3] for r in rows), str(rows))
    check("both active", all(r[4] for r in rows), str(rows))

    print("\n[4] Test 2 - Mike Simmons reused, not duplicated, org untouched")
    mikes = q("SELECT id, organization_id, role FROM users WHERE email=?",
              "mike@simmonsstrong.com")
    check("exactly one Mike Simmons row", len(mikes) == 1, str(mikes))
    check("Mike keeps his own organization", mikes[0][1] == "org-god", str(mikes))
    check("Mike is still god_admin", mikes[0][2] == "god_admin", str(mikes))

    print("\n[5] Test 3 - Schlueter and Simmons are separate identities")
    mps = q("SELECT id FROM users WHERE email=?", "michaelpschlueter@gmail.com")
    check("Michael Schlueter has his own user row", len(mps) == 1)
    check("ids differ", mps[0][0] != mikes[0][0])

    print("\n[6] Memberships")
    mem = q("SELECT u.email, m.role, m.scope_type, m.is_active "
            "FROM memberships m JOIN users u ON u.id=m.user_id ORDER BY u.email")
    check("three memberships granted", len(mem) == 3, str(mem))
    check("all scoped to brand_sales_org",
          all(r[2] == "brand_sales_org" for r in mem), str(mem))
    by_email = {r[0]: r[1] for r in mem}
    check("Michael Schlueter is sales_manager",
          by_email.get("michaelpschlueter@gmail.com") == "sales_manager", str(by_email))
    check("Blake Rehani is sales_rep",
          by_email.get("blakerehani@gmail.com") == "sales_rep", str(by_email))
    check("Mike Simmons is sales_manager",
          by_email.get("mike@simmonsstrong.com") == "sales_manager", str(by_email))
    check("memberships did not change users.role",
          q("SELECT COUNT(*) FROM users WHERE role NOT IN ('advisor','god_admin')")[0][0] == 0)

    print("\n[7] Test 4 - test flagging matches on evidence, never on name")
    flagged = dict((r[0], r[1]) for r in
                   q("SELECT id, test_note FROM leads WHERE is_test = 1"))
    check("Michael Schlueter's lead flagged (phone match)", "lead-mps" in flagged)
    check("Blake Rehani's lead flagged (email match)", "lead-blake" in flagged)
    check("DECOY same-surname customer NOT flagged", "lead-decoy" not in flagged,
          "decoy was flagged - name matching leaked in")
    check("exactly two leads flagged", len(flagged) == 2, str(sorted(flagged)))
    check("Schlueter note records a phone match",
          "phone" in (flagged.get("lead-mps") or ""), str(flagged.get("lead-mps")))
    check("Rehani note records an email match",
          "email" in (flagged.get("lead-blake") or ""), str(flagged.get("lead-blake")))

    print("\n[8] Credentials returned once, and only for newly created users")
    check("temporary password block printed", "TEMPORARY PASSWORDS" in out)
    check("Michael Schlueter listed", "michaelpschlueter@gmail.com" in out.split("TEMPORARY PASSWORDS")[1])
    check("Blake Rehani listed", "blakerehani@gmail.com" in out.split("TEMPORARY PASSWORDS")[1])
    check("Mike Simmons NOT issued a new password",
          "mike@simmonsstrong.com" not in out.split("TEMPORARY PASSWORDS")[1])

    print("\n[9] Test 5 - re-running is idempotent")
    before = (q("SELECT COUNT(*) FROM users")[0][0],
              q("SELECT COUNT(*) FROM brand_sales_orgs")[0][0],
              q("SELECT COUNT(*) FROM brand_packages")[0][0],
              q("SELECT COUNT(*) FROM memberships")[0][0],
              q("SELECT COUNT(*) FROM leads WHERE is_test = 1")[0][0])
    again = run_seed("--apply")
    after = (q("SELECT COUNT(*) FROM users")[0][0],
             q("SELECT COUNT(*) FROM brand_sales_orgs")[0][0],
             q("SELECT COUNT(*) FROM brand_packages")[0][0],
             q("SELECT COUNT(*) FROM memberships")[0][0],
             q("SELECT COUNT(*) FROM leads WHERE is_test = 1")[0][0])
    check("counts unchanged on re-run", before == after, "%s -> %s" % (before, after))
    check("no new credential issued on re-run", "TEMPORARY PASSWORDS" not in again)

    print("\n" + "=" * 66)
    if FAILURES:
        print("FAILED (%d): %s" % (len(FAILURES), "; ".join(FAILURES)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
