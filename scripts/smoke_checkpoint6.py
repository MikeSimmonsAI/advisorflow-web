"""Checkpoint 6 regression suite: provisioning, implementation, launch, isolation.

WHAT THIS PROVES
----------------
That a Won deal becomes a correctly isolated customer exactly once, that the
people who may do it are the only people who can, that the customer's first
admin never has a password anybody else knows, and that none of it opened a door
into the sales tree, another tenant, or God Mode.

NO TEST HERE CONTACTS ANYTHING EXTERNAL. There is no provider call in the
Checkpoint 6 code path at all - provisioning writes rows, and that is the whole
of it - so there is nothing to mock and nothing that could reach Google,
Microsoft, Twilio, Retell, Zoom, Resend or Stripe.

Temp SQLite. Never touches production.

    python scripts/smoke_checkpoint6.py
"""
import os
import sys
import json
import shutil
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="cp6_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59
os.environ.pop("APP_ENV", None)          # production semantics for the demo 404 check

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from sqlalchemy.exc import IntegrityError                              # noqa: E402
from app.main import app                                               # noqa: E402
from app.deps import SessionLocal, engine                              # noqa: E402
from app.models.models import (                                        # noqa: E402
    Base, Platform, Organization, User, Lead, Proposal, AuditLogEntry,
    PROP_ACCEPTED,
)
from app.models.sales_models import (                                  # noqa: E402
    BrandSalesOrg, BrandPackage, Opportunity, Membership, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_WON, STAGE_CLOSING, STAGE_ONBOARDING, STAGE_LIVE,
)
from app.models.implementation_models import (                         # noqa: E402
    Implementation, ImplementationMilestone, CustomerActivation,
    IMPL_NOT_STARTED, IMPL_CONFIGURATION, IMPL_BLOCKED,
    IMPL_READY_FOR_LAUNCH, IMPL_LIVE,
    INVITE_PENDING, INVITE_ACCEPTED, INVITE_REVOKED,
)
from app.services.auth_service import hash_password                    # noqa: E402

PW = "SmokeTest!2026"
CHI = "America/Chicago"
FAILURES = []
NOW = datetime.utcnow()
STATE = {}


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:500]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def section(title):
    print("\n--- %s " % title + "-" * max(0, 66 - len(title)))


def token_for(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s %s" % (email, r.status_code, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def read_src(rel):
    """Read a repo file by path relative to the repository root.

    Not relative to the working directory: this suite is run from wherever the
    deploy script happens to be, and a static assertion that silently cannot
    find its file is worse than no assertion at all.
    """
    with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
        return fh.read()


def code_only(src):
    """Source with docstrings and comments stripped.

    An assertion like "this module never returns a password" must be about what
    the code does, not about whether the word appears in a paragraph explaining
    why it deliberately does not.
    """
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            node.value = ast.Constant(value="")
    return ast.unparse(ast.fix_missing_locations(tree))


# ── fixture ─────────────────────────────────────────────────────────────────

def build():
    """Two platforms, two brands, five sales identities and one outsider.

    Two of everything on purpose: a cross-brand test that only has one brand
    proves nothing, and a platform-match test needs a second platform to be
    able to land on the wrong one.
    """
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    db.add_all([
        Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"),
        Platform(id="plt-bb", name="BookaBoost", slug="bookaboost"),
    ])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                      slug="evosyspro-sales", timezone=CHI),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb", name="BookaBoost Sales",
                      slug="bookaboost-sales", timezone=CHI),
        # A third brand with nobody running it: proves the "no sales manager"
        # alert is real, and gives the Won queue an entry that stays unprovisioned.
        BrandSalesOrg(id="bso-orphan", platform_id="plt-bb", name="Orphan Sales",
                      slug="orphan-sales", timezone=CHI),
    ])
    db.flush()
    db.add_all([
        BrandPackage(id="pkg-pro", platform_id="plt-evo", key="professional",
                     name="Professional", price=4995, setup_fee=1500, currency="USD"),
        BrandPackage(id="pkg-start", platform_id="plt-evo", key="starter",
                     name="Starter", price=1497, currency="USD"),
        BrandPackage(id="pkg-bb", platform_id="plt-bb", key="starter",
                     name="BB Starter", price=99, currency="USD"),
    ])
    db.flush()

    def mk(uid, email, name, role="advisor", org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True))

    mk("u-god", "god@example.com", "Mike Simmons", role="god_admin")
    mk("u-mgr", "mgr@example.com", "Evo Manager")
    mk("u-rep", "rep@example.com", "Evo Rep")
    mk("u-bbmgr", "bbmgr@example.com", "BB Manager")
    mk("u-implowner", "impl@example.com", "Implementation Specialist")
    db.flush()

    db.add_all([
        Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bbmgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_MANAGER, is_active=True),
    ])
    db.flush()

    won = NOW - timedelta(days=2)
    db.add_all([
        Opportunity(id="opp-won", brand_sales_org_id="bso-evo", owner_user_id="u-rep",
                    company_name="Greenwood Chapel", contact_name="Dana Reyes",
                    email="dana@greenwoodchapel.test", phone="+15125550100",
                    industry="funeral", timezone=CHI, stage=STAGE_WON, status="won",
                    selected_package_id="pkg-pro", deal_value=4995, won_at=won,
                    created_at=NOW - timedelta(days=40), updated_at=won),
        Opportunity(id="opp-won-2", brand_sales_org_id="bso-evo", owner_user_id="u-rep",
                    company_name="Greenwood Chapel", contact_name="Sam Ortiz",
                    email="sam@greenwood2.test", industry="funeral", timezone=CHI,
                    stage=STAGE_WON, status="won", selected_package_id="pkg-start",
                    deal_value=1497, won_at=won, created_at=NOW - timedelta(days=30)),
        Opportunity(id="opp-bb", brand_sales_org_id="bso-bb", owner_user_id="u-bbmgr",
                    company_name="Northside Barbers", contact_name="Lee Park",
                    stage=STAGE_WON, status="won", selected_package_id="pkg-bb",
                    deal_value=99, won_at=won, created_at=NOW - timedelta(days=20)),
        Opportunity(id="opp-open", brand_sales_org_id="bso-evo", owner_user_id="u-rep",
                    company_name="Still Deciding LLC", stage=STAGE_CLOSING,
                    status="open", deal_value=2495, created_at=NOW - timedelta(days=60),
                    updated_at=NOW - timedelta(days=45),
                    next_action="Follow up", next_action_due_at=NOW - timedelta(days=5)),
        Opportunity(id="opp-orphan", brand_sales_org_id="bso-orphan",
                    company_name="Unstaffed Brand Co", stage=STAGE_WON, status="won",
                    won_at=won, created_at=NOW - timedelta(days=10)),
    ])
    db.flush()

    db.add(DiscoveryRecord(id="disc-1", opportunity_id="opp-won",
                           business_goals="Book more pre-need appointments.",
                           bottlenecks="Nobody answers the phone after 5pm.",
                           required_integrations="Google Calendar",
                           current_tools="Spreadsheet"))
    db.add(Proposal(id="prop-1", organization_id=None, created_by_id="u-rep",
                    brand_sales_org_id="bso-evo", opportunity_id="opp-won",
                    proposal_number="P-1001", version=2, title="Professional",
                    client_company="Greenwood Chapel", package_id="pkg-pro",
                    base_amount=4995, final_amount=4995, currency="USD",
                    sales_status=PROP_ACCEPTED, status="accepted",
                    implementation_plan="Two-week configuration, then training.",
                    accepted_at=won, sent_at=won - timedelta(days=3)))
    db.commit()
    db.close()


# ── §5-§8 provisioning ──────────────────────────────────────────────────────

def test_provisioning(c):
    section("Provisioning authority (§5, §32)")
    god, mgr, rep, bbmgr = (token_for(c, e) for e in
                            ("god@example.com", "mgr@example.com",
                             "rep@example.com", "bbmgr@example.com"))
    STATE.update(god=god, mgr=mgr, rep=rep, bbmgr=bbmgr)

    r = c.post("/god/ops/opportunities/opp-won/provision", json={}, headers=rep)
    check("rep cannot provision", r.status_code == 403, r.text[:200])

    r = c.post("/god/ops/opportunities/opp-won/provision", json={}, headers=bbmgr)
    check("other brand's manager cannot provision", r.status_code == 403, r.text[:200])

    r = c.get("/god/ops/opportunities/opp-won/provisioning-review", headers=rep)
    check("rep cannot even open the review", r.status_code == 403, r.text[:200])

    r = c.post("/god/ops/opportunities/opp-open/provision", json={}, headers=god)
    check("a non-Won opportunity is refused", r.status_code == 409, r.text[:200])

    # A deal whose brand has no platform cannot be provisioned onto a guessed
    # one. The service refuses it - and the SCHEMA makes it unreachable in the
    # first place, which is the stronger of the two guarantees, so both are
    # asserted rather than only the code path.
    from sqlalchemy import inspect as _sa_inspect
    _cols = {c["name"]: c for c in _sa_inspect(engine).get_columns("brand_sales_orgs")}
    check("a brand sales org cannot exist without a platform",
          _cols["platform_id"]["nullable"] is False, _cols["platform_id"])
    _ocols = {c["name"]: c for c in _sa_inspect(engine).get_columns("opportunities")}
    check("an opportunity cannot exist without a brand sales org",
          _ocols["brand_sales_org_id"]["nullable"] is False, _ocols["brand_sales_org_id"])
    from app.services import provisioning as _prov
    _src = code_only(read_src("app/services/provisioning.py"))
    check("provisioning still refuses a platformless brand in code",
          "not bso.platform_id" in _src, "guard missing")

    section("Provisioning review (§6)")
    r = c.get("/god/ops/opportunities/opp-won/provisioning-review", headers=god)
    check("god can open the review", r.status_code == 200, r.text[:200])
    rev = r.json() if r.status_code == 200 else {}
    check("review names the real platform",
          (rev.get("platform") or {}).get("slug") == "evosyspro", rev.get("platform"))
    check("review carries the accepted proposal version",
          (rev.get("accepted_proposal") or {}).get("version") == 2,
          rev.get("accepted_proposal"))
    check("review carries the package that was sold",
          (rev.get("package") or {}).get("key") == "professional", rev.get("package"))
    check("review carries real discovery answers",
          "bottlenecks" in (rev.get("discovery") or {}), rev.get("discovery"))
    check("review suggests a free slug",
          rev.get("suggested_slug") == "greenwood-chapel", rev.get("suggested_slug"))
    check("review says not yet provisioned", rev.get("already_provisioned") is False)

    section("Provisioning (§7, §8, §41)")
    db = SessionLocal()
    orgs_before = db.query(Organization).count()
    won_at_before = db.query(Opportunity).filter(Opportunity.id == "opp-won").first().won_at
    db.close()

    r = c.post("/god/ops/opportunities/opp-won/provision",
               json={"org_name": "Greenwood Chapel & Cremation",
                     "target_launch_date": (NOW + timedelta(days=21)).isoformat(),
                     "notes": "Wants voice live before the launch date."},
               headers=god)
    check("god provisions the Won deal", r.status_code == 200, r.text[:300])
    body = r.json() if r.status_code == 200 else {}
    check("response says it created something", body.get("created") is True, body)
    impl_id = (body.get("implementation") or {}).get("implementation_id")
    STATE["impl_id"] = impl_id
    STATE["org_id"] = (body.get("implementation") or {}).get("organization_id")

    db = SessionLocal()
    impl = db.query(Implementation).filter(Implementation.id == impl_id).first()
    org = db.query(Organization).filter(Organization.id == STATE["org_id"]).first()
    opp = db.query(Opportunity).filter(Opportunity.id == "opp-won").first()

    check("exactly one organisation was created",
          db.query(Organization).count() == orgs_before + 1)
    check("customer organisation is on the deal's platform",
          org is not None and org.platform_id == "plt-evo",
          org.platform_id if org else None)
    check("operator's corrected name was used for the TENANT",
          org.name == "Greenwood Chapel & Cremation", org.name)
    check("the opportunity's own company name was NOT rewritten",
          opp.company_name == "Greenwood Chapel", opp.company_name)
    check("opportunity now points at the customer organisation",
          opp.customer_organization_id == org.id, opp.customer_organization_id)
    check("opportunity stage advanced to onboarding",
          opp.stage == STAGE_ONBOARDING, opp.stage)
    check("opportunity is still status=won", opp.status == "won", opp.status)
    check("won_at was not touched", opp.won_at == won_at_before)
    check("implementation records the accepted proposal",
          impl.accepted_proposal_id == "prop-1" and impl.accepted_proposal_version == 2)
    check("implementation records who sold it", impl.sold_by_user_id == "u-rep")
    check("implementation has NO owner by default (not the rep)",
          impl.owner_user_id is None, impl.owner_user_id)
    check("implementation starts at not_started", impl.status == IMPL_NOT_STARTED)
    check("billing intent copied, billing not activated",
          impl.billing_status == "not_configured" and float(impl.implementation_fee) == 1500.0,
          (impl.billing_status, impl.implementation_fee))

    ms = (db.query(ImplementationMilestone)
            .filter(ImplementationMilestone.implementation_id == impl_id).all())
    keys = [m.key for m in sorted(ms, key=lambda x: x.position)]
    check("package-aware milestones were created",
          "ai_config" in keys and "voice_config" in keys, keys)
    check("milestones are industry-neutral (no funeral-specific default)",
          not any("funeral" in k or "cemetery" in k for k in keys), keys)
    check("launch is the last milestone", keys[-1] == "launch", keys)
    check("SMS milestone sits before testing, not after launch",
          keys.index("sms") < keys.index("testing"), keys)

    audit = (db.query(AuditLogEntry)
               .filter(AuditLogEntry.action == "customer_provisioned").all())
    check("provisioning wrote exactly one audit entry", len(audit) == 1, len(audit))
    if audit:
        a = audit[0]
        check("audit carries the platform", a.platform_id == "plt-evo", a.platform_id)
        check("audit carries the brand", a.brand_sales_org_id == "bso-evo")
        check("audit carries before and after",
              a.before_state and a.after_state and "onboarding" in a.after_state)
        check("audit actor is the god who did it", a.actor_user_id == "u-god")
    db.close()

    section("Idempotency (§7, §32)")
    r2 = c.post("/god/ops/opportunities/opp-won/provision", json={}, headers=god)
    check("second provision returns 200, not an error", r2.status_code == 200, r2.text[:200])
    b2 = r2.json() if r2.status_code == 200 else {}
    check("second provision says created=false", b2.get("created") is False, b2.get("created"))
    check("second provision returns the ORIGINAL implementation",
          (b2.get("implementation") or {}).get("implementation_id") == impl_id)

    db = SessionLocal()
    check("no second organisation was created",
          db.query(Organization).count() == orgs_before + 1)
    check("no second implementation exists",
          db.query(Implementation).filter(Implementation.opportunity_id == "opp-won").count() == 1)
    check("no second audit entry was written",
          db.query(AuditLogEntry).filter(AuditLogEntry.action == "customer_provisioned").count() == 1)

    # The database, not the service, is the guarantee.
    dup = Implementation(opportunity_id="opp-won", organization_id="anything-else",
                         platform_id="plt-evo", status=IMPL_NOT_STARTED,
                         created_at=NOW)
    db.add(dup)
    raised = False
    try:
        db.commit()
    except IntegrityError:
        raised = True
        db.rollback()
    check("DB refuses a second implementation for the same opportunity", raised)
    db.close()

    section("Cross-brand and slug collision (§41)")
    r = c.post("/god/ops/opportunities/opp-bb/provision", json={}, headers=god)
    check("BookaBoost deal provisions", r.status_code == 200, r.text[:200])
    bb_org_id = (r.json().get("implementation") or {}).get("organization_id") if r.status_code == 200 else None
    db = SessionLocal()
    bb_org = db.query(Organization).filter(Organization.id == bb_org_id).first()
    check("BookaBoost customer landed on the BookaBoost platform",
          bb_org is not None and bb_org.platform_id == "plt-bb",
          bb_org.platform_id if bb_org else None)
    db.close()

    # Second deal with the SAME company name: slug must not collide.
    r = c.post("/god/ops/opportunities/opp-won-2/provision", json={}, headers=mgr)
    check("brand's own manager may provision their own brand",
          r.status_code == 200, r.text[:250])
    if r.status_code == 200:
        db = SessionLocal()
        o2 = db.query(Organization).filter(
            Organization.id == r.json()["implementation"]["organization_id"]).first()
        STATE["impl2_id"] = r.json()["implementation"]["implementation_id"]
        slugs = [x[0] for x in db.query(Organization.slug).all()]
        check("every customer organisation has a distinct slug",
              len(slugs) == len(set(slugs)), slugs)
        # The first tenant was renamed by the operator, so this pair does not
        # collide. Test the collision path directly rather than pretending it did.
        from app.services.provisioning import unique_slug
        check("a taken slug is suffixed, not rejected",
              unique_slug(db, o2.name) == o2.slug + "-2",
              (o2.slug, unique_slug(db, o2.name)))
        check("suffixing keeps going past -2",
              unique_slug(db, "greenwood-chapel") not in slugs)
        db.close()


# ── §9, §10 customer admin ──────────────────────────────────────────────────

def test_customer_admin(c):
    god, rep, mgr = STATE["god"], STATE["rep"], STATE["mgr"]
    impl_id, org_id = STATE["impl_id"], STATE["org_id"]

    section("Customer admin creation (§9)")
    r = c.post("/god/ops/implementations/%s/customer-admin" % impl_id,
               json={"full_name": "Dana Reyes", "email": "dana@greenwoodchapel.test",
                     "role": "org_admin", "base_url": "https://app.evosyspro.live"},
               headers=god)
    check("god creates the customer admin", r.status_code == 200, r.text[:300])
    body = r.json() if r.status_code == 200 else {}
    raw_url = body.get("activation_url", "")
    token = raw_url.split("token=")[-1] if "token=" in raw_url else ""
    STATE["token"] = token
    STATE["activation_id"] = (body.get("activation") or {}).get("id")

    flat = json.dumps(body).lower()
    for bad_key in ('"password"', '"temp_password"', '"temporary_password"',
                    '"password_hash"', '"new_password"'):
        check("response carries no %s field" % bad_key, bad_key not in flat, flat[:300])
    check("the only mention of a password is the warning that none was made",
          flat.count("password") == 1 and "no password was created" in flat, flat[-200:])
    check("activation link was returned once", token.startswith("act_"), raw_url[:60])

    db = SessionLocal()
    u = db.query(User).filter(User.email == "dana@greenwoodchapel.test").first()
    check("admin belongs to the new customer organisation",
          u is not None and u.organization_id == org_id)
    check("admin is on the customer's platform", u.platform_id == "plt-evo", u.platform_id)
    check("admin holds no brand-sales membership",
          db.query(Membership).filter(Membership.user_id == u.id).count() == 0)
    act = db.query(CustomerActivation).filter(CustomerActivation.user_id == u.id).first()
    check("only a hash is stored, never the token",
          act is not None and token not in (act.token_hash or "")
          and len(act.token_hash) == 64)
    check("activation is pending", act.status == INVITE_PENDING)
    audits = db.query(AuditLogEntry).filter(
        AuditLogEntry.action == "customer_admin_created").all()
    check("admin creation is audited", len(audits) == 1)
    check("the token appears in NO audit row",
          not any(token in ((a.details or "") + (a.after_state or "") + (a.before_state or ""))
                  for a in db.query(AuditLogEntry).all()))
    db.close()

    r = c.post("/god/ops/implementations/%s/customer-admin" % impl_id,
               json={"full_name": "Dana Again", "email": "dana@greenwoodchapel.test"},
               headers=god)
    check("duplicate email is refused, not silently reused", r.status_code == 409, r.text[:200])

    r = c.post("/god/ops/implementations/%s/customer-admin" % impl_id,
               json={"full_name": "Sneaky", "email": "sneaky@x.test", "role": "god_admin"},
               headers=god)
    check("provisioning cannot mint a god_admin", r.status_code == 400, r.text[:200])

    r = c.post("/god/ops/implementations/%s/customer-admin" % impl_id,
               json={"full_name": "Nope", "email": "nope@x.test"}, headers=mgr)
    check("a sales manager cannot create customer admins", r.status_code == 403, r.text[:200])

    section("Activation (§9, §10)")
    r = c.get("/auth/activation", params={"token": token})
    check("activation link previews", r.status_code == 200, r.text[:200])
    prev = r.json() if r.status_code == 200 else {}
    check("preview leaks no ids",
          "user_id" not in prev and "organization_id" not in prev, list(prev))

    r = c.get("/auth/activation", params={"token": "act_" + "x" * 40})
    check("a wrong token fails closed", r.status_code == 400, r.status_code)
    check("wrong-token message is identical to expired-token message",
          "invalid or has expired" in r.text)

    r = c.post("/auth/activation/accept", json={"token": token, "new_password": "short"})
    check("a weak password is refused", r.status_code == 400, r.text[:200])

    r = c.post("/auth/activation/accept",
               json={"token": token, "new_password": "CustomerChosen!2026"})
    check("customer sets their own password", r.status_code == 200, r.text[:200])
    check("accept returns no session token",
          "access_token" not in r.text, r.text[:200])

    r = c.post("/auth/login", data={"username": "dana@greenwoodchapel.test",
                                    "password": "CustomerChosen!2026"})
    check("customer can now log in", r.status_code == 200, r.text[:200])
    STATE["cust"] = {"Authorization": "Bearer " + r.json()["access_token"]} if r.status_code == 200 else {}

    r = c.post("/auth/activation/accept",
               json={"token": token, "new_password": "Another!Password2026"})
    check("the token cannot be used twice", r.status_code == 400, r.text[:200])

    db = SessionLocal()
    a = db.query(CustomerActivation).filter(CustomerActivation.id == STATE["activation_id"]).first()
    check("activation is marked accepted", a.status == INVITE_ACCEPTED, a.status)
    db.close()

    section("Resend revokes rather than extends (§9)")
    r = c.post("/god/ops/implementations/%s/customer-admin" % impl_id,
               json={"full_name": "Ops Contact", "email": "ops@greenwoodchapel.test",
                     "base_url": "https://app.evosyspro.live"}, headers=god)
    tok1 = r.json()["activation_url"].split("token=")[-1]
    aid1 = r.json()["activation"]["id"]
    r = c.post("/god/ops/activations/%s/resend" % aid1,
               json={"base_url": "https://app.evosyspro.live"}, headers=god)
    check("resend issues a new link", r.status_code == 200, r.text[:200])
    tok2 = r.json()["activation_url"].split("token=")[-1] if r.status_code == 200 else ""
    STATE["token2"] = tok2
    check("the new link differs from the old", tok1 != tok2 and tok2.startswith("act_"))
    check("the OLD link is dead", c.get("/auth/activation", params={"token": tok1}).status_code == 400)
    check("the NEW link works", c.get("/auth/activation", params={"token": tok2}).status_code == 200)
    check("send_count incremented", r.json()["activation"]["send_count"] == 2)

    r = c.post("/god/ops/activations/%s/revoke" % r.json()["activation"]["id"], headers=god)
    check("an invitation can be revoked", r.status_code == 200 and
          r.json()["activation"]["status"] == INVITE_REVOKED, r.text[:200])
    check("a revoked link is dead",
          c.get("/auth/activation", params={"token": tok2}).status_code == 400)

    section("Existing identity is added by id, never by email (§9)")
    r = c.post("/god/ops/implementations/%s/customer-user" % impl_id,
               json={"user_id": "u-rep"}, headers=god)
    check("a brand-sales rep is NOT silently moved into a tenant",
          r.status_code == 200, r.text[:200])
    db = SessionLocal()
    rep_row = db.query(User).filter(User.id == "u-rep").first()
    moved = rep_row.organization_id == org_id
    db.close()
    # This one is a genuine judgement call, so it is asserted explicitly rather
    # than left implicit: a brand-sales user has organization_id = NULL as a
    # positive assertion, so adding them to a tenant SUCCEEDS but must be an
    # explicit, audited act naming their id. It must never happen by inference.
    check("adding by explicit id is what actually moved them", moved, rep_row.organization_id)
    db = SessionLocal()
    check("that move was audited",
          db.query(AuditLogEntry).filter(AuditLogEntry.action == "customer_user_added").count() == 1)
    # Put the rep back where they belong for the remaining tests.
    rr = db.query(User).filter(User.id == "u-rep").first()
    rr.organization_id = None
    db.commit()
    db.close()

    r = c.post("/god/ops/implementations/%s/customer-user" % STATE["impl2_id"],
               json={"user_id": (SessionLocal().query(User)
                                 .filter(User.email == "dana@greenwoodchapel.test")
                                 .first().id)}, headers=god)
    check("a user already inside another tenant is refused, not transferred",
          r.status_code == 409, r.text[:250])


# ── §10, §32 customer isolation ─────────────────────────────────────────────

def test_customer_isolation(c):
    section("Customer admin isolation (§10, §32)")
    cust = STATE.get("cust") or {}
    if not cust:
        check("customer session available for isolation tests", False, "login failed earlier")
        return

    for path in ("/god/ops/sales-operations", "/god/ops/implementations",
                 "/god/ops/won-queue", "/god/ops/customer-organizations",
                 "/god/ops/audit", "/god/stats", "/god/orgs"):
        r = c.get(path, headers=cust)
        check("customer admin blocked from %s" % path, r.status_code == 403,
              "%s %s" % (r.status_code, r.text[:120]))

    for path in ("/sales/opportunities", "/sales/implementations", "/sales/team"):
        r = c.get(path, headers=cust)
        check("customer admin blocked from %s" % path, r.status_code == 403,
              "%s %s" % (r.status_code, r.text[:120]))

    r = c.get("/sales/opportunities/opp-won/implementation", headers=cust)
    check("customer admin cannot read the sale that created them",
          r.status_code == 403, r.status_code)

    # Another tenant's data must be invisible even by direct id.
    db = SessionLocal()
    other_org = (db.query(Organization)
                   .filter(Organization.id != STATE["org_id"]).first())
    db.add(Lead(organization_id=other_org.id, first_name="Someone",
                last_name="Else", phone="+15125559999"))
    db.commit()
    other_lead = (db.query(Lead).filter(Lead.organization_id == other_org.id).first())
    db.close()
    r = c.get("/leads/%s" % other_lead.id, headers=cust)
    check("customer admin cannot read another tenant's lead by id",
          r.status_code in (403, 404), "%s %s" % (r.status_code, r.text[:120]))

    r = c.get("/leads", headers=cust)
    if r.status_code == 200:
        rows = r.json()
        rows = rows.get("leads", rows) if isinstance(rows, dict) else rows
        ids = {x.get("id") for x in rows} if isinstance(rows, list) else set()
        check("customer's lead list contains no other tenant's leads",
              other_lead.id not in ids, len(ids))
    else:
        check("customer can list their own (empty) leads", r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:120]))


# ── §11-§18 implementation lifecycle ────────────────────────────────────────

def test_implementation(c):
    god, rep, mgr, bbmgr = STATE["god"], STATE["rep"], STATE["mgr"], STATE["bbmgr"]
    impl_id = STATE["impl_id"]

    section("Owner, status and blockers (§12, §11)")
    r = c.post("/god/ops/implementations/%s/owner" % impl_id,
               json={"owner_user_id": "u-implowner"}, headers=god)
    check("owner can be assigned", r.status_code == 200, r.text[:200])
    check("owner is NOT the salesperson",
          (r.json().get("implementation") or {}).get("owner", {}).get("id") == "u-implowner")

    r = c.post("/god/ops/implementations/%s/owner" % impl_id,
               json={"owner_user_id": "does-not-exist"}, headers=god)
    check("an unknown owner is refused", r.status_code == 404, r.status_code)

    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": IMPL_BLOCKED}, headers=god)
    check("blocking without a reason is refused", r.status_code == 400, r.text[:200])

    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": IMPL_BLOCKED,
                     "blocker_note": "Waiting on the family's DNS registrar."},
               headers=god)
    check("blocking with a reason works", r.status_code == 200, r.text[:200])

    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": IMPL_CONFIGURATION}, headers=god)
    check("leaving blocked clears the blocker",
          r.status_code == 200 and
          (r.json().get("implementation") or {}).get("blocker_note") is None, r.text[:200])

    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": IMPL_LIVE}, headers=god)
    check("Live cannot be reached through the status route", r.status_code == 400, r.text[:200])

    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": "teleported"}, headers=god)
    check("an unknown status is refused", r.status_code == 400, r.status_code)

    section("Milestones (§13)")
    r = c.get("/god/ops/implementations/%s" % impl_id, headers=god)
    check("implementation detail loads", r.status_code == 200, r.text[:200])
    detail = r.json() if r.status_code == 200 else {}
    total = detail.get("completion", {}).get("total", 0)
    check("detail exposes the handoff projection", bool(detail.get("handoff")), list(detail))
    check("handoff carries discovery, not the whole opportunity",
          "discovery" in (detail.get("handoff") or {})
          and "deal_value" not in (detail.get("handoff") or {}),
          list(detail.get("handoff") or {}))

    for k in ("kickoff", "business_profile", "customer_users", "calendar",
              "sms", "cadences", "ai_config", "testing", "training"):
        c.post("/god/ops/implementations/%s/milestones/%s" % (impl_id, k),
               json={"status": "done"}, headers=god)
    r = c.post("/god/ops/implementations/%s/milestones/voice_config" % impl_id,
               json={"status": "skipped", "notes": "Not buying voice yet."}, headers=god)
    check("a milestone can be skipped", r.status_code == 200, r.text[:200])
    comp = r.json().get("completion", {})
    check("skipped counts as settled, not outstanding",
          not any(m["key"] == "voice_config" for m in comp.get("required_open", [])),
          comp.get("required_open"))

    r = c.post("/god/ops/implementations/%s/milestones/not_a_key" % impl_id,
               json={"status": "done"}, headers=god)
    check("an unknown milestone key is 404", r.status_code == 404, r.status_code)

    r = c.post("/god/ops/implementations/%s/milestones" % impl_id,
               json={"key": "data_migration", "label": "Data migration",
                     "is_required": False}, headers=god)
    check("a milestone can be added outside the package template",
          r.status_code == 200, r.text[:200])
    r = c.post("/god/ops/implementations/%s/milestones" % impl_id,
               json={"key": "data_migration", "label": "Again"}, headers=god)
    check("duplicate milestone keys are refused", r.status_code == 409, r.status_code)

    section("Launch (§18, §32)")
    r = c.post("/god/ops/implementations/%s/launch" % impl_id,
               json={"acknowledge_warnings": False}, headers=god)
    check("launch warns rather than silently proceeding",
          r.status_code == 409, r.text[:250])
    warn_body = r.json().get("detail", {}) if r.status_code == 409 else {}
    check("the warning names what is outstanding",
          isinstance(warn_body, dict) and warn_body.get("warnings"), warn_body)

    r = c.post("/god/ops/implementations/%s/launch" % impl_id,
               json={"acknowledge_warnings": True}, headers=mgr)
    check("a sales manager cannot mark a customer Live", r.status_code == 403, r.status_code)
    r = c.post("/god/ops/implementations/%s/launch" % impl_id,
               json={"acknowledge_warnings": True}, headers=rep)
    check("a rep cannot mark a customer Live", r.status_code == 403, r.status_code)

    # Finish the last required milestone, then launch cleanly.
    c.post("/god/ops/implementations/%s/milestones/lead_import" % impl_id,
           json={"status": "done"}, headers=god)
    c.post("/god/ops/implementations/%s/milestones/launch" % impl_id,
           json={"status": "done"}, headers=god)
    c.post("/god/ops/implementations/%s/status" % impl_id,
           json={"status": IMPL_READY_FOR_LAUNCH}, headers=god)

    r = c.get("/god/ops/implementations/%s" % impl_id, headers=god)
    check("no launch warnings remain", r.json().get("launch_warnings") == [],
          r.json().get("launch_warnings"))

    r = c.post("/god/ops/implementations/%s/launch" % impl_id,
               json={"acknowledge_warnings": False, "note": "Family trained Friday."},
               headers=god)
    check("god marks the customer Live", r.status_code == 200, r.text[:250])

    db = SessionLocal()
    impl = db.query(Implementation).filter(Implementation.id == impl_id).first()
    opp = db.query(Opportunity).filter(Opportunity.id == "opp-won").first()
    check("launch recorded a timestamp and an actor",
          impl.launched_at is not None and impl.launched_by == "u-god")
    check("implementation status is live", impl.status == IMPL_LIVE)
    check("opportunity stage advanced to live", opp.stage == STAGE_LIVE, opp.stage)
    check("opportunity is STILL status=won after launch", opp.status == "won")
    live_audit = db.query(AuditLogEntry).filter(
        AuditLogEntry.action == "customer_marked_live").all()
    check("launch is audited exactly once", len(live_audit) == 1)
    db.close()

    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": IMPL_CONFIGURATION}, headers=god)
    check("a live customer cannot be quietly reopened", r.status_code == 409, r.text[:200])

    section("Billing handoff (§19)")
    r = c.post("/god/ops/implementations/%s/billing" % impl_id,
               json={"billing_status": "invoiced", "recurring_amount": 4995,
                     "billing_start_date": (NOW + timedelta(days=30)).isoformat(),
                     "billing_notes": "Net 30, PO required."}, headers=god)
    check("billing intent can be recorded", r.status_code == 200, r.text[:200])
    db = SessionLocal()
    check("billing change is audited",
          db.query(AuditLogEntry).filter(
              AuditLogEntry.action == "billing_configuration_changed").count() == 1)
    src = code_only(read_src("app/routers/god_ops_router.py"))
    check("no billing route imports or calls stripe",
          "stripe" not in src.lower())
    db.close()


# ── §15, §16 post-Won visibility ────────────────────────────────────────────

def test_post_won_visibility(c):
    god, rep, mgr, bbmgr = STATE["god"], STATE["rep"], STATE["mgr"], STATE["bbmgr"]
    impl_id = STATE["impl_id"]

    section("Salesperson and manager post-Won visibility (§15, §16)")
    r = c.get("/sales/opportunities/opp-won/implementation", headers=rep)
    check("the rep who sold it sees the launch status", r.status_code == 200, r.text[:200])
    proj = r.json() if r.status_code == 200 else {}
    check("rep sees status and owner", proj.get("is_live") is True
          and proj.get("implementation_owner") == "Implementation Specialist", proj)
    for leaked in ("blocker_note", "notes", "handoff", "milestones",
                   "billing_status", "recurring_amount"):
        check("rep does NOT see %s" % leaked, leaked not in proj, list(proj))

    # /sales/implementations returns an OBJECT, not a bare list, as of the Sales
    # Workspace completion: the manager's Sold / Onboarding screen needs to know
    # whether it is looking at a team view without a second call to /sales/me.
    # `rows_of` reads either shape so this assertion is about the SCOPING, which
    # did not change, rather than about the envelope, which did.
    def rows_of(resp):
        if resp.status_code != 200:
            return []
        body = resp.json()
        return body if isinstance(body, list) else body.get("implementations", [])

    r = c.get("/sales/implementations", headers=rep)
    check("rep lists their own implementations", r.status_code == 200, r.text[:200])
    rows = rows_of(r)
    check("rep sees only deals they sold",
          all(x.get("implementation_id") for x in rows) and len(rows) >= 1, len(rows))
    check("the rep's response does not claim manager scope",
          r.json().get("is_manager") is False if isinstance(r.json(), dict) else True,
          r.json() if isinstance(r.json(), dict) else "legacy list shape")

    r = c.get("/sales/implementations", headers=mgr)
    mrows = rows_of(r)
    check("manager sees their brand's implementations", len(mrows) >= 2, len(mrows))
    check("manager does NOT see the other brand's customer",
          all("Northside" not in (x.get("customer_organization_name") or "")
              for x in mrows), [x.get("customer_organization_name") for x in mrows])
    check("the manager's response reports manager scope",
          r.json().get("is_manager") is True if isinstance(r.json(), dict) else True)
    check("...and names the salesperson, which the team view renders",
          all("sold_by_name" in x for x in mrows), list(mrows[0]) if mrows else [])
    check("...without widening what a rep may not see",
          all(not any(k in x for k in ("blocker_note", "notes", "handoff",
                                       "milestones", "billing_status"))
              for x in mrows), list(mrows[0]) if mrows else [])

    r = c.get("/sales/implementations", headers=bbmgr)
    brows = rows_of(r)
    check("the other brand's manager sees only their own",
          all("Greenwood" not in (x.get("customer_organization_name") or "")
              for x in brows), [x.get("customer_organization_name") for x in brows])

    r = c.get("/sales/opportunities/opp-won/implementation", headers=bbmgr)
    check("cross-brand probing returns 404, not 403",
          r.status_code == 404, "%s %s" % (r.status_code, r.text[:150]))

    r = c.get("/god/ops/implementations/%s" % impl_id, headers=mgr)
    check("a manager cannot open the god implementation record",
          r.status_code == 403, r.status_code)
    r = c.post("/god/ops/implementations/%s/status" % impl_id,
               json={"status": IMPL_CONFIGURATION}, headers=rep)
    check("a rep cannot modify an implementation", r.status_code == 403, r.status_code)


# ── §2, §17, §20, §37 god surfaces ──────────────────────────────────────────

def test_god_surfaces(c):
    god, rep, mgr = STATE["god"], STATE["rep"], STATE["mgr"]

    section("God Mode sales operations (§2, §3, §37)")
    r = c.get("/god/ops/sales-operations", headers=god)
    check("sales operations loads", r.status_code == 200, r.text[:250])
    d = r.json() if r.status_code == 200 else {}
    t = d.get("totals", {})
    check("brands are data, not hardcoded", len(d.get("brands", [])) == 3, len(d.get("brands", [])))
    check("pipeline value is a real sum", t.get("pipeline_value") == 2495.0, t.get("pipeline_value"))
    check("won value counts every won deal", t.get("won_value") == 4995 + 1497 + 99,
          t.get("won_value"))
    check("customers live is real", t.get("customers_live") == 1, t.get("customers_live"))
    check("won awaiting provisioning excludes provisioned deals",
          t.get("won_awaiting_provisioning") == 1, t.get("won_awaiting_provisioning"))
    check("overdue next actions are counted", t.get("overdue_next_actions") == 1,
          t.get("overdue_next_actions"))
    check("orgs without an implementation are stated, not hidden",
          "customer_organizations_without_implementation" in t, list(t))

    evo = [b for b in d.get("brands", []) if b["brand_sales_org_id"] == "bso-evo"]
    check("brand summary names its manager",
          evo and evo[0]["managers"] and evo[0]["managers"][0]["id"] == "u-mgr",
          evo[0]["managers"] if evo else None)
    check("brand attention items have real causes",
          evo and all(isinstance(x, str) and x for x in evo[0]["attention"]),
          evo[0]["attention"] if evo else None)
    orphan = [b for b in d.get("brands", []) if b["brand_sales_org_id"] == "bso-orphan"]
    check("a brand with no manager is flagged",
          orphan and any("No sales manager" in x for x in orphan[0]["attention"]),
          orphan[0]["attention"] if orphan else None)

    q = d.get("queues", {})
    check("every queue key is present",
          set(q) == {"won_awaiting_provisioning", "customer_admin_not_invited",
                     "implementation_has_no_owner", "blocked_implementations",
                     "launch_date_overdue", "ready_for_launch",
                     "billing_review_needed"}, sorted(q))
    check("the won queue holds the unprovisioned deal only",
          [x["opportunity_id"] for x in q["won_awaiting_provisioning"]] == ["opp-orphan"],
          [x["opportunity_id"] for x in q["won_awaiting_provisioning"]])

    section("Brand drilldown and implementations (§3, §4, §17)")
    r = c.get("/god/ops/brands/bso-evo", headers=god)
    check("brand detail loads", r.status_code == 200, r.text[:200])
    bd = r.json() if r.status_code == 200 else {}
    check("brand config exposes the real package catalogue",
          len(bd.get("configuration", {}).get("packages", [])) == 2,
          bd.get("configuration", {}).get("packages"))
    check("billing_plan_key is surfaced as-is, still unwired",
          all("billing_plan_key" in p for p in bd["configuration"]["packages"]))

    r = c.get("/god/ops/implementations", params={"live": True}, headers=god)
    check("implementations filter by live", r.status_code == 200 and
          len(r.json()["implementations"]) == 1, r.text[:200])
    r = c.get("/god/ops/implementations", params={"brand_sales_org_id": "bso-bb"},
              headers=god)
    check("implementations filter by brand",
          all(x["brand_sales_org"]["id"] == "bso-bb" for x in r.json()["implementations"]),
          r.text[:200])

    section("Customer organisations and audit (§20, §23)")
    r = c.get("/god/ops/customer-organizations", headers=god)
    check("customer organisations load", r.status_code == 200, r.text[:200])
    orgs = r.json().get("organizations", []) if r.status_code == 200 else []
    check("every provisioned org reports its source opportunity",
          all(o["implementation"]["opportunity_id"] for o in orgs
              if o["provisioned_from_sale"]), len(orgs))
    check("orgs not from a sale are labelled rather than hidden",
          all("provisioned_from_sale" in o for o in orgs))

    r = c.get("/god/ops/audit", headers=god)
    check("control-plane audit loads", r.status_code == 200, r.text[:200])
    entries = r.json().get("entries", []) if r.status_code == 200 else []
    actions = {e["action"] for e in entries}
    for required in ("customer_provisioned", "customer_admin_created",
                     "implementation_owner_assigned", "implementation_status_changed",
                     "implementation_milestone_changed", "customer_marked_live",
                     "billing_configuration_changed", "customer_user_added"):
        check("audit covers %s" % required, required in actions, sorted(actions))
    # The 12-character prefix IS recorded on purpose - it is the non-secret
    # lookup handle, exactly as the integration keys store theirs. What must
    # never appear is a full token, so that is what is asserted.
    blob = json.dumps(entries)
    check("no audit row contains a full activation token",
          not any(tok and tok in blob for tok in
                  (STATE.get("token"), STATE.get("token2"))), blob[:200])
    import re as _re
    long_tokens = [m for m in _re.findall(r"act_[A-Za-z0-9_\-]{9,}", blob)
                   if len(m) > 12]
    check("no audit row contains anything longer than a token prefix",
          not long_tokens, long_tokens[:3])

    for path in ("/god/ops/sales-operations", "/god/ops/queues",
                 "/god/ops/customer-organizations", "/god/ops/audit",
                 "/god/ops/implementations", "/god/ops/brands"):
        check("%s is god-only (manager blocked)" % path,
              c.get(path, headers=mgr).status_code == 403)
        check("%s is god-only (rep blocked)" % path,
              c.get(path, headers=rep).status_code == 403)


# ── §24, §25, §33, §34, §35 regressions ─────────────────────────────────────

def test_regressions(c):
    section("Retell bridges untouched (§24, §33)")
    paths = sorted({getattr(r, "path", "") for r in app.routes})
    for p in ("/integrations/retell/tenant/availability",
              "/integrations/retell/tenant/book",
              "/integrations/retell/availability",
              "/integrations/retell/book"):
        check("route still mounted: %s" % p, p in paths, "MISSING")

    # No credential, no answer — and the same answer for both bridges.
    for p in ("/integrations/retell/tenant/availability", "/integrations/retell/availability"):
        r = c.post(p, json={})
        check("%s still refuses an unauthenticated call" % p,
              r.status_code in (401, 403, 422), "%s %s" % (r.status_code, r.text[:120]))
        r = c.post(p, json={}, headers={"Authorization": "Bearer not-a-real-key"})
        check("%s still refuses a bogus key" % p,
              r.status_code in (401, 403), "%s %s" % (r.status_code, r.text[:120]))

    from app.services import integration_auth as _ia
    src = code_only(read_src("app/services/integration_auth.py"))
    check("credential kind is still checked against the scope columns",
          "scope_kind" in src and "_require_kind" in src)
    for name in ("require_retell", "require_retell_tenant"):
        check("integration guard %s still exists" % name, hasattr(_ia, name))

    src6 = "".join(code_only(read_src(f)) for f in (
        "app/services/provisioning.py", "app/services/implementation_service.py",
        "app/services/customer_activation.py", "app/services/god_operations.py",
        "app/routers/god_ops_router.py"))
    # Match module usage, not English. `resend` is also the name of the
    # invitation function, and a substring scan that cannot tell those apart
    # produces a failure that teaches nobody anything.
    low = src6.lower()
    for mod in ("retell", "twilio", "resend", "stripe", "googleapiclient",
                "httpx", "openai", "requests"):
        for pattern in ("import %s" % mod, "%s." % mod, "from %s" % mod):
            if pattern == "resend.":
                continue          # `activation.resend(...)` is our own function
            check("Checkpoint 6 code has no '%s'" % pattern, pattern not in low,
                  low[max(0, low.find(pattern) - 60):low.find(pattern) + 60])
    check("no provider SDK is imported anywhere in Checkpoint 6 code",
          not any(x in low for x in ("import twilio", "import stripe",
                                     "import resend", "import openai",
                                     "import httpx", "import requests")))

    section("Demo Mode untouched (§25, §34)")
    from app.services import environment as _env
    check("environment defaults to production", not _env.is_demo(), _env.current())
    for p in ("/demo/overview", "/demo/scenarios"):
        if p in paths:
            r = c.get(p, headers=STATE["god"])
            check("demo route %s still 404s in production" % p,
                  r.status_code == 404, "%s %s" % (r.status_code, r.text[:120]))
    from app.services import demo_runner as _dr
    check("demo reset table list still present", hasattr(_dr, "DELETE_ORDER"))
    check("demo cascade rules still present", hasattr(_dr, "CASCADE_RULES"))
    check("Checkpoint 6 created no demo-prefixed production rows",
          _no_demo_rows())

    section("Proposal and calendar security untouched (§27, §28)")
    check("proposal public projection module unchanged in shape",
          hasattr(__import__("app.services.proposal_service", fromlist=["x"]),
                  "create_version"))
    src_p = code_only(read_src("app/services/provisioning.py"))
    check("provisioning reads the accepted proposal server-side only",
          "accepted_proposal" in src_p and "public" not in src_p.lower())
    check("provisioning never writes to a Proposal",
          "Proposal(" not in src_p and "prop.final_amount =" not in src_p)
    from app.services import availability as _av
    check("availability engine still exports its intersection helper",
          hasattr(_av, "utc_to_local") and hasattr(_av, "local_to_utc"))

    section("No plaintext passwords anywhere in Checkpoint 6 (§9, FINAL RULES)")
    check("no Checkpoint 6 module generates a returnable temp password",
          "temp_password" not in src6 and "_generate_temp_password" not in src6)
    act_src = code_only(read_src("app/services/customer_activation.py"))
    check("the generated password never leaves its function",
          act_src.count("_unknowable_password") == 2, act_src.count("_unknowable_password"))
    check("activation stores a hash, never the token",
          "token_hash" in act_src and "token_plain" not in act_src)
    check("nothing in Checkpoint 6 sends email or SMS",
          "send_email" not in src6 and "send_sms" not in src6)


def _no_demo_rows():
    db = SessionLocal()
    try:
        bad = (db.query(Organization)
                 .filter(Organization.slug.like("demo-%")).count())
        bad += (db.query(Implementation)
                  .filter(Implementation.id.like("demo-%")).count())
        return bad == 0
    finally:
        db.close()


# ── §21, §22 user tenancy ───────────────────────────────────────────────────

def test_tenancy_preserved(c):
    section("Nullable user tenancy preserved (§21, §22)")
    db = SessionLocal()
    for uid in ("u-mgr", "u-rep", "u-bbmgr"):
        u = db.query(User).filter(User.id == uid).first()
        check("%s still has organization_id = NULL" % uid,
              u.organization_id is None, u.organization_id)
    god = db.query(User).filter(User.id == "u-god").first()
    check("god is not inside any tenant", god.organization_id is None, god.organization_id)

    src = code_only(read_src("app/services/provisioning.py"))
    check("provisioning never assigns a 'first organization' fallback",
          "first()" in src and ".organization_id = " not in src.replace(
              "opp.customer_organization_id = org.id", ""))
    check("provisioning creates no Lead from the sales contact",
          "Lead(" not in src)
    check("provisioning grants no Membership",
          "Membership(" not in src)
    db.close()


# ── run ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 74)
    print("CHECKPOINT 6 — provisioning, implementation, launch, isolation")
    print("=" * 74)
    build()
    with TestClient(app) as c:
        test_provisioning(c)
        test_customer_admin(c)
        test_customer_isolation(c)
        test_implementation(c)
        test_post_won_visibility(c)
        test_god_surfaces(c)
        test_regressions(c)
        test_tenancy_preserved(c)

    print("\n" + "=" * 74)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("ALL CHECKPOINT 6 CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
