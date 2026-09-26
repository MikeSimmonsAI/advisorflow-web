"""EvoSense provider truth — Priority 1: ONE canonical operational state.

Every screen and endpoint derives a provider's status from
`providers.canonical()`. These tests pin the model itself (every state, and
the dimensions kept apart: configured / enabled / reachable / healthy /
operational / failed / blocked), prove the Source Registry and the Service
Providers table can never disagree, and prove the controls report what a
channel can actually do rather than only whether its switch is paused.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.evosense import common as C
from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B

from test_evosense_dfw_sources import _admin, _enable, _pilot, dfw_files, fake_apis, no_network, ok  # noqa: F401

NOW = datetime(2026, 9, 26, 12, 0, 0)


def cfg(**kw):
    base = dict(enabled=True, last_success_at=None, last_failure_at=None, last_failure_reason=None,
                consecutive_failures=0, rate_limited_until=None, degraded_until=None)
    base.update(kw)
    return SimpleNamespace(**base)


TAD = PV.PROVIDERS["tad"]


@pytest.mark.parametrize("name,p,c,blocked,expect", [
    ("enabled, never tried", TAD, cfg(), False,
     dict(state="UNVERIFIED", configured=True, enabled=True, reachable=None, healthy=False, operational=False,
          routable=True)),
    ("verified", TAD, cfg(last_success_at=NOW - timedelta(hours=1)), False,
     dict(state="HEALTHY", reachable=True, healthy=True, operational=True, failed=False)),
    ("switched off", TAD, cfg(enabled=False), False,
     dict(state="DISABLED", configured=True, enabled=False, operational=False, routable=False)),
    ("one failure after success", TAD, cfg(last_success_at=NOW - timedelta(days=1), last_failure_at=NOW,
                                           last_failure_reason="DOWNLOAD_FAILED: reset", consecutive_failures=1),
     False, dict(state="DEGRADED", failed=True, healthy=False, operational=False, reachable=False, routable=True)),
    ("repeated failures, backing off", TAD, cfg(last_failure_at=NOW, consecutive_failures=3,
                                                last_failure_reason="TIMEOUT: no answer",
                                                degraded_until=NOW + timedelta(minutes=10)),
     False, dict(state="FAILED", reachable=False, routable=False, operational=False)),
    ("repeated failures, back-off over: FAILED is not 'Connected'", TAD,
     cfg(last_failure_at=NOW - timedelta(hours=1), consecutive_failures=6, last_failure_reason="TIMEOUT: x",
         degraded_until=NOW - timedelta(minutes=30)),
     False, dict(state="FAILED", operational=False, routable=True)),
    ("refused the platform", TAD, cfg(last_failure_at=NOW, consecutive_failures=6,
                                      last_failure_reason="AUTH_FAILED: www.tad.org refused the request (403)"),
     True, dict(state="BLOCKED", blocked=True, reachable=True, operational=False, routable=False)),
    ("rate limited", PV.PROVIDERS["fw_code_violations"],
     cfg(last_failure_at=NOW, last_failure_reason="RATE_LIMITED: 429", rate_limited_until=NOW + timedelta(minutes=5)),
     False, dict(state="RATE LIMITED", reachable=True, routable=False, operational=False)),
    ("paid vendor never bought", PV.PROVIDERS["rentcast"], cfg(enabled=False), False,
     dict(state="NOT PURCHASED", configured=False, operational=False, routable=False)),
    ("manual entry", PV.PROVIDERS["manual"], cfg(), False,
     dict(state="MANUAL ONLY", operational=False, routable=False, manual=True)),
    ("sandbox on", PV.PROVIDERS["sandbox_skiptrace"], cfg(), False,
     dict(state="SANDBOX", operational=True, routable=True)),
    ("sandbox off", PV.PROVIDERS["sandbox_skiptrace"], cfg(enabled=False), False,
     dict(state="DISABLED", routable=False)),
])
def test_the_canonical_state_matrix(name, p, c, blocked, expect):
    st = PV.canonical(p, c, NOW, blocked=blocked)
    for k, v in expect.items():
        assert st[k] == v, "%s: %s is %r, expected %r (%s)" % (name, k, st[k], v, st["why"])
    assert st["state"] in PV.STATES and st["why"]


def test_healthy_is_not_implied_by_enabled_or_by_a_stale_success():
    # enabled is not healthy
    assert PV.canonical(TAD, cfg(), NOW)["healthy"] is False
    # a success followed by a failure is not healthy
    st = PV.canonical(TAD, cfg(last_success_at=NOW - timedelta(days=2), last_failure_at=NOW - timedelta(days=1),
                               last_failure_reason="SOURCE_FORMAT_CHANGED: x", consecutive_failures=1), NOW)
    assert st["healthy"] is False and st["failed"] is True and st["state"] == "DEGRADED"
    assert st["reachable"] is True          # it answered; the answer was wrong


def test_registry_and_providers_table_never_disagree(client, db_session, sample_org, dfw_files, fake_apis,
                                                     monkeypatch):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "tarrant_tax_roll", "tad", "dcad", "census_geocoder")
    monkeypatch.setattr(PV.TadSource, "verify", lambda self: (_ for _ in ()).throw(
        B.SourceError(B.AUTH_FAILED, "www.tad.org refused the request (403)")))
    ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "tad"}))
    ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "dcad"}))
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    prov = ok(client.get("/wholesale/evosense/providers", headers=h))
    rows = {r["key"]: r for r in prov["providers"]}
    for key, r in reg.items():
        assert rows[key]["state"] == r["state"] == rows[key]["status"], key
        for dim in ("configured", "enabled", "reachable", "healthy", "operational", "failed", "blocked"):
            assert rows[key][dim] == r[dim], (key, dim)
    assert reg["tad"]["state"] == "BLOCKED" and reg["dcad"]["state"] == "HEALTHY"
    assert reg["tarrant_tax_roll"]["state"] == "UNVERIFIED" and not reg["tarrant_tax_roll"]["operational"]
    assert "tad" not in prov["real_connectors"] and "tad" in prov["real_connectors_unavailable"]
    assert prov["real_connectors"] == ["dcad"], "only a VERIFIED source counts as a real connector"
    assert not any(r["state"] == "CONNECTED" for r in prov["providers"]), "the word 'Connected' is gone"


def test_disabled_unpurchased_and_manual_are_different_things(client, db_session, sample_org, dfw_files):
    _, h = _admin(db_session, sample_org)
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    assert reg["tad"]["state"] == "DISABLED" and reg["tad"]["configured"] is True
    assert reg["rentcast"]["state"] == "NOT PURCHASED" and reg["rentcast"]["cost"] == "paid (not purchased)"
    assert reg["manual"]["state"] == "MANUAL ONLY"
    prov = {r["key"]: r for r in ok(client.get("/wholesale/evosense/providers", headers=h))["providers"]}
    assert prov["rentcast"]["cost"] == "paid (not purchased)", "never 'free · always available'"


def test_capabilities_are_rated_per_market(client, db_session, sample_org, dfw_files, fake_apis, monkeypatch):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "tarrant_tax_roll", "tad", "dcad", "dallas_311_code")
    _pilot(client, h)                                   # Tarrant + Dallas
    monkeypatch.setattr(PV.TadSource, "verify", lambda self: (_ for _ in ()).throw(
        B.SourceError(B.AUTH_FAILED, "www.tad.org refused the request (403)")))
    ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "tad"}))
    prov = ok(client.get("/wholesale/evosense/providers", headers=h))
    caps = {c["capability"]: c for c in prov["capabilities"]}
    assessor = {m["market"]: m for m in caps["ASSESSOR"]["by_market"]}
    assert assessor["Dallas County"]["kind"] == "real"
    assert assessor["Tarrant County"]["kind"] == "unavailable" and "blocked" in assessor["Tarrant County"]["label"]
    assert "Tarrant Appraisal District (TAD) property data" not in caps["ASSESSOR"]["providers"]
    assert any(u["state"] == "BLOCKED" for u in caps["ASSESSOR"]["unavailable"])
    tax = {m["market"]: m for m in caps["TAX"]["by_market"]}
    assert tax["Tarrant County"]["kind"] == "real" and tax["Dallas County"]["kind"] == "manual"
    code = {m["market"]: m for m in caps["CODE_VIOLATION"]["by_market"]}
    assert "City of Dallas only" in code["Dallas County"]["label"]
    # a disabled sandbox adapter serves nothing
    assert not any("Sandbox" in x for x in caps["PROPERTY_SEARCH"]["providers"])


def test_controls_report_what_a_channel_can_actually_do(client, db_session, sample_org):
    _, h = _admin(db_session, sample_org)
    ctl = ok(client.get("/wholesale/evosense/controls", headers=h))
    ch = ctl["channels"]
    assert ctl["paused_sms"] is False                              # the switch is not paused ...
    assert ch["paused_sms"]["state"] == "PROGRAM OFF"              # ... and still nothing can be sent
    assert ch["paused_sms"]["operational"] is False and ch["paused_sms"]["program"]["enabled"] is False
    assert ch["paused_paid_data"]["state"] == "NOTHING CONNECTED" and not ch["paused_paid_data"]["operational"]
    assert ch["paused_email"]["state"] == "NOT BUILT" and ch["paused_voice"]["state"] == "NOT BUILT"
    assert ch["paused_discovery"]["operational"] is False          # no source enabled
    # sandbox skip trace on: "paid data" is SANDBOX ONLY, not a live paid provider
    ok(client.patch("/wholesale/evosense/providers", headers=h, json={"key": "sandbox_skiptrace", "enabled": True}))
    ch = ok(client.get("/wholesale/evosense/controls", headers=h))["channels"]
    assert ch["paused_paid_data"]["state"] == "SANDBOX ONLY" and ch["paused_paid_data"]["sandbox_only"]
    # pausing reads PAUSED whatever the availability
    ok(client.patch("/wholesale/evosense/controls", headers=h, json={"paused_sms": True}))
    ch = ok(client.get("/wholesale/evosense/controls", headers=h))["channels"]
    assert ch["paused_sms"]["state"] == "PAUSED"


def test_sms_reads_operational_only_when_the_program_can_send(client, db_session, sample_org, monkeypatch):
    from app.services import wholesale_sms as WSMS
    _, h = _admin(db_session, sample_org)
    monkeypatch.setattr(WSMS, "program_status", lambda db, org_id: {
        "enabled": True, "can_send": True, "messaging_service_configured": True, "kill_switch": False})
    ch = ok(client.get("/wholesale/evosense/controls", headers=h))["channels"]
    assert ch["paused_sms"]["state"] == "OPERATIONAL" and "consent" in ch["paused_sms"]["why"]
    monkeypatch.setattr(WSMS, "program_status", lambda db, org_id: {
        "enabled": True, "can_send": False, "messaging_service_configured": False, "kill_switch": False})
    ch = ok(client.get("/wholesale/evosense/controls", headers=h))["channels"]
    assert ch["paused_sms"]["state"] == "NOT CONFIGURED" and not ch["paused_sms"]["operational"]
