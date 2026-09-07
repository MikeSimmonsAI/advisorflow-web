"""FROM A STRIPE PAYMENT TO THE ONE COMPENSATION ENGINE — AND NO FURTHER.

`billing_compensation` contains no commission math. Its whole job is
RESOLUTION and REFUSAL: which sold deal a payment belongs to, a clear written
reason when it cannot tell, and one call to `compensation.earn()` with a
stable reference.

WHAT THIS FILE DEFENDS

  1. A COLLECTED SAAS PAYMENT EARNS WHAT THE PLAN SAYS — not a number this
     layer computed.
  2. ONE PAYMENT PAYS ONCE. A retried event, a sibling event, and a direct
     double call to `earn_for_payment` all produce one set of entries.
  3. AN UNCONFIGURED RULE INVENTS NOTHING. Zero is recorded as a real outcome
     with a reason on the payment row, because an unpaid commission and a bug
     look identical without one.
  4. A FAILED PAYMENT IS NOT COLLECTED FUNDS.
  5. THE HOLDBACK COMES FROM THE PLAN.
  6. NOTHING ROLLS BACK PAID COMPENSATION — not a cancellation, not a refund.

Nothing here reaches Stripe: these tests drive `billing_webhook.handle_event`
and `billing_compensation` directly with the payload shape Stripe sends.
"""

import itertools
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.billing_models import (BillingEvent, BillingInvoice,
                                       BillingPayment, BrandBillingPlan,
                                       SubscriptionStatus)
from app.models.compensation_models import (BASIS_FIXED, COMP_EARNED, COMP_PAID,
                                            COMP_PAYABLE, PAYEE_SELLER,
                                            CompensationEntry,
                                            CompensationPlan, CompensationRule)
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.models.sales_models import (ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     BrandPackage, BrandSalesOrg, Membership,
                                     Opportunity)
from app.services import billing_compensation, billing_webhook
from app.services import compensation as comp
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _brand(db, label="Alpha", *, seller=Decimal("500.00"), holdback=14):
    """A brand that sells, with a configured compensation plan, plus its own
    customer SaaS catalogue. The two catalogues key on the same words and are
    deliberately never mapped to each other."""
    platform = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(platform)
    db.commit()

    sales_org = BrandSalesOrg(platform_id=platform.id, name=label + " Sales",
                              slug="%s-s-%d" % (label.lower(), next(_SEQ)))
    db.add(sales_org)
    db.commit()

    # The SALES catalogue: a one-time implementation fee.
    pkg = BrandPackage(platform_id=platform.id, name="Starter",
                       key="starter-%d" % next(_SEQ),
                       price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13, currency="USD")
    # A second package nobody has written a compensation rule for.
    unconfigured = BrandPackage(platform_id=platform.id, name="Growth",
                                key="growth-%d" % next(_SEQ),
                                price=Decimal("2495.00"),
                                setup_fee=Decimal("2495.00"), currency="USD")
    db.add_all([pkg, unconfigured])
    db.commit()

    # The BILLING catalogue: what the customer pays every month.
    db.add(BrandBillingPlan(platform_id=platform.id, key="starter",
                            name="Starter", monthly_cents=49700,
                            currency="usd", is_purchasable=True,
                            is_active=True))
    db.commit()

    rep = _user(db, label + " Rep")
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=sales_org.id, role=ROLE_SALES_REP,
                      is_active=True))
    db.commit()

    plan = CompensationPlan(brand_sales_org_id=sales_org.id,
                            name=label + " Plan",
                            effective_from=date(2026, 1, 1),
                            holdback_days=holdback, max_override_levels=1,
                            is_active=True)
    db.add(plan)
    db.commit()
    db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                            payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                            amount=seller, sort_order=1))
    db.commit()
    return dict(platform=platform, sales_org=sales_org, pkg=pkg,
                unconfigured_pkg=unconfigured, rep=rep, plan=plan)


def _sold_customer(db, brand, *, package=None, status="won",
                   with_implementation=True):
    """A paying customer organization joined to the deal that sold it.

    `Organization` has no opportunity_id: the customer-tenant tree and the
    brand-sales tree meet only at `implementations`, so the join goes the long
    way round rather than being invented here.
    """
    n = next(_SEQ)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=brand["platform"].id, plan="starter",
                       billing_plan_key="starter", is_active=True,
                       stripe_customer_id="cus_sold_%d" % n,
                       stripe_subscription_id="sub_sold_%d" % n,
                       billing_status="active")
    db.add(org)
    db.commit()

    opp = Opportunity(brand_sales_org_id=brand["sales_org"].id,
                      owner_user_id=brand["rep"].id,
                      company_name=org.name,
                      selected_package_id=(package or brand["pkg"]).id,
                      stage="closing", status=status,
                      billing_option="term_agreement",
                      contract_term_months=13,
                      customer_organization_id=org.id)
    db.add(opp)
    db.commit()

    if with_implementation:
        db.add(Implementation(opportunity_id=opp.id, organization_id=org.id,
                              platform_id=brand["platform"].id,
                              brand_sales_org_id=brand["sales_org"].id,
                              package_id=(package or brand["pkg"]).id,
                              sold_by_user_id=brand["rep"].id))
        db.commit()
    return {"org": org, "opportunity": opp}


def _unix(dt):
    """Naive UTC -> Stripe's unix seconds, independent of the machine's zone."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _invoice(org, *, invoice_id, amount=49700, status="paid", paid_at=None,
             billing_reason="subscription_cycle"):
    paid_at = paid_at or datetime(2026, 9, 1, 12, 0, 0)
    return {
        "id": invoice_id, "object": "invoice",
        "customer": org.stripe_customer_id,
        "subscription": org.stripe_subscription_id,
        "status": status, "currency": "usd",
        "amount_due": amount,
        "amount_paid": amount if status == "paid" else 0,
        "billing_reason": billing_reason,
        "period_start": _unix(datetime(2026, 9, 1)),
        "period_end": _unix(datetime(2026, 10, 1)),
        "status_transitions": {"paid_at": _unix(paid_at)},
        "payment_intent": "pi_%s" % invoice_id,
        "charge": "ch_%s" % invoice_id,
    }


def _event(event_type, obj, evt_id=None):
    return {"id": evt_id or "evt_test_%d" % next(_SEQ), "type": event_type,
            "data": {"object": obj}}


@pytest.fixture()
def brand(db_session):
    return _brand(db_session, "Alpha")


@pytest.fixture()
def sold(db_session, brand):
    return _sold_customer(db_session, brand)


# ═══════════════════════════════════════════════════════════════════════════
# 15 & 18. A COLLECTED SAAS PAYMENT EARNS WHAT THE PLAN SAYS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_paid_invoice_earns_compensation_for_the_deal_that_sold_it(
        db_session, brand, sold):
    result = billing_webhook.handle_event(db_session, _event(
        "invoice.paid",
        _invoice(sold["org"], invoice_id="in_earn_1",
                 billing_reason="subscription_create"),
        "evt_earn_1"))

    assert result["earned_compensation"] is True

    entries = db_session.query(CompensationEntry).all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.payee_user_id == brand["rep"].id
    assert entry.payee_kind == PAYEE_SELLER
    assert entry.amount == Decimal("500.00")       # plan configuration
    assert entry.state == COMP_EARNED
    # The SAME string on the payment row and the compensation entry, so the
    # two cannot disagree about how many payments there were.
    assert entry.collection_reference == "stripe:in_earn_1"

    payment = db_session.query(BillingPayment).one()
    assert payment.earned_compensation is True
    assert payment.compensation_skipped_reason is None
    assert payment.opportunity_id == sold["opportunity"].id

    event_row = (db_session.query(BillingEvent)
                 .filter(BillingEvent.stripe_event_id == "evt_earn_1").one())
    assert event_row.earned_compensation is True
    assert "1 compensation entr" in (event_row.compensation_note or "")


def test_a_recurring_saas_payment_earns_again_because_it_is_different_money(
        db_session, brand, sold):
    """Month one and month two are two collections, not a duplicate."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_m1",
                                 billing_reason="subscription_create"),
        "evt_m1"))
    second = billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_m2",
                                 billing_reason="subscription_cycle"),
        "evt_m2"))

    assert second["earned_compensation"] is True
    entries = db_session.query(CompensationEntry).all()
    assert len(entries) == 2
    assert {e.collection_reference for e in entries} == {"stripe:in_m1",
                                                         "stripe:in_m2"}
    payments = {p.collection_reference: p
                for p in db_session.query(BillingPayment).all()}
    assert payments["stripe:in_m1"].is_initial is True
    assert payments["stripe:in_m2"].is_initial is False


# ═══════════════════════════════════════════════════════════════════════════
# 16, 13 & 30. ONE PAYMENT PAYS ONCE, THREE GUARDS DEEP
# ═══════════════════════════════════════════════════════════════════════════

def test_a_duplicate_event_does_not_duplicate_compensation_entries(
        db_session, sold):
    """Guard 1: the unique insert on stripe_event_id. The retry does nothing."""
    event = _event("invoice.paid",
                   _invoice(sold["org"], invoice_id="in_dupe_1"),
                   "evt_dupe_1")
    first = billing_webhook.handle_event(db_session, event)
    second = billing_webhook.handle_event(db_session, event)

    assert first["earned_compensation"] is True
    assert second["duplicate"] is True
    assert second.get("earned_compensation") is not True
    assert db_session.query(CompensationEntry).count() == 1
    assert db_session.query(BillingPayment).count() == 1


def test_two_related_events_for_one_invoice_pay_one_commission(
        db_session, sold):
    """Guard 2: the collection reference comes from the INVOICE, so the pair
    of events that describe one card charge collapse to one payment — and
    therefore to one commission."""
    invoice = _invoice(sold["org"], invoice_id="in_sibling_1")
    billing_webhook.handle_event(
        db_session, _event("invoice.paid", invoice, "evt_sib_a"))
    sibling = billing_webhook.handle_event(
        db_session, _event("invoice.payment_succeeded", invoice, "evt_sib_b"))

    assert sibling["duplicate"] is False        # a genuinely different event
    assert sibling["earned_compensation"] is False
    assert db_session.query(CompensationEntry).count() == 1
    assert db_session.query(BillingPayment).count() == 1
    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_sib_b").one())
    assert "already recorded" in (row.detail or "")


def test_earning_twice_for_one_payment_yields_one_set_of_entries(
        db_session, sold):
    """Guard 3: even a direct double call returns what already exists. This is
    the concurrency case — two deliveries in flight at once."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_race_1"),
        "evt_race_1"))
    payment = db_session.query(BillingPayment).one()
    first_ids = {e.id for e in db_session.query(CompensationEntry).all()}

    again = billing_compensation.earn_for_payment(db_session, payment)
    assert again["earned"] is True
    assert again["entries"] == len(first_ids)
    assert {e.id for e in db_session.query(CompensationEntry).all()} == first_ids
    assert db_session.query(CompensationEntry).count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# 17. A FAILED PAYMENT IS NOT COLLECTED FUNDS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_failed_invoice_creates_no_earned_compensation(db_session, sold):
    """There is no path from the failed-payment handler to the engine at all."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.payment_failed",
        _invoice(sold["org"], invoice_id="in_fail_1", status="open"),
        "evt_fail_1"))

    assert db_session.query(CompensationEntry).count() == 0
    assert db_session.query(BillingPayment).count() == 0
    db_session.refresh(sold["org"])
    assert sold["org"].billing_status == SubscriptionStatus.PAST_DUE
    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_fail_1").one())
    assert row.earned_compensation is False


def test_a_zero_amount_payment_earns_nothing_and_says_why(db_session, sold):
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_zero_1", amount=0),
        "evt_zero_1"))
    payment = db_session.query(BillingPayment).one()
    assert payment.amount_cents == 0
    assert payment.earned_compensation is False
    assert payment.compensation_skipped_reason == \
        billing_compensation.SKIP_ZERO_AMOUNT
    assert db_session.query(CompensationEntry).count() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 19. AN UNCONFIGURED RULE INVENTS NO COMMISSION
# ═══════════════════════════════════════════════════════════════════════════

def test_an_unconfigured_compensation_rule_invents_no_commission(
        db_session, brand):
    """Growth and Professional direct-sale compensation are deliberately
    UNCONFIGURED. A payment against those must earn nothing and SAY SO — never
    fall back to Starter's numbers or to a percentage somebody assumed."""
    sold = _sold_customer(db_session, brand,
                          package=brand["unconfigured_pkg"])
    result = billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_unconf_1",
                                 amount=99700), "evt_unconf_1"))

    assert result["earned_compensation"] is False

    # The money IS banked. Only the commission is refused.
    payment = db_session.query(BillingPayment).one()
    assert payment.amount_cents == 99700
    assert payment.earned_compensation is False
    assert payment.compensation_skipped_reason
    assert billing_compensation.SKIP_NO_RULE in \
        payment.compensation_skipped_reason
    assert db_session.query(CompensationEntry).count() == 0

    event_row = (db_session.query(BillingEvent)
                 .filter(BillingEvent.stripe_event_id == "evt_unconf_1").one())
    assert event_row.earned_compensation is False
    assert "No compensation earned" in (event_row.compensation_note or "")


def test_a_customer_nobody_sold_earns_nothing_and_says_why(db_session, brand):
    """Self-serve signup, migration, manual onboarding. No Implementation
    links the organization to a deal, and inventing one would fabricate a
    relationship between a paying customer and somebody's commission."""
    sold = _sold_customer(db_session, brand, with_implementation=False)
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_noimpl_1"),
        "evt_noimpl_1"))

    payment = db_session.query(BillingPayment).one()
    assert payment.earned_compensation is False
    assert payment.compensation_skipped_reason == \
        billing_compensation.SKIP_NO_IMPLEMENTATION
    assert payment.opportunity_id is None
    assert db_session.query(CompensationEntry).count() == 0


def test_a_payment_against_a_deal_that_is_not_won_earns_nothing(
        db_session, brand):
    """A deal reopened, or provisioned early. Not an error, not a commission."""
    sold = _sold_customer(db_session, brand, status="open")
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_notwon_1"),
        "evt_notwon_1"))

    payment = db_session.query(BillingPayment).one()
    assert payment.earned_compensation is False
    assert payment.compensation_skipped_reason == \
        billing_compensation.SKIP_NOT_WON
    assert db_session.query(CompensationEntry).count() == 0


def test_no_compensation_plan_for_the_brand_earns_nothing(db_session, brand):
    """`NotEarnable` from the engine is the engine saying, correctly, that no
    configured rule covers this. It is not an error to route around and not an
    invitation to compute something here."""
    sold = _sold_customer(db_session, brand)
    brand["plan"].is_active = False
    db_session.commit()

    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_noplan_1"),
        "evt_noplan_1"))

    payment = db_session.query(BillingPayment).one()
    assert payment.earned_compensation is False
    assert billing_compensation.SKIP_NO_RULE in \
        payment.compensation_skipped_reason
    assert db_session.query(CompensationEntry).count() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 20. THE HOLDBACK IS PLAN CONFIGURATION, NOT A CONSTANT
# ═══════════════════════════════════════════════════════════════════════════

def test_the_payable_date_comes_from_the_plans_holdback(db_session):
    """Alpha holds 14 days, Beta holds 45. Same engine, same webhook, two
    different payout dates — because the number lives on the plan."""
    alpha = _brand(db_session, "Alpha", holdback=14)
    beta = _brand(db_session, "Beta", holdback=45)
    paid_at = datetime(2026, 9, 1, 12, 0, 0)

    a_sold = _sold_customer(db_session, alpha)
    b_sold = _sold_customer(db_session, beta)

    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(a_sold["org"], invoice_id="in_hold_a",
                                 paid_at=paid_at), "evt_hold_a"))
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(b_sold["org"], invoice_id="in_hold_b",
                                 paid_at=paid_at), "evt_hold_b"))

    entries = {e.collection_reference: e
               for e in db_session.query(CompensationEntry).all()}
    assert entries["stripe:in_hold_a"].collected_at == paid_at
    assert entries["stripe:in_hold_a"].payable_at == paid_at + timedelta(days=14)
    assert entries["stripe:in_hold_b"].payable_at == paid_at + timedelta(days=45)


def test_a_later_holdback_change_does_not_move_a_promised_payout_date(
        db_session, brand, sold):
    """Stored, not derived on read."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_hold_c",
                                 paid_at=datetime(2026, 9, 1, 12, 0, 0)),
        "evt_hold_c"))
    entry = db_session.query(CompensationEntry).one()
    promised = entry.payable_at

    brand["plan"].holdback_days = 120
    db_session.commit()
    db_session.refresh(entry)
    assert entry.payable_at == promised


# ═══════════════════════════════════════════════════════════════════════════
# 22. CANCELLATION PRESERVES COMPENSATION HISTORY
# ═══════════════════════════════════════════════════════════════════════════

def test_cancellation_preserves_compensation_history(db_session, brand, sold):
    """A subscription ending does not un-earn what a salesperson was owed on
    the money that already arrived."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_cancel_1"),
        "evt_cancel_pay"))
    before = [(e.id, e.amount, e.state, e.payable_at, e.collection_reference)
              for e in db_session.query(CompensationEntry).all()]
    assert before

    billing_webhook.handle_event(db_session, _event(
        "customer.subscription.deleted",
        {"id": sold["org"].stripe_subscription_id, "object": "subscription",
         "customer": sold["org"].stripe_customer_id, "status": "canceled",
         "cancel_at_period_end": False, "items": {"data": []}, "metadata": {}},
        "evt_cancel_sub"))

    db_session.refresh(sold["org"])
    assert sold["org"].billing_status == SubscriptionStatus.CANCELED

    after = [(e.id, e.amount, e.state, e.payable_at, e.collection_reference)
             for e in db_session.query(CompensationEntry).all()]
    assert after == before
    payment = db_session.query(BillingPayment).one()
    assert payment.earned_compensation is True


# ═══════════════════════════════════════════════════════════════════════════
# 23. A REFUND IS RECORDED AND NEVER REWRITES PAID COMPENSATION
# ═══════════════════════════════════════════════════════════════════════════

def test_a_refund_is_recorded_without_mutating_paid_compensation(
        db_session, brand, sold):
    """Paid compensation is a record of money that left the business and
    reached a person. Editing it to match a later reversal would make the
    ledger disagree with the bank, silently."""
    god = _user(db_session, "Owner", role="god_admin")
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid",
        _invoice(sold["org"], invoice_id="in_refund_1",
                 paid_at=datetime.utcnow() - timedelta(days=30)),
        "evt_refund_pay"))

    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).one()
    assert entry.state == COMP_PAYABLE
    comp.mark_paid(db_session, entry, payment_reference="ACH-1",
                   paid_by=god.id)
    before = (entry.amount, entry.state, entry.paid_at, entry.payment_reference)

    payment = db_session.query(BillingPayment).one()
    outcome = billing_compensation.record_refund(db_session, payment,
                                                 refunded_cents=49700)

    assert outcome["recorded"] is True
    assert outcome["refunded_cents"] == 49700
    assert outcome["compensation_adjusted"] is False
    assert outcome["reason"] == "clawback_policy_required"

    db_session.refresh(payment)
    assert payment.refunded_cents == 49700
    assert payment.refunded_at is not None
    # The payment still records that it earned; that is a historical fact.
    assert payment.earned_compensation is True

    db_session.refresh(entry)
    assert (entry.amount, entry.state, entry.paid_at,
            entry.payment_reference) == before
    assert entry.state == COMP_PAID
    assert db_session.query(CompensationEntry).count() == 1


def test_a_refund_webhook_records_against_the_original_payment(
        db_session, sold):
    """charge.refunded resolves back to the same collection reference, so the
    refund lands on the payment it reverses rather than creating a new row."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(sold["org"], invoice_id="in_refund_2"),
        "evt_refund_pay_2"))

    billing_webhook.handle_event(db_session, _event("charge.refunded", {
        "id": "ch_in_refund_2", "object": "charge",
        "customer": sold["org"].stripe_customer_id,
        "invoice": "in_refund_2", "payment_intent": "pi_in_refund_2",
        "amount_refunded": 20000}, "evt_refunded_2"))

    payments = db_session.query(BillingPayment).all()
    assert len(payments) == 1
    assert payments[0].refunded_cents == 20000
    # Compensation is untouched.
    assert db_session.query(CompensationEntry).count() == 1
    assert db_session.query(CompensationEntry).one().state == COMP_EARNED

    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_refunded_2").one())
    assert "deliberately unchanged" in (row.detail or "")


def test_a_refund_for_an_unknown_payment_is_recorded_only(db_session, sold):
    billing_webhook.handle_event(db_session, _event("charge.refunded", {
        "id": "ch_unknown", "object": "charge",
        "customer": sold["org"].stripe_customer_id,
        "invoice": "in_never_seen", "amount_refunded": 5000},
        "evt_refunded_orphan"))
    assert db_session.query(BillingPayment).count() == 0
    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_refunded_orphan").one())
    assert "no local payment row" in (row.detail or "")
