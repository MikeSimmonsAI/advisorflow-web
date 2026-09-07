"""THE GOD MODE PATH TO OPERATIONAL COMPENSATION, AND WHAT IT MUST NOT COST.

WHY THIS FILE EXISTS

The Compensation Command Center shipped and was invisible. Its nav entry went
into the SALES workspace rail; the God Mode rail got "Pricing & Comp" — which
DEFINES the rules — and nothing pointing at the money those rules produced. An
owner looking at God Mode had no way to know operational compensation existed
short of typing a /sales URL.

So this file asserts two things that a screenshot cannot:

  1. THE TWO SURFACES STAY SEPARATE. Pricing & Comp configures; Sales
     Compensation operates. Merging them to save a rail item would put a
     payment run behind a screen called "Pricing".
  2. THE GOD ROUTE CHANGES NOTHING ABOUT AUTHORITY. Rendering the same screen
     inside the God shell must not turn a capability-gated API into a god-only
     one — a brand finance user with no sales membership must still reach the
     same data through the same endpoints.

The frontend nav itself is asserted against the SOURCE that produces the
bundle, because a rail item that exists only in a component nobody registered a
route for is exactly the failure being fixed.
"""

import itertools
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, PAYEE_SELLER,
                                            CompensationEntry,
                                            CompensationPlan, CompensationRule)
from app.models.models import (Organization, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG,
                                     BrandPackage, BrandSalesOrg, Membership,
                                     Opportunity)
from app.services import capabilities as caps
from app.services import compensation as comp
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "frontend", "src")


def _src(*parts):
    with open(os.path.join(_FRONTEND, *parts), encoding="utf-8") as fh:
        return fh.read()


# ═════════════════════════════════════════════════════════════════════════════
# The navigation itself
# ═════════════════════════════════════════════════════════════════════════════

def test_the_god_rail_offers_sales_compensation():
    """THE BUG THIS FIXES. The rail had Pricing & Comp and nothing else."""
    shell = _src("pages", "GodShell.jsx")
    assert "'Sales Compensation'" in shell
    assert "'/god/compensation'" in shell


def test_pricing_and_comp_survives_as_its_own_entry():
    """Two jobs, two entries. Not merged to save a line."""
    shell = _src("pages", "GodShell.jsx")
    assert "'Pricing & Comp'" in shell
    assert "'/god/pricing'" in shell


def test_both_entries_sit_in_the_platform_group():
    """A payment run does not belong under OPERATIONS beside Lead Scraper."""
    shell = _src("pages", "GodShell.jsx")
    platform_block = shell.split("{ group: 'PLATFORM' }", 1)[1]
    assert "'Sales Compensation'" in platform_block
    assert "'Pricing & Comp'" in platform_block


def test_the_god_route_is_registered_and_renders_the_same_component():
    """A rail item pointing at an unregistered route would fall through to the
    /god/* catch-all and silently render the Command Center home instead."""
    app_src = _src("App.jsx")
    assert re.search(r'path="/god/compensation"', app_src)
    assert "CompensationCommand embedded" in app_src
    # The SAME component the sales workspace serves — not a copy.
    assert "import CompensationCommand from './pages/sales/CompensationCommand'" in app_src


def test_the_god_route_is_registered_before_the_god_catch_all():
    """React Router ranks static segments above the wildcard, but a route
    written after the catch-all reads as dead code to the next person."""
    app_src = _src("App.jsx")
    assert (app_src.index('path="/god/compensation"')
            < app_src.index('path="/god/*"'))


def test_the_sales_route_still_exists_for_a_non_god_finance_user():
    """GodRoute refuses a brand finance user, and correctly so. Removing the
    /sales route would leave them with no way in at all."""
    app_src = _src("App.jsx")
    assert re.search(r'path="/sales/compensation"', app_src)
    assert re.search(r'path="/sales/my-compensation"', app_src)


def test_the_command_center_does_not_depend_on_the_sales_shell():
    """The regression that would silently lock finance out again."""
    screen = _src("pages", "sales", "CompensationCommand.jsx")
    assert "import SalesShell" not in screen
    assert "<SalesShell" not in screen


def test_my_compensation_still_uses_the_sales_shell():
    """It is genuinely a salesperson's screen; they always have a membership."""
    screen = _src("pages", "sales", "MyCompensation.jsx")
    assert "SalesShell" in screen


def test_no_totals_are_computed_in_the_command_center():
    """Every figure arrives from the server. A rate or a holdback in this file
    would be a second answer to what somebody earns."""
    screen = _src("pages", "sales", "CompensationCommand.jsx")
    for forbidden in ("holdback_days", "* 0.1", "rate_percent /"):
        assert forbidden not in screen


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures for the authorization half
# ═════════════════════════════════════════════════════════════════════════════

def _user(db, name, role="advisor", org_id=None):
    u = User(organization_id=org_id, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _brand(db, label):
    plat = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(plat); db.commit()
    org = BrandSalesOrg(platform_id=plat.id, name=label + " Sales",
                        slug="%s-s-%d" % (label.lower(), next(_SEQ)))
    db.add(org); db.commit()
    pkg = BrandPackage(platform_id=plat.id, name="Starter",
                       key="starter-%d" % next(_SEQ),
                       price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13, currency="USD")
    db.add(pkg); db.commit()
    manager = _user(db, label + " Manager")
    db.add(Membership(user_id=manager.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=org.id, role=ROLE_SALES_MANAGER, is_active=True))
    db.commit()
    rep = _user(db, label + " Rep")
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=org.id, role=ROLE_SALES_REP, is_active=True,
                      reports_to_user_id=manager.id))
    db.commit()
    plan = CompensationPlan(brand_sales_org_id=org.id, name=label + " Plan",
                            effective_from=datetime(2026, 1, 1).date(),
                            holdback_days=14, max_override_levels=1,
                            is_active=True)
    db.add(plan); db.commit()
    db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                            payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                            amount=Decimal("500.00"), sort_order=1))
    db.commit()
    return dict(platform=plat, org=org, pkg=pkg, manager=manager, rep=rep)


@pytest.fixture()
def a(db_session):
    return _brand(db_session, "Alpha")


@pytest.fixture()
def b(db_session):
    return _brand(db_session, "Beta")


@pytest.fixture()
def god(db_session):
    return _user(db_session, "Owner", role="god_admin")


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _earned(db, brand, days_ago=20):
    opp = Opportunity(brand_sales_org_id=brand["org"].id,
                      owner_user_id=brand["rep"].id,
                      company_name="Deal %d" % next(_SEQ),
                      selected_package_id=brand["pkg"].id,
                      stage="closing", status="won",
                      billing_option="term_agreement", contract_term_months=13)
    db.add(opp); db.commit()
    comp.earn(db, opp, collection_reference="inv-%d" % next(_SEQ),
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=days_ago))
    return opp


# ═════════════════════════════════════════════════════════════════════════════
# Linking it from God Mode changed no authority
# ═════════════════════════════════════════════════════════════════════════════

def test_the_endpoints_did_not_become_god_only(client, db_session, a, god):
    """THE POINT. The screen is now reachable from the God rail; the API behind
    it is still capability-gated, not owner-only."""
    finance = _user(db_session, "Finance")
    caps.set_brand_grants(db_session, finance, a["org"].id, "Alpha", god,
                          ["sales_comp_view"])
    db_session.commit()
    _earned(db_session, a)

    assert client.get("/sales/compensation/overview",
                      headers=_h(db_session, finance)).status_code == 200


def test_a_finance_user_with_no_sales_membership_still_reaches_the_data(
        client, db_session, a, god):
    from app.services.sales_access import is_sales_member
    finance = _user(db_session, "Finance")
    caps.set_brand_grants(db_session, finance, a["org"].id, "Alpha", god,
                          ["sales_comp_view"])
    db_session.commit()
    _earned(db_session, a)

    assert is_sales_member(finance, db_session, a["org"].id) is False
    d = client.get("/sales/compensation/ledger",
                   headers=_h(db_session, finance)).json()
    assert d["count"] >= 1
    assert d["total"] > 0


def test_a_customer_workspace_admin_cannot_reach_the_command_center(
        client, db_session, a):
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    admin = _user(db_session, "Customer Admin", role="org_admin", org_id=org.id)
    _earned(db_session, a)

    for path in ("/sales/compensation/overview", "/sales/compensation/ledger",
                 "/sales/compensation/payables"):
        assert client.get(path, headers=_h(db_session, admin)).status_code == 403


def test_a_sales_manager_keeps_visibility_but_not_settlement(
        client, db_session, a):
    _earned(db_session, a)
    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).first()
    h = _h(db_session, a["manager"])

    assert client.get("/sales/compensation/overview", headers=h).status_code == 200
    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [entry.id], "payment_reference": "ACH-1"},
                    headers=h)
    assert r.status_code == 403


def test_cross_brand_access_still_fails_server_side(client, db_session, a, b, god):
    finance = _user(db_session, "Alpha Finance")
    caps.set_brand_grants(db_session, finance, a["org"].id, "Alpha", god,
                          ["sales_comp_view", "sales_comp_manage"])
    db_session.commit()
    _earned(db_session, b)

    r = client.get("/sales/compensation/ledger?brand_sales_org_id=" + b["org"].id,
                   headers=_h(db_session, finance))
    assert r.status_code == 403


def test_my_compensation_stays_scoped_to_the_caller(client, db_session, a):
    _earned(db_session, a)
    d = client.get("/sales/compensation/me",
                   headers=_h(db_session, a["rep"])).json()
    assert d["payee_user_id"] == a["rep"].id
    assert {e["payee_user_id"] for e in d["entries"]} == {a["rep"].id}


def test_god_authority_did_not_regress(client, db_session, a, b, god):
    _earned(db_session, a)
    _earned(db_session, b)
    h = _h(db_session, god)
    assert client.get("/sales/compensation/overview", headers=h).status_code == 200
    assert client.get("/sales/compensation/payables",
                      headers=h).json()["can_process_payments"] is True


# ═════════════════════════════════════════════════════════════════════════════
# The migration health check — the thing that was previously unverifiable
# ═════════════════════════════════════════════════════════════════════════════

def test_scope_health_reports_healthy_when_grants_survived(
        client, db_session, a, god):
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    admin = _user(db_session, "Customer Admin", role="org_admin", org_id=org.id)
    db_session.add(UserCapabilityGrant(
        user_id=admin.id, organization_id=org.id, scope_id=org.id,
        capability="twilio_credentials", is_active=True))
    db_session.commit()
    caps.set_brand_grants(db_session, _user(db_session, "Finance"),
                          a["org"].id, "Alpha", god, ["sales_comp_view"])
    db_session.commit()

    d = client.get("/god/pricing/capability-scope-health",
                   headers=_h(db_session, god)).json()
    assert d["healthy"] is True
    assert d["unscoped_customer_grants"] == 0
    assert d["brand_grants_carrying_an_organization"] == 0
    assert d["active_customer_grants_sampled"] == 1
    assert d["active_customer_grants_still_resolving"] == 1
    assert d["grants_by_scope"][SCOPE_CUSTOMER_ORG] == 1
    assert d["grants_by_scope"][SCOPE_BRAND_SALES_ORG] == 1


def test_scope_health_catches_a_grant_the_backfill_missed(
        client, db_session, a, god):
    """THE FAILURE IT EXISTS TO FIND. A customer-org grant with a NULL scope_id
    is one the backfill did not reach, and its holder is about to start being
    refused for no visible reason."""
    from sqlalchemy import text
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    admin = _user(db_session, "Customer Admin", role="org_admin", org_id=org.id)
    grant = UserCapabilityGrant(user_id=admin.id, organization_id=org.id,
                                scope_id=org.id, capability="twilio_credentials",
                                is_active=True)
    db_session.add(grant); db_session.commit()
    db_session.execute(
        text("UPDATE user_capability_grants SET scope_id = NULL WHERE id = :i"),
        {"i": grant.id})
    db_session.commit()

    d = client.get("/god/pricing/capability-scope-health",
                   headers=_h(db_session, god)).json()
    assert d["healthy"] is False
    assert d["unscoped_customer_grants"] == 1
    assert "backfill" in d["explanation"]


def test_scope_health_is_god_only(client, db_session, a):
    for who in (a["manager"], a["rep"]):
        r = client.get("/god/pricing/capability-scope-health",
                       headers=_h(db_session, who))
        assert r.status_code == 403


def test_scope_health_writes_nothing(client, db_session, a, god):
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    admin = _user(db_session, "Customer Admin", role="org_admin", org_id=org.id)
    db_session.add(UserCapabilityGrant(
        user_id=admin.id, organization_id=org.id, scope_id=org.id,
        capability="twilio_credentials", is_active=True))
    db_session.commit()
    before = [(g.id, g.scope_type, g.scope_id, g.capability, g.is_active)
              for g in db_session.query(UserCapabilityGrant).all()]

    client.get("/god/pricing/capability-scope-health", headers=_h(db_session, god))

    db_session.expire_all()
    after = [(g.id, g.scope_type, g.scope_id, g.capability, g.is_active)
             for g in db_session.query(UserCapabilityGrant).all()]
    assert after == before
