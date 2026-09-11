"""THE ATTACK PASS FOR THE CUSTOMER-UX / BRANDING CORRECTIONS.

The three defect files assert that the fixes WORK. This one assumes somebody
is trying to break them, or that a future edit will quietly undo them.

    A brand whose own domain is an infrastructure host.
    A platform row whose support email is blank rather than absent.
    A plan configured with zero of something.
    An unauthenticated or wrong-tenant caller asking where to return to.
    A ticket whose organization was moved to another brand after it opened.
    A features list somebody re-populates with the old marketing text.

Every one of these has a wrong answer that looks completely normal.
"""

import itertools
import json

import pytest

from app.models.billing_models import BrandBillingPlan
from app.models.models import Organization, Platform, User
from app.models.support_models import TicketCategory
from app.services import (billing_catalog, stripe_return, support_branding,
                          support_tickets)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _platform(db, **kw):
    kw.setdefault("name", "Brand %d" % next(_SEQ))
    kw.setdefault("slug", "brand-%d" % next(_SEQ))
    kw.setdefault("is_active", True)
    p = Platform(**kw)
    db.add(p)
    db.commit()
    return p


def _org(db, platform):
    n = next(_SEQ)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       plan="starter", billing_plan_key="starter",
                       platform_id=(platform.id if platform else None),
                       is_active=True)
    db.add(org)
    db.commit()
    return org


def _user(db, org, role="advisor"):
    u = User(organization_id=org.id, email="u%d@example.com" % next(_SEQ),
             password_hash=hash_password("TestPass123!"), full_name="A Person",
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


class _Recorder(object):
    def __init__(self):
        self.sent = []

    def __call__(self, to_email, subject, body_html, attachments=None, org=None):
        self.sent.append({"to": to_email, "html": body_html,
                          "from_email": getattr(org, "from_email", None)})
        return {"success": True, "provider_message_id": "m", "error": None}


@pytest.fixture()
def mail(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", rec)
    return rec


# ═══════════════════════════════════════════════════════════════════════════
# STRIPE RETURN
# ═══════════════════════════════════════════════════════════════════════════

def test_a_brand_whose_own_domain_is_an_infrastructure_host_is_refused(
        db_session, monkeypatch):
    """SOMEBODY PASTES THE RENDER HOSTNAME INTO THE BRAND'S DOMAIN FIELD.

    The guard is on the DESTINATION, not on where the string came from — a
    configured value gets no free pass, because the customer sees the same
    hostname either way.
    """
    monkeypatch.setenv("APP_BASE_URL", "")
    brand = _platform(db_session, domain="advisorflow-frontend.onrender.com")
    org = _org(db_session, brand)
    with pytest.raises(stripe_return.ReturnTargetUnavailable):
        stripe_return.base_url_for_org(db_session, org)


def test_the_env_cannot_reintroduce_an_infrastructure_host(db_session,
                                                           monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://advisorflow-frontend.onrender.com")
    brand = _platform(db_session, domain=None)
    org = _org(db_session, brand)
    with pytest.raises(stripe_return.ReturnTargetUnavailable):
        stripe_return.base_url_for_org(db_session, org)


def test_return_targets_is_refused_without_a_session(client):
    """STALE SESSION. An expired or absent token gets no destination map."""
    resp = client.get("/billing/return-targets")
    assert resp.status_code in (401, 403)


def test_return_targets_cannot_be_pointed_at_another_tenant(client, db_session):
    """There is no parameter to point it with, and this is the assertion that
    fails if somebody ever adds one."""
    a = _platform(db_session, domain="app.evosyspro.live")
    b = _platform(db_session, domain="app.bookaboost.live")
    org_a = _org(db_session, a)
    org_b = _org(db_session, b)
    admin = _user(db_session, org_a, role="org_admin")
    headers = {"Authorization": "Bearer " + create_access_token(admin, db_session)}

    for query in ("", "?organization_id=%s" % org_b.id,
                  "?org_id=%s" % org_b.id, "?surface=https://evil.example"):
        resp = client.get("/billing/return-targets" + query, headers=headers)
        assert resp.status_code == 200
        for url in resp.json()["surfaces"].values():
            assert url.startswith("https://app.evosyspro.live")


def test_every_produced_url_is_absolute_and_on_one_origin(db_session):
    brand = _platform(db_session, domain="app.evosyspro.live")
    org = _org(db_session, brand)
    urls = list(stripe_return.checkout_targets(db_session, org,
                                               part="setup").values())
    urls.append(stripe_return.portal_return_url(db_session, org))
    urls.extend(stripe_return.describe(db_session, org)["surfaces"].values())
    for url in urls:
        assert url.startswith("https://app.evosyspro.live/")
        assert url.count("://") == 1
        assert "\n" not in url and "\r" not in url


# ═══════════════════════════════════════════════════════════════════════════
# SUPPORT BRANDING
# ═══════════════════════════════════════════════════════════════════════════

def test_a_blank_support_email_is_treated_as_absent_not_as_an_address(
        db_session, mail):
    """"" IS NOT A SENDER. An empty string in the column would otherwise sail
    past a truthiness check somewhere and become a From header."""
    brand = _platform(db_session, slug="blank-%d" % next(_SEQ),
                      support_email="   ")
    org = _org(db_session, brand)
    identity = support_branding.sending_identity_for_ticket(
        db_session, type("T", (), {"platform_id": brand.id,
                                   "organization_id": org.id})())
    assert not (identity.from_email or "").strip()

    support_tickets.create_ticket(
        db_session, org=org, user=_user(db_session, org),
        subject="x", body="y",
        category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT)
    assert mail.sent == []


def test_moving_an_org_to_another_brand_does_not_rewrite_an_open_ticket(
        db_session, mail):
    """THE TICKET IS THE RECORD OF WHICH PRODUCT THEY WERE INSIDE.

    An organization can be moved between brands. A ticket that was opened
    under EvoSys Pro keeps answering as EvoSys Pro, because its own
    `platform_id` is read first — the customer would otherwise get a reply
    from a company they have never dealt with, about a conversation they
    started somewhere else.
    """
    evosys = _platform(db_session, name="EvoSys Pro", slug="evosyspro-%d" % next(_SEQ),
                       support_email="support@evosyspro.live")
    bookaboost = _platform(db_session, name="BookaBoost",
                           slug="bookaboost-%d" % next(_SEQ),
                           support_email="support@bookaboost.live")
    org = _org(db_session, evosys)
    ticket = support_tickets.create_ticket(
        db_session, org=org, user=_user(db_session, org),
        subject="x", body="y",
        category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT)
    mail.sent.clear()

    org.platform_id = bookaboost.id
    db_session.commit()

    support_tickets.notify_ticket_update(db_session, ticket,
                                         headline="Update", body="Looking.")
    assert mail.sent
    for msg in mail.sent:
        assert msg["from_email"] == "support@evosyspro.live"
        assert "bookaboost" not in (msg["html"] or "").lower()


def test_an_internal_note_never_reaches_the_customer_email(db_session, mail):
    """UNCHANGED BEHAVIOUR, RE-ASSERTED. The branding fix touched the
    envelope; this proves it did not touch the audience boundary."""
    brand = _platform(db_session, slug="ev-%d" % next(_SEQ),
                      support_email="support@evosyspro.live")
    org = _org(db_session, brand)
    user = _user(db_session, org)
    ticket = support_tickets.create_ticket(
        db_session, org=org, user=user, subject="x", body="y",
        category=TicketCategory.TECHNICAL_PRODUCT_SUPPORT)
    mail.sent.clear()

    support_tickets.add_message(db_session, ticket, author_kind="agent",
                                user=user, body="INTERNAL: card declined twice",
                                is_internal=True)
    joined = " ".join(m["html"] for m in mail.sent)
    assert "INTERNAL" not in joined


# ═══════════════════════════════════════════════════════════════════════════
# PLAN CARDS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_zero_ceiling_is_not_advertised_as_a_plan_that_includes_nothing(
        db_session):
    """0 IS NOT A TIER ANYBODY SELLS. `plan_limits.limit_for` already refuses
    to enforce a non-positive ceiling; the card must not claim one either."""
    brand = _platform(db_session)
    plan = BrandBillingPlan(platform_id=brand.id, key="odd", name="Odd",
                            monthly_cents=1000, currency="usd",
                            is_purchasable=True, is_active=True,
                            max_users=0, max_leads=0,
                            sms_monthly_allowance=0)
    db_session.add(plan)
    db_session.commit()
    assert billing_catalog.capacity_for(plan) == []


def test_a_malformed_features_blob_is_a_display_gap_not_a_crash(db_session):
    brand = _platform(db_session)
    plan = BrandBillingPlan(platform_id=brand.id, key="broken", name="Broken",
                            monthly_cents=1000, currency="usd",
                            is_purchasable=True, is_active=True,
                            max_users=1, features_json="{not json")
    db_session.add(plan)
    db_session.commit()
    assert billing_catalog.features_for(plan) == []
    assert billing_catalog.capacity_for(plan)


def test_reintroducing_the_old_marketing_text_is_visible_in_the_payload(
        client, db_session):
    """THE REGRESSION CANARY.

    Nothing stops an operator typing "24-month price lock" back into a
    brand's features list — it is their field. What this asserts is that the
    SEED no longer ships it and the platform does not generate it: the string
    can only appear if a person put it there deliberately, which is a
    decision rather than a stale default.
    """
    from app.services import evosys_billing_seed
    source = json.dumps(evosys_billing_seed.EVOSYS_PLANS)
    for stale in ("24-month", "AI voice", "price lock", "Up to 2 users"):
        assert stale not in source

    brand = _platform(db_session, domain="app.evosyspro.live")
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    org = _org(db_session, brand)
    admin = _user(db_session, org, role="org_admin")
    payload = client.get(
        "/billing/plans",
        headers={"Authorization": "Bearer " + create_access_token(admin, db_session)}
    ).json()
    blob = json.dumps(payload).lower()
    for stale in ("24-month", "ai voice", "price lock", "unlimited leads"):
        assert stale not in blob


def test_the_custom_tier_is_named_custom_and_is_never_self_serve(
        client, db_session):
    from app.services import evosys_billing_seed
    brand = _platform(db_session, domain="app.evosyspro.live")
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    org = _org(db_session, brand)
    admin = _user(db_session, org, role="org_admin")
    headers = {"Authorization": "Bearer " + create_access_token(admin, db_session)}

    plans = client.get("/billing/plans", headers=headers).json()["plans"]
    names = {p["name"] for p in plans}
    assert "Custom" in names
    assert "Enterprise" not in names
    custom = [p for p in plans if p["name"] == "Custom"][0]
    assert custom["is_purchasable"] is False

    # And the server refuses to sell it, whatever the screen draws.
    resp = client.post("/billing/checkout", headers=headers,
                       json={"plan": custom["key"], "interval": "month",
                             "commitment": "month_to_month"})
    assert resp.status_code == 400
