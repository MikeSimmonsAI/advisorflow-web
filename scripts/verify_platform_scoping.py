# scripts/verify_platform_scoping.py  --  READ ONLY.
# Runs the real get_platform_org_ids() against the real database for every
# admin account and prints exactly which orgs each one can see. This is the
# acceptance test for the platform-scope migration.
#
# Expected after the Aug 25 2026 migration:
#   god_admin   -> every org on every platform
#   super_admin -> only orgs on their own platform
import os, sys

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set in this shell.")
os.environ.setdefault("JWT_SECRET", "local" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "local" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal, get_platform_org_ids
from app.models.models import User, Organization, Platform

db = SessionLocal()
failures = []
try:
    all_orgs = {o.id: o for o in db.query(Organization).all()}
    platforms = {p.id: p.slug for p in db.query(Platform).all()}
    total = len(all_orgs)

    admins = (db.query(User)
              .filter(User.role.in_(["super_admin", "god_admin"]))
              .order_by(User.role, User.email).all())

    for u in admins:
        ids = get_platform_org_ids(u, db)
        visible = [all_orgs[i] for i in ids if i in all_orgs]
        print("\n%s  (%s)" % (u.email, u.role))
        print("  user.platform_id : %s" % (u.platform_id or "(null)"))
        print("  can see %d of %d orgs:" % (len(visible), total))
        for o in sorted(visible, key=lambda x: x.name):
            print("     - %-36s [%s]" % (o.name, platforms.get(o.platform_id, "unassigned")))

        if u.role == "god_admin":
            if len(ids) != total:
                failures.append("%s is god_admin but sees %d/%d orgs" % (u.email, len(ids), total))
            else:
                print("  OK: god sees everything")
        else:
            foreign = [o for o in visible if o.platform_id != u.platform_id]
            if not u.platform_id:
                failures.append("%s is super_admin with NULL platform_id - will be scoped to own org only" % u.email)
            elif foreign:
                failures.append("%s LEAKS %d org(s) from another platform: %s" % (
                    u.email, len(foreign), ", ".join(o.name for o in foreign)))
            else:
                print("  OK: scoped to platform '%s', no cross-platform leak" % platforms.get(u.platform_id, "?"))
finally:
    db.close()

print("\n" + "=" * 60)
if failures:
    print("FAILURES:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("PLATFORM SCOPING VERIFIED - no cross-platform leaks")
