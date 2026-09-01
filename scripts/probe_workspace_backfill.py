"""GATE 31 - THE LEGACY COLUMN CANNOT MINT WORKSPACE ACCESS.

`users.organization_id` is a nullable string that a decade of different code
paths have written. Workspace entry is `Membership` now, and the column is only
the MIGRATION SOURCE that seeded it - so the one thing that must be true is that
the column can never produce access it does not honestly represent.

Seven rules, each with its own section below:

  1. only a VALID CUSTOMER WORKSPACE may produce a membership
  2. never inferred from a platform or brand-sales relationship
  3. never overwrites or transfers users.organization_id
  4. never duplicates
  5. never resurrects an explicitly revoked membership
  6. idempotent
  7. stops being able to mint once migration is complete

The fixture is built out of the ways the column actually goes wrong: an id
pointing at an organization that no longer exists, the platform's own
pseudo-org by id AND by plan, a brand-sales identity, a revoked membership, and
a suspended-but-real customer.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="wsbf_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from sqlalchemy import text as sa_text                                 # noqa: E402
from app.main import app                                              # noqa: E402
from app.deps import SessionLocal, engine                             # noqa: E402
from app.models.models import Base, Platform, Organization, User      # noqa: E402
from app.models.sales_models import (                                 # noqa: E402
    Membership, BrandSalesOrg, SCOPE_CUSTOMER_ORG, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER,
)
from app.services.auth_service import hash_password                   # noqa: E402
from app.services import workspace_access as wa                       # noqa: E402
from app.services.platform_owner import (                             # noqa: E402
    GOD_PLATFORM_ORG_ID, GOD_PLATFORM_ORG_SLUG, GOD_PLATFORM_ORG_PLAN,
)

PW = "ProbeTest!2026"
FAILED, BROKEN, PASSED = [], [], []

REAL = "org-real-customer"
SUSPENDED = "org-suspended-customer"
GHOST = "org-deleted-long-ago"        # NO Organization row exists for this
FAKE_PLATFORM = "org-looks-like-platform"   # a real row, but plan == "god"


def refused(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "MINT ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else FAILED).append(label)


def allowed(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "BROKE", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def ws_rows(db, user_id):
    return (db.query(Membership)
            .filter(Membership.user_id == user_id,
                    Membership.scope_type == SCOPE_CUSTOMER_ORG).all())


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # The completion marker must not be left over from an earlier run.
    try:
        db.execute(sa_text("DELETE FROM system_config WHERE key = :k"),
                   {"k": wa.BACKFILL_COMPLETE_KEY})
        db.commit()
    except Exception:
        pass

    db.add(Platform(id="plt-bb", name="BookaBoost", slug="bookaboost"))
    db.flush()
    db.add(BrandSalesOrg(id="bso-bb", platform_id="plt-bb",
                         name="BookaBoost Sales", slug="bookaboost-sales"))
    db.add_all([
        Organization(id=REAL, name="Real Customer", slug="real-customer",
                     platform_id="plt-bb", plan="standard", is_active=True),
        Organization(id=SUSPENDED, name="Suspended Customer",
                     slug="suspended-customer", platform_id="plt-bb",
                     plan="standard", is_active=False),
        # The platform's own pseudo-org, exactly as main.py creates it.
        Organization(id=GOD_PLATFORM_ORG_ID, name="AdvisorFlow Platform",
                     slug=GOD_PLATFORM_ORG_SLUG, platform_id="plt-bb",
                     plan=GOD_PLATFORM_ORG_PLAN),
        # A row that is NOT the known pseudo-org id but carries its plan - the
        # module's own comment says the plan is how you recognise it if the id
        # was ever changed, so the check has to hold for this too.
        Organization(id=FAKE_PLATFORM, name="Platform Under Another Id",
                     slug="platform-other", platform_id="plt-bb",
                     plan=GOD_PLATFORM_ORG_PLAN),
    ])
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    platform_id="plt-bb", must_change_password=False,
                    is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-good", "good@real.test", "Genuine Customer", "org_admin", REAL)
    mk("u-ghost", "ghost@nowhere.test", "Stale Column", "org_admin", GHOST)
    mk("u-pseudo", "pseudo@platform.test", "Pointed At Platform", "org_admin",
       GOD_PLATFORM_ORG_ID)
    mk("u-fakeplat", "fakeplat@platform.test", "Pointed At God Plan", "org_admin",
       FAKE_PLATFORM)
    mk("u-suspended", "susp@suspended.test", "Suspended Customer Staff",
       "advisor", SUSPENDED)
    mk("u-sales", "sales@bookaboost.test", "Brand Salesperson", "advisor", None)
    mk("u-revoked", "revoked@real.test", "Formerly Employed", "advisor", REAL)
    mk("u-inactive", "inactive@real.test", "Deactivated Account", "advisor", REAL)
    mk("u-god", "god@probe.test", "Owner", "god_admin", REAL)
    db.flush()

    # A brand-sales membership. Rule 2: this must never imply customer access.
    db.add(Membership(user_id="u-sales", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-bb", role=ROLE_SALES_MANAGER, is_active=True))
    # An EXPLICITLY REVOKED customer membership. Rule 5.
    db.add(Membership(user_id="u-revoked", scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=REAL, role="advisor", is_active=False))
    db.flush()

    u = db.query(User).filter(User.id == "u-inactive").first()
    u.is_active = False
    db.commit()
    db.close()


def main():
    print("=" * 78)
    print("GATE 31 - THE LEGACY COLUMN CANNOT MINT WORKSPACE ACCESS")
    print("=" * 78)
    build()

    with TestClient(app):
        db = SessionLocal()

        # STARTUP ALREADY RAN A BACKFILL, so a "fresh" pass here would find
        # everything already present and report zero creations - which is
        # correct behaviour and useless as evidence. The customer_org rows and
        # the completion marker are cleared so THIS gate observes the creation
        # itself, and the revoked row is deliberately left in place because
        # rule 5 is about exactly that row surviving a pass untouched.
        try:
            db.execute(sa_text("DELETE FROM system_config WHERE key = :k"),
                       {"k": wa.BACKFILL_COMPLETE_KEY})
        except Exception:
            pass
        db.query(Membership).filter(
            Membership.scope_type == SCOPE_CUSTOMER_ORG,
            Membership.is_active.is_(True)).delete(synchronize_session=False)
        db.commit()
        # A CRASH IS A NAMED FAILURE, NOT A TRACEBACK.
        #
        # Adding `is_active.is_(True)` to the existence check - the "obvious
        # fix" rule 5 warns against - makes the pass try to insert a second row
        # for a revoked member and hit the table's unique constraint. The
        # database was catching that, which is real protection, but the gate
        # died with a traceback and reported no failing check. A migration that
        # raises has failed whatever the reason, so it is caught and named.
        try:
            rep = wa.backfill_from_legacy_column(db, force=True)
        except Exception as exc:
            db.rollback()
            allowed("the backfill completes without raising",
                    False, "%s: %s" % (type(exc).__name__, str(exc)[:160]))
            rep = {"created": 0, "created_rows": [], "skipped_existing": 0,
                   "skipped_revoked": 0, "refused_stale_org": 0,
                   "refused_platform_org": 0, "candidates": 0, "ran": True,
                   "complete_at": None}

        # ── RULE 1: ONLY A VALID CUSTOMER WORKSPACE ─────────────────────────
        section("RULE 1 - only a real customer workspace may mint a membership")
        allowed("a genuine customer's column produced a membership",
                [(m.scope_id, m.role, m.is_active) for m in ws_rows(db, "u-good")]
                == [(REAL, "org_admin", True)],
                [(m.scope_id, m.role, m.is_active) for m in ws_rows(db, "u-good")])
        refused("a STALE id with no Organization row minted NOTHING",
                ws_rows(db, "u-ghost") == [], ws_rows(db, "u-ghost"))
        refused("   and it was reported as refused, not silently dropped",
                rep["refused_stale_org"] >= 1, rep)
        refused("the platform pseudo-org by ID minted NOTHING",
                ws_rows(db, "u-pseudo") == [], ws_rows(db, "u-pseudo"))
        refused("the platform pseudo-org by PLAN minted NOTHING",
                ws_rows(db, "u-fakeplat") == [], ws_rows(db, "u-fakeplat"))
        refused("   both platform refusals were counted",
                rep["refused_platform_org"] >= 1 or rep["refused_stale_org"] >= 2,
                rep)
        # A suspended customer IS a real workspace - the membership is written
        # and `authorized_contexts` is what hides a closed one.
        allowed("a SUSPENDED but real customer still got its membership",
                len(ws_rows(db, "u-suspended")) == 1,
                ws_rows(db, "u-suspended"))
        refused("   but the suspended workspace is NOT offered as a context",
                True, "asserted below against the live endpoint")
        refused("a DEACTIVATED account minted nothing",
                ws_rows(db, "u-inactive") == [], ws_rows(db, "u-inactive"))
        refused("god_admin minted nothing - the owner enters via X-Org-Override",
                ws_rows(db, "u-god") == [], ws_rows(db, "u-god"))

        # ── RULE 2: NEVER INFERRED FROM PLATFORM / BRAND SALES ──────────────
        section("RULE 2 - a platform or brand-sales relationship implies nothing")
        refused("the brand salesperson got NO customer membership",
                ws_rows(db, "u-sales") == [], ws_rows(db, "u-sales"))
        allowed("   while their brand-sales membership is untouched",
                db.query(Membership).filter(
                    Membership.user_id == "u-sales",
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.is_active.is_(True)).count() == 1)
        # The org exists and the platform exists; only the COLUMN decides, and
        # theirs is NULL.
        refused("   sharing a platform with a customer granted nothing",
                not wa.has_workspace(
                    db.query(User).filter(User.id == "u-sales").first(), db, REAL))

        # ── RULE 3: NEVER OVERWRITES / TRANSFERS THE COLUMN ─────────────────
        section("RULE 3 - users.organization_id is read, never written")
        cols = {u.id: u.organization_id for u in db.query(User).all()}
        allowed("every column value is exactly what the fixture set",
                cols["u-good"] == REAL and cols["u-ghost"] == GHOST
                and cols["u-pseudo"] == GOD_PLATFORM_ORG_ID
                and cols["u-sales"] is None
                and cols["u-suspended"] == SUSPENDED,
                cols)
        refused("   the stale id was NOT repaired, repointed or cleared",
                cols["u-ghost"] == GHOST, cols["u-ghost"])

        # ── RULE 5: NEVER RESURRECTS A REVOKED MEMBERSHIP ───────────────────
        section("RULE 5 - a revoked membership stays revoked")
        rev = ws_rows(db, "u-revoked")
        refused("the revoked membership was NOT reactivated",
                len(rev) == 1 and rev[0].is_active is False,
                [(m.scope_id, m.is_active) for m in rev])
        refused("   and no second, active row was added beside it",
                len(rev) == 1, len(rev))
        refused("   it was reported as deliberately left revoked",
                rep["skipped_revoked"] >= 1, rep)

        # ── RULE 7 (part): THE REPORT ───────────────────────────────────────
        section("the backfill REPORTS what it did, per row")
        created_ids = {r["user_id"] for r in rep["created_rows"]}
        allowed("every created membership is listed by user, org and role",
                created_ids == {"u-good", "u-suspended"},
                rep["created_rows"])
        allowed("   and the totals agree with the rows",
                rep["created"] == len(rep["created_rows"]) == 2, rep["created"])
        db.close()

        # ── RULE 4 + 6: NO DUPLICATES, IDEMPOTENT ───────────────────────────
        section("RULES 4 and 6 - re-running changes nothing")
        db = SessionLocal()
        before = db.query(Membership).filter(
            Membership.scope_type == SCOPE_CUSTOMER_ORG).count()
        rep2 = wa.backfill_from_legacy_column(db, force=True)
        after = db.query(Membership).filter(
            Membership.scope_type == SCOPE_CUSTOMER_ORG).count()
        refused("a second full pass created nothing", rep2["created"] == 0, rep2)
        refused("   and the total row count is unchanged",
                before == after, "%s -> %s" % (before, after))
        rev2 = ws_rows(db, "u-revoked")
        refused("   the revoked membership is STILL revoked after a re-run",
                len(rev2) == 1 and rev2[0].is_active is False,
                [(m.scope_id, m.is_active) for m in rev2])
        db.close()

        # ── RULE 7: IT STOPS BEING ABLE TO MINT ─────────────────────────────
        section("RULE 7 - once complete the column can no longer mint anything")
        db = SessionLocal()
        stamp = wa.backfill_is_complete(db)
        allowed("the pass that created nothing stamped completion",
                bool(stamp), stamp)

        # A brand new legacy-style user appears AFTER completion. Under the old
        # design the column would still mint for them forever. It must not.
        db.add(User(id="u-late", organization_id=REAL, email="late@real.test",
                    full_name="Late Arrival", password_hash=hash_password(PW),
                    role="org_admin", platform_id="plt-bb",
                    must_change_password=False, is_active=True))
        db.commit()
        rep3 = wa.backfill_from_legacy_column(db)
        refused("a later call does not even read the column",
                rep3["ran"] is False and rep3["candidates"] == 0, rep3)
        refused("   and the new legacy user got NO membership from it",
                ws_rows(db, "u-late") == [], ws_rows(db, "u-late"))
        allowed("   completion is reported back to the caller",
                rep3["complete_at"] == stamp, rep3["complete_at"])
        # The deliberate operator override still works - a switch, not a wall.
        rep4 = wa.backfill_from_legacy_column(db, force=True)
        allowed("an explicit force=True override still runs",
                rep4["ran"] is True, rep4["ran"])
        db.close()

        # ── LOGIN DOES NOT MINT AFTER COMPLETION EITHER ─────────────────────
        section("login is not a second door for the legacy column")
        db = SessionLocal()
        db.query(Membership).filter(Membership.user_id == "u-late").delete()
        db.commit()
        stamp2 = wa.backfill_is_complete(db)
        db.close()
        allowed("(fixture: still marked complete)", bool(stamp2), stamp2)
        c2 = TestClient(app)
        r = c2.post("/auth/login", data={"username": "late@real.test",
                                         "password": PW})
        allowed("the late user can still sign in", r.status_code == 200,
                "%s %s" % (r.status_code, r.text[:120]))
        db = SessionLocal()
        refused("   but signing in minted NO membership from the column",
                ws_rows(db, "u-late") == [], ws_rows(db, "u-late"))
        db.close()

        # ── AND THE STALE COLUMN GRANTS NOTHING AT REQUEST TIME ─────────────
        section("a stale column is not a back door at request time either")
        c3 = TestClient(app)
        ghost_tok = c3.post("/auth/login", data={"username": "ghost@nowhere.test",
                                                 "password": PW})
        allowed("the stale-column user can sign in", ghost_tok.status_code == 200,
                ghost_tok.status_code)
        gh = {"Authorization": "Bearer " + ghost_tok.json()["access_token"]}
        r = c3.get("/auth/my-contexts", headers=gh)
        j = r.json() if r.status_code == 200 else {}
        refused("their context list contains NO workspace",
                j.get("workspace_count") == 0, j.get("workspace_contexts"))
        r = c3.get("/auth/workspace/%s" % REAL, headers=gh)
        refused("   and they cannot enter a real customer by URL",
                r.status_code == 403, "%s %s" % (r.status_code, r.text[:120]))
        r = c3.get("/auth/workspace/%s" % GHOST, headers=gh)
        refused("   nor the ghost organization their own column names",
                r.status_code in (403, 404), "%s" % r.status_code)

        # The suspended customer is a membership that must NOT appear as a
        # context - the promise made in RULE 1's section above.
        susp_tok = c3.post("/auth/login", data={"username": "susp@suspended.test",
                                                "password": PW})
        st = {"Authorization": "Bearer " + susp_tok.json()["access_token"]}
        j = c3.get("/auth/my-contexts", headers=st).json()
        refused("a SUSPENDED customer is not offered as a context",
                j.get("workspace_count") == 0, j.get("workspace_contexts"))

        # And the revoked person, end to end.
        rev_tok = c3.post("/auth/login", data={"username": "revoked@real.test",
                                               "password": PW})
        rt = {"Authorization": "Bearer " + rev_tok.json()["access_token"]}
        j = c3.get("/auth/my-contexts", headers=rt).json()
        refused("the revoked user gets no context, even after logging in",
                j.get("workspace_count") == 0, j.get("workspace_contexts"))
        r = c3.get("/auth/workspace/%s" % REAL, headers=rt)
        refused("   and cannot enter the workspace they were removed from",
                r.status_code == 403, "%s" % r.status_code)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILED:
        print("\nTHE LEGACY COLUMN MINTED ACCESS (%d):" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
    if BROKEN:
        print("\nLEGITIMATE MIGRATION BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not FAILED and not BROKEN:
        print("\nTHE LEGACY COLUMN CANNOT MINT WORKSPACE ACCESS - and it is "
              "finished as a source.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (FAILED or BROKEN) else 0)


if __name__ == "__main__":
    main()
