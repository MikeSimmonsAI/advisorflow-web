"""Phase 7.3 closeout - the public Wholesale contact comes from configuration.

THE RULE (not "phone numbers don't matter"):

  * The Investor Deal Room and the Seller Portal show the public phone and email
    configured in THIS organization's Wholesale settings
    (`public_contact_phone` / `public_contact_email`) - and nothing else.
  * No fallback to the platform (brand) support line or address, and never to
    another organization. Missing configuration shows NO public contact.
  * For the EvoSysPro-owned Wholesale configuration the approved public phone is
    469-553-7417, set through that configuration path (the review seed does it
    for the canonical review org) - never hardcoded in a page or payload.
"""
from types import SimpleNamespace

import pytest

from app.models.models import Organization, Platform
from app.models.wholesale_models import WholesaleSettings
from app.services import wholesale_publication

APPROVED_PHONE = "469-553-7417"
PLATFORM_SUPPORT_EMAIL = "support@evosyspro.live"


def ok(response):
    assert response.status_code < 400, (response.status_code, response.text)
    return response.json()


@pytest.fixture()
def evosyspro(db_session):
    platform = Platform(name="EvoSys Pro", slug="evosyspro")
    db_session.add(platform)
    db_session.flush()
    return platform


def _org(db_session, platform, name):
    org = Organization(name=name, slug=name.lower().replace(" ", "-"), plan="standard",
                       industry="real_estate", platform_id=platform.id)
    db_session.add(org)
    db_session.flush()
    return org


def _brand(db_session, org):
    return wholesale_publication.branding(db_session, SimpleNamespace(organization_id=org.id))


def test_configured_organization_resolves_its_own_public_contact(db_session, evosyspro):
    a = _org(db_session, evosyspro, "Wholesale A")
    db_session.add(WholesaleSettings(organization_id=a.id, public_contact_phone=APPROVED_PHONE,
                                     public_contact_email="wholesale@a.example"))
    db_session.commit()
    brand = _brand(db_session, a)
    assert brand["support_phone"] == APPROVED_PHONE
    assert brand["support_email"] == "wholesale@a.example"
    assert brand["contact_source"] == "wholesale_settings"


def test_missing_configuration_shows_no_contact_and_never_the_platform_s(db_session, evosyspro):
    """Same platform, nothing configured: no phone, no email - not EvoSysPro's."""
    b = _org(db_session, evosyspro, "Wholesale B")
    db_session.commit()
    brand = _brand(db_session, b)
    assert brand["support_phone"] is None
    assert brand["support_email"] is None
    assert brand["contact_source"] is None
    assert APPROVED_PHONE not in repr(brand)
    assert PLATFORM_SUPPORT_EMAIL not in repr(brand)


def test_no_cross_tenant_public_contact_leakage(db_session, evosyspro):
    """Customer B never inherits Customer A's contact, even on the same brand."""
    a = _org(db_session, evosyspro, "Wholesale A")
    b = _org(db_session, evosyspro, "Wholesale B")
    db_session.add(WholesaleSettings(organization_id=a.id, public_contact_phone=APPROVED_PHONE,
                                     public_contact_email="wholesale@a.example"))
    db_session.add(WholesaleSettings(organization_id=b.id))       # row exists, blank
    db_session.commit()
    brand_b = _brand(db_session, b)
    assert brand_b["support_phone"] is None and brand_b["support_email"] is None
    assert "a.example" not in repr(brand_b) and APPROVED_PHONE not in repr(brand_b)


def test_phone_only_does_not_pull_in_the_platform_support_email(db_session, evosyspro):
    a = _org(db_session, evosyspro, "Wholesale A")
    db_session.add(WholesaleSettings(organization_id=a.id, public_contact_phone=APPROVED_PHONE))
    db_session.commit()
    brand = _brand(db_session, a)
    assert brand["support_phone"] == APPROVED_PHONE
    assert brand["support_email"] is None


def test_a_public_page_read_never_creates_a_settings_row(db_session, evosyspro):
    b = _org(db_session, evosyspro, "Wholesale B")
    db_session.commit()
    _brand(db_session, b)
    assert db_session.query(WholesaleSettings).filter_by(organization_id=b.id).count() == 0


def test_settings_api_sets_validates_and_clears_the_public_contact(client, auth_headers):
    got = ok(client.patch("/wholesale/settings", headers=auth_headers,
                          json={"public_contact_phone": APPROVED_PHONE,
                                "public_contact_email": "Wholesale@Example.com"}))
    assert got["public_contact_phone"] == APPROVED_PHONE
    assert got["public_contact_email"] == "wholesale@example.com"
    assert ok(client.get("/wholesale/settings", headers=auth_headers))["public_contact_phone"] == APPROVED_PHONE

    assert client.patch("/wholesale/settings", headers=auth_headers,
                        json={"public_contact_email": "not-an-email"}).status_code == 400
    assert client.patch("/wholesale/settings", headers=auth_headers,
                        json={"public_contact_phone": "call me"}).status_code == 400

    cleared = ok(client.patch("/wholesale/settings", headers=auth_headers,
                              json={"public_contact_phone": "  ", "public_contact_email": ""}))
    assert cleared["public_contact_phone"] is None and cleared["public_contact_email"] is None


def test_seller_portal_carries_the_configured_contact_end_to_end(client, auth_headers):
    """Configured in settings -> published seller page shows it. Nothing else."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers, json={
        "street_address": "100 Contact Test Ln", "city": "Dallas", "state": "TX",
        "zip_code": "75201", "is_test": True}))
    deal_id = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/publication/state" % deal_id, headers=auth_headers,
                   json={"audience": "seller", "published": True}))
    link = ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=auth_headers,
                          json={"audience": "seller"}))

    before = ok(client.get("/wholesale-rooms/seller/%s" % link["token"]))["brand"]
    assert before["support_phone"] is None and before["contact_source"] is None

    ok(client.patch("/wholesale/settings", headers=auth_headers,
                    json={"public_contact_phone": APPROVED_PHONE}))
    after = ok(client.get("/wholesale-rooms/seller/%s" % link["token"]))["brand"]
    assert after["support_phone"] == APPROVED_PHONE
    assert after["contact_source"] == "wholesale_settings"
    assert after["support_email"] is None
