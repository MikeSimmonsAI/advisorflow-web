"""Discovery Inbox deeper search, street-level photos, public-record links and
the command-center channel truth (Oct 10 2026)."""
import json

import pytest

from app.models.evosense_models import EvoSenseProperty
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import sandbox_seed as SS
from app.services.evosense import street_view as SV


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:500])
    return r.json()


@pytest.fixture()
def seeded(db_session, sample_org, sample_advisor):
    out = SS.seed_review(db_session, sample_org.id, sample_advisor, replies=True)
    db_session.commit()
    return out


def _props(db, org_id):
    return (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id)
            .order_by(EvoSenseProperty.street_address).all())


@pytest.fixture()
def shaped(db_session, sample_org, seeded):
    """Give three seeded properties known public-record facts."""
    ps = _props(db_session, sample_org.id)
    a, b, c = ps[0], ps[1], ps[2]
    a.city, a.zip_code, a.bedrooms, a.bathrooms, a.square_feet, a.year_built = "Dallas", "75215", 3, 2, 1400, 1955
    a.appraisal_value, a.ownership_years, a.property_type, a.parcel_apn = 120000, 30, "single_family", "00000220945000000"
    b.city, b.zip_code, b.bedrooms, b.bathrooms, b.square_feet, b.year_built = "Fort Worth", "76114", 2, 1, 900, 1990
    b.appraisal_value, b.ownership_years, b.property_type = 260000, 5, "single_family"
    c.city, c.zip_code, c.bedrooms, c.square_feet, c.year_built = "Dallas", "75216", 4, None, None
    c.appraisal_value, c.ownership_years, c.property_type = None, None, "duplex"
    db_session.commit()
    return a, b, c


def _ids(resp):
    return {i["id"] for i in resp["items"]}


def test_range_filters_never_guess_unknown_values(client, auth_headers, shaped):
    a, b, c = shaped
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                      params={"min_value": "100000", "max_value": "150000", "limit": 200}))
    assert a.id in _ids(r) and b.id not in _ids(r) and c.id not in _ids(r)   # c has no value: never matched
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                      params={"min_beds": "3", "limit": 200}))
    assert {a.id, c.id} <= _ids(r) and b.id not in _ids(r)
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                      params={"max_year_built": "1960", "min_owned_years": "20", "limit": 200}))
    assert _ids(r) & {a.id, b.id, c.id} == {a.id}
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                      params={"min_sqft": "1000", "max_sqft": "2000", "limit": 200}))
    assert _ids(r) & {a.id, b.id, c.id} == {a.id}


def test_city_zip_type_parcel_and_missing_filters(client, auth_headers, shaped):
    a, b, c = shaped
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"city": "dallas", "limit": 200}))
    assert {a.id, c.id} <= _ids(r) and b.id not in _ids(r)
    assert "Dallas" in r["cities"] and "duplex" in r["property_types"]
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"zip": "76114, 75216", "limit": 200}))
    assert _ids(r) & {a.id, b.id, c.id} == {b.id, c.id}
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"property_type": "duplex", "limit": 200}))
    assert c.id in _ids(r) and a.id not in _ids(r)
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"q": "220945", "limit": 200}))
    assert _ids(r) == {a.id}
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"missing": "physical", "limit": 200}))
    assert c.id in _ids(r) and a.id not in _ids(r)
    row = next(i for i in ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                                        params={"q": "220945"}))["items"])
    assert row["bedrooms"] == 3 and row["square_feet"] == 1400 and row["year_built"] == 1955
    assert row["ownership_years"] == 30


def test_new_sorts_order_by_the_real_column(client, auth_headers, shaped):
    a, b, c = shaped
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"sort": "value_high", "limit": 200}))
    vals = [i["appraisal"]["value"] for i in r["items"] if i["appraisal"]]
    assert vals == sorted(vals, reverse=True)
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"sort": "owned_longest", "limit": 200}))
    yrs = [i["ownership_years"] for i in r["items"] if i["ownership_years"] is not None]
    assert yrs == sorted(yrs, reverse=True)


def test_all_of_several_signals(client, auth_headers, seeded):
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"limit": 200}))
    sig = r["signal_types"][0]["key"]
    one = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"signal": sig, "limit": 200}))
    two = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                        params={"signal": "%s,%s" % (sig, r["signal_types"][1]["key"]), "limit": 200}))
    assert _ids(two) <= _ids(one)


def test_filters_stay_inside_the_tenant(client, db_session, shaped):
    org = Organization(name="Org B WS", slug="org-b-ws-search", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="b@search.test", password_hash=hash_password("TestPass123!"),
             full_name="B", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    r = client.get("/wholesale/evosense/inbox", headers=h, params={"city": "Dallas", "q": "220945"})
    if r.status_code == 200:
        assert r.json()["total"] == 0
    else:
        assert r.status_code in (402, 403)


# ── photos ──────────────────────────────────────────────────────────────────

class _FakeGoogle:
    def __init__(self, meta_status="OK"):
        self.calls = []
        self.meta_status = meta_status

    def __call__(self, url):
        self.calls.append(url)
        if "/metadata?" in url:
            return 200, json.dumps({"status": self.meta_status, "date": "2024-05"}).encode(), "application/json"
        return 200, b"\xff\xd8\xff\xe0FAKEJPEG", "image/jpeg"


@pytest.fixture(autouse=True)
def _clear_sv_cache():
    SV._CACHE.clear(); SV._META.clear()
    yield
    SV._CACHE.clear(); SV._META.clear()


def _live_prop(db, org_id):
    p = _props(db, org_id)[0]
    p.is_test = False
    p.street_address, p.city, p.state, p.zip_code, p.county = "2304 MACON ST", "Dallas", "TX", "75215", "Dallas"
    p.parcel_apn = "00000220945000000"
    db.commit()
    return p


def test_photo_not_configured_says_so_and_never_calls_google(client, auth_headers, db_session, sample_org,
                                                             seeded, monkeypatch):
    for k in ("GOOGLE_STREET_VIEW_API_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_PLACES_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    fake = _FakeGoogle()
    monkeypatch.setattr(SV, "_get", fake)
    p = _live_prop(db_session, sample_org.id)
    m = ok(client.get("/wholesale/evosense/properties/%s/photo" % p.id, headers=auth_headers))
    assert m["available"] is False and m["reason"] == "not_configured"
    assert m["maps_url"].startswith("https://www.google.com/maps/search/")
    assert client.get("/wholesale/evosense/properties/%s/photo.jpg" % p.id, headers=auth_headers).status_code == 404
    assert fake.calls == []


def test_photo_served_with_source_and_date_and_cached(client, auth_headers, db_session, sample_org,
                                                      seeded, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "k-test")
    fake = _FakeGoogle()
    monkeypatch.setattr(SV, "_get", fake)
    p = _live_prop(db_session, sample_org.id)
    m = ok(client.get("/wholesale/evosense/properties/%s/photo" % p.id, headers=auth_headers))
    assert m["available"] is True and m["date"] == "2024-05" and m["source"] == "Google Street View"
    r = client.get("/wholesale/evosense/properties/%s/photo.jpg" % p.id, headers=auth_headers)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-photo-source"] == "Google Street View" and r.headers["x-photo-date"] == "2024-05"
    client.get("/wholesale/evosense/properties/%s/photo.jpg" % p.id, headers=auth_headers)
    assert sum(1 for c in fake.calls if "/metadata?" not in c) == 1      # second view: no second purchase
    assert "MACON" in fake.calls[0] and "k-test" in fake.calls[0]


def test_no_imagery_is_404_and_sandbox_never_photographed(client, auth_headers, db_session, sample_org,
                                                          seeded, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "k-test")
    fake = _FakeGoogle(meta_status="ZERO_RESULTS")
    monkeypatch.setattr(SV, "_get", fake)
    p = _live_prop(db_session, sample_org.id)
    m = ok(client.get("/wholesale/evosense/properties/%s/photo" % p.id, headers=auth_headers))
    assert m["available"] is False and m["reason"] == "no_imagery"
    assert client.get("/wholesale/evosense/properties/%s/photo.jpg" % p.id, headers=auth_headers).status_code == 404
    sandbox = next(x for x in _props(db_session, sample_org.id) if x.is_test)
    fake.calls.clear()
    m = ok(client.get("/wholesale/evosense/properties/%s/photo" % sandbox.id, headers=auth_headers))
    assert m["reason"] == "sandbox" and fake.calls == []


def test_property_detail_carries_public_links(client, auth_headers, db_session, sample_org, seeded):
    p = _live_prop(db_session, sample_org.id)
    d = ok(client.get("/wholesale/evosense/properties/%s" % p.id, headers=auth_headers))
    assert d["links"]["county_record_url"] == "https://www.dallascad.org/AcctDetailRes.aspx?ID=00000220945000000"
    assert d["links"]["maps_url"] and d["links"]["street_view_url"]


def test_command_center_reports_channel_truth(client, auth_headers, seeded):
    cc = ok(client.get("/wholesale/evosense/command-center", headers=auth_headers))
    ch = cc["controls"]["channels"]
    assert "program" in ch["paused_sms"] and "can_send" in ch["paused_sms"]["program"]
    assert ch["paused_paid_data"]["state"]
