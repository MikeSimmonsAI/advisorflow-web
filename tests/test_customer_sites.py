"""A customer's own public website, and the enquiries it files.

The property under test is the destination. The brand's marketing site posts
to a brand slug and lands in that brand's configured intake organization; a
customer's own site belongs to one customer and must land in that customer's
workspace and nowhere else. Everything else here — the honeypot, the reserved
slugs, the consent wording — protects that one fact from the edges.
"""

import json

from app.models.customer_site_models import CustomerSite
from app.models.models import (EmailMessage, Lead, Message, Notification,
                               Organization, User)
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password


PAGE = ("<!DOCTYPE html><html><head><title>Energy</title></head>"
        "<body><h1>Compare your options</h1></body></html>")

CONSENT = ("By submitting this form you agree that we may contact you by "
           "phone, text or email about your request.")


def _org(db_session, name, slug):
    org = Organization(name=name, slug=slug, plan="standard", industry="energy")
    db_session.add(org)
    db_session.commit()
    return org


def _god(db_session, email="god@example.com"):
    user = User(organization_id=None, email=email,
                password_hash=hash_password("TestPass123!"),
                full_name="Platform Owner", role="god_admin",
                must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return user


def _headers(db_session, user):
    return {"Authorization": f"Bearer {create_access_token(user, db_session)}"}


def _publish(db_session, org, slug="energy-co", **kw):
    return cs.publish(db_session, organization=org, slug=slug, html=PAGE,
                      title=org.name, **kw)


# ── PUBLISHING ──────────────────────────────────────────────────────────────

def test_publishing_keeps_the_address_across_republishes(db_session):
    """A demo link is meant to be retired. A website address is on stationery."""
    org = _org(db_session, "EnergyCo", "energyco")
    first = _publish(db_session, org)
    again = cs.publish(db_session, organization=org, slug="energy-co",
                       html=PAGE.replace("Compare", "Shop"))
    assert again.id == first.id
    assert again.slug == first.slug
    assert "Shop" in again.html


def test_a_reserved_slug_is_refused(db_session):
    org = _org(db_session, "Grabby", "grabby")
    for slug in ("admin", "god", "site", "privacy-policy"):
        try:
            cs.publish(db_session, organization=org, slug=slug, html=PAGE)
        except ValueError as exc:
            assert "reserved" in str(exc)
        else:  # pragma: no cover - the assertion is the point
            raise AssertionError("%r was accepted" % slug)


def test_a_malformed_slug_is_refused(db_session):
    org = _org(db_session, "Badslug", "badslug")
    for slug in ("ab", "-leading", "trailing-", "has space", "under_score",
                 "x" * 80):
        try:
            cs.publish(db_session, organization=org, slug=slug, html=PAGE)
        except ValueError:
            continue
        raise AssertionError("%r was accepted" % slug)


def test_a_slug_is_normalised_rather_than_rejected_for_case(db_session):
    """A URL is lowercase. Refusing "Energy-Co" would teach nothing; storing
    it as typed would give the same page two addresses."""
    org = _org(db_session, "Casing", "casing")
    site = cs.publish(db_session, organization=org, slug="  Energy-Co  ",
                      html=PAGE)
    assert site.slug == "energy-co"


def test_a_slug_cannot_be_taken_from_another_customer(db_session):
    first = _org(db_session, "First", "first")
    second = _org(db_session, "Second", "second")
    _publish(db_session, first, slug="shared-name")
    try:
        cs.publish(db_session, organization=second, slug="shared-name", html=PAGE)
    except ValueError as exc:
        assert "another customer" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the slug was stolen")


def test_an_empty_or_oversized_page_is_refused(db_session):
    org = _org(db_session, "Sizes", "sizes")
    for html in ("", "   ", "x" * (cs.MAX_HTML_BYTES + 1)):
        try:
            cs.publish(db_session, organization=org, slug="sized-page", html=html)
        except ValueError:
            continue
        raise AssertionError("accepted a page it should not have")


def test_a_consent_version_without_the_wording_is_refused(db_session):
    """A version number proves nothing. The sentence is the evidence."""
    org = _org(db_session, "Consent", "consent-co")
    try:
        cs.publish(db_session, organization=org, slug="consent-page", html=PAGE,
                   consent_version="2026-09")
    except ValueError as exc:
        assert "wording" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("published a consent page with no wording")


def test_the_api_answer_never_carries_the_page_source(db_session):
    org = _org(db_session, "NoHtml", "nohtml")
    site = _publish(db_session, org)
    assert "html" not in cs.out(site)


# ── SERVING ─────────────────────────────────────────────────────────────────

def test_the_page_is_served_and_counted(db_session, client):
    org = _org(db_session, "Served", "served")
    site = _publish(db_session, org, slug="served-page")
    res = client.get("/site/served-page")
    assert res.status_code == 200
    assert "Compare your options" in res.text
    db_session.refresh(site)
    assert site.view_count == 1


def test_unknown_and_deactivated_answer_the_same_way(db_session, client):
    org = _org(db_session, "Gone", "gone")
    site = _publish(db_session, org, slug="gone-page")
    cs.deactivate(db_session, site)
    for path in ("/site/gone-page", "/site/never-existed"):
        res = client.get(path)
        assert res.status_code == 404
        assert "Gone" not in res.text  # the customer's name is not in the answer


# ── ENQUIRIES ───────────────────────────────────────────────────────────────

def test_an_enquiry_lands_in_the_owning_workspace_and_nowhere_else(
        db_session, client):
    mine = _org(db_session, "Mine", "mine")
    theirs = _org(db_session, "Theirs", "theirs")
    _publish(db_session, mine, slug="mine-page")

    res = client.post("/site/mine-page/inquiry", json={
        "name": "Dana Whitfield", "email": "dana@example.com",
        "phone": "(214) 555-0100", "message": "Please review my bill.",
        "service_type": "Residential",
    })
    assert res.status_code == 201
    assert res.json()["success"] is True

    assert db_session.query(Lead).filter(
        Lead.organization_id == theirs.id).count() == 0
    lead = db_session.query(Lead).filter(
        Lead.organization_id == mine.id).one()
    assert lead.first_name == "Dana"
    assert lead.last_name == "Whitfield"
    # The source names the thing the visitor believes they contacted.
    assert lead.source == "Mine Website"
    # A form answer with no column of its own is kept, not dropped.
    assert "Residential" in (lead.notes or "")
    assert "service_type" in json.loads(lead.custom_fields or "{}")


def test_a_second_submission_updates_the_same_person(db_session, client):
    org = _org(db_session, "Dedup", "dedup")
    _publish(db_session, org, slug="dedup-page")
    payload = {"name": "Dana Whitfield", "phone": "(214) 555-0100"}
    client.post("/site/dedup-page/inquiry", json=payload)
    client.post("/site/dedup-page/inquiry", json=dict(payload, email="d@example.com"))
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 1


def test_nothing_is_sent_when_an_enquiry_arrives(db_session, client):
    org = _org(db_session, "Quiet", "quiet")
    _publish(db_session, org, slug="quiet-page")
    client.post("/site/quiet-page/inquiry",
                json={"name": "Dana Whitfield", "email": "dana@example.com"})
    assert db_session.query(Message).count() == 0
    assert db_session.query(EmailMessage).count() == 0
    assert db_session.query(Notification).count() == 0


def test_an_unreachable_submission_is_refused_rather_than_stored(db_session, client):
    org = _org(db_session, "Unreachable", "unreachable")
    _publish(db_session, org, slug="unreachable-page")
    res = client.post("/site/unreachable-page/inquiry", json={"name": "No Way"})
    assert res.status_code == 422
    assert db_session.query(Lead).count() == 0


def test_the_honeypot_is_answered_cheerfully_and_stores_nothing(db_session, client):
    org = _org(db_session, "Bots", "bots")
    _publish(db_session, org, slug="bots-page")
    res = client.post("/site/bots-page/inquiry", json={
        "name": "Bot", "email": "bot@example.com", "website_url": "http://spam"})
    assert res.status_code == 201
    assert res.json()["success"] is True
    assert db_session.query(Lead).count() == 0


def test_the_consent_wording_comes_from_the_page_not_the_post(db_session, client):
    """A client can lie about what it posts back. It cannot change the row."""
    org = _org(db_session, "Evidence", "evidence")
    _publish(db_session, org, slug="evidence-page", consent_text=CONSENT,
             consent_version="2026-09")
    client.post("/site/evidence-page/inquiry", json={
        "name": "Dana Whitfield", "phone": "(214) 555-0100",
        "consent": True, "consent_text": "I agree to anything"})
    lead = db_session.query(Lead).one()
    assert lead.sms_consent is True
    assert lead.sms_consent_text == CONSENT
    assert "v2026-09" in (lead.sms_consent_source or "")


def test_an_enquiry_for_a_deactivated_customer_is_not_filed(db_session, client):
    org = _org(db_session, "Closed", "closed")
    _publish(db_session, org, slug="closed-page")
    org.is_active = False
    db_session.commit()
    res = client.post("/site/closed-page/inquiry",
                      json={"name": "Dana", "email": "dana@example.com"})
    assert res.status_code == 503
    assert db_session.query(Lead).count() == 0


def test_the_default_owner_is_applied_to_a_new_enquiry(db_session, client):
    org = _org(db_session, "Owned", "owned")
    advisor = User(organization_id=org.id, email="owner@owned.example",
                   password_hash=hash_password("TestPass123!"),
                   full_name="Named Owner", role="advisor",
                   must_change_password=False)
    db_session.add(advisor)
    db_session.commit()
    _publish(db_session, org, slug="owned-page", default_owner_user_id=advisor.id)
    client.post("/site/owned-page/inquiry",
                json={"name": "Dana", "email": "dana@example.com"})
    assert db_session.query(Lead).one().assigned_to_id == advisor.id


# ── THE STAFF DOOR ──────────────────────────────────────────────────────────

def test_publishing_is_god_only(db_session, client):
    org = _org(db_session, "Staff", "staff")
    advisor = User(organization_id=org.id, email="nobody@staff.example",
                   password_hash=hash_password("TestPass123!"),
                   full_name="Nobody", role="advisor",
                   must_change_password=False)
    db_session.add(advisor)
    db_session.commit()

    unauthenticated = client.put("/god/customer-sites/%s/nope" % org.id,
                                 json={"html": PAGE})
    assert unauthenticated.status_code == 401

    refused = client.put("/god/customer-sites/%s/nope" % org.id,
                         headers=_headers(db_session, advisor),
                         json={"html": PAGE})
    assert refused.status_code == 403
    assert db_session.query(CustomerSite).count() == 0


def test_god_can_publish_and_list(db_session, client):
    org = _org(db_session, "GodPub", "godpub")
    god = _god(db_session)
    headers = _headers(db_session, god)

    published = client.put("/god/customer-sites/%s/godpub-page" % org.id,
                           headers=headers,
                           json={"html": PAGE, "title": "GodPub",
                                 "consent_text": CONSENT})
    assert published.status_code == 200, published.text
    assert published.json()["slug"] == "godpub-page"

    listed = client.get("/god/customer-sites/%s" % org.id, headers=headers)
    assert listed.status_code == 200
    assert [s["slug"] for s in listed.json()["sites"]] == ["godpub-page"]

    # And the page it just published is live.
    assert client.get("/site/godpub-page").status_code == 200


def test_a_bad_slug_is_a_400_with_a_reason(db_session, client):
    org = _org(db_session, "BadReq", "badreq")
    god = _god(db_session, email="god2@example.com")
    res = client.put("/god/customer-sites/%s/admin" % org.id,
                     headers=_headers(db_session, god), json={"html": PAGE})
    assert res.status_code == 400
    assert "reserved" in res.json()["detail"]
