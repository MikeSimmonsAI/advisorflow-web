"""LOCAL ONLY - mirrors the production SHAPE of Program Access for testing.

Run after run_local.py has seeded its throwaway SQLite database, with the same
DATABASE_URL. Business names match the real launcher so screenshots read the
same; every login is @example.test except the wholesale@ account it mirrors,
every phone is 555-01xx, and nothing here can reach a real database or person.

Accounts (password LocalTest!1):
  A wholesale@evosyspro.live  home EVO; sales manager; org_admin x6; Wholesale seat
  B rep@example.test          sales rep only (from run_local.py)
  C multi@example.test        sales manager + 3 customer workspaces
  D clean@example.test        one workspace only: Commercial Cleaning Blueprint
  E exec@example.test         Executive Suite + 2 workspaces
  F owner@example.test        god_admin
"""
import json
import os
import secrets
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.environ.get("APP_ROOT", os.getcwd()))
from app.deps import SessionLocal  # noqa: E402
import app.models.registry  # noqa: E402,F401
from app.models.demo_site_models import DemoSite  # noqa: E402
from app.models.models import Organization, Platform, User  # noqa: E402
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402

db = SessionLocal()
plat = db.query(Platform).first()
brand = db.query(BrandSalesOrg).first()
rep = db.query(User).filter(User.email == "rep@example.test").first()


def org(name, slug, industry, feats=None):
    o = Organization(name=name, slug=slug, plan="trial", industry=industry, is_active=True,
                     platform_id=plat.id, enabled_features=json.dumps(feats) if feats else None)
    db.add(o)
    db.commit()
    return o


def user(email, name, role="advisor", home=None):
    u = User(organization_id=home.id if home else None, email=email, password_hash=hash_password("LocalTest!1"),
             full_name=name, role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def m(u, st, sid, role):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


evo = org("EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", "generic",
          ["leads", "reports", "users", "wholesale_real_estate"])
sci = org("Service Corporation International", "service-corporation-international", "funeral",
          ["leads", "reports", "users", "sms", "email", "cadences"])
from app.services.programs import setup as PS  # noqa: E402
PS.ensure_program(db, sci, name="SCI Family Service Center")
db.commit()
atl = org("Atlantis Light & Power", "atlantis-light-power", "energy", ["leads", "reports", "users"])
roof = org("EvoSys Pro Roofing", "evosys-pro-roofing", "roofing", ["leads", "users", "roofing_program"])
rest = org("Restland Cemetery and Funeral Home", "restland", "funeral", ["leads", "reports", "users"])
ins = org("Max Life Demo Agency (DEMO)", "max-life-demo-agency", "insurance", ["leads", "insurance_agency", "users"])
ccb = org("Commercial Cleaning Blueprint", "commercial-cleaning-blueprint", "cleaning", ["leads", "reports", "users"])

# A: the operator account (same shape as production)
ws = user("wholesale@evosyspro.live", "EvoSys Wholesale", role="org_admin", home=evo)
m(ws, "brand_sales_org", brand.id, "sales_manager")
for o in (atl, sci, evo, ins, rest, roof):
    m(ws, "customer_org", o.id, "org_admin")
m(ws, "product", "wholesale", "member")

# C: multi-company manager
multi = user("multi@example.test", "Jordan Multicompany")
m(multi, "brand_sales_org", brand.id, "sales_manager")
for o in (atl, rest, sci):
    m(multi, "customer_org", o.id, "org_admin")

# D: one client workspace only
clean = user("clean@example.test", "Dana Cleanowner", role="org_admin", home=ccb)
m(clean, "customer_org", ccb.id, "org_admin")

# E: executive
ex = user("exec@example.test", "Avery Executive")
m(ex, "platform", plat.id, "brand_executive")
for o in (atl, roof):
    m(ex, "customer_org", o.id, "org_admin")

# F: owner
user("owner@example.test", "Owner Testgod", role="god_admin")


def deal(company, contact, stage, demos, proposal=None, industry=None):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=rep.id, company_name=company,
                    contact_name=contact, stage=stage, status="open", proposal_status=proposal,
                    industry=industry, phone="(214) 555-0150", email="deal@example.test")
    db.add(o)
    db.commit()
    for slot in demos:
        db.add(DemoSite(opportunity_id=o.id, brand_sales_org_id=brand.id, slot=slot,
                        title="%s %s" % (company, slot), token=secrets.token_urlsafe(32), is_active=True,
                        expires_at=datetime.utcnow() + timedelta(days=60),
                        html="<!doctype html><title>%s</title><body style='font-family:sans-serif;padding:40px'>"
                             "<h1>%s</h1><p>LOCAL TEST PAGE - %s demo placeholder.</p></body>" % (company, company, slot)))
    db.commit()
    return o


deal("Commercial Cleaning Blueprint — Daniel Almaguer", "Daniel Almaguer", "demo_build",
     ["platform", "website", "client_portal"], industry="cleaning")
deal("Countryside Land Partners", "Samuel St.Germain", "demo_proposal", ["platform", "website"], proposal="sent")
deal("VP Wraps Vehicle Protection", None, "demo_build", ["platform"])
deal("Building Equity Investments LLC", None, "prospect", ["platform", "website"], proposal="sent")
deal("Atlantis Light & Power", None, "onboarding", ["platform", "website"])   # becoming a customer: no preview
# Isolation markers: one lead per company, named after it. A login must only
# ever see the markers of companies it belongs to.
from app.models.models import Lead  # noqa: E402
for o in (atl, sci, roof, rest, ins, ccb, evo):
    db.add(Lead(organization_id=o.id, first_name="Marker", last_name=o.slug, phone="(214) 555-0199",
                email="marker-%s@example.test" % o.slug))
db.commit()
print("fixture ok")
