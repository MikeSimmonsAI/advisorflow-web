"""P8 — billing integrity: does the mirror still agree with Stripe, and with itself.

WHAT THIS FILE DEFENDS

**Report first, never reprice.** `run()` has no mutation path, and the tests
prove it against the recorded Stripe call list AND a field-by-field database
snapshot rather than against a return value. `apply_repair` defaults to a dry
run and refuses every discrepancy whose resolution is a business decision — and
that refusal is a whitelist, not a policy check, so the tests assert there is no
implementation behind the refused codes at all.

**The awkward middle is a decision, not a repair.** "Stripe charges $349 and
the agreement says $499" is reported with both numbers and never resolved,
because either side could be the wrong one and picking one silently reprices
somebody. That single case is what separates a reconciliation tool from an
incident.

**Failures must be visible and recoverable.** A Stripe object created while the
local write failed, a webhook that exhausted its retries, a stale status — each
has to surface somewhere a human looks, and each safe one has to be fixable
without touching money.
"""

import itertools
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.billing_agreement_models import BillingAgreement
from app.models.billing_models import (EVENT_FAILED, EVENT_PROCESSED,
                                       EVENT_RECEIVED, Invoice, Payment,
                                       StripeWebhookEvent)
from app.models.implementation_models import Implementation
from app.models.models import (AuditLogEntry, Organization, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.services import billing_agreement as agreements
from app.services import billing_integrity as bi
from app.services import merchant_entity as entity_svc
from app.services import stripe_gateway as gw
from app.services.auth_service import create_access_token, hash_password
from app.services.billing_access import BILLING_MANAGE
from app.services.billing_integrity import RepairRefused

_SEQ = itertools.count(1)
BASE = "/platform/billing"
REPO = Path(__file__).resolve().parents[1]


# ═════════════════════════════════════════════════════════════════════════════
# a Stripe that records everything
# ═════════════════════════════════════════════════════════════════════════════

class _Obj(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


MUTATION_VERBS = ("create", "modify", "delete", "void", "finalize", "send",
                  "update", "cancel", "pay", "refund")


class FakeStripe:
    """Retrieve and list return data; every mutating verb records and raises.

    Raising rather than returning is deliberate: a mutation attempted during a
    read-only pass should break the test loudly, not be discovered later by
    counting calls.
    """

    def __init__(self):
        self.calls = []
        self.customer = _Obj(id="cus_x", metadata=_Obj(organization_id=None))
        self.customer_missing = False
        self.subscription = None
        self.subscription_missing = False
        self.invoices = []
        self.invoice_by_id = {}
        fake = self

        def read(name, make):
            def bound(*a, **k):
                fake.calls.append((name, a, k))
                return make(a, k)
            return staticmethod(bound)

        def forbidden(name):
            def bound(*a, **k):
                fake.calls.append((name, a, k))
                raise AssertionError("MUTATION during a read-only pass: %s" % name)
            return staticmethod(bound)

        def customer_retrieve(a, k):
            if fake.customer_missing:
                raise gw.StripeOperationFailed("No such customer")
            return fake.customer

        def subscription_retrieve(a, k):
            if fake.subscription_missing:
                raise gw.StripeOperationFailed("No such subscription")
            return fake.subscription

        self.Customer = type("C", (), {
            "retrieve": read("Customer.retrieve", customer_retrieve),
            "create": forbidden("Customer.create")})
        self.Subscription = type("S", (), {
            "retrieve": read("Subscription.retrieve", subscription_retrieve),
            "create": forbidden("Subscription.create"),
            "modify": forbidden("Subscription.modify"),
            "delete": forbidden("Subscription.delete")})
        self.Invoice = type("I", (), {
            "list": read("Invoice.list",
                         lambda a, k: _Obj(data=list(fake.invoices))),
            "retrieve": read("Invoice.retrieve",
                             lambda a, k: fake.invoice_by_id[a[0]]),
            "create": forbidden("Invoice.create"),
            "finalize_invoice": forbidden("Invoice.finalize_invoice"),
            "void_invoice": forbidden("Invoice.void_invoice"),
            "send_invoice": forbidden("Invoice.send_invoice")})
        self.Price = type("P", (), {"create": forbidden("Price.create")})
        self.Product = type("Pr", (), {"create": forbidden("Product.create")})
        self.InvoiceItem = type("II", (), {
            "create": forbidden("InvoiceItem.create")})

    @property
    def mutations(self):
        return [c[0] for c in self.calls
                if any(v in c[0].split(".")[-1].lower() for v in MUTATION_VERBS)]

    def set_subscription(self, status="active", unit_amount=49900,
                         currency="usd", sub_id="sub_1"):
        self.subscription = _Obj(
            id=sub_id, status=status,
            items=_Obj(data=[_Obj(price=_Obj(unit_amount=unit_amount,
                                             currency=currency))]))
        return self.subscription

    def add_invoice(self, invoice_id, status="paid", number="INV-S", **over):
        payload = _Obj({"id": invoice_id, "customer": self.customer["id"],
                        "number": number, "status": status, "currency": "usd",
                        "total": 49900, "amount_due": 0, "amount_paid": 49900,
                        "status_transitions": {}, **over})
        self.invoices.append(payload)
        self.invoice_by_id[invoice_id] = payload
        return payload


@pytest.fixture
def stripe(monkeypatch):
    fake = FakeStripe()
    monkeypatch.setattr(gw, "client", lambda: fake)
    monkeypatch.setattr(gw, "_stripe", lambda: fake)
    return fake


@pytest.fixture
def no_stripe(monkeypatch):
    def _unavailable():
        raise gw.StripeUnavailable("not configured in this test")
    monkeypatch.setattr(gw, "client", _unavailable)


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════

def _brand(db, slug="evosyspro", name="EvoSys Pro"):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p:
        return p
    p = Platform(name=name, slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name="Restland", platform=None, stripe_customer_id=None,
         stripe_subscription_id=None, billing_status=None, plan="growth"):
    o = Organization(name=name, slug="%s-%d" % (name.lower(), next(_SEQ)),
                     plan=plan,
                     platform_id=platform.id if platform else None,
                     stripe_customer_id=stripe_customer_id,
                     stripe_subscription_id=stripe_subscription_id)
    if billing_status is not None:
        o.billing_status = billing_status
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id if org else None,
             email="p8.%d@example.com" % next(_SEQ),
             password_hash=hash_password("TestPass123!"),
             full_name="Person", role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _god(db):
    return _user(db, None, "god_admin")


def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _agreement(db, org, platform, recurring="499.00", currency="USD",
               activate=True):
    entity_svc.ensure_evosys_pro_configuration(db)
    so = (db.query(BrandSalesOrg)
          .filter(BrandSalesOrg.platform_id == platform.id).first())
    if so is None:
        so = BrandSalesOrg(platform_id=platform.id, name="Sales",
                           slug="sales-%d" % next(_SEQ))
        db.add(so)
        db.commit()
    opp = Opportunity(company_name=org.name, status="won",
                      brand_sales_org_id=so.id)
    db.add(opp)
    db.commit()
    impl = Implementation(
        organization_id=org.id, platform_id=platform.id, opportunity_id=opp.id,
        billing_option="term_agreement", contract_term_months=13,
        implementation_fee=Decimal("1500.00"),
        recurring_amount=Decimal(recurring), currency=currency,
        billing_start_date=datetime(2026, 10, 1))
    db.add(impl)
    db.commit()
    return agreements.create_from_implementation(db, impl, activate=activate)


def _invoice(db, org, *, stripe_id=None, status="open", due=49900,
             number="INV-1", attempts=0):
    row = Invoice(organization_id=org.id,
                  stripe_invoice_id=stripe_id or "in_%d" % next(_SEQ),
                  stripe_customer_id=org.stripe_customer_id, number=number,
                  status=status, currency="USD", total_cents=49900,
                  amount_due_cents=due, amount_paid_cents=0,
                  attempt_count=attempts)
    db.add(row)
    db.commit()
    return row


def _event(db, *, status=EVENT_FAILED, event_type="invoice.payment_failed",
           payload=None, received_at=None, attempts=1, error="boom"):
    # The event ledger records the EVENT, not a tenant — which organization an
    # event concerns lives inside its payload.
    e = StripeWebhookEvent(
        stripe_event_id="evt_%d" % next(_SEQ), event_type=event_type,
        processing_status=status, attempts=attempts, error_message=error,
        received_at=received_at or datetime.utcnow(),
        payload_json=json.dumps(payload) if payload is not None else None)
    db.add(e)
    db.commit()
    return e


def _codes(report):
    if isinstance(report, dict):
        report = report["findings"]
    return {f["code"] for f in report}


def _find(report, code):
    rows = [f for f in (report["findings"] if isinstance(report, dict)
                        else report) if f["code"] == code]
    assert rows, "no %s finding" % code
    return rows[0]


def _snapshot(db):
    """Every financial field in the database, for before/after comparison."""
    return (
        sorted((o.id, o.billing_status, o.plan, o.stripe_customer_id,
                o.stripe_subscription_id) for o in db.query(Organization).all()),
        sorted((i.id, i.status, i.amount_due_cents, i.amount_paid_cents,
                i.currency) for i in db.query(Invoice).all()),
        sorted((p.id, p.status, p.amount_cents, p.refunded_cents)
               for p in db.query(Payment).all()),
        sorted((a.id, a.status, a.recurring_amount_cents, a.currency,
                a.stripe_subscription_id)
               for a in db.query(BillingAgreement).all()),
    )


# ═════════════════════════════════════════════════════════════════════════════
# THE RUN WRITES NOTHING
# ═════════════════════════════════════════════════════════════════════════════

def test_a_full_run_makes_no_stripe_mutation(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="active")
    _agreement(db_session, org, brand)
    stripe.set_subscription()
    stripe.add_invoice("in_remote")

    bi.run(db_session)

    assert stripe.mutations == []
    assert set(c[0] for c in stripe.calls) <= {
        "Customer.retrieve", "Subscription.retrieve", "Invoice.list"}


def test_a_full_run_makes_no_local_mutation(db_session, stripe):
    """Compared field by field, not by trusting a flag on the response."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="active")
    _agreement(db_session, org, brand)
    _invoice(db_session, org, status="open", attempts=2)
    stripe.set_subscription(status="past_due", unit_amount=34900)
    stripe.add_invoice("in_never_mirrored")

    before = _snapshot(db_session)
    report = bi.run(db_session)
    db_session.expire_all()

    assert _snapshot(db_session) == before
    assert report["dry_run"] is True
    assert report["mutations_performed"] == 0
    assert report["total_findings"] > 0


def test_the_run_has_no_apply_parameter(db_session):
    """STRUCTURAL. A reconciliation entry point that can also write is one
    somebody eventually calls with the wrong flag against production."""
    import inspect

    params = set(inspect.signature(bi.run).parameters)
    assert not params & {"apply", "dry_run", "repair", "fix", "write"}


def test_a_local_only_run_never_touches_stripe(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    _agreement(db_session, org, brand)

    bi.run(db_session, include_stripe=False)

    assert stripe.calls == []


def test_a_stripe_outage_degrades_the_run_rather_than_failing_it(db_session,
                                                                 no_stripe):
    """A run that dies on the first unreachable customer is a run nobody
    finishes."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    _agreement(db_session, org, brand)

    report = bi.run(db_session)

    assert report["stripe_checked"] is False
    assert report["stripe_unavailable_reason"]
    # The local checks still ran.
    assert "agreement_without_subscription" in _codes(report)


# ═════════════════════════════════════════════════════════════════════════════
# DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def test_a_missing_stripe_customer_is_critical(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_gone")
    stripe.customer_missing = True

    finding = _find(bi.run(db_session), "missing_stripe_customer")

    assert finding["severity"] == bi.CRITICAL
    assert finding["requires_human"] is True
    assert "cus_gone" in finding["detail"]


def test_a_missing_stripe_subscription_is_critical(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_gone")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_gone"
    db_session.commit()
    stripe.subscription_missing = True

    finding = _find(bi.run(db_session), "missing_stripe_subscription")

    assert finding["severity"] == bi.CRITICAL
    assert finding["requires_human"] is True


def test_an_invoice_stripe_has_and_we_never_recorded_is_found(db_session,
                                                              stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    stripe.add_invoice("in_unmirrored", status="paid", number="INV-9")

    finding = _find(bi.run(db_session), "missing_local_invoice")

    assert finding["stripe_ref"] == "in_unmirrored"
    assert finding["safe_repair"] is True


def test_a_stale_local_invoice_status_is_found(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    _invoice(db_session, org, stripe_id="in_1", status="open", number="INV-1")
    stripe.add_invoice("in_1", status="paid", number="INV-1")

    finding = _find(bi.run(db_session), "stale_invoice_status")

    assert finding["local_value"] == "open"
    assert finding["stripe_value"] == "paid"
    assert finding["safe_repair"] is True


def test_a_local_invoice_stripe_does_not_list_is_found(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    _invoice(db_session, org, stripe_id="in_local_only")

    assert "missing_stripe_invoice" in _codes(bi.run(db_session))


def test_an_amount_disagreement_is_critical_and_needs_a_human(db_session,
                                                              stripe):
    """THE ONE THAT MATTERS MOST. Either side could be wrong, and picking one
    silently reprices a customer."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    stripe.set_subscription(unit_amount=34900)

    finding = _find(bi.run(db_session), "amount_disagreement")

    assert finding["severity"] == bi.CRITICAL
    assert finding["requires_human"] is True
    assert "499.00" in finding["local_value"]
    assert "349.00" in finding["stripe_value"]
    assert bi.apply_repair.__doc__  # and there is no repair for it:
    with pytest.raises(RepairRefused):
        bi.apply_repair(db_session, "amount_disagreement", finding["target_id"])


def test_a_currency_disagreement_is_critical(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand, currency="USD")
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    stripe.set_subscription(currency="cad")

    finding = _find(bi.run(db_session), "currency_disagreement")

    assert finding["local_value"] == "USD" and finding["stripe_value"] == "CAD"
    assert finding["requires_human"] is True


def test_an_agreement_that_drifted_from_its_deal_is_found(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    agreement.recurring_amount_cents = 34900
    db_session.commit()

    finding = _find(bi.run(db_session, include_stripe=False),
                    "deal_amount_disagreement")

    assert finding["requires_human"] is True


def test_two_organizations_sharing_a_stripe_customer_is_critical(db_session,
                                                                 stripe):
    """Invoices are being attributed across tenants — a billing error and a
    data-isolation one at once."""
    brand = _brand(db_session)
    _org(db_session, "Restland", platform=brand, stripe_customer_id="cus_same")
    _org(db_session, "Hillcrest", platform=brand, stripe_customer_id="cus_same")

    findings = [f for f in bi.run(db_session, include_stripe=False)["findings"]
                if f["code"] == "duplicate_customer_mapping"]

    assert len(findings) == 2
    assert findings[0]["severity"] == bi.CRITICAL


def test_two_agreements_sharing_a_subscription_is_critical(db_session, stripe):
    brand = _brand(db_session)
    a = _org(db_session, "Restland", platform=brand)
    b = _org(db_session, "Hillcrest", platform=brand)
    for org in (a, b):
        agreement = _agreement(db_session, org, brand)
        agreement.stripe_subscription_id = "sub_shared"
        db_session.commit()

    assert "duplicate_subscription_mapping" in _codes(
        bi.run(db_session, include_stripe=False))


def test_a_customer_whose_metadata_names_another_org_is_found(db_session,
                                                              stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    stripe.customer["metadata"] = _Obj(organization_id="org_somebody_else")

    finding = _find(bi.run(db_session),
                    "customer_owned_by_another_organization")

    assert finding["severity"] == bi.CRITICAL


def test_stripe_says_past_due_and_we_say_active(db_session, stripe):
    """Nothing is chasing it, and nothing else on any screen would show that."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="active")
    _agreement(db_session, org, brand)
    stripe.set_subscription(status="past_due")

    finding = _find(bi.run(db_session), "unresolved_past_due")

    assert finding["severity"] == bi.CRITICAL
    assert finding["safe_repair"] is True


def test_stripe_says_healthy_and_we_still_say_past_due(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="past_due")
    _agreement(db_session, org, brand)
    stripe.set_subscription(status="active")

    assert "recovered_but_past_due" in _codes(bi.run(db_session))


def test_a_billing_status_disagreeing_with_our_own_invoices_is_found(db_session,
                                                                     stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, billing_status="active")
    _invoice(db_session, org, status="open", attempts=3)

    finding = _find(bi.run(db_session, include_stripe=False),
                    "stale_org_billing_status")

    assert finding["local_value"] == "active"
    assert finding["stripe_value"] == "past_due"


def test_a_merchant_mismatch_is_reported_and_never_corrected(db_session,
                                                             stripe):
    """The seller on an issued invoice is a legal fact, not a field to fix."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand)
    agreement.merchant_entity_id = "entity_someone_else"
    agreement.merchant_legal_name = "SOME OTHER LLC"
    db_session.commit()

    finding = _find(bi.run(db_session, include_stripe=False),
                    "merchant_mismatch")

    assert finding["requires_human"] is True
    with pytest.raises(RepairRefused):
        bi.apply_repair(db_session, "merchant_mismatch", agreement.id)


def test_an_orphan_invoice_and_payment_are_found(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_mine")
    invoice = _invoice(db_session, org)
    invoice.stripe_customer_id = "cus_someone_else"
    db_session.commit()
    db_session.add(Payment(organization_id=org.id,
                           stripe_payment_intent_id="pi_orphan",
                           invoice_id="inv_that_does_not_exist",
                           amount_cents=1000, currency="USD",
                           status="succeeded", refunded_cents=0))
    db_session.commit()

    codes = _codes(bi.run(db_session, include_stripe=False))

    assert "orphan_invoice" in codes and "orphan_payment" in codes


def test_a_clean_system_reports_nothing(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="active")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    _invoice(db_session, org, stripe_id="in_1", status="paid", due=0)
    stripe.customer["metadata"] = _Obj(organization_id=org.id)
    stripe.set_subscription(status="active", unit_amount=49900)
    stripe.add_invoice("in_1", status="paid")

    report = bi.run(db_session)

    assert report["findings"] == [], report["by_code"]


def test_findings_are_ordered_worst_first(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_gone")
    _agreement(db_session, org, brand)
    stripe.customer_missing = True

    severities = [f["severity"] for f in bi.run(db_session)["findings"]]

    assert severities == sorted(
        severities, key=lambda s: bi._SEVERITY_ORDER[s])


def test_every_finding_carries_enough_to_act_on(db_session, stripe):
    """A queue row that forces the reader to go and look something up is a
    queue row that does not get actioned."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    stripe.set_subscription(unit_amount=34900)

    for finding in bi.run(db_session)["findings"]:
        for key in ("code", "severity", "what", "detail", "organization_id",
                    "organization_name", "brand_name", "merchant_legal_name",
                    "local_ref", "stripe_ref", "local_value", "stripe_value",
                    "proposed_action", "safe_repair", "requires_human",
                    "target_type", "target_id"):
            assert key in finding, (finding["code"], key)
        assert finding["proposed_action"]


# ═════════════════════════════════════════════════════════════════════════════
# REPAIR — SAFE VERSUS BUSINESS
# ═════════════════════════════════════════════════════════════════════════════

def test_every_business_change_is_refused_and_has_no_implementation(db_session):
    """THE REFUSAL IS A WHITELIST, NOT A POLICY CHECK. There is no code path
    behind these codes that writes anything — which is a stronger guarantee
    than not being permitted to."""
    for code in sorted(bi.HUMAN_REVIEW):
        with pytest.raises(RepairRefused):
            bi.apply_repair(db_session, code, "anything", dry_run=False)
        assert code not in bi._REPAIRS, code


def test_the_dangerous_operations_are_not_repairable_at_all(db_session):
    """Named individually so this reads as the list it is: nothing here can
    start or cancel a subscription, or change an amount, currency, term, legal
    seller or brand."""
    for code in ("amount_disagreement", "currency_disagreement",
                 "deal_amount_disagreement", "agreement_without_subscription",
                 "subscription_without_agreement", "merchant_mismatch",
                 "brand_mismatch", "missing_stripe_customer",
                 "missing_stripe_subscription", "duplicate_customer_mapping"):
        assert code in bi.HUMAN_REVIEW
        assert code not in bi.SAFE_REPAIRS


def test_an_unknown_code_is_refused(db_session):
    with pytest.raises(RepairRefused):
        bi.apply_repair(db_session, "delete_everything", "x", dry_run=False)


def test_a_repair_is_a_dry_run_by_default(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, billing_status="active")
    _invoice(db_session, org, status="open", attempts=3)
    before = _snapshot(db_session)

    result = bi.apply_repair(db_session, "stale_org_billing_status", org.id)

    assert result["dry_run"] is True and result["applied"] is False
    assert result["plan"]["from"] == "active" and result["plan"]["to"] == "past_due"
    db_session.expire_all()
    assert _snapshot(db_session) == before


def test_applying_a_billing_status_repair_writes_only_that_column(db_session,
                                                                  stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, billing_status="active",
               stripe_customer_id="cus_x", plan="growth")
    _invoice(db_session, org, status="open", attempts=3)

    result = bi.apply_repair(db_session, "stale_org_billing_status", org.id,
                             dry_run=False)

    db_session.expire_all()
    reloaded = db_session.query(Organization).filter(
        Organization.id == org.id).first()
    assert result["applied"] is True
    assert reloaded.billing_status == "past_due"
    # Nothing else moved.
    assert reloaded.plan == "growth"
    assert reloaded.stripe_customer_id == "cus_x"
    assert stripe.mutations == []


def test_a_stale_invoice_status_repair_copies_stripes_answer(db_session,
                                                             stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org, stripe_id="in_1", status="open")
    stripe.add_invoice("in_1", status="paid")

    result = bi.apply_repair(db_session, "stale_invoice_status", invoice.id,
                             dry_run=False)

    db_session.expire_all()
    assert result["applied"] is True
    assert db_session.query(Invoice).filter(
        Invoice.id == invoice.id).first().status == "paid"
    assert stripe.mutations == []


def test_a_missing_local_invoice_repair_proves_ownership_through_stripe(
        db_session, stripe):
    """The upsert resolves the organization from the invoice's own customer id
    and refuses when no local organization holds it — ownership is proven by
    Stripe, not asserted by the caller."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    stripe.add_invoice("in_new", status="paid", number="INV-NEW")

    result = bi.apply_repair(db_session, "missing_local_invoice", "in_new",
                             dry_run=False)

    db_session.expire_all()
    assert result["applied"] is True
    mirrored = db_session.query(Invoice).filter(
        Invoice.stripe_invoice_id == "in_new").first()
    assert mirrored is not None and mirrored.organization_id == org.id


def test_an_invoice_for_a_customer_nobody_holds_is_not_mirrored(db_session,
                                                                stripe):
    stripe.customer["id"] = "cus_nobody"
    stripe.add_invoice("in_foreign", status="paid")

    result = bi.apply_repair(db_session, "missing_local_invoice", "in_foreign",
                             dry_run=False)

    assert result["applied"] is False
    assert db_session.query(Invoice).count() == 0


def test_a_repair_that_has_nothing_to_do_says_so_and_writes_nothing(db_session,
                                                                    stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, billing_status="active")
    before = _snapshot(db_session)

    result = bi.apply_repair(db_session, "stale_org_billing_status", org.id,
                             dry_run=False)

    assert result["applied"] is False
    assert result["plan"]["actionable"] is False
    db_session.expire_all()
    assert _snapshot(db_session) == before


def test_a_past_due_repair_reads_the_live_subscription(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="active")
    stripe.set_subscription(status="past_due")

    result = bi.apply_repair(db_session, "unresolved_past_due", org.id,
                             dry_run=False)

    db_session.expire_all()
    assert result["applied"] is True
    assert db_session.query(Organization).filter(
        Organization.id == org.id).first().billing_status == "past_due"
    assert stripe.mutations == []


def test_a_repair_refuses_when_stripe_cannot_confirm_it(db_session, no_stripe):
    """Correcting a mirror requires knowing what to correct it TO. Without
    Stripe there is no proven state, so nothing is written."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x",
               stripe_subscription_id="sub_1", billing_status="active")

    result = bi.apply_repair(db_session, "unresolved_past_due", org.id,
                             dry_run=False)

    assert result["applied"] is False


# ═════════════════════════════════════════════════════════════════════════════
# WEBHOOK HEALTH AND RECOVERY
# ═════════════════════════════════════════════════════════════════════════════

def test_webhook_health_counts_every_state(db_session):
    _event(db_session, status=EVENT_PROCESSED, error=None)
    _event(db_session, status=EVENT_PROCESSED, error=None)
    _event(db_session, status=EVENT_FAILED, error="card error")
    _event(db_session, status=EVENT_RECEIVED, error=None,
           received_at=datetime.utcnow() - timedelta(hours=3))

    health = bi.webhook_health(db_session)

    assert health["received"] == 4
    assert health["processed"] == 2
    assert health["failed"] == 1
    assert health["stuck"] == 1
    assert health["oldest_stuck_minutes"] >= 170


def test_webhook_health_names_repeated_failure_types(db_session):
    for _ in range(3):
        _event(db_session, status=EVENT_FAILED,
               event_type="invoice.payment_failed")
    _event(db_session, status=EVENT_FAILED, event_type="customer.updated")

    types = bi.webhook_health(db_session)["repeated_failure_types"]

    assert types[0] == {"event_type": "invoice.payment_failed", "count": 3}


def test_webhook_health_never_returns_a_payload_or_a_secret(db_session,
                                                            monkeypatch):
    """A stored event body carries customer payment detail; the signing secret
    is not in the database at all and must not appear here either."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_supersecret")
    _event(db_session, status=EVENT_FAILED,
           payload={"data": {"object": {"id": "in_1",
                                        "customer_secret": "whsec_supersecret",
                                        "card": {"number": "4242424242424242"}}}})

    rendered = json.dumps(bi.webhook_health(db_session), default=str)

    assert "whsec" not in rendered
    assert "4242424242424242" not in rendered
    assert "payload" not in rendered.lower()


def test_a_failed_event_becomes_a_finding(db_session, stripe):
    _event(db_session, status=EVENT_FAILED, error="handler blew up")

    finding = _find(bi.run(db_session, include_stripe=False), "webhook_failed")

    assert finding["safe_repair"] is True
    assert "handler blew up" in finding["detail"]


def test_a_stuck_event_becomes_a_finding_and_a_fresh_one_does_not(db_session,
                                                                  stripe):
    _event(db_session, status=EVENT_RECEIVED, error=None,
           received_at=datetime.utcnow() - timedelta(hours=2))
    _event(db_session, status=EVENT_RECEIVED, error=None)

    rows = [f for f in bi.run(db_session, include_stripe=False)["findings"]
            if f["code"] == "webhook_stuck"]

    assert len(rows) == 1


def test_a_failed_webhook_is_replayed_from_its_stored_payload(db_session,
                                                              stripe):
    """Not a re-request to Stripe and not a fabricated event: the body we
    already received, through the same handler live processing uses."""
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    payload = {"id": "evt_replay", "type": "invoice.payment_succeeded",
               "created": 1790000000,
               "data": {"object": {"id": "in_replay", "customer": "cus_x",
                                   "status": "paid", "currency": "usd",
                                   "number": "INV-R", "total": 49900,
                                   "amount_due": 0, "amount_paid": 49900,
                                   "status_transitions": {}}}}
    event = _event(db_session, status=EVENT_FAILED, payload=payload,
                   event_type="invoice.payment_succeeded")

    result = bi.apply_repair(db_session, "webhook_failed", event.id,
                             dry_run=False)

    db_session.expire_all()
    assert result["applied"] is True
    mirrored = db_session.query(Invoice).filter(
        Invoice.stripe_invoice_id == "in_replay").first()
    assert mirrored is not None and mirrored.status == "paid"


def test_an_event_with_no_retained_body_cannot_be_replayed(db_session, stripe):
    event = _event(db_session, status=EVENT_FAILED, payload=None)

    result = bi.apply_repair(db_session, "webhook_failed", event.id,
                             dry_run=False)

    assert result["applied"] is False
    assert "redelivered" in result["plan"]["reason"]


def test_replaying_an_already_processed_event_does_nothing(db_session, stripe):
    event = _event(db_session, status=EVENT_PROCESSED, payload={"id": "e"},
                   error=None)

    result = bi.apply_repair(db_session, "webhook_failed", event.id,
                             dry_run=False)

    assert result["applied"] is False
    assert "Already processed" in result["plan"]["reason"]


def test_a_replay_that_fails_again_reports_rather_than_corrupting(db_session,
                                                                  stripe):
    event = _event(db_session, status=EVENT_FAILED,
                   payload={"nonsense": True})

    result = bi.apply_repair(db_session, "webhook_failed", event.id,
                             dry_run=False)

    assert result["applied"] is False


# ═════════════════════════════════════════════════════════════════════════════
# THE HTTP SURFACE
# ═════════════════════════════════════════════════════════════════════════════

def test_integrity_is_platform_only(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand)
    tenant = _user(db_session, org, "org_admin")
    existing = json.loads(org.delegated_capabilities or "[]")
    existing.append(BILLING_MANAGE)
    org.delegated_capabilities = json.dumps(existing)
    db_session.add(UserCapabilityGrant(user_id=tenant.id,
                                       organization_id=org.id,
                                       capability=BILLING_MANAGE,
                                       is_active=True))
    db_session.commit()
    headers = _headers(db_session, tenant)

    for path in ("%s/integrity" % BASE, "%s/webhook-health" % BASE):
        assert client.get(path, headers=headers).status_code == 403, path
    assert client.post("%s/integrity/repair" % BASE, headers=headers,
                       json={"code": "stale_org_billing_status",
                             "target_id": org.id,
                             "apply": True}).status_code == 403


def test_a_god_admin_gets_the_report(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, stripe_customer_id="cus_x")
    _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))

    body = client.get("%s/integrity" % BASE, headers=headers).json()

    assert body["dry_run"] is True
    assert body["mutations_performed"] == 0
    assert "agreement_without_subscription" in {f["code"] for f in body["findings"]}


def test_the_repair_endpoint_defaults_to_a_dry_run(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, billing_status="active")
    _invoice(db_session, org, status="open", attempts=3)
    headers = _headers(db_session, _god(db_session))
    before = _snapshot(db_session)

    body = client.post("%s/integrity/repair" % BASE, headers=headers,
                       json={"code": "stale_org_billing_status",
                             "target_id": org.id}).json()

    assert body["dry_run"] is True and body["applied"] is False
    db_session.expire_all()
    assert _snapshot(db_session) == before


def test_the_repair_endpoint_refuses_a_business_change_with_a_reason(
        client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand)
    agreement = _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/integrity/repair" % BASE, headers=headers,
                    json={"code": "amount_disagreement",
                          "target_id": agreement.id, "apply": True})

    assert r.status_code == 400
    assert "business decision" in r.json()["detail"]


def test_a_reconciliation_run_is_audited(client, db_session, stripe):
    """Reading every customer's billing state is privileged even though it
    changes nothing, and an audit trail that records only writes cannot answer
    'who looked'."""
    god = _god(db_session)
    headers = _headers(db_session, god)

    client.get("%s/integrity" % BASE, headers=headers)

    entry = (db_session.query(AuditLogEntry)
             .filter(AuditLogEntry.action == "billing_reconciliation_run")
             .first())
    assert entry is not None and entry.actor_user_id == god.id


def test_an_applied_safe_repair_is_audited(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, platform=brand, billing_status="active")
    _invoice(db_session, org, status="open", attempts=3)
    headers = _headers(db_session, _god(db_session))

    client.post("%s/integrity/repair" % BASE, headers=headers,
                json={"code": "stale_org_billing_status",
                      "target_id": org.id, "apply": True})

    actions = [e.action for e in db_session.query(AuditLogEntry).all()]
    assert "billing_safe_repair_applied" in actions


def test_webhook_health_over_http_carries_no_secret(client, db_session,
                                                    monkeypatch, stripe):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_topsecret")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_topsecret")
    _event(db_session, status=EVENT_FAILED, payload={"card": "4242424242424242"})
    headers = _headers(db_session, _god(db_session))

    raw = client.get("%s/webhook-health" % BASE, headers=headers).text

    assert "sk_test" not in raw and "whsec" not in raw
    assert "4242424242424242" not in raw


# ═════════════════════════════════════════════════════════════════════════════
# WHOLE-SYSTEM AUDITS — the final review, as executable checks
# ═════════════════════════════════════════════════════════════════════════════

BILLING_SOURCES = [
    "app/services/billing_operations.py", "app/services/billing_access.py",
    "app/services/billing_agreement.py", "app/services/billing_migration.py",
    "app/services/billing_integrity.py", "app/services/merchant_entity.py",
    "app/services/platform_billing.py", "app/services/stripe_gateway.py",
    "app/routers/billing_router.py", "app/routers/platform_billing_router.py",
]
BILLING_FRONTEND = [
    "frontend/src/pages/Billing.jsx",
    "frontend/src/pages/god/BillingCommandCenter.jsx",
]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def test_no_billing_code_does_float_money_arithmetic():
    """One rule, checked across every billing file at once."""
    for rel in BILLING_SOURCES:
        body = _read(rel)
        assert "float(" not in body, rel
        for pattern in (r"/ ?100\b", r"\* ?100\b", r"\bround\("):
            hits = [l for l in body.splitlines()
                    if re.search(pattern, l) and not l.strip().startswith("#")]
            assert not hits, (rel, hits[:3])


def test_no_billing_code_uses_the_legacy_tenant_column_as_authority():
    """P3 removed `users.organization_id` from billing authority.

    Checked through the AST rather than by reading lines, so the comments and
    docstrings that EXPLAIN why it was removed do not trip the test and,
    more importantly, cannot hide a real use inside a string either.
    """
    import ast

    for rel in BILLING_SOURCES:
        tree = ast.parse(_read(rel))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr != "organization_id":
                continue
            base = node.value
            name = getattr(base, "id", None)
            assert name not in ("current_user", "user"), (
                rel, "line %d: %s.organization_id is the legacy tenant column"
                % (node.lineno, name))


def test_plans_is_never_a_pricing_authority_outside_the_legacy_checkout():
    """P5's boundary, still holding after three more phases."""
    for rel in BILLING_SOURCES:
        if rel.endswith("billing_router.py"):
            continue
        body = _read(rel)
        for line in body.splitlines():
            if "PLANS" in line and "import" in line:
                assert "billing_migration" in rel, (rel, line)


def test_no_payment_method_is_named_as_a_gate_anywhere():
    """Stripe decides eligibility. The only place a method name appears is the
    display-label map, which is read with a fallback and gates nothing."""
    for rel in BILLING_SOURCES + BILLING_FRONTEND:
        body = _read(rel)
        assert "payment_method_types" not in body, rel
        if rel.endswith("billing_operations.py"):
            continue  # _METHOD_LABELS, asserted below
        for method in ("afterpay", "klarna", "affirm", "cashapp"):
            assert method not in body.lower(), (rel, method)


def test_the_only_payment_method_names_are_display_labels():
    body = _read("app/services/billing_operations.py")
    labels = body.split("_METHOD_LABELS")[1].split("}")[0]
    for method in ("afterpay", "klarna", "affirm", "cashapp"):
        assert method in labels.lower()
    # Read with a fallback, so an unmapped method still renders.
    assert "_METHOD_LABELS.get(" in body


def test_no_secret_ever_reaches_the_frontend():
    for rel in BILLING_FRONTEND:
        body = _read(rel)
        for secret in ("sk_test", "sk_live", "whsec", "STRIPE_SECRET",
                       "api_key"):
            assert secret not in body, (rel, secret)


def test_no_stripe_secret_is_stored_in_any_model():
    """Keys are read from the environment on every call and never persisted."""
    for rel in ("app/models/billing_models.py",
                "app/models/billing_entity_models.py",
                "app/models/billing_agreement_models.py"):
        body = _read(rel).lower()
        for banned in ("secret_key", "api_key", "webhook_secret",
                       "private_key"):
            assert banned not in body, (rel, banned)


def test_the_gateway_still_refuses_a_live_key():
    from app.services.stripe_gateway import LiveModeRefused, assert_test_mode

    for key in ("sk_live_x", "rk_live_x"):
        with pytest.raises(LiveModeRefused):
            assert_test_mode(key)
    assert_test_mode("sk_test_x")


def test_every_billing_model_module_is_in_the_registry():
    """NO ALEMBIC IN PRODUCTION: a model module absent from the registry is a
    table `create_all()` never creates."""
    registry = _read("app/models/registry.py")
    for module in ("billing_models", "billing_entity_models",
                   "billing_agreement_models"):
        assert "app.models.%s" % module in registry, module


def test_every_billing_column_on_an_existing_table_is_auto_migrated():
    """`create_all()` never adds a column to a table that already exists."""
    from app.auto_migrate import COLUMNS_TO_ADD

    entries = {(t, c) for t, c, _ in COLUMNS_TO_ADD}
    assert ("payments", "payment_method_type") in entries
    assert ("platforms", "merchant_entity_id") in entries


def test_no_billing_code_depends_on_alembic():
    for rel in BILLING_SOURCES:
        assert "alembic" not in _read(rel).lower(), rel


def test_the_two_frontend_surfaces_remain_separate():
    cc = _read("frontend/src/pages/god/BillingCommandCenter.jsx")
    customer = _read("frontend/src/pages/Billing.jsx")
    assert "pages/Billing" not in cc and "../Billing" not in cc
    assert "/platform/billing" not in customer
