"""PREPARED FOR OWNER APPROVAL - NOT RUN.

Gives wholesale@evosyspro.live the same org_admin seat in the Commercial
Cleaning Blueprint workspace that it already holds in the other six EvoSys
workspaces (SCI, Atlantis, Roofing, Restland, Max Life, EVO/Wholesale).

Why it needs approval: Commercial Cleaning Blueprint is an open sales deal
(Demo Build), not a signed customer. Its workspace exists (organization
6fe0124d-..., industry "cleaning", public site /site/commercial-cleaning-
blueprint) but nobody is a member of it. Adding a seat is a new permission,
not one that existing rules already give.

What it does, and only this:
  * looks up the ONE existing user wholesale@evosyspro.live (never creates one)
  * looks up the ONE existing organization by slug commercial-cleaning-blueprint
  * calls workspace_access.grant_workspace_membership (idempotent: an existing
    row is re-activated, never duplicated; nothing else is touched)
  * writes one audit_log_entries row: action "workspace_membership_granted"

Dry run by default. To apply (Render Shell on advisorflow-backend):

    python scripts/grant_wholesale_ccb_workspace.py \
        --apply --approved-by "Mike Simmons" \
        --confirm "GRANT WHOLESALE CCB WORKSPACE"

Rollback (also audited by the existing workspace member screens): set that
membership's is_active to false from Admin > Users, or rerun with --revoke.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EMAIL = "wholesale@evosyspro.live"
ORG_SLUG = "commercial-cleaning-blueprint"
ROLE = "org_admin"
PHRASE = "GRANT WHOLESALE CCB WORKSPACE"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revoke", action="store_true")
    ap.add_argument("--approved-by", default="")
    ap.add_argument("--confirm", default="")
    a = ap.parse_args(argv)

    from app.deps import SessionLocal
    import app.models.registry  # noqa: F401  (every table mapped)
    from app.models.models import AuditLogEntry, Organization, User
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    from app.services import workspace_access

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.email == EMAIL).all()
        orgs = db.query(Organization).filter(Organization.slug == ORG_SLUG).all()
        if len(users) != 1 or len(orgs) != 1:
            print("STOP: expected exactly one user and one organization, found %d / %d" % (len(users), len(orgs)))
            return 2
        user, org = users[0], orgs[0]
        if user.role == "god_admin":
            print("STOP: refusing to touch a god_admin account")
            return 2
        row = (db.query(Membership)
                 .filter(Membership.user_id == user.id, Membership.scope_type == SCOPE_CUSTOMER_ORG,
                         Membership.scope_id == org.id).first())
        print("user   %s  %s  role=%s" % (user.id, user.email, user.role))
        print("org    %s  %s  industry=%s active=%s" % (org.id, org.name, org.industry, org.is_active))
        print("now    %s" % ("membership %s role=%s active=%s" % (row.id, row.role, row.is_active) if row else "no membership"))

        if not a.apply and not a.revoke:
            print("DRY RUN - nothing changed. Would %s an %s membership." % ("re-activate" if row else "create", ROLE))
            return 0
        if a.confirm != PHRASE or not a.approved_by.strip():
            print('STOP: needs --approved-by "<name>" and --confirm "%s"' % PHRASE)
            return 2
        owner = db.query(User).filter(User.role == "god_admin", User.is_active.is_(True)).first()
        actor_id = owner.id if owner else user.id

        if a.revoke:
            if not row or not row.is_active:
                print("Nothing to revoke.")
                return 0
            row.is_active = False
            action = "workspace_membership_revoked"
        elif row is not None and row.is_active and row.role == ROLE:
            print("Already in place - nothing changed, nothing logged.")
            return 0
        else:
            row = workspace_access.grant_workspace_membership(db, user.id, org.id, role=ROLE,
                                                              granted_by=actor_id, commit=False)
            action = "workspace_membership_granted"
        db.add(AuditLogEntry(organization_id=org.id, actor_user_id=actor_id, action=action,
                             target_type="user", target_id=user.id,
                             details=json.dumps({"email": EMAIL, "organization": org.name, "role": ROLE,
                                                 "approved_by": a.approved_by.strip(),
                                                 "source": "scripts/grant_wholesale_ccb_workspace.py"})))
        db.commit()
        print("DONE: %s (membership %s)" % (action, row.id))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
