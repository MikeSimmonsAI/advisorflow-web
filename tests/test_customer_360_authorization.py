"""WHO MAY OPEN A CUSTOMER'S RECORD, AND WHO MAY END THEIR RELATIONSHIP WITH US.

Customer 360 carries one customer's commercial terms, the identity of the person
who sold them, and platform payroll. Every one of those is a cross-tenant
disclosure in the wrong hands, and the wrong hands include a customer's own
administrator — the person most likely to try the URL.

The lifecycle transitions are worse than a disclosure: they are the only
operations in this system that can close a customer's doors. So they are
god_admin and nothing else, enforced server-side, and asserted here against a
rep, a sales manager, a super_admin and a customer's own org_admin.

A 403 is not enough on its own. Every refusal below also asserts that NOTHING
CHANGED — an endpoint that rejects the caller after writing the row has not
refused anything.
"""

import itertools
from datetime import datetime
from decimal import Decimal

import pytest

from app.models.customer_lifecycle_models import (CUST_ACTIVE,
                                                  CustomerLifecycleEvent)
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership, Opportunity)
from app.services import customer_360 as c360
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture()
def world(db_session):
    plat = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    db_session.add(plat); db_session.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales",
                          slug="evo-s-%d" % next(_SEQ))
    db_session.add(brand); db_session.commit()

    pkg = BrandPackage(platform_id=plat.id, name="Starter",
                       key="starter-%d" % next(_SEQ),
                       price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13, currency="USD")
    db_session.add(pkg); db_session.commit()

    org = Organization(name="Acme Memorial", slug="acme-%d" % next(_SEQ),
                       platform_id=plat.id, plan="standard", is_active=True)
    db_session.add(org); db_session.commit()

    def user(role, name, org_id=None):
        u = User(organization_id=org_id,
                 email="u%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("x"), full_name=name, role=role,
                 must_change_password=False)
        db_session.add(u); db_session.commit()
        return u

    def sales(role, name):
        u = user("advisor", name)
        db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                                  scope_id=brand.id, role=role, is_active=True))
        db_session.commit()
        return u

    rep = sales(ROLE_SALES_REP, "Rep")
    manager = sales(ROLE_SALES_MANAGER, "Manager")

    opp = Opportunity(brand_sales_org_id=brand.id, owner_user_id=rep.id,
                      company_name="Acme Memorial", selected_package_id=pkg.id,
                      stage="closing", status="won",
                      billing_option="term_agreement", contract_term_months=13)
    db_session.add(opp); db_session.commit()

    impl = Implementation(opportunity_id=opp.id, organization_id=org.id,
                          platform_id=plat.id, brand_sales_org_id=brand.id,
                          package_id=pkg.id, sold_by_user_id=rep.id,
                          status="live",
                          launched_at=datetime(2026, 7, 15, 9, 0),
                          implementation_fee=Decimal("1497.00"),
                          recurring_amount=Decimal("500.00"),
                          billing_option="term_agreement",
                          contract_term_months=13, currency="USD")
    db_session.add(impl); db_session.commit()

    return dict(
        plat=plat, brand=brand, org=org, opp=opp, impl=impl,
        rep=rep, manager=manager,
        god=user("god_admin", "Owner"),
        super_admin=user("super_admin", "Super"),
        # The customer's OWN administrator. The most important negative case on
        # this page: they are legitimately an admin, of their own tenant, and
        # none of this belongs to them.
        customer_admin=user("admin", "Customer Admin", org_id=org.id),
    )


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


OUTSIDERS = ["rep", "manager", "super_admin", "customer_admin"]


# ═════════════════════════════════════════════════════════════════════════════
# 14-15. Reading
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("who", OUTSIDERS)
def test_nobody_below_god_may_list_customers(client, db_session, world, who):
    r = client.get("/god/customer-360/customers",
                   headers=_h(db_session, world[who]))
    assert r.status_code == 403


@pytest.mark.parametrize("who", OUTSIDERS)
def test_nobody_below_god_may_open_a_customer_record(client, db_session, world, who):
    r = client.get("/god/customer-360/customers/%s" % world["org"].id,
                   headers=_h(db_session, world[who]))
    assert r.status_code == 403


def test_a_customer_admin_cannot_reach_their_own_organizations_record(
        client, db_session, world):
    """Their own tenant, and still refused.

    The record carries what they were sold for, who sold it and what it paid in
    commission. Being an admin OF a customer is not authority OVER the
    platform's view of that customer.
    """
    r = client.get("/god/customer-360/customers/%s" % world["org"].id,
                   headers=_h(db_session, world["customer_admin"]))
    assert r.status_code == 403


def test_god_may_read_and_gets_the_commercial_picture(client, db_session, world):
    r = client.get("/god/customer-360/customers/%s" % world["org"].id,
                   headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Acme Memorial"
    assert d["commercials"]["total_contract_value"] == 7997.0
    assert d["source"] == c360.SOURCE_PIPELINE
    # Compensation is served to the platform owner, and to nobody else.
    assert "compensation" in d


# ═════════════════════════════════════════════════════════════════════════════
# 24. Nobody below god may cancel, archive or delete — and nothing changes
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("who", OUTSIDERS)
def test_nobody_below_god_may_request_a_cancellation(client, db_session, world, who):
    r = client.post("/god/customer-360/customers/%s/request-cancellation"
                    % world["org"].id,
                    json={"reason": "price"},
                    headers=_h(db_session, world[who]))
    assert r.status_code == 403
    db_session.refresh(world["org"])
    assert c360.status_of(world["org"]) == CUST_ACTIVE
    assert db_session.query(CustomerLifecycleEvent).count() == 0


@pytest.mark.parametrize("who", OUTSIDERS)
def test_nobody_below_god_may_complete_a_cancellation(client, db_session, world, who):
    r = client.post("/god/customer-360/customers/%s/complete-cancellation"
                    % world["org"].id, json={},
                    headers=_h(db_session, world[who]))
    assert r.status_code == 403
    db_session.refresh(world["org"])
    assert world["org"].is_active is True


@pytest.mark.parametrize("who", OUTSIDERS)
def test_nobody_below_god_may_archive(client, db_session, world, who):
    r = client.post("/god/customer-360/customers/%s/archive" % world["org"].id,
                    json={}, headers=_h(db_session, world[who]))
    assert r.status_code == 403
    db_session.refresh(world["org"])
    assert c360.status_of(world["org"]) == CUST_ACTIVE


@pytest.mark.parametrize("who", OUTSIDERS)
def test_nobody_below_god_may_permanently_delete(client, db_session, world, who):
    org_id = world["org"].id
    r = client.post("/god/customer-360/customers/%s/permanent-delete" % org_id,
                    json={"confirmation": "Acme Memorial"},
                    headers=_h(db_session, world[who]))
    assert r.status_code == 403
    assert db_session.query(Organization).filter(
        Organization.id == org_id).first() is not None


def test_an_unauthenticated_caller_reaches_nothing(client, world):
    for path in ("", "/%s" % world["org"].id,
                 "/%s/offboarding-preview" % world["org"].id):
        assert client.get("/god/customer-360/customers" + path).status_code in (401, 403)


# ═════════════════════════════════════════════════════════════════════════════
# The god path works, end to end, through HTTP
# ═════════════════════════════════════════════════════════════════════════════

def test_the_owner_can_walk_a_customer_through_the_whole_lifecycle(
        client, db_session, world):
    h = _h(db_session, world["god"])
    org_id = world["org"].id
    base = "/god/customer-360/customers/%s" % org_id

    prev = client.get(base + "/offboarding-preview", headers=h)
    assert prev.status_code == 200
    body = prev.json()
    # The honest checklist is part of the contract, not decoration.
    assert body["manual_steps"]
    assert any("Nothing is deleted" in s for s in body["will_not_happen"])

    r = client.post(base + "/request-cancellation",
                    json={"reason": "price", "note": "Budget cut"}, headers=h)
    assert r.status_code == 200
    assert r.json()["lifecycle_status"] == "cancellation_requested"
    # Access deliberately untouched at this stage.
    assert r.json()["workspace_active"] is True

    assert client.post(base + "/start-offboarding", json={}, headers=h
                       ).json()["lifecycle_status"] == "offboarding"

    done = client.post(base + "/complete-cancellation", json={}, headers=h).json()
    assert done["lifecycle_status"] == "cancelled"
    assert done["workspace_active"] is False

    # And the record survived all of it.
    assert done["opportunity"]["id"] == world["opp"].id
    assert done["commercials"]["total_contract_value"] == 7997.0
    assert len(done["lifecycle_history"]) == 3


def test_deletion_is_refused_for_a_customer_with_history_even_for_god(
        client, db_session, world):
    """The one refusal that has no override. God is not the missing permission —
    the orphaned financial records are the reason."""
    h = _h(db_session, world["god"])
    org_id = world["org"].id
    impact = client.get("/god/customer-360/customers/%s/deletion-impact" % org_id,
                        headers=h).json()
    assert impact["may_delete"] is False

    r = client.post("/god/customer-360/customers/%s/permanent-delete" % org_id,
                    json={"confirmation": "Acme Memorial"}, headers=h)
    assert r.status_code == 409
    assert db_session.query(Organization).filter(
        Organization.id == org_id).first() is not None


def test_archived_customers_are_hidden_by_default_and_returned_on_request(
        client, db_session, world):
    h = _h(db_session, world["god"])
    org_id = world["org"].id
    client.post("/god/customer-360/customers/%s/archive" % org_id,
                json={}, headers=h)

    default = client.get("/god/customer-360/customers", headers=h).json()
    assert org_id not in {c["organization_id"] for c in default["customers"]}

    shown = client.get("/god/customer-360/customers?include_archived=true",
                       headers=h).json()
    assert org_id in {c["organization_id"] for c in shown["customers"]}
    # Counted either way — archiving must not make churn invisible.
    assert default["status_counts"].get("archived") == 1
