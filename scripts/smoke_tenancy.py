"""
Regression tests for the non-tenant user model — the six Mike specified.

  1. Brand-sales user with organization_id = NULL can be a sales member.
  2. That user cannot create/mutate customer-tenant records without an
     explicitly authorized customer-organization context.
  3. Test lead records are excluded from production outreach.
  4. Creating a user without organization_id never falls back to the first org.
  5. A sales membership never implies customer-tenant membership.
  6. A test lead record never implies user tenancy.

FORCE sqlite. Never inherit a production DATABASE_URL.
"""
import os, sys

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("JWT_SECRET", "local" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "local" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from app.models.models import Base, User, Organization, Platform, Lead
import app.models.sales_models as sm
from app.services.tenancy import (
    has_tenant_context, tenant_org_id, assert_same_tenant, NoTenantContext,
)
from app.services.test_records import (
    is_test_record, exclude_test_records, is_outreach_eligible, blocked_reason,
)
from app.services.sales_access import is_sales_member, is_sales_manager, sales_org_ids

failures = []


def check(label, ok, detail=""):
    print("  %-62s %s%s" % (label, "OK" if ok else "FAIL", ("  " + detail) if detail and not ok else ""))
    if not ok:
        failures.append(label)


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()

# ── fixtures ────────────────────────────────────────────────────────────────
plat = Platform(id="plt-evosyspro", name="EvoSys Pro", slug="evosyspro")
db.add(plat); db.flush()

cust = Organization(id="org-cust", name="Greenland Cemetery", slug="greenland",
                    platform_id=plat.id)
db.add(cust); db.flush()

bso = sm.BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
db.add(bso); db.flush()

# Brand-sales user: NO customer tenant.
sales_user = User(id="u-sales", organization_id=None, email="rep@example.test",
                  full_name="Sales Rep", password_hash="x", role="advisor")
# Ordinary customer-tenant user.
tenant_user = User(id="u-tenant", organization_id=cust.id, email="adv@example.test",
                   full_name="Advisor", password_hash="x", role="advisor")
god_user = User(id="u-god", organization_id=None, email="god@example.test",
                full_name="Owner", password_hash="x", role="god_admin")
db.add_all([sales_user, tenant_user, god_user]); db.flush()

db.add(sm.Membership(user_id=sales_user.id, scope_type=sm.SCOPE_BRAND_SALES_ORG,
                     scope_id=bso.id, role=sm.ROLE_SALES_REP))
db.commit()

print("--- 1. brand-sales user with NULL org is a valid sales member ---")
check("user persisted with organization_id = NULL", sales_user.organization_id is None)
check("recognised as a sales member", is_sales_member(sales_user, db))
check("not a sales manager (rep only)", not is_sales_manager(sales_user, db))
check("sales_org_ids resolves their brand", sales_org_ids(sales_user, db) == [bso.id])

print("\n--- 2. NULL-org user cannot write customer-tenant records ---")
check("has_tenant_context is False", not has_tenant_context(sales_user))
try:
    tenant_org_id(sales_user)
    check("tenant_org_id raises for NULL-org user", False, "did not raise")
except NoTenantContext:
    check("tenant_org_id raises for NULL-org user", True)
except HTTPException:
    check("tenant_org_id raises for NULL-org user", True)
check("tenant_org_id returns the org for a real tenant user",
      tenant_org_id(tenant_user) == cust.id)
try:
    assert_same_tenant(sales_user, cust.id)
    check("assert_same_tenant blocks NULL-org user", False, "did not raise")
except HTTPException:
    check("assert_same_tenant blocks NULL-org user", True)
try:
    assert_same_tenant(god_user, cust.id)
    check("god_admin is exempt (operates across tenants)", True)
except HTTPException:
    check("god_admin is exempt (operates across tenants)", False)

print("\n--- 3. test leads are excluded from production outreach ---")
real_lead = Lead(id="l-real", organization_id=cust.id, first_name="Real",
                 last_name="Prospect", email="real@example.test", is_test=False)
test_lead = Lead(id="l-test", organization_id=cust.id, first_name="Michael",
                 last_name="Schlueter", email="staff@example.test", is_test=True,
                 test_note="internal tester")
dnc_lead = Lead(id="l-dnc", organization_id=cust.id, first_name="Opted",
                last_name="Out", email="dnc@example.test", is_test=False, status="dnc")
db.add_all([real_lead, test_lead, dnc_lead]); db.commit()

check("is_test_record detects the flag", is_test_record(test_lead))
check("real lead is outreach eligible", is_outreach_eligible(real_lead))
check("TEST lead is NOT outreach eligible", not is_outreach_eligible(test_lead))
check("DNC lead is NOT outreach eligible", not is_outreach_eligible(dnc_lead))
check("blocked_reason explains the test skip",
      "test record" in (blocked_reason(test_lead) or ""))

bulk = exclude_test_records(db.query(Lead).filter(Lead.organization_id == cust.id))
ids = {l.id for l in bulk.all()}
check("bulk outreach query excludes the test lead", "l-test" not in ids)
check("bulk outreach query keeps real leads", "l-real" in ids)

print("\n--- 4. user creation never falls back to the first organization ---")
import inspect as _inspect
import app.routers.god_router as gr
src = _inspect.getsource(gr.god_create_user)
check("no 'first organization' fallback remains",
      "order_by(Organization.created_at).first()" not in src)
check("tenant roles require an explicit org", "TENANT_ROLES" in src)

print("\n--- 5. a sales membership never implies customer tenancy ---")
check("sales user still has no organization_id", sales_user.organization_id is None)
mem = db.query(sm.Membership).filter(sm.Membership.user_id == sales_user.id).one()
check("membership scope is brand_sales_org, not customer_org",
      mem.scope_type == sm.SCOPE_BRAND_SALES_ORG)
check("membership does not point at a customer org", mem.scope_id != cust.id)
check("brand sales org is not an organizations row",
      db.query(Organization).filter(Organization.id == bso.id).first() is None)

print("\n--- 6. a test lead record never implies user tenancy ---")
staff_user = User(id="u-staff", organization_id=None, email="staff@example.test",
                  full_name="Michael Schlueter", password_hash="x", role="advisor")
db.add(staff_user); db.commit()
check("same email as a lead in a customer org, yet user org is NULL",
      staff_user.organization_id is None)
check("the lead lives in the customer org", test_lead.organization_id == cust.id)
check("lead tenancy and user tenancy are independent",
      test_lead.organization_id != staff_user.organization_id)

print("\n--- schema: users.organization_id is nullable ---")
col = [c for c in inspect(engine).get_columns("users") if c["name"] == "organization_id"][0]
check("users.organization_id nullable", col["nullable"])
lead_cols = {c["name"] for c in inspect(engine).get_columns("leads")}
check("leads.is_test exists", "is_test" in lead_cols)

db.close()
print()
if failures:
    sys.exit("TENANCY REGRESSION FAILURES: " + ", ".join(failures))
print("ALL TENANCY REGRESSION TESTS PASSED")
