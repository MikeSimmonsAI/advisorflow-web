"""Reset the local god_admin password. Local databases only - see _safe_db."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.scripts._safe_db import require_local_db, require_init_password

require_local_db()
new_pw = require_init_password()

from app.deps import SessionLocal
from app.models.models import User
from app.services.auth_service import hash_password

target = os.environ.get("GOD_ADMIN_EMAIL", "mike@simmonsstrong.com")

db = SessionLocal()
try:
    u = db.query(User).filter(User.email == target).first()
    if u:
        u.password_hash = hash_password(new_pw)
        u.must_change_password = False
        db.commit()
        print("Password reset for %s (value taken from GOD_ADMIN_INIT_PW)." % target)
    else:
        print("User not found: %s" % target)
finally:
    db.close()
