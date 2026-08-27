"""GATE 25 - the platform owner is neutral, and entering a context does not change that.

Two claims, both of which used to be false.

NEUTRAL. app/main.py created an organization called 'org-god-platform' on every
boot and made the owner a member of it. That is not a harmless placeholder:
about 179 of the ~182 tenant filters in this codebase read
`current_user.organization_id` without checking `_god_all_orgs`, so an owner with
no customer selected did not get an empty result or an error - they got the
pseudo-org's data, and anything they created was stamped with it. A lead
imported that way belonged to nobody and looked like it had vanished.

NO MEMBERSHIP FROM CONTEXT. Entering a customer administratively must not make
the owner a user of that customer. This gate counts membership rows before and
after every context operation and requires the number to be identical - the
claim is checked, not asserted.

The positive controls matter as much as the refusals here. An owner who cannot
read across all customers has not been made neutral, they have been made
useless, so "All Orgs" reads are exercised too.

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="platowner_")
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
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER,
)
from app.services.auth_service import hash_password                  # noqa: E402
from app.services import platform_owner as po                        # noqa: E402

PW = "ProbeTest!2026"
FAIL = []
PASSED = []

GOD_ORG = po.GOD_PLATFORM_ORG_ID


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evo-probe"),
                Platform(id="plt-bab", name="BookaBoost", slug="bab-probe")])
    db.flush()
    # The pseudo-org, present exactly as an existing production database has it.
    db.add(Organization(id=GOD_ORG, name="AdvisorFlow Platform",
                        slug="advisorflow-platform", plan="god", is_active=True))
    db.add_all([
        Organization(id="org-sci", name="SCI Test", slug="sci-test",
                     platform_id="plt-evo", is_active=True),
        Organization(id="org-b", name="Unrelated Test Customer", slug="cust-b",
                     platform_id="plt-bab", is_active=True),
    ])
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                         slug="evo-sales-probe", timezone="America/Chicago"))
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    # The owner starts ATTACHED to the pseudo-org - the state production is in.
    mk("u-god", "god@probe.test", "Platform Owner", "god_admin", org=GOD_ORG)
    mk("u-sci-admin", "admin@probe.test", "SCI Admin", "org_admin", org="org-sci")
    db.flush()
    # A legitimate secondary membership: the owner genuinely sells.
    db.add(Membership(user_id="u-god", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True))
    db.add_all([
        Lead(id="lead-sci", organization_id="org-sci", first_name="SCI", last_name="Lead",
             phone="+15550000011", status="new", created_at=datetime.utcnow()),
        Lead(id="lead-b", organization_id="org-b", first_name="Bravo", last_name="Lead",
             phone="+15550000012", status="new", created_at=datetime.utcnow()),
        Lead(id="lead-ghost", organization_id=GOD_ORG, first_name="Ghost",
             last_name="Lead", phone="+15550000013", status="new",
             created_at=datetime.utcnow()),
    ])
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def memberships(user_id="u-god"):
    db = SessionLocal()
    try:
        return db.query(Membership).filter(Membership.user_id == user_id).count()
    finally:
        db.close()


def col(model, ident, field):
    db = SessionLocal()
    try:
        row = db.query(model).filter(model.id == ident).first()
        return getattr(row, field) if row else None
    finally:
        db.close()


def ids_of(r, key="leads"):
    """Lead ids out of whatever shape /leads/ returns."""
    if r.status_code != 200:
        return []
    body = r.json()
    rows = body if isinstance(body, list) else (body.get(key) or body.get("items") or [])
    return [x.get("id") for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []


def leads(c, headers):
    # Trailing slash on purpose: the route is @router.get("/") under the
    # /leads prefix. Calling it without the slash gets a redirect, not the list,
    # which is how an earlier version of this gate managed to assert against an
    # empty body and believe it.
    return c.get("/leads/", headers=headers)


def main():
    print("=" * 78)
    print("GATE 25 - NEUTRAL PLATFORM OWNER + CONTEXT SWITCHING")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        in_sci = dict(god, **{"X-Org-Override": "org-sci"})

        section("NEUTRALITY - the owner has no tenant while none is selected")
        r = c.get("/god/platform/context", headers=god)
        body = r.json() if r.status_code == 200 else {}
        check("context with no override reports the PLATFORM level",
              r.status_code == 200 and body.get("level") == "platform"
              and body.get("is_neutral") is True, "%s %s" % (r.status_code, body))

        r = c.get("/god/platform/owner-state", headers=god)
        st = r.json() if r.status_code == 200 else {}
        check("owner-state SEES the stored pseudo-org attachment (audit, not silent fix)",
              r.status_code == 200 and st.get("attached_to_pseudo_org") is True,
              "%s attached=%s problems=%s" % (r.status_code,
                                              st.get("attached_to_pseudo_org"),
                                              st.get("problems")))
        check("...and reports the brand-sales seat as a LEGITIMATE secondary membership",
              any(m.get("verdict", "").startswith("legitimate")
                  for m in st.get("memberships", [])), st.get("memberships"))

        # The heart of it. NOT "the pseudo-org's rows are hidden" - an explicit
        # All-Orgs sweep should absolutely surface orphaned rows, that is how
        # the owner finds them to clean them up. The claim is narrower and more
        # important: the owner has no tenant of their own, so those rows are not
        # presented as HIS, and nothing new can be added to them (asserted
        # below). Hiding the orphans would just make them harder to fix.
        r = leads(c, headers=god)
        got = ids_of(r)
        check("a context-less owner has NO tenant of their own",
              c.get("/god/platform/context", headers=god).json().get("customer") is None,
              c.get("/god/platform/context", headers=god).json())
        check("...and the orphaned pseudo-org rows are still FINDABLE for cleanup",
              "lead-ghost" in got, "%s %s" % (r.status_code, got))

        section("NEUTRALITY - but the owner can still see everything (positive control)")
        check("...and 'All Orgs' still returns every real customer's leads",
              r.status_code == 200 and {"lead-sci", "lead-b"} <= set(got),
              "%s %s" % (r.status_code, got))

        section("WRITES REFUSE rather than guess a customer")
        r = c.post("/leads/create", headers=god, json={
            "first_name": "Orphan", "last_name": "Lead", "phone": "+15550000099"})
        refused = r.status_code >= 400
        check("creating a lead with NO customer selected is refused",
              refused, "%s %s" % (r.status_code, r.text[:160]))
        db = SessionLocal()
        try:
            ghosts = db.query(Lead).filter(Lead.organization_id == GOD_ORG).count()
        finally:
            db.close()
        check("...and nothing new landed in the platform pseudo-org",
              ghosts == 1, "pseudo-org lead count=%s (expected 1, the fixture's)" % ghosts)

        r = c.post("/leads/create", headers=in_sci, json={
            "first_name": "Real", "last_name": "Lead", "phone": "+15550000098"})
        check("creating a lead WITH a customer selected still works",
              r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:160]))

        section("THE PSEUDO-ORG IS NOT A CONTEXT")
        r = leads(c, headers=dict(god, **{"X-Org-Override": GOD_ORG}))
        check("X-Org-Override on the platform pseudo-org is refused",
              r.status_code == 400, "%s %s" % (r.status_code, r.text[:160]))

        r = c.post("/god/platform/context/customer/%s" % GOD_ORG, headers=god)
        check("entering the platform pseudo-org as a customer is refused",
              r.status_code == 400, "%s %s" % (r.status_code, r.text[:160]))

        r = c.get("/god/platform/overview", headers=god)
        ov = r.json() if r.status_code == 200 else {}
        listed = [o["id"] for p in ov.get("platforms", []) for o in []]  # noqa: F841
        r2 = c.get("/god/platform/brands/plt-evo/customers", headers=god)
        cust_ids = [x["id"] for x in r2.json().get("customers", [])] if r2.status_code == 200 else []
        check("the pseudo-org is excluded from the customer list",
              GOD_ORG not in cust_ids, "%s %s" % (r2.status_code, cust_ids))
        check("...and from the platform customer TOTAL",
              ov.get("totals", {}).get("customers") == 2,
              "totals=%s (expected 2 real customers)" % ov.get("totals"))

        section("ENTERING A CONTEXT CREATES NO MEMBERSHIP")
        before = memberships()
        r = c.post("/god/platform/context/customer/org-sci", headers=god)
        entered = r.json() if r.status_code == 200 else {}
        after = memberships()
        check("enter customer succeeds", r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:160]))
        check("NO membership row was created by entering",
              before == after == entered.get("memberships_after"),
              "before=%s after=%s reported=%s" % (before, after,
                                                  entered.get("memberships_after")))
        check("...and the owner did not become a member of the customer org",
              not any(m["scope_id"] == "org-sci" for m in
                      c.get("/god/platform/owner-state", headers=god).json()["memberships"]),
              "memberships=%s" % c.get("/god/platform/owner-state",
                                       headers=god).json()["memberships"])
        check("the response banner names the customer AND the brand",
              "SCI Test" in (entered.get("context", {}).get("banner") or "")
              and "EvoSys Pro" in (entered.get("context", {}).get("banner") or ""),
              entered.get("context", {}).get("banner"))

        section("CONTEXT ENTRY IS AUDITED TO THE DATABASE, NOT THE LOGGER")
        from app.models.models import AuditLogEntry
        db = SessionLocal()
        try:
            rows = (db.query(AuditLogEntry)
                    .filter(AuditLogEntry.action == "platform_owner.enter_customer").all())
            entry = rows[-1] if rows else None
            check("an audit row exists for entering the customer", entry is not None,
                  "rows=%d" % len(rows))
            if entry is not None:
                check("...naming the acting owner", entry.actor_user_id == "u-god",
                      entry.actor_user_id)
                check("...the customer organization", entry.organization_id == "org-sci",
                      entry.organization_id)
                check("...and the platform", entry.platform_id == "plt-evo",
                      entry.platform_id)
        finally:
            db.close()

        section("EXITING RETURNS THE OWNER TO NEUTRAL")
        before = memberships()
        r = c.post("/god/platform/context/exit", headers=in_sci)
        exited = r.json() if r.status_code == 200 else {}
        after = memberships()
        check("exit succeeds and reports the platform level",
              r.status_code == 200 and exited.get("context", {}).get("is_neutral") is True,
              "%s %s" % (r.status_code, r.text[:160]))
        check("exiting creates no membership either", before == after,
              "before=%s after=%s" % (before, after))
        r = c.get("/god/platform/context", headers=god)
        check("after exit the owner is neutral again",
              r.status_code == 200 and r.json().get("is_neutral") is True, r.text[:160])

        section("A TENANT ADMIN IS UNAFFECTED (positive control)")
        adm = token(c, "admin@probe.test")
        r = leads(c, headers=adm)
        got = ids_of(r)
        check("the customer admin still sees their own leads",
              r.status_code == 200 and "lead-sci" in got, "%s %s" % (r.status_code, got))
        check("...and not another customer's", "lead-b" not in got, got)
        r = c.get("/god/platform/overview", headers=adm)
        check("a customer admin cannot read the platform overview",
              r.status_code == 403, "%s %s" % (r.status_code, r.text[:120]))

        section("NEUTRALIZE - explicit, confirmed, and narrow")
        r = c.post("/god/platform/owner-neutralize", headers=god, json={"confirm": "yes"})
        check("neutralize without the exact confirmation phrase is refused",
              r.status_code == 400, "%s %s" % (r.status_code, r.text[:140]))
        check("...and the stored attachment is untouched",
              col(User, "u-god", "organization_id") == GOD_ORG,
              col(User, "u-god", "organization_id"))

        before = memberships()
        r = c.post("/god/platform/owner-neutralize", headers=god,
                   json={"confirm": "NEUTRALIZE PLATFORM OWNER"})
        check("neutralize with the exact phrase succeeds",
              r.status_code == 200 and r.json().get("changed") is True,
              "%s %s" % (r.status_code, r.text[:160]))
        check("...the stored organization_id is now NULL",
              col(User, "u-god", "organization_id") is None,
              col(User, "u-god", "organization_id"))
        check("...and MEMBERSHIPS WERE NOT TOUCHED", memberships() == before,
              "before=%s after=%s" % (before, memberships()))
        r = c.post("/god/platform/owner-neutralize", headers=god,
                   json={"confirm": "NEUTRALIZE PLATFORM OWNER"})
        check("neutralize is idempotent once already neutral",
              r.status_code == 200 and r.json().get("changed") is False, r.text[:140])

        section("STILL WORKS AFTER NEUTRALIZING (positive control)")
        god = token(c, "god@probe.test")
        in_sci = dict(god, **{"X-Org-Override": "org-sci"})
        r = leads(c, headers=god)
        got = ids_of(r)
        check("a neutral owner still reads every customer's leads",
              r.status_code == 200 and {"lead-sci", "lead-b"} <= set(got),
              "%s %s" % (r.status_code, got))
        r = leads(c, headers=in_sci)
        got = ids_of(r)
        check("...and inside a customer sees only that customer",
              r.status_code == 200 and "lead-sci" in got and "lead-b" not in got,
              "%s %s" % (r.status_code, got))
        r = c.get("/god/ops/customer-organizations", headers=god)
        check("the control plane still answers a neutral owner",
              r.status_code == 200, "%s %s" % (r.status_code, r.text[:140]))
        rows = r.json().get("organizations", []) if r.status_code == 200 else []
        check("...and the customer list does not count the platform as a customer",
              not any(x.get("organization_id") == GOD_ORG for x in rows),
              [x.get("organization_id") for x in rows])

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nTHE PLATFORM OWNER IS NEUTRAL - and context switching grants nothing.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
