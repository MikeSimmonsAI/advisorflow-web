"""A PLAN CHANGE CHANGES THE PLAN. IT MUST NOT CHANGE THE DEAL.

Found in production, in the Stripe event log, minutes after the first live
subscription started. The customer was on Starter MONTH-TO-MONTH at $597. They
clicked upgrade. Stripe recorded:

    customer.subscription.updated
    upgraded to Growth — Committed term from Starter — Month-to-month

They asked for a bigger tier and received a CONTRACT. Nobody chose that, no
screen mentioned it, and no policy said to do it: the price was resolved from
(plan, interval) alone, and both of a tier's monthly rates are the `month`
interval, so `price_cents_for` fell through to its documented default — the
committed-term rate — and the committed-term Stripe Price went onto the
subscription.

The rule these tests pin: the commitment CARRIES OVER unless the request names
a different one, and a target with no price at the customer's commitment is
REFUSED rather than quietly substituted.
"""
import os
import re

import pytest

from app.models.billing_models import (BillingCommitment, BillingInterval,
                                       BrandBillingPlan)
from app.services import billing_catalog


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _plan(key="starter", name="Starter", term=50000, m2m=59700,
          term_price="price_term", m2m_price="price_m2m",
          purchasable=True, active=True) -> BrandBillingPlan:
    return BrandBillingPlan(
        key=key, name=name,
        monthly_cents=term, month_to_month_cents=m2m, annual_cents=None,
        stripe_price_id_monthly=term_price,
        stripe_price_id_month_to_month=m2m_price,
        stripe_price_id_annual=None,
        currency="usd",
        is_active=active, is_purchasable=purchasable,
    )


class TestTheCheckoutGateJudgesTheCommitment:
    """`require_purchasable` is the price-tampering defence. It has to know
    which of a tier's two prices is being asked for, or a plan mapped at only
    one commitment passes the gate and gets substituted afterwards."""

    def _gate(self, monkeypatch, plan):
        monkeypatch.setattr(billing_catalog, "resolve_plan",
                            lambda db, pid, key: plan if plan and key == plan.key else None)
        return billing_catalog.require_purchasable

    def test_a_tier_mapped_at_both_commitments_passes_either_way(self, monkeypatch):
        gate = self._gate(monkeypatch, _plan())
        for c in (BillingCommitment.TERM, BillingCommitment.MONTH_TO_MONTH):
            assert gate(None, "p", "starter", BillingInterval.MONTH, c) is not None

    def test_a_tier_with_no_month_to_month_price_is_refused_not_substituted(
            self, monkeypatch):
        """THE WHOLE POINT. Without the commitment in the gate this passes and
        the customer silently lands on the committed-term price."""
        plan = _plan(m2m=None, m2m_price=None)
        gate = self._gate(monkeypatch, plan)

        # The term rate is still perfectly buyable...
        assert gate(None, "p", "starter", BillingInterval.MONTH,
                    BillingCommitment.TERM) is not None

        # ...and month-to-month is refused rather than served the term price.
        with pytest.raises(billing_catalog.PlanNotAvailable) as exc:
            gate(None, "p", "starter", BillingInterval.MONTH,
                 BillingCommitment.MONTH_TO_MONTH)
        assert "month-to-month" in str(exc.value).lower()

    def test_an_unknown_commitment_is_refused(self, monkeypatch):
        gate = self._gate(monkeypatch, _plan())
        with pytest.raises(billing_catalog.PlanNotAvailable):
            gate(None, "p", "starter", BillingInterval.MONTH, "whatever_i_like")

    def test_omitting_the_commitment_still_means_the_term_rate(self, monkeypatch):
        """Unchanged from before the argument existed. Callers that have not
        been taught about commitments behave exactly as they did."""
        gate = self._gate(monkeypatch, _plan(m2m=None, m2m_price=None))
        assert gate(None, "p", "starter", BillingInterval.MONTH) is not None


# ═══════════════════════════════════════════════════════════════════════════
# THE RESOLVER BEHIND BOTH THE PREVIEW AND THE CHANGE
# ═══════════════════════════════════════════════════════════════════════════

class _Org:
    id = "org_1"
    platform_id = "plat_1"
    plan = "starter"
    billing_plan_key = "starter"
    billing_commitment = None
    stripe_subscription_id = "sub_1"
    stripe_plan_interval = "month"
    billing_current_period_end = None


class _Req:
    def __init__(self, plan="growth", interval="month", commitment=None):
        self.plan, self.interval, self.commitment = plan, interval, commitment


@pytest.fixture
def resolver(monkeypatch):
    """`_resolve_change` with the catalogue and the brand policy stubbed."""
    from app.routers import billing_router
    from app.models.billing_models import ChangeTiming, ProrationBehavior

    plans = {"starter": _plan(),
             "growth": _plan("growth", "Growth", 100000, 129700,
                             "price_growth_term", "price_growth_m2m")}

    monkeypatch.setattr(billing_catalog, "platform_id_for_org",
                        lambda db, org: org.platform_id)
    monkeypatch.setattr(billing_catalog, "resolve_plan",
                        lambda db, pid, key: plans.get(key))
    monkeypatch.setattr(billing_router.billing_policy, "change_behavior",
                        lambda db, pid, direction: {
                            "configured": True,
                            "timing": (ChangeTiming.IMMEDIATE
                                       if direction == billing_catalog.UPGRADE
                                       else ChangeTiming.PERIOD_END),
                            "proration": ProrationBehavior.CREATE_PRORATIONS,
                        })
    return billing_router._resolve_change, plans


class TestTheCommitmentCarriesOver:

    def test_a_month_to_month_customer_upgrades_to_month_to_month(self, resolver):
        """THE LIVE DEFECT, pinned. Starter month-to-month -> Growth must land
        on Growth MONTH-TO-MONTH, not on a term agreement."""
        resolve, _ = resolver
        org = _Org()
        org.billing_commitment = BillingCommitment.MONTH_TO_MONTH

        r = resolve(None, org, _Req("growth"))

        assert r["commitment"] == BillingCommitment.MONTH_TO_MONTH
        assert r["commitment_changed"] is False
        assert billing_catalog.stripe_price_id_for(
            r["target"], "month", r["commitment"]) == "price_growth_m2m", (
            "the upgrade must use the month-to-month Stripe Price; the term "
            "price would put the customer under an agreement they never made")

    def test_a_term_customer_upgrades_to_term(self, resolver):
        resolve, _ = resolver
        org = _Org()
        org.billing_commitment = BillingCommitment.TERM

        r = resolve(None, org, _Req("growth"))

        assert r["commitment"] == BillingCommitment.TERM
        assert billing_catalog.stripe_price_id_for(
            r["target"], "month", r["commitment"]) == "price_growth_term"

    def test_an_unknown_commitment_keeps_the_old_behaviour(self, resolver):
        """A subscription older than the column must not change price on its
        next plan change. NULL resolves to the term rate, as it always did."""
        resolve, _ = resolver
        org = _Org()          # billing_commitment stays None

        r = resolve(None, org, _Req("growth"))

        assert r["commitment"] is None
        assert billing_catalog.stripe_price_id_for(
            r["target"], "month", r["commitment"]) == "price_growth_term"

    def test_an_explicit_commitment_overrides_and_is_reported(self, resolver):
        """Changing commitment on purpose is allowed — and flagged, so the
        dialog can tell the customer their terms are changing too."""
        resolve, _ = resolver
        org = _Org()
        org.billing_commitment = BillingCommitment.MONTH_TO_MONTH

        r = resolve(None, org, _Req("growth", commitment=BillingCommitment.TERM))

        assert r["commitment"] == BillingCommitment.TERM
        assert r["commitment_changed"] is True


class TestSameTierDifferentCommitment:
    """Not lateral, and not silent. Moving between a tier's two rates changes
    what the customer pays and what they have promised."""

    def test_taking_on_a_term_is_an_upgrade(self, resolver):
        resolve, _ = resolver
        org = _Org()
        org.billing_commitment = BillingCommitment.MONTH_TO_MONTH

        r = resolve(None, org, _Req("starter", commitment=BillingCommitment.TERM))

        assert r["direction"] == billing_catalog.UPGRADE
        assert r["commitment_changed"] is True

    def test_dropping_a_term_is_a_downgrade(self, resolver):
        """Reducing commitment, exactly as this module already treats a move
        off the annual interval — so the brand's downgrade timing applies and
        the customer is not moved off a term they are still inside."""
        resolve, _ = resolver
        org = _Org()
        org.billing_commitment = BillingCommitment.TERM

        r = resolve(None, org,
                    _Req("starter", commitment=BillingCommitment.MONTH_TO_MONTH))

        assert r["direction"] == billing_catalog.DOWNGRADE

    def test_genuinely_no_change_is_still_refused(self, resolver):
        from fastapi import HTTPException

        resolve, _ = resolver
        org = _Org()
        org.billing_commitment = BillingCommitment.MONTH_TO_MONTH

        with pytest.raises(HTTPException) as exc:
            resolve(None, org,
                    _Req("starter", commitment=BillingCommitment.MONTH_TO_MONTH))
        assert exc.value.status_code == 400


class TestRefusalsRatherThanSubstitutions:

    def test_a_target_unmapped_at_this_commitment_is_refused(self, monkeypatch,
                                                            resolver):
        """A month-to-month customer moving to a tier that only has a term
        price must be REFUSED. The alternative is the defect: they would be
        moved onto the committed rate by a missing-configuration technicality.
        """
        from fastapi import HTTPException

        resolve, plans = resolver
        plans["growth"].month_to_month_cents = None
        plans["growth"].stripe_price_id_month_to_month = None

        org = _Org()
        org.billing_commitment = BillingCommitment.MONTH_TO_MONTH

        with pytest.raises(HTTPException) as exc:
            resolve(None, org, _Req("growth"))

        # 409 — the request is fine, the CONFIGURATION is missing.
        assert exc.value.status_code == 409
        assert "month-to-month" in str(exc.value.detail).lower()

    def test_no_subscription_is_refused_before_anything_else(self, resolver):
        from fastapi import HTTPException

        resolve, _ = resolver
        org = _Org()
        org.stripe_subscription_id = None

        with pytest.raises(HTTPException) as exc:
            resolve(None, org, _Req("growth"))
        assert exc.value.status_code == 409

    def test_an_unconfigured_policy_is_refused_rather_than_guessed(
            self, monkeypatch, resolver):
        from fastapi import HTTPException
        from app.routers import billing_router

        resolve, _ = resolver
        monkeypatch.setattr(billing_router.billing_policy, "change_behavior",
                            lambda db, pid, direction: {"configured": False,
                                                        "timing": None,
                                                        "proration": None})
        with pytest.raises(HTTPException) as exc:
            resolve(None, _Org(), _Req("growth"))
        assert exc.value.status_code == 409
        assert "policy" in str(exc.value.detail).lower()


# ═══════════════════════════════════════════════════════════════════════════
# THE CONFIRMATION THE CUSTOMER SEES
# ═══════════════════════════════════════════════════════════════════════════

def _billing_jsx() -> str:
    with open(os.path.join(ROOT, "frontend", "src", "pages", "Billing.jsx"),
              "r", encoding="utf-8") as fh:
        return fh.read()


def _code_only(src: str) -> str:
    """Strip // and /* */ comments so a comment describing the old behaviour
    cannot satisfy — or break — an assertion about the code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


class TestNoNativeConfirmForPlanChanges:

    def test_the_plan_change_no_longer_uses_a_browser_confirm(self):
        """A native confirm() cannot show a price, cannot show two plans side
        by side, and states whatever sentence is hardcoded into the browser —
        which was the EvoSys policy, shown to every brand on the platform."""
        code = _code_only(_billing_jsx())
        block = code.split("async function handleChangePlan", 1)
        assert len(block) == 2, "handleChangePlan moved — re-pin this test"
        body = block[1].split("async function", 1)[0]

        assert "window.confirm" not in body, (
            "the plan change is back on a native confirm dialog")
        assert "change-plan/preview" in body, (
            "the dialog must be filled from the server's own preview, not "
            "from text written in the browser")

    def test_the_dialog_renders_the_facts_a_customer_needs(self):
        code = _code_only(_billing_jsx())
        dialog = code.split("function PlanChangeDialog", 1)[1].split(
            "\nfunction ", 1)[0]

        for label in ("Current plan", "New plan", "Current amount",
                      "New amount", "Billing frequency", "Commitment",
                      "Takes effect"):
            assert label in dialog, "the dialog does not show %r" % label

        assert "Cancel" in dialog and "actionLabel" in dialog

    def test_success_is_not_claimed_before_the_server_answers(self):
        """setNotice must come AFTER the await, inside the try — never
        optimistically alongside the request."""
        code = _code_only(_billing_jsx())
        body = code.split("async function confirmChangePlan", 1)[1].split(
            "\n  async function", 1)[0]

        post_at = body.index("api.post('/billing/change-plan'")
        notice_at = body.index("setNotice(")
        assert notice_at > post_at, (
            "the success message is set before the server has confirmed the "
            "change")

    def test_a_failure_keeps_the_dialog_open_and_says_so(self):
        code = _code_only(_billing_jsx())
        body = code.split("async function confirmChangePlan", 1)[1].split(
            "\n  async function", 1)[0]
        catch = body.split("catch", 1)[1]

        assert "setChangeError(" in catch
        assert "setPendingChange(null)" not in catch, (
            "closing the dialog on failure reads as though the change went "
            "through")
