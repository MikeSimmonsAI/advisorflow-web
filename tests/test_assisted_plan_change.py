"""AN OPERATOR CHANGING A PLAN MUST NOT BE A SECOND BILLING ENGINE.

A customer provisioned from a signed deal has a live subscription and, until
somebody is invited, NO USER ACCOUNT AT ALL. ZZ Launch Verify A is exactly
that: active Growth subscription, "No active user account — nobody can log in."
Nobody can reach the customer's own Billing page to change it, and the only
other route is the Stripe dashboard, which writes nothing back here and leaves
Stripe and this platform disagreeing.

So there is a god-only assisted change. The danger in that is obvious: a second
path to the same money, which agrees with the first right up until the day it
does not. These tests pin that there is only ONE implementation — the assisted
endpoint resolves with `_resolve_change` and performs with `apply_change`, the
same functions behind POST /billing/change-plan — and that it previews before
it writes.
"""
import inspect
import os
import re

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _god_src() -> str:
    with open(os.path.join(ROOT, "app", "routers", "god_billing_router.py"),
              "r", encoding="utf-8") as fh:
        return fh.read()


def _assisted_src() -> str:
    src = _god_src()
    body = src.split("def assisted_change_plan", 1)[1]
    return body.split("\n@router.", 1)[0]


class TestThereIsOnlyOneImplementation:

    def test_it_performs_through_the_customers_own_function(self):
        body = _assisted_src()
        assert "apply_change(" in body, (
            "the assisted change must perform through billing_router."
            "apply_change — the same function the customer's own endpoint "
            "calls — not through its own copy of the Stripe calls")

    def test_it_resolves_through_the_customers_own_function(self):
        body = _assisted_src()
        assert "_resolve_change(" in body, (
            "the preview must project the SAME decision, not form a second "
            "opinion about it")

    def test_it_never_calls_stripe_itself(self):
        """Every Stripe operation belongs to apply_change. A direct call here
        would be the start of the second engine."""
        body = _assisted_src()
        for forbidden in ("stripe.Subscription", "stripe.checkout",
                          "stripe.Price", "stripe.SubscriptionSchedule",
                          "billing_schedule."):
            assert forbidden not in body, (
                "assisted_change_plan touches Stripe directly (%r); it must "
                "go through apply_change" % forbidden)

    def test_the_customer_endpoint_also_delegates(self):
        """If the CUSTOMER path stopped delegating, the two would diverge from
        the other direction."""
        from app.routers.billing_router import change_plan

        src = inspect.getsource(change_plan)
        assert "apply_change(" in src
        # And it must not have quietly regrown its own Stripe handling.
        assert "stripe.Subscription.modify" not in src


class TestItPreviewsBeforeItWrites:

    def test_apply_defaults_to_false(self):
        from app.routers.god_billing_router import AssistedChangeIn

        assert AssistedChangeIn(plan="growth").apply is False, (
            "a new button on a billing screen must show what it would do "
            "before it does it")

    def test_the_dry_run_returns_without_applying(self):
        body = _assisted_src()
        dry = body.split("if not body.apply:", 1)
        assert len(dry) == 2, "the dry-run branch was removed"
        before_apply = dry[1].split("apply_change(", 1)[0]
        assert "return {" in before_apply, (
            "the preview branch must return before anything is applied")

    def test_the_commitment_defaults_to_carrying_over(self):
        """An operator moving somebody between TIERS must not move them
        between RATES by accident — the same rule the customer's screen
        follows."""
        from app.routers.god_billing_router import AssistedChangeIn

        assert AssistedChangeIn(plan="growth").commitment is None


class TestItIsGodOnlyAndAudited:

    def test_the_endpoint_requires_god(self):
        from app.routers.god_billing_router import assisted_change_plan

        sig = inspect.signature(assisted_change_plan)
        default = sig.parameters["user"].default
        assert "require_god" in repr(default), (
            "changing a customer's subscription is not an ordinary admin "
            "action")

    def test_applying_writes_an_audit_row_naming_the_actor(self):
        body = _assisted_src()
        applied = body.split("apply_change(", 1)[1]
        assert "_audit(" in applied, (
            "a plan change with no attributable actor cannot be answered for")
        assert "plan_change_assisted" in applied

    def test_the_actor_is_the_god_user_not_the_customer(self):
        body = _assisted_src()
        assert "actor=user" in body, (
            "the audit row on the customer's organization must name who "
            "actually did it")
