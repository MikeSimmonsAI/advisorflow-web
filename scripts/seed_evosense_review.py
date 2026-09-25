"""Seed the Phase 7 EvoSense SANDBOX review organization into the LOCAL database.

    cd C:\\Dev\\advisorflow-web
    python scripts\\seed_evosense_review.py

Creates (or reuses) ONE new organization, "EvoSense Review (TEST)", and one
review login in it, then runs the real engine against the sandbox adapters:
the SCHEDULER pass (the same function the platform loop runs every 15 minutes)
finds both strategies due and hunts them, contacts are looked up through the
atomic budget, eligible owners are worked with SIMULATED messages, and three
sandbox seller replies are delivered through the platform's real inbound SMS
processing and routed to EvoSense. Nothing is written by hand that the engine would not write itself.

Refuses anything that is not SQLite. Touches no other organization. Every row
it causes is is_test and labelled SANDBOX / TEST. No real person, phone or
email: phones are 555-01xx, emails end in .example, mailing streets are
"Sandbox ...".
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
if not os.environ.get("DATABASE_URL", "sqlite").startswith("sqlite"):
    raise SystemExit("REFUSING: this seeder only writes to a local SQLite database.")

from app.main import app  # noqa: E402

SLUG = "evosense-review-test"
NAME = "EvoSense Review (TEST)"
EMAIL = "evosense.review@example.test"
PASSWORD = "EvoSense-Review-2026!"


def main() -> int:
    # Phase 7.1: `--autonomy` builds a SECOND, fresh sandbox organization so the
    # autonomous loop can be reviewed from an empty start without touching the
    # Phase 7 review organization.
    global SLUG, NAME, EMAIL
    if "--autonomy" in sys.argv:
        SLUG, NAME, EMAIL = ("evosense-autonomy-test", "EvoSense Autonomy Review (TEST)",
                             "evosense.autonomy@example.test")
    from fastapi.testclient import TestClient
    with TestClient(app) as c:            # startup: create_all + migrations
        c.get("/ping")

    from app.deps import SessionLocal
    from app.models.models import Organization, User
    from app.services.auth_service import hash_password
    from app.services.evosense import sandbox_seed as SS

    db = SessionLocal()
    try:
        template = (db.query(Organization).filter(Organization.name == "Wholesale Review (TEST)").first())
        org = db.query(Organization).filter(Organization.slug == SLUG).first()
        if org is None:
            org = Organization(name=NAME, slug=SLUG,
                               plan=getattr(template, "plan", None) or "standard",
                               industry="real_estate",
                               enabled_features=getattr(template, "enabled_features", None))
            db.add(org)
            db.commit()
        user = db.query(User).filter(User.email == EMAIL).first()
        if user is None:
            user = User(organization_id=org.id, email=EMAIL, password_hash=hash_password(PASSWORD),
                        full_name="EvoSense Reviewer (TEST)", role="org_admin",
                        must_change_password=False)
            db.add(user)
            db.commit()
        out = SS.seed_review(db, org.id, user, replies=True)
        db.commit()
        from app.services.evosense import sandbox_seed
        flag = sandbox_seed.prop_at(db, org.id, "1418 Cedar Springs Rd")
        print("organization  %s  %s" % (org.id, org.name))
        print("login         %s / %s" % (EMAIL, PASSWORD))
        print("strategies    %s" % out["strategies"])
        print("scheduler     %s   (nobody pressed Run hunt)" % out.get("scheduler"))
        print("hunt counts   %s" % out["runs"])
        print("flagship      /wholesale/evosense/property/%s" % (flag.id if flag else "-"))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
