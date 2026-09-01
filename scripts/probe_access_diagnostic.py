"""GATE 32 - THE GOD-ONLY ACCESS DIAGNOSTIC.

A diagnostic that reads one named person's identity, memberships, workspace
resolution and lead counts is exactly as sensitive as the database shell it
replaces. Three things therefore have to be true and stay true:

  1. GOD ONLY. Not super_admin, not org_admin, not an advisor, not a brand
     sales manager, not a capability grant. `require_god` and nothing else.
  2. IT WRITES NOTHING. Not a row, not a column. A diagnosis that edits the
     patient is not a diagnosis - and this one runs against production.
  3. IT LEAKS NO SECRET. No DATABASE_URL, no connection string, no token, no
     key, no password, no environment variable.

And it has to be CORRECT, so the fixture builds the four situations a
missing-leads report actually turns out to be, and asserts the diagnostic names
each one rather than returning a shrug.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="diag_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from app.main import app                                              # noqa: E402
from app.deps import SessionLocal, engine                             # noqa: E402
from app.models.models import Base, Platform, Organization, User, Lead  # noqa: E402
from app.models.sales_models import (                                 # noqa: E402
    Membership, BrandSalesOrg, SCOPE_CUSTOMER_ORG, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER,
)
from app.services.auth_service import hash_password                   # noqa: E402

PW = "ProbeTest!2026"
FAILED, BROKEN, PASSED = [], [], []
ORG = "org-restland-like"
OTHER = "org-other-customer"

# Every shape of secret this must never emit. Checked against the WHOLE body.
SECRET_MARKERS = [
    "DATABASE_URL", "postgres://", "postgresql://", "sqlite://",
    "password_hash", "auth_token", "twilio_auth", "api_key", "API_KEY",
    "SECRET_KEY", "JWT_SECRET", "access_token", "session_token",
    "refresh_token", "encrypted", "connection_string", "OPENAI",
]


def refused(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "OPEN ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else FAILED).append(label)


def allowed(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "BROKE", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"))
    db.flush()
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                         name="EvoSys Sales", slug="evosys-sales"))
    db.add_all([
        Organization(id=ORG, name="Restland Cemetery", slug="restland-like",
                     platform_id="plt-evo", plan="standard"),
        Organization(id=OTHER, name="Other Customer", slug="other-customer",
                     platform_id="plt-evo", plan="standard"),
    ])
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    platform_id="plt-evo", must_change_password=False,
                    is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    # THE FOUR SHAPES A MISSING-LEADS REPORT TURNS OUT TO BE.
    mk("u-healthy", "healthy@rest.test", "Healthy Advisor", "advisor", ORG)
    mk("u-noleads", "noleads@rest.test", "No Leads Advisor", "advisor", ORG)
    mk("u-revoked", "revoked@rest.test", "Revoked Advisor", "advisor", ORG)
    mk("u-nomember", "nomember@rest.test", "No Membership", "advisor", ORG)
    # The people who must be REFUSED the diagnostic.
    mk("u-superadmin", "super@evo.test", "Evo Super Admin", "super_admin", ORG)
    mk("u-orgadmin", "orgadmin@rest.test", "Restland Org Admin", "org_admin", ORG)
    mk("u-salesmgr", "salesmgr@evo.test", "Brand Sales Manager", "advisor")
    mk("u-god", "god@probe.test", "Owner", "god_admin")
    db.flush()

    db.add(Membership(user_id="u-salesmgr", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True))
    for uid in ("u-healthy", "u-noleads", "u-superadmin", "u-orgadmin"):
        db.add(Membership(user_id=uid, scope_type=SCOPE_CUSTOMER_ORG,
                          scope_id=ORG,
                          role=("org_admin" if uid in ("u-superadmin", "u-orgadmin")
                                else "advisor"),
                          is_active=True))
    # Revoked, deliberately: "no membership" and "membership switched off" are
    # different diagnoses and the report has to tell them apart.
    db.add(Membership(user_id="u-revoked", scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=ORG, role="advisor", is_active=False))
    db.flush()

    for n in range(7):
        db.add(Lead(id="ld-h%d" % n, organization_id=ORG,
                    assigned_to_id="u-healthy", first_name="Lead",
                    last_name="HEALTHY%d" % n, phone="+1214555%04d" % n,
                    email="ld-h%d@ex.test" % n, status="new", tier="pre_need"))
    for n in range(5):
        db.add(Lead(id="ld-o%d" % n, organization_id=ORG,
                    assigned_to_id="u-orgadmin", first_name="Lead",
                    last_name="OTHERADV%d" % n, phone="+1214556%04d" % n,
                    email="ld-o%d@ex.test" % n, status="new", tier="pre_need"))
    for n in range(3):
        db.add(Lead(id="ld-x%d" % n, organization_id=OTHER,
                    assigned_to_id=None, first_name="Lead",
                    last_name="OTHERORG%d" % n, phone="+1214557%04d" % n,
                    email="ld-x%d@ex.test" % n, status="new", tier="pre_need"))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def snapshot(db):
    """Every row count that a diagnostic must not change."""
    return {
        "users": db.query(User).count(),
        "leads": db.query(Lead).count(),
        "memberships": db.query(Membership).count(),
        "orgs": db.query(Organization).count(),
        "assigned": {u.id: db.query(Lead).filter(
            Lead.assigned_to_id == u.id).count() for u in db.query(User).all()},
        "member_state": {(m.user_id, m.scope_id, m.scope_type): m.is_active
                         for m in db.query(Membership).all()},
        "org_col": {u.id: u.organization_id for u in db.query(User).all()},
    }


DIAG = "/god/ops/diagnostics/user-access"


def main():
    print("=" * 78)
    print("GATE 32 - GOD-ONLY USER ACCESS DIAGNOSTIC")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")

        # THE "NO MEMBERSHIP" SHAPE HAS TO BE MADE AFTER STARTUP.
        #
        # u-nomember has a legacy organization_id, so the startup backfill
        # correctly gives them a membership - which is the product working. The
        # shape being tested is the one that actually reaches production: a user
        # who sits in an organization with NO membership row, because they were
        # created before the migration and nothing has run for them yet. So the
        # row is removed here, after boot, rather than the fixture pretending
        # the backfill does not exist.
        db = SessionLocal()
        db.query(Membership).filter(
            Membership.user_id == "u-nomember",
            Membership.scope_type == SCOPE_CUSTOMER_ORG).delete(
                synchronize_session=False)
        db.commit()
        db.close()

        # ── AUTHORIZATION ───────────────────────────────────────────────────
        section("god only - and every other role is refused")
        r = c.get(DIAG, params={"email": "healthy@rest.test"}, headers=god)
        allowed("God can run the diagnostic", r.status_code == 200,
                "%s %s" % (r.status_code, r.text[:140]))

        for who, email in (("an EvoSys super_admin", "super@evo.test"),
                           ("a customer org_admin", "orgadmin@rest.test"),
                           ("a plain advisor", "healthy@rest.test"),
                           ("a brand sales manager", "salesmgr@evo.test")):
            hdr = token(c, email)
            r = c.get(DIAG, params={"email": "healthy@rest.test"}, headers=hdr)
            refused("%s is DENIED" % who, r.status_code in (401, 403, 404),
                    "%s %s" % (r.status_code, r.text[:120]))
            refused("   and gets no diagnostic body",
                    "customer_workspace_memberships" not in r.text, r.text[:160])

        r = c.get(DIAG, params={"email": "healthy@rest.test"})
        refused("an unauthenticated caller is DENIED",
                r.status_code in (401, 403), "%s" % r.status_code)

        # ── IT WRITES NOTHING ───────────────────────────────────────────────
        section("the diagnostic performs no writes")
        db = SessionLocal()
        before = snapshot(db)
        db.close()
        for email in ("healthy@rest.test", "noleads@rest.test",
                      "revoked@rest.test", "nomember@rest.test"):
            c.get(DIAG, params={"email": email}, headers=god)
        db = SessionLocal()
        after = snapshot(db)
        db.close()
        refused("row counts are unchanged after four runs",
                before["users"] == after["users"]
                and before["leads"] == after["leads"]
                and before["orgs"] == after["orgs"],
                "%s -> %s" % (before["users"], after["users"]))
        refused("   NO membership was created, deleted or reactivated",
                before["member_state"] == after["member_state"]
                and before["memberships"] == after["memberships"],
                "%s -> %s" % (before["memberships"], after["memberships"]))
        refused("   no lead changed hands", before["assigned"] == after["assigned"])
        refused("   and users.organization_id is untouched",
                before["org_col"] == after["org_col"])

        # ── IT LEAKS NO SECRET ──────────────────────────────────────────────
        section("the report contains results, never credentials")
        r = c.get(DIAG, params={"email": "healthy@rest.test"}, headers=god)
        body = r.text
        for marker in SECRET_MARKERS:
            refused("no '%s' anywhere in the body" % marker,
                    marker not in body,
                    body[max(0, body.find(marker) - 60):body.find(marker) + 60]
                    if marker in body else "")

        # ── IT IS CORRECT: THE HEALTHY ADVISOR ──────────────────────────────
        section("a healthy advisor reads as healthy")
        j = r.json()
        allowed("identity resolves", j["identity"]["email"] == "healthy@rest.test",
                j["identity"])
        allowed("   the legacy column is reported",
                j["identity"]["legacy_organization_id"] == ORG,
                j["identity"]["legacy_organization_id"])
        ws = j["customer_workspace_memberships"]
        allowed("   one ACTIVE customer_org membership, named",
                len(ws) == 1 and ws[0]["state"] == "active"
                and ws[0]["organization_name"] == "Restland Cemetery", ws)
        prim = j["workspace_scenarios"][0]
        allowed("   the no-header scenario resolves their workspace",
                prim["resolved_workspace_id"] == ORG, prim)
        allowed("   effective workspace role is advisor",
                prim["effective_workspace_role"] == "advisor",
                prim["effective_workspace_role"])
        allowed("   A raw assigned = 7", prim["A_raw_assigned"] == 7, prim)
        allowed("   B lead_scope   = 7", prim["B_lead_scope_count"] == 7, prim)
        allowed("   C /leads service = 7",
                j["endpoint_service_counts"].get("C_leads_service_total") == 7,
                j["endpoint_service_counts"])
        allowed("   D status funnel = 7",
                j["endpoint_service_counts"].get("D_status_funnel_total") == 7,
                j["endpoint_service_counts"])
        allowed("   the organization total (12) is shown beside their 7",
                prim["organization_total_leads"] == 12,
                prim["organization_total_leads"])
        allowed("   and it says so in one line",
                any("counts agree" in f for f in j["findings"]), j["findings"])
        allowed("   timings are recorded", bool(j["timings_ms"]), j["timings_ms"])

        # ── IT IS CORRECT: EACH FAILING SHAPE IS NAMED ──────────────────────
        section("each failing shape is named, not shrugged at")
        j = c.get(DIAG, params={"email": "noleads@rest.test"}, headers=god).json()
        p = j["workspace_scenarios"][0]
        allowed("ZERO ASSIGNED: membership is fine, ownership is empty",
                p["A_raw_assigned"] == 0 and p["B_lead_scope_count"] == 0
                and len(j["customer_workspace_memberships"]) == 1, p)
        allowed("   and the finding says nothing is being hidden",
                any("nothing owned to hide" in f for f in j["findings"]),
                j["findings"])

        j = c.get(DIAG, params={"email": "revoked@rest.test"}, headers=god).json()
        allowed("REVOKED: the membership is shown, labelled REVOKED",
                len(j["customer_workspace_memberships"]) == 1
                and j["customer_workspace_memberships"][0]["state"] == "REVOKED",
                j["customer_workspace_memberships"])
        allowed("   and the finding says access was removed deliberately",
                any("REVOKED" in f for f in j["findings"]), j["findings"])
        allowed("   with zero authorized workspace contexts",
                j["authorized_contexts"]["workspace_count"] == 0,
                j["authorized_contexts"]["workspace_count"])

        j = c.get(DIAG, params={"email": "nomember@rest.test"}, headers=god).json()
        allowed("NO MEMBERSHIP: reported as none at all",
                j["customer_workspace_memberships"] == [], j["findings"])
        allowed("   and the finding says the switcher shows no button",
                any("no button" in f for f in j["findings"]), j["findings"])

        j = c.get(DIAG, params={"email": "orgadmin@rest.test"}, headers=god).json()
        p = j["workspace_scenarios"][0]
        allowed("MANAGER: scoped EXCEEDS raw and the reason is named",
                p["B_lead_scope_count"] == 12 and p["A_raw_assigned"] == 5
                and p["divergence"] and "manager" in p["divergence"],
                (p["A_raw_assigned"], p["B_lead_scope_count"], p["divergence"]))

        # ── LOOKUP DISCIPLINE ───────────────────────────────────────────────
        section("exact lookup only - never a guess")
        r = c.get(DIAG, params={"email": "HEALTHY@REST.TEST"}, headers=god)
        allowed("an exact email differing only in case still resolves",
                r.status_code == 200, r.status_code)
        r = c.get(DIAG, params={"email": "healthy"}, headers=god)
        refused("a PARTIAL email is refused, not fuzzily matched",
                r.status_code == 404, "%s %s" % (r.status_code, r.text[:120]))
        r = c.get(DIAG, params={"user_id": "u-healthy"}, headers=god)
        allowed("lookup by exact user id works", r.status_code == 200,
                r.status_code)
        r = c.get(DIAG, headers=god)
        refused("no identifier at all is refused", r.status_code == 400,
                "%s" % r.status_code)

        # ── AUDIT ───────────────────────────────────────────────────────────
        section("every run is audited")
        db = SessionLocal()
        from app.models.models import AuditLogEntry
        n = db.query(AuditLogEntry).filter(
            AuditLogEntry.action == "user_access_diagnostic").count()
        allowed("audit rows were written for the runs", n >= 4, n)
        rows = db.query(AuditLogEntry).filter(
            AuditLogEntry.action == "user_access_diagnostic").all()
        blob = " ".join((r_.details or "") + (r_.note or "") for r_ in rows)
        refused("   and the audit trail carries no lead counts for a named person",
                "A_raw_assigned" not in blob and "lead_scope" not in blob,
                blob[:200])
        db.close()

        # ── THE OPERATOR'S OWN HEADERS ARE NEVER READ ───────────────────────
        section("the operator's session cannot contaminate the diagnosis")
        r1 = c.get(DIAG, params={"email": "healthy@rest.test"}, headers=god)
        r2 = c.get(DIAG, params={"email": "healthy@rest.test"},
                   headers={**god, "X-Workspace-Id": OTHER})
        a = r1.json()["workspace_scenarios"][0]
        b = r2.json()["workspace_scenarios"][0]
        refused("god sending a workspace header changes nothing in the report",
                a["resolved_workspace_id"] == b["resolved_workspace_id"] == ORG
                and a["A_raw_assigned"] == b["A_raw_assigned"],
                (a["resolved_workspace_id"], b["resolved_workspace_id"]))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAILED:
        print("\nDIAGNOSTIC SAFETY FAILURES (%d):" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
    if BROKEN:
        print("\nDIAGNOSTIC CORRECTNESS BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not FAILED and not BROKEN:
        print("\nGOD ONLY, READ ONLY, NO SECRETS - and it names the actual cause.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (FAILED or BROKEN) else 0)


if __name__ == "__main__":
    main()
