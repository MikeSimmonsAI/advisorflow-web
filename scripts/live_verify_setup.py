"""LOCAL LIVE-VERIFICATION FIXTURE — a real database, real services, safe identities.

Not a test. This builds the smallest real world the Demo Suite, Manage Access
and Training screens need in order to be CLICKED: two brands, a sales
organization, a real customer, and four people whose access differs in exactly
the ways the screens are supposed to distinguish.

Every identity here is fictional and every address is on a reserved domain, so
nothing this creates can reach a real person. Run against a local SQLite file,
never against production.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///./_liveverify.db")
os.environ.setdefault("JWT_SECRET", "live-verify-secret-not-for-production-32!")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from app.deps import SessionLocal, engine                      # noqa: E402
from app.models.models import Base, Organization, Platform, User  # noqa: E402
import app.models.registry                                     # noqa: E402  (side effects)
from app.models.sales_models import (BrandSalesOrg, Membership,  # noqa: E402
                                     ROLE_SALES_MANAGER,
                                     SCOPE_BRAND_SALES_ORG,
                                     SCOPE_CUSTOMER_ORG)
from app.services import capabilities, training_service         # noqa: E402
from app.services import demo_environment as denv               # noqa: E402
from app.services.auth_service import hash_password             # noqa: E402

PASSWORD = "LiveVerify!2026"

Base.metadata.create_all(bind=engine)
db = SessionLocal()


def user(email, name, role="advisor", org=None):
    u = db.query(User).filter(User.email == email).first()
    if u is None:
        u = User(email=email, full_name=name, role=role,
                 organization_id=org, password_hash=hash_password(PASSWORD),
                 must_change_password=False, is_active=True)
        db.add(u)
        db.commit()
    return u


def platform(name, slug):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p is None:
        p = Platform(name=name, slug=slug)
        db.add(p)
        db.commit()
    return p


evo = platform("EvoSys Pro", "evosyspro")
boost = platform("BookaBoost", "bookaboost")

sales = db.query(BrandSalesOrg).filter(
    BrandSalesOrg.slug == "evosyspro-sales").first()
if sales is None:
    sales = BrandSalesOrg(platform_id=evo.id, name="EvoSys Pro Sales",
                          slug="evosyspro-sales")
    db.add(sales)
    db.commit()

customer = db.query(Organization).filter(
    Organization.slug == "northgate-memorial").first()
if customer is None:
    customer = Organization(name="Northgate Memorial", slug="northgate-memorial",
                            plan="standard", platform_id=evo.id, is_active=True)
    db.add(customer)
    db.commit()

god = user("owner@example.invalid", "Platform Owner", role="god_admin")

# The person provisioned into the wrong brand — the case Manage Access exists
# for. Deliberately left wrong so the screen has something real to correct.
wrong = user("c.torres@example.invalid", "Christina Torres",
             role="org_admin", org=customer.id)
if not db.query(Membership).filter(
        Membership.user_id == wrong.id,
        Membership.scope_type == SCOPE_CUSTOMER_ORG).first():
    db.add(Membership(user_id=wrong.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=customer.id, role="org_admin", is_active=True))
    db.commit()

# A salesperson who WILL be given Demo Suite access, so the presenter surface
# can be exercised as somebody who is not the owner.
rep = user("j.pike@example.invalid", "Jordan Pike")
if not db.query(Membership).filter(
        Membership.user_id == rep.id,
        Membership.scope_type == SCOPE_BRAND_SALES_ORG).first():
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=sales.id, role=ROLE_SALES_MANAGER,
                      is_active=True))
    db.commit()
capabilities.set_platform_grants(db, rep, evo.id, evo.name, god,
                                 ["demo_suite"], commit=True)
training_service.assign(db, user=rep, path_key="running_a_demo", actor=god)

# Somebody with NO demo access at all, to prove the refusal is real.
user("nobody@example.invalid", "No Access")

denv.build(db, evo.id, actor=god)

print("READY")
print("  owner        owner@example.invalid    god_admin")
print("  wrong        c.torres@example.invalid org_admin in Northgate Memorial")
print("  presenter    j.pike@example.invalid   demo_suite on EvoSys Pro")
print("  nobody       nobody@example.invalid   no access")
print("  password     %s" % PASSWORD)
print("  evo platform %s" % evo.id)
print("  boost        %s" % boost.id)
print("  christina    %s" % wrong.id)
db.close()
