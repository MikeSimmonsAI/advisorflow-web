# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST [parity] (#272): every Wholesale module answers for a
BookaBoost workspace through its REAL endpoints, on the one shared engine, and
never shows the other workspace's records.

For each module of the parity matrix, the BookaBoost admin and the EvoSys
admin call the same endpoints. Both get an answer (200), and each answer
carries only that workspace's synthetic marker - never the other's. All data
is synthetic; nothing is sent or bought (no provider keys, sending switched off).
"""
import json

import pytest

from app.models.evosense_models import EvoSenseProperty

import test_bookaboost_wholesale_parity as P

EVO_MARK, BAB_MARK = "EVOONLYMARKER", "BABONLYMARKER"

# module number in the parity matrix -> (name, endpoints). {deal} and {prop}
# are the caller's OWN deal and EvoSense property.
MODULES = {
    1: ("Acquisition Command", ["/wholesale/evosense/command-center", "/wholesale/evosense/funnel"]),
    2: ("Discovery / hunts", ["/wholesale/evosense/strategies", "/wholesale/evosense/strategy-starters",
                              "/wholesale/evosense/cohorts"]),
    3: ("Imports & sources", ["/wholesale/evosense/sources", "/wholesale/evosense/import/list-kinds",
                              "/wholesale/data-sources"]),
    4: ("Property intelligence & scoring", ["/wholesale/evosense/properties/{prop}",
                                            "/wholesale/evosense/properties/{prop}/readiness",
                                            "/wholesale/evosense/truth-gate"]),
    5: ("Ownership verification", ["/wholesale/evosense/identity-reviews"]),
    6: ("Valuations & comps", ["/wholesale/deals/{deal}/valuation", "/wholesale/deals/{deal}/underwriting",
                               "/wholesale/deals/{deal}/repairs/photo-ai"]),
    7: ("Skip tracing / contact lookup", ["/wholesale/skip-trace/providers", "/wholesale/ops/skip-trace",
                                          "/wholesale/enrichment/providers"]),
    8: ("Pre-foreclosure", ["/wholesale/evosense/foreclosures", "/wholesale/evosense/foreclosures/properties"]),
    9: ("Seller leads & conversations", ["/wholesale/evosense/permission-links", "/wholesale/deals/{deal}"]),
    10: ("Deal Room", ["/wholesale/deal-room/settings", "/wholesale/deal-room/invites",
                       "/wholesale/deal-room/dashboard", "/wholesale/deal-room/readiness"]),
    11: ("Acquisition pipeline / deal operations", ["/wholesale/properties", "/wholesale/deals",
                                                    "/wholesale/exceptions", "/wholesale/command-center"]),
    12: ("Buyer database", ["/wholesale/buyers", "/wholesale/buyers/network"]),
    13: ("Buyer matching & dispositions", ["/wholesale/disposition/channels", "/wholesale/deals/{deal}/matches",
                                           "/wholesale/buyer-intel/summary"]),
    14: ("Investor workflows / funding", ["/wholesale/funding/partners", "/wholesale/funding/products",
                                          "/wholesale/funding/deals/{deal}/options"]),
    15: ("Field canvassing", ["/wholesale/canvass/routes"]),
    16: ("Team management", ["/wholesale/team", "/wholesale/team/me", "/wholesale/team/rules"]),
    17: ("Appointments & follow-ups", ["/wholesale/appointments", "/wholesale/appointments/board"]),
    18: ("AI automation", ["/wholesale/evosense/controls", "/wholesale/evosense/automation/readiness"]),
    19: ("Communications inbox", ["/wholesale/evosense/inbox", "/wholesale/sms/status", "/wholesale/sms/consents"]),
    20: ("Templates & campaigns", ["/wholesale/contract-templates", "/wholesale/deals/{deal}/cadence"]),
    21: ("Documents & contracts", ["/wholesale/contract-kits", "/wholesale/documents/lifecycle",
                                   "/wholesale/files/capability"]),
    22: ("E-signatures", ["/wholesale/settings"]),
    23: ("Reports & analytics", ["/wholesale/disposition/analytics", "/wholesale/dashboard", "/wholesale/events"]),
    24: ("System settings", ["/wholesale/settings", "/wholesale/settings/assignees", "/wholesale/evosense/providers"]),
    25: ("Notifications", ["/notifications/"]),
    26: ("Launcher / workspace entry", ["/auth/my-contexts"]),
}


@pytest.fixture()
def two(client, db_session, monkeypatch):
    for env in P.LINK_ENVS:
        monkeypatch.delenv(env, raising=False)
    db = db_session
    evp, bbp = P._platform(db, "evosyspro", "EvoSys Pro"), P._platform(db, "bookaboost", "BookaBoost")
    evo = P._org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", P.EVO_FEATURES, evp)
    bab = P._org(db, "BookaBoost Wholesale", "bookaboost-wholesale", P.BAB_FEATURES, bbp)
    bab.brand_name = "BookaBoost Wholesale"
    db.commit()
    w = type("W", (), {})()
    w.db, w.evo, w.bab = db, evo, bab
    w.evo_admin = P._admin(db, evo, "mod.evo.admin@example.com")
    w.bab_admin = P._admin(db, bab, "mod.bab.admin@example.com")
    for env in ("OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION", "OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION"):
        monkeypatch.delenv(env, raising=False)
    seeded = {}
    for key, admin, org, mark in (("evo", w.evo_admin, w.evo, EVO_MARK), ("bab", w.bab_admin, w.bab, BAB_MARK)):
        h = P._h(w.db, admin, org)
        prop = P.ok(client.post("/wholesale/properties", headers=h, json={
            "street_address": "1 %s Rd" % mark, "city": "Dallas", "state": "TX", "county": "Dallas",
            "zip_code": "75203", "owner_name": "Owner %s" % mark}))
        P.ok(client.post("/wholesale/buyers", headers=h, json={"company_name": "%s Capital" % mark,
                                                               "email": "%s@example.com" % mark.lower()}))
        P.ok(client.post("/wholesale/funding/partners", headers=h, json={"name": "%s Lending" % mark, "email": "lending.%s@example.com" % mark.lower()}))
        ev = EvoSenseProperty(organization_id=org.id, street_address="2 %s Ave" % mark, city="Dallas",
                              state="TX", zip_code="75203", county="Dallas", parcel_apn="APN-%s" % mark)
        w.db.add(ev)
        w.db.commit()
        seeded[key] = {"h": h, "deal": prop["deal"]["id"], "prop": ev.id, "mark": mark}
    return w, seeded


@pytest.mark.parametrize("module", sorted(MODULES), ids=lambda m: "m%02d" % m)
def test_module_answers_for_bookaboost_and_never_shows_evosys_records(client, two, module):
    _w, seeded = two
    name, paths = MODULES[module]
    for who, other in (("bab", "evo"), ("evo", "bab")):
        me = seeded[who]
        for path in paths:
            url = path.format(deal=me["deal"], prop=me["prop"])
            r = client.get(url, headers=me["h"])
            assert r.status_code == 200, "%s %s as %s: %s %s" % (name, url, who, r.status_code, r.text[:300])
            body = json.dumps(r.json()) if "application/json" in r.headers.get("content-type", "") else r.text
            assert seeded[other]["mark"] not in body, "%s %s as %s shows the other workspace" % (name, url, who)


def test_bookaboost_sees_its_own_records_where_they_are_listed(client, two):
    _w, seeded = two
    me = seeded["bab"]
    for path in ("/wholesale/properties", "/wholesale/buyers", "/wholesale/funding/partners"):
        assert BAB_MARK in json.dumps(P.ok(client.get(path, headers=me["h"]))), path


def test_bookaboost_cannot_open_evosys_records_by_id(client, two):
    _w, seeded = two
    me, other = seeded["bab"], seeded["evo"]
    for path in ("/wholesale/deals/{deal}", "/wholesale/deals/{deal}/valuation",
                 "/wholesale/funding/deals/{deal}/options", "/wholesale/evosense/properties/{prop}"):
        r = client.get(path.format(deal=other["deal"], prop=other["prop"]), headers=me["h"])
        assert r.status_code in (403, 404), "%s -> %s" % (path, r.status_code)
