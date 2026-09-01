"""GATE 24 - `?org_id=` cannot cross a brand boundary.

Three routers resolved an organization from a QUERY parameter for anyone holding
super_admin, loading it by id alone with no platform comparison:

    org_settings_router._resolve_org        13 endpoints, including
                                            PUT /org-settings/twilio, which
                                            writes org_twilio_account_sid and
                                            the encrypted auth token
    settings_router._resolve_appt_org       3 endpoints, read AND write
    crm_native_router  (inline)             1 endpoint, read

`require_super_admin` proves the caller is *a* platform operator. It says nothing
about *which* platform. So a super_admin on Brand A could pass a Brand B
customer's org id and read - or overwrite - that customer's Twilio credentials.

`load_org_in_scope` already existed for exactly this, and its own comment says
every route accepting an org_id must go through it. These three took the id as a
query parameter rather than a path parameter, which is how they were missed.

This gate answers TWO questions, because a guard that refuses everything passes a
leak probe perfectly and destroys the product. Every REFUSED check below is
paired with an ALLOWED check proving the same route still works for the operator
entitled to use it - the same-brand super_admin, the customer's own org_admin,
and the platform owner reaching across brands.

Fixture: two platforms, one customer org on each, a god_admin above both, a
super_admin per platform, and a customer org_admin inside Brand A's org.
Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="orgscope_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from app.main import app                                               # noqa: E402
from app.deps import SessionLocal, engine                              # noqa: E402
from app.models.models import (                                        # noqa: E402
    Base, Platform, Organization, User, AuditLogEntry, UserCapabilityGrant,
)
from app.services.auth_service import hash_password                    # noqa: E402

PW = "ProbeTest!2026"
LEAKS, BROKEN, PASSED = [], [], []

# Brand B's real Twilio credentials. If Brand A's operator can read this SID
# back, or replace it, the boundary is not a boundary.
B_SID = "ACbravobravobravobravobravobravob"


def refused(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "LEAK ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else LEAKS).append(label)


def allowed(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "BROKE", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 64 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-a", name="Brand A", slug="brand-a"),
                Platform(id="plt-b", name="Brand B", slug="brand-b")])
    db.flush()
    db.add_all([
        # BOTH ORGS DELEGATE twilio_credentials, AND BOTH SUPER ADMINS ARE
        # GRANTED IT BELOW. That is not this gate going soft - it is this gate
        # testing the thing it is named after.
        #
        # As of the delegation model, `/org-settings/twilio` requires the
        # organization to be allowed to self-manage AND the caller to hold the
        # capability. Without the fixture below, every request here is refused
        # by `require_capability` BEFORE `_resolve_org` ever runs - so the
        # cross-brand checks would "pass" while proving nothing about the
        # platform boundary, and the legitimate-access checks would fail for a
        # reason that has nothing to do with brands.
        #
        # Opening the delegation gates puts the boundary back under test: a
        # fully entitled operator, refused solely because the org belongs to
        # another brand. probe_delegation.py owns the other question - that an
        # operator WITHOUT these grants is refused - and it must stay owned
        # there rather than being half-asserted in two places.
        Organization(id="org-a", name="Alpha Cemetery", slug="alpha", platform_id="plt-a",
                     enabled_features='["sms"]',
                     delegated_capabilities='["twilio_credentials", "twilio_numbers"]'),
        # Brand B's entitlements start at a KNOWN, NON-EMPTY value. If they
        # started as NULL, "unchanged" would also be true after a successful
        # cross-brand write of NULL, and the assertion would prove nothing.
        Organization(id="org-b", name="Bravo Funeral Home", slug="bravo", platform_id="plt-b",
                     org_twilio_account_sid=B_SID,
                     enabled_features='["campaigns", "reports", "sms"]',
                     delegated_capabilities='["twilio_credentials", "twilio_numbers"]'),
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
    mk("u-sa-b", "sa.b@probe.test", "Super B", "super_admin", org="org-b", platform="plt-b")
    mk("u-oa-a", "oa.a@probe.test", "Org Admin A", "org_admin", org="org-a")
    db.flush()

    # GATE 2 for the two platform operators, so the only thing that can refuse
    # them below is the brand boundary. See the comment on org-a above.
    db.add_all([
        UserCapabilityGrant(id="cap-sa-a", user_id="u-sa-a", organization_id="org-a",
                            capability="twilio_credentials", is_active=True),
        UserCapabilityGrant(id="cap-sa-a2", user_id="u-sa-a", organization_id="org-a",
                            capability="twilio_numbers", is_active=True),
        UserCapabilityGrant(id="cap-sa-b", user_id="u-sa-b", organization_id="org-b",
                            capability="twilio_credentials", is_active=True),
    ])
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def sid_of(org_id):
    db = SessionLocal()
    try:
        row = db.query(Organization).filter(Organization.id == org_id).first()
        return getattr(row, "org_twilio_account_sid", None) if row else None
    finally:
        db.close()


def features_of(org_id):
    """The org's stored allow-list, read straight from the column.

    Read from the DATABASE, never from a response body. A route can return
    {"updated": true} and have written nothing, or refuse and have written
    anyway; the column is the only thing that says which actually happened.
    """
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Organization).filter(Organization.id == org_id).first()
        raw = getattr(row, "enabled_features", None) if row else None
        if raw is None:
            return None
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            return raw
    finally:
        db.close()


def audit_has(action):
    """True if an audit row with this action exists. The god-side writer has
    always written one; the super_admin-side writer used not to.

    The model is `AuditLogEntry` (table `audit_log_entries`). Imported at module
    scope, NOT inside a try/except: a rename would then raise here and stop the
    gate, rather than being swallowed into a quiet False that reads as a real
    failure of the code under test.
    """
    db = SessionLocal()
    try:
        return db.query(AuditLogEntry).filter(
            AuditLogEntry.action == action).count() > 0
    finally:
        db.close()


def body_text(r):
    try:
        return r.text
    except Exception:
        return ""


def main():
    print("=" * 78)
    print("GATE 24 - CROSS-BRAND ?org_id= SCOPING")
    print("=" * 78)
    build()

    if not hasattr(User, "platform_id"):
        print("\n!! User.platform_id is absent - get_platform_org_ids scopes a")
        print("!! super_admin by that column, so this gate cannot mean anything.")
        sys.exit(1)

    with TestClient(app) as c:
        god, sa_a, sa_b, oa_a = (token(c, "god@probe.test"), token(c, "sa.a@probe.test"),
                                 token(c, "sa.b@probe.test"), token(c, "oa.a@probe.test"))

        # ── THE HEADLINE: Brand A's operator reaching Brand B ────────────────
        section("cross-brand org settings")
        r = c.get("/org-settings/", params={"org_id": "org-b"}, headers=sa_a)
        refused("GET /org-settings/?org_id=<other brand> is 404",
                r.status_code == 404, "%s %s" % (r.status_code, body_text(r)[:120]))
        refused("   and the other brand's org name never appears in the body",
                "Bravo" not in body_text(r), body_text(r)[:160])

        section("cross-brand TWILIO READ")
        r = c.get("/org-settings/twilio", params={"org_id": "org-b"}, headers=sa_a)
        refused("GET /org-settings/twilio?org_id=<other brand> is 404",
                r.status_code == 404, "%s %s" % (r.status_code, body_text(r)[:120]))
        refused("   and Brand B's Account SID is not in the response",
                B_SID not in body_text(r) and B_SID[-4:] not in body_text(r),
                body_text(r)[:200])

        section("cross-brand TWILIO WRITE")
        before = sid_of("org-b")
        # A SCHEMA-VALID payload, deliberately. An invalid one 422s during
        # validation, before the org is ever resolved, and would let this gate
        # pass while the boundary was wide open.
        r = c.put("/org-settings/twilio", params={"org_id": "org-b"}, headers=sa_a,
                  json={"org_twilio_account_sid": "ACattackerattackerattackerattack",
                        "org_twilio_auth_token": "attacker-token"})
        refused("PUT /org-settings/twilio?org_id=<other brand> is refused",
                r.status_code in (403, 404), "%s %s" % (r.status_code, body_text(r)[:120]))
        refused("   the refusal is 404, not 422 - the guard ran, not validation",
                r.status_code != 422, "%s" % r.status_code)
        after = sid_of("org-b")
        refused("   and Brand B's stored Account SID is UNCHANGED",
                after == before == B_SID, "before=%s after=%s" % (before, after))

        # ── THE ONE THAT WAS MISSED THE FIRST TIME ──────────────────────────
        #
        # PATCH /org-settings/features never called _resolve_org, so the sweep
        # that fixed the other thirteen endpoints in this file never found it.
        # It loaded the Organization by the raw query-parameter id after
        # checking only that the caller held super_admin - which means Brand A's
        # operator could switch Brand B's customer's paid features off, or on.
        #
        # Entitlement is what the customer is BUYING. Being able to rewrite
        # another brand's customer's entitlements is not a smaller hole than the
        # Twilio one above; it is the same hole pointed at the invoice.
        section("cross-brand FEATURE ENTITLEMENT WRITE")
        before_feats = features_of("org-b")
        r = c.patch("/org-settings/features", params={"org_id": "org-b"}, headers=sa_a,
                    json={"enabled_features": []})
        refused("PATCH /org-settings/features?org_id=<other brand> is refused",
                r.status_code in (403, 404), "%s %s" % (r.status_code, body_text(r)[:120]))
        refused("   the refusal is 404, not 422 - the guard ran, not validation",
                r.status_code != 422, "%s" % r.status_code)
        after_feats = features_of("org-b")
        refused("   and Brand B's stored entitlements are UNCHANGED",
                after_feats == before_feats,
                "before=%s after=%s" % (before_feats, after_feats))

        # BOTH WRITERS TO THIS COLUMN MUST NOW OBEY THE SAME RULES. The god path
        # has always rejected an unregistered key and written an audit row; this
        # path used to json.dumps() whatever it was handed. That difference is
        # exactly how `a2p_10dlc` and `master_dashboard` ended up stored in a
        # column whose registry has never contained them, which is the "unknown
        # feature key" warning on the God Features screen.
        section("the two feature writers share one set of rules")
        r = c.patch("/org-settings/features", params={"org_id": "org-a"}, headers=sa_a,
                    json={"enabled_features": ["campaigns", "not_a_real_feature"]})
        refused("PATCH /org-settings/features rejects an UNREGISTERED key",
                r.status_code == 400, "%s %s" % (r.status_code, body_text(r)[:160]))
        refused("   and the rejection names the key",
                "not_a_real_feature" in body_text(r), body_text(r)[:160])
        refused("   and nothing was written",
                "not_a_real_feature" not in str(features_of("org-a")), features_of("org-a"))
        # `sms` IS IN THIS LIST DELIBERATELY. This write REPLACES org-a's
        # entitlements, and the Twilio checks further down need the org to still
        # own `sms` - `require_capability("twilio_credentials")` refuses with 402
        # when the underlying feature is absent, because administering the Twilio
        # account of a customer with no SMS is not a permission question.
        # Dropping sms here made those later checks fail for a reason that had
        # nothing to do with the boundary this gate exists to test.
        r = c.patch("/org-settings/features", params={"org_id": "org-a"}, headers=sa_a,
                    json={"enabled_features": ["campaigns", "reports", "sms"]})
        allowed("...and a VALID key list still writes normally",
                r.status_code == 200, "%s %s" % (r.status_code, body_text(r)[:160]))
        allowed("   and it persisted",
                set(features_of("org-a") or []) == {"campaigns", "reports", "sms"},
                features_of("org-a"))
        allowed("   and it wrote a customer.features_set audit row",
                audit_has("customer.features_set"), "no audit row found")

        section("cross-brand appointment types and CRM stages")
        r = c.get("/settings/appointment-types", params={"org_id": "org-b"}, headers=sa_a)
        refused("GET /settings/appointment-types?org_id=<other brand> is refused",
                r.status_code in (403, 404), "%s" % r.status_code)
        r = c.put("/settings/appointment-types", params={"org_id": "org-b"}, headers=sa_a,
                  json={"appointment_types": ["Injected Appointment"]})
        refused("PUT /settings/appointment-types?org_id=<other brand> is refused",
                r.status_code in (403, 404), "%s %s" % (r.status_code, body_text(r)[:120]))
        r = c.get("/crm/stages", params={"org_id": "org-b"}, headers=sa_a)
        refused("GET /crm/stages?org_id=<other brand> is refused",
                r.status_code in (403, 404), "%s" % r.status_code)

        # ── AND THE PRODUCT STILL WORKS ─────────────────────────────────────
        section("same-brand super_admin can still do their job")
        r = c.get("/org-settings/", params={"org_id": "org-a"}, headers=sa_a)
        allowed("GET /org-settings/?org_id=<own brand> succeeds",
                r.status_code == 200, "%s %s" % (r.status_code, body_text(r)[:120]))
        r = c.get("/org-settings/twilio", params={"org_id": "org-a"}, headers=sa_a)
        allowed("GET /org-settings/twilio?org_id=<own brand> succeeds",
                r.status_code == 200, "%s" % r.status_code)
        r = c.put("/org-settings/twilio", params={"org_id": "org-a"}, headers=sa_a,
                  json={"org_twilio_account_sid": "ACalphaalphaalphaalphaalphaalpha",
                        "org_twilio_auth_token": "alpha-token"})
        allowed("PUT /org-settings/twilio?org_id=<own brand> succeeds",
                r.status_code in (200, 204), "%s %s" % (r.status_code, body_text(r)[:120]))
        allowed("   and it actually persisted",
                (sid_of("org-a") or "").startswith("ACalpha"), sid_of("org-a"))

        section("Brand B's own operator is unaffected")
        r = c.get("/org-settings/twilio", params={"org_id": "org-b"}, headers=sa_b)
        allowed("Brand B's super_admin still reads Brand B's Twilio settings",
                r.status_code == 200, "%s" % r.status_code)

        section("the platform owner still reaches every brand")
        for org in ("org-a", "org-b"):
            r = c.get("/org-settings/", params={"org_id": org}, headers=god)
            allowed("god_admin reads %s across platforms" % org,
                    r.status_code == 200, "%s %s" % (r.status_code, body_text(r)[:120]))

        section("a customer's own org_admin is unchanged")
        r = c.get("/org-settings/", headers=oa_a)
        allowed("org_admin reads their OWN org with no org_id",
                r.status_code == 200, "%s" % r.status_code)
        r = c.get("/org-settings/", params={"org_id": "org-b"}, headers=oa_a)
        refused("org_admin passing another org's id gets their own org, never Bravo",
                r.status_code != 200 or "Bravo" not in body_text(r),
                body_text(r)[:160])

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
        print("\nORG_ID SCOPING HOLDS - and every legitimate operation still works.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (LEAKS or BROKEN) else 0)


if __name__ == "__main__":
    main()
