"""Automatic owner lookups in the daily search use the SAME Tracerfy provider as
the Get phones & emails button: 65+ only, inside the Wholesale Settings
paid-lookup limits (0 = OFF), counted against them, a miss is free, and Do
Not Call numbers never become a phone to use. Tracerfy is never really
called here - the provider's answers are controlled."""
import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry, EvoSenseProviderConfig)
from app.models.wholesale_models import WholesaleEnrichmentRequest
from app.services import wholesale_enrichment as WE
from app.services import wholesale_service as WS
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import enrichment as EN
from app.services.evosense import hunt as HU
from app.services.evosense import providers as PV
from app.services.evosense import sandbox_seed as SS

CALLS = []


def found(data):
    CALLS.append(data)
    return WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="tracerfy", owner_name=data.owner_name,
                               phones=[WE.EnrichmentPhone(number="2145550188", phone_type="mobile")],
                               emails=["owner@example.com"], billable=True, cost_cents=10,
                               match_evidence={"name_match": "full", "dnc_numbers": ["2145550111"]})


def miss(data):
    CALLS.append(data)
    return WE.EnrichmentResult(status=WE.STATUS_NO_MATCH, provider="tracerfy", billable=False, cost_cents=0)


@pytest.fixture()
def world(db_session, sample_org, sample_advisor, monkeypatch):
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Auto lookup (TEST)",
                                                          daily_budget_cents=100000,
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    HU.run_strategy(db, org, s, trigger="test", enrich=False)
    db.commit()
    # Only Tracerfy may answer: the sandbox skip tracers are switched off.
    for cfg in db.query(EvoSenseProviderConfig).filter(EvoSenseProviderConfig.organization_id == org).all():
        if cfg.provider_key.startswith("sandbox_skiptrace"):
            cfg.enabled = False
    PV.config(db, org, "tracerfy_contact").enabled = True
    db.commit()
    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token")
    CALLS.clear()
    monkeypatch.setattr(WE.PROVIDERS["tracerfy"], "lookup", found)
    return db, org, s


def _candidate(db, org, s, score=70):
    from app.models.evosense_models import EvoSenseProperty
    for p in db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org).all():
        owner = CT.primary_owner(db, p)
        if owner is None or owner.owner_type not in ("individual", "unknown", None):
            continue
        if db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == owner.id).count():
            continue
        p.opportunity_score = score
        p.data_confidence = "high"
        db.flush()
        return p
    raise AssertionError("no sandbox candidate")


def _caps(db, org, day, month):
    st = WS.resolve_settings(db, org, commit=False)
    st.enrichment_daily_cap, st.enrichment_monthly_cap = day, month
    db.commit()


def test_zero_limits_mean_off_and_nothing_is_called(world):
    db, org, s = world
    _caps(db, org, 0, 0)
    prop = _candidate(db, org, s)
    res = EN.run(db, prop, s)
    assert res["decision"] == C.D_BUDGET and "Wholesale Settings" in res["reasons"][0]
    assert CALLS == []


def test_below_65_is_not_looked_up_automatically(world):
    db, org, s = world
    _caps(db, org, 10, 100)
    s.min_opportunity_score = 40
    prop = _candidate(db, org, s, score=55)
    res = EN.run(db, prop, s)
    assert res["decision"] == C.D_INSUFFICIENT and "65" in res["reasons"][0]
    assert CALLS == []


def test_a_find_is_charged_counted_and_dnc_stays_out(world):
    db, org, s = world
    _caps(db, org, 10, 100)
    prop = _candidate(db, org, s)
    res = EN.run(db, prop, s)
    assert res["decision"] == C.D_PAID and res["outcome"] == "found" and len(CALLS) == 1
    owner = CT.primary_owner(db, prop)
    values = [c.value for c in db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == owner.id)]
    assert any("5550188" in (v or "") for v in values)
    assert not any("5550111" in (v or "") for v in values)                 # Do Not Call never filled
    entry = db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.property_id == prop.id,
                                               EvoSenseCostEntry.provider_key == "tracerfy_contact",
                                               EvoSenseCostEntry.status == "charged").one()
    assert entry.total_cents == 10
    rows = db.query(WholesaleEnrichmentRequest).filter(WholesaleEnrichmentRequest.organization_id == org).all()
    assert len(rows) == 1 and rows[0].billable and rows[0].cost_cents == 10
    # the next one is held to the same daily limit
    _caps(db, org, 1, 100)
    other = _candidate(db, org, s)
    res2 = EN.run(db, other, s)
    assert res2["decision"] == C.D_BUDGET and len(CALLS) == 1


def test_a_miss_is_free(world, monkeypatch):
    db, org, s = world
    _caps(db, org, 10, 100)
    monkeypatch.setattr(WE.PROVIDERS["tracerfy"], "lookup", miss)
    prop = _candidate(db, org, s)
    res = EN.run(db, prop, s)
    assert res["outcome"] == "no_match"
    entry = db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.property_id == prop.id,
                                               EvoSenseCostEntry.provider_key == "tracerfy_contact").one()
    assert entry.total_cents == 0
