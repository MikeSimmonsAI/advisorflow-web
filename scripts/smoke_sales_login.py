"""
Login smoke test for brand-sales users (organization_id = NULL).

WHY THIS EXISTS
---------------
Making users.organization_id nullable broke login for exactly the users it was
added for, in two places, and neither is obvious from reading the seed:

  1. TokenResponse.organization_id was a non-optional `str`. A NULL-org user
     authenticates fine and then the RESPONSE fails validation -> 500.
  2. Platform isolation resolved the user's platform from their organization.
     With no organization it fell through to the legacy default "bookaboost",
     so an EvoSys Pro salesperson was refused on app.evosyspro.live with a
     generic 401 -- and would have been ACCEPTED on bookaboost.live, which is
     the cross-brand leak the check exists to prevent.

A brand-sales user's platform comes from their membership, not from a tenant
they do not have. These assertions lock that in.

Runs offline against a temp SQLite DB. Never touches production.

    python scripts/smoke_sales_login.py
"""
import os
import sys
import shutil
import tempfile

TMP = tempfile.mkdtemp(prefix="saleslogin_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient          # noqa: E402
from app.main import app                            # noqa: E402
from app.deps import SessionLocal, engine           # noqa: E402
from app.models.models import Base, Platform, Organization, User   # noqa: E402
from app.models.sales_models import (               # noqa: E402
    Membership, BrandSalesOrg, SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password  # noqa: E402

PW = "TempPass123!"
FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    evo = db.query(Platform).filter(Platform.slug == "evosyspro").first()
    if not evo:
        evo = Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro")
        db.add(evo)
    bb = db.query(Platform).filter(Platform.slug == "bookaboost").first()
    if not bb:
        bb = Platform(id="plt-bookaboost", name="BookaBoost", slug="bookaboost")
        db.add(bb)
    db.flush()

    sales_org = BrandSalesOrg(id="bso-evo", platform_id=evo.id,
                              name="EvoSys Pro Sales", slug="evosyspro-sales")
    bb_org = Organization(id="org-bb", name="Restland", slug="restland",
                          platform_id=bb.id)
    db.add_all([sales_org, bb_org])
    db.flush()

    # The brand-sales user: no customer tenancy at all.
    sales_user = User(id="usr-sales", organization_id=None,
                      email="salesrep@example.com", full_name="Sales Rep",
                      password_hash=hash_password(PW), role="advisor",
                      must_change_password=False, is_active=True)
    # A NULL-org user with NO membership. Must be refused everywhere.
    orphan = User(id="usr-orphan", organization_id=None,
                  email="orphan@example.com", full_name="Orphan",
                  password_hash=hash_password(PW), role="advisor",
                  must_change_password=False, is_active=True)
    # An ordinary BookaBoost tenant user, to prove nothing regressed.
    bb_user = User(id="usr-bb", organization_id=bb_org.id,
                   email="advisor@example.com", full_name="BB Advisor",
                   password_hash=hash_password(PW), role="advisor",
                   must_change_password=False, is_active=True)
    db.add_all([sales_user, orphan, bb_user])
    db.flush()

    db.add(Membership(user_id=sales_user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=sales_org.id, role=ROLE_SALES_REP, is_active=True))
    db.commit()
    db.close()


def login(client, email, origin):
    return client.post("/auth/login",
                       data={"username": email, "password": PW},
                       headers={"Origin": origin} if origin else {})


def main():
    build()
    client = TestClient(app)

    print("\n--- brand-sales user (organization_id = NULL) ---")
    r = login(client, "salesrep@example.com", "https://app.evosyspro.live")
    check("logs in on their OWN brand domain", r.status_code == 200,
          "%s %s" % (r.status_code, r.text[:200]))
    if r.status_code == 200:
        body = r.json()
        check("response carries organization_id = null",
              body.get("organization_id") is None, body)
        check("response carries their real role", body.get("role") == "advisor", body)

    r = login(client, "salesrep@example.com", "https://bookaboost.live")
    check("REFUSED on another brand's domain", r.status_code == 401,
          "%s %s" % (r.status_code, r.text[:200]))

    r = login(client, "salesrep@example.com", "https://advisorflow.example.com")
    check("REFUSED on the god domain", r.status_code == 401, r.status_code)

    print("\n--- NULL-org user with no membership: fail closed ---")
    for origin in ("https://app.evosyspro.live", "https://bookaboost.live"):
        r = login(client, "orphan@example.com", origin)
        check("orphan refused on %s" % origin.split("//")[1],
              r.status_code == 401, "%s %s" % (r.status_code, r.text[:200]))

    print("\n--- regression: ordinary tenant users unaffected ---")
    r = login(client, "advisor@example.com", "https://bookaboost.live")
    check("BookaBoost advisor logs in on bookaboost.live", r.status_code == 200,
          "%s %s" % (r.status_code, r.text[:200]))
    r = login(client, "advisor@example.com", "https://app.evosyspro.live")
    check("BookaBoost advisor refused on evosyspro.live", r.status_code == 401,
          r.status_code)
    r = login(client, "advisor@example.com", None)
    check("localhost / no origin still allowed for dev", r.status_code == 200,
          "%s %s" % (r.status_code, r.text[:200]))

    print("\n" + "=" * 62)
    if FAILURES:
        print("FAILED (%d): %s" % (len(FAILURES), "; ".join(FAILURES)))
        return 1
    print("ALL SALES LOGIN CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
