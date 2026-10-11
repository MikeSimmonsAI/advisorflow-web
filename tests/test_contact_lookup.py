"""Get phones & emails, on every list that holds people (buyers, funding
partners, property owners): only the records a person ticked, cost shown and
approved first, caps applied, Do Not Call numbers never filled in, a person's
own entry never overwritten, and someone else's record never touched."""
import json
from datetime import datetime

import pytest

from app.services import contact_lookup as CL
from app.services import wholesale_buyer_finder as F
from app.services import wholesale_enrichment as WE

TODAY = datetime(2026, 10, 10)
REAL_CAP_CHECK = CL.cap_refusal


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


class FakeTracerfy(WE.EnrichmentProvider):
    key = "tracerfy"
    billable = True
    cost_per_find_cents = 10
    calls = []
    on = True

    def is_configured(self):
        return FakeTracerfy.on

    def lookup(self, data):
        FakeTracerfy.calls.append(data)
        hit = dict(status=WE.STATUS_SUCCEEDED, provider="tracerfy", billable=True, cost_cents=10)
        if data.owner_name == "JOHN Q INVESTOR":
            return WE.EnrichmentResult(owner_name="JANE DOE", match_evidence={"name_match": "none"},
                                       phones=[WE.EnrichmentPhone(number="2145550123")], **hit)
        return WE.EnrichmentResult(
            owner_name="RICK CASTRO", match_evidence={"name_match": None, "dnc_numbers": ["8175550111"]},
            phones=[WE.EnrichmentPhone(number="8175550199", phone_type="mobile")],
            emails=["rick@example.com"], **hit)


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeTracerfy.calls, FakeTracerfy.on = [], True
    monkeypatch.setattr(CL, "provider_factory", FakeTracerfy)
    # The organization's paid-lookup caps start at 0; tests of the lookup itself lift them.
    monkeypatch.setattr(CL, "cap_refusal", lambda *a, **k: None)
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def rows():
    out = []
    def add(owner, n, mail):
        for i in range(n):
            out.append(("A%s%d" % (owner[:3], i), owner, mail[0], mail[1], mail[2], mail[3],
                        "%d MAIN ST" % (100 + i), "Dallas", "75216", datetime(2026, 3, 1), "house"))
    add("OAK CLIFF HOLDINGS LLC", 5, ("4736 TRAIL LAKE DR", "Fort Worth", "TX", "76133"))
    add("JOHN Q INVESTOR", 4, ("12 ELM ST", "Dallas", "TX", "75215"))
    add("HAZEL HOMES LLC", 3, ("9 OAK AVE", "Dallas", "TX", "75201"))
    return out


@pytest.fixture
def buyers(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path))
    res = F.scan(lambda: iter(rows()), county="tarrant", today=TODAY)
    with open(F.result_path("tarrant"), "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    ok(client.post("/wholesale/buyers/finder/import", headers=auth_headers,
                   json={"county": "tarrant", "keys": [b["key"] for b in res["buyers"]]}))
    data = ok(client.get("/wholesale/buyers", headers=auth_headers))
    return {(b.get("company_name") or b.get("contact_name")): b for b in data["buyers"]}


def _buyer(client, auth_headers, name):
    data = ok(client.get("/wholesale/buyers", headers=auth_headers))
    return next(b for b in data["buyers"] if (b.get("company_name") or b.get("contact_name")) == name)


def test_parse_address():
    assert CL.parse_address("PO BOX 9, STE 4, Fort Worth, TX 76133-1234") == {
        "street": "PO BOX 9, STE 4", "city": "Fort Worth", "state": "TX", "zip": "76133"}
    assert CL.parse_address("just a street") is None


def test_only_the_ticked_buyers_are_looked_up(client, auth_headers, buyers):
    oak, john = buyers["OAK CLIFF HOLDINGS LLC"], buyers["JOHN Q INVESTOR"]
    picked = [oak["id"], john["id"]]                       # Hazel is NOT ticked
    est = ok(client.post("/wholesale/contact-lookup/estimate", headers=auth_headers,
                         json={"kind": "buyer", "ids": picked}))
    assert est["count"] == 2 and est["max_cost_cents"] == 20 and len(est["rows"]) == 2

    r = client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                    json={"kind": "buyer", "ids": picked, "max_cost_cents": 10})
    assert r.status_code == 409 and FakeTracerfy.calls == []      # more than approved

    out = ok(client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                         json={"kind": "buyer", "ids": picked, "max_cost_cents": 20}))
    assert out["looked_up"] == 2 and out["found"] == 1 and out["cost_cents"] == 20
    assert {c.owner_name for c in FakeTracerfy.calls} == {"OAK CLIFF HOLDINGS LLC", "JOHN Q INVESTOR"}

    oak = _buyer(client, auth_headers, "OAK CLIFF HOLDINGS LLC")
    assert oak["phone"] == "(817) 555-0199" and oak["email"] == "rick@example.com"
    assert oak["contact_name"] == "Rick Castro"
    assert "Do Not Call" in oak["notes"] and "(817) 555-0111" in oak["notes"]
    assert CL.NO_CONTACT_LINE not in oak["notes"]
    john = _buyer(client, auth_headers, "JOHN Q INVESTOR")
    assert not john.get("phone") and "not JOHN Q INVESTOR" in john["notes"]   # Jane is not John
    hazel = _buyer(client, auth_headers, "HAZEL HOMES LLC")
    assert not hazel.get("phone") and "lookup" not in (hazel.get("notes") or "")

    again = ok(client.post("/wholesale/contact-lookup/estimate", headers=auth_headers,
                           json={"kind": "buyer", "ids": picked}))
    assert again["count"] == 0
    assert {r["status"] for r in again["rows"]} == {"has_both", "looked_up"}


def test_not_connected_and_caps(client, auth_headers, buyers, monkeypatch):
    ids = [buyers["HAZEL HOMES LLC"]["id"]]
    FakeTracerfy.on = False
    r = client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                    json={"kind": "buyer", "ids": ids, "max_cost_cents": 10})
    assert r.status_code == 400 and "TRACERFY_API_TOKEN" in r.json()["detail"]
    FakeTracerfy.on = True
    monkeypatch.setattr(CL, "cap_refusal", REAL_CAP_CHECK)          # caps are 0 out of the box
    est = ok(client.post("/wholesale/contact-lookup/estimate", headers=auth_headers,
                         json={"kind": "buyer", "ids": ids}))
    assert est["cap_refusal"] and "cap" in est["cap_refusal"]
    r = client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                    json={"kind": "buyer", "ids": ids, "max_cost_cents": 10})
    assert r.status_code == 429 and FakeTracerfy.calls == []
    r = client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                    json={"kind": "buyer", "ids": ["x"] * 26, "max_cost_cents": 1000})
    assert r.status_code == 400


def test_whole_selection_is_checked_against_daily_caps_not_the_per_run_limit():
    class S:
        enrichment_max_records_per_run = 25
        enrichment_daily_cap = None
        enrichment_monthly_cap = None
    assert REAL_CAP_CHECK(None, "org", S(), 97, whole_list=True) is None
    assert "per run" in REAL_CAP_CHECK(None, "org", S(), 97)


def test_a_person_entry_is_never_overwritten(client, auth_headers, buyers):
    oak = buyers["OAK CLIFF HOLDINGS LLC"]
    ok(client.patch("/wholesale/buyers/%s" % oak["id"], headers=auth_headers, json={"phone": "(214) 000-1111"}))
    ok(client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                   json={"kind": "buyer", "ids": [oak["id"]], "max_cost_cents": 10}))
    oak = _buyer(client, auth_headers, "OAK CLIFF HOLDINGS LLC")
    assert oak["phone"] == "(214) 000-1111" and oak["email"] == "rick@example.com"


def test_funding_partner_needs_an_address_then_gets_looked_up(client, auth_headers):
    p = ok(client.post("/wholesale/funding/partners", headers=auth_headers,
                       json={"name": "Lone Star Lending LLC", "email": "deals@lonestar.test"}))
    est = ok(client.post("/wholesale/contact-lookup/estimate", headers=auth_headers,
                         json={"kind": "funding_partner", "ids": [p["id"]]}))
    assert est["rows"][0]["status"] == "no_address" and est["count"] == 0
    bad = client.post("/wholesale/contact-lookup/address", headers=auth_headers,
                      json={"kind": "funding_partner", "id": p["id"], "address": "somewhere"})
    assert bad.status_code == 400
    row = ok(client.post("/wholesale/contact-lookup/address", headers=auth_headers,
                         json={"kind": "funding_partner", "id": p["id"],
                               "address": "100 Main St, Dallas, TX 75201"}))
    assert row["status"] == "ready" and row["address"].startswith("100 Main St, Dallas, TX")
    out = ok(client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                         json={"kind": "funding_partner", "ids": [p["id"]], "max_cost_cents": 10}))
    assert out["found"] == 1
    got = next(x for x in ok(client.get("/wholesale/funding/partners", headers=auth_headers))["partners"]
               if x["id"] == p["id"])
    assert got["phone"] == "(817) 555-0199" and got["email"] == "deals@lonestar.test"   # email kept
    assert got["contact_person"] == "Rick Castro"


def test_property_owner_lookup_writes_the_seller(client, auth_headers, db_session):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "2620 Kirby St", "city": "Dallas", "state": "TX",
                                "county": "Dallas", "zip_code": "75203", "owner_name": "Elizabeth Bryant"}))
    pid = prop.get("id") or prop["property"]["id"]
    est = ok(client.post("/wholesale/contact-lookup/estimate", headers=auth_headers,
                         json={"kind": "property", "ids": [pid]}))
    assert est["count"] == 1
    out = ok(client.post("/wholesale/contact-lookup/run", headers=auth_headers,
                         json={"kind": "property", "ids": [pid], "max_cost_cents": 10}))
    assert out["looked_up"] == 1 and out["found"] == 1 and out["cost_cents"] == 10
    from app.models.models import Lead
    from app.models.wholesale_models import WholesaleSellerProfile
    prof = db_session.query(WholesaleSellerProfile).filter(WholesaleSellerProfile.property_id == pid).one()
    lead = db_session.query(Lead).filter(Lead.id == prof.lead_id).one()
    assert lead.email == "rick@example.com" and "8175550199" in (lead.phone or "").replace("+1", "")
    again = ok(client.post("/wholesale/contact-lookup/estimate", headers=auth_headers,
                           json={"kind": "property", "ids": [pid]}))
    assert again["rows"][0]["status"] in ("has_both", "looked_up")


def test_another_organizations_records_are_not_found(client, auth_headers, buyers, db_session):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    org = Organization(name="Other Co", slug="other-co-cl", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="other@cl.test", password_hash=hash_password("TestPass123!"),
             full_name="Other", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    theirs = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    oak = buyers["OAK CLIFF HOLDINGS LLC"]
    r = client.post("/wholesale/contact-lookup/estimate", headers=theirs, json={"kind": "buyer", "ids": [oak["id"]]})
    if r.status_code == 200:
        assert r.json()["rows"][0]["status"] == "not_found"
    r = client.post("/wholesale/contact-lookup/address", headers=theirs,
                    json={"kind": "buyer", "id": oak["id"], "address": "1 A St, Dallas, TX 75201"})
    assert r.status_code in (403, 404)
    r = client.post("/wholesale/contact-lookup/run", headers=theirs,
                    json={"kind": "buyer", "ids": [oak["id"]], "max_cost_cents": 10})
    assert r.status_code in (200, 403) and FakeTracerfy.calls == []
    if r.status_code == 200:
        assert r.json()["looked_up"] == 0


def test_tracerfy_provider_drops_do_not_call_numbers(monkeypatch):
    from app.services.evosense import vendors as V
    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token")
    monkeypatch.setattr(V.TracerfySkipTrace, "enrich", lambda self, data: WE.EnrichmentResult(
        status=WE.STATUS_SUCCEEDED, provider="tracerfy", billable=True, cost_cents=10,
        phones=[WE.EnrichmentPhone(number="1111111111", phone_type="landline", dnc_flag=False),
                WE.EnrichmentPhone(number="2222222222", phone_type="mobile", dnc_flag=True),
                WE.EnrichmentPhone(number="3333333333", phone_type="mobile", dnc_flag=False)],
        emails=[WE.EnrichmentEmail(address="a@b.test")]))
    res = WE.PROVIDERS["tracerfy"].lookup(WE.EnrichmentInput(street_address="1 A St", city="Dallas", state="TX"))
    assert [p.number for p in res.phones] == ["3333333333", "1111111111"]   # mobile first, DNC gone
    assert res.match_evidence["dnc_numbers"] == ["2222222222"] and res.emails == ["a@b.test"]
    monkeypatch.setattr(V.TracerfySkipTrace, "enrich", lambda self, data: (_ for _ in ()).throw(RuntimeError("boom")))
    assert WE.PROVIDERS["tracerfy"].lookup(WE.EnrichmentInput()).status == WE.STATUS_FAILED
