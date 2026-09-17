"""
SS1 + SS2 — a Custom plan must not silently mean unlimited.

TWO DEFECTS THAT COMPOUND.

SS2: the Custom tier carries NULL ceilings, and on every other tier a NULL
means "the brand's published rate card says uncapped". So a Custom customer
resolved to unlimited on every dimension, `entitlement_state` reported
"On the enterprise tier; its published ceilings apply", and nobody found out
until they were running 40,000 leads on a deal that agreed 5,000. The code's
own docstring warned about exactly this.

SS1: the one supported way to record what a deal agreed - a
CustomerEntitlementSnapshot, written by PUT /god/billing/customers/{id}/
entitlements - was consulted ONLY when the organization sat on no catalogue
tier. For everyone else it was stored and then ignored. A negotiated ceiling
the platform keeps and does not enforce is worse than one it never took.
"""

import json
from datetime import datetime

import pytest

from app.models.billing_models import BrandBillingPlan, CustomerEntitlementSnapshot
from app.models.models import Organization, Platform
from app.services import plan_limits


def _platform(db_session, slug="cust-brand"):
    p = Platform(name="Custom Brand", slug=slug)
    db_session.add(p); db_session.commit(); return p


def _plans(db_session, platform):
    """An entry tier, a middle tier, and the policy-governed Custom tier."""
    rows = [
        BrandBillingPlan(platform_id=platform.id, key="starter", name="Starter",
                         max_leads=2500, max_users=1, is_purchasable=True,
                         is_active=True, currency="usd", sort_order=10),
        BrandBillingPlan(platform_id=platform.id, key="growth", name="Growth",
                         max_leads=5000, max_users=3, is_purchasable=True,
                         is_active=True, currency="usd", sort_order=20),
        BrandBillingPlan(platform_id=platform.id, key="enterprise", name="Custom",
                         max_leads=None, max_users=None, is_purchasable=False,
                         requires_entitlement_policy=True,
                         is_active=True, currency="usd", sort_order=40),
    ]
    db_session.add_all(rows); db_session.commit(); return rows


def _org(db_session, platform, key, slug="cust-org"):
    org = Organization(name="Custom Co", slug=slug, plan=key,
                       billing_plan_key=key, platform_id=platform.id)
    db_session.add(org); db_session.commit(); return org


def _snapshot(db_session, org, **limits):
    unlimited = limits.pop("unlimited", None)
    snap = CustomerEntitlementSnapshot(
        organization_id=org.id,
        unlimited_json=json.dumps(unlimited) if unlimited else None,
        **limits)
    db_session.add(snap); db_session.commit(); return snap


# ── SS2 ─────────────────────────────────────────────────────────────────────

def test_an_unconfigured_custom_org_is_not_unlimited(db_session):
    """The defect, stated as plainly as it can be."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "enterprise")

    ceiling = plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS)
    assert ceiling is not None, "an unrecorded Custom deal must not mean unlimited"
    assert ceiling == 2500, "it falls back to the brand's entry tier"


def test_an_unconfigured_custom_org_is_never_blocked_at_zero(db_session):
    """An unconfigured deal is the platform's mistake, not the customer's."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "enterprise")
    for key in (plan_limits.LIMIT_LEADS, plan_limits.LIMIT_USERS):
        value = plan_limits.limit_for(db_session, org, key)
        assert value is None or value > 0


def test_a_configured_custom_org_gets_exactly_what_the_deal_agreed(db_session):
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "enterprise")
    _snapshot(db_session, org, max_leads=7500, max_users=12)

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 7500
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 12


def test_a_custom_deal_may_agree_unlimited_but_it_has_to_say_so(db_session):
    """Uncapped stays possible - it just has to be a decision somebody made,
    named in unlimited_json, rather than the accident of a NULL column."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "enterprise")
    _snapshot(db_session, org, max_users=5, unlimited=["max_leads"])

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) is None
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 5


def test_the_state_report_calls_an_unconfigured_custom_deal_what_it_is(db_session):
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "enterprise")

    state = plan_limits.entitlement_state(db_session, org)
    assert state["policy_required"] is True
    assert "max_leads" in state["unset_dimensions"]
    assert state["agreed_unlimited"] == []
    assert "NEEDS CONFIGURATION" in state["explanation"]


def test_the_state_report_stops_claiming_published_ceilings_apply(db_session):
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "enterprise")
    state = plan_limits.entitlement_state(db_session, org)
    assert "published ceilings apply" not in state["explanation"]


def test_a_published_tier_keeps_meaning_unlimited_on_a_null(db_session):
    """The fix must not change what a NULL means on a rate card."""
    platform = _platform(db_session)
    db_session.add(BrandBillingPlan(
        platform_id=platform.id, key="unlimited_tier", name="All In",
        max_leads=None, max_users=None, is_purchasable=True, is_active=True,
        currency="usd", sort_order=50))
    db_session.commit()
    org = _org(db_session, platform, "unlimited_tier", slug="unl-org")

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) is None
    state = plan_limits.entitlement_state(db_session, org)
    assert state["policy_required"] is False
    assert "max_leads" in state["agreed_unlimited"]


# ── SS1 ─────────────────────────────────────────────────────────────────────

def test_a_snapshot_now_overrides_a_tier_instead_of_being_ignored(db_session):
    """The whole of SS1. This was stored and never read for any org with a
    plan key, which is nearly all of them."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "growth")
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 5000

    _snapshot(db_session, org, max_leads=25000)
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 25000


def test_the_override_is_per_dimension(db_session):
    """A deal that raised the lead ceiling and said nothing about seats leaves
    seats on the tier, because that is what it said."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "growth")
    _snapshot(db_session, org, max_leads=25000)

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 25000
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 3


def test_a_superseded_snapshot_does_not_override_anything(db_session):
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "growth")
    old = _snapshot(db_session, org, max_leads=25000)
    old.superseded_at = datetime.utcnow()
    db_session.commit()

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 5000


def test_the_reported_ceiling_agrees_with_the_enforced_one(db_session):
    """They disagreed: entitlement_state read the tier while limit_for read the
    snapshot, so the support screen and the enforcement said different things."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = _org(db_session, platform, "growth")
    _snapshot(db_session, org, max_leads=25000)

    state = plan_limits.entitlement_state(db_session, org)
    assert state["limits"]["max_leads"] == 25000
    assert state["effective_limits"]["max_leads"] == \
        plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS)


def test_an_org_on_no_tier_with_a_snapshot_still_works(db_session):
    """The original Custom shape - no plan key, snapshot only - is unchanged."""
    platform = _platform(db_session)
    _plans(db_session, platform)
    org = Organization(name="Deal Co", slug="deal-co", platform_id=platform.id)
    db_session.add(org); db_session.commit()
    _snapshot(db_session, org, max_leads=9000)
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 9000
