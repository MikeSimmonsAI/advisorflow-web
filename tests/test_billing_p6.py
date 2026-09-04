"""P6 — the customer workspace billing surface.

WHAT IS ACTUALLY BEING DEFENDED HERE

The screen is new; the authority is not. So most of this file is about the two
ways a UI phase can quietly undo a security phase:

  THE SIDEBAR ANSWERING FOR THE WRONG ORGANIZATION. Billing visibility used to
  be resolvable only through `my-capabilities`, which reads
  `users.organization_id` - the legacy column P3 removed from billing precisely
  because it names the wrong tenant for anyone holding two memberships. The
  tests below switch workspaces with a real header and a real membership and
  assert the answer moves.

  A HIDDEN NAV ITEM MISTAKEN FOR A LOCK. Every authorization test here calls
  the ROUTE, not the nav gate. `GET /billing/access` is asserted to be a report
  and never a gate: it answers 200 for a person with no billing authority at
  all, and that person is still refused by every billing route.

The rest pins down what the screen renders: amounts as backend-formatted
strings so no arithmetic happens in a browser, a payment method summary that
survives a non-card payment, and setup and subscription as separate things.
"""

import itertools
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.billing_agreement_models import BillingAgreement
from app.models.billing_models import Invoice, Payment
from app.models.implementation_models import Implementation
from app.models.models import (Organization, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import (SCOPE_CUSTOMER_ORG, BrandSalesOrg,
                                     Membership, Opportunity)
from app.services import billing_agreement as agreements
from app.services import billing_operations as ops
from app.services import merchant_entity as entity_svc
from app.services import stripe_gateway as gw
from app.services.auth_service import create_access_token, hash_password
from app.services.billing_access import BILLING_MANAGE, BILLING_VIEW
from app.services.billing_operations import payment_method_label

_SEQ = itertools.count(1)

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
BILLING_PAGE = FRONTEND / "pages" / "Billing.jsx"
LAYOUT = FRONTEND / "components" / "Layout.jsx"
APP = FRONTEND / "App.jsx"


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════

class _Obj(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


class FakeStripe:
    """A subscription that is live, automatic and has a method saved."""

    def __init__(self, **over):
        sub = dict(id="sub_1", status="active", current_period_end=1790000000,
                   collection_method="charge_automatically",
                   default_payment_method="pm_1", cancel_at_period_end=False,
                   items=_Obj(data=[_Obj(price=_Obj(unit_amount=49900,
                                                    currency="usd"))]))
        sub.update(over)
        self._sub = _Obj(sub)
        self.calls = []
        fake = self

        class Subscription:
            @staticmethod
            def retrieve(*a, **k):
                fake.calls.append("Subscription.retrieve")
                return fake._sub

        self.Subscription = Subscription


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


def _brand(db, slug="evosyspro"):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p:
        return p
    p = Platform(name="EvoSys Pro", slug=slug)
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
             email="p6.%d@example.com" % next(_SEQ),
             password_hash=hash_password("TestPass123!"),
             full_name="Billing Person", role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _headers(db, user, workspace=None):
    h = {"Authorization": "Bearer %s" % create_access_token(user, db)}
    if workspace is not None:
        h["X-Workspace-Id"] = workspace
    return h


def _membership(db, user, org, role="org_admin"):
    """A real customer_org membership - what makes X-Workspace-Id resolve."""
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=org.id, role=role, is_active=True))
    db.commit()


def _grant(db, user, org, key):
    existing = json.loads(org.delegated_capabilities or "[]")
    if key not in existing:
        existing.append(key)
    org.delegated_capabilities = json.dumps(existing)
    db.add(UserCapabilityGrant(user_id=user.id, organization_id=org.id,
                               capability=key, is_active=True))
    db.commit()


def _agreement(db, org, platform, recurring="499.00", setup="1500.00"):
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
        recurring_amount=Decimal(recurring), currency="USD",
        billing_start_date=datetime(2026, 10, 1))
    db.add(impl)
    db.commit()
    return agreements.create_from_implementation(db, impl, activate=True)


def _invoice(db, org, *, stripe_id=None, kind="subscription", status="open",
             total=49900, due=49900, number="INV-1"):
    row = Invoice(organization_id=org.id,
                  stripe_invoice_id=stripe_id or "in_%d" % next(_SEQ),
                  stripe_customer_id=org.stripe_customer_id, number=number,
                  kind=kind, status=status, currency="USD", total_cents=total,
                  amount_due_cents=due, amount_paid_cents=0,
                  hosted_invoice_url="https://invoice.stripe.test/x",
                  invoice_pdf="https://invoice.stripe.test/x.pdf")
    db.add(row)
    db.commit()
    return row


def _payment(db, org, **kw):
    p = Payment(organization_id=org.id,
                stripe_payment_intent_id="pi_%d" % next(_SEQ),
                amount_cents=kw.pop("amount", 49900), currency="USD",
                status=kw.pop("status", "succeeded"), refunded_cents=0, **kw)
    db.add(p)
    db.commit()
    return p


# ═════════════════════════════════════════════════════════════════════════════
# GET /billing/access — the nav gate. A REPORT, NEVER A GATE.
# ═════════════════════════════════════════════════════════════════════════════

def test_an_org_admin_is_told_they_may_view_and_manage(client, db_session):
    org = _org(db_session, "Restland")
    body = client.get("/billing/access",
                      headers=_headers(db_session, _user(db_session, org))).json()

    assert body["can_view"] is True
    assert body["can_manage"] is True
    assert body["organization_id"] == org.id


def test_a_plain_advisor_is_told_no_and_it_is_not_an_error(client, db_session):
    """200 with `can_view: false`, not 403. The sidebar has to tell 'denied'
    from 'the request failed', and a 403 makes those identical."""
    org = _org(db_session, "Restland")
    r = client.get("/billing/access",
                   headers=_headers(db_session, _user(db_session, org, "advisor")))

    assert r.status_code == 200
    assert r.json()["can_view"] is False
    assert r.json()["can_manage"] is False


def test_the_nav_answer_is_not_a_lock(client, db_session, no_stripe):
    """HIDING IS NOT ACCESS CONTROL, and this is the test that says so: the
    same person who is told 'no' is refused by every billing route, and would
    be even if the sidebar had shown the item."""
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org, "advisor"))

    assert client.get("/billing/access", headers=headers).json()["can_view"] is False
    for path in ("/billing/overview", "/billing/invoices", "/billing/payments",
                 "/billing/agreement", "/billing/reconciliation"):
        assert client.get(path, headers=headers).status_code == 403, path


def test_a_billing_view_grant_is_reported_where_my_capabilities_would_say_no(
        client, db_session):
    """THE BOOKKEEPER. `capabilities.resolve` refuses a non-admin role before
    it reads grants, so `my-capabilities` answers 'no' for exactly the person
    these capabilities were invented for. This endpoint answers correctly."""
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor")
    _grant(db_session, person, org, BILLING_VIEW)
    headers = _headers(db_session, person)

    body = client.get("/billing/access", headers=headers).json()
    assert body["can_view"] is True
    assert body["can_manage"] is False

    caps = client.get("/settings/my-capabilities", headers=headers).json()
    assert BILLING_VIEW not in caps.get("capabilities", []), (
        "if my-capabilities starts reporting billing_view, revisit the "
        "separate endpoint - but it must still resolve per WORKSPACE")


def test_a_billing_manage_grant_reports_manage(client, db_session):
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor")
    _grant(db_session, person, org, BILLING_MANAGE)

    body = client.get("/billing/access",
                      headers=_headers(db_session, person)).json()
    assert body["can_manage"] is True and body["can_view"] is True


def test_access_needs_a_token(client, db_session):
    assert client.get("/billing/access").status_code in (401, 403)


# ═════════════════════════════════════════════════════════════════════════════
# THE ACTIVE WORKSPACE DECIDES — P3 MUST NOT REGRESS THROUGH THE UI
# ═════════════════════════════════════════════════════════════════════════════

def test_switching_workspace_switches_the_billing_answer(client, db_session,
                                                         stripe):
    """THE DUAL-ROLE TEST. One person, two workspaces, admin in one only. The
    header is the only thing that changes between the two calls."""
    admin_org = _org(db_session, "Restland")
    other_org = _org(db_session, "Hillcrest")
    person = _user(db_session, admin_org, "advisor")
    _membership(db_session, person, admin_org, "org_admin")
    _membership(db_session, person, other_org, "advisor")

    in_admin = client.get("/billing/access",
                          headers=_headers(db_session, person, admin_org.id)).json()
    in_other = client.get("/billing/access",
                          headers=_headers(db_session, person, other_org.id)).json()

    assert in_admin["can_view"] is True
    assert in_admin["organization_id"] == admin_org.id
    assert in_other["can_view"] is False
    assert in_other["organization_id"] == other_org.id


def test_the_overview_follows_the_selected_workspace(client, db_session, stripe):
    brand = _brand(db_session)
    a = _org(db_session, "Restland", platform=brand)
    b = _org(db_session, "Hillcrest", platform=brand)
    person = _user(db_session, a, "advisor")
    _membership(db_session, person, a, "org_admin")
    _membership(db_session, person, b, "org_admin")
    _invoice(db_session, a, stripe_id="in_a", number="INV-A")
    _invoice(db_session, b, stripe_id="in_b", number="INV-B")

    in_a = client.get("/billing/overview",
                      headers=_headers(db_session, person, a.id)).json()
    in_b = client.get("/billing/overview",
                      headers=_headers(db_session, person, b.id)).json()

    assert [i["number"] for i in in_a["invoices"]] == ["INV-A"]
    assert [i["number"] for i in in_b["invoices"]] == ["INV-B"]


def test_asserting_a_workspace_you_do_not_hold_buys_nothing(client, db_session,
                                                            stripe):
    """The header is a REQUEST, not a grant. Editing it in devtools changes
    which workspace you ask for and never which one you get."""
    mine = _org(db_session, "Restland")
    theirs = _org(db_session, "Hillcrest")
    _invoice(db_session, theirs, stripe_id="in_theirs", number="INV-THEIRS")
    admin = _user(db_session, mine)

    body = client.get("/billing/overview",
                      headers=_headers(db_session, admin, theirs.id)).json()

    assert body["organization"]["id"] == mine.id
    assert [i["number"] for i in body["invoices"]] != ["INV-THEIRS"]


def test_another_organizations_invoice_cannot_be_opened_from_this_screen(
        client, db_session, no_stripe):
    mine = _org(db_session, "Restland")
    theirs = _org(db_session, "Hillcrest")
    their_invoice = _invoice(db_session, theirs, stripe_id="in_theirs")
    headers = _headers(db_session, _user(db_session, mine))

    for bad in (their_invoice.id, "in_theirs", "in_guessed"):
        assert client.get("/billing/invoices/%s" % bad,
                          headers=headers).status_code == 400


def test_billing_view_cannot_manage_from_the_screen(client, db_session,
                                                    no_stripe):
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    person = _user(db_session, org, "advisor")
    _grant(db_session, person, org, BILLING_VIEW)
    invoice = _invoice(db_session, org)
    headers = _headers(db_session, person)

    assert client.get("/billing/overview", headers=headers).status_code == 200
    assert client.post("/billing/invoices/%s/void" % invoice.id,
                       headers=headers).status_code == 403
    assert client.post("/billing/portal", headers=headers).status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# WHAT THE SCREEN RENDERS
# ═════════════════════════════════════════════════════════════════════════════

def test_the_overview_carries_every_key_the_page_reads(client, db_session,
                                                       stripe):
    """A CONTRACT TEST. There is no frontend test runner in this project, so
    this stands in for one: it fails the moment a key the page renders stops
    being sent, which is the drift that actually breaks this screen."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x", stripe_subscription_id="sub_1")
    _agreement(db_session, org, brand)
    _invoice(db_session, org)
    _payment(db_session, org, payment_method_type="card",
             payment_method_brand="visa", payment_method_last4="4242")

    body = client.get("/billing/overview",
                      headers=_headers(db_session, _user(db_session, org))).json()

    for key in ("organization", "merchant", "agreement", "subscription",
                "setup", "invoices", "payments", "past_due", "permissions"):
        assert key in body, key
    for key in ("recurring_amount", "currency", "billing_interval", "status",
                "package_name", "merchant_legal_name", "brand_name",
                "contract_term_months", "billing_start_date"):
        assert key in body["agreement"], key
    for key in ("autopay_active", "payment_method_on_file",
                "requires_payment_method", "cancel_at_period_end",
                "current_period_end", "stripe_state", "has_subscription"):
        assert key in body["subscription"], key
    for key in ("amount", "status", "hosted_invoice_url", "invoice_pdf",
                "payment_flow"):
        assert key in body["setup"], key
    for key in ("number", "total", "amount_due", "status",
                "hosted_invoice_url", "invoice_pdf", "created_at", "due_date"):
        assert key in body["invoices"][0], key
    for key in ("amount", "status", "payment_method_label",
                "payment_method_type", "refunded", "created_at"):
        assert key in body["payments"][0], key


def test_every_amount_is_a_preformatted_string(client, db_session, stripe):
    """SO THE BROWSER NEVER DOES ARITHMETIC. The integer is sent alongside for
    anything that must compute; the string is what renders."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    _agreement(db_session, org, brand, recurring="499.00", setup="1500.00")
    _invoice(db_session, org, kind="implementation", status="paid", due=0)
    _payment(db_session, org)

    body = client.get("/billing/overview",
                      headers=_headers(db_session, _user(db_session, org))).json()

    assert body["agreement"]["recurring_amount"] == "499.00"
    assert body["setup"]["amount"] == "1500.00"
    assert body["payments"][0]["amount"] == "499.00"
    assert isinstance(body["agreement"]["recurring_amount_cents"], int)


def test_setup_is_reported_separately_from_the_subscription(client, db_session,
                                                            stripe):
    """A SEPARATE PAYMENT AT A SEPARATE TIME, and under the approved payment
    model it may use methods a subscription cannot."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    _agreement(db_session, org, brand, recurring="499.00", setup="1500.00")
    _invoice(db_session, org, kind="implementation", status="open", due=150000,
             number="INV-SETUP")

    body = client.get("/billing/overview",
                      headers=_headers(db_session, _user(db_session, org))).json()

    assert body["setup"]["amount"] == "1500.00"
    assert body["setup"]["status"] == "unpaid"
    assert body["setup"]["invoice_number"] == "INV-SETUP"
    assert body["setup"]["payment_flow"] == "one_time_setup"
    # And it is NOT the recurring amount.
    assert body["agreement"]["recurring_amount"] == "499.00"


def test_a_setup_fee_never_invoiced_says_so(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand, setup="1500.00")

    from app.services.billing_access import BillingScope
    scope = BillingScope(_user(db_session, org), org, True, True)
    setup = ops.billing_overview(db_session, scope)["setup"]

    assert setup["status"] == "not_invoiced"
    assert setup["amount"] == "1500.00"


def test_no_setup_fee_reports_none_rather_than_zero(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand)
    _agreement(db_session, org, brand, setup=None)

    from app.services.billing_access import BillingScope
    scope = BillingScope(_user(db_session, org), org, True, True)

    assert ops.billing_overview(db_session, scope)["setup"]["status"] == "none"


# ═════════════════════════════════════════════════════════════════════════════
# AUTOPAY
# ═════════════════════════════════════════════════════════════════════════════

def test_autopay_is_active_when_all_three_facts_hold(db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()

    from app.services.billing_access import BillingScope
    out = ops.get_subscription(db_session,
                               BillingScope(None, org, True, True))

    assert out["autopay_active"] is True
    assert out["payment_method_on_file"] is True
    assert out["requires_payment_method"] is False


def test_no_saved_method_is_the_state_the_customer_must_act_on(db_session,
                                                               stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    stripe._sub["default_payment_method"] = None

    from app.services.billing_access import BillingScope
    out = ops.get_subscription(db_session, BillingScope(None, org, True, True))

    assert out["autopay_active"] is False
    assert out["requires_payment_method"] is True


def test_send_invoice_collection_is_not_autopay_and_not_a_missing_method(
        db_session, stripe):
    """A customer Stripe emails an invoice to is not failing to pay
    automatically - they were never set to."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()
    stripe._sub["collection_method"] = "send_invoice"
    stripe._sub["default_payment_method"] = None

    from app.services.billing_access import BillingScope
    out = ops.get_subscription(db_session, BillingScope(None, org, True, True))

    assert out["autopay_active"] is False
    assert out["requires_payment_method"] is False


def test_a_stripe_outage_leaves_autopay_unknown_not_off(db_session, no_stripe):
    """`None` means NOT ANSWERED. Rendering an outage as 'autopay is off'
    tells a paying customer their service is about to stop."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_subscription_id="sub_1")
    agreement = _agreement(db_session, org, brand)
    agreement.stripe_subscription_id = "sub_1"
    db_session.commit()

    from app.services.billing_access import BillingScope
    out = ops.get_subscription(db_session, BillingScope(None, org, True, True))

    assert out["autopay_active"] is None
    assert out["requires_payment_method"] is None
    assert out["recurring_amount_cents"] == 49900


# ═════════════════════════════════════════════════════════════════════════════
# PAYMENT METHODS — NOT CARD-ONLY (the defect P5 recorded)
# ═════════════════════════════════════════════════════════════════════════════

def test_a_bank_payment_gets_a_label_instead_of_a_blank_cell(db_session):
    assert payment_method_label("us_bank_account", "Wells Fargo", "6789") \
        == "Bank account · Wells Fargo ····6789"


def test_a_card_does_not_say_card_twice(db_session):
    assert payment_method_label("card", "visa", "4242") == "Visa ····4242"


def test_a_wallet_with_no_brand_still_reads(db_session):
    assert payment_method_label("cashapp", None, None) == "Cash App Pay"
    assert payment_method_label("link", None, None) == "Link"


def test_a_method_this_code_has_never_heard_of_still_renders(db_session):
    """Stripe adds payment methods without this code changing. A customer
    seeing 'Revolut Pay' for an unmapped type is right; a blank cell is the
    defect being fixed."""
    assert payment_method_label("revolut_pay", None, None) == "Revolut Pay"


def test_nothing_known_is_none_not_an_empty_string(db_session):
    assert payment_method_label(None, None, None) is None


def test_a_bnpl_payment_is_labelled_as_what_it_was(db_session):
    """Afterpay is a legitimate ONE-TIME method and shows in history as such.
    That is different from offering it as the saved recurring method, which
    nothing in this application does."""
    assert payment_method_label("afterpay_clearpay", None, None) == "Afterpay"


def test_the_mirror_records_a_non_card_method(db_session):
    """The seam itself: `upsert_payment_from_stripe` read only the `card`
    sub-object, so ACH mirrored with brand and last4 both null."""
    from app.services.stripe_sync import upsert_payment_from_stripe
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    intent = {
        "id": "pi_ach", "customer": "cus_x", "amount": 49900,
        "amount_received": 49900, "currency": "usd", "status": "succeeded",
        "payment_method_details": {
            "type": "us_bank_account",
            "us_bank_account": {"bank_name": "Wells Fargo", "last4": "6789"},
        },
    }
    row, ignored = upsert_payment_from_stripe(db_session, intent)
    db_session.commit()

    assert row is not None, ignored
    assert row.payment_method_type == "us_bank_account"
    assert row.payment_method_last4 == "6789"
    assert ops.describe_payment(row)["payment_method_label"] \
        == "Bank account · Wells Fargo ····6789"


def test_a_card_payload_without_a_type_still_mirrors(db_session):
    """Older payloads carry `card` and no `type`. Widening must not break the
    shape that already worked."""
    from app.services.stripe_sync import upsert_payment_from_stripe
    org = _org(db_session, "Restland", stripe_customer_id="cus_y")
    intent = {
        "id": "pi_card", "customer": "cus_y", "amount": 1000,
        "currency": "usd", "status": "succeeded",
        "payment_method_details": {"card": {"brand": "visa", "last4": "4242"}},
    }
    row, _ = upsert_payment_from_stripe(db_session, intent)
    db_session.commit()

    assert row.payment_method_type == "card"
    assert row.payment_method_brand == "visa"


def test_no_account_number_is_ever_stored_or_rendered(db_session):
    """The mirror holds a type, a brand and four digits. Nothing widens that."""
    from app.services.stripe_sync import upsert_payment_from_stripe
    _org(db_session, "Restland", stripe_customer_id="cus_z")
    intent = {
        "id": "pi_full", "customer": "cus_z", "amount": 1000,
        "currency": "usd", "status": "succeeded",
        "payment_method_details": {
            "type": "us_bank_account",
            "us_bank_account": {"bank_name": "Chase", "last4": "1111",
                                "account_number": "000123456789",
                                "routing_number": "021000021"},
        },
    }
    row, _ = upsert_payment_from_stripe(db_session, intent)
    db_session.commit()
    rendered = json.dumps(ops.describe_payment(row), default=str)

    assert "000123456789" not in rendered
    assert "021000021" not in rendered


def test_the_payments_column_is_registered_for_auto_migration(db_session):
    """No Alembic in production: a new column on an EXISTING table appears
    only if auto_migrate is told about it."""
    from app.auto_migrate import COLUMNS_TO_ADD

    assert ("payments", "payment_method_type", "VARCHAR") in COLUMNS_TO_ADD


# ═════════════════════════════════════════════════════════════════════════════
# PAST DUE AND RECOVERY
# ═════════════════════════════════════════════════════════════════════════════

def test_past_due_is_reported_with_what_is_owed(client, db_session, stripe):
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand, billing_status="past_due")
    _invoice(db_session, org, status="open", due=49900)
    _payment(db_session, org, status="failed",
             failure_message="Your card was declined.")

    body = client.get("/billing/overview",
                      headers=_headers(db_session, _user(db_session, org))).json()

    assert body["past_due"]["is_past_due"] is True
    assert body["past_due"]["outstanding"] == "499.00"
    assert body["past_due"]["failed_payment_count"] == 1
    assert body["payments"][0]["failure_message"] == "Your card was declined."


def test_recovery_clears_the_past_due_state(client, db_session, stripe):
    """The UI reflects backend state and invents none, so recovery is simply
    the same read returning something different."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand, billing_status="past_due")
    invoice = _invoice(db_session, org, status="open", due=49900)
    headers = _headers(db_session, _user(db_session, org))

    assert client.get("/billing/overview",
                      headers=headers).json()["past_due"]["is_past_due"] is True

    invoice.status = "paid"
    invoice.amount_due_cents = 0
    org.billing_status = "active"
    db_session.commit()

    after = client.get("/billing/overview", headers=headers).json()["past_due"]
    assert after["is_past_due"] is False
    assert after["outstanding_invoice_count"] == 0


def test_an_account_with_nothing_yet_renders_as_empty_not_broken(client,
                                                                 db_session,
                                                                 no_stripe):
    """No agreement, no customer, no subscription, no invoices, no payments,
    and Stripe not configured. Still a 200 with a describable shape."""
    org = _org(db_session, "Restland", plan=None)
    body = client.get("/billing/overview",
                      headers=_headers(db_session, _user(db_session, org))).json()

    assert body["agreement"] is None
    assert body["invoices"] == [] and body["payments"] == []
    assert body["subscription"]["has_subscription"] is False
    assert body["setup"]["status"] == "none"
    assert body["past_due"]["is_past_due"] is False


# ═════════════════════════════════════════════════════════════════════════════
# THE FRONTEND SOURCE ITSELF
#
# Standing in for a component test runner this project does not have. These
# are narrow on purpose: each one pins a rule that is invisible at runtime
# until it is already wrong in production.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_billing_page_names_no_payment_method():
    """WHICH METHODS ARE ELIGIBLE IS STRIPE'S ANSWER, per flow. A screen that
    names one promises something Stripe may refuse."""
    source = BILLING_PAGE.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    for method in ("payment_method_types", "afterpay", "klarna", "affirm",
                   "cashapp", "us_bank_account", "apple pay", "google pay"):
        assert method.lower() not in body.lower(), method


def test_the_billing_page_does_no_arithmetic_on_money():
    """Every amount rendered is a backend-formatted string. A number computed
    twice is a number that eventually disagrees with itself."""
    source = BILLING_PAGE.read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    assert "/ 100" not in body and "/100" not in body
    assert "toFixed" not in body
    assert "Intl.NumberFormat" not in body
    # Arithmetic ON A MONEY VALUE. `value * 1000` in the date formatter is
    # Unix seconds to milliseconds and is not money, so the check is anchored
    # to the names money actually travels under rather than to the operators.
    money = r"(amount|cents|total|balance|price|fee|outstanding|refunded)"
    for op in (r"\*", r"/", r"\+", r"-"):
        pattern = r"%s[a-zA-Z_]*\s*%s\s*\d" % (money, op)
        found = re.findall(pattern, body, flags=re.I)
        assert not found, found
    # A `_cents` integer may be TESTED - `{x.amount_due_cents ? x.amount_due
    # : dash}` is correct and common - but must never be the whole expression,
    # because that renders 49900 to a customer expecting $499.00.
    raw = re.findall(r"\{\s*[A-Za-z_][\w.]*_cents\s*\}", body)
    assert not raw, raw


def test_the_billing_page_reads_only_the_workspace_scoped_endpoints():
    """No organization id is ever put in a billing URL from this screen."""
    source = BILLING_PAGE.read_text(encoding="utf-8")
    calls = re.findall(r"api\.(?:get|post)\('([^']+)'", source)
    assert set(calls) <= {"/billing/access", "/billing/overview",
                          "/billing/portal"}, calls
    assert "${" not in "".join(calls)


def test_the_nav_item_does_not_gate_billing_on_a_platform_capability():
    """It sat behind `platform_billing` - a PLATFORM capability resolved
    against the legacy tenant column - while opening the customer's own
    billing."""
    source = LAYOUT.read_text(encoding="utf-8")
    nav_line = [l for l in source.splitlines()
                if "to: '/billing'" in l]
    assert len(nav_line) == 1, nav_line
    assert "platform_billing" not in nav_line[0]
    assert "billingAuthority" in nav_line[0]
    assert "/billing/access" in source


def test_the_billing_route_does_not_require_an_admin_role():
    """`requireAdmin` locked out the bookkeeper case these capabilities were
    created for. Backend authorization is what refuses."""
    source = APP.read_text(encoding="utf-8")
    route = [l for l in source.splitlines()
             if 'path="/billing"' in l]
    assert len(route) == 1, route
    assert "requireAdmin" not in route[0]


def test_the_billing_stylesheet_introduces_no_raw_colour():
    """Every colour is a token from index.css, so the palette stays retunable
    from one file."""
    css = (FRONTEND / "pages" / "Billing.css").read_text(encoding="utf-8")
    assert re.findall(r"#[0-9a-fA-F]{3,8}", css) == []


def test_wide_tables_scroll_inside_their_own_container():
    """A billing table must not be what makes the page scroll sideways on a
    tablet."""
    css = (FRONTEND / "pages" / "Billing.css").read_text(encoding="utf-8")
    assert ".bill-table-wrap" in css and "overflow-x: auto" in css
    source = BILLING_PAGE.read_text(encoding="utf-8")
    assert source.count('className="bill-table-wrap"') == source.count("<table")
