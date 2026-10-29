"""PREPARED FOR OWNER APPROVAL - NOT RUN.

Sets up the BookaBoost Back Office: the BookaBoost sales team record (one
BrandSalesOrg under the BookaBoost platform) and the sales seats for the
people the owner names. Nothing here is run by a deploy or at startup.

What it does, and only this:
  * looks up the ONE platform with slug `bookaboost` (never creates one)
  * creates its sales team record "BookaBoost Sales" if the platform has none
    (a platform already having one is reported and reused, never duplicated)
  * for every --manager / --rep email: looks up that ONE existing user (never
    creates a login) and gives them an active sales_manager / sales_rep
    membership on that team (an existing row is re-activated, not duplicated)
  * refuses god_admin accounts (the owner reaches every Back Office already)
  * writes one audit_log_entries row per change

DRY RUN BY DEFAULT: it prints what it would do and changes nothing. To apply
(Render Shell on the backend service, after the owner approves):

    python scripts/setup_bookaboost_sales_org.py \
        --manager manager@example.com --rep rep@example.com \
        --apply --approved-by "<owner name>" \
        --confirm "SET UP BOOKABOOST BACK OFFICE"

Rollback: set the created memberships' is_active to false from the sales team
screen (or Admin > Users); the team record can stay - with no members nobody
but the owner can enter it.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PLATFORM_SLUG = "bookaboost"
TEAM_NAME = "BookaBoost Sales"
PHRASE = "SET UP BOOKABOOST BACK OFFICE"


def plan_and_apply(db, *, managers=(), reps=(), apply=False, approved_by="", confirm="",
                   platform_slug=PLATFORM_SLUG, team_name=TEAM_NAME):
    """Returns {"ok", "applied", "steps": [...], "stop": reason-or-None}.
    Changes nothing unless apply=True AND the confirm phrase AND approved_by."""
    from app.models.models import AuditLogEntry, Platform, User
    from app.models.sales_models import (BrandSalesOrg, Membership, ROLE_SALES_MANAGER,
                                         ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG)

    out = {"ok": False, "applied": False, "steps": [], "stop": None}
    plats = db.query(Platform).filter(Platform.slug == platform_slug).all()
    if len(plats) != 1:
        out["stop"] = "expected exactly one '%s' platform, found %d" % (platform_slug, len(plats))
        return out
    plat = plats[0]
    team = db.query(BrandSalesOrg).filter(BrandSalesOrg.platform_id == plat.id).first()
    out["steps"].append("sales team: %s" % ("exists (%s) - reused" % team.name if team
                                             else "would create '%s'" % team_name))

    people = []
    for email, role in ([(e, ROLE_SALES_MANAGER) for e in managers] + [(e, ROLE_SALES_REP) for e in reps]):
        email = (email or "").strip().lower()
        if not email:
            continue
        users = db.query(User).filter(User.email == email).all()
        if len(users) != 1:
            out["stop"] = "expected exactly one login for %s, found %d" % (email, len(users))
            return out
        u = users[0]
        if u.role == "god_admin":
            out["stop"] = "refusing to give a sales seat to a god_admin account (%s)" % email
            return out
        row = None
        if team is not None:
            row = (db.query(Membership).filter(Membership.user_id == u.id,
                                               Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                                               Membership.scope_id == team.id).first())
        if row is not None and row.is_active and row.role == role:
            out["steps"].append("%s: already %s - nothing to do" % (email, role))
        else:
            out["steps"].append("%s: would %s %s" % (email, "re-activate as" if row else "add as", role))
        people.append((u, role, row))

    if not apply:
        out["ok"] = True
        out["steps"].append("DRY RUN - nothing changed.")
        return out
    if confirm != PHRASE or not (approved_by or "").strip():
        out["stop"] = 'needs approved_by and confirm "%s"' % PHRASE
        return out

    owner = db.query(User).filter(User.role == "god_admin", User.is_active.is_(True)).first()
    actor_id = getattr(owner, "id", None) or (people[0][0].id if people else None)
    if actor_id is None:
        out["stop"] = "no active owner account to record the change against"
        return out
    audit = {"approved_by": approved_by.strip(), "source": "scripts/setup_bookaboost_sales_org.py"}
    if team is None:
        slug, n = "%s-sales" % platform_slug, 2
        while db.query(BrandSalesOrg).filter(BrandSalesOrg.slug == slug).first():
            slug, n = "%s-sales-%d" % (platform_slug, n), n + 1
        team = BrandSalesOrg(platform_id=plat.id, name=team_name, slug=slug, timezone="America/Chicago")
        db.add(team)
        db.flush()
        db.add(AuditLogEntry(organization_id=None, actor_user_id=actor_id, platform_id=plat.id,
                             brand_sales_org_id=team.id,
                             action="platform.brand_sales_org_created", target_type="brand_sales_org",
                             target_id=team.id, details=json.dumps(dict(audit, name=team.name))))
    for u, role, row in people:
        if row is not None and row.is_active and row.role == role:
            continue
        if row is None:
            row = Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=team.id,
                             role=role, is_active=True)
            db.add(row)
        else:
            row.role, row.is_active = role, True
        db.flush()
        db.add(AuditLogEntry(organization_id=None, actor_user_id=actor_id, platform_id=plat.id,
                             brand_sales_org_id=team.id, action="sales_membership_granted", target_type="user", target_id=u.id,
                             details=json.dumps(dict(audit, role=role, brand_sales_org_id=team.id))))
    db.commit()
    out.update(ok=True, applied=True, brand_sales_org_id=team.id)
    out["steps"].append("DONE")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manager", action="append", default=[])
    ap.add_argument("--rep", action="append", default=[])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--approved-by", default="")
    ap.add_argument("--confirm", default="")
    a = ap.parse_args(argv)

    from app.deps import SessionLocal
    import app.models.registry  # noqa: F401  (every table mapped)
    db = SessionLocal()
    try:
        r = plan_and_apply(db, managers=a.manager, reps=a.rep, apply=a.apply,
                           approved_by=a.approved_by, confirm=a.confirm)
        for line in r["steps"]:
            print(line)
        if r["stop"]:
            print("STOP: %s" % r["stop"])
            return 2
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
