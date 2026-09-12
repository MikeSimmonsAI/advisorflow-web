"""Stand up a THROWAWAY database with one controlled test identity.

Exists so the three-device scenario in `m2_live_verify.py` can be driven over
real HTTP - real uvicorn, real routing, real middleware, real tokens - without
a single request touching a real account. The database is a file this script
creates; nothing here reads or writes production.

    set DATABASE_URL=sqlite:///./_m2_local.db
    python scripts/m2_local_stage.py
"""
import os
import sys

# Running a file out of scripts/ puts scripts/ on sys.path, not the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "DATABASE_URL" not in os.environ:
    print("Refusing to run without an explicit DATABASE_URL - this script "
          "creates and seeds a database, and it must be one you named.")
    sys.exit(2)
if "sqlite" not in os.environ["DATABASE_URL"]:
    print("Refusing to seed anything that is not a local sqlite file.")
    sys.exit(2)

if not os.environ.get("JWT_SECRET"):
    print("Refusing to run without JWT_SECRET - run_m2_local_verify.bat "
          "generates a throwaway one per run so that none is ever committed.")
    sys.exit(2)

from app.deps import engine, SessionLocal            # noqa: E402
from app.models.models import Base, Organization, User  # noqa: E402
from app.models import registry                      # noqa: E402,F401
from app.services.auth_service import hash_password  # noqa: E402

EMAIL = "m2.verify@example.invalid"
# NOT A LITERAL, deliberately. A password in a repository is a password in a
# repository even when the account is a throwaway one on a throwaway database:
# the runner generates a fresh one per run and passes it through the
# environment, so nothing credential-shaped is ever committed.
PASSWORD = os.environ.get("M2_VERIFY_PASSWORD")
if not PASSWORD:
    print("Refusing to seed without M2_VERIFY_PASSWORD - "
          "run scripts/run_m2_local_verify.bat, which generates one.")
    sys.exit(2)

Base.metadata.create_all(bind=engine)
db = SessionLocal()
try:
    org = db.query(Organization).filter(
        Organization.slug == "m2-verify-org").first()
    if org is None:
        org = Organization(name="M2 Verify Org", slug="m2-verify-org")
        db.add(org)
        db.commit()

    user = db.query(User).filter(User.email == EMAIL).first()
    if user is None:
        user = User(organization_id=org.id, email=EMAIL,
                    password_hash=hash_password(PASSWORD),
                    full_name="M2 Verify Advisor", role="advisor",
                    must_change_password=False)
        db.add(user)
    else:
        user.password_hash = hash_password(PASSWORD)
        user.is_active = True
        user.must_change_password = False
        db.add(user)
    db.commit()
    print("seeded %s in org %s" % (EMAIL, org.id))
finally:
    db.close()
