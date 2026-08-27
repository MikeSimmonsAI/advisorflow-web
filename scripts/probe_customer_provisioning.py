"""GATE 26 - a customer can be provisioned end to end over HTTP, by a person.

The acceptance test the mission actually asks for: an owner with no shell, no
database client and no seed script logs in, creates a customer, gives it
locations and people, decides what it is entitled to, reads an honest summary,
and activates it. If any step here needed SQL, the workflow would not be done.

So this gate is written as that sequence, in order, and it fails if any step
cannot be reached from a browser.

It also pins the three rules the engine exists to enforce:
  · a customer ALWAYS belongs to a brand - no orphan organizations
  · ONE HUMAN, ONE IDENTITY - a person who exists is reused or refused, never
    duplicated
  · STATUS IS OBSERVED - the summary may not say CONFIGURED about a thing no
    backend can see

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="custprov_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, Organization, User     # noqa: E402
from app.models.location_models import Location, UserLocation        # noqa: E402
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                  # noqa: E402

PW = "ProbeTest!2026"
FAIL, PASSED = [], []


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:250]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt-evo", name="EvoSys Pro", slug="evo-cp"))
    db.add(Platform(id="plt-bab", name="BookaBoost", slug="bab-cp"))
    db.flush()
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                         slug="evo-sales-cp", timezone="America/Chicago"))
    # An existing, unrelated customer, so "already belongs elsewhere" is real.
    db.add(Organization(id="org-other", name="Other Customer", slug="other-cp",
                        platform_id="plt-bab", is_active=True))
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-god", "god@probe.test", "Platform Owner", "god_admin")
    mk("u-taken", "taken@probe-corp.com", "Already Elsewhere", "advisor", org="org-other")
    mk("u-seller", "seller@probe-corp.com", "Brand Seller", "advisor", org=None)
    db.flush()
    db.add(Membership(user_id="u-seller", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def main():
    print("=" * 78)
    print("GATE 26 - CUSTOMER PROVISIONING, END TO END, NO SHELL")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")

        section("STEP 1 - create the company")
        r = c.post("/god/customers", headers=god, json={
            "name": "SCI Test", "platform_id": "plt-evo", "industry": "funeral",
            "plan": "trial", "phone": "+15125550100",
            "primary_location": {
                "name": "Austin Chapel", "city": "Austin", "state": "TX",
                "phone": "+15125550101", "timezone": "America/Chicago",
                "operating_hours": {"mon": "09:00-17:00", "tue": "09:00-17:00"},
            },
        })
        created = r.json() if r.status_code == 201 else {}
        org_id = created.get("customer", {}).get("id")
        check("create customer returns 201", r.status_code == 201,
              "%s %s" % (r.status_code, r.text[:200]))
        check("...stamped with the brand", created.get("customer", {}).get("brand") == "EvoSys Pro",
              created.get("customer"))
        check("...and its primary location was created",
              (created.get("primary_location") or {}).get("is_primary") is True,
              created.get("primary_location"))

        r = c.post("/god/customers", headers=god, json={"name": "No Brand Co"})
        check("a customer with NO brand is refused", r.status_code == 422,
              "%s %s" % (r.status_code, r.text[:140]))
        r = c.post("/god/customers", headers=god,
                   json={"name": "No Brand Co", "platform_id": ""})
        check("...and an empty brand is refused with a reason naming the problem",
              r.status_code == 400 and "platform" in r.text.lower(),
              "%s %s" % (r.status_code, r.text[:200]))

        section("STEP 2 - locations")
        r = c.post("/god/customers/%s/locations" % org_id, headers=god, json={
            "name": "Round Rock Chapel", "city": "Round Rock", "state": "TX"})
        loc2 = r.json() if r.status_code == 201 else {}
        check("a second location can be added", r.status_code == 201,
              "%s %s" % (r.status_code, r.text[:160]))
        check("...and it is NOT automatically primary", loc2.get("is_primary") is False,
              loc2)
        check("...its hours are reported as NOT_CONFIGURED, not defaulted",
              loc2.get("operating_hours_status") == "NOT_CONFIGURED",
              loc2.get("operating_hours_status"))

        r = c.get("/god/customers/%s/locations" % org_id, headers=god)
        locs = r.json()["locations"]
        primaries = [l for l in locs if l["is_primary"]]
        check("exactly one location is primary", len(primaries) == 1,
              [(l["name"], l["is_primary"]) for l in locs])

        r = c.patch("/god/customers/%s/locations/%s" % (org_id, loc2["id"]),
                    headers=god, json={"is_primary": True})
        locs = c.get("/god/customers/%s/locations" % org_id, headers=god).json()["locations"]
        primaries = [l["name"] for l in locs if l["is_primary"]]
        check("promoting a location demotes the previous primary",
              primaries == ["Round Rock Chapel"], primaries)

        section("STEP 3/4 - people, canonically")
        r = c.get("/god/customers/%s/identity-lookup?email=NEW@Probe-Corp.com" % org_id,
                  headers=god)
        look = r.json()
        check("lookup normalises the email", look.get("email") == "new@probe-corp.com",
              look.get("email"))
        check("...and reports an unknown person as creatable",
              look.get("exists") is False and look.get("action") == "create", look)

        r = c.get("/god/customers/%s/identity-lookup?email=taken@probe-corp.com" % org_id,
                  headers=god)
        look = r.json()
        check("a human who belongs to ANOTHER customer is refused, not duplicated",
              look.get("can_add") is False and look.get("action") == "refuse", look)
        check("...and the refusal names the other customer",
              "Other Customer" in (look.get("reason") or ""), look.get("reason"))

        r = c.get("/god/customers/%s/identity-lookup?email=seller@probe-corp.com" % org_id,
                  headers=god)
        look = r.json()
        check("brand-sales staff are refused as customer users",
              look.get("can_add") is False and "brand-sales" in (look.get("reason") or "").lower(),
              look.get("reason"))

        r = c.get("/god/customers/%s/identity-lookup?email=god@probe.test" % org_id,
                  headers=god)
        check("the platform owner cannot be added to a customer",
              r.json().get("can_add") is False, r.json().get("reason"))

        before_users = SessionLocal().query(User).count()
        r = c.post("/god/customers/%s/users" % org_id, headers=god, json={
            "email": "fsa@probe-corp.com", "full_name": "Dana Reyes",
            "role": "org_admin", "location_ids": [loc2["id"]]})
        added = r.json() if r.status_code == 201 else {}
        check("adding a new person succeeds", r.status_code == 201,
              "%s %s" % (r.status_code, r.text[:200]))
        check("...and hands back a one-time setup link, never a password",
              bool(added.get("setup_url")) and "password" not in r.text.lower(),
              added.get("setup_url"))

        r = c.post("/god/customers/%s/users" % org_id, headers=god, json={
            "email": "taken@probe-corp.com", "full_name": "Already Elsewhere"})
        after_users = SessionLocal().query(User).count()
        check("adding a person who belongs elsewhere is refused",
              r.status_code == 409, "%s %s" % (r.status_code, r.text[:180]))
        check("...and NO duplicate user row was created",
              after_users == before_users + 1,
              "before=%s after=%s (expected +1, the new person only)"
              % (before_users, after_users))

        section("STEP 5 - features, enforced on the SERVER")
        r = c.get("/god/customers/%s/features" % org_id, headers=god)
        feats = r.json()
        check("a brand-new customer starts with NOTHING enabled",
              feats.get("enabled") == [], feats.get("enabled"))

        r = c.put("/god/customers/%s/features" % org_id, headers=god,
                  json={"enabled": ["leads", "booking", "not_a_real_feature"]})
        check("an unknown feature key is refused rather than silently stored",
              r.status_code == 400 and "not_a_real_feature" in r.text, r.text[:200])

        r = c.put("/god/customers/%s/features" % org_id, headers=god,
                  json={"enabled": ["leads", "booking", "calendar", "case_files"]})
        check("valid keys are stored", r.status_code == 200
              and set(r.json()["enabled"]) == {"leads", "booking", "calendar", "case_files"},
              r.json().get("enabled"))

        section("STEP 10 - an HONEST review")
        r = c.get("/god/customers/%s/readiness" % org_id, headers=god)
        rd = r.json()
        s = rd["sections"]
        check("readiness reads back", r.status_code == 200, r.status_code)
        check("SMS says NOT_CONFIGURED - no credentials are stored",
              s["communications_sms"]["status"] == "NOT_CONFIGURED",
              s["communications_sms"])
        check("...and explicitly says it has not called the provider",
              s["communications_sms"]["verified_against_provider"] is False,
              s["communications_sms"]["verified_against_provider"])
        check("email reports the shared-sender fallback rather than 'CONNECTED'",
              s["communications_email"]["status"] == "NOT_CONFIGURED",
              s["communications_email"]["reason"])
        check("calendar reports NOT_CONFIGURED with a real count",
              s["calendar"]["status"] == "NOT_CONFIGURED"
              and s["calendar"]["connected_count"] == 0, s["calendar"])
        check("AI says per-org config is not supported, rather than showing a tick",
              s["ai"]["per_org_config_supported"] is False, s["ai"])
        check("no section claims HEALTHY / ACTIVE / SYNCED anywhere",
              not any(str(v).upper().find("HEALTHY") >= 0
                      or str(v).upper().find("SYNCED") >= 0 for v in s.values()),
              [k for k, v in s.items()
               if "HEALTHY" in str(v).upper() or "SYNCED" in str(v).upper()])
        check("data says NONE - nothing imported", s["data"]["status"] == "NONE",
              s["data"])

        section("ACTIVATE - blockers refuse, warnings must be acknowledged")
        check("the customer is activatable (has a user, a location, features)",
              rd["can_activate"] is True, rd["blockers"])
        r = c.post("/god/customers/%s/activate" % org_id, headers=god, json={})
        check("activation with unacknowledged warnings is refused",
              r.status_code == 409 and "acknowledge_warnings" in r.text,
              "%s %s" % (r.status_code, r.text[:200]))
        r = c.post("/god/customers/%s/activate" % org_id, headers=god,
                   json={"acknowledge_warnings": True})
        check("activation with acknowledgement succeeds",
              r.status_code == 200 and r.json().get("activated") is True,
              "%s %s" % (r.status_code, r.text[:160]))

        section("A CUSTOMER WITH NO USER CANNOT BE ACTIVATED")
        r = c.post("/god/customers", headers=god,
                   json={"name": "Empty Co", "platform_id": "plt-evo"})
        empty_id = r.json()["customer"]["id"]
        r = c.get("/god/customers/%s/readiness" % empty_id, headers=god)
        rd2 = r.json()
        check("readiness lists the real blockers", rd2["can_activate"] is False
              and any("log in" in b for b in rd2["blockers"]), rd2["blockers"])
        r = c.post("/god/customers/%s/activate" % empty_id, headers=god,
                   json={"acknowledge_warnings": True})
        check("...and activation is refused", r.status_code == 409,
              "%s %s" % (r.status_code, r.text[:180]))

        section("NOBODY BUT THE OWNER CAN PROVISION")
        seller = token(c, "seller@probe-corp.com")
        for path, method in (("/god/customers", "GET"),
                             ("/god/customers", "POST"),
                             ("/god/customers/%s" % org_id, "GET"),
                             ("/god/customers/%s/users" % org_id, "POST"),
                             ("/god/customers/%s/features" % org_id, "PUT"),
                             ("/god/customers/%s/activate" % org_id, "POST")):
            r = c.request(method, path, headers=seller, json={})
            check("%s %s refuses a brand-sales user" % (method, path),
                  r.status_code == 403, r.status_code)

        section("THE PSEUDO-ORG IS NOT A CUSTOMER HERE EITHER")
        db = SessionLocal()
        try:
            db.add(Organization(id="org-god-platform", name="AdvisorFlow Platform",
                                slug="advisorflow-platform", plan="god", is_active=True))
            db.commit()
        finally:
            db.close()
        r = c.get("/god/customers", headers=god)
        ids = [x["id"] for x in r.json()["customers"]]
        check("it is absent from the customer list", "org-god-platform" not in ids, ids)
        r = c.get("/god/customers/org-god-platform", headers=god)
        check("...and cannot be opened as one", r.status_code == 404, r.status_code)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nA CUSTOMER CAN BE PROVISIONED END TO END WITHOUT A SHELL.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
