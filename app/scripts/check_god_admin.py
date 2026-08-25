"""Inspect (and optionally create) the god_admin account. Local DBs only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.scripts._safe_db import require_local_db, require_init_password

require_local_db()

from app.deps import SessionLocal
from app.models.models import User, Organization
from app.services.auth_service import hash_password

target = os.environ.get("GOD_ADMIN_EMAIL", "mike@simmonsstrong.com")

db = SessionLocal()
try:
    u = db.query(User).filter(User.email == target).first()
    if u:
        print("User found: %s  role=%s  active=%s" % (u.email, u.role, u.is_active))
        if u.role != "god_admin":
            u.role = "god_admin"
            db.commit()
            print("  -> role upgraded to god_admin")
    else:
        print("User NOT found: %s" % target)
        # Creating an account needs a password, so it needs GOD_ADMIN_INIT_PW.
        new_pw = require_init_password()
        db.add(User(
            email=target,
            password_hash=hash_password(new_pw),
            full_name="Mike Simmons",
            role="god_admin",
            is_active=True,
            must_change_password=False,
            organization_id=None,
        ))
        db.commit()
        print("  -> created %s (password taken from GOD_ADMIN_INIT_PW)" % target)

    orgs = db.query(Organization).all()
    print("\nOrganizations in DB (%d):" % len(orgs))
    for o in orgs:
        print("  %-40s plan=%-12s active=%s" % (o.name, o.plan, o.is_active))
finally:
    db.close()
