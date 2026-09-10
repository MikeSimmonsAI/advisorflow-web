"""THE RATE THE CUSTOMER IS ACTUALLY ON — not the one they might have had.

Found on the first live subscription payment. A customer bought EvoSys Pro
Starter MONTH-TO-MONTH. Stripe charged $597.00, correctly, and the invoice
recorded $597.00, correctly. Every MRR figure on every screen said $500.00.

Nothing was broken in the money. The defect was in the READING: Starter sells
at $500/mo on a committed term and $597/mo month-to-month, and BOTH are the
`month` interval — they differ in what the customer promised, not in how often
the card is charged. Every reader priced subscriptions from (plan, interval),
so `price_cents_for` fell through to its documented default, the TERM rate, and
handed a month-to-month customer a discount they never earned.

A number wrong by exactly the size of the term discount is the kind that
survives for years, because it looks entirely plausible. These tests make it
impossible to reintroduce quietly.
"""
import os
import re

import pytest

from app.models.billing_models import (BillingCommitment, BillingInterval,
                                       BrandBillingPlan)
from app.services import billing_catalog


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _starter() -> BrandBillingPlan:
    """The real shape: one tier, two monthly rates, two Stripe Prices."""
    return BrandBillingPlan(
        key="starter", name="Starter",
        monthly_cents=50000,                 # committed term
        month_to_month_cents=59700,          # no commitment
        annual_cents=None,
        stripe_price_id_monthly="price_term",
        stripe_price_id_month_to_month="price_m2m",
        stripe_price_id_annual=None,
        currency="usd",
    )


class TestTheCommitmentDecidesTheRate:
    """Both rates are the MONTH interval. Only the commitment separates them."""

    def test_the_two_rates_differ_only_by_commitment(self):
        plan = _starter()
        term = billing_catalog.price_cents_for(
            plan, BillingInterval.MONTH, BillingCommitment.TERM)
        m2m = billing_catalog.price_cents_for(
            plan, BillingInterval.MONTH, BillingCommitment.MONTH_TO_MONTH)

        assert term == 50000
        assert m2m == 59700
        assert term != m2m, (
            "if these ever match, this whole class of bug is impossible and "
            "these tests are free — but the catalogue would have lost the "
            "term discount, which is a bigger problem")

    def test_a_month_to_month_subscription_is_priced_at_its_own_rate(self):
        """The exact figure that was wrong live: $597, not $500."""
        from app.routers.god_billing_router import _monthly_equivalent_cents

        cents = _monthly_equivalent_cents(
            _starter(), BillingInterval.MONTH, BillingCommitment.MONTH_TO_MONTH)

        assert cents == 59700, (
            "a month-to-month customer must be counted at the rate they pay, "
            "not at the committed-term discount they did not agree to")

    def test_an_unknown_commitment_keeps_the_previous_behaviour(self):
        """NULL is UNKNOWN, and unknown must not inflate revenue.

        Rows written before the column existed carry no commitment. Reading
        those as month-to-month would overstate MRR on every one of them, which
        is the worse direction to be wrong in — an understated figure matches
        what the screen said yesterday and gets corrected as the webhook fills
        the column in.
        """
        from app.routers.god_billing_router import _monthly_equivalent_cents

        assert _monthly_equivalent_cents(
            _starter(), BillingInterval.MONTH, None) == 50000

    def test_no_falling_back_between_commitments(self):
        """A missing month-to-month price is a refusal, not a discount.

        `price_cents_for` documents this rule; this pins it at the MRR layer,
        where the temptation to "just use the other price" would show up as
        free money nobody notices.
        """
        from app.routers.god_billing_router import _monthly_equivalent_cents

        plan = _starter()
        plan.month_to_month_cents = None

        assert _monthly_equivalent_cents(
            plan, BillingInterval.MONTH, BillingCommitment.MONTH_TO_MONTH) is None


class TestThePriceIsWhatRecordsTheCommitment:
    """Stripe's Price is the fact. A quote is only an intention."""

    def test_each_price_maps_back_to_its_own_commitment(self):
        plan = _starter()

        assert billing_catalog.commitment_for_price_id(plan, "price_m2m") \
            == BillingCommitment.MONTH_TO_MONTH
        assert billing_catalog.commitment_for_price_id(plan, "price_term") \
            == BillingCommitment.TERM

    def test_a_foreign_price_maps_to_nothing(self):
        """Unrecognised must be None, so the caller can leave the record alone
        rather than writing a guess over a known-good commitment."""
        assert billing_catalog.commitment_for_price_id(
            _starter(), "price_from_some_other_plan") is None
        assert billing_catalog.commitment_for_price_id(_starter(), None) is None


class TestEveryReaderPassesTheCommitment:
    """A reader that forgets is the whole bug. Pinned at the source level.

    Reading the source rather than exercising each endpoint is deliberate: the
    failure is silent and produces a plausible number, so a behavioural test
    only catches it where a fixture happens to use a tier with two rates. This
    catches a NEW reader added later that repeats the mistake.
    """

    CALLERS = (
        ("app/routers/god_billing_router.py", "_monthly_equivalent_cents("),
        ("app/services/executive_portfolio.py",
         "billing_catalog._monthly_equivalent_cents("),
    )

    def _call_args(self, path: str, needle: str):
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as fh:
            src = fh.read()
        # Only CALL sites, never the definition.
        out = []
        for m in re.finditer(re.escape(needle), src):
            head = src[max(0, m.start() - 4):m.start()]
            if head.endswith("def "):
                continue
            depth, i = 1, m.end()
            while i < len(src) and depth:
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                i += 1
            out.append(src[m.end():i - 1])
        return out

    @pytest.mark.parametrize("path,needle", CALLERS)
    def test_the_commitment_is_passed_at_every_call_site(self, path, needle):
        calls = self._call_args(path, needle)
        assert calls, "no call sites found — did this move? %s" % path
        for args in calls:
            assert "commitment" in args, (
                "%s calls %s without a commitment, so it prices every "
                "month-to-month customer at the committed-term discount. "
                "Offending call: %s(%s)" % (path, needle.rstrip("("),
                                            needle.rstrip("("), args.strip()))

    def test_the_customers_own_screen_prices_at_their_commitment(self):
        """The worst place of all to report the wrong rate.

        `recurring_cents` on /billing/subscription is the number the CUSTOMER
        reads as "what I pay". Priced from the interval alone it would show a
        month-to-month customer the committed discount — lower than their card
        is actually charged, which reads as a billing error or a broken
        promise. Pinned separately because `price_cents_for` has many entirely
        correct callers that pass no commitment.
        """
        calls = self._call_args("app/routers/billing_router.py",
                                "billing_catalog.price_cents_for(")
        assert calls, "no price_cents_for call sites found in billing_router"

        recurring = [a for a in calls if "stripe_plan_interval" in a]
        assert recurring, "the recurring_cents call site moved — re-pin it"
        for args in recurring:
            assert "commitment" in args, (
                "the customer's own recurring amount is priced without their "
                "commitment: %s" % " ".join(args.split()))


# ═══════════════════════════════════════════════════════════════════════════
# WHAT THE WEBHOOK RECORDS
# ═══════════════════════════════════════════════════════════════════════════

class _Org:
    """Only the fields apply_subscription touches."""
    id = "org_test"
    platform_id = "plat_test"
    plan = None
    billing_plan_key = None
    billing_commitment = None
    billing_status = None
    stripe_subscription_id = None
    stripe_plan_interval = None
    billing_current_period_end = None
    billing_trial_end = None
    billing_cancel_at_period_end = False
    billing_pending_plan_key = None
    billing_pending_effective_at = None
    stripe_schedule_id = None


def _sub(price_id, period_end_on_subscription=None,
         period_end_on_item=None, status="active"):
    item = {"price": {"id": price_id, "recurring": {"interval": "month"}}}
    if period_end_on_item is not None:
        item["current_period_end"] = period_end_on_item
    sub = {"id": "sub_test", "status": status,
           "items": {"data": [item]}, "metadata": {}}
    if period_end_on_subscription is not None:
        sub["current_period_end"] = period_end_on_subscription
    return sub


def _apply(monkeypatch, org, sub, plan):
    """Run apply_subscription with no database behind it.

    `apply_subscription` imports billing_catalog INSIDE the function, so the
    real module's own attributes are what it will reach — patch those, not a
    stand-in object bound to the webhook module. Only the two lookups that need
    a Session are replaced; `commitment_for_price_id` is pure and stays real,
    which is the point: these tests must exercise the actual classification.
    """
    from app.services import billing_webhook

    def _by_price(db, platform_id, price_id):
        known = (plan.stripe_price_id_monthly,
                 plan.stripe_price_id_month_to_month)
        return plan if price_id and price_id in known else None

    def _by_key(db, platform_id, key):
        return plan if key == plan.key else None

    monkeypatch.setattr(billing_catalog, "resolve_plan_by_price_id", _by_price)
    monkeypatch.setattr(billing_catalog, "resolve_plan", _by_key)
    billing_webhook.apply_subscription(None, org, sub)


class TestTheWebhookRecordsTheCommitment:

    def test_a_month_to_month_price_records_month_to_month(self, monkeypatch):
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org, _sub("price_m2m"), plan)

        assert org.billing_plan_key == "starter"
        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH

    def test_a_committed_price_records_the_term(self, monkeypatch):
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org, _sub("price_term"), plan)

        assert org.billing_commitment == BillingCommitment.TERM

    def test_moving_between_rates_is_recorded(self, monkeypatch):
        """The same tier at a different rate is still a change worth storing."""
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org, _sub("price_m2m"), plan)
        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH

        _apply(monkeypatch, org, _sub("price_term"), plan)
        assert org.billing_commitment == BillingCommitment.TERM

    def test_an_unrecognised_price_does_not_erase_what_is_known(self, monkeypatch):
        """Losing track of a price is not evidence the customer changed terms.

        If the plan resolves through METADATA while the price is one we cannot
        classify, blanking the commitment would silently move an existing
        month-to-month customer back onto the discounted term rate in every
        report. Leaving it alone keeps the last thing Stripe actually told us.
        """
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org, _sub("price_m2m"), plan)
        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH

        stray = _sub("price_retired_last_year")
        stray["metadata"] = {"plan": "starter"}
        _apply(monkeypatch, org, stray, plan)

        assert org.billing_commitment == BillingCommitment.MONTH_TO_MONTH, (
            "an unclassifiable price must leave the recorded commitment alone")


class TestTheNextBillDateSurvivesTheApiVersion:
    """Stripe moved `current_period_end` onto the subscription ITEM in the
    2025-03-31 API version. Nothing here pins an api_version, so the shape can
    change without a deploy — and reading one location turned an active,
    correctly-charged subscription into a blank renewal date on every screen.
    """

    WHEN = 1_800_000_000   # a fixed, far-future epoch second

    def test_the_subscriptions_own_field_is_used_when_present(self, monkeypatch):
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org,
               _sub("price_m2m", period_end_on_subscription=self.WHEN), plan)

        assert org.billing_current_period_end is not None

    def test_the_item_period_is_used_when_the_subscription_has_none(self, monkeypatch):
        """The live failure, exactly: active subscription, blank next bill."""
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org,
               _sub("price_m2m", period_end_on_item=self.WHEN), plan)

        assert org.billing_current_period_end is not None, (
            "an active subscription must never show a blank next-bill date "
            "merely because Stripe moved the field onto the item")

    def test_the_furthest_out_item_period_wins(self, monkeypatch):
        """A multi-item subscription is next FULLY due at its latest period."""
        from app.services.billing_webhook import _ts

        org, plan = _Org(), _starter()
        sub = _sub("price_m2m", period_end_on_item=self.WHEN)
        sub["items"]["data"].append(
            {"price": {"id": "price_addon", "recurring": {"interval": "month"}},
             "current_period_end": self.WHEN + 86400})

        _apply(monkeypatch, org, sub, plan)

        assert org.billing_current_period_end == _ts(self.WHEN + 86400)

    def test_no_period_anywhere_leaves_it_alone(self, monkeypatch):
        """Absent is absent. Inventing a renewal date is worse than showing
        none, because somebody will plan cash against it."""
        org, plan = _Org(), _starter()
        _apply(monkeypatch, org, _sub("price_m2m"), plan)

        assert org.billing_current_period_end is None
