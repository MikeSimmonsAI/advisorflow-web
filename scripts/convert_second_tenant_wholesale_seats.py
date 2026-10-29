"""PREPARED FOR OWNER APPROVAL - NOT RUN.

Per-workspace Wholesale seats (#272). Before this change every Wholesale seat
was ONE platform row ("wholesale"), so a person put on a second-tenant
workspace's team (BookaBoost Wholesale) held the same row that opens EvoSys's
own Wholesale. After the change a second tenant reads only its OWN rows
("wholesale:<org id>"), so anyone who opened that workspace only through the
old platform row (and is not one of its org admins) would no longer get in
until they have a seat on that workspace's team.

What this does, and only this:
  * finds every second-tenant Wholesale workspace (same rule the app uses:
    product_access.is_second_tenant)
  * for each person of that workspace (wholesale_team.people_q: home org, or an
    active workspace membership) who holds an ACTIVE platform "wholesale" row
    and has NO row of that workspace's own yet, proposes ONE new
    "wholesale:<org id>" row with the same team role, and the same manager when
    that manager is also being given (or already has) a seat there
  * NEVER changes, deactivates or deletes a platform "wholesale" row: those
    decide EvoSys Wholesale access, which this must not touch. Whether anyone
    should also lose their EvoSys seat is a separate owner decision.
  * NEVER touches GOD / super admin logins (they need no seat).

Dry run by default: prints what it would do and writes nothing. To apply:

    python scripts/convert_second_tenant_wholesale_seats.py \\
        --apply --approved-by "<name>" --confirm "CONVERT SECOND TENANT WHOLESALE SEATS"

Rollback: set is_active false on the rows it created (each is listed, and each
carries granted_by = the approving platform owner login when one exists).
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PHRASE = "CONVERT SECOND TENANT WHOLESALE SEATS"


def plan(db):
    """What would be created, as plain dicts. Reads only."""
    from app.models.models import Organization, User
    from app.models.sales_models import Membership, SCOPE_PRODUCT
    from app.services import product_access as PA
    from app.services import wholesale_team as T

    out = []
    for org in db.query(Organization).order_by(Organization.created_at.asc()).all():
        if not PA.is_second_tenant(org):
            continue
        key = PA.org_seat_key(org.id)
        people = {u.id: u for u in T.people_q(db, org.id).all()
                  if getattr(u, "role", None) not in ("god_admin", "super_admin")}
        if not people:
            continue
        legacy = {m.user_id: m for m in db.query(Membership).filter(
            Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == PA.WHOLESALE,
            Membership.is_active.is_(True), Membership.user_id.in_(list(people))).all()}
        have = {m.user_id for m in db.query(Membership).filter(
            Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == key,
            Membership.user_id.in_(list(people))).all()}
        todo = [uid for uid in legacy if uid not in have]
        seated = have | set(todo)
        for uid in todo:
            m = legacy[uid]
            boss = m.reports_to_user_id if m.reports_to_user_id in seated else None
            out.append({"org_id": org.id, "org_name": org.name, "scope_id": key,
                        "user_id": uid, "email": people[uid].email,
                        "role": m.role if m.role in T.ROLES else T.MEMBER,
                        "reports_to_user_id": boss})
    return out


def apply(db, rows, granted_by=None):
    """Create the planned rows (idempotent: an existing row is left alone)."""
    from app.models.sales_models import Membership, SCOPE_PRODUCT
    made = 0
    for r in rows:
        exists = db.query(Membership.id).filter(
            Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == r["scope_id"],
            Membership.user_id == r["user_id"]).first()
        if exists:
            continue
        db.add(Membership(user_id=r["user_id"], scope_type=SCOPE_PRODUCT, scope_id=r["scope_id"],
                          role=r["role"], reports_to_user_id=r["reports_to_user_id"], is_active=True,
                          granted_by=granted_by, created_at=datetime.utcnow()))
        made += 1
    db.commit()
    return made


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--approved-by", default="")
    ap.add_argument("--confirm", default="")
    a = ap.parse_args(argv)

    from app.deps import SessionLocal
    import app.models.registry  # noqa: F401  (every table mapped)
    from app.models.models import User

    db = SessionLocal()
    try:
        rows = plan(db)
        for r in rows:
            print("would create  %s  for %s  in %s  role=%s  manager=%s"
                  % (r["scope_id"], r["email"], r["org_name"], r["role"], r["reports_to_user_id"] or "-"))
        print("%d seat(s) to create" % len(rows))
        if not a.apply:
            print("DRY RUN - nothing changed.")
            return 0
        if a.confirm != PHRASE or not a.approved_by.strip():
            print('STOP: needs --approved-by "<name>" and --confirm "%s"' % PHRASE)
            return 2
        owner = db.query(User).filter(User.role == "god_admin", User.is_active.is_(True)).first()
        made = apply(db, rows, granted_by=getattr(owner, "id", None))
        print("created %d seat(s); approved by %s" % (made, a.approved_by.strip()))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
