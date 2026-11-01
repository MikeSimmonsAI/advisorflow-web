# -*- coding: utf-8 -*-
"""Issue #452 P2 - metered, gated business discovery. A fake provider stands
in for Google Places: these tests make no network calls."""
import asyncio
import json
import uuid

import pytest

from app.models.acquisition_models import AcquisitionDiscoveryRun, AcquisitionEvidence, AcquisitionOpportunity
from app.models.models import Lead, Organization, User
from app.models.sales_models import Membership
from app.services.acquisition import discovery as D
from app.services.acquisition import engine as E
from app.services.auth_service import create_access_token, hash_password


def _org(db, name, industry="cleaning"):
    o = Organization(name=name, slug="dq-%s" % uuid.uuid4().hex[:8], plan="enterprise", industry=industry,
                     is_active=True, enabled_features=json.dumps(["leads"]))
    db.add(o)
    db.commit()
    return o


def _user(db, org, role):
    u = User(organization_id=org.id if org else None, email="dq-%s@example.com" % uuid.uuid4().hex[:8], full_name="U",
             password_hash=hash_password("Pass12345!"), role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


PLACES = [
    {"place_id": "p1", "name": "Oak Lawn Family Dental", "formatted_address": "1 Main St, Dallas, TX 75201, USA",
     "user_ratings_total": 140},
    {"place_id": "p2", "name": "Top 10 Dentists in Dallas", "formatted_address": "x, Dallas, TX 75201, USA"},
    {"place_id": "p3", "name": "Existing Client Dental", "formatted_address": "2 Elm St, Dallas, TX 75201, USA"},
    {"place_id": "p4", "name": "Oak Lawn Family Dental", "formatted_address": "1 Main St, Dallas, TX 75201, USA"},
]
DETAILS = {"p1": {"website": "https://oaklawn-dental.example/", "formatted_phone_number": "(214) 555-0100",
                  "user_ratings_total": 140},
           "p2": {"website": "https://www.yelp.com/biz/x"},
           "p3": {"website": "https://existing-client.example"},
           "p4": {"website": "https://www.oaklawn-dental.example/about"}}


def _fake():
    calls = {"search": 0, "details": 0}

    async def search(query, n):
        calls["search"] += 1
        return PLACES[:n]

    async def details(pid):
        calls["details"] += 1
        return DETAILS.get(pid, {})
    return search, details, calls


@pytest.fixture()
def setup(db_session, monkeypatch):
    db = db_session
    org = _org(db, "Disc Test Cleaning")
    god = _user(db, None, "god_admin")
    E.enable(db, org, god)
    E.update_profile(db, org.id, {"exclusions": "existing-client.example"}, owner=True)
    monkeypatch.setenv("ACQUISITION_DISCOVERY_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("ACQUISITION_PLACES_SEARCH_USD", "0.03")
    monkeypatch.setenv("ACQUISITION_PLACES_DETAILS_USD", "0.02")
    return db, org, god


def test_plan_states_cost_and_refusals_without_calls(setup, monkeypatch):
    db, org, god = setup
    p = D.plan(db, org, "medical_dental", "Dallas", 20, practice=True)
    assert p["can_run"] and p["query"] == "dental office in Dallas, TX"
    assert p["requests"] == {"search": 1, "details_max": 20} and p["max_cost_usd"] == 0.43
    # paused pilot: real (non-practice) discovery is refused
    assert "paused" in " ".join(D.plan(db, org, "medical_dental", "Dallas", 5, practice=False)["refused_because"])
    # outside the service area / unwanted type
    assert not D.plan(db, org, "office", "Houston", 5, practice=True)["can_run"]
    E.update_profile(db, org.id, {"segments": ["office"]}, owner=False)
    assert not D.plan(db, org, "medical_dental", "Dallas", 5, practice=True)["can_run"]
    # no configured price -> refused, never guessed
    monkeypatch.delenv("ACQUISITION_PLACES_DETAILS_USD")
    p = D.plan(db, org, "office", "Dallas", 5, practice=True)
    assert p["max_cost_usd"] is None and not p["can_run"]
    # switched off by default
    monkeypatch.delenv("ACQUISITION_DISCOVERY_ENABLED")
    assert any("switched off" in r for r in D.plan(db, org, "office", "Dallas", 5, True)["refused_because"])
    with pytest.raises(Exception):
        D.plan(db, org, "office", "Dallas", 21, True)


def test_run_meters_dedupes_filters_and_separates_facts(setup):
    db, org, god = setup
    search, details, calls = _fake()
    out = asyncio.get_event_loop().run_until_complete(
        D.run(db, org, god, "medical_dental", "Dallas", 4, True, search=search, details=details))
    assert out["status"] == "done" and out["found"] == 4
    assert out["added"] == 1 and out["junk"] == 1 and out["excluded"] == 1 and out["duplicates"] == 1
    assert out["search_requests"] == 1 and out["detail_requests"] == 4 == calls["details"]
    assert out["cost_usd"] == round(1 * 0.03 + 4 * 0.02, 4)
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.organization_id == org.id).one()
    assert opp.is_practice and opp.source == "google_places" and opp.domain == "oaklawn-dental.example"
    assert opp.score == 100 and json.loads(opp.score_reasons)
    lead = db.query(Lead).get(opp.lead_id)
    assert lead.organization_id == org.id and lead.city == "Dallas" and lead.source == "managed_acquisition_practice"
    ev = {e.kind: e for e in db.query(AcquisitionEvidence).filter(AcquisitionEvidence.opportunity_id == opp.id)}
    assert ev["segment"].verified is False and "inferred" in ev["segment"].value
    assert ev["website"].verified is True and ev["contact_phone"].verified is True
    assert opp.stage == "discovered"      # discovery never contacts or advances anyone


def test_daily_limit(setup):
    db, org, god = setup
    for _ in range(D.MAX_RUNS_PER_DAY):
        db.add(AcquisitionDiscoveryRun(organization_id=org.id, segment="office", city="Dallas", query="q",
                                       max_results=1, status="done"))
    db.commit()
    p = D.plan(db, org, "office", "Dallas", 1, True)
    assert not p["can_run"] and any("Daily limit" in r for r in p["refused_because"])


def test_run_is_god_only_and_plan_is_operator_ok(client, setup):
    db, org, god = setup
    w = _user(db, _org(db, "Other Org", industry="generic"), "org_admin")
    h = {"Authorization": "Bearer %s" % create_access_token(w, db)}
    assert client.post("/god/acquisition/%s/discovery/run" % org.id, json={"segment": "office", "city": "Dallas"},
                       headers=h).status_code == 403
    assert client.post("/acquisition-ops/%s/discovery/plan" % org.id, json={"segment": "office", "city": "Dallas"},
                       headers=h).status_code == 403
    E.grant_operator(db, org, w.email, god)
    r = client.post("/acquisition-ops/%s/discovery/plan" % org.id,
                    json={"segment": "office", "city": "Dallas", "max_results": 5}, headers=h)
    assert r.status_code == 200 and r.json()["can_run"] is True
    assert client.post("/god/acquisition/%s/discovery/run" % org.id, json={"segment": "office", "city": "Dallas"},
                       headers=h).status_code == 403
