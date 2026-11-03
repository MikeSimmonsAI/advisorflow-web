# -*- coding: utf-8 -*-
"""Issue #452 stage 2 - onboarding readiness, supported territory, offer type.
Synthetic records only."""
import json
import uuid

import pytest
from fastapi import HTTPException

from app.models.acquisition_models import AcquisitionOpportunity
from app.models.models import Lead, Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from app.services.acquisition import geo as G
from app.services.acquisition import outreach as O
from app.services.auth_service import hash_password


@pytest.fixture()
def setup(db_session, monkeypatch):
    db = db_session
    org = Organization(name="Geo Test Cleaning", slug="gt-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                       industry="cleaning", is_active=True, enabled_features=json.dumps(["leads"]))
    db.add(org)
    god = User(organization_id=None, email="gt-%s@example.com" % uuid.uuid4().hex[:8], full_name="G",
               password_hash=hash_password("Pass12345!"), role="god_admin", is_active=True, must_change_password=False)
    db.add(god)
    db.commit()
    E.enable(db, org, god)
    monkeypatch.setenv("ACQUISITION_OUTREACH_ENABLED", "1")
    monkeypatch.setenv("ACQUISITION_TEST_RECIPIENTS", "owner@example.com")
    return db, org, god


@pytest.mark.parametrize("state,country,want", [
    ("TX", None, G.ELIGIBLE), ("tx", None, G.ELIGIBLE), ("Texas", None, G.ELIGIBLE), ("DC", None, G.ELIGIBLE),
    ("CA", None, G.EXCLUDED), ("California", None, G.EXCLUDED), ("AK", None, G.EXCLUDED), ("HI", None, G.EXCLUDED),
    ("PR", None, G.EXCLUDED), ("ON", "Canada", G.EXCLUDED), ("TX", "Mexico", G.EXCLUDED),
    (None, None, G.REVIEW), ("", None, G.REVIEW), ("ZZ", None, G.REVIEW),
])
def test_territory(state, country, want):
    assert G.status(state, country)[0] == want


def test_address_parsing_never_guesses():
    assert G.from_address("1 Main St, Dallas, TX 75201, USA") == ("TX", "75201", "USA")
    assert G.from_address("9 Ocean Ave, Los Angeles, CA 90001, USA")[0] == "CA"
    assert G.from_address("Dallas") == (None, None, None)
    assert G.from_address("12 King St W, Toronto, ON M5H 1A1, Canada")[2] == "canada"
    assert G.from_address("(214) 555-0100") == (None, None, None)     # an area code is not a location


def test_prospect_geography_and_review(setup):
    db, org, god = setup
    with pytest.raises(HTTPException) as e:
        E.add_prospect(db, org, god, {"business_name": "LA Office", "city": "Los Angeles", "state": "CA",
                                      "is_practice": False})
    assert "outside the supported territory" in e.value.detail
    unk = E.add_prospect(db, org, god, {"business_name": "Somewhere Dental", "email": "owner@example.com",
                                        "phone": "214-555-0100", "is_practice": True})
    assert unk.geo_status == G.REVIEW
    lead = db.query(Lead).filter(Lead.id == unk.lead_id).first()
    assert lead.state is None                                        # no "TX" default any more
    assert any(w.startswith("Location:") for w in O.refusals(db, org, unk, lead))
    out = E.set_location(db, org.id, unk, god, {"city": "Plano", "state": "TX", "note": "Checked their site"})
    assert out["geo_status"] == G.ELIGIBLE and not any(w.startswith("Location:") for w in O.refusals(db, org, unk, lead))
    out = E.set_location(db, org.id, unk, god, {"state": "CA"})
    db.refresh(unk)
    assert out["geo_status"] == G.EXCLUDED and unk.stage == C.DISQUALIFIED


def test_older_rows_use_the_lead_state(setup):
    db, org, god = setup
    opp = E.add_prospect(db, org, god, {"business_name": "Old Row", "state": "TX", "is_practice": True})
    opp.geo_status = None
    db.commit()
    assert E.opp_geo(opp, db.query(Lead).filter(Lead.id == opp.lead_id).first())["status"] == G.ELIGIBLE


def test_readiness_draft_until_required_items(setup):
    db, org, god = setup
    p = E.get_profile(db, org.id)
    r = E.readiness(org, p)
    assert not r["launch_ready"] and "Contact name" in r["missing"] and "Booking calendar" in r["missing"]
    E.update_profile(db, org.id, {"owner_name": "Ciara Test", "contact_email": "c@example.com",
                                  "contact_phone": "555-0101", "services": ["Office cleaning"],
                                  "service_states": "TX", "service_zips": "75201 75202",
                                  "mailing_address": "1 Test St", "approved_offer": "Free walkthrough",
                                  "appointment_attendee": "Ciara"}, owner=False)
    r = E.readiness(org, E.get_profile(db, org.id))
    assert r["missing"] == ["Booking calendar"]
    with pytest.raises(HTTPException):
        E.update_profile(db, org.id, {"service_states": ["TX", "CA"]}, owner=False)
    with pytest.raises(HTTPException):
        E.update_profile(db, org.id, {"service_zips": "7520"}, owner=False)
    with pytest.raises(HTTPException):
        E.update_profile(db, org.id, {"timezone": "Mars/Base"}, owner=False)


def test_offer_type_owner_only_and_free_by_default(setup):
    db, org, god = setup
    p = E.get_profile(db, org.id)
    assert E.profile_json(org, p, full=False)["offer_type"] == C.FREE_PILOT
    E.update_profile(db, org.id, {"offer_type": C.GROWTH_PILOT}, owner=False)          # client can't change it
    assert E.get_profile(db, org.id).offer_type in (None, C.FREE_PILOT)
    E.update_profile(db, org.id, {"offer_type": C.GROWTH_PILOT}, owner=True)
    assert E.profile_json(org, E.get_profile(db, org.id), full=False)["offer_label"].startswith("$250")
    with pytest.raises(HTTPException):
        E.update_profile(db, org.id, {"offer_type": "lifetime_free"}, owner=True)


def test_acquisition_prospects_stay_in_the_client_org(setup):
    """EvoSys's own sales prospects and a client's commercial prospects never mix:
    every acquisition row and its lead belong to the client organization."""
    db, org, god = setup
    opp = E.add_prospect(db, org, god, {"business_name": "Scope Dental", "state": "TX", "is_practice": True})
    lead = db.query(Lead).filter(Lead.id == opp.lead_id).first()
    assert opp.organization_id == org.id and lead.organization_id == org.id
    assert lead.source == C.PRACTICE_SOURCE
    assert db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.organization_id != org.id,
                                                   AcquisitionOpportunity.lead_id == lead.id).count() == 0
