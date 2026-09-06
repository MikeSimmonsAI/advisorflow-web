"""THE ENDPOINT — server-side authority, and what a refusal must NOT do.

The risk this file exists for is not "does the maths work" (that is covered in
test_sales_compensation.py). It is that a below-floor price could be written to
the deal and merely FLAGGED as pending, which would mean the pipeline, the
proposal and the compensation projection all describe a deal nobody agreed to.

So the assertion that matters most here is a negative one: after a refusal, the
opportunity is byte-for-byte what it was.
"""

import itertools
from datetime import date
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, PAYEE_SELLER,
                                            CompensationPlan, CompensationRule)
from app.models.models import Platform, User
from app.models.pricing_policy_models import PricingPolicy
from app.models.sales_models import (APPROVAL_APPROVED, APPROVAL_PENDING,
                                     ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership, Opportunity,
                                     OpportunityEvent, PricingApprovalRequest)
from app.services import pricing_approvals as appr
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture()
def world(db_session):
    plat = Platform(name="EvoSys", slug="evo-%d" % next(_SEQ))
    db_session.add(plat); db_session.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Sales",
                          slug="evo-s-%d" % next(_SEQ))
    db_session.add(brand); db_session.commit()
    pkg = BrandPackage(platform_id=plat.id, name="Starter",
                       key="starter-%d" % next(_SEQ),
                       price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13, currency="USD")
    db_session.add(pkg); db_session.commit()

    def mk(role, reports_to=None):
        u = User(organization_id=None, email="u%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("x"), full_name=role,
                 role="advisor", must_change_password=False)
        db_session.add(u); db_session.commit()
        db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                                  scope_id=brand.id, role=role, is_active=True,
                                  reports_to_user_id=reports_to))
        db_session.commit()
        return u

    manager = mk(ROLE_SALES_MANAGER)
    rep = mk(ROLE_SALES_REP, reports_to=manager.id)

    # Reps may give 10% here.
    db_session.add(PricingPolicy(brand_sales_org_id=brand.id, role=ROLE_SALES_REP,
                                 max_discount_pct_monthly=Decimal("10"),
                                 max_discount_pct_setup=Decimal("10"),
                                 is_active=True))
    db_session.commit()

    opp = Opportunity(brand_sales_org_id=brand.id, owner_user_id=rep.id,
                      company_name="Acme Memorial", selected_package_id=pkg.id,
                      billing_option="month_to_month", stage="closing",
                      status="open")
    db_session.add(opp); db_session.commit()
    return dict(brand=brand, pkg=pkg, manager=manager, rep=rep, opp=opp)


def _hdr(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ═════════════════════════════════════════════════════════════════════════════
# Inside the floor
# ═════════════════════════════════════════════════════════════════════════════

def test_a_rep_may_save_a_custom_deal_inside_their_floor(client, db_session, world):
    """$597 -> $550 is 7.9% off. A rep could not do this at all before."""
    r = client.patch("/sales/opportunities/%s" % world["opp"].id,
                     json={"custom_unit_price": 550, "custom_min_units": 1},
                     headers=_hdr(db_session, world["rep"]))
    assert r.status_code == 200
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert opp.custom_unit_price == Decimal("550.00")


def test_the_two_pricing_narratives_are_saved(client, db_session, world):
    r = client.patch("/sales/opportunities/%s" % world["opp"].id,
                     json={"custom_unit_price": 550, "custom_min_units": 1,
                           "pricing_notes_internal": "Matched a competitor quote",
                           "pricing_description_customer": "Launch pricing"},
                     headers=_hdr(db_session, world["rep"]))
    assert r.status_code == 200
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert opp.pricing_notes_internal == "Matched a competitor quote"
    assert opp.pricing_description_customer == "Launch pricing"


# ═════════════════════════════════════════════════════════════════════════════
# Below the floor — routed, and NOTHING written
# ═════════════════════════════════════════════════════════════════════════════

def test_below_the_floor_is_refused_with_a_reason(client, db_session, world):
    r = client.patch("/sales/opportunities/%s" % world["opp"].id,
                     json={"custom_unit_price": 300, "custom_min_units": 1},
                     headers=_hdr(db_session, world["rep"]))
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["error"] == "pricing_approval_required"
    assert body["request_id"]
    assert body["breaches"]


def test_a_refused_price_is_not_written_to_the_deal(client, db_session, world):
    """THE ASSERTION THAT MATTERS. A pending price on the deal would mean every
    downstream reader describes terms nobody agreed to."""
    before = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one().custom_unit_price
    client.patch("/sales/opportunities/%s" % world["opp"].id,
                 json={"custom_unit_price": 300, "custom_min_units": 1},
                 headers=_hdr(db_session, world["rep"]))
    after = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert after.custom_unit_price == before
    assert after.custom_unit_price is None


def test_a_refusal_creates_exactly_one_pending_request(client, db_session, world):
    client.patch("/sales/opportunities/%s" % world["opp"].id,
                 json={"custom_unit_price": 300, "custom_min_units": 1},
                 headers=_hdr(db_session, world["rep"]))
    reqs = db_session.query(PricingApprovalRequest).filter(
        PricingApprovalRequest.opportunity_id == world["opp"].id).all()
    assert len(reqs) == 1
    assert reqs[0].status == APPROVAL_PENDING
    assert reqs[0].request_kind == "custom_deal"
    assert reqs[0].requested_unit_price == Decimal("300.00")
    # The queue is opportunity-scoped here — there is no proposal yet, and one
    # must not have been invented to satisfy a foreign key.
    assert reqs[0].proposal_id is None


def test_asking_twice_replaces_rather_than_stacks(client, db_session, world):
    """Two live asks on one deal is a queue a manager cannot answer: approving
    one silently contradicts the other."""
    for price in (300, 250):
        client.patch("/sales/opportunities/%s" % world["opp"].id,
                     json={"custom_unit_price": price, "custom_min_units": 1},
                     headers=_hdr(db_session, world["rep"]))
    pending = db_session.query(PricingApprovalRequest).filter(
        PricingApprovalRequest.opportunity_id == world["opp"].id,
        PricingApprovalRequest.status == APPROVAL_PENDING).all()
    assert len(pending) == 1
    assert pending[0].requested_unit_price == Decimal("250.00")


def test_the_refusal_is_recorded_on_the_timeline(client, db_session, world):
    client.patch("/sales/opportunities/%s" % world["opp"].id,
                 json={"custom_unit_price": 300, "custom_min_units": 1},
                 headers=_hdr(db_session, world["rep"]))
    events = db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == world["opp"].id,
        OpportunityEvent.event_type == "pricing_approval_requested").all()
    assert len(events) == 1
    assert events[0].actor_user_id == world["rep"].id


# ═════════════════════════════════════════════════════════════════════════════
# The manager decides
# ═════════════════════════════════════════════════════════════════════════════

def test_a_manager_may_price_below_the_reps_floor_directly(client, db_session, world):
    """No policy row names managers, so they fall back to unlimited — the
    authority they already had."""
    r = client.patch("/sales/opportunities/%s" % world["opp"].id,
                     json={"custom_unit_price": 300, "custom_min_units": 1},
                     headers=_hdr(db_session, world["manager"]))
    assert r.status_code == 200
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert opp.custom_unit_price == Decimal("300.00")


def test_approval_writes_the_agreed_price_with_the_manager_as_actor(
        client, db_session, world):
    client.patch("/sales/opportunities/%s" % world["opp"].id,
                 json={"custom_unit_price": 300, "custom_min_units": 1,
                       "custom_term_months": 12},
                 headers=_hdr(db_session, world["rep"]))
    req = db_session.query(PricingApprovalRequest).filter(
        PricingApprovalRequest.opportunity_id == world["opp"].id,
        PricingApprovalRequest.status == APPROVAL_PENDING).one()

    res = appr.decide(db_session, req, world["manager"], approve=True,
                      note="Agreed on the call")
    assert res["ok"] is True

    opp = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert opp.custom_unit_price == Decimal("300.00")
    assert opp.custom_term_months == 12
    db_session.refresh(req)
    assert req.status == APPROVAL_APPROVED
    assert req.decided_by == world["manager"].id


def test_denial_leaves_the_deal_untouched(client, db_session, world):
    client.patch("/sales/opportunities/%s" % world["opp"].id,
                 json={"custom_unit_price": 300, "custom_min_units": 1},
                 headers=_hdr(db_session, world["rep"]))
    req = db_session.query(PricingApprovalRequest).filter(
        PricingApprovalRequest.status == APPROVAL_PENDING).one()
    appr.decide(db_session, req, world["manager"], approve=False, note="Too thin")
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert opp.custom_unit_price is None


# ═════════════════════════════════════════════════════════════════════════════
# Tenancy, and who may read compensation
# ═════════════════════════════════════════════════════════════════════════════

def test_another_brands_manager_cannot_touch_this_deal(client, db_session, world):
    other_plat = Platform(name="Other", slug="oth-%d" % next(_SEQ))
    db_session.add(other_plat); db_session.commit()
    other_brand = BrandSalesOrg(platform_id=other_plat.id, name="Other Sales",
                                slug="oth-s-%d" % next(_SEQ))
    db_session.add(other_brand); db_session.commit()
    stranger = User(organization_id=None, email="s%d@other.com" % next(_SEQ),
                    password_hash=hash_password("x"), full_name="Stranger",
                    role="advisor", must_change_password=False)
    db_session.add(stranger); db_session.commit()
    db_session.add(Membership(user_id=stranger.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=other_brand.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    db_session.commit()

    r = client.patch("/sales/opportunities/%s" % world["opp"].id,
                     json={"custom_unit_price": 100, "custom_min_units": 1},
                     headers=_hdr(db_session, stranger))
    assert r.status_code in (403, 404)
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == world["opp"].id).one()
    assert opp.custom_unit_price is None


def test_a_rep_is_not_shown_compensation_on_their_own_deal(client, db_session, world):
    """The payload carries the override layers above them. "What does my
    manager earn on my deal" is not a question this screen answers by accident."""
    r = client.get("/sales/opportunities/%s" % world["opp"].id,
                   headers=_hdr(db_session, world["rep"]))
    assert r.status_code == 200
    assert r.json().get("compensation") is None


def test_a_manager_is_shown_compensation(client, db_session, world):
    plan = CompensationPlan(brand_sales_org_id=world["brand"].id, name="P",
                            effective_from=date(2026, 1, 1), is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(plan_id=plan.id, payee_kind=PAYEE_SELLER,
                                    basis=BASIS_FIXED, amount=Decimal("500")))
    db_session.commit()
    r = client.get("/sales/opportunities/%s" % world["opp"].id,
                   headers=_hdr(db_session, world["manager"]))
    assert r.status_code == 200
    comp = r.json().get("compensation")
    assert comp is not None
    assert comp["seller_total"] == 500.0
