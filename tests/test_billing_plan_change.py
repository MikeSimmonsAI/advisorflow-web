"""A DEFERRED DOWNGRADE IS A SUBSCRIPTION SCHEDULE, NEVER A MODIFY.

WHAT THIS FILE DEFENDS

  1. THE DEFECT NEVER COMES BACK. The first downgrade implementation called
     `Subscription.modify(proration_behavior="none",
     billing_cycle_anchor="unchanged")` believing it deferred the change. It
     does not. `billing_cycle_anchor: "unchanged"` only says "do not reset the
     billing period start"; THE ITEM SWAP IS IMMEDIATE. The customer lost the
     tier they had already paid for and, with proration "none", got no credit
     for it either - less product, same money. Test one is the guard.
  2. ONE SCHEDULE PER SUBSCRIPTION, EVER. Pressing "downgrade" twice, or
     changing the target, converges on ONE schedule rather than two phases
     disagreeing about what happens at the period boundary.
  3. THE CURRENT PLAN IS NOT TOUCHED BY A DOWNGRADE REQUEST. `billing_plan_key`
     is what the customer is entitled to today and they keep it until Stripe's
     schedule actually advances.
  4. THE PLAN COMES FROM THE PRICE ID. A schedule's second phase arrives as a
     `customer.subscription.updated` with a new price and no guarantee of our
     `plan` metadata, so price-first resolution is what makes a scheduled
     downgrade sync at all.

NOTHING HERE REACHES api.stripe.com. Every Stripe entry point on these paths is
refused by an autouse guard and re-installed as a recording mock by the tests
that need one.
"""

import itertools
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import stripe

from app.models.billing_models import (BrandBillingConfig, BrandBillingPlan,
                                       ChangeTiming, ProrationBehavior)
from app.models.models import Organization, Platform, User
from app.services import billing_webhook
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

FAKE_SECRET_KEY = "sk_test_FAKE_not_a_real_key"
FAKE_WEBHOOK_SECRET = "whsec_FAKE_not_a_real_secret"

# Stripe Price ids are public object identifiers, not secrets - but these are
# obvious fakes regardless, and nothing in this file may hold a real one.
PRICE = {
    "starter": "price_alpha_starter_month",
    "growth": "price_alpha_growth_month",
    "professional": "price_alpha_professional_month",
}

PERIOD_START = datetime(2026, 9, 1)
PERIOD_END = datetime(2026, 10, 1)


def _unix(dt):
    """Stripe sends UTC unix seconds.

    Built with an explicit UTC tzinfo rather than `dt.timestamp()`, which would
    read a naive datetime in the MACHINE's local zone and make these
    assertions pass or fail depending on where the suite is run.
    """
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


class _FakeSchedules:
    """The slice of Stripe's Subscription Schedule API this path uses.

    STATEFUL ON PURPOSE. "Pressing downgrade twice converges on ONE schedule"
    is a claim about what Stripe still holds when the second request arrives,
    and a stateless MagicMock cannot express it - the second request has to be
    able to retrieve what the first one left behind.
    """

    def __init__(self):
        self.store = {}
        self._n = itertools.count(1)
        self.create = MagicMock(side_effect=self._create)
        self.retrieve = MagicMock(side_effect=self._retrieve)
        self.modify = MagicMock(side_effect=self._modify)
        self.release = MagicMock(side_effect=self._release)

    def only_id(self):
        """The single schedule this subscription has. Asserting through this
        is itself a check: if two were ever created it raises rather than
        quietly picking one."""
        assert len(self.store) == 1, (
            "Expected exactly one Subscription Schedule, found %d: %s. A "
            "second schedule on the same subscription means two phases "
            "disagreeing about what happens at the period boundary."
            % (len(self.store), sorted(self.store)))
        return next(iter(self.store))

    def seed(self, schedule_id, price_id=None):
        """A schedule that already exists, for the paths that release one."""
        self.store[schedule_id] = {
            "id": schedule_id,
            "status": "not_started",
            "phases": [{"start_date": _unix(PERIOD_START),
                        "end_date": _unix(PERIOD_END),
                        "items": [{"price": price_id or PRICE["professional"],
                                   "quantity": 1}]}],
        }
        return schedule_id

    def _create(self, **kwargs):
        schedule_id = "sub_sched_%d" % next(self._n)
        self.seed(schedule_id)
        self.store[schedule_id]["from_subscription"] = kwargs.get("from_subscription")
        return self.store[schedule_id]

    def _retrieve(self, schedule_id, **kwargs):
        if schedule_id not in self.store:
            raise stripe.error.InvalidRequestError(
                "No such subscription schedule: %s" % schedule_id, "id")
        return self.store[schedule_id]

    def _modify(self, schedule_id, **kwargs):
        schedule = self.store[schedule_id]
        phases = [dict(p) for p in (kwargs.get("phases") or [])]
        # Stripe derives a phase's start from the previous phase's end.
        for index, phase in enumerate(phases):
            if index and not phase.get("start_date"):
                phase["start_date"] = (phases[index - 1].get("end_date")
                                       or _unix(PERIOD_END))
        schedule["phases"] = phases
        schedule["end_behavior"] = kwargs.get("end_behavior")
        schedule["metadata"] = kwargs.get("metadata")
        return schedule

    def _release(self, schedule_id, **kwargs):
        if schedule_id not in self.store:
            raise stripe.error.InvalidRequestError(
                "No such subscription schedule: %s" % schedule_id, "id")
        self.store[schedule_id]["status"] = "released"
        return self.store[schedule_id]


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    """Obviously-fake credentials, set for every test on these paths."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", FAKE_SECRET_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", FAKE_WEBHOOK_SECRET)


@pytest.fixture(autouse=True)
def no_real_stripe_calls(monkeypatch):
    """THE SUITE MUST NEVER REACH api.stripe.com.

    Same reasoning as conftest's Twilio guard and test_billing_authorization's:
    a stale patch target that silently becomes a live HTTPS request is the
    worst failure mode available, because the only symptom is an assertion
    about a count. Any Stripe entry point a test has not deliberately mocked is
    refused here, by name.
    """
    def _refuse(*args, **kwargs):
        raise RuntimeError(
            "A test tried to make a real Stripe API request. Patch the exact "
            "entry point the route calls (stripe.Subscription.retrieve/modify, "
            "stripe.SubscriptionSchedule.create/retrieve/modify/release) "
            "instead - the `processor` fixture in this file does that.")

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


@pytest.fixture()
def processor(monkeypatch):
    """Every Stripe call these routes make, recording, and nothing else."""
    schedules = _FakeSchedules()
    sub_retrieve = MagicMock(return_value={
        "id": "sub_live_1",
        "status": "active",
        "current_period_end": _unix(PERIOD_END),
        "items": {"data": [{"id": "si_live_1",
                            "price": {"id": PRICE["professional"],
                                      "recurring": {"interval": "month"}}}]},
    })
    sub_modify = MagicMock(return_value={"id": "sub_live_1", "status": "active"})
    monkeypatch.setattr(stripe.Subscription, "retrieve", sub_retrieve)
    monkeypatch.setattr(stripe.Subscription, "modify", sub_modify)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "create", schedules.create)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "retrieve", schedules.retrieve)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "modify", schedules.modify)
    monkeypatch.setattr(stripe.SubscriptionSchedule, "release", schedules.release)
    return SimpleNamespace(schedules=schedules, sub_retrieve=sub_retrieve,
                           sub_modify=sub_modify)


# ── Fixtures: one brand, three mapped tiers, both directions decided ────────

def _platform(db, label="Alpha"):
    platform = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(platform)
    db.commit()
    return platform


def _plan(db, platform, key, *, monthly, price_id=None, sort_order=10,
          purchasable=True):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=monthly, currency="usd", is_purchasable=purchasable,
        is_active=True, sort_order=sort_order,
        stripe_price_id_monthly=price_id)
    db.add(plan)
    db.commit()
    return plan


def _org(db, platform, **kw):
    n = next(_SEQ)
    kw.setdefault("plan", "trial")
    kw.setdefault("stripe_customer_id", "cus_org_%d" % n)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=platform.id if platform else None,
                       is_active=True, **kw)
    db.add(org)
    db.commit()
    return org


def _admin_headers(db, org, role="org_admin"):
    admin = User(organization_id=org.id,
                 email="admin%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role=role, must_change_password=False)
    db.add(admin)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


def _decided(db, platform):
    """EvoSys's decided pair: upgrade immediate + prorated, downgrade at
    period end with no credit. Stored as brand configuration, not code."""
    db.add(BrandBillingConfig(
        platform_id=platform.id,
        upgrade_timing=ChangeTiming.IMMEDIATE,
        upgrade_proration=ProrationBehavior.CREATE_PRORATIONS,
        downgrade_timing=ChangeTiming.PERIOD_END,
        downgrade_proration=ProrationBehavior.NONE))
    db.commit()


@pytest.fixture()
def brand(db_session):
    platform = _platform(db_session, "Alpha")
    # "unmapped" is deliberately cheaper than professional AND has no Stripe
    # price id, so a downgrade to it is a real downgrade that cannot be
    # scheduled. That is the 409 case, not a 502.
    _plan(db_session, platform, "unmapped", monthly=29700, price_id=None,
          sort_order=5)
    _plan(db_session, platform, "starter", monthly=49700,
          price_id=PRICE["starter"], sort_order=10)
    _plan(db_session, platform, "growth", monthly=99700,
          price_id=PRICE["growth"], sort_order=20)
    _plan(db_session, platform, "professional", monthly=199700,
          price_id=PRICE["professional"], sort_order=30)
    _decided(db_session, platform)
    return platform


@pytest.fixture()
def subscriber(db_session, brand):
    """A live Professional customer, mid-period, with nothing pending."""
    return _org(db_session, brand, stripe_subscription_id="sub_live_1",
                billing_status="active", billing_plan_key="professional",
                plan="professional", stripe_plan_interval="month",
                billing_current_period_end=PERIOD_END)


def _change(client, headers, to, interval="month"):
    return client.post("/billing/change-plan",
                       json={"plan": to, "interval": interval},
                       headers=headers)


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE REGRESSION GUARD. A DOWNGRADE DOES NOT SWAP THE ITEM.
# ═══════════════════════════════════════════════════════════════════════════

IMMEDIATE_SWAP = (
    "The downgrade called stripe.Subscription.modify with a new price. THAT IS "
    "THE DEFECT THIS PATH WAS REWRITTEN TO REMOVE.\n\n"
    "billing_cycle_anchor='unchanged' does NOT defer an item swap. It says one "
    "thing only: 'do not reset the billing period start'. The ITEM SWAP TAKES "
    "EFFECT IMMEDIATELY - so the customer is moved to the lower plan the same "
    "second, having already paid for the higher one for the rest of the "
    "period, and proration_behavior='none' means they get no credit for the "
    "difference either. Less product, same money.\n\n"
    "A downgrade must go through billing_schedule.schedule_change_at_period_"
    "end(), which builds a two-phase Subscription Schedule: phase one is what "
    "they already paid for, phase two starts at the period boundary.")


def test_a_downgrade_does_not_modify_the_subscription_item(
        client, db_session, brand, subscriber, processor):
    """The exact defect, named. Subscription.modify is not called AT ALL on a
    downgrade; the schedule path is."""
    headers = _admin_headers(db_session, subscriber)
    response = _change(client, headers, "starter")

    assert response.status_code == 200, response.text
    assert processor.sub_modify.call_count == 0, IMMEDIATE_SWAP
    assert processor.schedules.create.call_count == 1, (
        "The downgrade did not create a Subscription Schedule. " + IMMEDIATE_SWAP)
    assert response.json()["timing"] == ChangeTiming.PERIOD_END
    assert response.json()["direction"] == "downgrade"


def test_the_scheduled_downgrade_has_two_phases_and_the_target_price_is_second(
        client, db_session, brand, subscriber, processor):
    """Phase one is what the customer already bought and is carried forward
    untouched; phase two is the plan they move to at the boundary."""
    headers = _admin_headers(db_session, subscriber)
    assert _change(client, headers, "starter").status_code == 200

    args, kwargs = processor.schedules.modify.call_args
    phases = kwargs["phases"]
    assert len(phases) == 2, (
        "A deferred change needs exactly two phases: what they paid for, then "
        "what they move to. Got %d." % len(phases))
    assert phases[0]["items"][0]["price"] == PRICE["professional"]
    assert phases[1]["items"][0]["price"] == PRICE["starter"]
    # The boundary between them is the end of the period already paid for.
    assert phases[0]["end_date"] == _unix(PERIOD_END)
    # And after phase two the subscription returns to ordinary billing.
    assert kwargs["end_behavior"] == "release"
    assert args[0] == processor.schedules.only_id()


def test_a_downgrade_records_the_pending_change_and_leaves_the_current_plan(
        client, db_session, brand, subscriber, processor):
    """THE CUSTOMER KEEPS WHAT THEY PAID FOR. `billing_plan_key` is what
    entitlements read, and a requested downgrade must not touch it - only the
    webhook, when Stripe's schedule actually advances, moves it."""
    headers = _admin_headers(db_session, subscriber)
    body = _change(client, headers, "starter").json()

    db_session.refresh(subscriber)
    assert subscriber.billing_pending_plan_key == "starter"
    assert subscriber.billing_pending_effective_at == PERIOD_END
    assert subscriber.stripe_schedule_id == processor.schedules.only_id()
    assert subscriber.billing_plan_key == "professional", (
        "The downgrade moved billing_plan_key immediately. The customer has "
        "paid for Professional through the end of this period and must keep it "
        "until the scheduled change actually lands.")
    assert subscriber.plan == "professional"
    assert body["current_plan"] == "professional"
    assert body["pending_plan"] == "starter"


# ═══════════════════════════════════════════════════════════════════════════
# 4-5. ONE SCHEDULE PER SUBSCRIPTION, EVER
# ═══════════════════════════════════════════════════════════════════════════

def test_asking_for_the_same_downgrade_twice_never_creates_a_second_schedule(
        client, db_session, brand, subscriber, processor):
    """A retry, a double click, or two browser tabs. Stripe refuses a second
    schedule on a subscription that already has one, and "the API errored" is
    not an answer a customer should get for pressing a button twice."""
    headers = _admin_headers(db_session, subscriber)

    first = _change(client, headers, "starter")
    db_session.refresh(subscriber)
    schedule_id = subscriber.stripe_schedule_id
    second = _change(client, headers, "starter")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert processor.schedules.create.call_count <= 1, (
        "SubscriptionSchedule.create was called %d times across two downgrade "
        "requests. The schedule id is stored on the organization precisely so "
        "the second request MODIFIES the existing schedule."
        % processor.schedules.create.call_count)
    assert processor.schedules.modify.call_count == 2
    db_session.refresh(subscriber)
    assert subscriber.stripe_schedule_id == schedule_id
    assert subscriber.billing_pending_plan_key == "starter"
    assert subscriber.billing_plan_key == "professional"


def test_changing_the_downgrade_target_converges_on_one_schedule(
        client, db_session, brand, subscriber, processor):
    """Professional -> Starter, then Professional -> Growth. The customer must
    end up with ONE schedule saying Growth, not two disagreeing about the
    period boundary."""
    headers = _admin_headers(db_session, subscriber)

    assert _change(client, headers, "starter").status_code == 200
    assert _change(client, headers, "growth").status_code == 200

    assert processor.schedules.create.call_count == 1
    schedule_id = processor.schedules.only_id()      # raises if there are two
    phases = processor.schedules.store[schedule_id]["phases"]
    assert len(phases) == 2
    assert phases[1]["items"][0]["price"] == PRICE["growth"], (
        "The surviving schedule's second phase is %r, not Growth. The last "
        "request the customer made is the one that must hold."
        % phases[1]["items"][0]["price"])
    db_session.refresh(subscriber)
    assert subscriber.stripe_schedule_id == schedule_id
    assert subscriber.billing_pending_plan_key == "growth"
    assert subscriber.billing_plan_key == "professional"


# ═══════════════════════════════════════════════════════════════════════════
# 6-7. AN UPGRADE IS IMMEDIATE, PRORATED, AND SUPERSEDES A PENDING DOWNGRADE
# ═══════════════════════════════════════════════════════════════════════════

def test_an_upgrade_modifies_the_subscription_with_prorations(
        client, db_session, brand, processor):
    """The customer asked for more and gets it now; Stripe charges the
    difference for the remainder of the period."""
    org = _org(db_session, brand, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="starter",
               plan="starter", stripe_plan_interval="month",
               billing_current_period_end=PERIOD_END)
    response = _change(client, _admin_headers(db_session, org), "professional")

    assert response.status_code == 200, response.text
    assert processor.sub_modify.call_count == 1
    kwargs = processor.sub_modify.call_args.kwargs
    assert kwargs["proration_behavior"] == ProrationBehavior.CREATE_PRORATIONS
    assert kwargs["items"] == [{"id": "si_live_1",
                                "price": PRICE["professional"]}]
    assert processor.schedules.create.call_count == 0, (
        "An upgrade created a Subscription Schedule. An upgrade applies "
        "immediately - there is nothing to defer.")
    body = response.json()
    assert body["direction"] == "upgrade"
    assert body["timing"] == ChangeTiming.IMMEDIATE
    assert body["effective"] == "immediately"
    # The webhook is still the authority on what Stripe actually did.
    db_session.refresh(org)
    assert org.billing_plan_key == "starter"


def test_an_upgrade_releases_a_pending_downgrade(
        client, db_session, brand, processor):
    """Downgrade on Monday, upgrade on Tuesday. They must not still drop a
    tier at month end because a schedule nobody cancelled was sitting there."""
    schedule_id = processor.schedules.seed("sub_sched_pending",
                                           price_id=PRICE["growth"])
    org = _org(db_session, brand, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="growth",
               plan="growth", stripe_plan_interval="month",
               billing_current_period_end=PERIOD_END,
               stripe_schedule_id=schedule_id,
               billing_pending_plan_key="starter",
               billing_pending_effective_at=PERIOD_END)

    response = _change(client, _admin_headers(db_session, org), "professional")

    assert response.status_code == 200, response.text
    processor.schedules.release.assert_called_once_with(schedule_id)
    db_session.refresh(org)
    assert org.stripe_schedule_id is None
    assert org.billing_pending_plan_key is None
    assert org.billing_pending_effective_at is None
    assert response.json()["pending_plan"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 8. CANCELLING A PENDING CHANGE
# ═══════════════════════════════════════════════════════════════════════════

def test_cancel_pending_change_releases_the_schedule_and_clears_the_markers(
        client, db_session, brand, processor):
    """Somebody who changed their mind should not have to upgrade and
    re-downgrade to undo it. Nothing is charged and nothing is refunded,
    because nothing has happened yet."""
    schedule_id = processor.schedules.seed("sub_sched_to_cancel")
    org = _org(db_session, brand, stripe_subscription_id="sub_live_1",
               billing_status="active", billing_plan_key="professional",
               plan="professional", stripe_plan_interval="month",
               stripe_schedule_id=schedule_id,
               billing_pending_plan_key="starter",
               billing_pending_effective_at=PERIOD_END)

    response = client.post("/billing/cancel-pending-change",
                           headers=_admin_headers(db_session, org))

    assert response.status_code == 200, response.text
    processor.schedules.release.assert_called_once_with(schedule_id)
    body = response.json()
    assert body["cancelled_pending_plan"] == "starter"
    assert body["current_plan"] == "professional"
    db_session.refresh(org)
    assert org.stripe_schedule_id is None
    assert org.billing_pending_plan_key is None
    assert org.billing_pending_effective_at is None
    # The plan they are actually on is untouched by cancelling the change.
    assert org.billing_plan_key == "professional"


def test_cancel_pending_change_is_409_when_nothing_is_pending(
        client, db_session, brand, subscriber, processor):
    response = client.post("/billing/cancel-pending-change",
                           headers=_admin_headers(db_session, subscriber))
    assert response.status_code == 409
    assert "no pending plan change" in response.json()["detail"].lower()
    assert processor.schedules.release.call_count == 0


def test_cancel_pending_change_requires_an_admin(
        client, db_session, brand, subscriber, auth_headers):
    assert client.post("/billing/cancel-pending-change").status_code == 401
    assert client.post("/billing/cancel-pending-change",
                       headers=auth_headers).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 9. A PLAN WITH NO STRIPE PRICE CANNOT BE SCHEDULED - AND SAYS SO
# ═══════════════════════════════════════════════════════════════════════════

def test_a_downgrade_to_an_unmapped_plan_is_refused_with_409_naming_the_cause(
        client, db_session, brand, subscriber, processor):
    """A schedule phase must name a real Stripe Price - it will not accept an
    ad-hoc price_data block the way Subscription.modify will. 409 rather than
    502 because the processor is not at fault: an administrator can fix this,
    and the message has to say what to fix."""
    response = _change(client, _admin_headers(db_session, subscriber),
                       "unmapped")

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "Stripe Price" in detail, detail
    assert "billing period" in detail.lower(), detail
    # The refusal happens before the processor is called at all.
    assert processor.schedules.create.call_count == 0
    assert processor.sub_modify.call_count == 0
    db_session.refresh(subscriber)
    assert subscriber.billing_pending_plan_key is None
    assert subscriber.stripe_schedule_id is None
    assert subscriber.billing_plan_key == "professional"


# ═══════════════════════════════════════════════════════════════════════════
# 10-13. WHO MAY CHANGE A PLAN, WHOSE PLAN CHANGES, AND WHAT IS REFUSED
# ═══════════════════════════════════════════════════════════════════════════

def test_changing_a_plan_requires_an_authenticated_admin(
        client, db_session, brand, subscriber, auth_headers, processor):
    """Spending decisions belong to the person who can make them."""
    anonymous = client.post("/billing/change-plan", json={"plan": "starter"})
    assert anonymous.status_code == 401
    advisor = _change(client, auth_headers, "starter")
    assert advisor.status_code == 403
    assert processor.schedules.create.call_count == 0
    assert processor.sub_modify.call_count == 0
    db_session.refresh(subscriber)
    assert subscriber.billing_pending_plan_key is None


def test_a_plan_change_acts_only_on_the_callers_own_organization(
        client, db_session, brand, subscriber, processor):
    """Two customers of the same brand. The organization comes from the token
    and there is no parameter that could point it at the other one."""
    neighbour = _org(db_session, brand, stripe_subscription_id="sub_neighbour",
                     billing_status="active", billing_plan_key="professional",
                     plan="professional", stripe_plan_interval="month",
                     billing_current_period_end=PERIOD_END)

    response = _change(client, _admin_headers(db_session, subscriber), "starter")
    assert response.status_code == 200, response.text

    # The subscription read and scheduled was the caller's own.
    processor.sub_retrieve.assert_called_once_with("sub_live_1")
    assert processor.schedules.create.call_args.kwargs["from_subscription"] == "sub_live_1"

    db_session.refresh(neighbour)
    assert neighbour.billing_pending_plan_key is None
    assert neighbour.billing_pending_effective_at is None
    assert neighbour.stripe_schedule_id is None
    assert neighbour.billing_plan_key == "professional"


def test_a_lateral_move_is_refused_before_the_processor_is_called(
        client, db_session, brand, subscriber, processor):
    """Same plan, same interval. There is nothing to do and a no-op should not
    be sent to Stripe."""
    response = _change(client, _admin_headers(db_session, subscriber),
                       "professional")
    assert response.status_code == 400
    assert processor.schedules.create.call_count == 0
    assert processor.sub_modify.call_count == 0


def test_a_downgrade_is_refused_when_the_brand_has_not_decided_the_direction(
        client, db_session, processor):
    """An unset policy means DO NOTHING, never a default. Applying a plan
    change on a guessed schedule either bills somebody early or hands them a
    tier they have not paid for."""
    undecided = _platform(db_session, "Beta")
    _plan(db_session, undecided, "starter", monthly=49700,
          price_id="price_beta_starter_month")
    _plan(db_session, undecided, "professional", monthly=199700,
          price_id="price_beta_professional_month")
    org = _org(db_session, undecided, stripe_subscription_id="sub_beta_1",
               billing_status="active", billing_plan_key="professional",
               plan="professional", stripe_plan_interval="month")

    response = _change(client, _admin_headers(db_session, org), "starter")

    assert response.status_code == 409, response.text
    assert "downgrade" in response.json()["detail"]
    assert processor.schedules.create.call_count == 0
    db_session.refresh(org)
    assert org.billing_pending_plan_key is None
    assert org.billing_plan_key == "professional"


# ═══════════════════════════════════════════════════════════════════════════
# 14-17. THE WEBHOOK SIDE — THE PRICE ID IS WHAT MAKES A SCHEDULE SYNC
# ═══════════════════════════════════════════════════════════════════════════

def _subscription(org, price_id, *, metadata=None, interval="month",
                  status="active"):
    """The shape Stripe sends, trimmed to what apply_subscription reads.

    `metadata` defaults to ABSENT ENTIRELY, which is the case that matters: a
    subscription arriving on a schedule's second phase is not guaranteed to
    carry our `plan` key at all.
    """
    return {
        "id": org.stripe_subscription_id or "sub_live_1",
        "object": "subscription",
        "customer": org.stripe_customer_id,
        "status": status,
        "cancel_at_period_end": False,
        "current_period_end": _unix(PERIOD_END),
        "trial_end": None,
        "items": {"data": [{"id": "si_live_1",
                            "price": {"id": price_id,
                                      "recurring": {"interval": interval}}}]},
        "metadata": {} if metadata is None else metadata,
    }


def test_the_plan_is_resolved_from_the_price_id_when_metadata_is_absent(
        db_session, brand, subscriber):
    """THIS IS WHAT MAKES A SCHEDULED DOWNGRADE SYNC AT ALL. When a schedule
    advances to phase two, the item's price always changes; our metadata is
    not guaranteed to be there. Metadata-only resolution left the customer
    paying the lower price while every screen showed the higher plan."""
    sub = _subscription(subscriber, PRICE["growth"])
    assert "plan" not in sub["metadata"]

    billing_webhook.apply_subscription(db_session, subscriber, sub)

    assert subscriber.billing_plan_key == "growth", (
        "The plan was not resolved from the Stripe price id. A price id is "
        "created by us, mapped in the brand's own catalogue, and is what the "
        "customer is actually charged against - it is the durable identifier, "
        "and metadata is only the fallback.")
    assert subscriber.plan == "growth"
    assert subscriber.stripe_plan_interval == "month"


def test_the_price_id_wins_over_contradictory_metadata(
        db_session, brand, subscriber):
    """A metadata string can be edited into something else from the Stripe
    dashboard. A price id cannot."""
    sub = _subscription(subscriber, PRICE["starter"],
                        metadata={"plan": "professional"})

    billing_webhook.apply_subscription(db_session, subscriber, sub)

    assert subscriber.billing_plan_key == "starter", (
        "Metadata overrode the price id. The customer is being charged the "
        "Starter price; the plan written locally must be the one the money "
        "actually corresponds to.")


def test_a_price_id_belonging_to_another_brand_does_not_resolve(
        db_session, brand, subscriber):
    """The platform_id filter on the lookup is not decorative. Two brands
    could map the same Stripe price, and a webhook must resolve it to the plan
    belonging to the organization being updated."""
    other = _platform(db_session, "Beta")
    _plan(db_session, other, "beta-elite", monthly=500000,
          price_id="price_beta_only_month")

    sub = _subscription(subscriber, "price_beta_only_month")
    billing_webhook.apply_subscription(db_session, subscriber, sub)

    assert subscriber.billing_plan_key == "professional", (
        "A price belonging to another brand's catalogue resolved onto this "
        "organization. resolve_plan_by_price_id must stay scoped to the org's "
        "own platform_id.")
    assert subscriber.plan == "professional"


def test_a_scheduled_change_landing_clears_the_pending_markers(
        db_session, brand, subscriber):
    """The moment Stripe's schedule advances, and not before, "what plan am I
    on" changes - and it must change in exactly one place."""
    subscriber.billing_pending_plan_key = "starter"
    subscriber.billing_pending_effective_at = PERIOD_END
    subscriber.stripe_schedule_id = "sub_sched_landed"
    db_session.commit()

    # Phase two has begun: the subscription now carries the target price.
    sub = _subscription(subscriber, PRICE["starter"])
    billing_webhook.apply_subscription(db_session, subscriber, sub)
    db_session.commit()
    db_session.refresh(subscriber)

    assert subscriber.billing_plan_key == "starter"
    assert subscriber.plan == "starter"
    assert subscriber.billing_pending_plan_key is None, (
        "The scheduled change has landed but the pending marker is still set. "
        "Anything reading it - the Billing screen, plan_limits.report - will "
        "keep announcing a change that already happened.")
    assert subscriber.billing_pending_effective_at is None
    assert subscriber.stripe_schedule_id is None


def test_a_pending_change_that_has_not_landed_is_left_alone(
        db_session, brand, subscriber):
    """The counterpart: an unrelated subscription update must not clear a
    pending change that is still genuinely pending."""
    subscriber.billing_pending_plan_key = "starter"
    subscriber.billing_pending_effective_at = PERIOD_END
    subscriber.stripe_schedule_id = "sub_sched_waiting"
    db_session.commit()

    # Still on Professional - the boundary has not been reached.
    billing_webhook.apply_subscription(
        db_session, subscriber, _subscription(subscriber, PRICE["professional"]))

    assert subscriber.billing_plan_key == "professional"
    assert subscriber.billing_pending_plan_key == "starter"
    assert subscriber.stripe_schedule_id == "sub_sched_waiting"
