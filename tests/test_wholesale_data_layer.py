"""Unified data view: every source reports the same metadata; paid sources say they need
approval; a deal's numbers each carry a source. Read-only - no fetch, no spend."""
ES = "/wholesale/evosense"
KEYS = ("key", "label", "kind", "state", "confidence", "cost_class", "last_success_at", "error_state", "retry", "caching")


def ok(r):
    assert r.status_code in (200, 201), r.text[:300]
    return r.json()


def test_every_source_reports_the_same_metadata(client, auth_headers):
    d = ok(client.get("/wholesale/data-sources", headers=auth_headers))
    assert d["sources"] and d["precedence"][0]["rank"] == 100 and "never silently overwritten" in d["rule"]
    for s in d["sources"]:
        missing = [k for k in KEYS if k not in s]
        assert not missing, (s.get("key"), missing)
        if s["cost_class"].startswith("paid"):
            assert s["needs_approval_to_spend"] is True
    assert {r["key"] for r in d["buyer_rolls"]} == {"county_roll:dallas", "county_roll:tarrant"}


def test_deal_provenance_names_a_source_for_each_number(client, auth_headers):
    H = auth_headers
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "9 Provenance Pl", "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "square_feet": 1300, "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Provenance Test Owner"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H, json={"arv": 200000}))
    p = ok(client.get("/wholesale/deals/%s/provenance" % deal_id, headers=H))
    f = {x["fact"]: x for x in p["facts"]}
    assert f["After-repair value"]["value"] == 200000 and f["After-repair value"]["source"] == "manual"
    assert f["After-repair value"]["verified"] is False            # a typed ARV is never "verified"
    assert f["Comparable sales"]["value"] == 0
    assert f["Owner of record"]["value"] == "Provenance Test Owner"
