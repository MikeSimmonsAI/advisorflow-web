"""
Build a LOCAL demo database for screenshots and manual walkthroughs.

NEVER TOUCHES PRODUCTION. It writes to a SQLite file under the repo and refuses
to run against anything else — the guard below is the whole point of the file.
Production holds 14,735 real leads and zero fake prospects, and it stays that
way.

    python scripts/demo_env.py
    uvicorn app.main:app --port 8099        (with DATABASE_URL pointed here)
"""
import os
import sys
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO, "demo_local.db")
DB_URL = "sqlite:///" + DB_PATH.replace("\\", "/")

# The guard. An accidental run with a production DATABASE_URL exported in the
# shell would otherwise seed fake prospects into a real pipeline.
existing = (os.environ.get("DATABASE_URL") or "").strip()
if existing and not existing.startswith("sqlite"):
    print("REFUSING TO RUN: DATABASE_URL points at %s" % existing.split("@")[-1][:40])
    print("This script only ever seeds a local SQLite file.")
    sys.exit(2)

os.environ["DATABASE_URL"] = DB_URL
os.environ["JWT_SECRET"] = "demo" + "0" * 60
os.environ["SECRET_KEY"] = "demo" + "0" * 60
sys.path.insert(0, REPO)

from app.deps import SessionLocal, engine                        # noqa: E402
from app.models.models import (                                  # noqa: E402
    Base, Platform, User, PortalEvent, PROP_SENT,
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED, PORTAL_DEMO_OPENED,
)
from app.models.sales_models import (                            # noqa: E402
    Membership, BrandSalesOrg, Opportunity, BrandPackage, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (                       # noqa: E402
    MeetingType, SalesAppointment, AppointmentParticipant,
)
from app.models.meeting_models import AppointmentMeeting, MEET_CREATED  # noqa: E402
import app.models.calendar_models                                # noqa: F401,E402
from app.services.auth_service import hash_password              # noqa: E402
from app.services import proposal_service as ps                  # noqa: E402
from app.services.meeting_roles import ensure_meeting_types      # noqa: E402

PW = "DemoPass123!"
CHI = "America/Chicago"


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    now = datetime.utcnow()

    db.add(Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"))
    db.flush()
    db.add(BrandSalesOrg(id="bso-evo", platform_id="plt-evo",
                         name="EvoSys Pro Sales", slug="evosyspro-sales",
                         timezone=CHI))
    db.flush()
    for key, name, price in (("starter", "Starter", 1497),
                             ("growth", "Growth", 2495),
                             ("professional", "Professional", 4995)):
        db.add(BrandPackage(id="pkg-" + key, platform_id="plt-evo", key=key,
                            name=name, price=price, currency="USD",
                            description=("The full platform, configured for you."
                                         if key == "professional" else None)))
    db.flush()

    def mk(uid, email, name, role="advisor"):
        u = User(id=uid, organization_id=None, email=email, full_name=name,
                 password_hash=hash_password(PW), role=role,
                 must_change_password=False, is_active=True)
        db.add(u)
        return u

    mk("u-blake", "blake@evosyspro.live", "Blake Rehani")
    mk("u-michael", "michael@evosyspro.live", "Michael Schlueter")
    db.flush()
    db.add(Membership(user_id="u-blake", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True))
    db.add(Membership(user_id="u-michael", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True))
    db.commit()

    ensure_meeting_types(db, "bso-evo")
    db.commit()

    # ── the headline deal ───────────────────────────────────────────────────
    opp = Opportunity(
        id="opp-demo", brand_sales_org_id="bso-evo", owner_user_id="u-blake",
        company_name="Greenland Memorial", contact_name="Dana Reyes",
        email="dana@greenland.example", phone="(469) 555-0142",
        industry="Memorial services", stage="closing", status="open",
        selected_package_id="pkg-professional",
        demo_url="https://demo.evosyspro.live/greenland",
        demo_status="ready", demo_ready_at=now - timedelta(days=6),
        demo_notes="INTERNAL: their IT lead is the blocker — go around him.",
        next_action="Confirm acceptance and start closing",
        next_action_due_at=now,
        discovery_completed_at=now - timedelta(days=12))
    db.add(opp)
    db.flush()
    db.add(DiscoveryRecord(
        opportunity_id="opp-demo",
        business_description="Family-run memorial services group across three locations.",
        business_goals="Book more appointments without hiring another coordinator.",
        current_process="Everything runs through phone calls and a paper diary.",
        current_tools="Google Workspace, a shared inbox, and a whiteboard.",
        bottlenecks="Nobody answers the phone after 5pm and evening enquiries go cold.",
        required_integrations="Google Calendar and the existing website form.",
        automation_opportunities="After-hours capture, automatic follow-up, reminders.",
        desired_outcome="Stop losing evening enquiries.",
        team_size="11 across three locations",
        completed_at=now - timedelta(days=12)))
    db.commit()


    # ── meetings, one past and one upcoming with a Zoom room ────────────────
    blake = db.query(User).filter(User.id == "u-blake").first()
    dd = (db.query(MeetingType)
          .filter(MeetingType.brand_sales_org_id == "bso-evo",
                  MeetingType.key == "discovery_demo").first())
    closing = (db.query(MeetingType)
               .filter(MeetingType.brand_sales_org_id == "bso-evo",
                       MeetingType.key == "closing").first())

    def appt(aid, mt, starts, mins, title, with_zoom, conf="confirmed"):
        a = SalesAppointment(
            id=aid, brand_sales_org_id="bso-evo", opportunity_id="opp-demo",
            meeting_type_id=mt.id, title=title,
            starts_at=starts, ends_at=starts + timedelta(minutes=mins),
            timezone=CHI, status="scheduled", confirmation_status=conf,
            prospect_name="Dana Reyes", prospect_company="Greenland Memorial",
            prospect_email="dana@greenland.example",
            meeting_provider="zoom" if with_zoom else None,
            meeting_url=("https://zoom.us/j/8842019" + aid[-2:]) if with_zoom else None,
            notes="INTERNAL: budget is soft — do not lead with the discount.")
        db.add(a)
        db.flush()
        for uid in ("u-blake", "u-michael"):
            db.add(AppointmentParticipant(
                appointment_id=a.id, user_id=uid, is_required=True,
                busy_start_at=a.starts_at, busy_end_at=a.ends_at))
        if with_zoom:
            # A demo Zoom room. host_url_encrypted is deliberately left NULL —
            # this file never fabricates a credential, even a fake one.
            db.add(AppointmentMeeting(
                appointment_id=a.id, brand_sales_org_id="bso-evo",
                provider="zoom", provider_meeting_id="884201" + aid[-2:],
                join_url=a.meeting_url, passcode="418205",
                status=MEET_CREATED, last_synced_at=now))
        return a

    # Today, a couple of hours out — so My Day has something to join.
    soon = now + timedelta(hours=2)
    appt("appt-01", dd, now - timedelta(days=6, hours=3), 60,
         "Discovery + Demo", True)
    appt("appt-02", closing, soon, 60, "Closing Call", True, conf="pending")
    db.commit()

    # ── the proposal, sent and viewed ───────────────────────────────────────
    prop = ps.create_proposal(db, opp, blake, now=now - timedelta(days=4))
    ps.apply_pricing(db, prop, db.query(User).filter(User.id == "u-michael").first(),
                     adjustment=-500,
                     reason="Competitive against Vendor X; approved by Michael",
                     now=now - timedelta(days=4))
    prop.deliverables = ("Configured EvoSys Pro workspace\n"
                         "After-hours enquiry capture\n"
                         "Automated follow-up sequences\n"
                         "Google Calendar two-way sync\n"
                         "Team training session")
    prop.implementation_plan = ("Week 1 — workspace configured and data imported.\n"
                                "Week 2 — automations live, team trained.\n"
                                "Week 3 — review and tune.")
    prop.terms = ("Monthly, cancel any time with 30 days' notice.\n"
                  "Setup and training included.")
    db.commit()

    ps.publish_proposal(db, prop, blake, now=now - timedelta(days=4))
    from app.models.models import ProposalBlock
    db.add(ProposalBlock(proposal_id=prop.id, block_type="website_url",
                         position=99, content="Your demo site",
                         file_url="https://demo.evosyspro.live/greenland"))
    db.commit()


    # Send it WITHOUT touching email — dry_run publishes and issues the key but
    # contacts nobody. No demo run may ever email a real address.
    sent_at = now - timedelta(days=4)
    res = ps.send_proposal(db, prop, blake, now=sent_at, dry_run=True)

    # A dry run publishes and issues the key but does not claim the customer
    # received anything — so this seed states the "already sent" story it wants
    # in its own name, rather than leaning on a preview to fake it.
    prop.sales_status = PROP_SENT
    prop.sent_at = sent_at
    opp.proposal_status = PROP_SENT
    opp.proposal_sent_at = sent_at
    db.commit()

    tok = (db.query(__import__("app.models.models", fromlist=["ProposalToken"])
                    .ProposalToken)
           .filter_by(proposal_id=prop.id).first())

    # Buyer activity, as it would really have been recorded.
    for mins, ev, label in ((27, PORTAL_OPENED, None),
                            (29, PORTAL_PROPOSAL_VIEWED, None),
                            (34, PORTAL_DEMO_OPENED, "Your demo site"),
                            (123, PORTAL_OPENED, None)):
        db.add(PortalEvent(
            proposal_id=prop.id, opportunity_id="opp-demo",
            token_id=tok.id if tok else None, event_type=ev, label=label,
            proposal_version=prop.version,
            recipient_email="dana@greenland.example",
            user_agent_family="Chrome",
            occurred_at=now - timedelta(days=4) + timedelta(minutes=mins)))
    prop.first_viewed_at = now - timedelta(days=4) + timedelta(minutes=29)
    prop.last_viewed_at = now - timedelta(days=4) + timedelta(minutes=123)
    from app.models.models import PROP_VIEWED
    prop.sales_status = PROP_VIEWED
    db.commit()

    # ── two more deals so the queues are not all one row ────────────────────
    o2 = Opportunity(id="opp-2", brand_sales_org_id="bso-evo",
                     owner_user_id="u-blake", company_name="Ridgeway Funeral Home",
                     contact_name="Marcus Hale", email="marcus@ridgeway.example",
                     stage="proposal", status="open",
                     selected_package_id="pkg-growth",
                     next_action="Send the proposal")
    o3 = Opportunity(id="opp-3", brand_sales_org_id="bso-evo",
                     owner_user_id="u-blake", company_name="Pinecrest Services",
                     contact_name="Ana Whitfield", email="ana@pinecrest.example",
                     stage="proposal", status="open",
                     selected_package_id="pkg-starter")
    db.add_all([o2, o3])
    db.commit()

    p2 = ps.create_proposal(db, o2, blake, now=now - timedelta(days=1))
    ps.publish_proposal(db, p2, blake, now=now - timedelta(days=1))   # READY
    p3 = ps.create_proposal(db, o3, blake, now=now - timedelta(hours=5))  # DRAFT
    db.commit()

    # A rep waiting on their manager (Checkpoint 5). Created through the real
    # service so the demo exercises the same validation the product does, and
    # so the Team Command screen has a genuine decision to show rather than a
    # hand-written row that could never have been produced by the app.
    from app.services import pricing_approvals as appr
    appr.create_request(db, p3, blake, -300,
                        "They are quoting Vendor X at 1,200. I need to be close.",
                        now=now - timedelta(hours=4))
    db.commit()

    print("Demo database written to %s" % DB_PATH)
    print("  blake@evosyspro.live / %s   (rep)" % PW)
    print("  michael@evosyspro.live / %s (manager)" % PW)
    if res.get("portal_url"):
        print("  deal room token: %s" % res["portal_url"].rsplit("/", 1)[-1])
    db.close()


if __name__ == "__main__":
    main()
