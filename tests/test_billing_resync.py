"""RE-READING STRIPE IS NOT THE SAME AS OVERRIDING IT.

A webhook is delivered once and handled by whatever code was deployed at that
moment. Three ordinary things leave the local mirror stale afterwards and none
of them fixes itself:

  * a column added AFTER the event arrived (`billing_commitment` was added the
    same afternoon a live subscription changed plan — the event landed minutes
    before the deploy and the column stayed NULL);
  * a field Stripe MOVED between API versions (`current_period_end` left the
    Subscription object in 2025-03-31 and no api_version is pinned here);
  * a delivery that FAILED while the endpoint was misconfigured.

In every case the money at Stripe is right and the local copy is behind. These
tests pin the two properties that make asking Stripe again safe: it never
writes to Stripe, and it never invents a value.
"""
import inspect
import os

import pytest

from app.models.billing_models import BillingCommitment, BrandBillingPlan
from app.services import billing_catalog, billing_resync


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plan():
    return BrandBillingPlan(
        key="starter", name="Starter",
        monthly_cents=50000, month_to_month_cents=59700, annual_cents=None,
        stripe_price_id_monthly="price_term",
        stripe_price_id_month_to_month="price_m2m",
        stripe_price_id_annual=None, currency="usd",
        is_active=True, is_purchasable=True,
    )


class _Org:
    """An organization whose mirror is stale in exactly the live way: the
    subscription is known, the commitment and the period end are not."""
    id = "org_1"
    platform_id = "plat_1"
    plan = "starter"
    billing_plan_key = "starter"
    billing_commitment = None
    billing_status = "active"
    stripe_subscription_id = "sub_1"
    stripe_plan_interval = "month"
    billing_current_period_end = None
    billing_trial_end = None
    billing_cancel_at_period_end = False
    billing_pending_plan_key = None
    # The rate a scheduled change lands on. Present here because the stub has
    # to model every field the resync mirrors — a stub missing one makes the
    # dry-run test fail on the stub rather than on the behaviour.
    billing_pending_commitment = None
    billing_pending_effective_at = None
    stripe_schedule_id = None


class _Db:
    """Enough Session for apply_subscription and the rollback path."""
    def __init__(self):
        self.rolled_back = False
        self.committed = False

    def rollback(self):
        self.rolled_back = True

    def commit(self):                                    # pragma: no cover
        self.committed = True


def _stripe_sub(price_id="price_m2m", period_end=1_800_000_000):
    return {
        "id": "sub_1",
        "status": "active",
        "items": {"data": [{
            "id": "si_1",
            "current_period_end": period_end,
            "price": {"id": price_id, "recurring": {"interval": "month"}},
        }]},
        "metadata": {},
    }


@pytest.fixture
def stripe_returning(monkeypatch):
    """Point the resync at a fake Stripe and a stubbed catalogue.

    `stripe.Subscription.retrieve` is replaced by a recorder, so the tests can
    assert not only what came back but that nothing else was called.
    """
    import stripe
    from app.routers import billing_router

    plan = _plan()
    calls = {"retrieve": []}

    def _install(sub):
        def _retrieve(sub_id, **kw):
            calls["retrieve"].append((sub_id, kw))
            if sub is None:
                raise RuntimeError("No such subscription")
            return sub
        monkeypatch.setattr(stripe.Subscription, "retrieve", _retrieve)
        return calls

    monkeypatch.setattr(billing_router, "_stripe_client", lambda: None)
    monkeypatch.setattr(billing_catalog, "resolve_plan_by_price_id",
                        lambda db, pid, price_id: plan if price_id in (
                            "price_term", "price_m2m") else None)
    monkeypatch.setattr(billing_catalog, "resolve_plan",
                        lambda db, pid, key: plan if key == "starter" else None)
    return _install


class TestItNeverWritesToStripe:
    """The single property that makes an operator-triggered refresh safe."""

    def test_the_source_contains_no_mutating_stripe_call(self):
        src = inspect.getsource(billing_resync)
        for forbidden in ("Subscription.modify", "Subscription.delete",
                          "Subscription.cancel", "Price.create",
                          "Product.create", "Invoice.create",
                          "PaymentIntent.create", "Refund.create",
                          "SubscriptionSchedule.create"):
            assert forbidden not in src, (
                "billing_resync must only READ from Stripe; found %r"
                % forbidden)

    def test_only_retrieve_is_called(self, stripe_returning):
        calls = stripe_returning(_stripe_sub())
        billing_resync.resync_subscription(_Db(), _Org(), dry_run=False)
        assert len(calls["retrieve"]) == 1

    def test_it_expands_the_price_objects(self, stripe_returning):
        """A bare price id string leaves the plan and the commitment
        unresolvable, and the refresh would report 'nothing changed' while
        having changed nothing for entirely the wrong reason."""
        calls = stripe_returning(_stripe_sub())
        billing_resync.resync_subscription(_Db(), _Org(), dry_run=False)
        _sub_id, kw = calls["retrieve"][0]
        assert "items.data.price" in (kw.get("expand") or [])


class TestItRepairsTheStaleMirror:

    def test_a_missing_commitment_is_filled_in_from_the_price(
            self, stripe_returning):
        """THE LIVE CASE. The column was added after the event arrived."""
        stripe_returning(_stripe_sub(price_id="price_m2m"))
        org = _Org()
        assert org.billing_commitment is None

        result = billing_resync.resync_subscription(_Db(), org, dry_run=False)

        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH
        fields = {c["field"] for c in result["changed"]}
        assert "billing_commitment" in fields

    def test_a_missing_period_end_is_filled_in_from_the_item(
            self, stripe_returning):
        stripe_returning(_stripe_sub())
        org = _Org()

        result = billing_resync.resync_subscription(_Db(), org, dry_run=False)

        assert org.billing_current_period_end is not None
        assert "billing_current_period_end" in {
            c["field"] for c in result["changed"]}

    def test_an_already_correct_mirror_reports_no_change(self, stripe_returning):
        """The good outcome, and it must be demonstrable rather than assumed —
        an operator needs to be able to prove the webhook copy is right."""
        stripe_returning(_stripe_sub())
        org = _Org()
        billing_resync.resync_subscription(_Db(), org, dry_run=False)

        again = billing_resync.resync_subscription(_Db(), org, dry_run=False)
        assert again["changed"] == []
        assert again["unchanged"] is True


class TestThePreviewChangesNothing:

    def test_a_dry_run_leaves_every_field_as_it_was(self, stripe_returning):
        stripe_returning(_stripe_sub())
        org, db = _Org(), _Db()
        before = {f: getattr(org, f) for f in billing_resync.MIRRORED_FIELDS}

        result = billing_resync.resync_subscription(db, org, dry_run=True)

        assert result["dry_run"] is True
        assert result["changed"], "the preview must still report what WOULD change"
        for field, value in before.items():
            assert getattr(org, field) == value, (
                "a dry run wrote %s to the organization" % field)
        assert db.rolled_back is True


class TestItRefusesRatherThanInvents:

    def test_no_subscription_is_refused(self, stripe_returning):
        stripe_returning(_stripe_sub())
        org = _Org()
        org.stripe_subscription_id = None

        with pytest.raises(billing_resync.ResyncRefused) as exc:
            billing_resync.resync_subscription(_Db(), org, dry_run=False)
        assert "nothing to refresh" in str(exc.value).lower()

    def test_a_subscription_stripe_does_not_know_leaves_the_record_alone(
            self, stripe_returning):
        """A processor that cannot find the subscription is not evidence the
        customer has no plan. Blanking the record on a failed read would turn a
        transient Stripe error into a cancelled-looking customer."""
        stripe_returning(None)
        org = _Org()
        org.billing_commitment = BillingCommitment.TERM

        with pytest.raises(billing_resync.ResyncRefused):
            billing_resync.resync_subscription(_Db(), org, dry_run=False)

        assert org.billing_commitment == BillingCommitment.TERM
        assert org.billing_status == "active"

    def test_the_mirrored_field_list_is_billing_only(self):
        """The refresh must not be a general-purpose organization editor.
        Nothing about the customer, their users, their brand or their data is
        in reach of this button."""
        for field in billing_resync.MIRRORED_FIELDS:
            assert field.startswith(("billing_", "stripe_")) or field == "plan", (
                "%r is not a mirrored billing field" % field)
        for forbidden in ("name", "id", "platform_id", "owner_user_id",
                          "is_active", "created_at"):
            assert forbidden not in billing_resync.MIRRORED_FIELDS


class TestItReportsWhatStripeStillHasBooked:
    """NO WEBHOOK CARRIES A SCHEDULE.

    A deferred plan change lives on a Subscription Schedule, and the payloads
    this platform handles say nothing about its phases. So the question an
    operator asks precisely when a scheduled downgrade looks wrong — "is it
    still there?" — had no answer inside the product at all, and the refresh
    button answered it with "already matches Stripe", which is true of the
    mirrored columns and beside the point.
    """

    @pytest.fixture
    def with_schedule(self, monkeypatch):
        import stripe

        def _install(schedule):
            def _retrieve(schedule_id, **kw):
                if schedule is None:
                    raise RuntimeError("no such schedule")
                return schedule
            monkeypatch.setattr(stripe.SubscriptionSchedule, "retrieve",
                                _retrieve)
        return _install

    def _sub_with(self, schedule_id):
        sub = _stripe_sub()
        sub["schedule"] = schedule_id
        return sub

    def _schedule(self, *, current_end=1_800_000_000,
                  next_start=1_800_000_000, price="price_m2m"):
        return {
            "id": "sub_sched_1", "status": "active",
            "current_phase": {"start_date": 1, "end_date": current_end},
            "phases": [
                {"start_date": 1, "items": [{"price": "price_term"}]},
                {"start_date": next_start, "items": [{"price": price}]},
            ],
        }

    def test_no_schedule_is_reported_as_none_booked(self, stripe_returning):
        stripe_returning(_stripe_sub())
        out = billing_resync.resync_subscription(_Db(), _Org(), dry_run=True)

        assert out["schedule"]["known"] is True
        assert out["schedule"]["has_schedule"] is False
        assert out["schedule"]["next_phase"] is None

    def test_a_booked_change_is_reported(self, stripe_returning, with_schedule):
        stripe_returning(self._sub_with("sub_sched_1"))
        with_schedule(self._schedule())

        out = billing_resync.resync_subscription(_Db(), _Org(), dry_run=True)
        assert out["schedule"]["has_schedule"] is True
        assert out["schedule"]["next_phase"]["price_ids"] == ["price_m2m"]

    def test_a_schedule_stripe_will_not_return_is_unknown_not_absent(
            self, stripe_returning, with_schedule):
        """"Stripe has no scheduled change" and "we could not ask" are
        opposite facts. Rendering them the same is how somebody concludes a
        customer's downgrade was lost when it was not."""
        stripe_returning(self._sub_with("sub_sched_1"))
        with_schedule(None)

        out = billing_resync.resync_subscription(_Db(), _Org(), dry_run=True)
        assert out["schedule"]["known"] is False
        assert out["schedule"]["has_schedule"] is None

    def test_stripe_holding_a_change_this_platform_lost_is_named(
            self, stripe_returning, with_schedule):
        """THE LIVE FAILURE. The mirrored columns matched perfectly while
        Stripe still had a downgrade booked that no screen was showing."""
        stripe_returning(self._sub_with("sub_sched_1"))
        with_schedule(self._schedule())

        org = _Org()
        org.billing_pending_plan_key = None      # the platform lost it

        out = billing_resync.resync_subscription(_Db(), org, dry_run=True)
        assert out["disagreement"]
        assert "not showing" in out["disagreement"]

    def test_a_pending_change_stripe_does_not_have_is_named(
            self, stripe_returning):
        """The opposite direction, and worse for the customer: a date they
        were given on which nothing will happen."""
        stripe_returning(_stripe_sub())

        org = _Org()
        org.billing_pending_plan_key = "starter"

        out = billing_resync.resync_subscription(_Db(), org, dry_run=True)
        assert out["disagreement"]
        assert "nothing will happen" in out["disagreement"]

    def test_agreement_reports_no_disagreement(self, stripe_returning,
                                               with_schedule):
        stripe_returning(self._sub_with("sub_sched_1"))
        with_schedule(self._schedule())

        org = _Org()
        org.billing_pending_plan_key = "starter"

        out = billing_resync.resync_subscription(_Db(), org, dry_run=True)
        assert out["disagreement"] is None

    def test_reading_a_schedule_writes_nothing(self, stripe_returning,
                                               with_schedule):
        stripe_returning(self._sub_with("sub_sched_1"))
        with_schedule(self._schedule())

        db = _Db()
        billing_resync.resync_subscription(db, _Org(), dry_run=True)
        assert db.committed is False
