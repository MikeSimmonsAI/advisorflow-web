"""Phone/email lookup for cash buyers: shows the cost first, charges only with
the person's approval, never overwrites what a person typed, and never fills
in someone else found at a person-buyer's address."""
import json
from datetime import datetime

import pytest

from app.services import wholesale_buyer_finder as F
from app.services import wholesale_buyer_trace as BT
from app.services import wholesale_enrichment as WE

TODAY = datetime(2026, 10, 10)
REAL_CAP_CHECK = BT.cap_refusal


def rows():
    out = []
    def add(owner, n, recent, mail):
        for i in range(n):
            deed = datetime(2026, 3, 1) if i < recent else datetime(2015, 1, 1)
            out.append(("A%s%d" % (owner[:3], i), owner, mail[0], mail[1], mail[2], mail[3],
                        "%d MAIN ST" % (100 + i), "Dallas", "75216", deed, "house"))
    add("OAK CLIFF HOLDINGS LLC", 5, 2, ("4736 TRAIL LAKE DR", "Fort Worth", "TX", "76133"))
    add("JOHN Q INVESTOR", 4, 1, ("12 ELM ST", "Dallas", "TX", "75215"))
    return out


class FakeTracerfy:
    calls = []

    def enrich(self, data):
        FakeTracerfy.calls.append(data)
        if data.owner_name == "OAK CLIFF HOLDINGS LLC":
            return WE.EnrichmentResult(
                status=WE.STATUS_SUCCEEDED, provider="tracerfy", owner_name="RICK CASTRO",
                phones=[WE.EnrichmentPhone(number="8175550100", phone_type="landline", dnc_flag=False),
                        WE.EnrichmentPhone(number="8175550199", phone_type="mobile", dnc_flag=False)],
                emails=[WE.EnrichmentEmail(address="rick@example.com")],
                match_evidence={"name_match": None}, billable=True, cost_cents=10)
        return WE.EnrichmentResult(
            status=WE.STATUS_SUCCEEDED, provider="tracerfy", owner_name="JANE DOE",
            phones=[WE.EnrichmentPhone(number="2145550123", phone_type="mobile")],
            match_evidence={"name_match": "none"}, billable=True, cost_cents=10)


@pytest.fixture
def imported(client, auth_headers, tmp_path, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path))
    res = F.scan(lambda: iter(rows()), county="tarrant", today=TODAY)
    with open(F.result_path("tarrant"), "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    keys = [b["key"] for b in res["buyers"]]
    r = client.post("/wholesale/buyers/finder/import", headers=auth_headers,
                    json={"county": "tarrant", "keys": keys})
    assert r.json()["created"] == 2, r.text
    FakeTracerfy.calls = []
    monkeypatch.setattr(BT, "provider_factory", FakeTracerfy)
    # The organization's paid-lookup caps start at 0; tests of the lookup itself lift them.
    monkeypatch.setattr(BT, "cap_refusal", lambda *a, **k: None)
    return res


def _buyers(client, auth_headers):
    data = client.get("/wholesale/buyers", headers=auth_headers).json()
    return {(b.get("company_name") or b.get("contact_name")): b for b in data["buyers"]}


def test_mailing_address_is_read_from_the_county_note():
    from app.models.wholesale_models import WholesaleBuyer
    b = WholesaleBuyer(notes="Mailing address (county record): PO BOX 9, STE 4, Fort Worth, TX 76133-1234\nx")
    assert BT.mailing(b) == {"street": "PO BOX 9, STE 4", "city": "Fort Worth", "state": "TX", "zip": "76133"}
    assert BT.mailing(WholesaleBuyer(notes="nothing here")) is None


def test_estimate_then_run_fills_company_and_refuses_wrong_person(client, auth_headers, imported, monkeypatch):
    est = client.post("/wholesale/buyers/skip-trace/estimate", headers=auth_headers, json={}).json()
    assert est["count"] == 2 and est["max_cost_cents"] == 20 and est["configured"] is False

    ids = est["buyer_ids"]
    r = client.post("/wholesale/buyers/skip-trace/run", headers=auth_headers,
                    json={"buyer_ids": ids, "max_cost_cents": 20})
    assert r.status_code == 400 and "TRACERFY_API_TOKEN" in r.json()["detail"]   # not connected
    assert FakeTracerfy.calls == []

    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token")
    r = client.post("/wholesale/buyers/skip-trace/run", headers=auth_headers,
                    json={"buyer_ids": ids, "max_cost_cents": 10})
    assert r.status_code == 409                                                  # more than approved
    assert FakeTracerfy.calls == []

    r = client.post("/wholesale/buyers/skip-trace/run", headers=auth_headers,
                    json={"buyer_ids": ids, "max_cost_cents": 20})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["looked_up"] == 2 and out["found"] == 1 and out["cost_cents"] == 20
    assert FakeTracerfy.calls[0].street_address in ("4736 TRAIL LAKE DR", "12 ELM ST")

    got = _buyers(client, auth_headers)
    oak = got["OAK CLIFF HOLDINGS LLC"]
    assert oak["phone"] == "(817) 555-0199"            # the mobile, not the landline
    assert oak["email"] == "rick@example.com" and oak["contact_name"] == "Rick Castro"
    assert BT.NO_CONTACT_LINE not in oak["notes"] and "Phones:" in oak["notes"]
    john = got["JOHN Q INVESTOR"]
    assert not john.get("phone") and not john.get("email")       # Jane Doe is not John
    assert "not JOHN Q INVESTOR" in john["notes"]

    again = client.post("/wholesale/buyers/skip-trace/estimate", headers=auth_headers, json={}).json()
    assert again["count"] == 0 and again["skipped"]["already_looked_up"] == 1
    assert again["skipped"]["has_phone_and_email"] == 1


def test_lookup_never_overwrites_what_a_person_typed(client, auth_headers, imported, monkeypatch):
    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token")
    oak = _buyers(client, auth_headers)["OAK CLIFF HOLDINGS LLC"]
    assert client.patch("/wholesale/buyers/%s" % oak["id"], headers=auth_headers,
                        json={"phone": "(214) 000-1111"}).status_code == 200
    r = client.post("/wholesale/buyers/skip-trace/run", headers=auth_headers,
                    json={"buyer_ids": [oak["id"]], "max_cost_cents": 10})
    assert r.status_code == 200, r.text
    oak = _buyers(client, auth_headers)["OAK CLIFF HOLDINGS LLC"]
    assert oak["phone"] == "(214) 000-1111" and oak["email"] == "rick@example.com"


def test_run_refuses_big_batches(client, auth_headers, imported, monkeypatch):
    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token")
    r = client.post("/wholesale/buyers/skip-trace/run", headers=auth_headers,
                    json={"buyer_ids": ["x"] * 26, "max_cost_cents": 1000})
    assert r.status_code == 400


def test_paid_lookup_caps_still_apply(client, auth_headers, imported, monkeypatch):
    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token")
    monkeypatch.setattr(BT, "cap_refusal", REAL_CAP_CHECK)
    est = client.post("/wholesale/buyers/skip-trace/estimate", headers=auth_headers, json={}).json()
    assert est["cap_refusal"] and "cap" in est["cap_refusal"]
    r = client.post("/wholesale/buyers/skip-trace/run", headers=auth_headers,
                    json={"buyer_ids": est["buyer_ids"], "max_cost_cents": 20})
    assert r.status_code == 429 and FakeTracerfy.calls == []


def test_estimate_checks_whole_list_against_daily_caps_not_the_per_run_limit():
    class S:
        enrichment_max_records_per_run = 25
        enrichment_daily_cap = None
        enrichment_monthly_cap = None
    assert BT.cap_refusal(None, "org", S(), 97, whole_list=True) is None
    assert "per run" in BT.cap_refusal(None, "org", S(), 97)
