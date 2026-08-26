"""Seed a LOCAL Checkpoint 6 demonstration database. Never production.

Refuses to run against anything that is not SQLite, and refuses if the file
already holds seeded rows. The data is invented and obviously so - the companies
do not exist, the phone numbers are in the 555 reserved range, and no address is
real. Its only job is to make the Checkpoint 6 screens show something, so the
completion screenshots are of working software rather than of a mockup.

The last section drives two of the Won deals through the REAL provisioning,
milestone and launch services rather than writing their rows by hand. A
screenshot of hand-written rows would prove nothing.

    python scripts\seed_cp6_local.py cp6_local.db

Then, in two shells:

    set DATABASE_URL=sqlite:///./cp6_local.db
    python -m uvicorn app.main:app --port 8099

    cd frontend
    npm run dev

Log in as owner@local.test / LocalDemo!2026 (god_admin).
"""
import os
import sys
from datetime import datetime, timedelta

DB = sys.argv[1] if len(sys.argv) > 1 else "cp6_local.db"
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.abspath(DB).replace("\\", "/")
os.environ.setdefault("JWT_SECRET", "local" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "local" + "0" * 60)
os.environ.pop("APP_ENV", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ["DATABASE_URL"].startswith("sqlite:"):
    raise SystemExit("REFUSING: this seeder only ever writes to a local SQLite file.")

from app.main import app                                          # noqa: E402
from app.deps import SessionLocal, engine                         # noqa: E402
from app.models.models import (                                   # noqa: E402
    Base, Platform, Organization, User, Proposal, PROP_ACCEPTED, PROP_SENT,
)
from app.models.sales_models import (                              # noqa: E402
    BrandSalesOrg, BrandPackage, Opportunity, Membership, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_WON, STAGE_CLOSING, STAGE_PROPOSAL, STAGE_DISCOVERY, STAGE_DEMO_BUILD,
)
from app.models.scheduling_models import SalesAppointment, APPT_SCHEDULED  # noqa: E402
from app.services.auth_service import hash_password                # noqa: E402

PW = "LocalDemo!2026"
CHI = "America/Chicago"
NOW = datetime.utcnow()


def main():
    # Startup is what runs create_all() and the auto-migrations, so the tables
    # must exist before anything below touches them.
    from fastapi.testclient import TestClient
    with TestClient(app) as _c:
        _c.get("/ping")

    db = SessionLocal()
    # Startup seeds the platform rows itself, so "is this database empty" has to
    # be asked about something startup does NOT create.
    if db.query(BrandSalesOrg).count() or db.query(Opportunity).count():
        raise SystemExit("REFUSING: %s already has seeded data. Delete it first." % DB)

    def platform(pid, name, slug, domain):
        row = db.query(Platform).filter(Platform.slug == slug).first()
        if row is None:
            row = Platform(id=pid, name=name, slug=slug, domain=domain)
            db.add(row)
        return row

    plt_evo = platform("plt-evo", "EvoSys Pro", "evosyspro", "app.evosyspro.live")
    plt_bb = platform("plt-bb", "BookaBoost", "bookaboost", "app.bookaboost.live")
    db.flush()
    EVO, BB = plt_evo.id, plt_bb.id

    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id=EVO, name="EvoSys Pro Sales",
                      slug="evosyspro-sales", timezone=CHI),
        BrandSalesOrg(id="bso-bb", platform_id=BB, name="BookaBoost Sales",
                      slug="bookaboost-sales", timezone=CHI),
    ])
    db.flush()
    db.add_all([
        BrandPackage(id="pkg-start", platform_id=EVO, key="starter",
                     name="Starter", price=1497, setup_fee=500, currency="USD",
                     billing_period="month", sort_order=1),
        BrandPackage(id="pkg-growth", platform_id=EVO, key="growth",
                     name="Growth", price=2495, setup_fee=900, currency="USD",
                     billing_period="month", sort_order=2),
        BrandPackage(id="pkg-pro", platform_id=EVO, key="professional",
                     name="Professional", price=4995, setup_fee=1500, currency="USD",
                     billing_period="month", sort_order=3),
        BrandPackage(id="pkg-multi", platform_id=EVO, key="multi_tenant",
                     name="Multi-Tenant / Custom", is_custom=True, currency="USD",
                     sort_order=4),
        BrandPackage(id="pkg-bb", platform_id=BB, key="starter",
                     name="BookaBoost Starter", price=149, currency="USD",
                     billing_period="month", sort_order=1),
    ])
    db.flush()

    def mk(uid, email, name, role="advisor"):
        db.add(User(id=uid, organization_id=None, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True))

    mk("u-god", "owner@local.test", "Platform Owner", role="god_admin")
    mk("u-mgr", "manager@local.test", "Regional Sales Manager")
    mk("u-rep1", "rep1@local.test", "Avery Lang")
    mk("u-rep2", "rep2@local.test", "Jordan Wells")
    mk("u-impl", "impl@local.test", "Riley Chen")
    mk("u-bbmgr", "bbmanager@local.test", "BookaBoost Manager")
    db.flush()

    db.add_all([
        Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG, scope_id="bso-evo",
                   role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-rep1", scope_type=SCOPE_BRAND_SALES_ORG, scope_id="bso-evo",
                   role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-rep2", scope_type=SCOPE_BRAND_SALES_ORG, scope_id="bso-evo",
                   role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bbmgr", scope_type=SCOPE_BRAND_SALES_ORG, scope_id="bso-bb",
                   role=ROLE_SALES_MANAGER, is_active=True),
    ])
    db.flush()

    def opp(oid, name, contact, stage, status, pkg, value, owner, days_ago,
            won_days=None, phone=None, email=None, industry="funeral", nxt=None,
            due_days=None):
        db.add(Opportunity(
            id=oid, brand_sales_org_id="bso-evo", owner_user_id=owner,
            company_name=name, contact_name=contact, phone=phone, email=email,
            industry=industry, timezone=CHI, stage=stage, status=status,
            selected_package_id=pkg, deal_value=value,
            won_at=(NOW - timedelta(days=won_days)) if won_days is not None else None,
            next_action=nxt,
            next_action_due_at=(NOW + timedelta(days=due_days)) if due_days is not None else None,
            created_at=NOW - timedelta(days=days_ago),
            updated_at=NOW - timedelta(days=max(0, days_ago // 4))))

    # Won and not yet provisioned - this is what the Won queue is for.
    opp("opp-cedar", "Cedar Ridge Memorial", "Dana Reyes", STAGE_WON, "won",
        "pkg-pro", 4995, "u-rep1", 46, won_days=3,
        phone="+15125550142", email="dana@cedarridge.test")
    # Won, provisioned below, mid-implementation.
    opp("opp-brook", "Brookfield Family Services", "Sam Ortiz", STAGE_WON, "won",
        "pkg-growth", 2495, "u-rep2", 71, won_days=26,
        phone="+15125550188", email="sam@brookfield.test")
    # Won, provisioned below, launched.
    opp("opp-north", "Northgate Chapel", "Lee Park", STAGE_WON, "won",
        "pkg-start", 1497, "u-rep1", 120, won_days=61,
        phone="+15125550199", email="lee@northgate.test")
    # Open pipeline, including one overdue next action and one stalled deal.
    opp("opp-lake", "Lakeside Rest", "Morgan Diaz", STAGE_CLOSING, "open",
        "pkg-pro", 4995, "u-rep1", 22, nxt="Send revised terms", due_days=-4)
    opp("opp-vall", "Valley Green Cemetery", "Casey Nolan", STAGE_PROPOSAL, "open",
        "pkg-growth", 2495, "u-rep2", 31, nxt="Follow up on proposal", due_days=2)
    opp("opp-pine", "Pinecrest Funeral Home", "Robin Vale", STAGE_DEMO_BUILD, "open",
        "pkg-pro", 4995, "u-rep2", 12, nxt="Build the demo", due_days=5)
    opp("opp-elm", "Elmwood Services", "Alex Bright", STAGE_DISCOVERY, "open",
        "pkg-start", 1497, "u-rep1", 40, nxt="Book discovery", due_days=-9)
    db.add(Opportunity(
        id="opp-bb1", brand_sales_org_id="bso-bb", owner_user_id="u-bbmgr",
        company_name="Northside Barbers", contact_name="Jamie Fox",
        industry="salon", timezone=CHI, stage=STAGE_CLOSING, status="open",
        selected_package_id="pkg-bb", deal_value=149,
        created_at=NOW - timedelta(days=9), updated_at=NOW - timedelta(days=1)))
    db.flush()

    db.add(DiscoveryRecord(
        opportunity_id="opp-cedar",
        business_description="Family-owned funeral home, two locations.",
        business_goals="Book more pre-need appointments without adding staff.",
        current_process="Front desk returns calls the next morning.",
        current_tools="Paper diary and a shared spreadsheet.",
        bottlenecks="Nobody answers the phone after 5pm or at weekends.",
        required_integrations="Google Calendar for both directors.",
        desired_outcome="Every after-hours enquiry gets an appointment offer.",
        opportunity_notes="Wants the voice agent live before the launch date.",
        completed_at=NOW - timedelta(days=20)))

    db.add_all([
        Proposal(id="prop-cedar", created_by_id="u-rep1", brand_sales_org_id="bso-evo",
                 opportunity_id="opp-cedar", proposal_number="P-1042", version=2,
                 title="Professional", client_company="Cedar Ridge Memorial",
                 client_name="Dana Reyes", package_id="pkg-pro",
                 base_amount=4995, final_amount=4995, currency="USD",
                 sales_status=PROP_ACCEPTED, status="accepted",
                 implementation_plan="Week 1 configuration and calendar connection. "
                                     "Week 2 cadences, AI tone and voice script. "
                                     "Week 3 testing and staff training.",
                 scope="Two locations, four users, one shared voice number.",
                 accepted_at=NOW - timedelta(days=3),
                 sent_at=NOW - timedelta(days=7),
                 first_viewed_at=NOW - timedelta(days=6)),
        Proposal(id="prop-vall", created_by_id="u-rep2", brand_sales_org_id="bso-evo",
                 opportunity_id="opp-vall", proposal_number="P-1051", version=1,
                 title="Growth", client_company="Valley Green Cemetery",
                 package_id="pkg-growth", base_amount=2495, final_amount=2495,
                 currency="USD", sales_status=PROP_SENT, status="sent",
                 sent_at=NOW - timedelta(days=5),
                 first_viewed_at=NOW - timedelta(days=4)),
        Proposal(id="prop-lake", created_by_id="u-rep1", brand_sales_org_id="bso-evo",
                 opportunity_id="opp-lake", proposal_number="P-1049", version=1,
                 title="Professional", client_company="Lakeside Rest",
                 package_id="pkg-pro", base_amount=4995, final_amount=4750,
                 currency="USD", sales_status=PROP_SENT, status="sent",
                 sent_at=NOW - timedelta(days=2)),
    ])

    for n, (oid, comp) in enumerate([("opp-lake", "Lakeside Rest"),
                                     ("opp-pine", "Pinecrest Funeral Home"),
                                     ("opp-vall", "Valley Green Cemetery")]):
        db.add(SalesAppointment(
            brand_sales_org_id="bso-evo", opportunity_id=oid,
            title="Working session", starts_at=NOW + timedelta(days=n + 1, hours=15),
            ends_at=NOW + timedelta(days=n + 1, hours=16), timezone=CHI,
            status=APPT_SCHEDULED, prospect_company=comp, created_by="u-rep1"))
    db.commit()
    db.close()

    # ── drive two of the Won deals through the REAL services ──
    from app.services import provisioning as prov
    from app.services import implementation_service as impls
    from app.services import customer_activation as act

    db = SessionLocal()
    god = db.query(User).filter(User.id == "u-god").first()

    brook = db.query(Opportunity).filter(Opportunity.id == "opp-brook").first()
    impl_b, _ = prov.provision_customer(
        db, brook, god, org_name="Brookfield Family Services",
        target_launch_date=NOW + timedelta(days=9),
        owner_user_id="u-impl",
        notes="Two offices; the second one goes live a month later.")
    for k in ("kickoff", "business_profile", "customer_users", "sms"):
        impls.set_milestone(db, impl_b, god, k, "done")
    impls.set_milestone(db, impl_b, god, "calendar", "in_progress")
    impls.set_status(db, impl_b, god, "configuration")
    org_b = db.query(Organization).filter(Organization.id == impl_b.organization_id).first()
    # The raw token is deliberately discarded here. The operator gets it from the
    # UI, once; a seeder that printed it would be teaching the wrong habit.
    act.create_customer_admin(db, org_b, god,
                              full_name="Sam Ortiz", email="sam@brookfield.test")

    north = db.query(Opportunity).filter(Opportunity.id == "opp-north").first()
    impl_n, _ = prov.provision_customer(
        db, north, god, org_name="Northgate Chapel",
        target_launch_date=NOW - timedelta(days=30), owner_user_id="u-impl")
    for m in impls.milestones(db, impl_n):
        impls.set_milestone(db, impl_n, god, m.key, "done")
    impls.set_status(db, impl_n, god, "ready_for_launch")
    org_n = db.query(Organization).filter(Organization.id == impl_n.organization_id).first()
    act.create_customer_admin(db, org_n, god,
                              full_name="Lee Park", email="lee@northgate.test")
    impls.launch(db, impl_n, god, acknowledge_warnings=True,
                 note="Staff trained; both directors' calendars connected.")
    db.close()

    print("Seeded %s" % os.path.abspath(DB))
    print("  owner@local.test / %s   (god_admin)" % PW)
    print("  manager@local.test, rep1@local.test, rep2@local.test, impl@local.test")
    print("  Cedar Ridge Memorial is Won and NOT provisioned - use the Won queue.")


if __name__ == "__main__":
    main()
