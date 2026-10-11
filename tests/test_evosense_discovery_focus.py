"""A ready-made search reads only the discovery sources that fit it: "Behind
on Taxes" reads tax sources, not owner records. Searches that never set a
focus (New Check Out) keep reading every connected discovery source."""
from types import SimpleNamespace

from app.services.evosense import common as C
from app.services.evosense import hunt as HU
from app.services.evosense import providers as PV
from app.services.evosense import strategy as ST


def _fake_route(monkeypatch):
    prov = {C.TAX: ["tarrant_tax_roll", "dallas_trw", "lgbs_tax_sales"],
            C.PROPERTY_SEARCH: ["dcad", "collin_cad", "denton_gis"],
            C.CODE_VIOLATION: ["fw_open_cases"]}

    def route(db, org_id, cap):
        return [(SimpleNamespace(key=k, discovery=True), None, 0) for k in prov.get(cap, [])]
    monkeypatch.setattr(PV, "route", route)


def test_focus_limits_discovery(monkeypatch):
    _fake_route(monkeypatch)
    keys = lambda focus: [p.key for _c, p, _cfg in HU._discovery_routes(None, "o", focus)]
    assert keys(None) == ["dcad", "collin_cad", "denton_gis", "tarrant_tax_roll", "dallas_trw",
                          "lgbs_tax_sales", "fw_open_cases"]
    assert keys(["TAX"]) == ["tarrant_tax_roll", "dallas_trw", "lgbs_tax_sales"]
    assert keys(["collin_cad", "denton_gis", "TAX"]) == ["collin_cad", "denton_gis", "tarrant_tax_roll",
                                                         "dallas_trw", "lgbs_tax_sales"]
    assert keys(["FORECLOSURE"]) == []


def test_focus_read_from_the_strategy():
    assert HU.discovery_focus(SimpleNamespace(provider_preferences=None)) == []
    assert HU.discovery_focus(SimpleNamespace(provider_preferences='{"discovery": ["TAX"]}')) == ["TAX"]
    assert HU.discovery_focus(SimpleNamespace(provider_preferences='{"contact_enrichment": {}}')) == []


def test_every_starter_names_its_sources():
    for key in ST.STARTERS:
        v = ST.starter_values(key)
        assert v["provider_preferences"]["discovery"], key
        clean, problems = ST.validate(v)
        assert not problems and clean["provider_preferences"]["discovery"]
    assert ST.starter_values("behind_on_taxes")["provider_preferences"]["discovery"] == ["TAX"]


def test_starter_api_stores_the_focus(client, auth_headers):
    r = client.post("/wholesale/evosense/strategy-starters", headers=auth_headers, json={"key": "behind_on_taxes"})
    assert r.status_code in (200, 201), r.text[:300]
    s = r.json()
    assert s["provider_preferences"]["discovery"] == ["TAX"]
