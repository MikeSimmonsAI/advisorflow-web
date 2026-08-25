"""
LOCAL demo fixture for the Sales Workspace. Development only.

Builds a throwaway SQLite database with the EvoSys Pro sales team, one
opportunity and a couple of meetings, so the scheduling UI can be exercised and
screenshotted without inventing anything in production.

    set DATABASE_URL=sqlite:///./demo.db
    python scripts/demo_local_scheduling.py
    uvicorn app.main:app --port 8010

REFUSES TO RUN against anything but SQLite. Production data stays clean —
real prospects arrive only through the real create flow.
"""
import os
import sys
from datetime import datetime, timedelta

url = os.environ.get("DATABASE_URL", "")
if not url.startswith("sqlite"):
    sys.exit("REFUSING: this is a local demo fixture and DATABASE_URL is not SQLite.")
os.environ.setdefault("JWT_SECRET", "demo" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "demo" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal, engine                     # noqa: E402
from app.models.models import Base, Platform, Organization, User   # noqa: E402
from app.models.sales_models import (                          # noqa: E402
    Membership, BrandSalesOrg, BrandPackage, Opportunity, OpportunityEvent,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (                     # noqa: E402
    AvailabilityProfile, AvailabilityWindow, AvailabilityBlock, BLOCK_RECURRING,
)
import app.models.scheduling_models  # noqa: E402,F401
from app.services.auth_service import hash_password            # noqa: E402
from app.services import availability as av                    # noqa: E402
from app.services.meeting_roles import ensure_meeting_types    # noqa: E402

PW = "DemoPass123!"
CHI = "America/Chicago"

Base.metadata.create_all(bind=engine)
db = SessionLocal()

if db.query(BrandSalesOrg).count():
    print("Demo data already present.")
    sys.exit(0)

evo = Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro")
db.add(evo); db.flush()

sales = BrandSalesOrg(id="bso-evo", platform_id=evo.id, name="EvoSys Pro Sales",
                      slug="evosyspro-sales", timezone=CHI)
db.add(sales); db.flush()

for key, name, price, order in [("starter", "Starter", 1497, 1),
                                ("growth", "Growth", 2495, 2),
                                ("professional", "Professional", 4995, 3),
                                ("multi_tenant", "Multi-Tenant / Custom", None, 4)]:
    db.add(BrandPackage(platform_id=evo.id, key=key, name=name, price=price,
                        sort_order=order, is_custom=price is None))

TEAM = [
    ("u-blake", "blake@demo.local", "Blake Rehani", ROLE_SALES_REP, "advisor"),
    ("u-michael", "michael@demo.local", "Michael Schlueter", ROLE_SALES_MANAGER, "advisor"),
    ("u-mike", "mike@demo.local", "Mike Simmons", ROLE_SALES_MANAGER, "god_admin"),
]
for uid, email, name, srole, urole in TEAM:
    u = User(id=uid, organization_id=None, email=email, full_name=name,
             password_hash=hash_password(PW), role=urole,
             must_change_password=False, is_active=True)
    db.add(u); db.flush()
    db.add(Membership(user_id=uid, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=sales.id, role=srole, is_active=True))
    p = AvailabilityProfile(user_id=uid, timezone=CHI, min_notice_minutes=0,
                            booking_horizon_days=90, buffer_before_minutes=0,
                            buffer_after_minutes=15)
    db.add(p); db.flush()
    for dow in range(5):
        db.add(AvailabilityWindow(profile_id=p.id, day_of_week=dow,
                                  start_minute=9 * 60, end_minute=17 * 60))
        db.add(AvailabilityBlock(profile_id=p.id, kind=BLOCK_RECURRING, label="Lunch",
                                 day_of_week=dow, start_minute=12 * 60, end_minute=13 * 60))

# Michael runs a standing team meeting on Monday mornings — it makes the
# intersection visibly different from a union on the Team Availability grid.
pm = db.query(AvailabilityProfile).filter(AvailabilityProfile.user_id == "u-michael").first()
db.add(AvailabilityBlock(profile_id=pm.id, kind=BLOCK_RECURRING, label="Pipeline review",
                         day_of_week=0, start_minute=9 * 60, end_minute=11 * 60))

now = datetime.utcnow()
opps = [
    ("opp-atlas", "Atlas Restoration", "Renee Carter", "renee@atlas.example",
     "2145550101", "Restoration", "discovery", "u-blake", "Run discovery call"),
    ("opp-evergreen", "Evergreen Roofing", "Chris Nolan", "chris@evergreen.example",
     "2145550102", "Roofing", "demo_build", "u-blake", "Build the demo"),
    ("opp-metro", "Metro Windows", "Dana Brooks", "dana@metro.example",
     "2145550103", "Home services", "demo_proposal", "u-blake", "Send proposal"),
    ("opp-premier", "Premier Fence Co.", "John Ellis", "john@premier.example",
     "2145550104", "Fencing", "prospect", "u-blake", "First contact"),
    ("opp-oakridge", "Oakridge Home Services", "Lisa Patel", "lisa@oakridge.example",
     "2145550105", "Home services", "contacted", "u-michael", "Qualify"),
]
for oid, company, contact, email, phone, industry, stage, owner, action in opps:
    o = Opportunity(id=oid, brand_sales_org_id=sales.id, owner_user_id=owner,
                    company_name=company, contact_name=contact, email=email,
                    phone=phone, industry=industry, stage=stage, status="open",
                    timezone=CHI, next_action=action,
                    next_action_due_at=now + timedelta(days=1),
                    stage_changed_at=now - timedelta(days=3))
    db.add(o); db.flush()
    db.add(OpportunityEvent(opportunity_id=oid, event_type="created",
                            summary="Prospect created", detail=company,
                            actor_user_id=owner))

ensure_meeting_types(db, sales.id)
db.commit()
db.close()

print("Demo fixture ready.")
print("  blake@demo.local / michael@demo.local / mike@demo.local")
print("  password: %s" % PW)
