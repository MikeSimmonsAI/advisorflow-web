"""The pages in `public-sites/` are live customer websites, so this file
treats them as code rather than as assets.

It publishes each one exactly as it sits in the repo, serves it, posts the
payload its own form would post, and asserts a lead arrives in the right
workspace. A prototype that was never rewired, a form pointing at the wrong
endpoint, or a page still carrying its demo password all fail here rather than
in front of a customer.
"""

import io
import pathlib
import re

import pytest

from app.models.models import Lead, Organization
from app.services import customer_sites as cs

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITES = sorted((ROOT / "public-sites").glob("*/index.html"))

# The markers that mean a page is still a prototype. A live customer site may
# carry none of them, and the list is the same one the publisher enforces.
PROTOTYPE_MARKERS = (
    "demo123", "demo mode", "sample data only", "no real authentication",
    "sample account data", "not live rates", "demo pricing",
)


def _slug_of(path):
    return path.parent.name


def _org(db_session, name, slug):
    org = Organization(name=name, slug=slug, plan="standard", industry="energy")
    db_session.add(org)
    db_session.commit()
    return org


def test_there_are_sites_to_check():
    """A green suite that checked nothing would be worse than a red one."""
    assert SITES, "public-sites/ holds no pages"


@pytest.mark.parametrize("path", SITES, ids=_slug_of)
def test_a_published_page_carries_no_prototype_text(path):
    html = io.open(path, encoding="utf-8").read().lower()
    for marker in PROTOTYPE_MARKERS:
        assert marker not in html, "%s still says %r" % (path.name, marker)


@pytest.mark.parametrize("path", SITES, ids=_slug_of)
def test_a_published_page_quotes_no_price_the_platform_cannot_prove(path):
    """A number on a live customer website is a representation that customer
    has to stand behind. Until a real provider feed is connected, the page
    asks for the enquiry instead of inventing the answer."""
    html = io.open(path, encoding="utf-8").read()
    assert not re.search(r"\d+(\.\d+)?\s*¢", html), "%s states a rate" % path.name
    assert not re.search(r"\$\d[\d,]*\s*/\s*mo", html), "%s states a price" % path.name


@pytest.mark.parametrize("path", SITES, ids=_slug_of)
def test_a_published_page_posts_to_its_own_inquiry_endpoint(path):
    html = io.open(path, encoding="utf-8").read()
    assert "/inquiry'" in html or '/inquiry"' in html, \
        "%s has no inquiry endpoint" % path.name
    # The slug is read from the address bar rather than written in, so a page
    # republished at a second address still files to that address's owner.
    assert "window.location.pathname" in html


@pytest.mark.parametrize("path", SITES, ids=_slug_of)
def test_a_published_page_carries_the_honeypot_the_server_reads(path):
    html = io.open(path, encoding="utf-8").read()
    assert 'name="website_url"' in html
    assert "form_started_at" in html


@pytest.mark.parametrize("path", SITES, ids=_slug_of)
def test_a_published_page_fits_and_publishes(db_session, path):
    slug = _slug_of(path)
    assert cs.slug_error(slug) is None, "%s is not a usable address" % slug
    html = io.open(path, encoding="utf-8").read()
    assert len(html.encode("utf-8")) < cs.MAX_HTML_BYTES
    org = _org(db_session, "Owner of %s" % slug, "owner-%s" % slug[:20])
    site = cs.publish(db_session, organization=org, slug=slug, html=html,
                      title=org.name)
    assert site.is_live()


@pytest.mark.parametrize("path", SITES, ids=_slug_of)
def test_the_page_serves_and_its_form_files_a_lead(db_session, client, path):
    """End to end, against the real routes, with the real markup."""
    slug = _slug_of(path)
    html = io.open(path, encoding="utf-8").read()
    org = _org(db_session, "Owner of %s" % slug, "e2e-%s" % slug[:20])
    other = _org(db_session, "Somebody Else", "e2e-other-%s" % slug[:12])
    cs.publish(db_session, organization=org, slug=slug, html=html,
               title=org.name,
               consent_text="By checking this box you agree that we may "
                            "contact you about your request.")

    served = client.get("/site/%s" % slug)
    assert served.status_code == 200
    assert "<!DOCTYPE html>" in served.text

    # The shape every one of these forms posts: a name, a way to reply, the
    # anti-spam pair, and whatever else that page happens to ask.
    filed = client.post("/site/%s/inquiry" % slug, json={
        "name": "Dana Whitfield",
        "email": "dana@example.com",
        "phone": "(214) 555-0100",
        "company": "Whitfield Facilities",
        "message": "Please get in touch.",
        "consent": True,
        "website_url": "",
        "form_started_at": 0,
        "page_url": "https://example.test/site/%s" % slug,
    })
    assert filed.status_code == 201, filed.text

    assert db_session.query(Lead).filter(
        Lead.organization_id == other.id).count() == 0
    lead = db_session.query(Lead).filter(
        Lead.organization_id == org.id).one()
    assert lead.first_name == "Dana"
    assert lead.sms_consent is True
