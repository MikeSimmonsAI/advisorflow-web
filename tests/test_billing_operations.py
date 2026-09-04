"""P4 — Stripe operations: customers, invoices, subscriptions, payments.

WHAT THIS FILE IS ACTUALLY DEFENDING

Three failure modes cost real money, and each has its own block below.

  A RETRY IS NOT A SECOND CHARGE. Every create is exercised twice and the
  second attempt must not reach Stripe. The local guard is asserted first
  because the idempotency key is a backstop, not the plan.

  MONEY IS COPIED, NEVER DERIVED. The subscription tests assert the exact
  integer minor units and the exact currency that reached Stripe, against the
  BillingAgreement they came from. Nothing is allowed to consult a price list.

  ANOTHER TENANT'S IDS ARE NOT A KEY. Every operation that accepts an id is
  called with a real id belonging to a different organization, and must refuse
  it the same way it refuses one that was invented.

Stripe itself is faked. That is deliberate: these tests pin down what THIS
code does with money and with tenancy, which is the part that can be wrong in a
way Stripe would happily execute. Behaviour against the real sandbox is covered
by docs/billing/STRIPE_SANDBOX_TEST_PLAN.md, which a human runs.
"""

import itertools
from datetime import datetime
from decimal import Decimal

import pytest

from app.models.billing_agreement_models import (AGREEMENT_ACTIVE,
                                                 AGREEMENT_CANCELLED,
                                                 AGREEMENT_DRAFT,
                                                 AGREEMENT_PAST_DUE,
                                                 BillingAgreement)
from app.models.billing_models import Invoice, Payment
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.services import billing_agreement as agreements
from app.services import billing_operations as ops
from app.services import merchant_entity as entity_svc
from app.services import stripe_gateway as gw
from app.services.auth_service import hash_password
from app.services.billing_access import BillingScope
from app.services.billing_operations import BillingOperationRefused
from app.services.stripe_gateway import (LiveModeRefused, StripeOperationFailed,
                                         StripeUnavailable)


# ═════════════════════════════════════════════════════════════════════════════
# A fake Stripe
#
# Records every call with its kwargs so a test can assert the AMOUNT, the
# CURRENCY and the IDEMPOTENCY KEY that actually crossed the boundary, rather
# than asserting that our own function returned what we told it to.
# ═════════════════════════════════════════════════════════════════════════════

class _Obj(dict):
    """Stripe objects answer to both attribute and key access; so does this."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


class FakeStripe:

    def __init__(self):
        self.calls = []
        self.fail_with = {}      # "Customer.create" -> exception to raise
        self._seq = 0
        s = self

        def resource(name, **makers):
            ns = type(name, (), {})
            for op, make in makers.items():
                def bound(*args, _op=op, _make=make, _name=name, **kwargs):
                    return s._record("%s.%s" % (_name, _op), _make,
                                     args, kwargs)
                bound.__name__ = "%s_%s" % (name, op)
                setattr(ns, op, staticmethod(bound))
            return ns

        self.Customer = resource(
            "Customer", create=lambda a, k: _Obj(id=s._id("cus")))
        self.Product = resource(
            "Product", create=lambda a, k: _Obj(id=s._id("prod")))
        self.Price = resource(
            "Price", create=lambda a, k: _Obj(id=s._id("price"),
                                              unit_amount=k.get("unit_amount"),
                                              currency=k.get("currency")))
        self.Subscription = resource(
            "Subscription",
            create=lambda a, k: _Obj(id=s._id("sub"), status="active"),
            retrieve=lambda a, k: _Obj(id=a[0], status="active",
                                       current_period_end=1790000000),
            modify=lambda a, k: _Obj(id=a[0], status="active",
                                     cancel_at_period_end=True),
            delete=lambda a, k: _Obj(id=a[0], status="canceled"))
        self.InvoiceItem = resource(
            "InvoiceItem", create=lambda a, k: _Obj(id=s._id("ii")))
        self.Invoice = resource(
            "Invoice",
            create=lambda a, k: _Obj(id=s._id("in")),
            finalize_invoice=lambda a, k: s.invoice_payload(a[0], "open"),
            send_invoice=lambda a, k: s.invoice_payload(a[0], "open"),
            void_invoice=lambda a, k: s.invoice_payload(a[0], "void"))

    # -- helpers ------------------------------------------------------------

    def _id(self, prefix):
        self._seq += 1
        return "%s_fake%03d" % (prefix, self._seq)

    def _record(self, op, make, args, kwargs):
        self.calls.append((op, args, kwargs))
        if op in self.fail_with:
            raise self.fail_with[op]
        return make(args, kwargs)

    def customer_of(self, op="Customer.create"):
        return [c for c in self.calls if c[0] == op]

    def ops_named(self, op):
        return [c for c in self.calls if c[0] == op]

    def kwargs_of(self, op):
        return [c[2] for c in self.calls if c[0] == op]

    def invoice_payload(self, invoice_id, status, **over):
        payload = {
            "id": invoice_id,
            "customer": self.customer_id,
            "number": "INV-0001",
            "status": status,
            "collection_method": "send_invoice",
            "currency": "usd",
            "subtotal": 49900,
            "total": 49900,
            "amount_due": 0 if status == "void" else 49900,
            "amount_paid": 0,
            "hosted_invoice_url": "https://invoice.stripe.test/%s" % invoice_id,
            "invoice_pdf": "https://invoice.stripe.test/%s.pdf" % invoice_id,
            "description": "Monthly service",
            "status_transitions": {},
        }
        payload.update(over)
        return _Obj(payload)


@pytest.fixture
def stripe(monkeypatch):
    """A fake Stripe wired in at the gateway's single seam.

    `gw.call` is left ALONE on purpose - the real one runs, so these tests also
    exercise the idempotency-key plumbing and the error translation rather than
    stepping over them.
    """
    fake = FakeStripe()
    fake.customer_id = None
    monkeypatch.setattr(gw, "client", lambda: fake)
    monkeypatch.setattr(gw, "_stripe", lambda: fake)
    return fake


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures-as-helpers
# ═════════════════════════════════════════════════════════════════════════════

def _brand(db, slug="evosyspro", name="EvoSys Pro"):
    existing = db.query(Platform).filter(Platform.slug == slug).first()
    if existing:
        return existing
    p = Platform(name=name, slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name="Restland", platform=None, stripe_customer_id=None,
         billing_status=None):
    o = Organization(name=name, slug=name.lower().replace(" ", "-"),
                     plan="standard",
                     platform_id=platform.id if platform else None,
                     stripe_customer_id=stripe_customer_id)
    if billing_status is not None:
        o.billing_status = billing_status
    db.add(o)
    db.commit()
    return o


_EMAIL_SEQ = itertools.count(1)


def _user(db, org, role="org_admin", email=None):
    u = User(organization_id=org.id if org else None,
             email=email or ("billing%d@example.com" % next(_EMAIL_SEQ)),
             password_hash=hash_password("TestPass123!"),
             full_name="Billing Person", role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _scope(db, org, can_view=True, can_manage=True):
    """A BillingScope built directly.

    P3 already proves how a scope is DERIVED, with negative tests for every
    way it can be wrong. Constructing one here keeps these tests about what
    the operations do with the authority they are handed.

    One user per organization, reused: hashing a password is slow and a second
    identity would prove nothing these tests are about.
    """
    key = org.id if org is not None else None
    user = _USERS.get(key)
    if user is None:
        user = _USERS[key] = _user(db, org)
    return BillingScope(user, org, can_view, can_manage)


@pytest.fixture(autouse=True)
def _reset_user_cache():
    _USERS.clear()
    yield
    _USERS.clear()


_USERS = {}


def _sales_org(db, platform):
    existing = (db.query(BrandSalesOrg)
                .filter(BrandSalesOrg.platform_id == platform.id).first())
    if existing:
        return existing
    so = BrandSalesOrg(platform_id=platform.id, name=platform.name + " Sales",
                       slug=platform.slug + "-sales")
    db.add(so)
    db.commit()
    return so


def _agreement(db, org, platform, *, recurring="499.00", setup="1500.00",
               currency="USD", status=AGREEMENT_ACTIVE, interval="month"):
    """A real agreement through the real P2 path - opportunity, implementation
    and all. Hand-built rows would let a schema change pass unnoticed."""
    entity_svc.ensure_evosys_pro_configuration(db)
    opp = Opportunity(company_name=org.name, status="won",
                      brand_sales_org_id=_sales_org(db, platform).id)
    db.add(opp)
    db.commit()
    impl = Implementation(
        organization_id=org.id, platform_id=platform.id,
        opportunity_id=opp.id, billing_option="term_agreement",
        contract_term_months=13,
        implementation_fee=Decimal(setup) if setup else None,
        recurring_amount=Decimal(recurring) if recurring else None,
        currency=currency, billing_start_date=datetime(2026, 10, 1))
    db.add(impl)
    db.commit()
    agreement = agreements.create_from_implementation(db, impl)
    if agreement.status != status:
        agreement.status = status
        agreement.billing_interval = interval
        db.commit()
    else:
        agreement.billing_interval = interval
        db.commit()
    return agreement


def _configured(db, name="Restland", **kw):
    brand = _brand(db)
    org = _org(db, name, platform=brand, **kw)
    return brand, org


def _invoice_row(db, org, *, stripe_id="in_local001", status="open",
                 total=49900, due=49900, number="INV-0001"):
    row = Invoice(organization_id=org.id, stripe_invoice_id=stripe_id,
                  stripe_customer_id=org.stripe_customer_id, number=number,
                  status=status, currency="USD", total_cents=total,
                  amount_due_cents=due, amount_paid_cents=0,
                  hosted_invoice_url="https://invoice.stripe.test/%s" % stripe_id,
                  invoice_pdf="https://invoice.stripe.test/%s.pdf" % stripe_id)
    db.add(row)
    db.commit()
    return row


def _payment_row(db, org, *, status="succeeded", amount=49900,
                 intent="pi_001", invoice_id=None, failure=None):
    p = Payment(organization_id=org.id, invoice_id=invoice_id,
                stripe_payment_intent_id=intent, amount_cents=amount,
                currency="USD", status=status, refunded_cents=0,
                payment_method_brand="visa", payment_method_last4="4242",
                failure_message=failure)
    db.add(p)
    db.commit()
    return p


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOMERS
# ═════════════════════════════════════════════════════════════════════════════

def test_an_existing_customer_is_reused_and_stripe_is_not_called(db_session, stripe):
    """The cheapest duplicate-customer bug is the one where the id was already
    sitting on the row."""
    brand, org = _configured(db_session, stripe_customer_id="cus_already")
    scope = _scope(db_session, org)

    assert ops.ensure_customer(db_session, scope) == "cus_already"
    assert stripe.calls == []


def test_a_customer_is_created_and_the_id_is_persisted(db_session, stripe):
    brand, org = _configured(db_session)
    scope = _scope(db_session, org)

    customer_id = ops.ensure_customer(db_session, scope)

    assert customer_id.startswith("cus_")
    db_session.refresh(org)
    assert org.stripe_customer_id == customer_id


def test_customer_creation_carries_a_stable_idempotency_key(db_session, stripe):
    """A key derived from the organization, so two racing callers collapse into
    one customer instead of two."""
    brand, org = _configured(db_session)
    ops.ensure_customer(db_session, _scope(db_session, org))

    key = stripe.kwargs_of("Customer.create")[0]["idempotency_key"]
    assert key == "customer:%s" % org.id


def test_a_second_call_does_not_create_a_second_customer(db_session, stripe):
    brand, org = _configured(db_session)
    scope = _scope(db_session, org)

    first = ops.ensure_customer(db_session, scope)
    second = ops.ensure_customer(db_session, scope)

    assert first == second
    assert len(stripe.ops_named("Customer.create")) == 1


def test_customer_metadata_names_the_organization_not_just_a_name(db_session, stripe):
    """Metadata is what makes a Stripe object traceable back here during a
    reconciliation. A display name alone is not enough."""
    brand, org = _configured(db_session)
    ops.ensure_customer(db_session, _scope(db_session, org))

    metadata = stripe.kwargs_of("Customer.create")[0]["metadata"]
    assert metadata["organization_id"] == org.id
    assert metadata["platform_id"] == brand.id


def test_customer_metadata_records_the_legal_seller_when_there_is_an_agreement(
        db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)

    ops.ensure_customer(db_session, _scope(db_session, org))

    metadata = stripe.kwargs_of("Customer.create")[0]["metadata"]
    assert metadata["merchant_legal_name"] == agreement.merchant_legal_name
    assert metadata["merchant_entity_id"] == agreement.merchant_entity_id


def test_a_stripe_failure_creating_a_customer_is_translated_not_leaked(
        db_session, stripe):
    brand, org = _configured(db_session)
    stripe.fail_with["Customer.create"] = ValueError("card_declined")

    with pytest.raises(StripeOperationFailed):
        ops.ensure_customer(db_session, _scope(db_session, org))

    db_session.refresh(org)
    assert org.stripe_customer_id is None


def test_a_connection_failure_is_unavailable_and_not_a_refusal(db_session, stripe):
    """Retryable and not-retryable must not look the same to a caller."""
    brand, org = _configured(db_session)

    class APIConnectionError(Exception):
        pass

    stripe.fail_with["Customer.create"] = APIConnectionError("network down")

    with pytest.raises(StripeUnavailable):
        ops.ensure_customer(db_session, _scope(db_session, org))


def test_no_organization_in_scope_means_no_customer(db_session, stripe):
    scope = BillingScope(None, None, True, True)

    with pytest.raises(BillingOperationRefused):
        ops.ensure_customer(db_session, scope)
    assert stripe.calls == []


def test_get_customer_id_creates_nothing(db_session, stripe):
    brand, org = _configured(db_session)

    assert ops.get_customer_id(_scope(db_session, org)) is None
    assert stripe.calls == []


def test_two_organizations_get_two_customers_and_neither_sees_the_other(
        db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")

    a = ops.ensure_customer(db_session, _scope(db_session, mine))
    b = ops.ensure_customer(db_session, _scope(db_session, theirs))

    assert a != b
    db_session.refresh(mine)
    db_session.refresh(theirs)
    assert mine.stripe_customer_id == a
    assert theirs.stripe_customer_id == b


# ═════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTIONS — the money assertions
# ═════════════════════════════════════════════════════════════════════════════

def test_a_subscription_charges_the_exact_amount_from_the_agreement(
        db_session, stripe):
    """THE CENTRAL MONEY TEST. 499.00 became 49900 minor units in P2; nothing
    on this path is permitted to recompute, round or look it up again."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, recurring="499.00")

    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    price = stripe.kwargs_of("Price.create")[0]
    assert price["unit_amount"] == 49900
    assert price["unit_amount"] == agreement.recurring_amount_cents
    assert isinstance(price["unit_amount"], int)


def test_a_fractional_price_is_not_rounded_away(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, recurring="499.99")

    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    assert stripe.kwargs_of("Price.create")[0]["unit_amount"] == 49999


def test_the_currency_is_the_agreement_currency(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, currency="CAD")

    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    assert stripe.kwargs_of("Price.create")[0]["currency"] == "cad"


def test_the_subscription_is_linked_back_to_the_agreement(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)

    result = ops.create_subscription(db_session, _scope(db_session, org),
                                     agreement.id)

    db_session.refresh(agreement)
    assert result["created"] is True
    assert agreement.stripe_subscription_id == result["subscription_id"]


def test_a_retry_returns_the_same_subscription_and_calls_nothing(
        db_session, stripe):
    """THE DOUBLE-CHARGE TEST. The local guard has to run BEFORE Stripe, or a
    retried request is a second monthly bill."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    scope = _scope(db_session, org)

    first = ops.create_subscription(db_session, scope, agreement.id)
    before = len(stripe.calls)
    second = ops.create_subscription(db_session, scope, agreement.id)

    assert second["subscription_id"] == first["subscription_id"]
    assert second["created"] is False
    assert len(stripe.calls) == before
    assert len(stripe.ops_named("Subscription.create")) == 1


def test_the_subscription_create_carries_an_agreement_scoped_key(
        db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)

    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    key = stripe.kwargs_of("Subscription.create")[0]["idempotency_key"]
    assert key == "subscription:%s" % agreement.id


def test_a_draft_agreement_cannot_be_executed(db_session, stripe):
    """A draft has not been agreed to. Charging against one is charging for
    something nobody signed."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, status=AGREEMENT_DRAFT)

    with pytest.raises(BillingOperationRefused):
        ops.create_subscription(db_session, _scope(db_session, org),
                                agreement.id)
    assert stripe.ops_named("Subscription.create") == []


def test_a_cancelled_agreement_cannot_be_executed(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, status=AGREEMENT_CANCELLED)

    with pytest.raises(BillingOperationRefused):
        ops.create_subscription(db_session, _scope(db_session, org),
                                agreement.id)


def test_a_past_due_agreement_can_still_be_executed(db_session, stripe):
    """past_due is a payment problem, not a cancelled relationship."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, status=AGREEMENT_PAST_DUE)

    result = ops.create_subscription(db_session, _scope(db_session, org),
                                     agreement.id)
    assert result["created"] is True


def test_an_agreement_with_no_recurring_amount_refuses(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    agreement.recurring_amount_cents = None
    db_session.commit()

    with pytest.raises(BillingOperationRefused):
        ops.create_subscription(db_session, _scope(db_session, org),
                                agreement.id)
    assert stripe.ops_named("Subscription.create") == []


def test_another_tenants_agreement_id_is_refused(db_session, stripe):
    """NEGATIVE TENANT TEST. The id is real; it simply is not theirs."""
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    their_agreement = _agreement(db_session, theirs, brand)

    with pytest.raises(BillingOperationRefused):
        ops.create_subscription(db_session, _scope(db_session, mine),
                                their_agreement.id)

    db_session.refresh(their_agreement)
    assert their_agreement.stripe_subscription_id is None
    assert stripe.calls == []


def test_an_invented_agreement_id_refuses_the_same_way(db_session, stripe):
    """Same message for a real-but-foreign id and an invented one, so the
    endpoint is not an oracle for which agreements exist."""
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    their_agreement = _agreement(db_session, theirs, brand)

    with pytest.raises(BillingOperationRefused) as foreign:
        ops.create_subscription(db_session, _scope(db_session, mine),
                                their_agreement.id)
    with pytest.raises(BillingOperationRefused) as invented:
        ops.create_subscription(db_session, _scope(db_session, mine),
                                "agr_does_not_exist")

    assert str(foreign.value) == str(invented.value)


def test_a_stripe_failure_leaves_the_agreement_unlinked(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    stripe.fail_with["Subscription.create"] = ValueError("card_declined")

    with pytest.raises(StripeOperationFailed):
        ops.create_subscription(db_session, _scope(db_session, org),
                                agreement.id)

    db_session.refresh(agreement)
    assert agreement.stripe_subscription_id is None


def test_get_subscription_reads_the_agreement_first(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    out = ops.get_subscription(db_session, _scope(db_session, org))

    assert out["has_subscription"] is True
    assert out["recurring_amount_cents"] == agreement.recurring_amount_cents
    assert out["currency"] == agreement.currency
    assert out["stripe_state"] == "active"


def test_get_subscription_survives_a_stripe_outage(db_session, stripe):
    """LOCAL TRUTH FIRST. A Stripe outage must degrade the billing screen, not
    break it - what was agreed is known here."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    class APIConnectionError(Exception):
        pass

    stripe.fail_with["Subscription.retrieve"] = APIConnectionError("down")

    out = ops.get_subscription(db_session, _scope(db_session, org))

    assert out["has_subscription"] is True
    assert out["recurring_amount_cents"] == agreement.recurring_amount_cents
    assert out["stripe_state"] is None


def test_cancelling_defaults_to_end_of_period(db_session, stripe):
    """The customer paid for the period they are in."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    out = ops.cancel_subscription(db_session, _scope(db_session, org),
                                  agreement.id)

    assert out["cancel_at_period_end"] is True
    assert stripe.kwargs_of("Subscription.modify")[0]["cancel_at_period_end"]
    assert stripe.ops_named("Subscription.delete") == []


def test_immediate_cancellation_stays_available_and_explicit(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    ops.cancel_subscription(db_session, _scope(db_session, org), agreement.id,
                            at_period_end=False)

    assert len(stripe.ops_named("Subscription.delete")) == 1


def test_cancelling_does_not_rewrite_local_status(db_session, stripe):
    """The webhook owns that transition. Writing it here would contradict
    Stripe for anything cancelled at period end."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)
    ops.create_subscription(db_session, _scope(db_session, org), agreement.id)

    ops.cancel_subscription(db_session, _scope(db_session, org), agreement.id)

    db_session.refresh(agreement)
    assert agreement.status == AGREEMENT_ACTIVE


def test_cancelling_another_tenants_subscription_is_refused(db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    their_agreement = _agreement(db_session, theirs, brand)
    ops.create_subscription(db_session, _scope(db_session, theirs),
                            their_agreement.id)
    before = len(stripe.calls)

    with pytest.raises(BillingOperationRefused):
        ops.cancel_subscription(db_session, _scope(db_session, mine),
                                their_agreement.id)

    assert len(stripe.calls) == before


def test_cancelling_an_agreement_with_no_subscription_refuses(db_session, stripe):
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)

    with pytest.raises(BillingOperationRefused):
        ops.cancel_subscription(db_session, _scope(db_session, org),
                                agreement.id)


# ═════════════════════════════════════════════════════════════════════════════
# INVOICES
# ═════════════════════════════════════════════════════════════════════════════

def test_a_draft_invoice_is_created_with_its_line_items(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    scope = _scope(db_session, org)

    out = ops.create_draft_invoice(
        db_session, scope,
        [{"amount_cents": 25000, "description": "Setup"},
         {"amount_cents": 49900, "description": "First month"}],
        description="October")

    assert out["status"] == "draft"
    assert out["line_item_count"] == 2
    amounts = [k["amount"] for k in stripe.kwargs_of("InvoiceItem.create")]
    assert amounts == [25000, 49900]


def test_a_draft_is_created_as_a_draft_and_charges_nothing(db_session, stripe):
    """auto_advance=False is the difference between a reviewable draft and an
    invoice Stripe starts collecting on by itself."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")

    ops.create_draft_invoice(db_session, _scope(db_session, org),
                            [{"amount_cents": 1000}])

    kwargs = stripe.kwargs_of("Invoice.create")[0]
    assert kwargs["auto_advance"] is False
    assert kwargs["collection_method"] == "send_invoice"
    assert stripe.ops_named("Invoice.finalize_invoice") == []


def test_a_float_amount_is_refused_before_stripe_is_called(db_session, stripe):
    """THE ROUNDING TEST. 49.99 dollars as a float is how a customer is billed
    49 cents. The type is the guard and it runs first."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")

    with pytest.raises(BillingOperationRefused):
        ops.create_draft_invoice(db_session, _scope(db_session, org),
                                 [{"amount_cents": 499.00}])
    assert stripe.calls == []


def test_a_boolean_amount_is_refused(db_session, stripe):
    """bool is a subclass of int; True would otherwise bill one cent."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")

    with pytest.raises(BillingOperationRefused):
        ops.create_draft_invoice(db_session, _scope(db_session, org),
                                 [{"amount_cents": True}])


def test_an_invoice_with_no_line_items_is_refused(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")

    with pytest.raises(BillingOperationRefused):
        ops.create_draft_invoice(db_session, _scope(db_session, org), [])
    assert stripe.calls == []


def test_mixed_currencies_on_one_invoice_are_refused_up_front(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")

    with pytest.raises(BillingOperationRefused):
        ops.create_draft_invoice(
            db_session, _scope(db_session, org),
            [{"amount_cents": 1000, "currency": "USD"},
             {"amount_cents": 1000, "currency": "CAD"}])
    assert stripe.calls == []


def test_the_invoice_currency_comes_from_the_first_line_item(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")

    ops.create_draft_invoice(db_session, _scope(db_session, org),
                             [{"amount_cents": 1000, "currency": "CAD"},
                              {"amount_cents": 2000}])

    currencies = [k["currency"] for k in stripe.kwargs_of("InvoiceItem.create")]
    assert currencies == ["cad", "cad"]


def test_invoicing_creates_the_customer_only_when_money_is_involved(
        db_session, stripe):
    brand, org = _configured(db_session)

    ops.create_draft_invoice(db_session, _scope(db_session, org),
                             [{"amount_cents": 1000}])

    assert len(stripe.ops_named("Customer.create")) == 1
    db_session.refresh(org)
    assert org.stripe_customer_id is not None


def test_a_request_id_makes_a_retried_submission_one_invoice(db_session, stripe):
    """OPT-IN DUPLICATE PROTECTION. Two submissions carrying the same request
    id are one submission; the key is what tells Stripe so."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    scope = _scope(db_session, org)

    for _ in range(2):
        ops.create_draft_invoice(db_session, scope, [{"amount_cents": 1000}],
                                 request_id="req-abc")

    keys = [k["idempotency_key"] for k in stripe.kwargs_of("Invoice.create")]
    assert keys == ["invoice:%s:req-abc" % org.id] * 2


def test_two_deliberate_invoices_without_a_request_id_stay_two(db_session, stripe):
    """Billing the same amount twice is normal. A content-derived key would
    silently return the first invoice and look like it worked."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    scope = _scope(db_session, org)

    a = ops.create_draft_invoice(db_session, scope, [{"amount_cents": 1000}])
    b = ops.create_draft_invoice(db_session, scope, [{"amount_cents": 1000}])

    assert a["stripe_invoice_id"] != b["stripe_invoice_id"]
    assert all(k.get("idempotency_key") is None
               for k in stripe.kwargs_of("Invoice.create"))


def test_a_paid_or_void_invoice_cannot_be_finalized(db_session, stripe):
    """The two states an invoice never leaves. Anything else is Stripe's call,
    because a stale local mirror must not veto the authority."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    scope = _scope(db_session, org)

    for i, status in enumerate(("paid", "void")):
        row = _invoice_row(db_session, org, stripe_id="in_x%d" % i,
                           status=status)
        with pytest.raises(BillingOperationRefused):
            ops.finalize_invoice(db_session, scope, row.id)
    assert stripe.calls == []


def test_finalizing_mirrors_stripes_answer_locally(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    row = _invoice_row(db_session, org, stripe_id="in_1", status="draft")

    out = ops.finalize_invoice(db_session, _scope(db_session, org), row.id)

    assert out["status"] == "open"
    db_session.refresh(row)
    assert row.status == "open"


def test_the_mirrored_invoice_survives_the_transaction(db_session, stripe):
    """upsert flushes; the commit belongs at this boundary. Without it the
    Stripe-side change stands and the local record vanishes."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    row = _invoice_row(db_session, org, stripe_id="in_1", status="draft")

    ops.finalize_invoice(db_session, _scope(db_session, org), row.id)
    db_session.rollback()

    db_session.refresh(row)
    assert row.status == "open"


def test_finalizing_exposes_the_hosted_url_and_the_pdf(db_session, stripe):
    """What a customer is actually sent. If these are not surfaced the invoice
    might as well not exist."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    row = _invoice_row(db_session, org, stripe_id="in_1", status="draft")

    out = ops.finalize_invoice(db_session, _scope(db_session, org), row.id)

    assert out["hosted_invoice_url"].startswith("https://")
    assert out["invoice_pdf"].endswith(".pdf")


def test_sending_an_invoice_asks_stripe_to_email_it(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    row = _invoice_row(db_session, org, stripe_id="in_1")

    ops.send_invoice(db_session, _scope(db_session, org), row.id)

    assert len(stripe.ops_named("Invoice.send_invoice")) == 1


def test_a_paid_invoice_is_never_voided(db_session, stripe):
    """Voiding a paid invoice erases a payment that happened. The refusal is
    local so it costs no API call and gives a sentence a human can act on."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    row = _invoice_row(db_session, org, stripe_id="in_1", status="paid")

    with pytest.raises(BillingOperationRefused) as exc:
        ops.void_invoice(db_session, _scope(db_session, org), row.id)

    assert "refund" in str(exc.value).lower()
    assert stripe.calls == []


def test_voiding_an_open_invoice_mirrors_the_void(db_session, stripe):
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    row = _invoice_row(db_session, org, stripe_id="in_1", status="open")

    out = ops.void_invoice(db_session, _scope(db_session, org), row.id)

    assert out["status"] == "void"


def test_an_invoice_is_found_by_either_local_or_stripe_id(db_session, stripe):
    """A UI holds one and a Stripe link holds the other; BOTH are matched
    inside the organization-scoped filter."""
    brand, org = _configured(db_session, stripe_customer_id="cus_x")
    row = _invoice_row(db_session, org, stripe_id="in_9")
    scope = _scope(db_session, org)

    assert ops._invoice_in_scope(db_session, scope, row.id).id == row.id
    assert ops._invoice_in_scope(db_session, scope, "in_9").id == row.id


def test_another_tenants_invoice_id_is_refused(db_session, stripe):
    """NEGATIVE TENANT TEST. A real Stripe invoice id belonging to somebody
    else is worth exactly as much as a guess."""
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest",
                            stripe_customer_id="cus_theirs")
    their_invoice = _invoice_row(db_session, theirs, stripe_id="in_theirs")

    for bad in (their_invoice.id, "in_theirs"):
        with pytest.raises(BillingOperationRefused):
            ops._invoice_in_scope(db_session, _scope(db_session, mine), bad)


def test_another_tenants_invoice_cannot_be_voided_or_finalized(db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest",
                            stripe_customer_id="cus_theirs")
    their_invoice = _invoice_row(db_session, theirs, stripe_id="in_theirs")
    scope = _scope(db_session, mine)

    for call in (ops.void_invoice, ops.finalize_invoice, ops.send_invoice):
        with pytest.raises(BillingOperationRefused):
            call(db_session, scope, their_invoice.id)

    db_session.refresh(their_invoice)
    assert their_invoice.status == "open"
    assert stripe.calls == []


def test_an_invented_invoice_id_refuses_identically(db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    their_invoice = _invoice_row(db_session, theirs, stripe_id="in_theirs")
    scope = _scope(db_session, mine)

    with pytest.raises(BillingOperationRefused) as foreign:
        ops._invoice_in_scope(db_session, scope, their_invoice.id)
    with pytest.raises(BillingOperationRefused) as invented:
        ops._invoice_in_scope(db_session, scope, "in_never_existed")

    assert str(foreign.value) == str(invented.value)


# ═════════════════════════════════════════════════════════════════════════════
# PAYMENTS AND HISTORY
# ═════════════════════════════════════════════════════════════════════════════

def test_payment_history_is_scoped_to_the_organization(db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    _payment_row(db_session, mine, intent="pi_mine")
    _payment_row(db_session, theirs, intent="pi_theirs")

    mine_rows = ops.list_payments(db_session, _scope(db_session, mine))

    assert len(mine_rows) == 1
    assert mine_rows[0]["id"] != "pi_theirs"


def test_invoice_history_is_scoped_to_the_organization(db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    _invoice_row(db_session, mine, stripe_id="in_mine")
    _invoice_row(db_session, theirs, stripe_id="in_theirs")

    rows = ops.list_invoices(db_session, _scope(db_session, mine))

    assert [r["stripe_invoice_id"] for r in rows] == ["in_mine"]


def test_a_failed_payment_keeps_its_reason(db_session, stripe):
    """A failed payment with no reason is a support ticket nobody can answer."""
    brand, org = _configured(db_session)
    _payment_row(db_session, org, status="failed",
                 failure="Your card was declined.")

    rows = ops.list_payments(db_session, _scope(db_session, org))

    assert rows[0]["status"] == "failed"
    assert rows[0]["failure_message"] == "Your card was declined."


def test_a_payment_is_described_in_minor_units_and_as_a_decimal_string(
        db_session, stripe):
    """Both, deliberately: the integer is authoritative and the string is for
    display. Nothing downstream should have to divide by 100 itself."""
    brand, org = _configured(db_session)
    _payment_row(db_session, org, amount=49900)

    row = ops.list_payments(db_session, _scope(db_session, org))[0]

    assert row["amount_cents"] == 49900
    assert row["amount"] == "499.00"


def test_no_organization_in_scope_lists_nothing(db_session, stripe):
    scope = BillingScope(None, None, True, True)

    assert ops.list_invoices(db_session, scope) == []
    assert ops.list_payments(db_session, scope) == []


# ═════════════════════════════════════════════════════════════════════════════
# OVERVIEW — what the P6 billing screen will read
# ═════════════════════════════════════════════════════════════════════════════

def test_the_overview_reports_outstanding_money_from_the_local_mirror(
        db_session, stripe):
    """Computed locally on purpose: a past-due customer must still be told they
    are past due while Stripe is unreachable."""
    brand, org = _configured(db_session, billing_status="past_due")
    _invoice_row(db_session, org, stripe_id="in_1", status="open", due=49900)
    _invoice_row(db_session, org, stripe_id="in_2", status="open", due=25000)
    _invoice_row(db_session, org, stripe_id="in_3", status="paid", due=0)

    out = ops.billing_overview(db_session, _scope(db_session, org))

    assert out["past_due"]["is_past_due"] is True
    assert out["past_due"]["outstanding_invoice_count"] == 2
    assert out["past_due"]["outstanding_cents"] == 74900
    assert out["past_due"]["outstanding"] == "749.00"


def test_the_overview_counts_failed_payments(db_session, stripe):
    brand, org = _configured(db_session)
    _payment_row(db_session, org, status="failed", intent="pi_1")
    _payment_row(db_session, org, status="succeeded", intent="pi_2")

    out = ops.billing_overview(db_session, _scope(db_session, org))

    assert out["past_due"]["failed_payment_count"] == 1


def test_the_overview_names_the_legal_seller(db_session, stripe):
    """An organization's billing screen has to say who is billing them."""
    brand, org = _configured(db_session)
    agreement = _agreement(db_session, org, brand)

    out = ops.billing_overview(db_session, _scope(db_session, org))

    assert out["merchant"]["legal_name"] == agreement.merchant_legal_name
    assert out["agreement"]["recurring_amount"] == "499.00"


def test_the_overview_reports_what_this_caller_may_do(db_session, stripe):
    """So the UI can hide a button rather than offer one that 403s."""
    brand, org = _configured(db_session)

    out = ops.billing_overview(db_session,
                               _scope(db_session, org, can_manage=False))

    assert out["permissions"] == {"can_view": True, "can_manage": False}


def test_the_overview_shows_only_this_organizations_money(db_session, stripe):
    brand, mine = _configured(db_session, "Restland")
    _, theirs = _configured(db_session, "Hillcrest")
    _invoice_row(db_session, theirs, stripe_id="in_theirs", due=999999)
    _payment_row(db_session, theirs, intent="pi_theirs")

    out = ops.billing_overview(db_session, _scope(db_session, mine))

    assert out["invoices"] == []
    assert out["payments"] == []
    assert out["past_due"]["outstanding_cents"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# THE GATEWAY ITSELF
# ═════════════════════════════════════════════════════════════════════════════

def test_a_live_secret_key_is_refused(monkeypatch):
    """STRUCTURAL, NOT PROCEDURAL. Every phase has been told not to touch live
    Stripe; this is the line that makes it true if somebody forgets."""
    for key in ("sk_live_abc123", "rk_live_abc123"):
        with pytest.raises(LiveModeRefused):
            gw.assert_test_mode(key)


def test_a_test_key_is_accepted(monkeypatch):
    gw.assert_test_mode("sk_test_abc123")


def test_a_missing_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    with pytest.raises(StripeUnavailable):
        gw.client()
    assert gw.is_configured() is False


def test_a_live_key_in_the_environment_never_reaches_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_realmoney")

    with pytest.raises(LiveModeRefused):
        gw.client()


def test_an_idempotency_key_is_stable_and_skips_empties(monkeypatch):
    assert gw.idempotency_key("subscription", "agr_1") == "subscription:agr_1"
    assert gw.idempotency_key("subscription", "agr_1") == \
        gw.idempotency_key("subscription", "agr_1")
    assert gw.idempotency_key("customer", None, "org_1") == "customer:org_1"


def test_an_orphaned_stripe_object_is_logged_at_error_with_its_id(caplog):
    """The one failure mode that silently costs money. It must be findable in
    the log by id."""
    with caplog.at_level("ERROR", logger="billing.orphan"):
        gw.log_orphan("Subscription.create", "sub_abc",
                      {"agreement_id": "agr_1"})

    assert "sub_abc" in caplog.text
    assert "ORPHANED" in caplog.text
