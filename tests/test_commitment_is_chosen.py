"""A COMMITMENT IS A PROMISE. NOBODY GETS ONE BY DEFAULT.

A monthly tier is sold at two rates. The committed-term rate is LOWER, because
it is earned by promising a term; the month-to-month rate is the standard one.
Both are billed monthly, so the interval cannot say which was bought.

Self-serve checkout used to resolve a missing commitment through
`price_cents_for`'s default — the term rate. A customer clicking a plan card
therefore bought the discount AND the multi-month obligation behind it, having
never been shown a term to accept. A promise nobody was asked to make is not a
promise; it is a default nobody noticed.

The decided rule, and what these tests pin:

  * the customer must CHOOSE, and a monthly checkout with no commitment is
    REFUSED rather than resolved;
  * the screen shows both rates and which one is selected;
  * a committed term needs an explicit affirmative acknowledgment;
  * an unavailable (plan, commitment) pair FAILS CLOSED on both sides — the
    control is dead and the server refuses it. Neither ever substitutes the
    other rate.
"""
import os
import re

import pytest

from app.models.billing_models import (BillingCommitment, BillingInterval,
                                       BrandBillingPlan)
from app.services import billing_catalog


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _billing_jsx() -> str:
    with open(os.path.join(ROOT, "frontend", "src", "pages", "Billing.jsx"),
              "r", encoding="utf-8") as fh:
        return fh.read()


def _code_only(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _plan(term=50000, m2m=59700):
    return BrandBillingPlan(
        key="starter", name="Starter",
        monthly_cents=term, month_to_month_cents=m2m, annual_cents=None,
        stripe_price_id_monthly="price_term",
        stripe_price_id_month_to_month="price_m2m",
        stripe_price_id_annual=None, currency="usd",
        is_active=True, is_purchasable=True,
        description=None, max_leads=None, max_users=None, features_json=None,
    )


class TestTheCatalogueOffersBothRates:
    """The screen cannot ask a question it has not been given the options to."""

    def test_a_plan_carries_every_commitment_it_is_priced_at(self):
        from app.routers.billing_router import _plan_public

        out = _plan_public(_plan())
        by_key = {c["key"]: c for c in out["commitments"]}

        assert set(by_key) == {BillingCommitment.TERM,
                               BillingCommitment.MONTH_TO_MONTH}
        assert by_key[BillingCommitment.TERM]["monthly_cents"] == 50000
        assert by_key[BillingCommitment.MONTH_TO_MONTH]["monthly_cents"] == 59700
        for c in out["commitments"]:
            assert c["label"], "every offered commitment needs a human label"

    def test_an_unpriced_commitment_is_absent_rather_than_substituted(self):
        """FAIL CLOSED. A tier with no month-to-month price offers only the
        term — it does not offer month-to-month AT the term price."""
        from app.routers.billing_router import _plan_public

        out = _plan_public(_plan(m2m=None))
        keys = [c["key"] for c in out["commitments"]]

        assert keys == [BillingCommitment.TERM]
        assert all(c["monthly_cents"] != 50000
                   or c["key"] == BillingCommitment.TERM
                   for c in out["commitments"])

    def test_a_tier_priced_at_neither_offers_nothing(self):
        from app.routers.billing_router import _plan_public
        assert _plan_public(_plan(term=None, m2m=None))["commitments"] == []

    def test_no_stripe_ids_reach_the_customer(self):
        """The commitment list is prices and labels. A price id on a customer
        screen is an id a customer can put in a request."""
        from app.routers.billing_router import _plan_public

        blob = repr(_plan_public(_plan()))
        assert "price_term" not in blob and "price_m2m" not in blob
        assert "stripe" not in blob.lower()


class TestCheckoutRefusesRatherThanChooses:

    def test_a_monthly_checkout_with_no_commitment_is_refused(self, monkeypatch):
        """THE DEFECT, closed. This used to succeed and silently sell a term."""
        from fastapi import HTTPException
        from app.routers import billing_router

        called = {"purchasable": False}

        def _never(*a, **k):
            called["purchasable"] = True
            return _plan()

        monkeypatch.setattr(billing_catalog, "platform_id_for_org",
                            lambda db, org: "plat_1")
        monkeypatch.setattr(billing_catalog, "require_purchasable", _never)

        class _Org:
            id = "org_1"
            platform_id = "plat_1"
            stripe_subscription_id = None
            billing_status = None

        class _User:
            organization_id = "org_1"

        class _Db:
            def query(self, *a):
                return self

            def filter(self, *a):
                return self

            def first(self):
                return _Org()

        req = billing_router.CheckoutRequest(plan="starter", interval="month")

        with pytest.raises(HTTPException) as exc:
            billing_router.create_checkout(req, _User(), _Db())

        assert exc.value.status_code == 400
        detail = str(exc.value.detail).lower()
        assert "commitment" in detail
        # The refusal must name both options, or the customer cannot answer it.
        assert "month-to-month" in detail and "term" in detail
        assert not called["purchasable"], (
            "the refusal must come BEFORE the plan is resolved — resolving "
            "first is how a default creeps back in")

    def test_the_request_model_still_names_no_price(self):
        from app.routers.billing_router import CheckoutRequest

        fields = set(CheckoutRequest.model_fields)
        assert fields == {"plan", "interval", "commitment"}
        for forbidden in ("price", "amount", "unit_amount", "price_id",
                          "stripe_price_id", "currency"):
            assert forbidden not in fields


class TestTheScreenAsksTheQuestion:
    """Pinned at source level: these are claims about what the customer is
    shown, and a behavioural test cannot make them without a browser."""

    def test_the_default_selection_creates_no_obligation(self):
        code = _code_only(_billing_jsx())
        decl = code.split("useState('month_to_month')", 1)
        assert len(decl) == 2, (
            "the commitment must default to month-to-month — the option that "
            "creates no obligation is the only safe thing to preselect")

    def test_a_committed_term_requires_an_acknowledgment(self):
        code = _code_only(_billing_jsx())

        assert "termAck" in code
        # The gate: a term with no acknowledgment blocks the buttons.
        block = code.split("const commitmentBlocked", 1)
        assert len(block) == 2, "the acknowledgment gate was removed"
        expr = block[1].split(";", 1)[0]
        assert "term_agreement" in expr and "termAck" in expr

    def test_the_acknowledgment_resets_when_the_choice_changes(self):
        """A tick left over from an earlier decision is not an acknowledgment
        of this one."""
        code = _code_only(_billing_jsx())
        setter = code.split("setCommitment(opt.key)", 1)
        assert len(setter) == 2
        assert "setTermAck(false)" in setter[1][:80]

    def test_both_actions_send_the_chosen_commitment(self):
        code = _code_only(_billing_jsx())
        for endpoint in ("'/billing/checkout'", "'/billing/change-plan'",
                         "'/billing/change-plan/preview'"):
            idx = code.index(endpoint)
            call = code[idx:idx + 320]
            assert "commitment" in call, (
                "%s is called without the chosen commitment" % endpoint)

    def test_an_existing_customer_opens_on_their_own_commitment(self):
        """Otherwise every plan card silently offers to change their terms."""
        code = _code_only(_billing_jsx())
        assert "sub?.billing_commitment" in code
        eff = code.split("if (sub?.billing_commitment)", 1)
        assert len(eff) == 2
        assert "setCommitment(sub.billing_commitment)" in eff[1][:160]

    def test_the_card_shows_the_rate_that_was_not_chosen(self):
        """The trade has to be visible. A single price on a card reads as THE
        price, and the customer never learns what the other option costs."""
        code = _code_only(_billing_jsx())
        assert "otherOffer" in code

    def test_an_unavailable_commitment_disables_the_button(self):
        code = _code_only(_billing_jsx())
        assert "Not offered" in code, (
            "a tier with no price at the chosen commitment must say so rather "
            "than falling back to the other rate")
