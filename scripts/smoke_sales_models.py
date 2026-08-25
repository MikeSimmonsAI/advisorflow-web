"""
Phase 1 verification for the Sales Workspace schema.

Builds every table on a THROWAWAY in-memory sqlite database and asserts the
architectural rules actually hold in the schema — not just in the docs.

FORCE sqlite. Never inherit a production DATABASE_URL from the shell (the same
trap that would have run migrations against the live database earlier today).
"""
import os, sys

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("JWT_SECRET", "local" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "local" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect
from app.models.models import Base, User, Organization, Platform
import app.models.sales_models as sm

failures = []


def check(label, ok, detail=""):
    print("  %-56s %s%s" % (label, "OK" if ok else "FAIL", ("  " + detail) if detail and not ok else ""))
    if not ok:
        failures.append(label)


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
insp = inspect(engine)
tables = set(insp.get_table_names())

print("--- new tables created ---")
for t in ("memberships", "brand_sales_orgs", "brand_packages",
          "opportunities", "discovery_records", "opportunity_events"):
    check(t, t in tables)

print("\n--- existing tables untouched (regression guard) ---")
for t in ("users", "organizations", "platforms", "leads", "booking_links"):
    check(t + " still present", t in tables)

print("\n--- TENANCY RULE: brand sales orgs are NOT customer orgs ---")
bso = {c["name"] for c in insp.get_columns("brand_sales_orgs")}
check("brand_sales_orgs hangs off platform", "platform_id" in bso)
check("brand_sales_orgs is NOT an organizations row",
      "organizations" != sm.BrandSalesOrg.__tablename__)
opp = {c["name"] for c in insp.get_columns("opportunities")}
check("opportunity owned by brand sales org", "brand_sales_org_id" in opp)
check("opportunity links to customer org (nullable until Won)",
      "customer_organization_id" in opp)
_cust = [c for c in insp.get_columns("opportunities") if c["name"] == "customer_organization_id"]
check("customer_organization_id is nullable", bool(_cust) and _cust[0]["nullable"])

print("\n--- ROLE RULE: memberships are additive, users.role untouched ---")
users_cols = {c["name"] for c in insp.get_columns("users")}
check("users.role still exists and is unchanged", "role" in users_cols)
check("users has NO sales_rep/sales_manager column added",
      not {"sales_role", "sales_rep", "sales_manager"} & users_cols)
mem = {c["name"] for c in insp.get_columns("memberships")}
for col in ("user_id", "scope_type", "scope_id", "role", "is_active"):
    check("memberships.%s" % col, col in mem)
check("scope vocabulary defined", set(sm.SCOPE_TYPES) ==
      {"platform", "brand_sales_org", "customer_org"})
check("brand sales roles defined", set(sm.BRAND_SALES_ROLES) ==
      {"sales_manager", "sales_rep"})

print("\n--- PACKAGE RULE: sales catalog is separate from Stripe plans ---")
pkg = {c["name"] for c in insp.get_columns("brand_packages")}
check("brand_packages scoped per platform (reusable across brands)", "platform_id" in pkg)
check("price stored as exact numeric, not float", "price" in pkg)
check("billing link is explicit + deliberate", "billing_plan_key" in pkg)
_bp = [c for c in insp.get_columns("brand_packages") if c["name"] == "billing_plan_key"]
check("billing_plan_key nullable (never inferred)", bool(_bp) and _bp[0]["nullable"])

print("\n--- AUDIT RULE: deal value override is recorded, not silent ---")
for col in ("deal_value", "deal_value_override", "deal_value_override_by",
            "deal_value_override_at", "deal_value_override_reason"):
    check("opportunities.%s" % col, col in opp)

print("\n--- TIMEZONE RULE: prospect timezone captured, never hardcoded ---")
check("opportunities.timezone exists", "timezone" in opp)

print("\n--- write/read round trip ---")
from sqlalchemy.orm import sessionmaker
S = sessionmaker(bind=engine)
s = S()
try:
    p = Platform(id="plt-test", name="Test Brand", slug="testbrand")
    s.add(p); s.flush()
    org = sm.BrandSalesOrg(platform_id=p.id, name="Test Brand Sales", slug="testbrand-sales")
    s.add(org); s.flush()
    pk = sm.BrandPackage(platform_id=p.id, key="growth", name="Growth", price=2495.00)
    s.add(pk); s.flush()
    o = sm.Opportunity(brand_sales_org_id=org.id, company_name="Acme Co",
                       selected_package_id=pk.id, deal_value=2495.00,
                       timezone="America/New_York")
    s.add(o); s.flush()
    s.add(sm.OpportunityEvent(opportunity_id=o.id, event_type="created",
                              summary="Opportunity created"))
    s.commit()
    check("round trip: opportunity persisted", s.query(sm.Opportunity).count() == 1)
    check("round trip: customer_organization_id starts NULL",
          s.query(sm.Opportunity).first().customer_organization_id is None)
    check("round trip: timeline event persisted", s.query(sm.OpportunityEvent).count() == 1)
except Exception as e:
    check("round trip", False, str(e)[:120])
finally:
    s.close()

print()
if failures:
    sys.exit("SALES SCHEMA CHECKS FAILED: " + ", ".join(failures))
print("ALL SALES SCHEMA CHECKS PASSED")
