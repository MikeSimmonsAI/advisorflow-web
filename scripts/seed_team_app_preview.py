"""Seed SYNTHETIC people for a preview of the EvoSys Pro team app (/app).

NEVER WRITES TO A REAL DATABASE. It runs only when one of these holds:
  * DATABASE_URL is a local sqlite:/// file (a laptop preview), or
  * TEAM_APP_PREVIEW=1 AND the database holds no person outside @preview.test
    (a dedicated preview database, e.g. render-team-app-preview.yaml).
Anything else - production, SCI staging, any database with a real user - is
refused before a single row is written.

PASSWORD: PREVIEW_PASSWORD from the environment (required for a hosted
preview, never committed). A local SQLite preview falls back to Preview123!.

Safe to run on every start: it adds what is missing and changes nothing else.

The accounts mirror the LIVE assignments read on 2026-11-15 (roles only, no
real names or emails):
  owner@      god_admin - the only account that can change Team access
  platform@   org_admin; org admin of SCI + other workspaces; sales manager on
              the EvoSys Pro sales team; team grants: manage chat rooms,
              publish announcements, Team Time over every rep
  partner@    advisor role; sales manager + EvoSys Pro executive; org admin of
              SCI + Roofing; NO team-management grants (turn on in Team access)
  rep1@ rep2@ sales reps (Chat + Time)
  newrep@     sales rep with no workspace at all (Chat + Time still work)
  chatmgr@    manage chat rooms only
  timemgr@    Team Time over Rep One only
  outsider@   customer advisor - no Chat, no Time
"""
import os
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

URL = os.environ.get("DATABASE_URL", "")
LOCAL = URL.startswith("sqlite:///")
HOSTED = os.environ.get("TEAM_APP_PREVIEW") == "1"
DOMAIN = "@preview.test"

if not (LOCAL or HOSTED):
    sys.exit("Refusing: set DATABASE_URL to a local sqlite:/// file, or TEAM_APP_PREVIEW=1 "
             "for a dedicated preview database.")
if HOSTED and not LOCAL:
    name = (urlparse(URL).path or "").lstrip("/").lower()
    if not name or "prod" in name or name in ("advisorflow_414g", "sci_staging"):
        sys.exit("Refusing: %r does not look like a dedicated preview database." % name)

PASSWORD = os.environ.get("PREVIEW_PASSWORD") or ("Preview123!" if LOCAL else "")
if not PASSWORD or len(PASSWORD) < 12 and not LOCAL:
    sys.exit("Refusing: set PREVIEW_PASSWORD (12+ characters) for a hosted preview.")

import app.models.registry  # noqa: E402,F401
from app.deps import SessionLocal, engine  # noqa: E402
from app.models.models import Base, Organization, Platform, User, UserCapabilityGrant  # noqa: E402
from app.models.program_models import OutreachProgram  # noqa: E402
from app.models.sales_models import (BrandSalesOrg, Membership, ROLE_BRAND_EXECUTIVE,  # noqa: E402
                                     SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG, SCOPE_PLATFORM)
from app.models.team_app_models import TeamTimeScope  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402
from app.services.sms_programs import SCI_ORG_NAME  # noqa: E402


def _get(db, model, **kw):
    return db.query(model).filter_by(**kw).first()


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    real = (db.query(User.email).filter(~User.email.like("%" + DOMAIN)).first())
    if real:
        sys.exit("Refusing: this database already holds a person outside %s." % DOMAIN)

    p = _get(db, Platform, slug="evosyspro")
    if not p:
        p = Platform(name="EvoSys Pro", slug="evosyspro")
        db.add(p); db.flush()
    bso = _get(db, BrandSalesOrg, slug="evosys-pro-sales-preview")
    if not bso:
        bso = BrandSalesOrg(platform_id=p.id, name="EvoSys Pro Sales", slug="evosys-pro-sales-preview")
        db.add(bso); db.flush()

    def org(name, slug, industry):
        o = _get(db, Organization, slug=slug)
        if not o:
            o = Organization(name=name, slug=slug, plan="standard", industry=industry, platform_id=p.id)
            db.add(o); db.flush()
        return o

    sci = org(SCI_ORG_NAME, "sci-preview", "funeral")
    if not _get(db, OutreachProgram, organization_id=sci.id):
        db.add(OutreachProgram(organization_id=sci.id, name="SCI Family Outreach (preview)",
                               primary_contact_name="Preview Contact"))
    roofing = org("Preview Roofing Co", "roofing-preview", "roofing")
    funeral = org("Preview Funeral Home", "funeral-preview", "funeral")
    customer = org("Preview Customer Co", "customer-preview", "generic")

    def user(key, name, role="advisor", org_id=None):
        email = key + DOMAIN
        u = _get(db, User, email=email)
        if not u:
            u = User(email=email, full_name=name, role=role, password_hash=hash_password(PASSWORD),
                     must_change_password=False, organization_id=org_id)
            db.add(u); db.flush()
        return u

    def member(u, scope_type, scope_id, role):
        if not _get(db, Membership, user_id=u.id, scope_type=scope_type, scope_id=scope_id, role=role):
            db.add(Membership(user_id=u.id, scope_type=scope_type, scope_id=scope_id, role=role, is_active=True))

    def grant(u, key, by):
        if not _get(db, UserCapabilityGrant, user_id=u.id, scope_type=SCOPE_PLATFORM, scope_id=p.id, capability=key):
            db.add(UserCapabilityGrant(user_id=u.id, scope_type=SCOPE_PLATFORM, scope_id=p.id,
                                       organization_id=None, capability=key, is_active=True, granted_by=by.id))

    def scope(mgr, who, by):
        if not _get(db, TeamTimeScope, platform_id=p.id, manager_user_id=mgr.id, member_user_id=who.id):
            db.add(TeamTimeScope(platform_id=p.id, manager_user_id=mgr.id, member_user_id=who.id, granted_by=by.id))

    owner = user("owner", "Owner (preview)", role="god_admin")
    platform = user("platform", "Platform (preview)", role="org_admin", org_id=funeral.id)
    partner = user("partner", "Owner Partner (preview)")
    rep1 = user("rep1", "Rep One")
    rep2 = user("rep2", "Rep Two")
    newrep = user("newrep", "New Rep")
    chatmgr = user("chatmgr", "Chat Manager (preview)", org_id=customer.id)
    timemgr = user("timemgr", "Time Manager (preview)", org_id=customer.id)
    outsider = user("outsider", "Outside Advisor", org_id=customer.id)

    for o in (sci, roofing, funeral):
        member(platform, SCOPE_CUSTOMER_ORG, o.id, "org_admin")
    for o in (sci, roofing):
        member(partner, SCOPE_CUSTOMER_ORG, o.id, "org_admin")
    member(rep1, SCOPE_CUSTOMER_ORG, customer.id, "advisor")
    member(outsider, SCOPE_CUSTOMER_ORG, customer.id, "advisor")
    for u in (platform, partner):
        member(u, SCOPE_BRAND_SALES_ORG, bso.id, "sales_manager")
    for u in (rep1, rep2, newrep):
        member(u, SCOPE_BRAND_SALES_ORG, bso.id, "sales_rep")
    member(partner, SCOPE_PLATFORM, p.id, ROLE_BRAND_EXECUTIVE)

    for k in ("team_chat_manage", "team_announce", "team_time_manage"):
        grant(platform, k, owner)
    grant(chatmgr, "team_chat_manage", owner)
    grant(timemgr, "team_time_manage", owner)
    grant(timemgr, "team_chat", owner)
    for r in (rep1, rep2, newrep):
        scope(platform, r, owner)
    scope(timemgr, rep1, owner)
    db.commit()
    print("Preview people ready (synthetic, %s)." % DOMAIN)


if __name__ == "__main__":
    main()
