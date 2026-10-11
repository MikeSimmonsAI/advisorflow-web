"""Permission links: a caller sends an owner a personal link; ONLY the owner's
own tick on that page writes a consent of record, for that number only."""
import re

import pytest

from app.models.evosense_models import EvoSensePermissionLink, EvoSenseProperty
from app.models.sms_consent_models import SmsConsentRecord
from app.services import wholesale_sms as WS


@pytest.fixture
def prop(db_session, sample_org):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="2620 Kirby St", city="Dallas",
                         state="TX", zip_code="75203", county="Dallas", parcel_apn="00000123")
    db_session.add(p)
    db_session.commit()
    return p


def _make(client, auth_headers, prop, phone="214-555-0142"):
    r = client.post("/wholesale/evosense/properties/%s/owner" % prop.id, headers=auth_headers,
                    json={"name": "Maria Lopez"})
    assert r.status_code in (200, 201), r.text
    r = client.post("/wholesale/evosense/properties/%s/permission-link" % prop.id, headers=auth_headers,
                    json={"phone": phone, "owner_name": "Maria Lopez"})
    assert r.status_code == 200, r.text
    return r.json()


def _token(out):
    return re.search(r"/permission/([A-Za-z0-9_-]+)$", out["url"]).group(1)


def test_creating_a_link_grants_nothing(client, auth_headers, db_session, sample_org, prop):
    out = _make(client, auth_headers, prop)
    assert out["status"] == "sent" and out["phone_last4"] == "0142"
    assert "/permission/" in out["url"] and out["url"] in out["text"]
    assert db_session.query(SmsConsentRecord).count() == 0
    link = db_session.query(EvoSensePermissionLink).one()
    assert link.token_hash != _token(out)                                  # only the hash is stored
    detail = client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers).json()
    assert detail["permission"]["state"] == "link_sent"
    assert any(c["value"].endswith("5550142") for c in detail["contacts"])  # the number is now a contact
    pend = client.get("/wholesale/evosense/permission-links", headers=auth_headers).json()["items"]
    assert [x["property_id"] for x in pend] == [prop.id]


def test_owner_tick_records_consent_for_that_number(client, auth_headers, db_session, sample_org, prop):
    out = _make(client, auth_headers, prop)
    tok = _token(out)
    page = client.get("/permission/%s" % tok)
    assert page.status_code == 200 and "2620 Kirby St" in page.text and "STOP" in page.text
    assert "5550142" not in page.text and "0142" in page.text              # only the last four digits
    assert "checked" not in page.text                                     # the box starts unticked
    # pressing Confirm without the tick records nothing
    bad = client.post("/permission/%s" % tok, data={"answer": "confirm"})
    assert bad.status_code == 400 and db_session.query(SmsConsentRecord).count() == 0
    ok = client.post("/permission/%s" % tok, data={"answer": "confirm", "agree": "yes"},
                     headers={"user-agent": "pytest-owner"})
    assert ok.status_code == 200 and "Thank you" in ok.text
    rec = db_session.query(SmsConsentRecord).one()
    assert rec.organization_id == sample_org.id and rec.program == WS.PROGRAM
    assert rec.phone_normalized == "+12145550142" and rec.status == "opted_in"
    assert rec.consent_method == "personal_link_checkbox" and rec.property_id == prop.id
    assert "AI" in rec.disclosure_text and "STOP" in rec.disclosure_text and rec.user_agent == "pytest-owner"
    assert tok not in (rec.source_url or "")                              # the token is never stored
    assert WS.latest_consent(db_session, sample_org.id, "+12145550142").id == rec.id
    detail = client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers).json()
    assert detail["permission"]["state"] == "on_file"
    again = client.post("/permission/%s" % tok, data={"answer": "confirm", "agree": "yes"})
    assert "already confirmed" in again.text and db_session.query(SmsConsentRecord).count() == 1


def test_no_thanks_and_bad_links_record_nothing(client, auth_headers, db_session, prop):
    out = _make(client, auth_headers, prop)
    tok = _token(out)
    r = client.post("/permission/%s" % tok, data={"answer": "decline"})
    assert r.status_code == 200 and "chose not" in r.text
    assert db_session.query(SmsConsentRecord).count() == 0
    assert db_session.query(EvoSensePermissionLink).one().status == "declined"
    assert client.get("/permission/not-a-real-token").status_code == 404


def test_a_new_link_retires_the_old_one(client, auth_headers, db_session, prop):
    first = _make(client, auth_headers, prop)
    second = client.post("/wholesale/evosense/properties/%s/permission-link" % prop.id, headers=auth_headers,
                         json={"phone": "2145550142"}).json()
    assert "already" not in client.get("/permission/%s" % _token(second)).text
    assert "expired" in client.get("/permission/%s" % _token(first)).text


def test_bad_phone_is_refused(client, auth_headers, prop):
    r = client.post("/wholesale/evosense/properties/%s/permission-link" % prop.id, headers=auth_headers,
                    json={"phone": "12"})
    assert r.status_code == 422
