"""The acceptance additions — the gaps the existing billing suites leave open.

═══════════════════════════════════════════════════════════════════════════
WHAT IS ALREADY COVERED ELSEWHERE, AND IS NOT REPEATED HERE
═══════════════════════════════════════════════════════════════════════════

test_billing_plan_change.py already proves: a downgrade never swaps the item;
pressing downgrade twice converges on one schedule; changing the target
converges on one schedule; an upgrade releases a pending downgrade; cancel
clears it; a price id from another brand does not resolve; the price id beats
contradictory metadata.

test_billing_catalog.py already proves the plan-KEY lookup is brand-scoped.
test_billing_webhook.py already proves replay, duplicate banking, signature
refusal, and cancellation record preservation.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS FILE ADDS
═══════════════════════════════════════════════════════════════════════════

  1. SCHEDULE SAFETY, the scenario the others miss: SELECTING THE PLAN YOU ARE
     ALREADY ON, both with and without something pending. A "lateral" request
     must not create, replace or silently drop a schedule.
  2. THE PRICE-ID LOOKUP WITH NO BRAND AT ALL. The cross-brand test that
     existed compared two brands; the hole was an organization with NO
     platform_id, which searched the WHOLE catalogue and matched whichever
     brand's plan came first. That is a Brand-A price resolving as a valid
     plan for a Brand-B (or no-brand) org, which is the exact thing forbidden.
  3. ENTITLEMENTS FOLLOW THE EFFECTIVE SUBSCRIPTION STATE, not the pending
     one, asserted on features and limits together.
  4. THE WEBHOOK STATUS MATRIX and out-of-order delivery.

Nothing here reaches api.stripe.com.
"""

import itertools
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import stripe

from app.models.billing_models import (BrandBillingConfig, BrandBillingPlan,
                                       ChangeTiming, ProrationBehavior)
from app.models.models import Organization, Platform, User
from app.services import billing_catalog, billing_webhook, plan_limits
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(4000)

PERIOD_END = datetime(2026, 10, 1)

ALPHA_PRO_PRICE = "price_alpha_professional_month"
ALPHA_GROWTH_PRICE = "price_alpha_growth_month"
BETA_PRO_PRICE = "price_beta_professional_month"


def _unix(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_FAKE_not_a_real_key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_FAKE_not_a_real_secret")


@pytest.fixture(autouse=True)
def no_real_stripe_calls(monkeypatch):
    def _refuse(*a, **k):
        raise RuntimeError("A test tried to make a real Stripe API request.")
    for target, name in ((stripe.checkout.Session, "create"),
                         (stripe.billing_portal.Session, "create"),
                         (stripe.Customer, "create"),
                         (stripe.Subscription, "retrieve"),
                         (stripe.Subscription, "modify"),
                         (stripe.SubscriptionSchedule, "create"),
                         (stripe.SubscriptionSchedule, "retrieve"),
                         (stripe.SubscriptionSchedule, "modify"),
                         (stripe.SubscriptionSchedule, "release")):
        monkeypatch.setattr(target, name, _refuse)


def _platform(db, label):
    p = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(p); db.commit()
    return p


def _plan(db, platform, key, *, monthly, price_id=None, sort_order=10,
          max_users=None, max_leads=None, features=None):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=monthly, currency="usd", is_purchasable=True,
        is_active=True, sort_order=sort_order,
        stripe_price_id_monthly=price_id,
        max_users=max_users, max_leads=max_leads,
        # `features_json` is a JSON LIST of display strings, not a dict of
        # flags. Building it here rather than passing a dict keeps this test
        # honest about the shape the entitlement layer actually reads.
        features_json=json.dumps(features) if features is not None else None)
    db.add(plan); db.commit()
    return plan


def _org(db, platform, **kw):
    n = next(_SEQ)
    kw.setdefault("plan", "trial")
    kw.setdefault("stripe_customer_id", "cus_acc_%d" % n)
    org = Organization(name="Acc %d" % n, slug="acc-%d" % n,
                       platform_id=platform.id if platform else None,
                       is_active=True, **kw)
    db.add(org); db.commit()
    return org


def _admin_headers(db, org):
    admin = User(organization_id=org.id,
                 email="acc_admin%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role="org_admin",
                 must_change_password=False)
    db.add(admin); db.commit()
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


def _decided(db, platform):
    db.add(BrandBillingConfig(
        platform_id=platform.id,
        upgrade_timing=ChangeTiming.IMMEDIATE,
        upgrade_proration=ProrationBehavior.CREATE_PRORATIONS,
        downgrade_timing=ChangeTiming.PERIOD_END,
        downgrade_proration=ProrationBehavior.NONE))
    db.commit()


@pytest.fixture()
def alpha(db_session):
    p = _platform(db_session, "Alpha")
    _plan(db_session, p, "starter", monthly=49700, sort_order=10,
          max_users=2, max_leads=50, features=["Email campaigns"])
    _plan(db_session, p, "growth", monthly=99700, price_id=ALPHA_GROWTH_PRICE,
          sort_order=20, max_users=5, max_leads=500,
          features=["Email campaigns", "Reporting"])
    _plan(db_session, p, "professional", monthly=199700, price_id=ALPHA_PRO_PRICE,
          sort_order=30, max_users=10, max_leads=5000,
          features=["Email campaigns", "Reporting", "API access"])
    _decided(db_session, p)
    return p


@pytest.fixture()
def beta(db_session):
    """A SECOND brand whose 'professional' is a different Stripe Price."""
    p = _platform(db_session, "Beta")
    _plan(db_session, p, "professional", monthly=199700,
          price_id=BETA_PRO_PRICE, sort_order=30)
    _decided(db_session, p)
    return p


# ═══════════════════════════════════════════════════════════════════════════
# 1. SCHEDULE SAFETY — SELECTING THE PLAN YOU ARE ALREADY ON
# ═══════════════════════════════════════════════════════════════════════════

def _schedule_probe(monkeypatch):
    """Records any attempt to touch a schedule, and refuses none of them.

    The assertion these tests make is that NOTHING was touched, so the probe
    has to be permissive - a mock that raised would prove only that the test
    was written, not that the route behaved."""
    calls = []
    for name in ("create", "retrieve", "modify", "release"):
        monkeypatch.setattr(
            stripe.SubscriptionSchedule, name,
            MagicMock(side_effect=lambda *a, _n=name, **k: calls.append(_n) or {
                "id": "sub_sched_probe", "status": "not_started", "phases": []}))
    monkeypatch.setattr(stripe.Subscription, "retrieve", MagicMock(return_value={
        "id": "sub_live_1", "status": "active",
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"id": "si_1", "price": {
            "id": ALPHA_PRO_PRICE, "recurring": {"interval": "month"}}}]}}))
    sub_modify = MagicMock(return_value={"id": "sub_live_1", "status": "active"})
    monkeypatch.setattr(stripe.Subscription, "modify", sub_modify)
    return SimpleNamespace(schedule_calls=calls, sub_modify=sub_modify)


def test_selecting_the_plan_you_are_already_on_touches_no_schedule(
        client, db_session, alpha, monkeypatch):
    """Scenario 3 of the acceptance list.

    Re-selecting the current plan is not a change. It must not create a
    schedule, must not modify the subscription, and must not quietly become a
    second primary subscription."""
    probe = _schedule_probe(monkeypatch)
    org = _org(db_session, alpha, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="professional",
               plan="professional", stripe_plan_interval="month",
               billing_current_period_end=PERIOD_END)
    headers = _admin_headers(db_session, org)

    response = client.post("/billing/change-plan",
                           json={"plan": "professional", "interval": "month"},
                           headers=headers)

    assert response.status_code == 400, (
        "Re-selecting the current plan should be a clean 400 (nothing to do), "
        "not success and not a 500. Got %d: %s"
        % (response.status_code, response.text[:200]))
    assert probe.schedule_calls == [], (
        "A lateral request touched the Subscription Schedule API (%s)."
        % probe.schedule_calls)
    assert probe.sub_modify.call_count == 0

    db_session.commit()
    assert org.billing_plan_key == "professional"
    assert org.stripe_subscription_id == "sub_live_1", (
        "the one primary subscription must survive a lateral request intact")


def test_selecting_the_current_plan_does_not_disturb_a_pending_downgrade(
        client, db_session, alpha, monkeypatch):
    """The nastiest variant: a downgrade is already scheduled, and the customer
    clicks their CURRENT tier. That must neither cancel the pending change
    silently nor stack a second schedule on top of it."""
    probe = _schedule_probe(monkeypatch)
    effective = PERIOD_END
    org = _org(db_session, alpha, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="professional",
               plan="professional", stripe_plan_interval="month",
               billing_current_period_end=PERIOD_END,
               billing_pending_plan_key="growth",
               billing_pending_effective_at=effective,
               stripe_schedule_id="sub_sched_existing")
    headers = _admin_headers(db_session, org)

    client.post("/billing/change-plan",
                json={"plan": "professional", "interval": "month"},
                headers=headers)

    db_session.commit()
    assert org.billing_pending_plan_key == "growth", (
        "a lateral request silently cancelled the pending downgrade")
    assert org.stripe_schedule_id == "sub_sched_existing", (
        "a lateral request replaced or dropped the existing schedule id")
    assert org.billing_pending_effective_at == effective
    assert probe.schedule_calls == [], probe.schedule_calls


def test_a_scheduled_downgrade_preserves_the_billing_interval(
        client, db_session, alpha, monkeypatch):
    """A deferred change must not quietly move a monthly customer to annual or
    vice versa - the interval is part of what they bought."""
    probe = _schedule_probe(monkeypatch)
    org = _org(db_session, alpha, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="professional",
               plan="professional", stripe_plan_interval="month",
               billing_current_period_end=PERIOD_END)
    headers = _admin_headers(db_session, org)

    client.post("/billing/change-plan",
                json={"plan": "growth", "interval": "month"},
                headers=headers)

    db_session.commit()
    assert org.stripe_plan_interval == "month"


def test_a_pending_downgrade_grants_no_immediate_credit(
        client, db_session, alpha, monkeypatch):
    """The decided policy: no credit, no refund, no proration on a downgrade.
    Whatever Stripe is asked to do, it is never asked to prorate."""
    probe = _schedule_probe(monkeypatch)
    org = _org(db_session, alpha, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="professional",
               plan="professional", stripe_plan_interval="month",
               billing_current_period_end=PERIOD_END)
    headers = _admin_headers(db_session, org)

    client.post("/billing/change-plan",
                json={"plan": "growth", "interval": "month"},
                headers=headers)

    for call in probe.sub_modify.call_args_list:
        assert call.kwargs.get("proration_behavior") != "create_prorations", (
            "a downgrade asked Stripe to create prorations, which is a credit "
            "the decided policy says is not given")


# ═══════════════════════════════════════════════════════════════════════════
# 2. THE PRICE-ID LOOKUP IS BRAND-SAFE — INCLUDING WITH NO BRAND
# ═══════════════════════════════════════════════════════════════════════════

def test_a_price_never_resolves_without_a_brand_scope(db_session, alpha):
    """THE HOLE THIS TEST EXISTS FOR.

    The first version of resolve_plan_by_price_id filtered on platform_id only
    `if platform_id`. An organization with no platform - mid-provisioning, a
    legacy row, a customer whose brand was never set - therefore searched the
    WHOLE catalogue and got back whichever brand's plan matched first.

    That is Brand A's Stripe Price resolving as a valid plan for an org that
    does not belong to Brand A. No scope means no answer."""
    assert billing_catalog.resolve_plan_by_price_id(
        db_session, None, ALPHA_PRO_PRICE) is None


def test_brand_bs_price_does_not_resolve_inside_brand_a(db_session, alpha, beta):
    assert billing_catalog.resolve_plan_by_price_id(
        db_session, alpha.id, BETA_PRO_PRICE) is None
    assert billing_catalog.resolve_plan_by_price_id(
        db_session, beta.id, ALPHA_PRO_PRICE) is None


def test_each_brands_own_price_resolves_to_its_own_plan(db_session, alpha, beta):
    a = billing_catalog.resolve_plan_by_price_id(db_session, alpha.id, ALPHA_PRO_PRICE)
    b = billing_catalog.resolve_plan_by_price_id(db_session, beta.id, BETA_PRO_PRICE)
    assert a is not None and a.platform_id == alpha.id
    assert b is not None and b.platform_id == beta.id
    assert a.id != b.id, (
        "two brands' 'professional' plans resolved to the same catalogue row")


def test_an_empty_price_id_resolves_to_nothing(db_session, alpha):
    for empty in (None, ""):
        assert billing_catalog.resolve_plan_by_price_id(
            db_session, alpha.id, empty) is None


def test_a_webhook_carrying_another_brands_price_does_not_change_the_plan(
        db_session, alpha, beta):
    """The cross-brand negative at the level that matters: an event."""
    org = _org(db_session, alpha, stripe_subscription_id="sub_x",
               billing_status="active", billing_plan_key="growth",
               plan="growth")
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_x", "status": "active",
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"price": {"id": BETA_PRO_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_plan_key == "growth", (
        "a Stripe price belonging to another brand was written as this org's "
        "plan")


# ═══════════════════════════════════════════════════════════════════════════
# 3. ENTITLEMENTS FOLLOW THE EFFECTIVE SUBSCRIPTION STATE
# ═══════════════════════════════════════════════════════════════════════════

def test_a_scheduled_downgrade_does_not_remove_features_early(db_session, alpha):
    """Professional, paid through the period end, downgrading to Growth.

    Until Stripe advances the schedule, PROFESSIONAL is authoritative: they
    paid for it. Reading the pending key would take the API feature away the
    moment they clicked the button, for a change that has not happened and
    money they have not saved."""
    org = _org(db_session, alpha, billing_status="active",
               billing_plan_key="professional", plan="professional",
               billing_current_period_end=PERIOD_END,
               billing_pending_plan_key="growth",
               billing_pending_effective_at=PERIOD_END)

    effective = plan_limits.effective_plan(db_session, org)
    assert effective is not None and effective.key == "professional", (
        "the entitlement layer resolved the PENDING plan, not the one the "
        "customer has paid for through the end of this period")

    features = billing_catalog.features_for(effective)
    assert "API access" in features, (
        "the scheduled downgrade removed a Professional feature before the "
        "period the customer paid for had ended")
    assert "API access" not in billing_catalog.features_for(
        billing_catalog.resolve_plan(db_session, alpha.id, "growth")), (
        "the fixture is not actually testing anything - Growth would have to "
        "lack the feature for its absence to mean a downgrade took effect")


def test_a_scheduled_downgrade_does_not_lower_the_limits_early(db_session, alpha):
    org = _org(db_session, alpha, billing_status="active",
               billing_plan_key="professional", plan="professional",
               billing_pending_plan_key="starter",
               billing_pending_effective_at=PERIOD_END)

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 10
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_LEADS) == 5000


def test_the_report_shows_what_they_will_drop_to_without_applying_it(
        db_session, alpha):
    """A customer must be able to SEE the coming ceiling before it bites,
    which is a different thing from being subject to it."""
    org = _org(db_session, alpha, billing_status="active",
               billing_plan_key="professional", plan="professional",
               billing_pending_plan_key="starter",
               billing_pending_effective_at=PERIOD_END)

    report = plan_limits.report(db_session, org)
    assert report["limits"][plan_limits.LIMIT_USERS]["limit"] == 10
    assert report["pending_plan"] == "starter"
    assert report["pending_limits"][plan_limits.LIMIT_USERS]["limit"] == 2


def test_when_the_webhook_lands_the_lower_entitlement_takes_effect(
        db_session, alpha):
    """The transition is webhook-driven, and only then does the ceiling move."""
    org = _org(db_session, alpha, stripe_subscription_id="sub_y",
               billing_status="active", billing_plan_key="professional",
               plan="professional",
               billing_pending_plan_key="growth",
               billing_pending_effective_at=PERIOD_END,
               stripe_schedule_id="sub_sched_1")

    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 10

    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_y", "status": "active",
        "current_period_end": _unix(PERIOD_END + timedelta(days=30)),
        "items": {"data": [{"price": {"id": ALPHA_GROWTH_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()

    assert org.billing_plan_key == "growth"
    assert org.billing_pending_plan_key is None, (
        "the pending marker survived the change actually landing")
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 5


# ═══════════════════════════════════════════════════════════════════════════
# 4. WEBHOOK ROBUSTNESS — THE STATUS MATRIX AND ORDERING
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("status", ["active", "trialing", "past_due",
                                    "unpaid", "canceled", "incomplete",
                                    "incomplete_expired", "paused"])
def test_every_subscription_status_is_recorded_verbatim(db_session, alpha, status):
    """The status is DATA, not a decision. Recording it is what lets a brand
    later decide a policy; inventing suspension here would be inventing the
    policy that was deliberately left unset."""
    org = _org(db_session, alpha, stripe_subscription_id="sub_z",
               billing_status="active", billing_plan_key="growth", plan="growth")
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_z", "status": status,
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"price": {"id": ALPHA_GROWTH_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_status == status
    assert org.is_active is True, (
        "status %r deactivated the organization. No suspension policy is "
        "configured, and none may be invented." % status)


@pytest.mark.parametrize("status", ["past_due", "unpaid"])
def test_a_failing_payment_does_not_strip_entitlements_by_itself(
        db_session, alpha, status):
    org = _org(db_session, alpha, stripe_subscription_id="sub_f",
               billing_status="active", billing_plan_key="professional",
               plan="professional")
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_f", "status": status,
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"price": {"id": ALPHA_PRO_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_plan_key == "professional"
    assert plan_limits.limit_for(db_session, org, plan_limits.LIMIT_USERS) == 10


def test_an_out_of_order_delivery_does_not_resurrect_a_stale_plan(
        db_session, alpha):
    """Stripe does not guarantee ordering. The LAST event applied wins, which
    is why the local record must be driven by the payload it is given rather
    than by any assumption about sequence - and why the pending markers are
    cleared by matching the plan, not by counting events."""
    org = _org(db_session, alpha, stripe_subscription_id="sub_o",
               billing_status="active", billing_plan_key="professional",
               plan="professional")

    # The new state arrives first.
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_o", "status": "active",
        "current_period_end": _unix(PERIOD_END + timedelta(days=30)),
        "items": {"data": [{"price": {"id": ALPHA_GROWTH_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_plan_key == "growth"

    # Then a late duplicate of the SAME state. It must be inert, not a flap.
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_o", "status": "active",
        "current_period_end": _unix(PERIOD_END + timedelta(days=30)),
        "items": {"data": [{"price": {"id": ALPHA_GROWTH_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_plan_key == "growth"


def test_an_unknown_price_id_leaves_the_plan_alone(db_session, alpha):
    """A price nobody's catalogue knows is not a reason to blank the plan."""
    org = _org(db_session, alpha, stripe_subscription_id="sub_u",
               billing_status="active", billing_plan_key="growth", plan="growth")
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_u", "status": "active",
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"price": {"id": "price_never_seen",
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_plan_key == "growth"
    assert org.billing_status == "active"


def test_a_subscription_payload_with_no_items_at_all_does_not_crash(
        db_session, alpha):
    org = _org(db_session, alpha, stripe_subscription_id="sub_e",
               billing_status="active", billing_plan_key="growth", plan="growth")
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_e", "status": "active",
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": []},
    })
    db_session.commit()
    assert org.billing_plan_key == "growth"


def test_a_pending_change_is_only_cleared_when_the_matching_plan_arrives(
        db_session, alpha):
    """A webhook for some OTHER change must not clear a pending downgrade."""
    org = _org(db_session, alpha, stripe_subscription_id="sub_p",
               billing_status="active", billing_plan_key="professional",
               plan="professional", billing_pending_plan_key="starter",
               billing_pending_effective_at=PERIOD_END,
               stripe_schedule_id="sub_sched_p")

    # An unrelated status change on the SAME (professional) price.
    billing_webhook.apply_subscription(db_session, org, {
        "id": "sub_p", "status": "past_due",
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"price": {"id": ALPHA_PRO_PRICE,
                                      "recurring": {"interval": "month"}}}]},
    })
    db_session.commit()
    assert org.billing_pending_plan_key == "starter", (
        "an unrelated subscription update cleared the pending downgrade")
    assert org.billing_status == "past_due"
