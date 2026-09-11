"""DEFECT 1 — EVERY STRIPE FLOW HAS A SERVER-CONTROLLED WAY BACK.

WHAT WAS WRONG
--------------
Four places built their own return URL and all four ended the same way:

    public_identity.public_base_url(org)             the brand's own domain
    os.environ["APP_BASE_URL"]                       one value, three brands
    "https://advisorflow-frontend.onrender.com"      OURS, and nobody's brand

A funeral home that had just paid EvoSys Pro was returned to an AdvisorFlow
Render hostname. Meanwhile Stripe's hosted invoice/receipt page has no return
hook at all, so a customer who followed a receipt link had nothing to click.

WHAT THESE TESTS DEFEND
-----------------------
  1. The destination is built SERVER-SIDE, from the caller's own brand.
  2. The path comes from an ALLOWLIST — a hostile "surface" is not followed,
     not sanitised, not concatenated. It resolves to billing.
  3. An infrastructure host is never a destination, from any source.
  4. One tenant's return URL never carries another tenant's host.
  5. Unresolved is a REFUSAL, not a guess.
  6. What Stripe does not support is stated honestly rather than faked.

NOTHING HERE REACHES STRIPE.
"""

import itertools
from unittest.mock import MagicMock

import pytest
import stripe

from app.models.billing_models import BrandBillingPlan
from app.models.models import Organization, Platform, User
from app.services import stripe_return
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_FAKE_not_a_real_key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_FAKE_not_a_real_secret")


@pytest.fixture(autouse=True)
def no_real_stripe_calls(monkeypatch):
    def _refuse(*args, **kwargs):
        raise RuntimeError("A test tried to make a real Stripe API request.")
    monkeypatch.setattr(stripe.checkout.Session, "create", _refuse)
    monkeypatch.setattr(stripe.billing_portal.Session, "create", _refuse)
    monkeypatch.setattr(stripe.Customer, "create", _refuse)


def _platform(db, name, slug, domain=None):
    p = Platform(name=name, slug="%s-%d" % (slug, next(_SEQ)), domain=domain,
                 is_active=True)
    db.add(p)
    db.commit()
    return p


def _org(db, platform, **kw):
    n = next(_SEQ)
    kw.setdefault("plan", "trial")
    kw.setdefault("stripe_customer_id", "cus_%d" % n)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=platform.id, is_active=True, **kw)
    db.add(org)
    db.commit()
    return org


def _admin_headers(db, org):
    admin = User(organization_id=org.id, email="admin%d@example.com" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role="org_admin",
                 must_change_password=False)
    db.add(admin)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE ALLOWLIST. A SURFACE IS A KEY, NEVER A URL.
# ═══════════════════════════════════════════════════════════════════════════

HOSTILE_SURFACES = [
    "https://evil.example/steal",
    "//evil.example",
    "http://evil.example",
    "/../../wire-transfer",
    "javascript:alert(1)",
    "billing@evil.example",
    "billing evil",
    "BILLING\nLocation: https://evil.example",
    "%2f%2fevil.example",
    "data:text/html,<script>",
    "",
    None,
    "x" * 4000,
]


@pytest.mark.parametrize("hostile", HOSTILE_SURFACES)
def test_a_hostile_return_surface_resolves_to_billing(hostile):
    """THE OPEN-REDIRECT DEFENCE. The value is a dictionary key, so anything
    that is not one of four known words is simply not one of them."""
    assert stripe_return.resolve_surface(hostile) == "billing"


def test_only_the_four_known_surfaces_are_reachable():
    assert set(stripe_return.SAFE_SURFACES) == {
        "billing", "account", "support", "home"}
    for key in stripe_return.SAFE_SURFACES:
        assert stripe_return.resolve_surface(key) == key


# ═══════════════════════════════════════════════════════════════════════════
# 2. WHAT COUNTS AS A USABLE BASE
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("bad", [
    None, "", "   ",
    "advisorflow-frontend.onrender.com",              # no scheme
    "https://advisorflow-frontend.onrender.com",      # infrastructure
    "https://advisorflow-booking.vercel.app",
    "https://something.railway.app",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "//app.evosyspro.live",                           # scheme-relative
    "javascript:alert(1)",
    "https://app.evosyspro.live@evil.example",        # userinfo
    "https://app.evosyspro.live?next=https://evil",   # carries a query
    "https://app.evosyspro.live#frag",
    "https://app.evosyspro.live/../admin",
    "https://app.evosyspro.live\nX-Injected: 1",
])
def test_an_unusable_base_is_rejected_rather_than_repaired(bad):
    assert stripe_return._clean_base(bad) is None


@pytest.mark.parametrize("good,expected", [
    ("https://app.evosyspro.live", "https://app.evosyspro.live"),
    ("https://app.evosyspro.live/", "https://app.evosyspro.live"),
    ("https://app.bookaboost.live/app/", "https://app.bookaboost.live/app"),
])
def test_a_branded_origin_is_accepted(good, expected):
    assert stripe_return._clean_base(good) == expected


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE DESTINATION IS THE CALLER'S OWN BRAND
# ═══════════════════════════════════════════════════════════════════════════

def test_checkout_targets_use_the_brands_own_domain(db_session):
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)

    targets = stripe_return.checkout_targets(db_session, org,
                                             part="subscription")
    assert targets["success_url"].startswith("https://app.evosyspro.live/billing?")
    assert "success=1" in targets["success_url"]
    assert "part=subscription" in targets["success_url"]
    assert targets["cancel_url"].startswith("https://app.evosyspro.live/billing?")
    assert "canceled=1" in targets["cancel_url"]
    # THE CANCEL CARRIES `part` TOO. It used not to, so a customer who backed
    # out of a setup-fee checkout landed on a page that could not say what
    # they had backed out of.
    assert "part=subscription" in targets["cancel_url"]


def test_no_return_url_anywhere_names_an_infrastructure_host(db_session, monkeypatch):
    """Even with the environment set to the old literal."""
    monkeypatch.setenv("APP_BASE_URL", "https://advisorflow-frontend.onrender.com")
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)

    urls = list(stripe_return.checkout_targets(db_session, org).values())
    urls.append(stripe_return.portal_return_url(db_session, org))
    urls.extend(stripe_return.describe(db_session, org)["surfaces"].values())

    for url in urls:
        assert "onrender.com" not in url
        assert "vercel.app" not in url
        assert url.startswith("https://app.evosyspro.live")


def test_one_brands_customer_never_returns_to_another_brands_host(db_session):
    """WRONG TENANT. Two brands, two customers, two hosts, no crossing."""
    evosys = _platform(db_session, "EvoSys Pro", "evosyspro",
                       domain="app.evosyspro.live")
    bookaboost = _platform(db_session, "BookaBoost", "bookaboost",
                           domain="app.bookaboost.live")
    org_e = _org(db_session, evosys)
    org_b = _org(db_session, bookaboost)

    e = stripe_return.checkout_targets(db_session, org_e)["success_url"]
    b = stripe_return.checkout_targets(db_session, org_b)["success_url"]

    assert "app.evosyspro.live" in e and "bookaboost" not in e
    assert "app.bookaboost.live" in b and "evosyspro" not in b


def test_the_portal_return_marks_the_round_trip(db_session):
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)
    url = stripe_return.portal_return_url(db_session, org)
    assert url == "https://app.evosyspro.live/billing?returned=portal"


def test_an_unknown_surface_on_a_real_org_still_lands_on_billing(db_session):
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)
    url = stripe_return.url_for(db_session, org, "https://evil.example")
    assert url == "https://app.evosyspro.live/billing"


def test_query_values_are_encoded_not_concatenated(db_session):
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)
    url = stripe_return.url_for(db_session, org, "billing",
                                note="a b&c=d#e")
    assert "note=a%20b%26c%3Dd%23e" in url
    assert url.count("?") == 1


def test_an_unknown_part_is_dropped_rather_than_echoed(db_session):
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)
    targets = stripe_return.checkout_targets(
        db_session, org, part="<script>alert(1)</script>")
    assert "script" not in targets["success_url"]
    assert "part=" not in targets["success_url"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. UNRESOLVED IS A REFUSAL
# ═══════════════════════════════════════════════════════════════════════════

def test_a_brand_with_no_domain_and_no_env_refuses(db_session, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "")
    brand = _platform(db_session, "Unconfigured", "unconfigured", domain=None)
    org = _org(db_session, brand)
    with pytest.raises(stripe_return.ReturnTargetUnavailable):
        stripe_return.base_url_for_org(db_session, org)


def test_an_org_with_no_brand_refuses(db_session, monkeypatch):
    """No organization at all is the same answer, not a crash."""
    monkeypatch.setenv("APP_BASE_URL", "")
    with pytest.raises(stripe_return.ReturnTargetUnavailable):
        stripe_return.base_url_for_org(db_session, None)


def test_describe_reports_the_refusal_instead_of_a_plausible_url(
        db_session, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "")
    brand = _platform(db_session, "Unconfigured", "unconfigured", domain=None)
    org = _org(db_session, brand)
    out = stripe_return.describe(db_session, org)
    assert out["resolved"] is False
    assert out["surfaces"] == {}
    assert "domain" in out["detail"]


def test_the_portal_route_refuses_rather_than_bouncing_to_render(
        client, db_session, monkeypatch):
    """END TO END, at the route. A 409 with a sentence an operator can act on
    beats a customer landing on a hostname they have never seen."""
    monkeypatch.setenv("APP_BASE_URL", "")
    brand = _platform(db_session, "Unconfigured", "unconfigured", domain=None)
    org = _org(db_session, brand, stripe_customer_id="cus_unconfigured")
    headers = _admin_headers(db_session, org)

    resp = client.post("/billing/portal", headers=headers)
    assert resp.status_code == 409
    assert "domain" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. THE ROUTES THE APP'S OWN "WAY BACK" IS BUILT FROM
# ═══════════════════════════════════════════════════════════════════════════

def test_return_targets_endpoint_is_tenant_scoped(client, db_session):
    evosys = _platform(db_session, "EvoSys Pro", "evosyspro",
                       domain="app.evosyspro.live")
    bookaboost = _platform(db_session, "BookaBoost", "bookaboost",
                           domain="app.bookaboost.live")
    org_e = _org(db_session, evosys)
    _org(db_session, bookaboost)

    resp = client.get("/billing/return-targets",
                      headers=_admin_headers(db_session, org_e))
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is True
    for url in body["surfaces"].values():
        assert url.startswith("https://app.evosyspro.live")
    assert set(body["surfaces"]) == {"billing", "account", "support", "home"}


def test_return_targets_requires_an_admin(client, db_session, sample_advisor):
    resp = client.get(
        "/billing/return-targets",
        headers={"Authorization": "Bearer "
                 + create_access_token(sample_advisor, db_session)})
    assert resp.status_code == 403


def test_stripe_hosted_page_limitation_is_stated_not_faked(db_session):
    """WHERE STRIPE DOES NOT PERMIT A RETURN, WE SAY SO.

    Verified against Stripe's documentation: the hosted invoice page is
    customisable in brand colour, logo, icon and public business information,
    and exposes NO application-controlled return URL. This assertion is the
    thing that fails if somebody later writes a reassurance we cannot keep.
    """
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    org = _org(db_session, brand)
    limits = stripe_return.describe(db_session, org)["hosted_page_limitations"]
    assert limits, "the limitation must be reported, not omitted"
    invoice = [l for l in limits if l["surface"] == "stripe_hosted_invoice"][0]
    assert "return url" in invoice["not_supported"].lower()
    assert invoice["mitigation"]


# ═══════════════════════════════════════════════════════════════════════════
# 6. THE CHECKOUT PATHS ACTUALLY USE IT
# ═══════════════════════════════════════════════════════════════════════════

def test_subscription_checkout_hands_stripe_the_branded_urls(
        client, db_session, monkeypatch):
    brand = _platform(db_session, "EvoSys Pro", "evosyspro",
                      domain="app.evosyspro.live")
    db_session.add(BrandBillingPlan(
        platform_id=brand.id, key="starter", name="Starter",
        monthly_cents=50000, month_to_month_cents=59700, currency="usd",
        is_purchasable=True, is_active=True, sort_order=10))
    db_session.commit()
    org = _org(db_session, brand, stripe_customer_id="cus_ok")
    headers = _admin_headers(db_session, org)

    captured = {}

    def _create(**kwargs):
        captured.update(kwargs)
        session = MagicMock()
        session.url = "https://checkout.stripe.com/c/pay/cs_test_fake"
        session.id = "cs_test_fake"
        return session

    monkeypatch.setattr(stripe.checkout.Session, "create", _create)

    resp = client.post("/billing/checkout", headers=headers,
                       json={"plan": "starter", "interval": "month",
                             "commitment": "month_to_month"})
    assert resp.status_code == 200, resp.text
    assert captured["success_url"].startswith("https://app.evosyspro.live/billing?")
    assert captured["cancel_url"].startswith("https://app.evosyspro.live/billing?")
    assert "onrender" not in captured["success_url"]
    assert "onrender" not in captured["cancel_url"]
