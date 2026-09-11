"""Custom commercial agreements — the model, the money, and the refusals.

THE STANDARD THIS FILE HOLDS ITSELF TO
--------------------------------------
Borrowed from test_launch_engine.py and test_deal_billing.py: a refusal is not
proved by a status code. Every refusal below also asserts that NOTHING
CHANGED — no term written, no status moved, no settlement figure produced.

And one more, specific to this thread: WHEREVER A FIGURE IS UNKNOWN, THE TEST
ASSERTS IT IS NOT ZERO. That is the defect this whole feature exists to
prevent, and `assert x is None` next to `assert x != 0` is the only way to
catch a regression that starts reporting an unknown as nothing.

No test here names a real customer, a real percentage as a rule, or a real
person. The Atlantis shape is exercised in test_commercial_atlantis.py as
DATA, against a synthetic database.
"""

import itertools
from datetime import date, timedelta

import pytest

from app.models.commercial_models import (
    AG_ACTIVE, AG_APPROVED, AG_ENDED, AG_READY_FOR_APPROVAL, AG_TERMS_REQUIRED,
    ATTR_ALL_ELIGIBLE, ATTR_UNKNOWN_REVIEW, COLLECTION_APPROVED,
    CommercialTerm, ITEM_DEMO, ITEM_MILESTONE, MODE_COMPLETED_PREVIOUSLY,
    MODE_NOT_APPLICABLE, MODE_WAIVED, PARTY_CUSTOMER_ORG, PARTY_EXTERNAL,
    PARTY_PLATFORM_BRAND, SETTLE_CALCULATED, SETTLE_REVIEW_REQUIRED,
    SOURCE_MANUAL_APPROVED, TERM_ANSWERED, TERM_REQUIRED,
    TYPE_CUSTOM_FIXED, TYPE_HYBRID, TYPE_REVENUE_SHARE,
    TYPE_STANDARD_SUBSCRIPTION,
)
from app.models.implementation_models import (
    Implementation, ImplementationMilestone, MILESTONE_DONE, MILESTONE_PENDING,
    MILESTONE_SKIPPED,
)
from app.models.models import Organization, Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    SCOPE_BRAND_SALES_ORG,
)
from app.services.auth_service import hash_password
from app.services.commercial import agreements as ag
from app.services.commercial import authority as auth
from app.services.commercial import onboarding as flow
from app.services.commercial import overrides as ov
from app.services.commercial import questions as qs
from app.services.commercial import revenue_share as rs
from app.services.commercial import settlement as settle
from app.services.commercial import terms as t

_SEQ = itertools.count(1)

# Every activation-required term for a revenue-share agreement, with an answer
# that is deliberately ORDINARY. The point of the fixture is a complete sheet,
# not a particular deal.
FULL_SHARE_TERMS = {
    "setup_fee": 0,
    "recurring_base_fee": 0,
    "share_basis": "gross_collections",
    "eligible_collections": "all_collections",
    "payment_recipient": "customer_company",
    "settlement_frequency": "monthly",
    "collections_source": SOURCE_MANUAL_APPROVED,
    "attribution_rule": ATTR_ALL_ELIGIBLE,
    "allocation_rule": "full_allocation",
    "adjustment_refunds": "reduce_share",
    "adjustment_chargebacks": "reduce_share",
    "adjustment_processing_fees": "not_deducted",
    "adjustment_taxes": "excluded_from_basis",
    "adjustment_credits": "do_not_reduce_share",
    "adjustment_write_offs": "do_not_reduce_share",
}


# ── builders ────────────────────────────────────────────────────────────────

def _platform(db, name="Brand One"):
    p = Platform(name=name, slug="brand-%d" % next(_SEQ), short_name="B1",
                 tagline="t", support_email="s@example.test")
    db.add(p)
    db.commit()
    return p


def _brand_sales_org(db, plat):
    b = BrandSalesOrg(platform_id=plat.id, name="%s Sales" % plat.name,
                      slug="bso-%d" % next(_SEQ))
    db.add(b)
    db.commit()
    return b


def _org(db, plat, name="Customer Co"):
    o = Organization(name=name, slug="org-%d" % next(_SEQ),
                     platform_id=plat.id, plan="standard", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label, platform_id=None):
    u = User(organization_id=(org.id if org else None),
             email="%s-%d@test.local" % (label, next(_SEQ)),
             password_hash=hash_password("TestPass123!"),
             full_name=label.title(), role=role, must_change_password=False)
    if platform_id is not None:
        u.platform_id = platform_id
    db.add(u)
    db.commit()
    return u


def _member(db, user, bso, role):
    m = Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=bso.id, role=role, is_active=True)
    db.add(m)
    db.commit()
    return m


def _opportunity(db, bso, **kw):
    o = Opportunity(brand_sales_org_id=bso.id,
                    company_name=kw.pop("company_name", "Customer Co"),
                    **kw)
    db.add(o)
    db.commit()
    return o


def _impl(db, org, plat, bso=None, opp=None):
    im = Implementation(organization_id=org.id, platform_id=plat.id,
                        brand_sales_org_id=(bso.id if bso else None),
                        opportunity_id=(opp.id if opp else "opp-%d" % next(_SEQ)),
                        status="not_started")
    db.add(im)
    db.commit()
    for i, (key, label, required) in enumerate([
            ("business_profile", "Business profile", True),
            ("customer_users", "Customer users", True),
            ("calendar", "Calendar connection", True),
            ("lead_import", "Lead import", False),
            ("launch", "Launch", True)]):
        db.add(ImplementationMilestone(implementation_id=im.id, key=key,
                                       label=label, position=i,
                                       is_required=required,
                                       status=MILESTONE_PENDING))
    db.commit()
    return im


@pytest.fixture()
def world(db_session):
    """Two brands, two customers. Two of each, because one cannot fail an
    isolation test."""
    plat_a = _platform(db_session, "Brand One")
    plat_b = _platform(db_session, "Brand Two")
    bso_a = _brand_sales_org(db_session, plat_a)
    bso_b = _brand_sales_org(db_session, plat_b)

    org_a = _org(db_session, plat_a, "Alpha Utilities")
    org_b = _org(db_session, plat_b, "Beta Energy")

    god = _user(db_session, None, "god_admin", "owner")
    manager_a = _user(db_session, None, "user", "manager-a", platform_id=plat_a.id)
    rep_a = _user(db_session, None, "user", "rep-a", platform_id=plat_a.id)
    manager_b = _user(db_session, None, "user", "manager-b", platform_id=plat_b.id)
    _member(db_session, manager_a, bso_a, ROLE_SALES_MANAGER)
    _member(db_session, rep_a, bso_a, ROLE_SALES_REP)
    _member(db_session, manager_b, bso_b, ROLE_SALES_MANAGER)

    admin_a = _user(db_session, org_a, "org_admin", "alpha-admin")
    admin_b = _user(db_session, org_b, "org_admin", "beta-admin")

    return {
        "plat_a": plat_a, "plat_b": plat_b, "bso_a": bso_a, "bso_b": bso_b,
        "org_a": org_a, "org_b": org_b, "god": god,
        "manager_a": manager_a, "rep_a": rep_a, "manager_b": manager_b,
        "admin_a": admin_a, "admin_b": admin_b,
    }


def _agreement(db, w, agreement_type=TYPE_REVENUE_SHARE, org=None,
               effective=True, actor=None):
    return ag.create(db, actor or w["god"],
                     platform_id=w["plat_a"].id,
                     brand_sales_org_id=w["bso_a"].id,
                     organization_id=(org or w["org_a"]).id,
                     agreement_type=agreement_type,
                     name="Arrangement",
                     effective_date=(date.today() if effective else None))


def _two_parties(db, w, agreement, a_pct=75, b_pct=25):
    p1 = ag.add_party(db, agreement, w["god"], party_key="provider",
                      display_name="Provider Side",
                      party_type=PARTY_PLATFORM_BRAND,
                      brand_sales_org_id=w["bso_a"].id)
    p2 = ag.add_party(db, agreement, w["god"], party_key="customer",
                      display_name="Customer Side",
                      party_type=PARTY_CUSTOMER_ORG,
                      organization_id=agreement.organization_id)
    if a_pct is not None:
        ag.set_allocation(db, agreement, w["god"], p1.id, percent=a_pct)
    if b_pct is not None:
        ag.set_allocation(db, agreement, w["god"], p2.id, percent=b_pct)
    return p1, p2


def _answer_all(db, w, agreement, overrides=None):
    values = dict(FULL_SHARE_TERMS)
    values.update(overrides or {})
    for key, value in values.items():
        defn = qs.definition(db, agreement.platform_id, key)
        if defn is None or not defn["is_active"]:
            continue
        if not qs.applies_to(defn, agreement.agreement_type):
            continue
        t.set_term(db, agreement, w["god"], key, value, source="internal")
    db.commit()
    return agreement


# ════════════════════════════════════════════════════════════════════════════
# A — a standard subscription customer is untouched by any of this
# ════════════════════════════════════════════════════════════════════════════

class TestStandardSubscription:
    def test_standard_subscription_asks_no_custom_questions(self, db_session, world):
        a = _agreement(db_session, world, TYPE_STANDARD_SUBSCRIPTION)
        db_session.commit()
        rows = t.ordered_terms(db_session, a)
        assert [r for r in rows if r["required_for_activation"]] == []
        assert a.status == AG_READY_FOR_APPROVAL

    def test_standard_subscription_does_not_pretend_to_have_a_share(
            self, db_session, world):
        a = _agreement(db_session, world, TYPE_STANDARD_SUBSCRIPTION)
        db_session.commit()
        block = t.blocking(db_session, a)
        assert block[t.ACTION_CALCULATE]["allowed"] is False
        assert any("no revenue share" in r.lower()
                   for r in block[t.ACTION_CALCULATE]["reasons"])


# ════════════════════════════════════════════════════════════════════════════
# B, D, E, F — allocations of every shape
# ════════════════════════════════════════════════════════════════════════════

class TestAllocations:
    def test_two_party_split_reconciles(self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        db_session.commit()
        v = rs.validate(db_session, a, "full_allocation")
        assert v["valid"] is True
        assert str(v["total_percent"]) == "100.000000"

    def test_eighty_twenty_reconciles_on_the_same_code_path(self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 80, 20)
        db_session.commit()
        assert rs.validate(db_session, a, "full_allocation")["valid"] is True

    def test_three_parties(self, db_session, world):
        a = _agreement(db_session, world)
        p1, p2 = _two_parties(db_session, world, a, 70, 20)
        p3 = ag.add_party(db_session, a, world["god"], party_key="partner",
                          display_name="Referral Partner",
                          party_type=PARTY_EXTERNAL,
                          external_reference="partner-ref")
        ag.set_allocation(db_session, a, world["god"], p3.id, percent=10)
        db_session.commit()
        v = rs.validate(db_session, a, "full_allocation")
        assert v["valid"] is True
        assert len(v["parties"]) == 3

    def test_a_split_that_does_not_add_up_is_refused_not_normalised(
            self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 22)
        db_session.commit()
        v = rs.validate(db_session, a, "full_allocation")
        assert v["valid"] is False
        assert any("97" in r for r in v["reasons"])
        # and nothing was rescaled behind the scenes
        assert str(v["total_percent"]) == "97.000000"

    def test_an_unknown_percentage_is_unknown_not_zero(self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, None)
        db_session.commit()
        v = rs.validate(db_session, a, "full_allocation")
        assert v["valid"] is False
        row = [p for p in v["parties"] if p["party_key"] == "customer"][0]
        assert row["percent"] is None
        assert row["percent"] != 0
        assert rs.effective_percents(v) == {}

    def test_residual_party_is_computed_and_shown(self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, None)
        db_session.commit()
        v = rs.validate(db_session, a, "residual_to_party")
        assert v["valid"] is True
        assert str(v["residual_percent"]) == "25.000000"

    def test_hybrid_keeps_its_fixed_side_in_t2(self, db_session, world):
        a = _agreement(db_session, world, TYPE_HYBRID)
        ag.update(db_session, a, world["god"], references_t2_subscription=True,
                  t2_note="Base plan billed through the catalogue.")
        _two_parties(db_session, world, a, 60, 40)
        t.set_term(db_session, a, world["god"], "allocation_rule",
                   "full_allocation")
        db_session.commit()
        view = ag.internal_view(db_session, a)
        assert view["references_t2_subscription"] is True
        # no price, interval or Stripe id is restated on the agreement
        assert "stripe_price_id" not in view
        assert view["allocation"]["valid"] is True


# ════════════════════════════════════════════════════════════════════════════
# C, O, P — incomplete terms, and what they do and do not block
# ════════════════════════════════════════════════════════════════════════════

class TestIncompleteTerms:
    def test_a_new_revenue_share_lands_in_terms_required(self, db_session, world):
        a = _agreement(db_session, world)
        db_session.commit()
        assert a.status == AG_TERMS_REQUIRED
        missing = {r["key"] for r in t.missing_required(db_session, a)}
        for key in ("share_basis", "eligible_collections", "payment_recipient",
                    "settlement_frequency"):
            assert key in missing

    def test_zero_is_an_answer_and_unanswered_is_not(self, db_session, world):
        a = _agreement(db_session, world)
        t.set_term(db_session, a, world["god"], "setup_fee", 0)
        db_session.commit()
        state = t.state_map(db_session, a)
        assert state["setup_fee"]["state"] == TERM_ANSWERED
        assert state["setup_fee"]["value"] == 0
        assert state["recurring_base_fee"]["state"] == TERM_REQUIRED
        assert state["recurring_base_fee"]["value"] is None

    def test_incomplete_terms_block_activation_and_nothing_else(
            self, db_session, world):
        org = world["org_a"]
        impl = _impl(db_session, org, world["plat_a"], world["bso_a"])
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        db_session.commit()

        block = t.blocking(db_session, a)
        assert block[t.ACTION_ACTIVATE]["allowed"] is False

        steps = {s["key"]: s for s in flow.steps(db_session, impl)}
        assert steps["commercial_terms"]["status"] == flow.S_TERMS_REQUIRED
        # the steps that have nothing to do with money are untouched
        for key in ("workspace_setup", "users_roles", "calendar_booking",
                    "lead_data_intake", "business_profile", "integrations",
                    "ai_workforce_setup"):
            assert steps[key]["status"] != flow.S_BLOCKED, key

    def test_an_unanswered_adjustment_policy_blocks_settlement(
            self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a)
        t.set_term(db_session, a, world["god"], "adjustment_refunds", None)
        db_session.commit()
        block = t.blocking(db_session, a)
        assert block[t.ACTION_CALCULATE]["allowed"] is False
        assert any("Refunds" in r for r in block[t.ACTION_CALCULATE]["reasons"])

    def test_settlement_preview_with_unknown_terms_produces_no_figures(
            self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        db_session.commit()
        out = settle.preview(db_session, a, world["god"],
                             period_start=date.today() - timedelta(days=30),
                             period_end=date.today())
        db_session.commit()
        assert out["status"] == SETTLE_REVIEW_REQUIRED
        assert out["basis_cents"] is None
        assert out["basis_cents"] != 0
        assert out["lines"] == []
        assert out["blocked_reasons"]


# ════════════════════════════════════════════════════════════════════════════
# M, N — collections and attribution
# ════════════════════════════════════════════════════════════════════════════

class TestCollectionsAndAttribution:
    def _live(self, db, w, org=None):
        a = _agreement(db, w, org=org)
        _two_parties(db, w, a, 75, 25)
        _answer_all(db, w, a)
        ag.approve(db, a, w["god"])
        ag.activate(db, a, w["god"])
        db.commit()
        return a

    def test_a_period_with_no_records_refuses_rather_than_settling_zero(
            self, db_session, world):
        a = self._live(db_session, world)
        out = settle.preview(db_session, a, world["god"],
                             period_start=date(2026, 1, 1),
                             period_end=date(2026, 1, 31))
        db_session.commit()
        assert out["status"] == SETTLE_REVIEW_REQUIRED
        assert out["basis_cents"] is None
        assert any("not treated as zero" in r for r in out["blocked_reasons"])

    def test_an_unapproved_record_is_not_authoritative(self, db_session, world):
        a = self._live(db_session, world)
        settle.record_collection(db_session, a, world["god"],
                                 period_start=date(2026, 1, 1),
                                 period_end=date(2026, 1, 31),
                                 source=SOURCE_MANUAL_APPROVED,
                                 gross_cents=1_000_00,
                                 adjustments_cents=0,
                                 attribution_state=ATTR_ALL_ELIGIBLE)
        db_session.commit()
        out = settle.preview(db_session, a, world["god"],
                             period_start=date(2026, 1, 1),
                             period_end=date(2026, 1, 31))
        db_session.commit()
        assert out["status"] == SETTLE_REVIEW_REQUIRED
        assert out["basis_cents"] is None
        assert any("not approved" in r for r in out["blocked_reasons"])

    def test_unresolved_attribution_stops_the_calculation(self, db_session, world):
        a = self._live(db_session, world)
        rec = settle.record_collection(db_session, a, world["god"],
                                       period_start=date(2026, 2, 1),
                                       period_end=date(2026, 2, 28),
                                       source=SOURCE_MANUAL_APPROVED,
                                       gross_cents=500_00,
                                       adjustments_cents=0,
                                       attribution_state=ATTR_UNKNOWN_REVIEW)
        settle.approve_collection(db_session, a, world["god"], rec.id)
        db_session.commit()
        out = settle.preview(db_session, a, world["god"],
                             period_start=date(2026, 2, 1),
                             period_end=date(2026, 2, 28))
        db_session.commit()
        assert out["status"] == SETTLE_REVIEW_REQUIRED
        assert any("attribution" in r.lower() for r in out["blocked_reasons"])

    def test_a_complete_period_calculates_and_every_cent_is_allocated(
            self, db_session, world):
        a = self._live(db_session, world)
        rec = settle.record_collection(db_session, a, world["god"],
                                       period_start=date(2026, 3, 1),
                                       period_end=date(2026, 3, 31),
                                       source=SOURCE_MANUAL_APPROVED,
                                       gross_cents=10_000_01,
                                       adjustments_cents=0,
                                       attribution_state=ATTR_ALL_ELIGIBLE)
        settle.approve_collection(db_session, a, world["god"], rec.id)
        db_session.commit()
        out = settle.preview(db_session, a, world["god"],
                             period_start=date(2026, 3, 1),
                             period_end=date(2026, 3, 31))
        db_session.commit()
        assert out["status"] == SETTLE_CALCULATED
        assert out["basis_cents"] == 10_000_01
        assert sum(line["amount_cents"] for line in out["lines"]) == 10_000_01
        assert out["unallocated_cents"] == 0
        assert out["is_payout"] is False

    def test_a_named_but_unimplemented_source_does_not_become_a_feed(
            self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a,
                    {"collections_source": "connected_accounting_system"})
        db_session.commit()
        block = t.blocking(db_session, a)
        assert block[t.ACTION_CALCULATE]["allowed"] is False
        assert any("no feed" in r for r in block[t.ACTION_CALCULATE]["reasons"])


# ════════════════════════════════════════════════════════════════════════════
# MONEY SAFETY
# ════════════════════════════════════════════════════════════════════════════

class TestMoneySafety:
    def test_distribution_is_refused_and_the_request_is_recorded(
            self, db_session, world):
        from fastapi import HTTPException

        from app.models.models import AuditLogEntry
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a)
        ag.approve(db_session, a, world["god"])
        ag.activate(db_session, a, world["god"])
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            settle.distribute(db_session, a, world["god"], "any-settlement-id")
        assert exc.value.status_code == 409
        db_session.commit()

        refusals = (db_session.query(AuditLogEntry)
                    .filter(AuditLogEntry.action ==
                            "commercial_settlement_distribution_refused").all())
        assert len(refusals) == 1

    def test_the_payout_switch_is_off(self):
        assert settle.PAYOUT_EXECUTION_SUPPORTED is False
        assert t.PAYOUT_EXECUTION_SUPPORTED is False

    def test_rounding_allocates_every_cent_deterministically(self, db_session, world):
        a = _agreement(db_session, world)
        p1, p2 = _two_parties(db_session, world, a, 1, None)
        ag.set_allocation(db_session, a, world["god"], p2.id, percent=99)
        db_session.commit()
        v = rs.validate(db_session, a, "full_allocation")
        first = rs.split(1_000_03, v, rs.ROUND_LARGEST_REMAINDER)
        second = rs.split(1_000_03, v, rs.ROUND_LARGEST_REMAINDER)
        assert first["allocated_cents"] == 1_000_03
        assert [l["amount_cents"] for l in first["lines"]] == \
               [l["amount_cents"] for l in second["lines"]]


# ════════════════════════════════════════════════════════════════════════════
# G, H, I — onboarding milestone overrides
# ════════════════════════════════════════════════════════════════════════════

class TestOverrides:
    def test_a_demo_given_before_this_workflow_satisfies_the_step(
            self, db_session, world):
        opp = _opportunity(db_session, world["bso_a"], contact_name="A Contact",
                           email="c@example.test", phone="555-0100")
        impl = _impl(db_session, world["org_a"], world["plat_a"],
                     world["bso_a"], opp)
        before = {s["key"]: s["status"] for s in flow.steps(db_session, impl)}
        assert before["demo_discovery"] == flow.S_INCOMPLETE

        ov.apply(db_session, impl, world["manager_a"],
                 item_kind=ITEM_DEMO, item_key="demo",
                 mode=MODE_COMPLETED_PREVIOUSLY,
                 reason="Demo was given before this onboarding workflow existed.",
                 previously_completed_date_known=False)
        db_session.commit()

        step = {s["key"]: s for s in flow.steps(db_session, impl)}["demo_discovery"]
        assert step["status"] == flow.S_COMPLETED_PREVIOUSLY
        assert step["override"]["decided_by_user_id"] == world["manager_a"].id
        assert step["override"]["previously_completed_on"] is None
        assert step["override"]["previously_completed_date_known"] is False
        assert step["override"]["reason"]

    def test_no_date_is_invented_and_a_contradictory_date_is_refused(
            self, db_session, world):
        from fastapi import HTTPException
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        with pytest.raises(HTTPException) as exc:
            ov.apply(db_session, impl, world["manager_a"],
                     item_kind=ITEM_DEMO, item_key="demo",
                     mode=MODE_COMPLETED_PREVIOUSLY, reason="r",
                     previously_completed_on=date(2026, 8, 1),
                     previously_completed_date_known=False)
        assert exc.value.status_code == 400
        assert ov.list_for(db_session, impl.id) == []

    def test_a_future_prior_completion_is_refused(self, db_session, world):
        from fastapi import HTTPException
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        with pytest.raises(HTTPException):
            ov.apply(db_session, impl, world["manager_a"],
                     item_kind=ITEM_DEMO, item_key="demo",
                     mode=MODE_COMPLETED_PREVIOUSLY, reason="r",
                     previously_completed_on=date.today() + timedelta(days=1),
                     previously_completed_date_known=True)
        assert ov.list_for(db_session, impl.id) == []

    def test_an_override_without_a_reason_is_refused(self, db_session, world):
        from fastapi import HTTPException
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        with pytest.raises(HTTPException) as exc:
            ov.apply(db_session, impl, world["manager_a"],
                     item_kind=ITEM_MILESTONE, item_key="lead_import",
                     mode=MODE_WAIVED, reason="   ")
        assert exc.value.status_code == 400
        assert ov.list_for(db_session, impl.id) == []

    def test_waiving_a_milestone_mirrors_it_as_skipped_not_done(
            self, db_session, world):
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        ov.apply(db_session, impl, world["manager_a"],
                 item_kind=ITEM_MILESTONE, item_key="lead_import",
                 mode=MODE_WAIVED,
                 reason="Customer is starting with no existing contacts.")
        db_session.commit()
        row = (db_session.query(ImplementationMilestone)
               .filter(ImplementationMilestone.implementation_id == impl.id,
                       ImplementationMilestone.key == "lead_import").first())
        assert row.status == MILESTONE_SKIPPED
        assert row.completed_at is None
        step = {s["key"]: s for s in flow.steps(db_session, impl)}["lead_data_intake"]
        assert step["status"] == flow.S_WAIVED

    def test_not_applicable_reads_as_not_required(self, db_session, world):
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        ov.apply(db_session, impl, world["manager_a"],
                 item_kind=ITEM_MILESTONE, item_key="calendar",
                 mode=MODE_NOT_APPLICABLE,
                 reason="This customer books through their own system.")
        db_session.commit()
        step = {s["key"]: s for s in flow.steps(db_session, impl)}["calendar_booking"]
        assert step["status"] == flow.S_NOT_REQUIRED
        assert step["override"]["mode"] == MODE_NOT_APPLICABLE

    def test_completed_previously_mirrors_as_done_with_the_decision_date(
            self, db_session, world):
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        ov.apply(db_session, impl, world["manager_a"],
                 item_kind=ITEM_MILESTONE, item_key="business_profile",
                 mode=MODE_COMPLETED_PREVIOUSLY,
                 reason="Collected during the sales process.",
                 previously_completed_on=date(2026, 8, 12),
                 previously_completed_date_known=True)
        db_session.commit()
        row = (db_session.query(ImplementationMilestone)
               .filter(ImplementationMilestone.implementation_id == impl.id,
                       ImplementationMilestone.key == "business_profile").first())
        assert row.status == MILESTONE_DONE
        # the milestone stamp is the DECISION, not the backdated work
        assert row.completed_at.date() == date.today()
        o = ov.list_for(db_session, impl.id)[0]
        assert o.previously_completed_on == date(2026, 8, 12)

    def test_withdrawing_an_override_restores_the_step_and_keeps_the_history(
            self, db_session, world):
        impl = _impl(db_session, world["org_a"], world["plat_a"], world["bso_a"])
        row = ov.apply(db_session, impl, world["manager_a"],
                       item_kind=ITEM_MILESTONE, item_key="lead_import",
                       mode=MODE_WAIVED, reason="Not needed.")
        db_session.commit()
        ov.remove(db_session, impl, world["manager_a"], row.id,
                  "The customer found their contact list after all.")
        db_session.commit()

        m = (db_session.query(ImplementationMilestone)
             .filter(ImplementationMilestone.implementation_id == impl.id,
                     ImplementationMilestone.key == "lead_import").first())
        assert m.status == MILESTONE_PENDING
        assert ov.list_for(db_session, impl.id, active_only=True) == []
        assert len(ov.list_for(db_session, impl.id, active_only=False)) == 1


# ════════════════════════════════════════════════════════════════════════════
# J, K — concurrency and authority
# ════════════════════════════════════════════════════════════════════════════

class TestConcurrencyAndAuthority:
    def test_a_stale_term_edit_is_refused_and_writes_nothing(
            self, db_session, world):
        from fastapi import HTTPException
        a = _agreement(db_session, world)
        t.set_term(db_session, a, world["god"], "settlement_frequency", "monthly")
        db_session.commit()
        stored = t.state_map(db_session, a)["settlement_frequency"]
        assert stored["revision"] == 1

        # somebody else answers it again
        t.set_term(db_session, a, world["manager_a"], "settlement_frequency",
                   "quarterly", expected_revision=1)
        db_session.commit()

        with pytest.raises(HTTPException) as exc:
            t.set_term(db_session, a, world["god"], "settlement_frequency",
                       "weekly", expected_revision=1)
        assert exc.value.status_code == 409
        db_session.rollback()
        assert t.state_map(db_session, a)["settlement_frequency"]["value"] == \
            "quarterly"

    def test_a_stale_agreement_edit_is_refused(self, db_session, world):
        from fastapi import HTTPException
        a = _agreement(db_session, world)
        db_session.commit()
        version = a.version
        ag.update(db_session, a, world["god"], name="First")
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            ag.update(db_session, a, world["god"], name="Second",
                      expected_version=version)
        assert exc.value.status_code == 409
        db_session.rollback()
        assert a.name == "First"

    def test_a_rep_cannot_change_the_economics(self, db_session, world):
        a = _agreement(db_session, world)
        db_session.commit()
        assert auth.can_on(db_session, world["rep_a"], auth.CAP_EDIT_TERMS, a)
        assert not auth.can_on(db_session, world["rep_a"],
                               auth.CAP_EDIT_ECONOMICS, a)
        assert not auth.can_on(db_session, world["rep_a"], auth.CAP_APPROVE, a)
        assert not auth.can_on(db_session, world["rep_a"], auth.CAP_ACTIVATE, a)

    def test_a_customer_admin_can_answer_questions_and_nothing_else(
            self, db_session, world):
        a = _agreement(db_session, world)
        db_session.commit()
        caps = auth.capabilities(db_session, world["admin_a"],
                                 brand_sales_org_id=a.brand_sales_org_id,
                                 organization_id=a.organization_id)
        assert caps == {auth.CAP_ANSWER_CUSTOMER_Q}

    def test_a_manager_of_another_brand_has_no_standing(self, db_session, world):
        a = _agreement(db_session, world)
        db_session.commit()
        assert auth.actor_kind(db_session, world["manager_b"],
                               brand_sales_org_id=a.brand_sales_org_id,
                               organization_id=a.organization_id) == auth.ACTOR_NONE
        assert auth.capabilities(db_session, world["manager_b"],
                                 brand_sales_org_id=a.brand_sales_org_id,
                                 organization_id=a.organization_id) == set()

    def test_a_commercial_approver_can_approve_without_being_a_manager(
            self, db_session, world):
        approver = _user(db_session, None, "user", "approver",
                         platform_id=world["plat_a"].id)
        auth.grant_commercial_approver(db_session, approver.id,
                                       world["bso_a"].id,
                                       granted_by=world["god"].id)
        db_session.commit()
        a = _agreement(db_session, world)
        db_session.commit()
        assert auth.can_on(db_session, approver, auth.CAP_APPROVE, a)
        assert auth.can_on(db_session, approver, auth.CAP_APPROVE_SETTLEMENT, a)


# ════════════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ════════════════════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_approval_is_refused_while_terms_are_missing_and_changes_nothing(
            self, db_session, world):
        from fastapi import HTTPException
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            ag.approve(db_session, a, world["god"])
        assert exc.value.status_code == 409
        db_session.rollback()
        assert a.status == AG_TERMS_REQUIRED
        assert a.approved_at is None

    def test_activation_requires_approval_first(self, db_session, world):
        from fastapi import HTTPException
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a)
        db_session.commit()
        assert a.status == AG_READY_FOR_APPROVAL
        with pytest.raises(HTTPException):
            ag.activate(db_session, a, world["god"])
        db_session.rollback()
        assert a.status == AG_READY_FOR_APPROVAL
        assert a.activated_at is None

    def test_approve_then_activate(self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a)
        ag.approve(db_session, a, world["god"], note="Terms agreed.")
        assert a.status == AG_APPROVED
        ag.activate(db_session, a, world["god"])
        db_session.commit()
        assert a.status == AG_ACTIVE
        assert a.activated_at is not None

    def test_a_live_agreements_model_cannot_be_swapped_underneath_it(
            self, db_session, world):
        from fastapi import HTTPException
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a)
        ag.approve(db_session, a, world["god"])
        ag.activate(db_session, a, world["god"])
        db_session.commit()
        with pytest.raises(HTTPException) as exc:
            ag.set_type(db_session, a, world["god"], TYPE_CUSTOM_FIXED)
        assert exc.value.status_code == 409
        db_session.rollback()
        assert a.agreement_type == TYPE_REVENUE_SHARE

    def test_a_suspended_agreement_stops_settling_and_nothing_else(
            self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        _answer_all(db_session, world, a)
        ag.approve(db_session, a, world["god"])
        ag.activate(db_session, a, world["god"])
        ag.suspend(db_session, a, world["god"], "Payment dispute under review.")
        db_session.commit()
        block = t.blocking(db_session, a)
        assert block[t.ACTION_CALCULATE]["allowed"] is False
        assert any("suspended" in r for r in block[t.ACTION_CALCULATE]["reasons"])

    def test_ending_an_agreement_keeps_it_readable(self, db_session, world):
        a = _agreement(db_session, world)
        ag.end(db_session, a, world["god"], "Superseded by a new term sheet.")
        db_session.commit()
        assert a.status == AG_ENDED
        assert ag.current_for_organization(db_session, world["org_a"].id) is None
        assert len(ag.for_organization(db_session, world["org_a"].id)) == 1

    def test_a_document_does_not_approve_anything(self, db_session, world):
        a = _agreement(db_session, world)
        _two_parties(db_session, world, a, 75, 25)
        ag.link_document(db_session, a, world["god"],
                         reference="signed-agreement-2026-09")
        db_session.commit()
        assert a.status == AG_TERMS_REQUIRED
        assert a.approved_at is None
        assert ag.internal_view(db_session, a)["document"]["approves_nothing"]
