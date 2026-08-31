"""Brand-sales user creation and membership management — deploy gate 22.

THE GAP THIS CLOSES. Until now nothing anywhere created a
`SCOPE_BRAND_SALES_ORG` membership: `POST /god/users` made a users row with no
seat, and every real membership in production came from a seed script. Two
people existed, were correct, and could not log in. This suite proves the flow
that replaced that, and proves the things it must never do.

FOUR PROPERTIES, ASSERTED RATHER THAN ASSUMED:

  1. ONE HUMAN, ONE IDENTITY. An email that already belongs to somebody reuses
     that row and preserves every membership they hold, including customer ones.
     No second users row, ever - including for a differently-cased address.

  2. NO TENANCY IS INVENTED. A new brand-sales identity gets organization_id
     NULL. Not a default, not "the first organization", not the brand's own
     customers. A person who ALREADY had a tenancy keeps exactly the one they had.

  3. NO PLAINTEXT PASSWORD, ANYWHERE. Not from the new routes, and not from the
     four legacy ones that used to return one. Asserted against live response
     bodies, not by reading the source.

  4. DEACTIVATION IS NOT DELETION. The seat closes, the workspace closes, and
     the opportunities, meetings and audit rows survive untouched - as do the
     person's memberships in other brands.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="salesstaff_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, Organization, User      # noqa: E402
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, Membership, Opportunity, SCOPE_BRAND_SALES_ORG,
    SCOPE_CUSTOMER_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.staff_models import StaffActivation                  # noqa: E402
from app.services.auth_service import hash_password                  # noqa: E402

PW = "SmokeTest!2026"
FAILURES = []

# Every key that has ever carried a live credential in this codebase.
PW_KEYS = ("temp_password", "temporary_password", "password", "new_password",
           "generated_password", "plain_password")


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def no_password_anywhere(label, resp):
    """A response body must not carry a credential under ANY known key, at any
    depth. Checked on the parsed body rather than by eyeballing the route."""
    try:
        body = resp.json()
    except Exception:
        return
    found = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in PW_KEYS and v not in (None, ""):
                    found.append("%s.%s" % (path, k))
                walk(v, path + "." + k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))

    walk(body)
    check("%s returns NO password field" % label, not found, found)


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"),
                Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                      name="EvoSys Pro Sales", slug="evosyspro-sales"),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb",
                      name="BookaBoost Sales", slug="bookaboost-sales"),
    ])
    db.add(Organization(id="org-cust", name="Greenland Cemetery",
                        slug="greenland", platform_id="plt-evo"))
    db.flush()

    def mk(uid, email, name, org=None, role="advisor"):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-god", "god@probe.test", "Platform Owner", role="god_admin")
    mk("u-mgr", "mgr@probe.test", "Team Manager")
    mk("u-rep", "rep@probe.test", "Working Rep")
    mk("u-bbmgr", "bbmgr@probe.test", "Other Brand Manager")
    # The existing-identity case: a real customer advisor inside a tenant.
    mk("u-advisor", "advisor@probe.test", "Dana Reyes", org="org-cust")
    db.flush()
    db.add_all([
        Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bbmgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_MANAGER, is_active=True),
        # A customer-scope membership that must survive everything below.
        Membership(id="mem-cust", user_id="u-advisor",
                   scope_type=SCOPE_CUSTOMER_ORG, scope_id="org-cust",
                   role="org_admin", is_active=True),
    ])
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def users_with(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).all()
    finally:
        db.close()


def memberships_of(user_id):
    db = SessionLocal()
    try:
        return db.query(Membership).filter(Membership.user_id == user_id).all()
    finally:
        db.close()


ADD = "/god/ops/brands/bso-evo/sales-team"


# ── 1. permissions ──────────────────────────────────────────────────────────

def test_permissions(c, god, mgr, rep, advisor):
    section("Only GOD may create or change a sales seat")
    body = {"email": "nobody@probe.test", "role": "sales_rep", "full_name": "No Body"}
    for label, hdr, expect in (("anonymous", {}, 401),
                               ("a sales REP", rep, 403),
                               ("a sales MANAGER of this brand", mgr, 403),
                               ("a customer admin", advisor, 403),
                               ("another brand's manager", None, 403)):
        if hdr is None:
            continue
        r = c.post(ADD, json=body, headers=hdr)
        check("%s cannot add a sales user" % label, r.status_code == expect,
              "%s %s" % (r.status_code, r.text[:130]))
        r = c.get("/god/ops/brands/bso-evo/identity-lookup?email=x@y.co", headers=hdr)
        check("%s cannot even look an identity up" % label, r.status_code == expect,
              r.status_code)

    check("nobody was created by any refused attempt",
          len(users_with("nobody@probe.test")) == 0)

    r = c.post(ADD, json=body, headers=god)
    check("GOD can add a sales user", r.status_code == 200, r.text[:200])
    no_password_anywhere("add-sales-user", r)

    # A manager must still be able to READ their team - the read was never
    # god-only and this work must not have quietly narrowed it.
    r = c.get("/god/ops/brands/bso-evo/sales-team", headers=mgr)
    check("a sales manager can STILL read their own team", r.status_code == 200,
          r.status_code)


# ── 2. a brand new rep ──────────────────────────────────────────────────────

def test_new_rep(c, god):
    section("Acceptance: a brand new rep, created from nothing")
    r = c.post(ADD, json={"email": "Test.Rep@Probe.TEST", "role": "sales_rep",
                          "full_name": "Test Sales Rep",
                          "base_url": "https://app.evosyspro.live"}, headers=god)
    check("the request succeeds", r.status_code == 200, r.text[:250])
    if r.status_code != 200:
        return None
    body = r.json()
    no_password_anywhere("new rep creation", r)

    rows = users_with("test.rep@probe.test")
    check("EXACTLY ONE users identity exists", len(rows) == 1, len(rows))
    check("...and the email was normalised to lowercase",
          bool(rows) and rows[0].email == "test.rep@probe.test",
          rows[0].email if rows else None)
    u = rows[0]
    check("organization_id is NULL - no tenant was invented",
          u.organization_id is None, u.organization_id)
    check("users.role stays the baseline tenant role, not a sales string",
          u.role == "advisor", u.role)
    check("they are forced to set their own password", u.must_change_password is True)
    check("they have a hash (nothing treats them as password-less)",
          bool(u.password_hash))

    mems = [m for m in memberships_of(u.id)
            if m.scope_type == SCOPE_BRAND_SALES_ORG]
    check("EXACTLY ONE brand-sales membership", len(mems) == 1, len(mems))
    if mems:
        check("...in EvoSys Pro Sales", mems[0].scope_id == "bso-evo")
        check("...with role sales_rep", mems[0].role == ROLE_SALES_REP, mems[0].role)
        check("...and it is active", mems[0].is_active is True)
        check("...recording who granted it", mems[0].granted_by == "u-god")

    check("a setup link came back", bool(body.get("setup_url")), body.get("setup_url"))
    check("...on the activate page with an stf_ token",
          "/activate?token=stf_" in (body.get("setup_url") or ""),
          body.get("setup_url"))
    check("the response says the identity was created",
          body["user"]["created"] is True)
    return body.get("setup_url")


def test_new_rep_can_actually_log_in(c, setup_url):
    section("...and that link actually produces a working login")
    token_str = (setup_url or "").split("token=")[-1]
    r = c.get("/auth/staff-activation?token=" + token_str)
    check("the link previews before a password is set", r.status_code == 200,
          r.text[:150])
    if r.status_code == 200:
        check("...naming the brand they will land in",
              r.json().get("workspace") == "EvoSys Pro Sales", r.json())

    r = c.post("/auth/staff-activation/accept",
               json={"token": token_str, "new_password": "ChosenByThem!2026"})
    check("they set their OWN password", r.status_code == 200, r.text[:200])
    no_password_anywhere("activation accept", r)

    r = c.post("/auth/login", data={"username": "test.rep@probe.test",
                                    "password": "ChosenByThem!2026"})
    check("and can then log in", r.status_code == 200, r.text[:150])
    if r.status_code != 200:
        return None
    hdr = {"Authorization": "Bearer " + r.json()["access_token"]}

    r = c.get("/sales/me", headers=hdr)
    check("they land in a sales workspace", r.status_code == 200, r.text[:150])
    if r.status_code == 200:
        me = r.json()
        check("...as a REP", me["role"] == ROLE_SALES_REP, me["role"])
        check("...with organization_id still NULL",
              me["user"]["organization_id"] is None)
        check("...and no team permissions",
              me["permissions"]["view_team_pipeline"] is False
              and me["permissions"]["reassign_opportunity"] is False,
              me["permissions"])

    section("...and the new rep is contained")
    for path, what in (("/sales/manager/overview", "Team Command"),
                       ("/sales/manager/approvals", "the approval queue"),
                       ("/sales/appointments?scope=team", "the team calendar")):
        check("the new rep is refused %s" % what,
              c.get(path, headers=hdr).status_code == 403)
    check("...and cannot reassign",
          c.post("/sales/opportunities/opp-x/reassign",
                 json={"owner_user_id": "u-mgr"}, headers=hdr).status_code == 403)
    for path, what in (("/god/ops/brands", "god brands"),
                       ("/god/ops/customer-organizations", "customer organisations"),
                       ("/god/users", "god user administration"),
                       ("/god/ops/sales-operations", "god sales operations")):
        check("...and cannot reach %s" % what,
              c.get(path, headers=hdr).status_code == 403)
    check("...and cannot reach tenant leads",
          c.get("/leads", headers=hdr).status_code == 403)
    return hdr


# ── 3. an existing identity ─────────────────────────────────────────────────

def test_existing_identity(c, god):
    section("Acceptance: an EXISTING person, given a seat they did not have")
    before = memberships_of("u-advisor")
    check("the advisor starts with one customer membership",
          len(before) == 1 and before[0].scope_type == SCOPE_CUSTOMER_ORG,
          [(m.scope_type, m.role) for m in before])

    r = c.get("/god/ops/brands/bso-evo/identity-lookup?email=ADVISOR@probe.test",
              headers=god)
    check("the lookup finds them by a differently-cased address",
          r.status_code == 200 and r.json().get("exists") is True, r.text[:200])
    if r.status_code == 200:
        look = r.json()
        check("...and shows their existing tenancy so it is not a surprise",
              look.get("organization_id") == "org-cust", look.get("organization_id"))
        check("...and lists the memberships they already hold",
              len(look.get("memberships") or []) == 1, look.get("memberships"))
        check("...and says they are not in this brand yet",
              look.get("already_in_this_brand") is False)

    r = c.post(ADD, json={"email": "ADVISOR@probe.test", "role": "sales_rep",
                          "full_name": "SHOULD BE IGNORED"}, headers=god)
    check("the seat is granted", r.status_code == 200, r.text[:200])
    no_password_anywhere("existing identity", r)

    rows = users_with("advisor@probe.test")
    check("STILL exactly one users row - no duplicate identity", len(rows) == 1, len(rows))
    check("...and their name was NOT overwritten by the form",
          rows[0].full_name == "Dana Reyes", rows[0].full_name)
    check("...and their EXISTING tenancy is untouched",
          rows[0].organization_id == "org-cust", rows[0].organization_id)

    after = memberships_of("u-advisor")
    kinds = sorted((m.scope_type, m.role, bool(m.is_active)) for m in after)
    check("their customer membership SURVIVED",
          (SCOPE_CUSTOMER_ORG, "org_admin", True) in kinds, kinds)
    check("...and the new sales seat sits beside it",
          (SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP, True) in kinds, kinds)
    check("...two memberships in total, not three", len(after) == 2, kinds)

    # Idempotency: adding the same person again must not stack a second seat.
    r = c.post(ADD, json={"email": "advisor@probe.test", "role": "sales_rep"},
               headers=god)
    check("adding them AGAIN is accepted", r.status_code == 200, r.text[:200])
    check("...and still leaves exactly two memberships",
          len(memberships_of("u-advisor")) == 2,
          [(m.scope_type, m.role) for m in memberships_of("u-advisor")])
    if r.status_code == 200:
        check("...reported as a reuse, not a creation",
              r.json()["membership"]["created"] is False)


# ── 4. a manager, roles, reporting line ─────────────────────────────────────

def test_manager_and_roles(c, god):
    section("Acceptance: a manager, a role change and a reporting line")
    r = c.post(ADD, json={"email": "test.manager@probe.test",
                          "role": "sales_manager",
                          "full_name": "Test Sales Manager"}, headers=god)
    check("a manager can be created", r.status_code == 200, r.text[:200])
    if r.status_code != 200:
        return
    mgr_id = r.json()["user"]["id"]
    mem_id = r.json()["membership"]["id"]

    r = c.post(ADD, json={"email": "reporting.rep@probe.test", "role": "sales_rep",
                          "full_name": "Reporting Rep",
                          "reports_to_user_id": mgr_id}, headers=god)
    check("a rep can be created reporting to that manager",
          r.status_code == 200, r.text[:220])
    rep_mem = r.json()["membership"]["id"] if r.status_code == 200 else None
    if r.status_code == 200:
        check("...and the reporting line is stored",
              r.json()["membership"]["reports_to_user_id"] == mgr_id)

    # The org chart must not accept nonsense.
    r = c.post(ADD, json={"email": "bad.line@probe.test", "role": "sales_rep",
                          "full_name": "Bad Line",
                          "reports_to_user_id": "u-rep"}, headers=god)
    check("a REP cannot be named as somebody's reporting manager",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:150]))
    r = c.post(ADD, json={"email": "bad.line2@probe.test", "role": "sales_rep",
                          "full_name": "Bad Line Two",
                          "reports_to_user_id": "u-bbmgr"}, headers=god)
    check("ANOTHER BRAND's manager cannot be named either",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:150]))

    # Role vocabulary.
    r = c.post(ADD, json={"email": "specialist@probe.test",
                          "role": "product_specialist",
                          "full_name": "Product Specialist"}, headers=god)
    check("an unsupported role is REFUSED, not written",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:170]))
    check("...and no identity was created for it",
          len(users_with("specialist@probe.test")) == 0)

    # Promote the reporting rep to manager.
    if rep_mem:
        r = c.patch("/god/ops/sales-memberships/" + rep_mem,
                    json={"role": "sales_manager"}, headers=god)
        check("a rep can be promoted to manager", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            row = r.json()["membership"]
            check("...the role changed on the SAME seat", row["id"] == rep_mem)
            check("...and their reporting line was cleared, not left stale",
                  row["reports_to_user_id"] is None, row)


# ── 5. deactivation ─────────────────────────────────────────────────────────

def test_deactivation(c, god, rep_hdr):
    section("Acceptance: deactivation closes the workspace, destroys nothing")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "test.rep@probe.test").first()
        m = (db.query(Membership)
               .filter(Membership.user_id == u.id,
                       Membership.scope_type == SCOPE_BRAND_SALES_ORG).first())
        uid, mid = u.id, m.id
        # Give them something to lose.
        now = datetime.utcnow()
        db.add(Opportunity(id="opp-hist", brand_sales_org_id="bso-evo",
                           owner_user_id=uid, company_name="Their Deal",
                           stage="discovery", status="open",
                           created_at=now, stage_changed_at=now))
        db.commit()
    finally:
        db.close()

    check("they can reach the workspace before deactivation",
          c.get("/sales/me", headers=rep_hdr).status_code == 200)

    r = c.patch("/god/ops/sales-memberships/" + mid,
                json={"is_active": False}, headers=god)
    check("the seat is deactivated", r.status_code == 200, r.text[:200])

    r = c.get("/sales/me", headers=rep_hdr)
    check("the sales workspace is now CLOSED to them",
          r.status_code == 403, "%s %s" % (r.status_code, r.text[:150]))
    check("...and so is their pipeline",
          c.get("/sales/opportunities", headers=rep_hdr).status_code == 403)

    db = SessionLocal()
    try:
        check("the MEMBERSHIP ROW still exists - deactivated, not deleted",
              db.query(Membership).filter(Membership.id == mid).first() is not None)
        check("their users row still exists",
              db.query(User).filter(User.id == uid).first() is not None)
        opp = db.query(Opportunity).filter(Opportunity.id == "opp-hist").first()
        check("the opportunity they owned SURVIVED", opp is not None)
        check("...and still names them as owner",
              opp is not None and opp.owner_user_id == uid)
        from app.models.models import AuditLogEntry
        n = (db.query(AuditLogEntry)
               .filter(AuditLogEntry.target_id == uid).count())
        check("their audit history survived", n >= 2, n)
        acts = db.query(StaffActivation).filter(StaffActivation.user_id == uid).count()
        check("their activation history survived", acts >= 1, acts)
    finally:
        db.close()

    r = c.patch("/god/ops/sales-memberships/" + mid,
                json={"is_active": True}, headers=god)
    check("the seat can be switched back on", r.status_code == 200, r.text[:200])
    r = c.post("/auth/login", data={"username": "test.rep@probe.test",
                                    "password": "ChosenByThem!2026"})
    if r.status_code == 200:
        h = {"Authorization": "Bearer " + r.json()["access_token"]}
        check("...and the workspace reopens", c.get("/sales/me", headers=h).status_code == 200)


def test_other_memberships_survive_deactivation(c, god):
    section("Deactivating one seat leaves the person's OTHER memberships alone")
    db = SessionLocal()
    try:
        m = (db.query(Membership)
               .filter(Membership.user_id == "u-advisor",
                       Membership.scope_type == SCOPE_BRAND_SALES_ORG).first())
        mid = m.id
    finally:
        db.close()

    r = c.patch("/god/ops/sales-memberships/" + mid,
                json={"is_active": False}, headers=god)
    check("their sales seat is deactivated", r.status_code == 200, r.text[:150])

    after = memberships_of("u-advisor")
    cust = [m for m in after if m.scope_type == SCOPE_CUSTOMER_ORG]
    check("their CUSTOMER membership is still there", len(cust) == 1, len(cust))
    check("...and still ACTIVE - it was never touched",
          bool(cust) and cust[0].is_active is True)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == "u-advisor").first()
        check("...and their tenancy is intact", u.organization_id == "org-cust")
    finally:
        db.close()


# ── 6. no plaintext password on ANY user-creating route ─────────────────────

def test_no_plaintext_passwords(c, god):
    """The four routes that used to hand a live credential back to the caller.

    Two fixture details that are easy to get wrong and make this suite lie:

      * `super_admin` is a TENANT role, so /god/users refuses it without an
        org_id - deliberately, since guessing an organisation is the bug that
        once put a sales team inside a funeral home. Use god_admin, which is
        genuinely tenant-less.
      * admin_router validates with EmailStr, which rejects the reserved `.test`
        TLD. A 422 has no password field in it, so these assertions would have
        passed while proving nothing. Real addresses, real 200s, real proof.
    """
    section("No user-creating route returns a plaintext password")
    r = c.post("/god/users", json={"email": "godmade@probe-corp.com",
                                   "full_name": "God Made",
                                   "role": "god_admin"}, headers=god)
    check("POST /god/users still works", r.status_code in (200, 201), r.text[:200])
    no_password_anywhere("POST /god/users", r)
    if r.status_code in (200, 201):
        check("...and hands over a one-time link instead",
              bool(r.json().get("setup_url")), r.json())
        check("...and did not invent a tenancy for a tenant-less role",
              r.json().get("organization_id") is None, r.json().get("organization_id"))

    # THIS ASSERTION WAS INVERTED, AND IT ENCODED A BUG.
    #
    # It used to require that POST /admin/users SUCCEEDS for a god_admin with
    # no customer selected. That call wrote organization_id=current_user.
    # organization_id, which for a neutral owner is None - and an org-NULL user
    # is this system's POSITIVE ASSERTION that someone is a brand-sales
    # identity. So the "passing" behaviour was the manufacture of a phantom
    # seller: refused by every tenant route, holding no membership, useless in
    # the sales workspace, and indistinguishable from a real one.
    #
    # The route now refuses without a context. Both halves are proved here, so
    # this is strictly stronger than what it replaced - a guard that refuses
    # everything would fail the second half.
    r = c.post("/admin/users", json={"email": "adminmade@probe-corp.com",
                                     "full_name": "Admin Made",
                                     "role": "advisor"}, headers=god)
    check("POST /admin/users REFUSES a context-less create", r.status_code == 409,
          "%s %s" % (r.status_code, r.text[:160]))
    check("...and the refusal names the context to select",
          "customer" in r.text.lower(), r.text[:160])
    no_password_anywhere("POST /admin/users (refused)", r)

    # ...and it still works for the person it was always meant for: an owner
    # who has said which customer this user belongs to.
    r = c.post("/admin/users", json={"email": "adminmade@probe-corp.com",
                                     "full_name": "Admin Made",
                                     "role": "advisor"},
               headers={**god, "X-Org-Override": "org-cust"})
    check("POST /admin/users still works WITH a customer selected",
          r.status_code in (200, 201), r.text[:200])
    no_password_anywhere("POST /admin/users", r)
    if r.status_code in (200, 201):
        check("...and hands over a one-time link instead",
              bool(r.json().get("setup_url")), r.json())
        from app.deps import SessionLocal as _SL
        from app.models.models import User as _U
        _db = _SL()
        try:
            _made = _db.query(_U).filter(_U.email == "adminmade@probe-corp.com").first()
            check("...and the user BELONGS to that customer, not to nobody",
                  _made is not None and _made.organization_id == "org-cust",
                  None if _made is None else _made.organization_id)
        finally:
            _db.close()

    r = c.post("/admin/provision-client",
               json={"org_name": "Probe Cemetery", "org_slug": "probe-cemetery",
                     "supervisor_email": "sup@probe-corp.com",
                     "supervisor_full_name": "Sup Ervisor"}, headers=god)
    check("POST /admin/provision-client still works",
          r.status_code in (200, 201), r.text[:250])
    no_password_anywhere("POST /admin/provision-client", r)
    if r.status_code in (200, 201):
        check("...and hands over a one-time link instead",
              bool(r.json().get("setup_url")), r.json())

    db = SessionLocal()
    try:
        target = db.query(User).filter(User.email == "adminmade@probe-corp.com").first()
        tid = target.id if target else None
    finally:
        db.close()
    if tid:
        r = c.post("/admin/users/%s/reset-password" % tid, json={}, headers=god)
        check("POST /admin/users/{id}/reset-password still works",
              r.status_code == 200, r.text[:200])
        no_password_anywhere("reset-password (generated)", r)
        if r.status_code == 200:
            check("...and hands over a one-time link instead",
                  bool(r.json().get("setup_url")), r.json())

        r = c.post("/admin/users/%s/reset-password" % tid,
                   json={"new_password": "AdminChose!2026"}, headers=god)
        check("an admin may still set a password explicitly",
              r.status_code == 200, r.text[:200])
        no_password_anywhere("reset-password (explicit)", r)
        r2 = c.post("/auth/login", data={"username": "adminmade@probe-corp.com",
                                         "password": "AdminChose!2026"})
        check("...and it actually works", r2.status_code == 200, r2.status_code)


def main():
    print("=" * 78)
    print("BRAND-SALES USER CREATION AND MEMBERSHIP MANAGEMENT")
    print("=" * 78)
    build()
    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        mgr = token(c, "mgr@probe.test")
        rep = token(c, "rep@probe.test")
        advisor = token(c, "advisor@probe.test")

        test_permissions(c, god, mgr, rep, advisor)
        setup_url = test_new_rep(c, god)
        rep_hdr = test_new_rep_can_actually_log_in(c, setup_url)
        test_existing_identity(c, god)
        test_manager_and_roles(c, god)
        if rep_hdr:
            test_deactivation(c, god, rep_hdr)
        test_other_memberships_survive_deactivation(c, god)
        test_no_plaintext_passwords(c, god)

    print("\n" + "=" * 78)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("ALL SALES STAFF MANAGEMENT CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
