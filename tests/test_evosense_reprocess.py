"""EvoSense property truth — Priority 3 machinery: dry run, apply, rollback.

The production pilot's 40 records were derived under the old rules. They are
re-derived from their PRESERVED raw evidence, and the rules say: show the
complete before/after diff first, change nothing, and only apply on an
explicit, confirmed operator action - which can be rolled back.

These tests build a pilot under the new rules, then put one Dallas property
back into the shape the OLD rules left it in (the appraisal tax value parked
in the market-estimate column, a closed 311 request scored as a current
complaint), and prove:

    * re-deriving records that are already current changes nothing
    * a dry run writes the run record and nothing else
    * the diff names every correction, with its reason
    * apply writes exactly what the dry run predicted, deactivating (never
      deleting) old evidence
    * rollback restores the snapshot
    * another tenant can neither see nor apply nor roll back the run, and
      is untouched by it
"""
import pytest

from app.models.evosense_models import (EvoSenseObservation, EvoSenseProperty, EvoSenseReprocessRun,
                                        EvoSenseScore, EvoSenseSignal)
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import signals as SIG

from test_evosense_dfw_sources import (ALL_DFW, _admin, _enable, _pilot, _prop, dfw_files, fake_apis,  # noqa: F401
                                       no_network, ok)


def _counts(db, org_id):
    return {"signals": db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id).count(),
            "active": db.query(EvoSenseSignal).filter(EvoSenseSignal.organization_id == org_id,
                                                      EvoSenseSignal.active.is_(True)).count(),
            "scores": db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id).count(),
            "observations": db.query(EvoSenseObservation).filter(
                EvoSenseObservation.organization_id == org_id).count()}


def _state(db, org_id):
    out = {}
    for p in db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id).all():
        out[p.id] = (p.opportunity_score, p.estimated_value, p.appraisal_value, p.status, p.data_confidence,
                     tuple(sorted(s.signal_type for s in db.query(EvoSenseSignal).filter(
                         EvoSenseSignal.property_id == p.id, EvoSenseSignal.active.is_(True)).all())))
    return out


@pytest.fixture()
def pilot(client, db_session, sample_org, dfw_files, fake_apis):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, *ALL_DFW)
    s = _pilot(client, h)
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    return {"h": h, "strategy": s}


def _make_legacy(db, org_id):
    """Put D1 back the way derive/v2 left it."""
    d1 = _prop(db, org_id, "D1", "Dallas")
    d1.estimated_value = d1.appraisal_value
    d1.estimated_value_source = "dcad (DCAD %s certified appraisal district value)" % d1.appraisal_year
    d1.appraisal_value = d1.appraisal_year = d1.appraisal_source = None
    d1.appraisal_land_value = d1.appraisal_improvement_value = None
    hist = db.query(EvoSenseSignal).filter(EvoSenseSignal.property_id == d1.id,
                                           EvoSenseSignal.signal_type == "CODE_HISTORY").one()
    hist.active = False
    db.add(EvoSenseSignal(organization_id=org_id, property_id=d1.id, signal_type="CODE_COMPLAINT",
                          source=hist.source, connector_kind=hist.connector_kind,
                          source_reference=hist.source_reference, observation_id=hist.observation_id,
                          observed_at=hist.observed_at, effective_at=hist.effective_at, confidence=80,
                          normalized_value="Code Concern - CCS", active=True))
    d1.opportunity_score = 77
    db.commit()
    return d1


def test_current_records_rederive_to_themselves(client, db_session, sample_org, pilot):
    run = ok(client.post("/wholesale/evosense/reprocess/dry-run", headers=pilot["h"], json={}))
    assert run["status"] == "dry_run" and run["property_count"] >= 5
    moved = [d for d in run["diff"] if d["score"]["before"] != d["score"]["after"]
             or d["signals"]["removed"] or d["signals"]["added"]]
    assert not moved, moved


def test_dry_run_writes_only_the_run_and_shows_every_correction(client, db_session, sample_org, pilot):
    db, org = db_session, sample_org.id
    d1 = _make_legacy(db, org)
    before_counts, before_state = _counts(db, org), _state(db, org)
    run = ok(client.post("/wholesale/evosense/reprocess/dry-run", headers=pilot["h"], json={}))
    db.expire_all()
    assert _counts(db, org) == before_counts and _state(db, org) == before_state, "a dry run wrote records"
    assert db.query(EvoSenseReprocessRun).filter(EvoSenseReprocessRun.organization_id == org).count() == 1

    d = {x["property_id"]: x for x in run["diff"]}[d1.id]
    removed = {s["type"]: s["why"] for s in d["signals"]["removed"]}
    added = {s["type"] for s in d["signals"]["added"]}
    assert "CODE_COMPLAINT" in removed and "closed" in removed["CODE_COMPLAINT"]
    assert "CODE_HISTORY" in added
    assert d["value"]["value_before"] == 150000 and d["value"]["market_estimate_after"] is None
    assert d["value"]["appraisal_tax_value_after"]["value"] == 150000
    assert d["arv"]["before"] == 150000 and d["arv"]["after"] is None
    assert d["arv"]["after_label"] == "Insufficient comparable sales"
    assert d["mao"]["after"] is None and "NOT CALCULATED" in d["mao"]["after_label"]
    assert d["score"]["before"] == 77 and d["score"]["after"] != 77
    assert d["deed_transfer"]["after"] == "1999-06-01"
    assert d["owner"]["after"]["owner_type"] == "life_estate"
    s = run["summary"]
    assert s["signals_removed"].get("CODE_COMPLAINT") == 1 and s["appraisal_moved_out_of_estimate"] == 1
    # and it is readable again, in full, later
    again = ok(client.get("/wholesale/evosense/reprocess/%s" % run["id"], headers=pilot["h"]))
    assert again["diff"] == run["diff"]
    assert ok(client.get("/wholesale/evosense/reprocess", headers=pilot["h"]))["items"][0]["id"] == run["id"]


def test_apply_needs_confirmation_matches_the_dry_run_and_rolls_back(client, db_session, sample_org, pilot):
    db, org, h = db_session, sample_org.id, pilot["h"]
    d1 = _make_legacy(db, org)
    legacy_state = _state(db, org)
    run = ok(client.post("/wholesale/evosense/reprocess/dry-run", headers=h, json={}))
    rid = run["id"]
    assert client.post("/wholesale/evosense/reprocess/%s/apply" % rid, headers=h,
                       json={"confirm": "yes"}).status_code == 422
    out = ok(client.post("/wholesale/evosense/reprocess/%s/apply" % rid, headers=h,
                         json={"confirm": "APPLY %s" % rid}))
    assert out["mismatches"] == [], out["mismatches"]
    db.expire_all()
    d1 = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == d1.id).one()
    predicted = {x["property_id"]: x for x in run["diff"]}[d1.id]
    assert d1.opportunity_score == predicted["score"]["after"]
    assert d1.estimated_value is None and d1.appraisal_value == 150000
    old = db.query(EvoSenseSignal).filter(EvoSenseSignal.property_id == d1.id,
                                          EvoSenseSignal.signal_type == "CODE_COMPLAINT").one()
    assert old.active is False and "reprocess run" in old.retracted_reason       # deactivated, not deleted
    assert db.query(EvoSenseSignal).filter(EvoSenseSignal.property_id == d1.id,
                                           EvoSenseSignal.signal_type == "CODE_HISTORY",
                                           EvoSenseSignal.active.is_(True)).count() == 1
    # applied once only
    assert client.post("/wholesale/evosense/reprocess/%s/apply" % rid, headers=h,
                       json={"confirm": "APPLY %s" % rid}).status_code == 409

    ok(client.post("/wholesale/evosense/reprocess/%s/rollback" % rid, headers=h,
                   json={"confirm": "ROLLBACK %s" % rid}))
    db.expire_all()
    assert _state(db, org) == legacy_state
    assert ok(client.get("/wholesale/evosense/reprocess/%s" % rid, headers=h))["status"] == "rolled_back"


def test_only_an_admin_runs_or_applies(client, db_session, sample_org, pilot, auth_headers):
    assert client.post("/wholesale/evosense/reprocess/dry-run", headers=auth_headers, json={}).status_code == 403
    run = ok(client.post("/wholesale/evosense/reprocess/dry-run", headers=pilot["h"], json={}))
    r = client.post("/wholesale/evosense/reprocess/%s/apply" % run["id"], headers=auth_headers,
                    json={"confirm": "APPLY %s" % run["id"]})
    assert r.status_code == 403


def test_another_tenant_cannot_see_apply_or_be_touched_by_a_run(client, db_session, sample_org, pilot):
    db, org = db_session, sample_org.id
    _make_legacy(db, org)
    org_b = Organization(name="Tenant B", slug="tenant-b-reprocess", plan="standard", industry="real_estate")
    db.add(org_b)
    db.commit()
    ub = User(organization_id=org_b.id, email="admin@tenant-b.test", password_hash=hash_password("AdminPass123!"),
              full_name="B Admin", role="org_admin", must_change_password=False)
    db.add(ub)
    db.commit()
    hb = {"Authorization": "Bearer %s" % create_access_token(ub, db)}
    run = ok(client.post("/wholesale/evosense/reprocess/dry-run", headers=pilot["h"], json={}))
    rid = run["id"]
    assert client.get("/wholesale/evosense/reprocess/%s" % rid, headers=hb).status_code == 404
    assert client.post("/wholesale/evosense/reprocess/%s/apply" % rid, headers=hb,
                       json={"confirm": "APPLY %s" % rid}).status_code == 404
    assert client.post("/wholesale/evosense/reprocess/%s/rollback" % rid, headers=hb,
                       json={"confirm": "ROLLBACK %s" % rid}).status_code == 404
    assert ok(client.get("/wholesale/evosense/reprocess", headers=hb))["items"] == []
    # B's own dry run sees none of A's properties
    b_run = ok(client.post("/wholesale/evosense/reprocess/dry-run", headers=hb, json={}))
    assert b_run["property_count"] == 0 and b_run["diff"] == []
    # B cannot scope a dry run to A's strategy
    assert client.post("/wholesale/evosense/reprocess/dry-run", headers=hb,
                       json={"strategy_id": pilot["strategy"]["id"]}).status_code == 404
    # A's apply leaves B's world as it was (empty) and A's run untouched by B's attempts
    ok(client.post("/wholesale/evosense/reprocess/%s/apply" % rid, headers=pilot["h"],
                   json={"confirm": "APPLY %s" % rid}))
    assert _counts(db, org_b.id) == {"signals": 0, "active": 0, "scores": 0, "observations": 0}
