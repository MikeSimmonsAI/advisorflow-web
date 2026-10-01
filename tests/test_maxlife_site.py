"""Max Life public website: static contract + its intake adapter.

Static: files exist, both forms post to the existing public intake path,
the SMS consent box is unchecked by default and carries disclosure text, and
no rating / review markup or fabricated testimonial is shipped.

Backend: the journey inquiry lands in the CONFIGURED organization through
public_capture, consent=false is stored as a plain "no" without touching the
consent columns, and contact creation does not depend on consent.
"""

import json
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.models.models import Lead, Organization, Platform

SITE = Path(__file__).resolve().parent.parent / "public-site" / "maxlife"
INDEX = SITE / "index.html"


# ── static ──────────────────────────────────────────────────────────────────

def test_static_files_exist():
    for rel in ("index.html", "css/site.css", "js/site.js", "js/config.js",
                "img/lion-mark.svg", "privacy.html", "terms.html",
                "sms-terms.html", "disclosures.html", "404.html"):
        assert (SITE / rel).is_file(), rel


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms, self.checkboxes, self.links = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("data-journey"):
            self.forms.append(a)
        if tag == "input" and a.get("name") == "consent":
            self.checkboxes.append(a)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])


def _parsed():
    p = _Forms()
    p.feed(INDEX.read_text(encoding="utf-8"))
    return p


def test_two_separate_forms_post_to_the_public_intake_path():
    forms = _parsed().forms
    assert {f["data-journey"] for f in forms} == {"family", "builder"}
    for f in forms:
        assert re.fullmatch(r"/site-intake/[a-z0-9-]+/inquiry", f["action"]), f
        assert f["method"].lower() == "post"
    js = (SITE / "js" / "site.js").read_text(encoding="utf-8")
    assert 'consent: !!(consentBox && consentBox.checked)' in js


def test_consent_checkbox_defaults_unchecked_with_disclosure():
    p = _parsed()
    assert len(p.checkboxes) == 2
    for cb in p.checkboxes:
        assert cb.get("type") == "checkbox"
        assert "checked" not in cb and "required" not in cb
    html = INDEX.read_text(encoding="utf-8")
    assert html.count("data-consent-text") == 2
    assert html.count("Reply STOP to unsubscribe") == 2


def test_no_fabricated_ratings_or_reviews():
    html = INDEX.read_text(encoding="utf-8").lower()
    for bad in ("aggregaterating", "ratingvalue", "reviewcount",
                '"@type": "review"', "★", "&#9733;", "5 stars", "5-star"):
        assert bad not in html, bad
    # Every story is a clearly marked placeholder.
    stories = re.findall(r'<figure class="story[^"]*"[^>]*>', html)
    assert stories and all('data-placeholder="true"' in s for s in stories)
    assert html.count("not a real testimonial") == len(stories)


def test_internal_links_resolve():
    html = INDEX.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    for href in _parsed().links:
        if href.startswith("#"):
            assert href[1:] in ids, href
        elif not href.startswith(("http", "mailto:", "tel:")):
            assert (SITE / href.split("#")[0]).is_file(), href


# ── backend adapter ─────────────────────────────────────────────────────────

@pytest.fixture()
def inquiry_client(client):
    from app.main import app
    from app.routers import brand_site_inquiry_router as r
    if not any(getattr(rt, "path", "") == "/site-intake/{platform_slug}/inquiry"
               for rt in app.routes):
        app.include_router(r.router)
    return client


def _configured(db, slug="maxlife-test"):
    platform = Platform(name="Max Life", slug=slug,
                        website_url="https://maxlife.example")
    db.add(platform)
    db.commit()
    org = Organization(name="ML Org", slug="ml-" + uuid.uuid4().hex[:8],
                       plan="standard", platform_id=platform.id, is_active=True)
    db.add(org)
    db.commit()
    platform.public_intake_organization_id = org.id
    db.commit()
    return platform, org


BASE = {
    "first_name": "Jordan", "last_name": "Reyes",
    "email": "jordan@example.com", "phone": "(214) 555-0199",
    "interests": "Family protection, Retirement planning",
    "page_url": "https://maxlife.example/#contact",
    "submitted_at": "2026-10-01T12:00:00Z",
    "utm_source": "facebook", "utm_campaign": "fall",
    "consent": False,
    "consent_text": "By checking this box, I agree to receive SMS ...",
    "consent_version": "maxlife-site-sms-2026-10-01",
}


def test_family_inquiry_without_consent_creates_contact(inquiry_client, db_session):
    _, org = _configured(db_session)
    res = inquiry_client.post("/site-intake/maxlife-test/inquiry",
                              json=dict(BASE, journey="family"))
    assert res.status_code == 201, res.text
    lead = db_session.query(Lead).filter(Lead.id == res.json()["lead_id"]).one()
    assert lead.organization_id == org.id
    assert lead.source == "Max Life Website"
    assert lead.source_detail == "Website Inquiry - Families & Individuals"
    assert not lead.sms_consent
    assert not (lead.tier or "")  # no sales tier, no cadence
    fields = json.loads(lead.custom_fields)
    assert fields["sms_consent_answer"] == "no"
    assert fields["utm_source"] == "facebook"
    assert fields["journey"] == "family"


def test_builder_inquiry_with_consent_records_evidence(inquiry_client, db_session):
    _configured(db_session)
    res = inquiry_client.post("/site-intake/maxlife-test/inquiry",
                              json=dict(BASE, journey="builder", consent=True))
    assert res.status_code == 201, res.text
    lead = db_session.query(Lead).filter(Lead.id == res.json()["lead_id"]).one()
    assert lead.source_detail == "Website Inquiry - Agents & Builders"
    assert lead.sms_consent is True
    assert lead.sms_consent_text == BASE["consent_text"]


def test_unknown_journey_and_unconfigured_brand_refuse(inquiry_client, db_session):
    _configured(db_session)
    assert inquiry_client.post("/site-intake/maxlife-test/inquiry",
                               json=dict(BASE, journey="other")).status_code == 422
    res = inquiry_client.post("/site-intake/nobody/inquiry",
                              json=dict(BASE, journey="family"))
    assert res.status_code == 503
    assert db_session.query(Lead).count() == 0
