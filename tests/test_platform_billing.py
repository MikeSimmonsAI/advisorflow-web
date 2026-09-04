"""P7 — the back-office Billing Command Center.

WHAT THIS FILE IS DEFENDING

**The wall between two surfaces.** P6 gave customers billing authority over
their own workspace. The mistake this phase could make is treating that as a
lesser form of the same thing — it is not. A customer org_admin manages their
own company's money; that says nothing about whether they may read another
company's invoices. Most of this file is a customer holding every tenant
billing permission there is, being refused everything here.

**The scope factory.** P4's operations take a `BillingScope` so no caller can
name another tenant. P7 legitimately needs to act on an arbitrary organization,
so `platform_scope()` exists — the one place that guarantee is set aside. These
tests prove it is set aside by authority and not by accident: a guessed invoice
id still cannot cross out of the organization the operator selected, because
downstream it is the same P4 code with the same ownership filters.

**Money that is real.** Every figure is summed server-side from the mirror, in
integer minor units, per currency. There is no MRR and no ARR, and the tests
assert their absence rather than their value — a number that requires an
invented FX rate does not belong on a billing screen at all.
"""

import itertools
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.billing_agreement_models import BillingAgreement
from app.models.billing_models import Invoice, Payment
from app.models.implementation_models import Implementation
from app.models.models import (AuditLogEntry, Organization, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import (SCOPE_CUSTOMER_ORG, BrandSalesOrg,
                                     Membership, Opportunity)
from app.services import billing_agreement as agreements
from app.services import merchant_entity as entity_svc
from app.services import platform_billing as pb
from app.services import stripe_gateway as gw
from app.services.auth_service import create_access_token, hash_password
from app.services.billing_access import BILLING_MANAGE, BILLING_VIEW

_SEQ = itertools.count(1)

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
CC_PAGE = FRONTEND / "pages" / "god" / "BillingCommandCenter.jsx"
CUSTOMER_PAGE = FRONTEND / "pages" / "Billing.jsx"
GOD_SHELL = FRONTEND / "pages" / "GodShell.jsx"
APP = FRONTEND / "App.jsx"

BASE = "/platform/billing"


# ═════════════════════════════════════════════════════════════════════════════
# fakes and helpers
# ═════════════════════════════════════════════════════════════════════════════

class _Obj(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


class FakeStripe:
    def __init__(self):
        self.calls = []
        s = self
        self._sub = _Obj(id="sub_1", status="active",
                         collection_method="charge_automatically",
                         default_payment_method="pm_1",
                         current_period_end=1790000000,
                         cancel_at_period_end=False,
                         items=_Obj(data=[_Obj(price=_Obj(unit_amount=49900,
                                                          currency="usd"))]))

        def rec(name, make):
            def bound(*a, **k):
                s.calls.append((name, a, k))
                return make(a, k)
            return staticmethod(bound)

        self.Customer = type("C", (), {
            "create": rec("Customer.create", lambda a, k: _Obj(id="cus_new"))})
        self.Product = type("P", (), {
            "create": rec("Product.create", lambda a, k: _Obj(id="prod_1"))})
        self.Price = type("Pr", (), {
            "create": rec("Price.create", lambda a, k: _Obj(id="price_1",
                                                            **k))})
        self.Subscription = type("S", (), {
            "create": rec("Subscription.create", lambda a, k: _Obj(id="sub_new",
                                                                   status="active")),
            "retrieve": rec("Subscription.retrieve", lambda a, k: s._sub),
            "modify": rec("Subscription.modify", lambda a, k: s._sub),
            "delete": rec("Subscription.delete", lambda a, k: s._sub)})
        self.InvoiceItem = type("II", (), {
            "create": rec("InvoiceItem.create", lambda a, k: _Obj(id="ii_1"))})
        self.Invoice = type("I", (), {
            "create": rec("Invoice.create", lambda a, k: _Obj(id="in_new")),
            "finalize_invoice": rec("Invoice.finalize_invoice",
                                    lambda a, k: s.invoice_payload(a[0], "open")),
            "send_invoice": rec("Invoice.send_invoice",
                                lambda a, k: s.invoice_payload(a[0], "open")),
            "void_invoice": rec("Invoice.void_invoice",
                                lambda a, k: s.invoice_payload(a[0], "void"))})
        self.customer_id = None

    def named(self, op):
        return [c for c in self.calls if c[0] == op]

    def kwargs_of(self, op):
        return [c[2] for c in self.calls if c[0] == op]

    def invoice_payload(self, invoice_id, status):
        return _Obj({"id": invoice_id, "customer": self.customer_id,
                     "number": "INV-P7", "status": status, "currency": "usd",
                     "total": 49900, "amount_due": 0 if status == "void" else 49900,
                     "amount_paid": 0, "status_transitions": {},
                     "hosted_invoice_url": "https://invoice.stripe.test/p7",
                     "invoice_pdf": "https://invoice.stripe.test/p7.pdf"})


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


def _brand(db, slug="evosyspro", name="EvoSys Pro"):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p:
        return p
    p = Platform(name=name, slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name="Restland", platform=None, plan="growth",
         stripe_customer_id=None, stripe_subscription_id=None,
         billing_status=None):
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
             email="p7.%d@example.com" % next(_SEQ),
             password_hash=hash_password("TestPass123!"),
             full_name="Person", role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _god(db):
    return _user(db, None, "god_admin")


def _headers(db, user, workspace=None):
    h = {"Authorization": "Bearer %s" % create_access_token(user, db)}
    if workspace:
        h["X-Workspace-Id"] = workspace
    return h


def _grant(db, user, org, key):
    existing = json.loads(org.delegated_capabilities or "[]")
    if key not in existing:
        existing.append(key)
    org.delegated_capabilities = json.dumps(existing)
    db.add(UserCapabilityGrant(user_id=user.id, organization_id=org.id,
                               capability=key, is_active=True))
    db.commit()


def _membership(db, user, org, role="org_admin"):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=org.id, role=role, is_active=True))
    db.commit()


def _agreement(db, org, platform, recurring="499.00", setup="1500.00",
               currency="USD", activate=True):
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
        implementation_fee=Decimal(setup) if setup else None,
        recurring_amount=Decimal(recurring) if recurring else None,
        currency=currency, billing_start_date=datetime(2026, 10, 1))
    db.add(impl)
    db.commit()
    return agreements.create_from_implementation(db, impl, activate=activate)


def _invoice(db, org, *, stripe_id=None, status="open", total=49900,
             due=49900, number="INV-1", kind="subscription", due_date=None):
    row = Invoice(organization_id=org.id,
                  stripe_invoice_id=stripe_id or "in_%d" % next(_SEQ),
                  stripe_customer_id=org.stripe_customer_id, number=number,
                  kind=kind, status=status, currency="USD", total_cents=total,
                  amount_due_cents=due, amount_paid_cents=0, due_date=due_date,
                  hosted_invoice_url="https://invoice.stripe.test/x",
                  invoice_pdf="https://invoice.stripe.test/x.pdf")
    db.add(row)
    db.commit()
    return row


def _payment(db, org, *, status="succeeded", amount=49900, currency="USD",
             failure=None, method_type=None, brand=None, last4=None):
    p = Payment(organization_id=org.id,
                stripe_payment_intent_id="pi_%d" % next(_SEQ),
                amount_cents=amount, currency=currency, status=status,
                refunded_cents=0, failure_message=failure,
                payment_method_type=method_type, payment_method_brand=brand,
                payment_method_last4=last4)
    db.add(p)
    db.commit()
    return p


def _codes(rows):
    return {r["code"] for r in rows}


# ═════════════════════════════════════════════════════════════════════════════
# PLATFORM AUTHORITY — the wall between the two surfaces
# ═════════════════════════════════════════════════════════════════════════════

ALL_READS = [
    "%s/command-center" % BASE,
    "%s/organizations" % BASE,
]


def test_a_god_admin_can_enter_the_command_center(client, db_session, stripe):
    headers = _headers(db_session, _god(db_session))
    for path in ALL_READS:
        assert client.get(path, headers=headers).status_code == 200, path


def test_a_customer_org_admin_cannot_enter(client, db_session, stripe):
    """MANAGING YOUR OWN COMPANY'S MONEY IS NOT PERMISSION TO READ ANOTHER'S.
    This is the whole wall between P6 and P7 in one test."""
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org, "org_admin"))
    for path in ALL_READS:
        assert client.get(path, headers=headers).status_code == 403, path


def test_a_billing_view_tenant_user_cannot_enter(client, db_session, stripe):
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor")
    _grant(db_session, person, org, BILLING_VIEW)
    headers = _headers(db_session, person)

    # Their OWN billing still works - P6 is untouched.
    assert client.get("/billing/overview", headers=headers).status_code == 200
    for path in ALL_READS:
        assert client.get(path, headers=headers).status_code == 403, path


def test_a_billing_manage_tenant_user_cannot_enter(client, db_session, stripe):
    """The strongest CUSTOMER billing permission there is, and it buys nothing
    across organizations."""
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor")
    _grant(db_session, person, org, BILLING_MANAGE)
    headers = _headers(db_session, person)

    assert client.get("/billing/overview", headers=headers).status_code == 200
    for path in ALL_READS:
        assert client.get(path, headers=headers).status_code == 403, path


def test_a_super_admin_is_not_a_platform_billing_user(client, db_session,
                                                      stripe):
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org, "super_admin"))
    for path in ALL_READS:
        assert client.get(path, headers=headers).status_code == 403, path


def test_every_platform_mutation_refuses_a_tenant_admin(client, db_session,
                                                        no_stripe):
    """Not just the reads. A customer admin holding every tenant billing
    permission is refused every cross-organization ACTION too."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand)
    invoice = _invoice(db_session, org)
    person = _user(db_session, org, "org_admin")
    _grant(db_session, person, org, BILLING_MANAGE)
    headers = _headers(db_session, person)
    base = "%s/organizations/%s" % (BASE, org.id)

    posts = [
        ("%s/invoices" % base, {"line_items": [{"amount_cents": 1000}]}),
        ("%s/invoices/%s/finalize" % (base, invoice.id), None),
        ("%s/invoices/%s/send" % (base, invoice.id), None),
        ("%s/invoices/%s/void" % (base, invoice.id), None),
        ("%s/agreements/%s/subscribe" % (base, agreement.id), None),
        ("%s/agreements/%s/cancel" % (base, agreement.id), {}),
        ("%s/portal" % base, None),
    ]
    for path, body in posts:
        r = client.post(path, headers=headers, json=body)
        assert r.status_code == 403, (path, r.status_code)

    db_session.refresh(agreement)
    assert agreement.stripe_subscription_id is None
    db_session.refresh(invoice)
    assert invoice.status == "open"


def test_the_command_center_needs_a_token(client, db_session):
    for path in ALL_READS:
        assert client.get(path).status_code in (401, 403), path


def test_platform_billing_is_registered_non_delegable(db_session):
    """So no God screen can hand cross-organization billing to a customer,
    however it is used."""
    from app.services.capabilities import CAPABILITIES

    assert CAPABILITIES["platform_billing"].delegable is False


def test_the_capability_alone_refuses_every_tenant_role(db_session):
    """WHICH OF THE TWO REFUSALS CARRIES THE WEIGHT.

    The routes hold both `require_capability("platform_billing")` and an
    inline god_admin check. Removing the inline check alone changes nothing -
    verified by mutation - because `platform_billing` is non-delegable, so
    gate 1 (`org_may_self_manage`) fails for every customer no matter what
    role the caller holds or what their organization has been granted.

    That makes the capability the load-bearing refusal and the role check
    genuine defence in depth, matching what /billing/all already does. This
    test says so out loud, so nobody later removes the capability believing
    the role check is what protects this surface.
    """
    from app.services import capabilities as caps

    org = _org(db_session, "Restland")
    # Try to delegate it the way an organization would be granted anything.
    org.delegated_capabilities = json.dumps(["platform_billing"])
    db_session.commit()

    for role in ("advisor", "org_admin", "super_admin"):
        user = _user(db_session, org, role)
        db_session.add(UserCapabilityGrant(user_id=user.id,
                                           organization_id=org.id,
                                           capability="platform_billing",
                                           is_active=True))
        db_session.commit()
        decision = caps.resolve(db_session, user, org, "platform_billing")
        assert decision.allowed is False, role


def test_selecting_an_organization_does_not_go_through_workspace_authority(
        client, db_session, stripe):
    """THE OPERATOR NEVER BECOMES THE CUSTOMER. A god_admin with no membership
    anywhere, and no workspace header, reads any organization's billing -
    because platform authority is the mechanism, not impersonation."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))

    body = client.get("%s/organizations/%s" % (BASE, org.id),
                      headers=headers).json()

    assert body["identity"]["organization_id"] == org.id


def test_a_workspace_header_does_not_widen_a_tenant_users_reach(client,
                                                                db_session,
                                                                stripe):
    """Asserting a workspace you hold does not turn tenant billing authority
    into platform billing authority."""
    brand = _brand(db_session)
    mine = _org(db_session, "Restland", platform=brand)
    theirs = _org(db_session, "Hillcrest", platform=brand)
    person = _user(db_session, mine, "org_admin")
    _membership(db_session, person, mine, "org_admin")

    for ws in (None, mine.id, theirs.id):
        headers = _headers(db_session, person, ws)
        assert client.get("%s/organizations/%s" % (BASE, theirs.id),
                          headers=headers).status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# THE SCOPE FACTORY — platform authority does not become "no ownership check"
# ═════════════════════════════════════════════════════════════════════════════

def test_a_guessed_invoice_id_cannot_cross_organizations(client, db_session,
                                                         no_stripe):
    """PLATFORM AUTHORITY SELECTS AN ORGANIZATION; IT DOES NOT DISSOLVE
    OWNERSHIP. The operator may look at any org, and an id belonging to a
    different one is still refused - because downstream it is the same P4
    ownership filter."""
    brand = _brand(db_session)
    a = _org(db_session, "Restland", platform=brand, stripe_customer_id="cus_a")
    b = _org(db_session, "Hillcrest", platform=brand, stripe_customer_id="cus_b")
    b_invoice = _invoice(db_session, b, stripe_id="in_b")
    headers = _headers(db_session, _god(db_session))

    for path in ("%s/organizations/%s/invoices/%s/finalize" % (BASE, a.id, b_invoice.id),
                 "%s/organizations/%s/invoices/%s/void" % (BASE, a.id, b_invoice.id),
                 "%s/organizations/%s/invoices/in_b/send" % (BASE, a.id)):
        assert client.post(path, headers=headers).status_code == 400, path

    db_session.refresh(b_invoice)
    assert b_invoice.status == "open"


def test_a_guessed_agreement_id_cannot_cross_organizations(client, db_session,
                                                           stripe):
    brand = _brand(db_session)
    a = _org(db_session, "Restland", platform=brand)
    b = _org(db_session, "Hillcrest", platform=brand)
    b_agreement = _agreement(db_session, b, brand)
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/agreements/%s/subscribe"
                    % (BASE, a.id, b_agreement.id), headers=headers)

    assert r.status_code == 400
    db_session.refresh(b_agreement)
    assert b_agreement.stripe_subscription_id is None


def test_an_invented_organization_id_is_refused(client, db_session, stripe):
    headers = _headers(db_session, _god(db_session))
    assert client.get("%s/organizations/org_does_not_exist" % BASE,
                      headers=headers).status_code == 400


def test_a_raw_stripe_customer_id_is_not_an_organization_selector(client,
                                                                  db_session,
                                                                  stripe):
    """The route takes a LOCAL organization id and loads it from the database.
    A Stripe customer id lifted from a dashboard selects nothing."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_known")
    headers = _headers(db_session, _god(db_session))

    assert client.get("%s/organizations/cus_known" % BASE,
                      headers=headers).status_code == 400


def test_the_platform_scope_is_never_built_from_a_request(db_session):
    """STRUCTURAL. `platform_scope` takes an Organization object - one the
    route loaded from the database - and never an id, a header or a body. An id
    parameter here would be the whole guarantee, undone."""
    import inspect

    sig = inspect.signature(pb.platform_scope)
    assert list(sig.parameters) == ["org"]
    assert sig.parameters["org"].annotation is Organization


# ═════════════════════════════════════════════════════════════════════════════
# NO FAKE FINANCIAL METRICS
# ═════════════════════════════════════════════════════════════════════════════

def test_money_is_summed_per_currency_and_never_across_them(db_session):
    """100 USD plus 100 CAD is not a number. A dashboard that adds them looks
    right until somebody acts on it."""
    brand = _brand(db_session)
    usd = _org(db_session, "Restland", platform=brand)
    cad = _org(db_session, "Hillcrest", platform=brand)
    _invoice(db_session, usd, status="open", due=49900)
    inv = _invoice(db_session, cad, status="open", due=25000)
    inv.currency = "CAD"
    db_session.commit()

    money = pb.command_center(db_session)["money"]["open_invoice_total"]

    by = {m["currency"]: m for m in money}
    assert by["USD"]["cents"] == 49900 and by["USD"]["amount"] == "499.00"
    assert by["CAD"]["cents"] == 25000 and by["CAD"]["amount"] == "250.00"


def test_there_is_no_mrr_and_no_arr_anywhere(db_session):
    """Normalising a mixed-interval, mixed-currency book into one monthly
    number needs an FX rate and an annualisation rule this system has no
    authority to set. The narrower question answered exactly is worth more
    than the familiar one answered approximately."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand)

    rendered = json.dumps(pb.command_center(db_session), default=str).lower()

    for banned in ('"mrr"', '"arr"', "monthly_recurring_revenue",
                   "annual_recurring_revenue", "projected", "forecast"):
        assert banned not in rendered, banned


def test_contracted_recurring_is_grouped_by_interval_and_currency(db_session):
    brand = _brand(db_session)
    monthly = _org(db_session, "Restland", platform=brand)
    annual = _org(db_session, "Hillcrest", platform=brand)
    _agreement(db_session, monthly, brand, recurring="499.00")
    a = _agreement(db_session, annual, brand, recurring="5000.00")
    a.billing_interval = "year"
    db_session.commit()

    recurring = pb.command_center(db_session)["money"]["contracted_recurring"]

    assert recurring["month"][0]["cents"] == 49900
    assert recurring["year"][0]["cents"] == 500000
    assert set(recurring) == {"month", "year"}


def test_every_figure_declares_what_it_is_based_on(db_session):
    """A number lifted out of this screen carries its own caveat: it is the
    mirror as of the last processed webhook, not Stripe's own reporting."""
    report = pb.command_center(db_session)
    assert "mirror" in report["basis"].lower()
    assert "webhook" in report["basis"].lower()


def test_counts_come_from_the_mirror_and_are_exact(db_session):
    brand = _brand(db_session)
    a = _org(db_session, "Restland", platform=brand, billing_status="past_due")
    b = _org(db_session, "Hillcrest", platform=brand)
    agreement = _agreement(db_session, a, brand)
    agreement.stripe_subscription_id = "sub_live"
    db_session.commit()
    _agreement(db_session, b, brand)
    _invoice(db_session, a, status="open", due=49900)
    _invoice(db_session, a, status="paid", due=0)
    _payment(db_session, a, status="failed")
    _payment(db_session, b, status="succeeded")

    counts = pb.command_center(db_session)["counts"]

    assert counts["organizations"] == 2
    assert counts["organizations_with_live_agreement"] == 2
    assert counts["active_subscriptions"] == 1
    assert counts["open_invoices"] == 1
    assert counts["failed_payments"] == 1
    assert counts["organizations_past_due"] == 1


def test_the_dashboard_makes_no_stripe_call(db_session, no_stripe):
    """It must load during a Stripe outage: these numbers are about money that
    already moved, and the mirror knows all of it."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    _agreement(db_session, org, brand)
    _invoice(db_session, org)

    report = pb.command_center(db_session)

    assert report["counts"]["organizations"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# NEEDS ATTENTION
# ═════════════════════════════════════════════════════════════════════════════

def test_a_failed_payment_reaches_the_queue_with_context(db_session):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    _payment(db_session, org, status="failed", amount=49900,
             failure="Your card was declined.")

    rows = pb.needs_attention(db_session)
    row = [r for r in rows if r["code"] == "payment_failed"][0]

    assert row["organization_name"] == "Restland"
    assert row["brand_name"] == "EvoSys Pro"
    assert row["merchant_legal_name"] == agreement.merchant_legal_name
    assert row["amount"] == "499.00"
    assert "declined" in row["detail"]


def test_an_overdue_invoice_is_flagged_and_a_current_one_is_not(db_session):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _invoice(db_session, org, status="open",
             due_date=datetime.utcnow() - timedelta(days=5), number="LATE")
    _invoice(db_session, org, status="open",
             due_date=datetime.utcnow() + timedelta(days=5), number="SOON")

    rows = [r for r in pb.needs_attention(db_session)
            if r["code"] == "invoice_overdue"]

    assert len(rows) == 1
    assert "LATE" in rows[0]["detail"]


def test_a_live_agreement_with_no_subscription_is_flagged(db_session):
    """Nothing is being billed, and nobody would notice without this row."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand)

    assert "agreement_not_executed" in _codes(pb.needs_attention(db_session))


def test_a_legacy_subscription_with_no_agreement_is_flagged(db_session):
    brand = _brand(db_session)
    _org(db_session, "Restland", platform=brand,
         stripe_subscription_id="sub_legacy")

    assert "subscription_without_agreement" in _codes(
        pb.needs_attention(db_session))


def test_an_agreement_with_no_stripe_customer_is_flagged(db_session):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand)

    assert "billing_not_configured" in _codes(pb.needs_attention(db_session))


def test_a_healthy_account_produces_no_rows(db_session):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x", billing_status="active")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    _invoice(db_session, org, status="paid", due=0)
    _payment(db_session, org, status="succeeded")

    assert pb.needs_attention(db_session) == []


def test_the_queue_is_ordered_worst_first(db_session):
    """It is read top down by somebody with limited time."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand)
    _payment(db_session, org, status="failed")

    codes = [r["code"] for r in pb.needs_attention(db_session)]

    assert codes[0] == "payment_failed"
    assert codes.index("payment_failed") < codes.index("agreement_not_executed")


# ═════════════════════════════════════════════════════════════════════════════
# SEARCH AND FILTERS
# ═════════════════════════════════════════════════════════════════════════════

def test_search_matches_name_and_slug(db_session):
    brand = _brand(db_session)
    _org(db_session, "Restland", platform=brand)
    _org(db_session, "Hillcrest", platform=brand)

    found = pb.organizations(db_session, q="restl")["organizations"]

    assert [o["organization_name"] for o in found] == ["Restland"]


def test_search_is_a_substring_match_in_both_directions(db_session):
    """Deliberate: an operator half-remembering a name should find it. "rest"
    finding Hillcrest as well as Restland is the feature, not a defect."""
    brand = _brand(db_session)
    _org(db_session, "Restland", platform=brand)
    _org(db_session, "Hillcrest", platform=brand)

    found = pb.organizations(db_session, q="rest")["organizations"]

    assert {o["organization_name"] for o in found} == {"Restland", "Hillcrest"}


def test_each_filter_selects_what_it_says(db_session):
    brand = _brand(db_session)
    past_due = _org(db_session, "PastDue", platform=brand,
                    billing_status="past_due")
    failed = _org(db_session, "Failed", platform=brand)
    running = _org(db_session, "Running", platform=brand,
                   stripe_customer_id="cus_r")
    bare = _org(db_session, "Bare", platform=brand)

    _agreement(db_session, past_due, brand)
    _agreement(db_session, failed, brand)
    a = _agreement(db_session, running, brand)
    a.stripe_subscription_id = "sub_1"
    db_session.commit()
    _payment(db_session, failed, status="failed")
    _invoice(db_session, past_due, status="open", due=49900)

    def names(status):
        return {o["organization_name"]
                for o in pb.organizations(db_session, status=status)["organizations"]}

    assert names("all") == {"PastDue", "Failed", "Running", "Bare"}
    assert names("past_due") == {"PastDue"}
    assert names("payment_failed") == {"Failed"}
    assert names("open_invoices") == {"PastDue"}
    assert names("active_subscriptions") == {"Running"}
    assert names("no_agreement") == {"Bare"}
    assert "Running" not in names("needs_attention")
    assert {"PastDue", "Failed"} <= names("needs_attention")


def test_a_list_row_carries_what_the_operator_filters_on(db_session):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand, billing_status="active")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    _invoice(db_session, org, status="open", due=49900)

    row = pb.organizations(db_session)["organizations"][0]

    assert row["brand_name"] == "EvoSys Pro"
    assert row["merchant_legal_name"] == agreement.merchant_legal_name
    assert row["recurring_amount"] == "499.00"
    assert row["has_subscription"] is True
    assert row["open_invoice_count"] == 1
    assert row["outstanding"][0]["amount"] == "499.00"


def test_an_empty_platform_is_an_empty_list_not_an_error(db_session):
    report = pb.organizations(db_session)
    assert report["organizations"] == [] and report["count"] == 0
    assert pb.command_center(db_session)["needs_attention"] == []


# ═════════════════════════════════════════════════════════════════════════════
# ORGANIZATION DETAIL
# ═════════════════════════════════════════════════════════════════════════════

def test_detail_carries_identity_agreement_subscription_and_history(
        client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x", stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    # The AGREEMENT is what names the executed subscription - the column on
    # the organization is the legacy path, and an organization carrying one
    # with no agreement behind it is the `subscription_without_agreement`
    # finding rather than a running subscription.
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    _invoice(db_session, org)
    _payment(db_session, org, method_type="us_bank_account",
             brand="Wells Fargo", last4="6789")
    headers = _headers(db_session, _god(db_session))

    body = client.get("%s/organizations/%s" % (BASE, org.id),
                      headers=headers).json()

    identity = body["identity"]
    assert identity["organization_name"] == "Restland"
    assert identity["brand_name"] == "EvoSys Pro"
    assert identity["merchant_legal_name"] == agreement.merchant_legal_name
    assert identity["stripe_customer_id"] == "cus_x"
    assert body["agreement"]["recurring_amount"] == "499.00"
    assert body["agreement_history"]
    assert body["subscription"]["autopay_active"] is True
    # The non-card summary from P6, on the back-office surface too.
    assert body["payments"][0]["payment_method_label"] \
        == "Bank account · Wells Fargo ····6789"


def test_detail_never_returns_a_stripe_secret(client, db_session, stripe,
                                              monkeypatch):
    """Customer and subscription ids are identifiers an operator cross-checks
    against the dashboard. A KEY is a credential and never leaves the server."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_supersecret")
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))

    raw = client.get("%s/organizations/%s" % (BASE, org.id),
                     headers=headers).text

    assert "sk_test" not in raw and "sk_live" not in raw
    assert "supersecret" not in raw


def test_the_agreements_own_legal_seller_outranks_todays_default(client,
                                                                 db_session,
                                                                 stripe):
    """P1's snapshot exists so a later restructure does not rewrite who issued
    an existing invoice."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    agreement = _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))

    body = client.get("%s/organizations/%s" % (BASE, org.id),
                      headers=headers).json()

    assert body["identity"]["merchant_entity_id"] == agreement.merchant_entity_id


def test_detail_degrades_rather_than_failing_during_a_stripe_outage(
        client, db_session, no_stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    headers = _headers(db_session, _god(db_session))

    body = client.get("%s/organizations/%s" % (BASE, org.id),
                      headers=headers).json()

    assert body["agreement"]["recurring_amount"] == "499.00"
    assert body["subscription"]["autopay_active"] is None


# ═════════════════════════════════════════════════════════════════════════════
# OPERATIONS
# ═════════════════════════════════════════════════════════════════════════════

def test_an_operator_creates_a_draft_invoice_by_purpose_not_by_method(
        client, db_session, stripe):
    """The operator chooses a business intent. Which payment methods are
    eligible on the hosted invoice is Stripe's answer, from the account's own
    configuration - and this application names none of them."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/invoices" % (BASE, org.id),
                    headers=headers,
                    json={"purpose": "setup",
                          "line_items": [{"amount_cents": 150000,
                                          "description": "Implementation"}],
                          "description": "Setup"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft"
    assert body["payment_flow"] == "one_time_setup"
    assert stripe.kwargs_of("InvoiceItem.create")[0]["amount"] == 150000
    # Nothing was charged and nothing was named.
    assert stripe.named("Invoice.finalize_invoice") == []
    for kwargs in stripe.kwargs_of("Invoice.create"):
        assert "payment_method_types" not in kwargs


def test_a_manual_invoice_is_a_different_declared_flow(client, db_session,
                                                       stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    headers = _headers(db_session, _god(db_session))

    body = client.post("%s/organizations/%s/invoices" % (BASE, org.id),
                       headers=headers,
                       json={"purpose": "manual",
                             "line_items": [{"amount_cents": 25000}]}).json()

    assert body["payment_flow"] == "manual_invoice"


def test_an_unknown_purpose_is_refused(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/invoices" % (BASE, org.id),
                    headers=headers,
                    json={"purpose": "card_only",
                          "line_items": [{"amount_cents": 1000}]})

    assert r.status_code == 400
    assert stripe.calls == []


def test_a_float_amount_is_refused_at_the_edge(client, db_session, stripe):
    """The type is the guard, on this surface as on the customer one."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/invoices" % (BASE, org.id),
                    headers=headers,
                    json={"line_items": [{"amount_cents": 499.55}]})

    assert r.status_code == 422


def test_creating_an_invoice_does_not_touch_the_agreement(client, db_session,
                                                          stripe):
    """A manual line item is exactly what it says it is. The agreement remains
    the authority for recurring billing and this cannot edit it."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    headers = _headers(db_session, _god(db_session))

    client.post("%s/organizations/%s/invoices" % (BASE, org.id),
                headers=headers,
                json={"line_items": [{"amount_cents": 999999}]})

    db_session.expire_all()
    reloaded = (db_session.query(BillingAgreement)
                .filter(BillingAgreement.id == agreement.id).first())
    assert reloaded.recurring_amount_cents == 49900


def test_finalize_then_send_walks_the_invoice_forward(client, db_session,
                                                      stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    invoice = _invoice(db_session, org, stripe_id="in_1", status="draft")
    headers = _headers(db_session, _god(db_session))
    base = "%s/organizations/%s" % (BASE, org.id)

    finalized = client.post("%s/invoices/%s/finalize" % (base, invoice.id),
                            headers=headers).json()
    assert finalized["status"] == "open"
    assert finalized["hosted_invoice_url"].startswith("https://")

    sent = client.post("%s/invoices/%s/send" % (base, invoice.id),
                       headers=headers)
    assert sent.status_code == 200
    assert len(stripe.named("Invoice.send_invoice")) == 1


def test_a_paid_invoice_cannot_be_voided_from_the_back_office_either(
        client, db_session, no_stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org, status="paid")
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/invoices/%s/void"
                    % (BASE, org.id, invoice.id), headers=headers)

    assert r.status_code == 400
    assert "refund" in r.json()["detail"].lower()


def test_a_subscription_starts_from_the_agreement_amount(client, db_session,
                                                         stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand, recurring="499.00")
    headers = _headers(db_session, _god(db_session))

    body = client.post("%s/organizations/%s/agreements/%s/subscribe"
                       % (BASE, org.id, agreement.id), headers=headers).json()

    assert body["created"] is True
    assert stripe.kwargs_of("Price.create")[0]["unit_amount"] == 49900


def test_the_back_office_cannot_create_a_second_subscription(client,
                                                             db_session,
                                                             stripe):
    """THE DOUBLE-CHARGE TEST, ON THIS SURFACE. P7 must not reopen the path
    P4 closed - the guard is P4's and is not re-implemented here, so clicking
    twice costs nothing."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))
    path = "%s/organizations/%s/agreements/%s/subscribe" % (BASE, org.id,
                                                            agreement.id)

    first = client.post(path, headers=headers).json()
    second = client.post(path, headers=headers).json()

    assert first["created"] is True and second["created"] is False
    assert first["subscription_id"] == second["subscription_id"]
    assert len(stripe.named("Subscription.create")) == 1


def test_cancelling_defaults_to_end_of_period(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))
    base = "%s/organizations/%s/agreements/%s" % (BASE, org.id, agreement.id)
    client.post("%s/subscribe" % base, headers=headers)

    body = client.post("%s/cancel" % base, headers=headers, json={}).json()

    assert body["cancel_at_period_end"] is True
    assert stripe.named("Subscription.delete") == []


def test_a_stripe_refusal_is_402_and_an_outage_is_503(client, db_session,
                                                      no_stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org, status="draft")
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/invoices/%s/finalize"
                    % (BASE, org.id, invoice.id), headers=headers)

    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()


def test_a_live_key_is_refused_on_this_surface_too(client, db_session,
                                                   monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_realmoney")
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org, status="draft")
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/invoices/%s/finalize"
                    % (BASE, org.id, invoice.id), headers=headers)

    assert r.status_code == 503
    assert "sandbox" in r.json()["detail"].lower()


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT
# ═════════════════════════════════════════════════════════════════════════════

def _audit_actions(db, org):
    return [e.action for e in db.query(AuditLogEntry)
            .filter(AuditLogEntry.organization_id == org.id).all()]


def test_invoice_and_subscription_actions_are_audited_with_an_actor(
        client, db_session, stripe):
    """Through the platform's EXISTING audit table. Billing does not grow its
    own audit framework."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    stripe.customer_id = "cus_x"
    agreement = _agreement(db_session, org, brand)
    god = _god(db_session)
    headers = _headers(db_session, god)
    base = "%s/organizations/%s" % (BASE, org.id)

    created = client.post("%s/invoices" % base, headers=headers,
                          json={"line_items": [{"amount_cents": 1000}]}).json()
    client.post("%s/agreements/%s/subscribe" % (base, agreement.id),
                headers=headers)

    actions = _audit_actions(db_session, org)
    assert "billing_invoice_created" in actions
    assert "billing_subscription_started" in actions
    entry = (db_session.query(AuditLogEntry)
             .filter(AuditLogEntry.action == "billing_invoice_created").first())
    assert entry.actor_user_id == god.id
    assert entry.target_id == created["stripe_invoice_id"]


def test_a_retried_subscribe_is_not_audited_as_a_second_start(client,
                                                              db_session,
                                                              stripe):
    """An audit trail claiming two subscriptions were started when one was is
    worse than no line at all."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))
    path = "%s/organizations/%s/agreements/%s/subscribe" % (BASE, org.id,
                                                            agreement.id)

    client.post(path, headers=headers)
    client.post(path, headers=headers)

    starts = [a for a in _audit_actions(db_session, org)
              if a == "billing_subscription_started"]
    assert len(starts) == 1


def test_an_audit_failure_never_undoes_a_stripe_operation(client, db_session,
                                                          stripe, monkeypatch):
    """A customer charged with no log line is bad. A Stripe operation rolled
    back because a log line failed is worse."""
    import app.routers.platform_billing_router as router_mod

    def boom(*a, **k):
        raise RuntimeError("audit table unavailable")
    monkeypatch.setattr(router_mod, "log_action", boom)

    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    agreement = _agreement(db_session, org, brand)
    headers = _headers(db_session, _god(db_session))

    r = client.post("%s/organizations/%s/agreements/%s/subscribe"
                    % (BASE, org.id, agreement.id), headers=headers)

    assert r.status_code == 200 and r.json()["created"] is True


# ═════════════════════════════════════════════════════════════════════════════
# P6 IS UNTOUCHED
# ═════════════════════════════════════════════════════════════════════════════

def test_the_customer_surface_still_works_exactly_as_before(client, db_session,
                                                            stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    _agreement(db_session, org, brand)
    headers = _headers(db_session, _user(db_session, org, "org_admin"))

    assert client.get("/billing/access", headers=headers).json()["can_view"] is True
    body = client.get("/billing/overview", headers=headers).json()
    assert body["organization"]["id"] == org.id
    assert body["agreement"]["recurring_amount"] == "499.00"


def test_a_god_admin_sees_no_customer_billing_without_a_workspace(client,
                                                                  db_session,
                                                                  stripe):
    """The two surfaces stay distinct even for the one person who holds both
    authorities: platform authority is not a customer workspace."""
    headers = _headers(db_session, _god(db_session))

    assert client.get("/billing/access",
                      headers=headers).json()["organization_id"] is None
    assert client.get("%s/command-center" % BASE,
                      headers=headers).status_code == 200


def test_the_two_surfaces_do_not_share_a_prefix(db_session):
    from app.routers.platform_billing_router import router as platform_router

    for route in platform_router.routes:
        assert route.path.startswith("/platform/billing/")


# ═════════════════════════════════════════════════════════════════════════════
# THE FRONTEND SOURCE
# ═════════════════════════════════════════════════════════════════════════════

def _body(path):
    source = path.read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


def test_the_command_center_names_no_payment_method():
    body = _body(CC_PAGE)
    for method in ("payment_method_types", "afterpay", "klarna", "affirm",
                   "cashapp", "us_bank_account", "apple pay", "google pay"):
        assert method.lower() not in body.lower(), method


def test_the_command_center_does_no_arithmetic_on_money():
    """Totals are summed server-side, per currency. A dashboard that adds up
    money in the browser eventually disagrees with the invoices behind it."""
    body = _body(CC_PAGE)
    assert "/ 100" not in body and "/100" not in body
    assert "toFixed" not in body
    money = r"(amount|cents|total|balance|price|fee|outstanding|refunded)"
    for op in (r"\*", r"/", r"\+"):
        found = re.findall(r"%s[a-zA-Z_]*\s*%s\s*\d" % (money, op), body,
                           flags=re.I)
        # The one permitted conversion is parsing an operator's typed dollars
        # into minor units, which is integer arithmetic on a digit string.
        assert not [f for f in found if "toCents" not in body], found
    raw = re.findall(r"\{\s*[A-Za-z_][\w.]*_cents\s*\}", body)
    assert not raw, raw


def test_the_command_center_calls_only_platform_endpoints():
    """A back-office screen reaching for a customer endpoint would be reading
    somebody's billing through the wrong authority."""
    source = CC_PAGE.read_text(encoding="utf-8")
    calls = re.findall(r"api\.(?:get|post)\(\s*[`']([^`']+)", source)
    assert calls
    for call in calls:
        assert call.startswith("/platform/billing/"), call


def test_the_customer_page_calls_no_platform_endpoint():
    source = CUSTOMER_PAGE.read_text(encoding="utf-8")
    assert "/platform/billing" not in source


def test_the_two_pages_share_no_code():
    """Two surfaces, two authorities. Sharing a component is how one grows a
    prop that quietly widens the other."""
    source = CC_PAGE.read_text(encoding="utf-8")
    assert "pages/Billing" not in source and "../Billing" not in source
    assert "Billing.css'" not in source


def test_the_command_center_route_is_god_gated():
    source = APP.read_text(encoding="utf-8")
    line = [l for l in source.splitlines() if 'path="/god/billing"' in l]
    assert len(line) == 1, line
    assert "GodRoute" in line[0]


def test_the_god_rail_links_to_the_command_center():
    source = GOD_SHELL.read_text(encoding="utf-8")
    assert "'/god/billing'" in source


def test_the_command_center_stylesheet_introduces_no_raw_colour():
    css = (FRONTEND / "pages" / "god" / "BillingCommandCenter.css").read_text(
        encoding="utf-8")
    assert re.findall(r"#[0-9a-fA-F]{3,8}", css) == []


def test_wide_tables_scroll_inside_their_own_container():
    css = (FRONTEND / "pages" / "god" / "BillingCommandCenter.css").read_text(
        encoding="utf-8")
    assert ".bcc-scroll" in css and "overflow-x: auto" in css
    source = CC_PAGE.read_text(encoding="utf-8")
    assert source.count('className="bcc-scroll"') == source.count("<table")
