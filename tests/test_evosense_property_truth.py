"""EvoSense property truth — Priority 0.

    * an appraisal district TAX value is labelled as exactly that and is never
      an ARV; a modelled market estimate is never an ARV either
    * with no verified comparable closed sales the ARV is "Insufficient
      comparable sales" and there is NO actionable MAO
    * promotion never carries a tax value into the deal's value field
    * a public source that refuses the platform (401/403) is BLOCKED
      platform-wide: no hunt and no lookup calls it again, for any tenant, and
      only a platform admin's single explicit re-test may clear it

NO NETWORK: the DFW fixtures and fake APIs are the ones the source tests use.
"""
import json

import pytest

from app.models.evosense_models import (EvoSenseEnrichmentDecision, EvoSenseProperty,
                                        EvoSenseSourceAccess)
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import common as C
from app.services.evosense import economics as ECO
from app.services.evosense import providers as PV
from app.services.evosense import valuation as VAL
from app.services.evosense.sources import base as B

from test_evosense_dfw_sources import (ALL_DFW, _admin, _enable, _pilot, _prop, dfw_files,  # noqa: F401
                                       fake_apis, no_network, ok)


def _hunt(client, h, sid):
    return ok(client.post("/wholesale/evosense/strategies/%s/hunt" % sid, headers=h))


def _tenant(db, name, slug, email):
    org = Organization(name=name, slug=slug, plan="standard", industry="real_estate")
    db.add(org)
    db.commit()
    u = User(organization_id=org.id, email=email, password_hash=hash_password("AdminPass123!"),
             full_name=name + " Admin", role="org_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return org, {"Authorization": "Bearer %s" % create_access_token(u, db)}


# ── appraisal value is never ARV; no MAO without a verified ARV ──────────────

def test_appraisal_tax_value_is_labelled_and_never_becomes_arv_or_mao(client, db_session, sample_org,
                                                                        dfw_files, fake_apis):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "dcad", "dallas_311_code")
    s = _pilot(client, h, counties=["Dallas"])
    _hunt(client, h, s["id"])
    d1 = _prop(db_session, sample_org.id, "D1", "Dallas")
    d = ok(client.get("/wholesale/evosense/properties/%s" % d1.id, headers=h))

    assert d["facts"]["appraisal"]["value"] == 150000
    assert "Appraisal District Tax Value" in d["facts"]["appraisal"]["label"]
    assert d["facts"]["estimated_value"]["value"] is None          # not a market estimate
    assert d["property"]["estimated_value"] is None
    assert d["property"]["appraisal"]["value"] == 150000
    assert d["facts"]["arv"]["value"] is None and d["facts"]["arv"]["label"] == "Insufficient comparable sales"

    eco = d["economics"]
    lines = {l["label"]: l for l in eco["lines"]}
    assert lines["ARV"]["value"] is None and lines["ARV"]["display"] == "Insufficient comparable sales"
    assert lines["ARV"]["truth_label"] == "INSUFFICIENT COMPARABLE SALES"
    assert lines["Appraisal District Tax Value"]["value"] == 150000
    assert "never ARV" in lines["Appraisal District Tax Value"]["truth_label"]
    assert eco["mao"] is None and lines["Preliminary MAO"]["value"] is None
    assert eco["blocked"] == "no_verified_arv"
    assert not eco["steps"], "no formula runs on an appraisal value"
    assert "Value (used as ARV)" not in lines


def test_a_row_ingested_before_the_split_is_read_as_an_appraisal(db_session, sample_org):
    prop = EvoSenseProperty(organization_id=sample_org.id, street_address="3405 WENDELKIN ST",
                            estimated_value=235070,
                            estimated_value_source="dcad (DCAD 2026 appraised value (appraisal district, "
                                                   "not a market estimate))")
    db_session.add(prop)
    db_session.flush()
    v = VAL.view(prop)
    assert v["market_value"] is None
    assert v["appraisal"]["value"] == 235070 and v["appraisal"]["year"] == 2026
    assert v["appraisal"]["district"] == "DCAD"
    assert v["appraisal"]["label"] == "Appraisal District Tax Value (DCAD 2026)"
    eco = ECO.preliminary(db_session, prop)
    assert eco["mao"] is None and eco["appraisal"]["value"] == 235070


def test_a_modelled_market_estimate_is_not_an_arv_either(db_session, sample_org):
    prop = EvoSenseProperty(organization_id=sample_org.id, street_address="1 AVM Way", square_feet=1500,
                            estimated_value=200000, estimated_value_source="sandbox_property_records "
                                                                           "(sandbox assessor + sandbox AVM)")
    db_session.add(prop)
    db_session.flush()
    db_session.add_all([])
    eco = ECO.preliminary(db_session, prop)
    assert eco["market_estimate"]["value"] == 200000
    assert eco["arv"]["value"] is None and eco["mao"] is None and eco["blocked"] == "no_verified_arv"


def test_promotion_never_carries_a_tax_value_into_the_deal_value(client, db_session, sample_org,
                                                                 dfw_files, fake_apis):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "dcad")
    s = _pilot(client, h, counties=["Dallas"])
    _hunt(client, h, s["id"])
    d1 = _prop(db_session, sample_org.id, "D1", "Dallas")
    out = ok(client.post("/wholesale/evosense/properties/%s/promote" % d1.id, headers=h, json={}))
    from app.models.wholesale_models import WholesaleProperty
    wp = db_session.query(WholesaleProperty).filter(WholesaleProperty.id == out["property_id"]).one()
    assert wp.estimated_value is None and wp.estimated_value_source is None
    assert "Appraisal District Tax Value" in wp.notes and "not an ARV" in wp.notes


# ── a public source that refuses the platform is blocked platform-wide ───────

@pytest.fixture()
def tad_refuses(monkeypatch):
    calls = {"lookup": 0, "verify": 0}

    def refuse(self, targets):
        calls["lookup"] += 1
        raise B.SourceError(B.AUTH_FAILED, "www.tad.org refused the request (403)")

    def refuse_verify(self):
        calls["verify"] += 1
        raise B.SourceError(B.AUTH_FAILED, "www.tad.org refused the request (403)")
    monkeypatch.setattr(PV.TadSource, "lookup_many", refuse)
    monkeypatch.setattr(PV.TadSource, "verify", refuse_verify)
    return calls


def test_a_403_blocks_the_source_and_no_hunt_calls_it_again(client, db_session, sample_org, dfw_files,
                                                           fake_apis, tad_refuses):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll", "tad")
    s = _pilot(client, h, counties=["Tarrant"])
    out = _hunt(client, h, s["id"])
    assert out["status"] == "partial" and tad_refuses["lookup"] == 1
    row = db.query(EvoSenseSourceAccess).filter(EvoSenseSourceAccess.provider_key == "tad").one()
    assert row.blocked and row.blocked_code == "AUTH_FAILED"
    # the stored reason is host-level: no tenant, no property, no record
    assert sample_org.name not in (row.blocked_reason or "") and "1309" not in (row.blocked_reason or "")

    _hunt(client, h, s["id"])
    assert tad_refuses["lookup"] == 1, "a blocked source is never called again by a hunt"
    p = _prop(db, sample_org.id, "1309")
    last = (db.query(EvoSenseEnrichmentDecision)
            .filter(EvoSenseEnrichmentDecision.property_id == p.id,
                    EvoSenseEnrichmentDecision.provider_key == "tad")
            .order_by(EvoSenseEnrichmentDecision.created_at.desc()).first())
    assert last.decision == C.L_SKIP_PROVIDER_DOWN and "blocked platform-wide" in json.loads(last.reasons)[0]
    assert "tad" not in [p.key for p, _, _ in PV.route(db, sample_org.id, C.ASSESSOR)]

    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    assert reg["tad"]["state"] == "BLOCKED" and "platform" in reg["tad"]["why"]
    prov = {p["key"]: p for p in ok(client.get("/wholesale/evosense/providers", headers=h))["providers"]}
    assert prov["tad"]["status"] == "BLOCKED", "never 'Connected' while the source refuses us"


def test_the_block_reaches_every_tenant_without_leaking_who_tripped_it(client, db_session, sample_org,
                                                                      dfw_files, fake_apis, tad_refuses):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll", "tad")
    _hunt(client, h, _pilot(client, h, counties=["Tarrant"])["id"])
    assert tad_refuses["lookup"] == 1

    other, oh = _tenant(db, "Second Wholesaler", "second-wholesaler", "admin@second.test")
    _enable(client, oh, "tarrant_tax_roll", "tad")
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=oh))["sources"]}
    assert reg["tad"]["state"] == "BLOCKED"
    body = json.dumps(reg["tad"])
    assert sample_org.name not in body and sample_org.id not in body
    # the other tenant's own history is its own: it never called TAD
    assert reg["tad"]["calls_total"] == 0 and reg["tad"]["last_failure_at"] is None
    _hunt(client, oh, _pilot(client, oh, counties=["Tarrant"])["id"])
    assert tad_refuses["lookup"] == 1, "the second tenant's hunt did not hammer the blocked source"


def test_only_a_platform_admin_may_re_test_a_blocked_source(client, db_session, sample_org, dfw_files,
                                                           fake_apis, tad_refuses, monkeypatch):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tad")
    PV.block_platform(db, "tad", "AUTH_FAILED", "AUTH_FAILED: www.tad.org refused the request (403)")
    db.commit()
    r = ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "tad"}))
    assert r["ok"] is False and r["code"] == "PLATFORM_BLOCKED" and tad_refuses["verify"] == 0

    # a platform admin's re-test is ONE request; a refusal keeps the block ...
    res = PV.verify_source(db, sample_org.id, "tad", platform_admin=True)
    assert res["ok"] is False and tad_refuses["verify"] == 1
    assert PV.platform_blocked(db, "tad")
    # ... and a success clears it
    monkeypatch.setattr(PV.TadSource, "verify", lambda self: {"ok": True})
    res = PV.verify_source(db, sample_org.id, "tad", platform_admin=True)
    assert res["ok"] is True and not PV.platform_blocked(db, "tad")


def test_a_tenant_row_that_already_holds_a_403_blocks_on_first_read(client, db_session, sample_org, dfw_files):
    """Production state before this change: TAD's 403s were recorded only on
    the tenant's provider row. The first read after deploy raises the block."""
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tad")
    cfg = PV.config(db, sample_org.id, "tad")
    PV.record_failure(cfg, "AUTH_FAILED: www.tad.org refused the request (403)")
    db.commit()
    assert db.query(EvoSenseSourceAccess).count() == 0
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    assert reg["tad"]["state"] == "BLOCKED"
    assert PV.platform_blocked(db, "tad") and "tad" not in [p.key for p, _, _ in PV.route(db, sample_org.id, C.ASSESSOR)]


def test_a_rate_limit_is_not_a_block(client, db_session, sample_org, dfw_files, monkeypatch):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "fw_code_violations")
    monkeypatch.setattr(B, "get_json", lambda url, params=None: (_ for _ in ()).throw(
        B.SourceError(B.RATE_LIMITED, "429 from the city", retry_after=600)))
    ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "fw_code_violations"}))
    assert not PV.platform_blocked(db, "fw_code_violations")
