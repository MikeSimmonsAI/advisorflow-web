"""GATE 27 - THE TWO-GATE ADMINISTRATIVE DELEGATION MODEL.

    FEATURE ENABLED          the customer may USE the service
    SELF-MANAGEMENT ALLOWED  the ORGANIZATION may ADMINISTER the infrastructure
    ADMIN GRANT              THIS administrator actually holds it

Three states. Role alone is none of them.

This gate answers TWO questions throughout, because a guard that refuses
everything passes a leak probe perfectly and destroys the product. Every REFUSED
check is paired with an ALLOWED check proving the same route still works for the
person entitled to use it.

THE FIXTURE IS THE WORKED EXAMPLE FROM THE MISSION:

  Restland   sms enabled, self_manage_twilio NO
             -> advisors send all day, NOBODY there administers Twilio
             Rita   org_admin   (holds a grant that is INERT - gate 1 is shut)
             Andy   advisor

  Delegated  sms enabled, self_manage twilio_credentials + a2p_10dlc YES
             Jerome org_admin   manage_twilio YES  -> administers it
             Susan  org_admin   manage_twilio NO   -> refused
             Sam    advisor     granted nothing, and cannot be granted

Nothing here touches production. Every id is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="deleg_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from app.main import app                                              # noqa: E402
from app.deps import SessionLocal, engine                             # noqa: E402
from app.models.models import (                                       # noqa: E402
    Base, Platform, Organization, User, UserCapabilityGrant, AuditLogEntry,
)
from app.services.auth_service import hash_password                   # noqa: E402
from app.services import capabilities as caps                         # noqa: E402
from app.services import entitlements                                 # noqa: E402

PW = "ProbeTest!2026"
LEAKS, BROKEN, PASSED = [], [], []

REST_SID = "ACrestlandrestlandrestlandrestla"
DELEG_SID = "ACdelegateddelegateddelegatedde"


def refused(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "LEAK ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else LEAKS).append(label)


def allowed(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "BROKE", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def case(n, t):
    print("\n=== CASE %-2s %s " % (n, t) + "=" * max(0, 56 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt-1", name="Brand One", slug="brand-one"))
    db.flush()
    db.add_all([
        # RESTLAND: sms enabled, NOTHING delegated. The whole point.
        Organization(id="org-rest", name="Restland", slug="restland",
                     platform_id="plt-1", org_twilio_account_sid=REST_SID,
                     enabled_features='["sms", "leads", "users"]',
                     delegated_capabilities=None),
        # DELEGATED CO: sms enabled AND allowed to self-manage twilio + a2p.
        Organization(id="org-deleg", name="Delegated Co", slug="delegated",
                     platform_id="plt-1", org_twilio_account_sid=DELEG_SID,
                     enabled_features='["sms", "leads", "users"]',
                     delegated_capabilities='["twilio_credentials", "a2p_10dlc"]'),
        # NO SMS AT ALL: self-management is delegated but the feature is off,
        # so the FEATURE gate must refuse before the permission gate is reached.
        Organization(id="org-nosms", name="No SMS Co", slug="nosms",
                     platform_id="plt-1",
                     enabled_features='["leads", "users"]',
                     delegated_capabilities='["twilio_credentials"]'),
    ])
    db.flush()

    def mk(uid, email, name, role, org):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-god", "god@probe.test", "Owner", "god_admin", None)
    mk("u-rita", "rita@restland.test", "Rita", "org_admin", "org-rest")
    mk("u-andy", "andy@restland.test", "Andy", "advisor", "org-rest")
    mk("u-jerome", "jerome@deleg.test", "Jerome", "org_admin", "org-deleg")
    mk("u-susan", "susan@deleg.test", "Susan", "org_admin", "org-deleg")
    mk("u-sam", "sam@deleg.test", "Sam", "advisor", "org-deleg")
    mk("u-nick", "nick@nosms.test", "Nick", "org_admin", "org-nosms")
    db.flush()

    # GATE 2 rows. Jerome is granted and Rita is granted; the difference is that
    # Rita's organization is not allowed to self-manage, so hers is INERT. That
    # pair is what proves the two gates are independent rather than one gate
    # written twice.
    db.add_all([
        UserCapabilityGrant(id="g-1", user_id="u-jerome",
                            organization_id="org-deleg",
                            capability="twilio_credentials", is_active=True),
        UserCapabilityGrant(id="g-2", user_id="u-jerome",
                            organization_id="org-deleg",
                            capability="a2p_10dlc", is_active=True),
        UserCapabilityGrant(id="g-3", user_id="u-rita",
                            organization_id="org-rest",
                            capability="twilio_credentials", is_active=True),
        UserCapabilityGrant(id="g-4", user_id="u-nick",
                            organization_id="org-nosms",
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


def text(r):
    try:
        return r.text
    except Exception:
        return ""


def main():
    print("=" * 78)
    print("GATE 27 - ADMINISTRATIVE DELEGATION: THE TWO GATES")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        rita = token(c, "rita@restland.test")      # org_admin, gate 1 SHUT
        andy = token(c, "andy@restland.test")      # advisor
        jerome = token(c, "jerome@deleg.test")     # org_admin, BOTH gates open
        susan = token(c, "susan@deleg.test")       # org_admin, gate 2 shut
        sam = token(c, "sam@deleg.test")           # advisor
        nick = token(c, "nick@nosms.test")         # org_admin, no sms feature

        # ── CASE 1 ──────────────────────────────────────────────────────────
        case(1, "role alone grants NOTHING")
        r = c.get("/org-settings/twilio", headers=rita)
        refused("org_admin with no self-management cannot READ Twilio",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        refused("   and the refusal does not leak the Account SID",
                REST_SID not in text(r) and REST_SID[-4:] not in text(r), text(r)[:150])
        r = c.get("/org-settings/twilio", headers=susan)
        refused("org_admin in a DELEGATED org, without a grant, is still refused",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))

        # ── CASE 2 ──────────────────────────────────────────────────────────
        case(2, "the worked example: Restland uses SMS, administers nothing")
        allowed("Restland IS entitled to sms",
                entitlements.org_has_feature(
                    SessionLocal().query(Organization).get("org-rest"), "sms"), None)
        r = c.put("/org-settings/twilio", headers=rita,
                  json={"org_twilio_account_sid": "ACattackerattackerattackerattac",
                        "org_twilio_auth_token": "attacker-token"})
        refused("nobody at Restland can WRITE the Twilio credentials",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        refused("   and the stored Account SID is UNCHANGED",
                sid_of("org-rest") == REST_SID, sid_of("org-rest"))
        r = c.get("/10dlc/status", headers=rita)
        refused("nobody at Restland can reach A2P registration",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))

        # ── CASE 3 ──────────────────────────────────────────────────────────
        case(3, "Jerome YES / Susan NO, same org, same role")
        r = c.get("/org-settings/twilio", headers=jerome)
        allowed("Jerome (granted) READS his organization's Twilio settings",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))
        r = c.put("/org-settings/twilio", headers=jerome,
                  json={"org_twilio_account_sid": "ACjeromejeromejeromejeromejerom",
                        "org_twilio_auth_token": "jerome-token"})
        allowed("Jerome WRITES them",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))
        allowed("   and the write actually landed",
                (sid_of("org-deleg") or "").startswith("ACjerome"), sid_of("org-deleg"))
        r = c.put("/org-settings/twilio", headers=susan,
                  json={"org_twilio_account_sid": "ACsusansusansusansusansusansus",
                        "org_twilio_auth_token": "susan-token"})
        refused("Susan - identical role, same org, no grant - is refused",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        refused("   and Jerome's value survives her attempt",
                (sid_of("org-deleg") or "").startswith("ACjerome"), sid_of("org-deleg"))

        # ── GRANT AND REVOKE, THROUGH THE ACTUAL GOD ENDPOINT ───────────────
        #
        # Everything above this point read grants that the fixture inserted
        # directly, which proves the GATES work but says nothing about whether
        # the screen an owner actually uses can create an administrator. This
        # is the round trip: Susan is refused, God grants her, she works, God
        # revokes her, she is refused again - with the audit row each write is
        # supposed to leave.
        section("God can actually appoint and remove an administrator")
        r = c.put("/god/customers/org-deleg/users/u-susan/capabilities",
                  headers=god, json={"capabilities": ["twilio_credentials"]})
        allowed("God grants Susan twilio_credentials",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))
        r = c.put("/org-settings/twilio", headers=susan,
                  json={"org_twilio_account_sid": "ACsusansusansusansusansusansus",
                        "org_twilio_auth_token": "susan-token"})
        allowed("...and now Susan CAN administer Twilio",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))
        allowed("   and her write landed",
                (sid_of("org-deleg") or "").startswith("ACsusan"), sid_of("org-deleg"))

        r = c.put("/god/customers/org-deleg/users/u-susan/capabilities",
                  headers=god, json={"capabilities": []})
        allowed("God revokes it again", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:150]))
        r = c.put("/org-settings/twilio", headers=susan,
                  json={"org_twilio_account_sid": "ACnopenopenopenopenopenopenope",
                        "org_twilio_auth_token": "nope"})
        refused("...and Susan is refused once more",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        refused("   and the revoked write changed nothing",
                (sid_of("org-deleg") or "").startswith("ACsusan"), sid_of("org-deleg"))
        # The revoked row is deactivated, not deleted - "who held this in June"
        # is exactly the question an incident asks afterwards.
        db = SessionLocal()
        row = (db.query(UserCapabilityGrant)
               .filter(UserCapabilityGrant.user_id == "u-susan",
                       UserCapabilityGrant.capability == "twilio_credentials")
               .first())
        db.close()
        allowed("revocation DEACTIVATES the row rather than deleting the history",
                row is not None and row.is_active is False,
                None if row is None else row.is_active)

        # Jerome must keep working through all of that - revoking one person
        # is not revoking the organization.
        r = c.get("/org-settings/twilio", headers=jerome)
        allowed("Jerome is unaffected by Susan being granted and revoked",
                r.status_code == 200, "%s" % r.status_code)

        # ── CASE 4 ──────────────────────────────────────────────────────────
        case(4, "advisors never receive infrastructure administration")
        for who, hdr, org in (("Andy (Restland)", andy, "org-rest"),
                              ("Sam (Delegated Co)", sam, "org-deleg")):
            r = c.get("/org-settings/twilio", headers=hdr)
            refused("%s cannot read Twilio credentials" % who,
                    r.status_code == 403, "%s" % r.status_code)
            r = c.get("/10dlc/status", headers=hdr)
            refused("%s cannot reach A2P" % who, r.status_code == 403, "%s" % r.status_code)
        # ...and the refusal is not merely "no grant" - a grant CANNOT be made.
        r = c.put("/god/customers/org-deleg/users/u-sam/capabilities",
                  headers=god, json={"capabilities": ["twilio_credentials"]})
        refused("God CANNOT grant an advisor an infrastructure capability",
                r.status_code == 400, "%s %s" % (r.status_code, text(r)[:180]))
        refused("   and the refusal explains why",
                "administrator" in text(r).lower(), text(r)[:180])


        # ── CASE 5 ──────────────────────────────────────────────────────────
        case(5, "GATE 1 shut makes a personal grant INERT")
        # Rita HOLDS twilio_credentials in the database. Restland is not allowed
        # to self-manage it. If gate 1 were merely cosmetic, her grant would
        # work - this is the check that proves the gates are independent.
        db = SessionLocal()
        rita_grants = caps.grants_for(db, "u-rita", "org-rest")
        db.close()
        allowed("Rita genuinely HOLDS the grant in the database",
                "twilio_credentials" in rita_grants, rita_grants)
        r = c.get("/org-settings/twilio", headers=rita)
        refused("...and it does nothing, because her org may not self-manage",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        refused("   and the message names the organization, not her",
                "organization" in text(r).lower() or "AdvisorFlow" in text(r),
                text(r)[:180])

        # ── CASE 6 ──────────────────────────────────────────────────────────
        case(6, "revoking GATE 1 revokes everyone at once")
        r = c.put("/god/customers/org-deleg/self-management", headers=god,
                  json={"allowed": ["a2p_10dlc"]})     # twilio_credentials removed
        allowed("God removes twilio_credentials from the ORGANIZATION",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))
        r = c.get("/org-settings/twilio", headers=jerome)
        refused("Jerome - whose personal grant is untouched - is now refused",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        db = SessionLocal()
        still = caps.grants_for(db, "u-jerome", "org-deleg")
        db.close()
        allowed("   and his grant was NOT deleted, only made inert",
                "twilio_credentials" in still, still)
        # put it back for the remaining cases
        c.put("/god/customers/org-deleg/self-management", headers=god,
              json={"allowed": ["twilio_credentials", "a2p_10dlc"]})
        r = c.get("/org-settings/twilio", headers=jerome)
        allowed("re-delegating restores Jerome without re-granting him",
                r.status_code == 200, "%s" % r.status_code)

        # ── CASE 7 ──────────────────────────────────────────────────────────
        case(7, "FEATURE gate refuses before the permission gates")
        # Nick is an org_admin, holds the grant, and his org is delegated
        # twilio_credentials - but the org has no `sms` feature. Administering
        # the Twilio account of a customer with no SMS is not a permission
        # question, and answering 403 would send the operator hunting for a
        # grant that already exists.
        r = c.get("/org-settings/twilio", headers=nick)
        refused("no sms feature -> refused even with org + user permission",
                r.status_code == 402, "%s %s" % (r.status_code, text(r)[:180]))
        refused("   and it says the FEATURE is missing, not the permission",
                "sms" in text(r).lower(), text(r)[:180])

        # ── CASE 8 ──────────────────────────────────────────────────────────
        case(8, "God requires neither gate")
        for org in ("org-rest", "org-deleg", "org-nosms"):
            r = c.get("/org-settings/twilio", params={"org_id": org}, headers=god)
            allowed("god_admin reads Twilio for %s" % org,
                    r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/10dlc/status", headers={**god, "X-Org-Override": "org-rest"})
        allowed("god_admin reaches A2P inside a customer that delegates nothing",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))

        # ── CASE 9 ──────────────────────────────────────────────────────────
        case(9, "platform-wide capabilities can never be delegated")
        r = c.put("/god/customers/org-deleg/self-management", headers=god,
                  json={"allowed": ["twilio_credentials", "platform_billing"]})
        refused("delegating platform_billing is REFUSED, not stored",
                r.status_code == 400, "%s %s" % (r.status_code, text(r)[:180]))
        db = SessionLocal()
        after = caps.self_managed_by(
            db.query(Organization).get("org-deleg"))
        db.close()
        refused("   and nothing was written by the rejected call",
                "platform_billing" not in after, after)
        r = c.get("/billing/all", headers=jerome)
        refused("master billing refuses the most privileged customer admin",
                r.status_code == 403, "%s" % r.status_code)
        r = c.get("/billing/all", headers=god)
        allowed("...and still works for the owner",
                r.status_code == 200, "%s" % r.status_code)


        # ── CASE 10 ─────────────────────────────────────────────────────────
        case(10, "the closed surfaces: advisor Twilio, health, pricing")
        r = c.put("/settings/twilio", headers=andy,
                  json={"twilio_account_sid": "ACadvisoradvisoradvisoradvisor",
                        "twilio_auth_token": "advisor-token",
                        "twilio_phone_number": "+12145550000"})
        refused("an advisor can no longer write their OWN Twilio credentials",
                r.status_code == 410, "%s %s" % (r.status_code, text(r)[:150]))
        db = SessionLocal()
        andy_row = db.query(User).get("u-andy")
        wrote = getattr(andy_row, "twilio_account_sid", None)
        db.close()
        refused("   and nothing was written to their row",
                not wrote, wrote)
        r = c.put("/settings/twilio", headers=jerome,
                  json={"twilio_account_sid": "ACjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj",
                        "twilio_auth_token": "t", "twilio_phone_number": "+12145550001"})
        refused("   ...and neither can the most privileged customer admin",
                r.status_code == 410, "%s" % r.status_code)

        r = c.get("/health/advisor-status", headers=rita)
        refused("System Health refuses an org_admin", r.status_code == 403, "%s" % r.status_code)
        r = c.get("/health/advisor-status", headers=andy)
        refused("System Health refuses an advisor", r.status_code == 403, "%s" % r.status_code)
        r = c.get("/health/advisor-status", headers=god)
        allowed("System Health still works for the owner",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))

        r = c.get("/billing/plans")
        refused("GET /billing/plans is no longer unauthenticated",
                r.status_code in (401, 403), "%s" % r.status_code)
        refused("   and the price list is not in the body",
                "monthly_usd" not in text(r), text(r)[:150])
        r = c.get("/billing/plans", headers=rita)
        allowed("...and an org admin who may change the plan still reads it",
                r.status_code == 200, "%s" % r.status_code)

        # ── CASE 11 ─────────────────────────────────────────────────────────
        case(11, "the God control screen states three things, not one")
        r = c.get("/god/customers/org-rest/administration", headers=god)
        allowed("GET /administration succeeds", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:150]))
        rep = r.json() if r.status_code == 200 else {}
        for block in ("features", "self_management", "administrators"):
            allowed("   reports '%s' as its own block" % block,
                    block in rep, sorted(rep))
        allowed("FEATURES ENABLED says Restland may USE sms",
                "sms" in (rep.get("features", {}).get("enabled") or []),
                rep.get("features", {}).get("enabled"))
        allowed("SELF-MANAGEMENT says Restland may administer NOTHING",
                rep.get("self_management", {}).get("allowed") == [],
                rep.get("self_management", {}).get("allowed"))
        users = rep.get("administrators", {}).get("users") or []
        rita_row = next((u for u in users if u["email"] == "rita@restland.test"), None)
        allowed("AUTHORIZED ADMINISTRATORS lists Rita", rita_row is not None,
                [u["email"] for u in users])
        if rita_row:
            allowed("   showing her GRANT",
                    "twilio_credentials" in rita_row["capabilities"], rita_row)
            allowed("   and separately that it is NOT effective",
                    "twilio_credentials" not in rita_row["effective"], rita_row)
        allowed("advisors are not listed as possible administrators",
                all(u["email"] != "andy@restland.test" for u in users),
                [u["email"] for u in users])
        # The three states must be independently reachable, or the screen is
        # asking the operator to infer one from another.
        refused("platform-only capabilities carry a reason, not a live switch",
                all(c2.get("blocked_reason") for c2 in
                    rep.get("self_management", {}).get("available", [])
                    if not c2.get("delegable")), None)

        section("every write is audited")
        db = SessionLocal()
        actions = {a[0] for a in db.query(AuditLogEntry.action).all()}
        db.close()
        for a in ("customer.self_management_set", "customer.admin_capabilities_set"):
            allowed("audit row written: %s" % a, a in actions, sorted(actions))

        section("the sidebar asks the SERVER what it may show")
        r = c.get("/settings/my-capabilities", headers=jerome)
        allowed("/settings/my-capabilities answers for Jerome",
                r.status_code == 200 and "a2p_10dlc" in (r.json().get("capabilities") or []),
                text(r)[:200])
        r = c.get("/settings/my-capabilities", headers=andy)
        refused("...and gives an advisor an empty list",
                r.status_code == 200 and (r.json().get("capabilities") or []) == [],
                text(r)[:200])

        section("one feature vocabulary")
        import io as _io
        import re as _re
        def code_only(src):
            """Strip // and /* */ comments before matching.

            Without this the check trips on the comment that EXPLAINS the
            removal: Layout.jsx documents "A2P used to sit above with
            featureKey: 'a2p_10dlc'", and a naive scan reads that sentence as
            the very thing it is describing the absence of. A gate that fails
            on its own documentation teaches people to delete the
            documentation.
            """
            src = _re.sub(r"/\*.*?\*/", "", src, flags=_re.S)
            return _re.sub(r"//[^\n]*", "", src)

        lay = code_only(_io.open(
            os.path.join(REPO, "frontend/src/components/Layout.jsx"),
            encoding="utf-8").read())
        asked = set(_re.findall(r"featureKey:\s*'([^']+)'", lay))
        unknown = asked - set(entitlements.FEATURES)
        allowed("no sidebar featureKey is unknown to the server registry",
                not unknown, sorted(unknown))
        orgm = _io.open(os.path.join(REPO, "frontend/src/pages/OrgManager.jsx"),
                        encoding="utf-8").read()
        refused("OrgManager no longer carries its own feature list",
                "const ALL_FEATURES = [" not in orgm, None)
        refused("a2p_10dlc is not a FEATURE anywhere",
                "a2p_10dlc" not in entitlements.FEATURES
                and "'a2p_10dlc'" not in _re.sub(r"//[^\n]*", "", orgm),
                None)
        allowed("...and IS a capability", "a2p_10dlc" in caps.CAPABILITIES, None)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if LEAKS:
        print("\nDELEGATION LEAKS (%d):" % len(LEAKS))
        for f in LEAKS:
            print("  - %s" % f)
    if BROKEN:
        print("\nLEGITIMATE ACCESS BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not LEAKS and not BROKEN:
        print("\nTWO-GATE DELEGATION HOLDS - and every legitimate operation works.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (LEAKS or BROKEN) else 0)


if __name__ == "__main__":
    main()
