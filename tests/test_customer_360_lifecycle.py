"""CUSTOMER 360 AND THE CUSTOMER LIFECYCLE.

THE TWO LINES THIS FILE DEFENDS

  CANCELLATION IS NOT DELETION. A customer leaving changes their status and
  records why. It does not touch the originating opportunity, the proposal and
  its version, the pricing agreed at the sale, or a single commission row. Half
  the assertions below exist to make that structural rather than intentional.

  NOTHING IS GUESSED. An organization with no implementation record has no
  provable originating deal, and this system says so rather than matching on a
  company name. "Created outside pipeline" and "commercial data incomplete" are
  correct answers; a plausible reconstruction is not.

The worked example throughout is the real one: $1,497 setup, $500/month on a
13-month agreement, $6,500 recurring, $7,997 total.
"""

import itertools
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, COMP_EARNED,
                                            COMP_PAID, PAYEE_SELLER,
                                            CompensationEntry,
                                            CompensationPlan, CompensationRule)
from app.models.customer_lifecycle_models import (
    CUST_ACTIVE, CUST_ARCHIVED, CUST_CANCELLATION_REQUESTED, CUST_CANCELLED,
    CUST_OFFBOARDING, CustomerLifecycleEvent, may_transition)
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, Proposal, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership, Opportunity)
from app.services import customer_360 as c360
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures — one brand, one Starter package, a rep under a manager, and two
# customers: one that came through the pipeline and one that did not.
# ═════════════════════════════════════════════════════════════════════════════

def _user(db, name="Person", org_id=None, role="advisor"):
    u = User(organization_id=org_id, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    return p


@pytest.fixture()
def other_platform(db_session):
    p = Platform(name="Second Brand", slug="second-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="EvoSys Pro Sales",
                      slug="evo-sales-%d" % next(_SEQ))
    db_session.add(b); db_session.commit()
    return b


@pytest.fixture()
def starter(db_session, platform):
    p = BrandPackage(platform_id=platform.id, name="Starter",
                     key="starter-%d" % next(_SEQ),
                     price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                     monthly_price=Decimal("597.00"),
                     contract_monthly_price=Decimal("500.00"),
                     contract_term_months=13, currency="USD")
    db_session.add(p); db_session.commit()
    return p


@pytest.fixture()
def manager(db_session, brand):
    u = _user(db_session, "Manager")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    db_session.commit()
    return u


@pytest.fixture()
def rep(db_session, brand, manager):
    u = _user(db_session, "Rep")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_REP,
                              is_active=True, reports_to_user_id=manager.id))
    db_session.commit()
    return u


@pytest.fixture()
def god(db_session):
    return _user(db_session, "Owner", role="god_admin")


def _org(db, platform, name="Acme Memorial", **kw):
    o = Organization(name=name, slug="acme-%d" % next(_SEQ),
                     platform_id=platform.id, plan="standard",
                     is_active=kw.pop("is_active", True), **kw)
    db.add(o); db.commit()
    return o


def _sold_customer(db, platform, brand, rep, starter, *, name="Acme Memorial",
                   billing_option="term_agreement", term=13,
                   fee=Decimal("1497.00"), rate=Decimal("500.00"),
                   with_proposal=True):
    """A customer that came through Won → Provision, wired the way
    `provision_customer` actually wires one."""
    org = _org(db, platform, name=name)
    opp = Opportunity(brand_sales_org_id=brand.id, owner_user_id=rep.id,
                      company_name=name, selected_package_id=starter.id,
                      stage="closing", status="won",
                      billing_option=billing_option,
                      contract_term_months=term)
    db.add(opp); db.commit()

    prop = None
    if with_proposal:
        prop = Proposal(opportunity_id=opp.id, brand_sales_org_id=brand.id,
                        created_by_id=rep.id, title="Proposal",
                        proposal_number="EV-%d" % next(_SEQ), version=2,
                        status="published", sales_status="accepted",
                        package_id=starter.id, billing_option=billing_option,
                        contract_term_months=term,
                        sent_at=datetime(2026, 7, 1, 10, 0),
                        accepted_at=datetime(2026, 7, 9, 10, 0))
        db.add(prop); db.commit()

    impl = Implementation(
        opportunity_id=opp.id, organization_id=org.id,
        platform_id=platform.id, brand_sales_org_id=brand.id,
        package_id=starter.id,
        accepted_proposal_id=prop.id if prop else None,
        accepted_proposal_version=prop.version if prop else None,
        sold_by_user_id=rep.id, status="live",
        launched_at=datetime(2026, 7, 15, 9, 0),
        implementation_fee=fee, recurring_amount=rate,
        billing_option=billing_option, contract_term_months=term,
        currency="USD", billing_status="configured")
    db.add(impl); db.commit()
    return org, opp, prop, impl


# ═════════════════════════════════════════════════════════════════════════════
# 1-5. The pipeline-created customer resolves, and the figures reconcile
# ═════════════════════════════════════════════════════════════════════════════

def test_a_pipeline_customer_links_to_its_originating_opportunity(
        db_session, platform, brand, rep, starter):
    org, opp, _prop, _impl = _sold_customer(db_session, platform, brand, rep, starter)
    d = c360.customer_360(db_session, org)
    assert d["source"] == c360.SOURCE_PIPELINE
    assert d["opportunity"]["id"] == opp.id


def test_the_governing_proposal_and_version_can_be_traced(
        db_session, platform, brand, rep, starter):
    org, _opp, prop, _impl = _sold_customer(db_session, platform, brand, rep, starter)
    d = c360.customer_360(db_session, org)
    assert d["proposal"]["id"] == prop.id
    assert d["proposal"]["number"] == prop.proposal_number
    # The version recorded ON THE CROSSING, which is what governed the sale.
    assert d["accepted_proposal_version"] == 2


def test_the_package_purchased_resolves(db_session, platform, brand, rep, starter):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    d = c360.customer_360(db_session, org)
    assert d["package"]["name"] == "Starter"


def test_setup_mrr_term_rcv_and_tcv_reconcile(
        db_session, platform, brand, rep, starter):
    """$1,497 + ($500 x 13) = $7,997."""
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    c = c360.customer_360(db_session, org)["commercials"]
    assert c["complete"] is True
    assert c["structure"] == c360.STRUCTURE_TERM
    assert c["setup"] == 1497.0
    assert c["mrr"] == 500.0
    assert c["term_months"] == 13
    assert c["recurring_contract_value"] == 6500.0
    assert c["total_contract_value"] == 7997.0
    assert c["recurring_contract_value"] + c["setup"] == c["total_contract_value"]


def test_historical_pricing_comes_from_the_snapshot_not_the_catalogue(
        db_session, platform, brand, rep, starter):
    """A price rise must not rewrite what a customer already agreed to."""
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    before = c360.customer_360(db_session, org)["commercials"]

    starter.setup_fee = Decimal("2997.00")
    starter.contract_monthly_price = Decimal("900.00")
    starter.price = Decimal("2997.00")
    db_session.commit()

    after = c360.customer_360(db_session, org)["commercials"]
    assert after == before
    assert after["setup"] == 1497.0
    assert after["total_contract_value"] == 7997.0


# ═════════════════════════════════════════════════════════════════════════════
# 6. Month-to-month has an MRR and NO fabricated contract value
# ═════════════════════════════════════════════════════════════════════════════

def test_month_to_month_has_mrr_but_no_fake_rcv_or_tcv(
        db_session, platform, brand, rep, starter):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter,
                             billing_option="month_to_month", term=None,
                             rate=Decimal("597.00"))
    c = c360.customer_360(db_session, org)["commercials"]
    assert c["structure"] == c360.STRUCTURE_M2M
    assert c["mrr"] == 597.0
    assert c["term_label"] == "Month-to-month"
    assert c["recurring_contract_value"] is None
    assert c["total_contract_value"] is None
    # Not incomplete: the terms are known, they are simply open-ended.
    assert c["complete"] is True


def test_a_stray_term_on_a_month_to_month_sale_invents_nothing(
        db_session, platform, brand, rep, starter):
    """A term left on a row sold month-to-month must not manufacture a total."""
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter,
                             billing_option="month_to_month", term=13,
                             rate=Decimal("597.00"))
    c = c360.customer_360(db_session, org)["commercials"]
    assert c["recurring_contract_value"] is None
    assert c["total_contract_value"] is None


# ═════════════════════════════════════════════════════════════════════════════
# 7-9. Implementation, sales owner and brand resolve
# ═════════════════════════════════════════════════════════════════════════════

def test_implementation_status_resolves(db_session, platform, brand, rep, starter):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    d = c360.customer_360(db_session, org)
    assert d["implementation"]["status"] == "live"
    assert d["implementation_state"] == "live"


def test_a_workspace_without_an_implementation_is_not_assumed_complete(
        db_session, platform):
    org = _org(db_session, platform, name="Manual Co")
    d = c360.customer_360(db_session, org)
    assert d["implementation"] is None
    assert d["implementation_state"] == c360.NO_IMPLEMENTATION


def test_the_sales_owner_and_their_manager_resolve(
        db_session, platform, brand, rep, manager, starter):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    d = c360.customer_360(db_session, org)
    assert d["sold_by"]["id"] == rep.id
    assert d["sales_manager"]["id"] == manager.id
    assert d["sales_organization"]["id"] == brand.id


def test_the_owning_brand_resolves(db_session, platform, brand, rep, starter):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    d = c360.customer_360(db_session, org)
    assert d["platform"]["id"] == platform.id
    assert d["platform"]["name"] == "EvoSys Pro"


# ═════════════════════════════════════════════════════════════════════════════
# 10-11. Legacy customers are identified, never reconstructed
# ═════════════════════════════════════════════════════════════════════════════

def test_a_manual_customer_is_identified_as_created_outside_the_pipeline(
        db_session, platform):
    org = _org(db_session, platform, name="Manual Co")
    d = c360.customer_360(db_session, org)
    assert d["source"] == c360.SOURCE_OUTSIDE
    assert d["opportunity"] is None
    assert d["proposal"] is None
    assert d["sold_by"] is None


def test_unknown_commercial_information_is_marked_incomplete_not_zero(
        db_session, platform):
    org = _org(db_session, platform, name="Manual Co")
    c = c360.customer_360(db_session, org)["commercials"]
    assert c["complete"] is False
    assert c["incomplete_reason"]
    # None, not 0.0. A zero would read as "they pay nothing", which is a claim.
    assert c["setup"] is None
    assert c["mrr"] is None
    assert c["total_contract_value"] is None


def test_a_sale_that_recorded_no_figures_is_incomplete_rather_than_invented(
        db_session, platform, brand, rep, starter):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter,
                             fee=None, rate=None, term=None,
                             billing_option=None)
    d = c360.customer_360(db_session, org)
    # The DEAL is still provable — only the money is missing.
    assert d["source"] == c360.SOURCE_PIPELINE
    assert d["commercials"]["complete"] is False
    assert d["commercials"]["structure"] == c360.STRUCTURE_UNKNOWN


def test_no_relationship_is_matched_by_company_name(
        db_session, platform, brand, rep, starter):
    """A manual org sharing a name with a real deal must stay unlinked."""
    _sold, opp, _p, _i = _sold_customer(db_session, platform, brand, rep,
                                        starter, name="Twin Co")
    lookalike = _org(db_session, platform, name="Twin Co")
    d = c360.customer_360(db_session, lookalike)
    assert d["source"] == c360.SOURCE_OUTSIDE
    assert d["opportunity"] is None


# ═════════════════════════════════════════════════════════════════════════════
# 12-13. Isolation
# ═════════════════════════════════════════════════════════════════════════════

def test_one_customers_record_carries_no_other_customers_data(
        db_session, platform, brand, rep, starter):
    a, *_ = _sold_customer(db_session, platform, brand, rep, starter, name="A Co")
    b, opp_b, _p, _i = _sold_customer(db_session, platform, brand, rep, starter,
                                      name="B Co")
    d = c360.customer_360(db_session, a)
    assert d["name"] == "A Co"
    assert d["opportunity"]["id"] != opp_b.id
    assert d["user_count"] == 0


def test_the_customer_list_is_filtered_by_brand(
        db_session, platform, other_platform, brand, rep, starter):
    _sold_customer(db_session, platform, brand, rep, starter, name="Brand A Co")
    _org(db_session, other_platform, name="Brand B Co")

    rows_a = c360.customer_rows(db_session, platform_id=platform.id)
    names_a = {r["name"] for r in rows_a}
    assert "Brand A Co" in names_a
    assert "Brand B Co" not in names_a

    rows_b = c360.customer_rows(db_session, platform_id=other_platform.id)
    assert {r["name"] for r in rows_b} == {"Brand B Co"}


# ═════════════════════════════════════════════════════════════════════════════
# 16. Won → Customer preserves the lifecycle IDs
# ═════════════════════════════════════════════════════════════════════════════

def test_every_lifecycle_id_survives_the_crossing(
        db_session, platform, brand, rep, starter):
    org, opp, prop, impl = _sold_customer(db_session, platform, brand, rep, starter)
    # Durable IDs on the row itself, not reconstructed by this test.
    assert impl.opportunity_id == opp.id
    assert impl.organization_id == org.id
    assert impl.platform_id == platform.id
    assert impl.brand_sales_org_id == brand.id
    assert impl.package_id == starter.id
    assert impl.accepted_proposal_id == prop.id
    assert impl.accepted_proposal_version == prop.version
    assert impl.sold_by_user_id == rep.id


# ═════════════════════════════════════════════════════════════════════════════
# 19-22. CANCELLATION IS NOT DELETION
# ═════════════════════════════════════════════════════════════════════════════

def _cancel_fully(db, org, actor):
    from app.services import customer_lifecycle as lc
    lc.request_cancellation(db, org, actor, reason="price",
                            effective_at=datetime(2026, 12, 31),
                            note="Budget cut")
    lc.complete_cancellation(db, org, actor)


def test_cancellation_preserves_the_customer_and_all_their_history(
        db_session, platform, brand, rep, starter, god):
    org, opp, prop, impl = _sold_customer(db_session, platform, brand, rep, starter)
    org_id, opp_id, prop_id, impl_id = org.id, opp.id, prop.id, impl.id

    _cancel_fully(db_session, org, god)

    assert db_session.query(Organization).filter(
        Organization.id == org_id).first() is not None
    assert db_session.query(Opportunity).filter(
        Opportunity.id == opp_id).first() is not None
    assert db_session.query(Proposal).filter(
        Proposal.id == prop_id).first() is not None
    assert db_session.query(Implementation).filter(
        Implementation.id == impl_id).first() is not None


def test_cancellation_preserves_the_pricing_agreed_at_the_sale(
        db_session, platform, brand, rep, starter, god):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    before = c360.customer_360(db_session, org)["commercials"]
    _cancel_fully(db_session, org, god)
    after = c360.customer_360(db_session, org)["commercials"]
    assert after == before
    assert after["total_contract_value"] == 7997.0


def test_cancellation_does_not_touch_earned_or_paid_compensation(
        db_session, platform, brand, rep, starter, god):
    org, opp, _p, _i = _sold_customer(db_session, platform, brand, rep, starter)
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="Plan",
                            effective_from=date(2026, 1, 1), holdback_days=14,
                            max_override_levels=1, is_active=True)
    db_session.add(plan); db_session.commit()
    rule = CompensationRule(plan_id=plan.id, package_id=starter.id,
                            payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                            amount=Decimal("500.00"), sort_order=1)
    db_session.add(rule); db_session.commit()
    entry = CompensationEntry(opportunity_id=opp.id, plan_id=plan.id,
                              rule_id=rule.id, payee_user_id=rep.id,
                              brand_sales_org_id=brand.id,
                              payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                              amount=Decimal("500.00"), currency="USD",
                              state=COMP_PAID, collection_reference="inv-1",
                              collected_at=datetime(2026, 7, 20, 9, 0))
    db_session.add(entry); db_session.commit()
    entry_id = entry.id

    _cancel_fully(db_session, org, god)

    kept = db_session.query(CompensationEntry).filter(
        CompensationEntry.id == entry_id).first()
    assert kept is not None
    assert kept.amount == Decimal("500.00")
    assert kept.state == COMP_PAID


def test_cancellation_records_when_and_why(db_session, platform, brand, rep,
                                           starter, god):
    from app.services import customer_lifecycle as lc
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    lc.request_cancellation(db_session, org, god, reason="price",
                            effective_at=datetime(2026, 12, 31),
                            note="Budget cut", obligations_note="4 months left")

    d = c360.customer_360(db_session, org)
    assert d["lifecycle_status"] == CUST_CANCELLATION_REQUESTED
    assert d["cancellation_reason"] == "price"
    assert d["cancellation_requested_at"] is not None
    assert d["cancellation_effective_at"] == datetime(2026, 12, 31)
    assert d["cancellation_note"] == "Budget cut"
    ev = d["lifecycle_history"][0]
    assert ev["event"] == "cancellation_requested"
    assert ev["obligations_note"] == "4 months left"


def test_requesting_a_cancellation_does_not_close_the_workspace(
        db_session, platform, brand, rep, starter, god):
    """A notice period is normal. Cutting a customer off the moment somebody
    clicks Cancel would break an agreement we are still being paid under."""
    from app.services import customer_lifecycle as lc
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    lc.request_cancellation(db_session, org, god, reason="price")
    assert org.is_active is True
    assert c360.customer_360(db_session, org)["workspace_active"] is True


def test_completing_a_cancellation_closes_the_workspace_but_keeps_memberships(
        db_session, platform, brand, rep, starter, god):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    staff = _user(db_session, "Customer Admin", org_id=org.id)
    _cancel_fully(db_session, org, god)

    assert org.is_active is False
    assert c360.status_of(org) == CUST_CANCELLED
    # The person is kept. Reactivation must not require rebuilding the team.
    assert db_session.query(User).filter(User.id == staff.id).first() is not None


def test_a_cancelled_customer_is_still_searchable(
        db_session, platform, brand, rep, starter, god):
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter,
                             name="Gone Co")
    _cancel_fully(db_session, org, god)
    rows = c360.customer_rows(db_session, statuses=[CUST_CANCELLED])
    assert "Gone Co" in {r["name"] for r in rows}


# ═════════════════════════════════════════════════════════════════════════════
# 23. Archive retains everything and stays retrievable
# ═════════════════════════════════════════════════════════════════════════════

def test_archiving_retains_every_record_and_remains_retrievable(
        db_session, platform, brand, rep, starter, god):
    from app.services import customer_lifecycle as lc
    org, opp, prop, impl = _sold_customer(db_session, platform, brand, rep,
                                          starter, name="Filed Co")
    lc.archive(db_session, org, god, note="Long gone")

    assert c360.status_of(org) == CUST_ARCHIVED
    assert db_session.query(Opportunity).filter(Opportunity.id == opp.id).first()
    assert db_session.query(Proposal).filter(Proposal.id == prop.id).first()
    assert db_session.query(Implementation).filter(
        Implementation.id == impl.id).first()

    # Out of the everyday view, one filter away from being read.
    default = c360.customer_rows(
        db_session, statuses=[CUST_ACTIVE, CUST_CANCELLATION_REQUESTED,
                              CUST_OFFBOARDING, CUST_CANCELLED])
    assert "Filed Co" not in {r["name"] for r in default}
    found = c360.customer_rows(db_session, statuses=[CUST_ARCHIVED])
    assert "Filed Co" in {r["name"] for r in found}


def test_a_reactivated_customer_keeps_the_record_of_having_left(
        db_session, platform, brand, rep, starter, god):
    from app.services import customer_lifecycle as lc
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    _cancel_fully(db_session, org, god)
    lc.reactivate(db_session, org, god)

    d = c360.customer_360(db_session, org)
    assert d["lifecycle_status"] == CUST_ACTIVE
    assert d["workspace_active"] is True
    # The history is not rewritten. They did leave.
    assert d["cancelled_at"] is not None
    assert d["cancellation_reason"] == "price"
    assert d["reactivated_at"] is not None
    assert len(d["lifecycle_history"]) == 3


# ═════════════════════════════════════════════════════════════════════════════
# Transition safety
# ═════════════════════════════════════════════════════════════════════════════

def test_illegal_transitions_are_refused():
    assert may_transition(CUST_ACTIVE, CUST_CANCELLATION_REQUESTED) is True
    # Cannot complete a cancellation nobody requested.
    assert may_transition(CUST_ACTIVE, CUST_CANCELLED) is False
    # An unknown/NULL current status reads as active, and fails closed from there.
    assert may_transition(None, CUST_CANCELLED) is False
    assert may_transition(None, CUST_CANCELLATION_REQUESTED) is True


def test_the_service_refuses_an_illegal_transition(
        db_session, platform, brand, rep, starter, god):
    from fastapi import HTTPException
    from app.services import customer_lifecycle as lc
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    with pytest.raises(HTTPException) as e:
        lc.complete_cancellation(db_session, org, god)
    assert e.value.status_code == 409


def test_a_null_lifecycle_status_reads_as_active(db_session, platform):
    """Every organization predating the column was a live customer."""
    org = _org(db_session, platform, name="Legacy Co")
    assert org.lifecycle_status is None
    assert c360.status_of(org) == CUST_ACTIVE
    assert c360.customer_360(db_session, org)["lifecycle_status"] == CUST_ACTIVE


# ═════════════════════════════════════════════════════════════════════════════
# 25. Permanent deletion cannot orphan protected relationships
# ═════════════════════════════════════════════════════════════════════════════

def test_deletion_is_refused_for_a_customer_with_commercial_history(
        db_session, platform, brand, rep, starter, god):
    from app.routers.customer_lifecycle_router import _deletion_impact
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    impact = _deletion_impact(db_session, org)
    assert impact["may_delete"] is False
    assert impact["refusal"]
    assert any("implementation" in b for b in impact["protected_relationships"])


def test_deletion_is_permitted_for_an_organization_with_no_history(
        db_session, platform):
    from app.routers.customer_lifecycle_router import _deletion_impact
    org = _org(db_session, platform, name="Oops Test Org")
    impact = _deletion_impact(db_session, org)
    assert impact["may_delete"] is True
    assert impact["protected_relationships"] == []


def test_cancellation_and_permanent_deletion_are_separate_operations(
        db_session, platform, brand, rep, starter, god):
    """Cancelling never deletes, and a cancelled customer is still undeletable
    while their history exists."""
    from app.routers.customer_lifecycle_router import _deletion_impact
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    _cancel_fully(db_session, org, god)
    assert db_session.query(Organization).filter(
        Organization.id == org.id).first() is not None
    assert _deletion_impact(db_session, org)["may_delete"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 26-27. No regression, and one set of authorities
# ═════════════════════════════════════════════════════════════════════════════

def test_customer_360_uses_the_shared_pricing_authority(
        db_session, platform, brand, rep, starter):
    """The customer record and a live quote must agree on the arithmetic.

    Both go through package_pricing.contract_values, so this asserts they
    cannot diverge rather than that they happen to match today.
    """
    from app.services import package_pricing as pp
    org, *_ = _sold_customer(db_session, platform, brand, rep, starter)
    c = c360.customer_360(db_session, org)["commercials"]
    rcv, tcv = pp.contract_values(Decimal("1497.00"), Decimal("500.00"), 13)
    assert c["recurring_contract_value"] == float(rcv)
    assert c["total_contract_value"] == float(tcv)


def test_existing_quote_behaviour_is_unchanged_by_the_shared_helper(
        db_session, starter):
    """contract_values was extracted OUT of quote(); quote must still answer
    exactly as it did."""
    from app.services import package_pricing as pp
    q = pp.quote(starter, "term_agreement", term_months=13)
    assert q["implementation_fee"] == 1497.0
    assert q["mrr"] == 500.0
    assert q["recurring_contract_value"] == 6500.0
    assert q["total_contract_value"] == 7997.0

    m = pp.quote(starter, "month_to_month")
    assert m["mrr"] == 597.0
    assert m["recurring_contract_value"] is None
    assert m["total_contract_value"] is None


def test_a_one_time_package_still_reports_its_fee_as_the_whole_deal(
        db_session, platform):
    """The branch that says "no recurring rate at all, the fee is the whole of
    it" must survive the extraction."""
    from app.services import package_pricing as pp
    pkg = BrandPackage(platform_id=platform.id, name="Build Only",
                       key="build-%d" % next(_SEQ), price=Decimal("2495.00"),
                       setup_fee=Decimal("2495.00"), currency="USD")
    db_session.add(pkg); db_session.commit()
    q = pp.quote(pkg, "month_to_month")
    assert q["mrr"] is None
    assert q["recurring_contract_value"] is None
    assert q["total_contract_value"] == 2495.0


# ═════════════════════════════════════════════════════════════════════════════
# 17-18. Proposal history is never rewritten
# ═════════════════════════════════════════════════════════════════════════════

def test_reading_and_cancelling_never_modify_the_proposal(
        db_session, platform, brand, rep, starter, god):
    org, _opp, prop, _impl = _sold_customer(db_session, platform, brand, rep,
                                            starter)
    before = (prop.version, prop.billing_option, prop.contract_term_months,
              prop.sales_status, prop.sent_at, prop.accepted_at)

    c360.customer_360(db_session, org)
    _cancel_fully(db_session, org, god)

    db_session.refresh(prop)
    assert (prop.version, prop.billing_option, prop.contract_term_months,
            prop.sales_status, prop.sent_at, prop.accepted_at) == before
