"""Value estimates without surprise bills: RentCast only inside a hard monthly
budget counted BEFORE each request, cached, labelled as an estimate with
LISTING comparables; plus a free tax-value figure from the same ZIP. RentCast
is never really called here."""
from datetime import datetime

import pytest

from app.models.evosense_models import EvoSenseProperty
from app.services.evosense import value_lookup as VL
from app.services.evosense.sources import rentcast as RC

BODY = {"price": 245000, "priceRangeLow": 220000, "priceRangeHigh": 270000,
        "comparables": [
            {"formattedAddress": "1 A St, Dallas, TX 75203", "price": 239000, "status": "Active",
             "listingType": "Standard", "listedDate": "2026-09-01T00:00:00Z", "distance": 0.3,
             "squareFootage": 1200, "correlation": 0.97},
            {"formattedAddress": "2 B St, Dallas, TX 75203", "price": 251000, "status": "Inactive",
             "listingType": "Standard", "listedDate": "2026-05-01T00:00:00Z",
             "removedDate": "2026-07-01T00:00:00Z", "distance": 0.5, "correlation": 0.9}]}


@pytest.fixture
def prop(db_session, sample_org):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="2620 Kirby St", city="Dallas",
                         state="TX", zip_code="75203", county="Dallas", property_type="single_family",
                         square_feet=1100, opportunity_score=70)
    db_session.add(p)
    db_session.commit()
    return p


def test_the_budget_is_counted_before_any_request(db_session, monkeypatch):
    monkeypatch.setenv("RENTCAST_MONTHLY_LIMIT", "2")
    assert RC.reserve(db_session) and RC.reserve(db_session)
    assert RC.reserve(db_session) is False
    u = RC.usage(db_session)
    assert u["used"] == 2 and u["limit"] == 2 and u["left"] == 0
    monkeypatch.setenv("RENTCAST_MONTHLY_LIMIT", "500")
    assert RC.monthly_limit() == RC.HARD_CEILING                      # never above 45, whatever is set
    monkeypatch.setenv("RENTCAST_MONTHLY_LIMIT", "0")
    assert RC.reserve(db_session) is False


def test_estimate_is_labelled_and_cached(db_session, prop, monkeypatch):
    calls = []
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key")
    monkeypatch.setattr(RC, "fetch", lambda *a, **k: calls.append(a) or BODY)
    out = VL.value_property(db_session, prop)
    assert out["ok"] and len(calls) == 1
    d = out["detail"]
    assert d["estimate"] == 245000 and "ESTIMATE" in d["truth"] and "not a sale price" in d["truth"]
    assert d["comparables"][0]["label"].startswith("Listed comparable (asking price)")
    assert "sale price not confirmed" in d["comparables"][1]["label"]
    db_session.refresh(prop)
    assert prop.estimated_value == 245000 and prop.estimated_value_source.startswith("rentcast")
    again = VL.value_property(db_session, prop)
    assert again["cached"] and len(calls) == 1                         # cached: no second request
    assert RC.usage(db_session)["used"] == 1


def test_nothing_is_sent_when_not_connected_or_out_of_budget(db_session, prop, monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    assert "not connected" in VL.value_property(db_session, prop)["reason"]
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key")
    monkeypatch.setenv("RENTCAST_MONTHLY_LIMIT", "0")
    monkeypatch.setattr(RC, "fetch", lambda *a, **k: pytest.fail("must not be called"))
    assert "used up" in VL.value_property(db_session, prop)["reason"]
    prop.opportunity_score = 50
    monkeypatch.setenv("RENTCAST_MONTHLY_LIMIT", "10")
    assert "65" in VL.value_property(db_session, prop, auto=True)["reason"]


def test_free_tax_value_figure_needs_five_neighbours(db_session, sample_org, prop):
    assert VL.neighborhood_tax_estimate(db_session, prop) is None
    for i, (v, sq) in enumerate([(200000, 1000), (220000, 1100), (180000, 900), (300000, 1500), (210000, 1000)]):
        db_session.add(EvoSenseProperty(organization_id=sample_org.id, street_address="%s Elm St" % i,
                                        city="Dallas", state="TX", zip_code="75203", appraisal_value=v,
                                        square_feet=sq))
    db_session.commit()
    est = VL.neighborhood_tax_estimate(db_session, prop)
    assert est["sample"] == 5 and est["per_sq_ft"] == 200.0 and est["value"] == 220000
    assert "not a sale price" in est["truth"]


def test_value_route_refuses_when_not_connected(client, auth_headers, prop, monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    r = client.post("/wholesale/evosense/properties/%s/valuation" % prop.id, headers=auth_headers)
    assert r.status_code == 409 and "not connected" in r.json()["detail"]
    assert client.get("/wholesale/evosense/valuation/usage", headers=auth_headers).json()["limit"] == 40
