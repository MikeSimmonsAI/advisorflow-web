"""A SCHEDULED CHANGE MUST NOT BE FORGOTTEN BEFORE IT HAPPENS.

Found live, by the preview on the God Billing "Refresh" button — before
anything was written, which is the whole reason that button previews.

ZZ Launch Verify A was moved from Growth committed-term to Growth
month-to-month. That is a real change: the rate goes from $1,000 to $1,297 and
Stripe holds a Subscription Schedule to perform it at the period end. The
platform recorded it correctly. Then the refresh preview reported that three
fields would be wiped:

    billing_pending_plan_key      growth      -> not set
    billing_pending_effective_at  2026-10-10  -> not set
    stripe_schedule_id            sub_sched_… -> not set

THE CAUSE. `apply_subscription` clears the pending markers when the
subscription arrives on the pending PLAN — that is how a landed change stops
being pending. But a commitment-only change has a pending plan key of
"growth", which is exactly what the customer is already on. So the comparison
matched immediately and the very next `customer.subscription.updated` would
have cleared the markers while Stripe's schedule was still waiting to run.

The platform would have forgotten a downgrade that was still going to happen,
and nothing would have said so until the customer's rate moved on its own.

The fix: record the pending COMMITMENT too, and treat a change as landed only
when the tier AND the rate both match.
"""
from datetime import datetime

import pytest

from app.models.billing_models import (BillingCommitment, BrandBillingPlan)
from app.services import billing_catalog


def _plan():
    return BrandBillingPlan(
        key="growth", name="Growth",
        monthly_cents=100000, month_to_month_cents=129700, annual_cents=None,
        stripe_price_id_monthly="price_growth_term",
        stripe_price_id_month_to_month="price_growth_m2m",
        stripe_price_id_annual=None, currency="usd",
        is_active=True, is_purchasable=True,
    )


class _Org:
    """A customer mid-way through a scheduled commitment-only downgrade."""
    id = "org_1"
    platform_id = "plat_1"
    plan = "growth"
    billing_plan_key = "growth"
    billing_commitment = BillingCommitment.TERM
    billing_status = "active"
    stripe_subscription_id = "sub_1"
    stripe_plan_interval = "month"
    billing_current_period_end = None
    billing_trial_end = None
    billing_cancel_at_period_end = False
    # The scheduled move to month-to-month.
    billing_pending_plan_key = "growth"
    billing_pending_commitment = BillingCommitment.MONTH_TO_MONTH
    billing_pending_effective_at = "2026-10-10"
    stripe_schedule_id = "sub_sched_live"


def _sub(price_id):
    return {"id": "sub_1", "status": "active", "metadata": {},
            "items": {"data": [{
                "id": "si_1",
                "price": {"id": price_id, "recurring": {"interval": "month"}},
            }]}}


@pytest.fixture
def apply(monkeypatch):
    from app.services import billing_webhook

    plan = _plan()
    monkeypatch.setattr(
        billing_catalog, "resolve_plan_by_price_id",
        lambda db, pid, price_id: plan if price_id in (
            "price_growth_term", "price_growth_m2m") else None)
    monkeypatch.setattr(billing_catalog, "resolve_plan",
                        lambda db, pid, key: plan if key == "growth" else None)
    return billing_webhook.apply_subscription


class TestAScheduledChangeSurvivesOrdinaryEvents:

    def test_an_event_on_the_unchanged_rate_keeps_the_schedule(self, apply):
        """THE DEFECT, pinned.

        Stripe sends subscription.updated for all sorts of reasons — a card
        change, a metadata edit, its own housekeeping. One arriving while the
        subscription is STILL on the committed-term price must not be read as
        the scheduled change having landed.
        """
        org = _Org()
        apply(None, org, _sub("price_growth_term"))

        assert org.billing_pending_plan_key == "growth", (
            "the scheduled change was forgotten while Stripe still had it")
        assert org.billing_pending_commitment == BillingCommitment.MONTH_TO_MONTH
        assert org.stripe_schedule_id == "sub_sched_live", (
            "dropping the schedule id loses the only handle on the change — "
            "a later upgrade could no longer release it")
        assert org.billing_pending_effective_at == "2026-10-10"

    def test_the_change_landing_does_clear_it(self, apply):
        """The other half. When the subscription genuinely arrives on the
        month-to-month price, the change has happened and is no longer
        pending."""
        org = _Org()
        apply(None, org, _sub("price_growth_m2m"))

        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH
        assert org.billing_pending_plan_key is None
        assert org.billing_pending_commitment is None
        assert org.billing_pending_effective_at is None
        assert org.stripe_schedule_id is None

    def test_a_pending_change_with_no_recorded_commitment_still_lands(self, apply):
        """Rows written before the column existed must keep behaving as they
        did — the tier alone decides, because the tier is all that was known."""
        org = _Org()
        org.billing_pending_commitment = None

        apply(None, org, _sub("price_growth_term"))

        assert org.billing_pending_plan_key is None, (
            "a pre-existing pending change must still land on the tier match")

    def test_an_unclassifiable_price_does_not_clear_the_schedule(self, apply):
        """If we cannot tell which rate the subscription is on, we cannot tell
        whether the change landed. Not knowing is not the same as it having
        happened."""
        org = _Org()
        sub = _sub("price_retired_last_year")
        sub["metadata"] = {"plan": "growth"}

        apply(None, org, sub)

        assert org.billing_pending_plan_key == "growth"
        assert org.stripe_schedule_id == "sub_sched_live"


class TestTheScheduledChangeIsRecordedInFull:

    def test_a_deferred_change_records_the_commitment_it_lands_on(self):
        """Without this the pending state cannot describe a commitment-only
        change at all, which is what made it forgettable."""
        import inspect
        from app.routers.billing_router import apply_change

        src = inspect.getsource(apply_change)
        deferred = src.split("ChangeTiming.PERIOD_END", 1)[1].split("else:", 1)[0]

        assert "billing_pending_commitment = commitment" in deferred, (
            "the deferred branch must record which rate the change lands on")

    def test_an_upgrade_clears_the_pending_commitment_too(self):
        """An upgrade supersedes a scheduled downgrade. Leaving the pending
        commitment behind would strand a marker with nothing to land."""
        import inspect
        from app.routers.billing_router import apply_change

        src = inspect.getsource(apply_change)
        immediate = src.split("else:", 1)[1]

        assert "billing_pending_commitment = None" in immediate

    def test_the_resync_mirror_covers_the_new_field(self):
        """Otherwise a refresh could change it without reporting or auditing
        the change."""
        from app.services import billing_resync

        assert "billing_pending_commitment" in billing_resync.MIRRORED_FIELDS


class TestAChangeDatedInTheFutureHasNotLanded:
    """FOUND LIVE, NOT REASONED ABOUT.

    A customer had a commitment-only downgrade scheduled for October. In
    September an ADD-ON was attached to their subscription, Stripe sent the
    resulting `customer.subscription.updated` on the unchanged plan, and the
    pending markers were cleared — the platform forgot a change Stripe still
    had booked, and nothing would have said so until the rate failed to move.

    Every guard in front of it was reasonable and none of them caught it: the
    pending tier matched because a commitment-only change never changes the
    tier, and the pending commitment was NULL because it was recorded before
    that column existed — which is exactly the case the NULL escape hatch was
    written to be kind to.

    The date is the guard that cannot be argued with. A change booked for
    October has not happened in September.
    """

    FUTURE = datetime(2027, 1, 1, 12, 0, 0)
    PAST = datetime(2020, 1, 1, 12, 0, 0)

    def _org_with_pending(self, *, commitment=None, effective_at=None):
        org = _Org()
        org.billing_plan_key = "growth"
        org.plan = "growth"
        org.billing_commitment = BillingCommitment.TERM
        org.billing_pending_plan_key = "growth"
        org.billing_pending_commitment = commitment
        org.billing_pending_effective_at = effective_at
        org.stripe_schedule_id = "sub_sched_1"
        return org

    def test_an_unrelated_event_does_not_clear_a_future_change(self, apply):
        """The live failure, exactly: an add-on attached mid-period."""
        org = self._org_with_pending(effective_at=self.FUTURE)
        apply(None, org, _sub("price_growth_term"))

        assert org.billing_pending_plan_key == "growth"
        assert org.billing_pending_effective_at == self.FUTURE
        assert org.stripe_schedule_id == "sub_sched_1"

    def test_it_holds_even_with_no_recorded_commitment(self, apply):
        """The NULL commitment is what let it through before."""
        org = self._org_with_pending(commitment=None,
                                     effective_at=self.FUTURE)
        apply(None, org, _sub("price_growth_term"))
        assert org.billing_pending_plan_key == "growth"

    def test_the_target_actually_arriving_early_does_clear_it(self, apply):
        """THE OTHER HALF, and why the date is not the whole rule.

        If the subscription genuinely MOVES onto the pending target - the
        schedule fires early, or somebody applies the change by hand - the
        change has happened, whatever date was written down. Holding the
        marker would leave every screen announcing a change the customer
        already has.
        """
        org = self._org_with_pending(
            commitment=BillingCommitment.MONTH_TO_MONTH,
            effective_at=self.FUTURE)
        apply(None, org, _sub("price_growth_m2m"))

        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH
        assert org.billing_pending_plan_key is None
        assert org.stripe_schedule_id is None

    def test_a_change_whose_date_has_passed_still_clears(self, apply):
        """The guard must not strand a change that genuinely landed."""
        org = self._org_with_pending(
            commitment=BillingCommitment.MONTH_TO_MONTH,
            effective_at=self.PAST)
        apply(None, org, _sub("price_growth_m2m"))

        assert org.billing_pending_plan_key is None
        assert org.billing_pending_commitment is None
        assert org.billing_pending_effective_at is None
        assert org.stripe_schedule_id is None

    def test_a_change_with_no_date_still_clears_when_it_matches(self, apply):
        """Older rows carry no effective date; they keep the previous rule."""
        org = self._org_with_pending(
            commitment=BillingCommitment.MONTH_TO_MONTH, effective_at=None)
        apply(None, org, _sub("price_growth_m2m"))

        assert org.billing_pending_plan_key is None
