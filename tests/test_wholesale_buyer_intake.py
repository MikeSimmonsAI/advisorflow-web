"""Investor buy-box form (wholesale.evosyspro.live) -> EvoSys Wholesale buyers.

The rules:
  * the buyer lands in exactly the organization the configured intake key
    names - never one the payload names;
  * it becomes a real WholesaleBuyer + a structured WholesaleBuyBox, linked to
    the shared contact database as a partner, never a Lead;
  * a returning investor (same email or phone) is reused - nothing a person
    typed is overwritten - and a repeated submission writes nothing;
  * contact permission is required and is NOT SMS consent; nothing is sent.
"""
import json

import pytest

from app.models.models import Lead, Notification, Organization, Platform, User
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import (WholesaleBuyBox, WholesaleBuyer, WholesaleEvent,
                                         WholesaleSettings)
from app.services import wholesale_buyer_intake as BI
from app.services.auth_service import hash_password

KEY_A = "evo-test-buyer-key-aaaaaaaaaaaaa"
KEY_B = "other-test-buyer-key-bbbbbbbbbbb"


def form(**over):
    base = {"intent": "buyer", "full_name": "Jordan Investor", "company": "JI Holdings LLC",
            "email": "Jordan@Example.com", "phone": "(214) 555-0187",
            "markets": "Dallas, Tarrant County; Fort Worth", "strategy": "Fix & Flip",
            "budget": "$100,000–$250,000", "notes": "3/2 brick, light rehab, close in 14 days",
            "contact_permission": "yes", "submission_id": "BUY-TEST-" + str(len(over)) + str(sorted(over)),
            "source_url": "https://wholesale.evosyspro.live/buyers.html", "form_version": "buyers-v1",
            "organization_id": "someone-else"}
    base.update(over)
    return base


def _org(db, platform, name, key=None):
    org = Organization(name=name, slug=name.lower().replace(" ", "-"), plan="standard",
                       industry="real_estate", platform_id=platform.id)
    db.add(org)
    db.flush()
    db.add(WholesaleSettings(organization_id=org.id, public_intake_key=key))
    db.add(User(organization_id=org.id, email="admin@%s.test" % org.slug,
                password_hash=hash_password("TestPass123!"), full_name="Admin", role="org_admin",
                must_change_password=False))
    db.commit()
    return org


@pytest.fixture()
def world(db_session):
    db = db_session
    evo = Platform(name="EvoSys Pro", slug="evosyspro")
    db.add(evo)
    db.flush()
    return {"db": db, "evo": _org(db, evo, "EVO Integrated Solutions LLC", KEY_A),
            "other": _org(db, evo, "Other Wholesaler", KEY_B)}


def post(client, payload, key=KEY_A):
    return client.post("/site-intake/wholesale/%s/buyer-inquiry" % key, json=payload)


def test_buyer_form_creates_buyer_and_structured_buy_box_in_the_keyed_org(client, world):
    db, evo, other = world["db"], world["evo"], world["other"]
    leads_before = db.query(Lead).count()
    r = post(client, form())
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"success", "reference"} and body["reference"].startswith("BUY-")

    b = db.query(WholesaleBuyer).filter_by(organization_id=evo.id).one()
    assert db.query(WholesaleBuyer).filter_by(organization_id=other.id).count() == 0
    assert b.email == "jordan@example.com" and b.contact_name == "Jordan Investor"
    assert b.company_name == "JI Holdings LLC" and b.entity_type == "company"
    assert b.source == "website" and b.preferred_channel == "email" and not b.is_test
    assert not b.cash_verified and not b.proof_of_funds_on_file          # nothing invented
    assert "Not SMS marketing consent" in b.notes
    db.refresh(b)
    assert b.org_contact_id, "linked to the shared contact database as a partner"

    box = db.query(WholesaleBuyBox).filter_by(buyer_id=b.id).one()
    assert json.loads(box.markets) == ["Dallas", "Tarrant County", "Fort Worth"]
    assert json.loads(box.strategies) == ["flip"] and box.property_types is None
    assert float(box.min_price) == 100000 and float(box.max_price) == 250000

    ev = db.query(WholesaleEvent).filter_by(organization_id=evo.id,
                                            action=BI.EVENT_RECEIVED).one()
    det = json.loads(ev.details)
    assert det["contact_permission"] is True and det["sms_marketing_consent"] is False
    # Never a Lead, never SMS consent, staff told in-app only.
    assert db.query(Lead).count() == leads_before
    assert db.query(SmsConsentRecord).count() == 0
    note = db.query(Notification).filter(Notification.link == "/wholesale/buyers").one()
    assert "JI Holdings LLC" in note.message


def test_same_submission_twice_writes_nothing_new(client, world):
    db = world["db"]
    p = form(submission_id="BUY-DOUBLE-CLICK-1")
    first = post(client, p).json()
    second = post(client, p).json()
    assert first["reference"] == second["reference"]
    assert db.query(WholesaleBuyer).count() == 1 and db.query(WholesaleBuyBox).count() == 1


def test_existing_buyer_is_never_changed_by_a_public_form(client, world):
    db, evo = world["db"], world["evo"]
    db.add(WholesaleBuyer(organization_id=evo.id, entity_type="person", contact_name="J. Investor",
                          email=None, phone="214.555.0187", preferred_channel="sms",
                          source="manual", notes="Met at REIA.", do_not_contact=True,
                          do_not_contact_reason="Asked to stop"))
    db.commit()
    # Someone types this buyer's phone with THEIR OWN email.
    r = post(client, form(email="someone.else@example.com", strategy="Multifamily",
                          budget="Flexible / Deal Dependent", submission_id="BUY-RETURN-1"))
    assert r.status_code == 201, r.text
    b = db.query(WholesaleBuyer).filter_by(organization_id=evo.id).one()   # matched by phone
    assert b.email is None, "a public form never fills in a buyer's contact details"
    assert b.contact_name == "J. Investor" and b.preferred_channel == "sms" and b.source == "manual"
    assert b.do_not_contact is True and b.phone == "214.555.0187"
    assert b.notes.startswith("Met at REIA.") and "UNVERIFIED" in b.notes
    box = db.query(WholesaleBuyBox).filter_by(buyer_id=b.id).one()
    assert box.is_active is False and "unverified" in box.label
    assert json.loads(box.property_types) == ["multi_family"] and json.loads(box.strategies) == ["rental"]
    note = db.query(Notification).filter(Notification.link == "/wholesale/buyers").one()
    assert "confirm with the buyer" in note.message


def test_new_buyer_phone_is_stored_e164(client, world):
    db = world["db"]
    assert post(client, form(submission_id="BUY-E164")).status_code == 201
    assert db.query(WholesaleBuyer).one().phone == "+12145550187"


def test_permission_is_required_and_bad_answers_are_refused_with_field_errors(client, world):
    db = world["db"]
    r = post(client, form(contact_permission="no", strategy="Wholesale", budget="a lot",
                          email="nope", phone="12"))
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert set(errs) >= {"contact_permission", "strategy", "budget", "email", "phone"}
    assert db.query(WholesaleBuyer).count() == 0


def test_hyphen_budget_is_the_same_answer_as_the_en_dash(client, world):
    db = world["db"]
    assert post(client, form(budget="$250,000-$500,000", submission_id="BUY-HYPHEN")).status_code == 201
    box = db.query(WholesaleBuyBox).one()
    assert float(box.min_price) == 250000 and float(box.max_price) == 500000


def test_unknown_or_malformed_key_gets_the_neutral_refusal(client, world):
    db = world["db"]
    assert post(client, form(), key="x" * 30).status_code == 503
    assert post(client, form(), key="bad key!").status_code in (404, 503)
    assert db.query(WholesaleBuyer).count() == 0
