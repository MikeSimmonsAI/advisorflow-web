"""P4 — the billing HTTP surface: who may call what, and what a failure looks like.

WHY THIS FILE EXISTS SEPARATELY FROM test_billing_operations.py

That file proves the SERVICES are tenant-safe. This one proves the ROUTES are
wired to them - which is a different thing that can be wrong on its own. A
perfectly safe service reached through an endpoint that forgot its dependency
is an open door, and only a request through the app finds that.

Every test here goes through real HTTP with a real token. The only override is
the database session, so authentication, the capability dependencies and the
error translation all run as they do in production.

THE SHAPE OF EVERY REFUSAL

  403  you may not do this to this organization's billing
  400  the request is not valid for this organization's data - and notably,
       this is what another tenant's real id gets, identical to an invented
       one, because a different answer would make the endpoint an oracle
  503  Stripe could not be reached, or a live key was refused
  402  Stripe declined the operation itself
"""

import json
from datetime import datetime
from decimal import Decimal

import pytest

from app.models.billing_models import Invoice
from app.models.implementation_models import Implementation
from app.models.models import (Organization, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.services import billing_agreement as agreements
from app.services import billing_operations as ops
from app.services import merchant_entity as entity_svc
from app.services import stripe_gateway as gw
from app.services.auth_service import create_access_token, hash_password
from app.services.billing_access import BILLING_MANAGE, BILLING_VIEW

READS = ["/billing/overview", "/billing/invoices", "/billing/payments",
         "/billing/agreement"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _brand(db, slug="evosyspro"):
    existing = db.query(Platform).filter(Platform.slug == slug).first()
    if existing:
        return existing
    p = Platform(name="EvoSys Pro", slug=slug)
    db.add(p)
    db.commit()
    return p


def _org(db, name, platform=None, stripe_customer_id=None):
    o = Organization(name=name, slug=name.lower().replace(" ", "-"),
                     plan="standard",
                     platform_id=platform.id if platform else None,
                     stripe_customer_id=stripe_customer_id)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin", email=None):
    u = User(organization_id=org.id if org else None,
             email=email or ("%s.%s@example.com" % (role, (org.slug if org else "none"))),
             password_hash=hash_password("TestPass123!"),
             full_name="Billing Person", role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _grant(db, user, org, key):
    """Both gates, the way production sets them."""
    existing = json.loads(org.delegated_capabilities or "[]")
    if key not in existing:
        existing.append(key)
    org.delegated_capabilities = json.dumps(existing)
    db.add(UserCapabilityGrant(user_id=user.id, organization_id=org.id,
                               capability=key, is_active=True))
    db.commit()


def _agreement(db, org, platform, recurring="499.00"):
    entity_svc.ensure_evosys_pro_configuration(db)
    so = (db.query(BrandSalesOrg)
          .filter(BrandSalesOrg.platform_id == platform.id).first())
    if so is None:
        so = BrandSalesOrg(platform_id=platform.id, name="EvoSys Sales",
                           slug="evosys-sales")
        db.add(so)
        db.commit()
    opp = Opportunity(company_name=org.name, status="won",
                      brand_sales_org_id=so.id)
    db.add(opp)
    db.commit()
    impl = Implementation(organization_id=org.id, platform_id=platform.id,
                          opportunity_id=opp.id, billing_option="term_agreement",
                          contract_term_months=13,
                          implementation_fee=Decimal("1500.00"),
                          recurring_amount=Decimal(recurring), currency="USD",
                          billing_start_date=datetime(2026, 10, 1))
    db.add(impl)
    db.commit()
    return agreements.create_from_implementation(db, impl)


def _invoice(db, org, stripe_id="in_1", status="open"):
    row = Invoice(organization_id=org.id, stripe_invoice_id=stripe_id,
                  stripe_customer_id=org.stripe_customer_id, number="INV-1",
                  status=status, currency="USD", total_cents=49900,
                  amount_due_cents=49900, amount_paid_cents=0)
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def no_stripe(monkeypatch):
    """Stripe deliberately unreachable.

    Most tests here are about AUTHORIZATION, and authorization must be decided
    before anything is attempted. With Stripe unavailable, a mutation that
    reaches the service returns 503 - so a 403 in these tests proves the
    refusal happened at the gate, and a 503 proves the caller got through it.
    """
    def _unavailable():
        raise gw.StripeUnavailable("no key in this test")
    monkeypatch.setattr(gw, "client", _unavailable)


# ═════════════════════════════════════════════════════════════════════════════
# The baseline: an org admin keeps working
# ═════════════════════════════════════════════════════════════════════════════

def test_an_org_admin_can_read_every_billing_screen(client, db_session):
    """NO LOCKOUT. P3 re-gated these routes; an admin who could open billing
    before must still be able to."""
    org = _org(db_session, "Restland")
    admin = _user(db_session, org)
    headers = _headers(db_session, admin)

    for path in READS:
        assert client.get(path, headers=headers).status_code == 200, path


def test_the_overview_returns_this_organization_and_its_permissions(
        client, db_session):
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org))

    body = client.get("/billing/overview", headers=headers).json()

    assert body["organization"]["id"] == org.id
    assert body["permissions"] == {"can_view": True, "can_manage": True}


# ═════════════════════════════════════════════════════════════════════════════
# AUTHORIZATION
# ═════════════════════════════════════════════════════════════════════════════

def test_an_advisor_cannot_read_billing_at_all(client, db_session):
    org = _org(db_session, "Restland")
    headers = _headers(db_session, _user(db_session, org, "advisor"))

    for path in READS:
        assert client.get(path, headers=headers).status_code == 403, path


def test_an_advisor_cannot_mutate_billing(client, db_session, no_stripe):
    org = _org(db_session, "Restland")
    agreement_id = "agr_anything"
    headers = _headers(db_session, _user(db_session, org, "advisor"))

    posts = [
        ("/billing/invoices", {"line_items": [{"amount_cents": 1000}]}),
        ("/billing/invoices/in_1/finalize", None),
        ("/billing/invoices/in_1/send", None),
        ("/billing/invoices/in_1/void", None),
        ("/billing/agreements/%s/subscribe" % agreement_id, None),
        ("/billing/agreements/%s/cancel" % agreement_id, {}),
    ]
    for path, body in posts:
        r = client.post(path, headers=headers, json=body)
        assert r.status_code == 403, (path, r.status_code)


def test_billing_view_can_read(client, db_session):
    """The bookkeeper case: not an admin, and still able to see the books."""
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor", email="book@example.com")
    _grant(db_session, person, org, BILLING_VIEW)
    headers = _headers(db_session, person)

    for path in READS:
        assert client.get(path, headers=headers).status_code == 200, path


def test_billing_view_cannot_mutate(client, db_session, no_stripe):
    """THE POINT OF SPLITTING THE CAPABILITY. Reading the books is not
    permission to move money."""
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    person = _user(db_session, org, "advisor", email="book@example.com")
    _grant(db_session, person, org, BILLING_VIEW)
    invoice = _invoice(db_session, org)
    headers = _headers(db_session, person)

    for path in ("/billing/invoices/%s/finalize" % invoice.id,
                 "/billing/invoices/%s/void" % invoice.id):
        assert client.post(path, headers=headers).status_code == 403, path

    r = client.post("/billing/invoices", headers=headers,
                    json={"line_items": [{"amount_cents": 1000}]})
    assert r.status_code == 403

    db_session.refresh(invoice)
    assert invoice.status == "open"


def test_billing_manage_can_mutate(client, db_session, no_stripe):
    """Gets THROUGH the gate. 503 is Stripe being unreachable in this test,
    which is exactly the proof wanted: the refusal was not an authorization
    one."""
    brand = _brand(db_session)
    org = _org(db_session, "Restland", platform=brand,
               stripe_customer_id="cus_x")
    person = _user(db_session, org, "advisor", email="mgr@example.com")
    _grant(db_session, person, org, BILLING_MANAGE)
    invoice = _invoice(db_session, org)
    headers = _headers(db_session, person)

    r = client.post("/billing/invoices/%s/finalize" % invoice.id,
                    headers=headers)
    assert r.status_code == 503


def test_billing_manage_implies_view(client, db_session):
    org = _org(db_session, "Restland")
    person = _user(db_session, org, "advisor", email="mgr@example.com")
    _grant(db_session, person, org, BILLING_MANAGE)
    headers = _headers(db_session, person)

    assert client.get("/billing/overview", headers=headers).status_code == 200


def test_billing_needs_a_token(client, db_session):
    for path in READS:
        assert client.get(path).status_code in (401, 403), path


# ═════════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION THROUGH THE HTTP SURFACE
# ═════════════════════════════════════════════════════════════════════════════

def test_no_billing_endpoint_accepts_an_organization_id(client, db_session):
    """STRUCTURAL. The subject is the active workspace, so there is nowhere to
    put another tenant's id. This is asserted against the route table rather
    than trusted, because an org_id path parameter added later would reopen
    exactly the hole P3 closed."""
    from app.routers.billing_router import router

    p4 = [r for r in router.routes
          if r.path.startswith("/billing/")
          and any(seg in r.path for seg in
                  ("overview", "invoices", "payments", "agreement"))]
    assert p4, "P4 routes not registered"
    for route in p4:
        assert "{org_id}" not in route.path
        assert "{organization_id}" not in route.path


def test_another_tenants_invoice_id_is_refused_over_http(client, db_session,
                                                         no_stripe):
    """NEGATIVE TENANT TEST. The id is real and belongs to somebody else."""
    mine = _org(db_session, "Restland")
    theirs = _org(db_session, "Hillcrest", stripe_customer_id="cus_theirs")
    their_invoice = _invoice(db_session, theirs, stripe_id="in_theirs")
    headers = _headers(db_session, _user(db_session, mine))

    for path in ("/billing/invoices/%s" % their_invoice.id,
                 "/billing/invoices/in_theirs"):
        assert client.get(path, headers=headers).status_code == 400, path

    for path in ("/billing/invoices/%s/finalize" % their_invoice.id,
                 "/billing/invoices/%s/void" % their_invoice.id):
        assert client.post(path, headers=headers).status_code == 400, path

    db_session.refresh(their_invoice)
    assert their_invoice.status == "open"


def test_a_guessed_stripe_id_gets_the_same_answer_as_a_real_foreign_one(
        client, db_session, no_stripe):
    """No enumeration oracle: 'not yours' and 'does not exist' must be
    indistinguishable, body and all."""
    mine = _org(db_session, "Restland")
    theirs = _org(db_session, "Hillcrest", stripe_customer_id="cus_theirs")
    _invoice(db_session, theirs, stripe_id="in_theirs")
    headers = _headers(db_session, _user(db_session, mine))

    foreign = client.get("/billing/invoices/in_theirs", headers=headers)
    invented = client.get("/billing/invoices/in_never_existed", headers=headers)

    assert foreign.status_code == invented.status_code == 400
    assert foreign.json() == invented.json()


def test_another_tenants_agreement_cannot_be_subscribed_over_http(
        client, db_session, no_stripe):
    brand = _brand(db_session)
    mine = _org(db_session, "Restland", platform=brand)
    theirs = _org(db_session, "Hillcrest", platform=brand)
    their_agreement = _agreement(db_session, theirs, brand)
    headers = _headers(db_session, _user(db_session, mine))

    r = client.post("/billing/agreements/%s/subscribe" % their_agreement.id,
                    headers=headers)

    assert r.status_code == 400
    db_session.refresh(their_agreement)
    assert their_agreement.stripe_subscription_id is None


def test_one_organizations_invoices_never_appear_in_anothers_list(
        client, db_session):
    mine = _org(db_session, "Restland")
    theirs = _org(db_session, "Hillcrest", stripe_customer_id="cus_theirs")
    _invoice(db_session, theirs, stripe_id="in_theirs")
    _invoice(db_session, mine, stripe_id="in_mine")
    headers = _headers(db_session, _user(db_session, mine))

    body = client.get("/billing/invoices", headers=headers).json()

    assert [i["stripe_invoice_id"] for i in body["invoices"]] == ["in_mine"]


# ═════════════════════════════════════════════════════════════════════════════
# REQUEST VALIDATION AND FAILURE TRANSLATION
# ═════════════════════════════════════════════════════════════════════════════

def test_a_float_amount_is_rejected_by_the_request_schema(client, db_session,
                                                          no_stripe):
    """The type is the guard, and it is declared at the edge so a rounding
    error cannot even be described in a request body."""
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/invoices", headers=headers,
                    json={"line_items": [{"amount_cents": 499.55}]})

    assert r.status_code == 422


def test_an_invoice_with_no_line_items_is_a_400_not_a_500(client, db_session,
                                                          no_stripe):
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/invoices", headers=headers,
                    json={"line_items": []})

    assert r.status_code == 400


def test_stripe_being_unreachable_is_503_and_says_so(client, db_session,
                                                     no_stripe):
    """Retryable. A 500 would tell an advisor nothing and a 400 would tell them
    something false."""
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org)
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/invoices/%s/finalize" % invoice.id,
                    headers=headers)

    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"].lower()


def test_a_live_stripe_key_is_refused_at_the_http_surface(client, db_session,
                                                          monkeypatch):
    """SANDBOX ONLY, ENFORCED END TO END. A live key deployed by accident
    refuses the request rather than moving real money."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_realmoney")
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org)
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/invoices/%s/void" % invoice.id, headers=headers)

    assert r.status_code == 503
    assert "sandbox" in r.json()["detail"].lower()


def test_a_stripe_refusal_is_402_not_500(client, db_session, monkeypatch):
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org)
    headers = _headers(db_session, _user(db_session, org))

    class _Invoice:
        @staticmethod
        def finalize_invoice(*a, **k):
            raise ValueError("invoice_no_customer_line_items")

    monkeypatch.setattr(gw, "client",
                        lambda: type("S", (), {"Invoice": _Invoice})())
    monkeypatch.setattr(gw, "_stripe",
                        lambda: type("S", (), {"Invoice": _Invoice})())

    r = client.post("/billing/invoices/%s/finalize" % invoice.id,
                    headers=headers)

    assert r.status_code == 402


def test_a_paid_invoice_cannot_be_voided_over_http(client, db_session,
                                                   no_stripe):
    org = _org(db_session, "Restland", stripe_customer_id="cus_x")
    invoice = _invoice(db_session, org, status="paid")
    headers = _headers(db_session, _user(db_session, org))

    r = client.post("/billing/invoices/%s/void" % invoice.id, headers=headers)

    assert r.status_code == 400
    assert "refund" in r.json()["detail"].lower()
