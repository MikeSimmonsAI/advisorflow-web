"""ONE SCHEDULE PER SUBSCRIPTION — INCLUDING WHEN WE FORGOT ITS ID.

STRIPE KNOWS WHAT EXISTS THERE; THE `stripe_schedule_id` COLUMN ONLY
REMEMBERS. When the two disagree, creating is the one move guaranteed to
fail — Stripe refuses a second schedule on a subscription that already has
one — and the customer gets "the payment processor would not schedule the
plan change" for a button that should simply have worked.

FOUND LIVE. A stored schedule id was lost (by a webhook clearing pending
markers it should not have), and from that moment the downgrade button was
dead for that customer, with nothing on any screen explaining why. The repair
is to ask the subscription which schedule it has, rather than to trust a
column that can be wrong.
"""
import pytest

import stripe

from app.services import billing_schedule


class _Recorder:
    """Stands in for the four Stripe calls this path can make."""

    def __init__(self, *, subscription_schedule=None, schedules=None,
                 create_raises=False):
        self.subscription_schedule = subscription_schedule
        self.schedules = schedules or {}
        self.create_raises = create_raises
        self.created = 0
        self.modified = []
        self.retrieved_subs = []

    # stripe.Subscription.retrieve
    def sub_retrieve(self, sub_id, **kw):
        self.retrieved_subs.append(sub_id)
        return {"id": sub_id, "schedule": self.subscription_schedule}

    # stripe.SubscriptionSchedule.retrieve
    def sched_retrieve(self, sched_id, **kw):
        if sched_id not in self.schedules:
            raise RuntimeError("no such schedule")
        return self.schedules[sched_id]

    # stripe.SubscriptionSchedule.create
    def sched_create(self, **kw):
        if self.create_raises:
            raise RuntimeError(
                "Subscription already has a schedule attached to it.")
        self.created += 1
        made = {"id": "sub_sched_new", "status": "not_started",
                "phases": [{"start_date": 1, "end_date": 2,
                            "items": [{"price": "price_current",
                                       "quantity": 1}]}]}
        self.schedules["sub_sched_new"] = made
        return made

    # stripe.SubscriptionSchedule.modify
    def sched_modify(self, sched_id, **kw):
        self.modified.append((sched_id, kw))
        return {"id": sched_id,
                "phases": [{"start_date": 1, "end_date": 2},
                           {"start_date": 2}]}


@pytest.fixture
def recorder(monkeypatch):
    def _install(**kw):
        rec = _Recorder(**kw)
        monkeypatch.setattr(stripe.Subscription, "retrieve", rec.sub_retrieve)
        monkeypatch.setattr(stripe.SubscriptionSchedule, "retrieve",
                            rec.sched_retrieve)
        monkeypatch.setattr(stripe.SubscriptionSchedule, "create",
                            rec.sched_create)
        monkeypatch.setattr(stripe.SubscriptionSchedule, "modify",
                            rec.sched_modify)
        return rec
    return _install


def _existing(status="active"):
    return {"id": "sub_sched_live", "status": status,
            "phases": [{"start_date": 1, "end_date": 2,
                        "items": [{"price": "price_current", "quantity": 1}]}]}


def _schedule(rec_unused=None, existing_schedule_id=None):
    return billing_schedule.schedule_change_at_period_end(
        "sub_1", existing_schedule_id=existing_schedule_id,
        target_price_id="price_target", target_plan_key="growth",
        org_id="org_1")


class TestItFindsTheScheduleStripeAlreadyHas:

    def test_a_forgotten_id_is_recovered_from_the_subscription(self, recorder):
        """THE LIVE FAILURE. Nothing recorded locally, a schedule at Stripe."""
        rec = recorder(subscription_schedule="sub_sched_live",
                       schedules={"sub_sched_live": _existing()},
                       create_raises=True)

        out = _schedule(existing_schedule_id=None)

        assert out["schedule_id"] == "sub_sched_live"
        assert rec.created == 0, "a second schedule must never be created"
        assert rec.modified and rec.modified[0][0] == "sub_sched_live"

    def test_a_subscription_with_no_schedule_still_creates_one(self, recorder):
        rec = recorder(subscription_schedule=None)

        out = _schedule(existing_schedule_id=None)

        assert rec.created == 1
        assert out["schedule_id"] == "sub_sched_new"

    def test_a_recorded_id_is_used_without_asking_the_subscription(self,
                                                                   recorder):
        """The common path stays one call cheaper."""
        rec = recorder(schedules={"sub_sched_live": _existing()})

        out = _schedule(existing_schedule_id="sub_sched_live")

        assert out["schedule_id"] == "sub_sched_live"
        assert rec.retrieved_subs == []

    def test_a_released_schedule_at_stripe_is_not_reused(self, recorder):
        """Released means handed back to normal billing; it cannot be edited,
        and treating it as usable would fail on the modify instead."""
        rec = recorder(subscription_schedule="sub_sched_live",
                       schedules={"sub_sched_live": _existing("released")})

        out = _schedule(existing_schedule_id=None)

        assert rec.created == 1
        assert out["schedule_id"] == "sub_sched_new"

    def test_a_stale_recorded_id_falls_through_to_the_subscription(self,
                                                                   recorder):
        """The worst combination: a column pointing at something gone AND a
        real schedule at Stripe. Before this, that customer's downgrade button
        failed forever."""
        rec = recorder(subscription_schedule="sub_sched_live",
                       schedules={"sub_sched_live": _existing()},
                       create_raises=True)

        out = _schedule(existing_schedule_id="sub_sched_vanished")

        assert out["schedule_id"] == "sub_sched_live"
        assert rec.created == 0

    def test_the_target_price_becomes_the_second_phase(self, recorder):
        rec = recorder(subscription_schedule="sub_sched_live",
                       schedules={"sub_sched_live": _existing()},
                       create_raises=True)

        _schedule(existing_schedule_id=None)

        _id, kw = rec.modified[0]
        phases = kw["phases"]
        assert len(phases) == 2
        assert phases[0]["items"][0]["price"] == "price_current"
        assert phases[1]["items"][0]["price"] == "price_target"

    def test_a_subscription_lookup_that_fails_does_not_break_the_change(
            self, recorder, monkeypatch):
        """Checking for a forgotten schedule is a recovery, not a dependency."""
        rec = recorder(subscription_schedule=None)

        def _boom(sub_id, **kw):
            raise RuntimeError("stripe is unhappy")
        monkeypatch.setattr(stripe.Subscription, "retrieve", _boom)

        out = _schedule(existing_schedule_id=None)
        assert rec.created == 1
        assert out["schedule_id"] == "sub_sched_new"
