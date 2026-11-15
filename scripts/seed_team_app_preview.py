"""Seed SYNTHETIC people for a local preview of the EvoSys Pro team app (/app).

LOCAL ONLY. Refuses to run unless DATABASE_URL is a SQLite file, so it can
never write fake people into a real database.

    set DATABASE_URL=sqlite:///./team_app_preview.db        (Windows)
    export DATABASE_URL=sqlite:///./team_app_preview.db     (macOS/Linux)
    python scripts/seed_team_app_preview.py

Every account below uses the password  Preview123!
    platform@preview.test   super_admin of the preview platform (every right)
    partner@preview.test    executive, no team rights until granted
    chatmgr@preview.test    may manage chat rooms only
    timemgr@preview.test    Team Time for rep1 only
    rep1@preview.test       sales rep (Chat + Time)
    rep2@preview.test       sales rep (Chat + Time)
    outsider@preview.test   customer advisor, no Chat or Time
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

URL = os.environ.get("DATABASE_URL", "")
if not URL.startswith("sqlite:///"):
    sys.exit("Refusing: DATABASE_URL must be a local sqlite:/// file for preview seeding.")

import app.models.registry  # noqa: E402,F401
from app.deps import engine, SessionLocal  # noqa: E402
from app.models.models import Base, Organization, Platform, User, UserCapabilityGrant  # noqa: E402
from app.models.sales_models import (BrandSalesOrg, Membership, ROLE_BRAND_EXECUTIVE,  # noqa: E402
                                     SCOPE_BRAND_SALES_ORG, SCOPE_PLATFORM)
from app.models.team_app_models import TeamTimeScope  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402

PASSWORD = "Preview123!"


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if db.query(Platform).filter(Platform.slug == "preview-evosys").first():
        print("Already seeded.")
        return
    p = Platform(name="EvoSys Pro (Preview)", slug="preview-evosys")
    db.add(p); db.flush()
    bso = BrandSalesOrg(platform_id=p.id, name="Preview Sales Team", slug="preview-sales")
    org = Organization(name="Preview Customer Co", slug="preview-customer", plan="standard", industry="generic")
    db.add_all([bso, org]); db.flush()

    def u(email, name, role="advisor", platform=False, org_id=None):
        x = User(email=email, full_name=name, role=role, password_hash=hash_password(PASSWORD),
                 must_change_password=False, platform_id=p.id if platform else None,
                 organization_id=org_id)
        db.add(x); db.flush()
        return x

    plat = u("platform@preview.test", "Platform Preview", role="super_admin", platform=True)
    partner = u("partner@preview.test", "Partner Preview")
    chatmgr = u("chatmgr@preview.test", "Chat Manager Preview")
    timemgr = u("timemgr@preview.test", "Time Manager Preview")
    rep1 = u("rep1@preview.test", "Rep One")
    rep2 = u("rep2@preview.test", "Rep Two")
    u("outsider@preview.test", "Outside Advisor", org_id=org.id)
    for r in (rep1, rep2):
        db.add(Membership(user_id=r.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=bso.id,
                          role="sales_rep", is_active=True))
    db.add(Membership(user_id=partner.id, scope_type=SCOPE_PLATFORM, scope_id=p.id,
                      role=ROLE_BRAND_EXECUTIVE, is_active=True))
    for who, key in ((chatmgr, "team_chat_manage"), (timemgr, "team_time_manage"), (timemgr, "team_chat")):
        db.add(UserCapabilityGrant(user_id=who.id, scope_type=SCOPE_PLATFORM, scope_id=p.id,
                                   organization_id=None, capability=key, is_active=True,
                                   granted_by=plat.id))
    db.add(TeamTimeScope(platform_id=p.id, manager_user_id=timemgr.id, member_user_id=rep1.id,
                         granted_by=plat.id))
    db.commit()
    print("Seeded synthetic preview people. Password for all: %s" % PASSWORD)


if __name__ == "__main__":
    main()
