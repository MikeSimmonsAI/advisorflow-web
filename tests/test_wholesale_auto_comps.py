"""Automatic comparable sales: every source labelled for what it is, nothing priced
that Texas does not publish, a listing never becomes a sale, and the ARV only
moves when a person supplies a sourced price."""
import json
from datetime import date, timedelta

from app.models.evosense_models import EvoSenseProperty
from app.models.wholesale_models import WholesaleComp, WholesaleEvent
from app.services import wholesale_auto_comps as AC

ES = "/wholesale/evosense"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:400])
    return r.json()


def _deal(client, H, db_session, sample_org):
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "100 Comp Test Ln", "city": "Dallas", "state": "TX", "zip_code": "75299", "county": "Dallas",
        "property_type": "single_family", "square_feet": 1500, "year_built": 1985, "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Comp Test Owner"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    subj = db_session.query(EvoSenseProperty).filter(EvoSenseProperty.id == pid).first()
    subj.latitude, subj.longitude = 32.7000, -96.8000
    # RentCast-style listings already cached on the subject (no API call in tests)
    subj.valuation_detail = json.dumps({"source": "RentCast automated valuation", "comparables": [
        {"address": "120 Comp Test Ln, Dallas, TX 75299", "label": "Listed comparable (asking price)",
         "price": 260000, "status": "Active", "listed": (date.today() - timedelta(days=20)).isoformat(),
         "removed": None, "distance_miles": 0.2, "square_feet": 1480, "bedrooms": 3, "bathrooms": 2,
         "year_built": 1984}]})
    # county deed records in the same ZIP: three similar recent, one stale, one condo, one far
    rows = [("110 Comp Test Ln", 1450, 1983, 40, 32.7010, -96.8010, "single_family"),
            ("130 Comp Test Ln", 1600, 1990, 90, 32.7020, -96.8020, "single_family"),
            ("140 Comp Test Ln", 1520, 1986, 150, 32.7030, -96.8000, "single_family"),
            ("150 Comp Test Ln", 1500, 1985, 800, 32.7040, -96.8000, "single_family"),
            ("160 Comp Test Ln", 1500, 1985, 30, 32.7005, -96.8005, "condo"),
            ("170 Far Away Rd", 1500, 1985, 30, 32.8000, -96.9000, "single_family")]
    for addr, sqft, yb, days, lat, lon, pt in rows:
        db_session.add(EvoSenseProperty(organization_id=sample_org.id, street_address=addr, city="Dallas", state="TX",
                                        zip_code="75299", county="Dallas", square_feet=sqft, year_built=yb,
                                        property_type=pt, latitude=lat, longitude=lon, is_test=True,
                                        last_deed_transfer_date=date.today() - timedelta(days=days)))
    db_session.commit()
    return deal_id


def test_find_comps_labels_every_source_and_never_invents_a_price(client, auth_headers, db_session, sample_org):
    H = auth_headers
    deal_id = _deal(client, H, db_session, sample_org)
    r = ok(client.post("/wholesale/deals/%s/comps/find" % deal_id, headers=H, json={}))
    src = {s["key"]: s for s in r["sources"]}
    assert src["county_transfers"]["available"] and src["county_transfers"]["found"] == 5   # the 800-day-old deed is outside the window
    assert src["rentcast_listings"]["available"] and src["rentcast_listings"]["found"] == 1
    assert not src["johnson_subdivision"]["available"] and "Johnson" in src["johnson_subdivision"]["why"]
    assert not src["reapi_comps"]["available"] and "REAPI_API_KEY" in src["reapi_comps"]["why"]
    assert "truth gate" in src["reapi_comps"]["why"]
    by = {c["address"]: c for c in r["candidates"]}
    t = by["110 Comp Test Ln"]
    assert t["price_kind"] == AC.TRANSFER_NO_PRICE and t["price"] is None and t["needs_price"] and not t["usable_for_arv"]
    assert t["distance_miles"] is not None and t["distance_miles"] < 0.5 and t["why"]
    assert "150 Comp Test Ln" not in by
    assert "condo" in " ".join(by["160 Comp Test Ln"]["excluded"])
    assert "miles away" in " ".join(by["170 Far Away Rd"]["excluded"])
    lst = by["120 Comp Test Ln"]
    assert lst["price_kind"] == AC.LIST and lst["price"] == 260000 and not lst["usable_for_arv"]
    assert r["counts"]["closed_sales_usable"] == 0 and r["counts"]["transfers_need_price"] == 3
    assert "Texas does not publish sale prices" in r["gap"] and "REAPI_API_KEY" in r["gap"]
    # nothing was written to the deal by searching
    assert db_session.query(WholesaleComp).filter(WholesaleComp.deal_id == deal_id).count() == 0
    uw = ok(client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=H))
    assert uw["arv"]["value"] is None


def test_adding_needs_a_sourced_price_and_a_listing_is_refused(client, auth_headers, db_session, sample_org):
    H = auth_headers
    deal_id = _deal(client, H, db_session, sample_org)
    r = ok(client.post("/wholesale/deals/%s/comps/find" % deal_id, headers=H, json={}))
    by = {c["address"]: c for c in r["candidates"]}
    url = "/wholesale/deals/%s/comps/find/add" % deal_id
    resp = client.post(url, headers=H, json={"key": by["120 Comp Test Ln"]["key"], "sale_price": 250000,
                                             "source_reference": "MLS 123"})
    assert resp.status_code == 409 and "not a sale price" in resp.json()["detail"]
    t = by["110 Comp Test Ln"]["key"]
    assert client.post(url, headers=H, json={"key": t}).status_code == 422
    assert client.post(url, headers=H, json={"key": t, "sale_price": 210000}).status_code == 422
    assert client.post(url, headers=H, json={"key": "made|up|key", "sale_price": 1,
                                             "source_reference": "x123"}).status_code == 404
    for addr, price in (("110 Comp Test Ln", 205000), ("130 Comp Test Ln", 222000), ("140 Comp Test Ln", 212000)):
        ok(client.post(url, headers=H, json={"key": by[addr]["key"], "sale_price": price,
                                             "source_reference": "MLS 9%s" % price}))
    comps = db_session.query(WholesaleComp).filter(WholesaleComp.deal_id == deal_id).all()
    assert len(comps) == 3 and all(c.verification_state == "manual" and c.source_reference for c in comps)
    prov = json.loads(comps[0].provenance)
    assert prov["found_by"] == "county_transfers" and prov["evidence_kind"] == AC.TRANSFER_NO_PRICE
    assert prov["price_entered_by_person"] is True
    assert db_session.query(WholesaleEvent).filter(WholesaleEvent.deal_id == deal_id,
                                                   WholesaleEvent.action == "comp.added_from_search").count() == 3
    # a manual adjustment keeps its before / after in the audit trail
    ok(client.patch("/wholesale/comps/%s" % comps[0].id, headers=H, json={"sale_price": 207500}))
    ev = (db_session.query(WholesaleEvent).filter(WholesaleEvent.deal_id == deal_id,
                                                  WholesaleEvent.action == "comp.updated")
          .order_by(WholesaleEvent.created_at.desc()).first())
    assert "sale_price" in ev.summary and "205000" in ev.before_state and "207500" in ev.after_state


def test_ranking_explains_itself():
    subj = {"square_feet": 1500, "year_built": 1985, "bedrooms": 3, "property_type": "single_family"}
    c = {"distance_miles": 0.3, "square_feet": 1450, "year_built": 1983, "bedrooms": 3,
         "property_type": "single_family", "date": (date.today() - timedelta(days=40)).isoformat(),
         "date_kind": "deed recorded", "price_kind": AC.CLOSED_SALE}
    r = AC.rank(subj, c, dict(AC.DEFAULTS))
    assert r["usable_for_arv"] and r["score"] == 100 and len(r["why"]) >= 4
    c2 = dict(c, square_feet=3000)
    assert "living area differs" in AC.rank(subj, c2, dict(AC.DEFAULTS))["excluded"][0]


def test_another_company_cannot_search_or_add(client, auth_headers, db_session, sample_org):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    deal_id = _deal(client, auth_headers, db_session, sample_org)
    org = Organization(name="Other Comps Co", slug="other-comps", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="comps.other@example.test", full_name="O", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    assert client.post("/wholesale/deals/%s/comps/find" % deal_id, headers=h, json={}).status_code in (403, 404)
    assert client.post("/wholesale/deals/%s/comps/find/add" % deal_id, headers=h,
                       json={"key": "x", "sale_price": 1, "source_reference": "abc"}).status_code in (403, 404)
