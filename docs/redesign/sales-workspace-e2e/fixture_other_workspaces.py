"""Local synthetic data for the Other Workspaces check (#300). Run AFTER
run_local.py has seeded its database, with the same DATABASE_URL.

  rep@example.test       sales rep, no other workspace        -> no entry
  manager@example.test   sales manager + 1 customer workspace -> "1 other business"
  multi@example.test     sales manager + 6 customer workspaces + Executive Suite
  owner@example.test     god_admin                             -> no entry (God Mode return bar)

Synthetic names only. Nothing is sent anywhere.
"""
import os, sys
sys.path.insert(0, os.environ.get("APP_ROOT", os.getcwd()))
from app.deps import SessionLocal
from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Membership
from app.services.auth_service import hash_password

db = SessionLocal()
plat = db.query(Platform).first()
brand = db.query(BrandSalesOrg).first()


def org(name, slug, industry):
    o = db.query(Organization).filter(Organization.slug == slug).first()
    if not o:
        o = Organization(name=name, slug=slug, industry=industry, is_active=True)
        db.add(o); db.commit()
    return o


def member(user, scope_type, scope_id, role):
    db.add(Membership(user_id=user.id, scope_type=scope_type, scope_id=scope_id, role=role, is_active=True))
    db.commit()


def user(email, name, role="advisor"):
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(organization_id=None, email=email, password_hash=hash_password("LocalTest!1"),
                 full_name=name, role=role, must_change_password=False)
        db.add(u); db.commit()
    return u


orgs = [org("Testco Light & Power", "testco-power", "energy"),
        org("Testco Roofing", "testco-roofing", "roofing"),
        org("Testco Memorial Services", "testco-memorial", "funeral"),
        org("Testco Insurance Agency", "testco-insurance", "insurance"),
        org("Testco Holdings", "testco-holdings", ""),
        org("Testco Realty", "testco-realty", "real_estate")]

mgr = db.query(User).filter(User.email == "manager@example.test").first()
member(mgr, "customer_org", orgs[0].id, "org_admin")

multi = user("multi@example.test", "Jordan Multiworkspace")
member(multi, "brand_sales_org", brand.id, "sales_manager")
for o in orgs:
    member(multi, "customer_org", o.id, "org_admin")
member(multi, "platform", plat.id, "brand_executive")

user("owner@example.test", "Owner Testgod", role="god_admin")
print("fixture ok:", len(orgs), "workspaces")
