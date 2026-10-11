"""Call-backs on EvoSense properties show in the Callback Center; the owner's
permission page works from the app's own domain under the seller-facing name;
each property links to Zillow / Redfin for a person to look at."""
import re

import pytest

from app.models.evosense_models import EvoSenseProperty
from app.models.sms_consent_models import SmsConsentRecord


@pytest.fixture
def prop(db_session, sample_org):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="2620 Kirby St", city="Dallas",
                         state="TX", zip_code="75203", county="Dallas", parcel_apn="00000123")
    db_session.add(p)
    db_session.commit()
    return p


def test_call_back_flows_into_the_callback_center(client, auth_headers, prop):
    client.post("/wholesale/evosense/properties/%s/owner" % prop.id, headers=auth_headers, json={"name": "Maria Lopez"})
    r = client.post("/wholesale/evosense/properties/%s/follow-up" % prop.id, headers=auth_headers,
                    json={"due_at": "2020-01-01T15:00:00Z", "note": "Ask about the roof"})
    assert r.status_code == 200, r.text
    fu = r.json()
    assert fu["kind"] == "followup" and fu["bucket"] == "overdue" and fu["address"].startswith("2620 Kirby")
    detail = client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers).json()
    assert [x["followup_id"] for x in detail["follow_ups"]] == [fu["followup_id"]]
    center = client.get("/wholesale/ops/callbacks", headers=auth_headers).json()
    over = [x for x in center["buckets"]["overdue"] if x["kind"] == "followup"]
    assert over and over[0]["seller_name"] == "Maria Lopez" and over[0]["notes"] == "Ask about the roof"
    done = client.post("/wholesale/evosense/follow-ups/%s/complete" % fu["followup_id"], headers=auth_headers,
                       json={"note": "Owner wants to wait a month"})
    assert done.status_code == 200 and done.json()["bucket"] == "completed"
    center = client.get("/wholesale/ops/callbacks", headers=auth_headers).json()
    assert not [x for x in center["buckets"]["overdue"] if x["kind"] == "followup"]
    assert [x for x in center["buckets"]["completed"] if x["kind"] == "followup"]
    again = client.post("/wholesale/evosense/follow-ups/%s/cancel" % fu["followup_id"], headers=auth_headers)
    assert again.status_code == 409
    assert client.post("/wholesale/evosense/properties/%s/follow-up" % prop.id, headers=auth_headers,
                       json={"due_at": "not a date"}).status_code == 422


def test_owner_page_on_the_app_domain_under_the_seller_facing_name(client, auth_headers, db_session, prop):
    r = client.patch("/wholesale/settings", headers=auth_headers, json={"public_business_name": "  EvoSense   Properties "})
    assert r.status_code == 200, r.text
    assert client.get("/wholesale/settings", headers=auth_headers).json()["public_business_name"] == "EvoSense Properties"
    client.post("/wholesale/evosense/properties/%s/owner" % prop.id, headers=auth_headers, json={"name": "Maria Lopez"})
    out = client.post("/wholesale/evosense/properties/%s/permission-link" % prop.id,
                      headers=dict(auth_headers, Origin="https://app.evosyspro.live"),
                      json={"phone": "2145550142", "owner_name": "Maria Lopez"}).json()
    assert out["url"].startswith("https://app.evosyspro.live/permission/")
    assert "EvoSense Properties" in out["text"]
    tok = re.search(r"/permission/([A-Za-z0-9_-]+)$", out["url"]).group(1)
    data = client.get("/permission/%s/data" % tok).json()
    assert data["status"] == "sent" and data["business"] == "EvoSense Properties"
    assert data["first_name"] == "Maria" and data["phone_last4"] == "0142" and "EvoSense Properties" in data["disclosure"]
    assert "2145550142" not in str(data)
    assert client.post("/permission/%s/answer" % tok, json={"answer": "confirm", "agree": False}).status_code == 400
    assert db_session.query(SmsConsentRecord).count() == 0
    ok = client.post("/permission/%s/answer" % tok, json={"answer": "confirm", "agree": True})
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"
    rec = db_session.query(SmsConsentRecord).one()
    assert "EvoSense Properties" in rec.disclosure_text and tok not in (rec.source_url or "")
    assert client.get("/permission/nope/data").json()["status"] == "not_found"


def test_listing_site_links_for_a_person(client, auth_headers, prop):
    links = client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers).json()["links"]
    assert links["zillow_url"] == "https://www.zillow.com/homes/2620-Kirby-St-Dallas-TX-75203_rb/"
    assert links["redfin_url"].startswith("https://www.google.com/search?q=site%3Aredfin.com")
