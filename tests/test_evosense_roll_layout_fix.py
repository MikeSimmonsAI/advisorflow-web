"""Targeted repair of the Oct 17-19, 2026 Tarrant tax-roll misread.

  * scope = only tax-roll observations read by the old adapter whose raw line
    is the new 781-character layout; nothing else is read for writing;
  * the dry run writes nothing (it runs inside a savepoint);
  * invented tax evidence is retracted, a real delinquency is kept with the
    corrected wording, every other source's evidence is preserved;
  * owners are corrected only when they came from that record alone;
  * apply needs the plan's code; rollback restores everything.
"""
import json

import pytest

from app.models.evosense_models import (EvoSenseObservation, EvoSenseOwner, EvoSenseProperty,
                                        EvoSenseReprocessRun, EvoSenseScore, EvoSenseSignal)
from app.services.evosense import evaluate as EV
from app.services.evosense import ingest as IN
from app.services.evosense import providers as PV
from app.services.evosense import roll_layout_fix as RF
from app.services.evosense.sources import tarrant as TT

from tests.test_evosense_dfw_sources import master_line
from tests.test_evosense_tarrant_roll_layout import ROLL_DATE, v2_line


def _misread(db, org, line, street, *, litig="14"):
    """Exactly what production stored Oct 17-19: the new line read with the
    old offsets, by adapter tarrant_tax_roll/2."""
    old = {k: line[a - 1:a - 1 + n].strip() for k, a, n in TT.MASTER}
    old["litig"] = litig
    rec = TT.TarrantTaxRollReader().record(old, TT.classify(old, ROLL_DATE), line,
                                           "https://example/TaxRoll20261016.zip", ROLL_DATE)
    rec["street_address"] = street
    rec["_adapter_version"] = "tarrant_tax_roll/2"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", rec)
    db.flush()
    acct = TT.apn(old["account"])
    return db.query(EvoSenseProperty).filter_by(organization_id=org, parcel_apn=acct).one()


def _fw_code(db, org, prop):
    rec = {"source_reference": "FWCODE:26-1%s" % prop.parcel_apn, "street_address": prop.street_address,
           "city": "Fort Worth", "state": "TX", "county": "Tarrant", "parcel_apn": prop.parcel_apn,
           "signals": [{"type": "CODE_VIOLATION", "confidence": 90, "value": "open high grass case",
                        "ref": "FWCODE:26-1%s" % prop.parcel_apn, "effective_at": "2026-09-22",
                        "case_status": "open"}]}
    IN.ingest(db, org, PV.PROVIDERS["fw_code_violations"], "CODE_VIOLATION", rec)


@pytest.fixture()
def world(db_session, sample_org):
    db, org = db_session, sample_org.id
    not_owed = _misread(db, org, v2_line(account="700001", owner1="GARCIA FILIBERTO", addr1="9 FAR AWAY LN"),
                        "100 FIRST ST")
    _fw_code(db, org, not_owed)
    owed = _misread(db, org, v2_line(account="700002", owner1="LOPEZ ANA", addr1="200 SECOND ST",
                                     prior_due="000001250.40"), "200 SECOND ST", litig="")
    # out of scope: an old-layout line read correctly, and a new line read by the fixed reader
    ok_old = master_line(account="700003", prior_due="000000900.00", street_name="THIRD ST", street_no="300")
    r = TT.TarrantTaxRollReader().record_from_raw(ok_old, "x", ROLL_DATE)
    r["_adapter_version"] = "tarrant_tax_roll/2"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", r)
    ok_new = TT.TarrantTaxRollReader().record_from_raw(v2_line(account="700004", prior_due="000000500.00"),
                                                      "x", ROLL_DATE)
    ok_new["street_address"] = "400 FOURTH ST"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", ok_new)
    db.commit()
    for p in db.query(EvoSenseProperty).filter_by(organization_id=org).all():
        EV.rescore(db, p)
    db.commit()
    return {"db": db, "org": org, "not_owed": not_owed, "owed": owed}


def _state(db, org):
    """Every row of every table the repair can write, in full."""
    from app.models.evosense_models import EvoSensePerson, EvoSenseOwnership
    out = {}
    for model in (EvoSenseProperty, EvoSenseSignal, EvoSenseOwner, EvoSenseOwnership, EvoSenseObservation,
                  EvoSensePerson):
        out[model.__tablename__] = sorted(json.dumps(RF._row(o), sort_keys=True, default=str)
                                          for o in db.query(model).filter_by(organization_id=org).all())
    out["scores"] = sorted((s.id, s.is_current) for s in db.query(EvoSenseScore).filter_by(organization_id=org))
    out["runs"] = db.query(EvoSenseReprocessRun).count()
    return out


def test_scope_is_exactly_the_misread_records(world):
    db, org = world["db"], world["org"]
    ids = {o.property_id for o in RF.affected_observations(db, org)}
    assert ids == {world["not_owed"].id, world["owed"].id}


def test_dry_run_writes_nothing_and_reports_the_exact_effect(world):
    db, org = world["db"], world["org"]
    before = _state(db, org)
    rep = RF.dry_run(db, org)
    db.commit()
    assert _state(db, org) == before, "a dry run must not write anything"
    assert rep["scope"]["properties"] == 2
    by = {p["parcel_apn"]: p for p in rep["properties"]}
    bad = by["700001"]
    assert {s["type"] for s in bad["retract"]} == {"TAX_DELINQUENT", "TAX_SUIT"}
    assert any(s["type"] == "CODE_VIOLATION" for s in bad["preserved"])            # other source kept
    assert "TAX_DELINQUENT" not in bad["active_after"] and "CODE_VIOLATION" in bad["active_after"]
    assert (bad["score_after"] or 0) < (bad["score_before"] or 0)
    assert bad["owner"]["action"] == "correct" and bad["owner"]["after"]["mailing_zip"] == "76106"
    good = by["700002"]
    assert good["retract"] == [] and good["corrected_signals"][0]["type"] == "TAX_DELINQUENT"
    assert "$1,250.40" in good["corrected_signals"][0]["value"]
    assert "TAX_DELINQUENT" in good["active_after"]
    t = rep["totals"]
    assert t["signals_retracted"] == 2 and t["still_tax_delinquent"] == 1
    assert rep["confirm_with"].startswith("APPLY ROLL FIX ")


def test_apply_needs_the_code_then_matches_the_dry_run_and_rolls_back_exactly(world):
    db, org = world["db"], world["org"]
    rep = RF.dry_run(db, org)
    with pytest.raises(ValueError):
        RF.apply(db, org, confirm="APPLY ROLL FIX nope")
    before = _state(db, org)
    out = RF.apply(db, org, confirm=rep["confirm_with"])
    db.commit()
    assert out["totals"]["signals_retracted"] == 2
    bad = world["not_owed"]
    db.refresh(bad)
    assert bad.opportunity_score == {p["property_id"]: p for p in rep["properties"]}[bad.id]["score_after"]
    kinds = {s.signal_type for s in db.query(EvoSenseSignal).filter_by(property_id=bad.id, active=True)}
    assert "TAX_SUIT" not in kinds and "CODE_VIOLATION" in kinds
    retracted = db.query(EvoSenseSignal).filter_by(property_id=bad.id, signal_type="TAX_SUIT").one()
    assert "wrong column layout" in retracted.retracted_reason
    assert RF.affected_observations(db, org) == []                                # nothing left in scope
    # the general re-derive tools cannot touch this run
    from app.services.evosense import reprocess as RP
    run = db.query(EvoSenseReprocessRun).filter_by(id=out["run_id"]).one()
    with pytest.raises(ValueError):
        RP.rollback(db, org, run, user=None)
    RF.rollback(db, org, out["run_id"])
    db.commit()
    db.expire_all()
    after = _state(db, org)
    before.pop("runs"), after.pop("runs")
    for k in before:
        assert after[k] == before[k], k                     # every row exactly as before


def test_rollback_refuses_once_anything_changed_after_the_repair(world):
    from app.services.evosense import signals as SIG
    db, org = world["db"], world["org"]
    out = RF.apply(db, org, confirm=RF.dry_run(db, org)["confirm_with"])
    db.commit()
    SIG.upsert(db, world["not_owed"], "MANUAL_OPERATOR_SIGNAL", source="manual", connector_kind="manual",
               source_reference="person-1", confidence=70, normalized_value="seen boarded up")
    db.commit()
    snapshot = _state(db, org)
    with pytest.raises(ValueError, match="Changed since the repair"):
        RF.rollback(db, org, out["run_id"])
    db.rollback()
    assert _state(db, org) == snapshot                     # nothing undone


def test_the_corrected_owner_joins_the_existing_identical_owner(db_session, sample_org):
    """Production: the appraisal district already holds the same owner under
    the full name. The repair re-links instead of creating a twin, and the
    owner conflict the misread caused is settled."""
    from app.models.evosense_models import EvoSenseOwnership
    db, org = db_session, sample_org.id
    p = _misread(db, org, v2_line(account="710001", owner1="GARCIA FILIBERTO", addr1="9 FAR AWAY LN"),
                 "100 FIRST ST")
    tad = {"source_reference": "TAD:710001:2026", "street_address": "100 FIRST ST", "city": "Fort Worth",
           "state": "TX", "county": "Tarrant", "parcel_apn": "710001",
           "owner": {"name": "GARCIA FILIBERTO", "mailing_street": "9 FAR AWAY LN", "mailing_city": "FORT WORTH",
                     "mailing_state": "TX", "mailing_zip": "76106"}}
    IN.ingest(db, org, PV.PROVIDERS["tad"], "ASSESSOR", tad)
    db.commit()
    assert len({l.owner_id for l in db.query(EvoSenseOwnership).filter_by(property_id=p.id, is_current=True)}) == 2
    rep = RF.dry_run(db, org)
    row = [x for x in rep["properties"] if x["property_id"] == p.id][0]
    assert row["owner"]["action"] == "relink" and rep["totals"]["owners_relinked_to_existing"] == 1
    owners_before = db.query(EvoSenseOwner).filter_by(organization_id=org).count()
    RF.apply(db, org, confirm=rep["confirm_with"])
    db.commit()
    links = db.query(EvoSenseOwnership).filter_by(property_id=p.id, is_current=True).all()
    assert len({l.owner_id for l in links}) == 1 and not any(l.in_conflict for l in links)
    db.refresh(p)
    assert not p.has_conflicts
    assert db.query(EvoSenseOwner).filter_by(organization_id=org).count() == owners_before   # no twin


def test_a_shared_owner_is_left_alone_and_listed(world, db_session, sample_org):
    db, org = world["db"], world["org"]
    owner = EV.CT.primary_owner(db, world["not_owed"])
    from app.models.evosense_models import EvoSenseOwnership
    db.add(EvoSenseOwnership(organization_id=org, property_id=world["owed"].id, owner_id=owner.id,
                             source="manual", is_current=False))
    db.commit()
    rep = RF.dry_run(db, org)
    row = [p for p in rep["properties"] if p["property_id"] == world["not_owed"].id][0]
    assert row["owner"]["action"] == "ambiguous"
    assert rep["totals"]["owners_ambiguous_left_alone"] >= 1


def test_routes_are_admin_only_and_the_dry_run_writes_nothing(client, world, admin_auth_headers, auth_headers):
    db, org = world["db"], world["org"]
    assert client.post("/wholesale/evosense/roll-fix/dry-run", headers=auth_headers).status_code == 403
    before = _state(db, org)
    r = client.post("/wholesale/evosense/roll-fix/dry-run", headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert _state(db, org) == before
    code = r.json()["confirm_with"]
    assert client.post("/wholesale/evosense/roll-fix/apply", headers=admin_auth_headers,
                       json={"confirm": "APPLY ROLL FIX wrong"}).status_code == 422
    out = client.post("/wholesale/evosense/roll-fix/apply", headers=admin_auth_headers, json={"confirm": code})
    assert out.status_code == 200, out.text
    rid = out.json()["run_id"]
    assert client.post("/wholesale/evosense/roll-fix/%s/rollback" % rid, headers=admin_auth_headers,
                       json={"confirm": "nope"}).status_code == 422
    assert client.post("/wholesale/evosense/roll-fix/%s/rollback" % rid, headers=admin_auth_headers,
                       json={"confirm": "ROLLBACK %s" % rid}).status_code == 200


def test_tarrant_roll_only_pause_leaves_every_other_source_and_search_running(
        client, db_session, sample_org, admin_auth_headers):
    """The stand-by plan if the fix cannot ship before the next hunt: switch
    off ONLY the Tarrant tax-roll source (Controls > Sources, or
    PATCH /wholesale/evosense/providers). Searches are not edited."""
    from app.services.evosense import hunt as HU
    db, org = db_session, sample_org.id
    keys = ("tarrant_tax_roll", "dallas_trw", "lgbs_tax_sales", "dcad", "collin_cad", "denton_gis",
            "fw_open_cases")
    for k in keys:
        PV.config(db, org, k).enabled = True
    db.commit()
    before = {p.key for _, p, _ in HU._discovery_routes(db, org)}
    assert "tarrant_tax_roll" in before
    r = client.patch("/wholesale/evosense/providers", headers=admin_auth_headers,
                     json={"key": "tarrant_tax_roll", "enabled": False})
    assert r.status_code == 200, r.text
    db.expire_all()
    every = {p.key for _, p, _ in HU._discovery_routes(db, org)}           # "New Check Out": no focus
    taxes = {p.key for _, p, _ in HU._discovery_routes(db, org, ["TAX"])}   # "Behind on Taxes"
    assert every == before - {"tarrant_tax_roll"}
    assert "tarrant_tax_roll" not in taxes and {"dallas_trw", "lgbs_tax_sales"} <= taxes
    # undo is the same switch
    assert client.patch("/wholesale/evosense/providers", headers=admin_auth_headers,
                        json={"key": "tarrant_tax_roll", "enabled": True}).status_code == 200
    db.expire_all()
    assert {p.key for _, p, _ in HU._discovery_routes(db, org)} == before


def test_relink_when_the_right_owner_is_already_linked_from_the_same_source(db_session, sample_org):
    """An earlier (correctly read) roll already linked the right owner: the
    misread link simply stops being current - no duplicate link."""
    from app.models.evosense_models import EvoSenseOwnership
    db, org = db_session, sample_org.id
    p = _misread(db, org, v2_line(account="720001", owner1="GARCIA FILIBERTO", addr1="9 FAR AWAY LN"),
                 "100 FIRST ST")
    good = TT.TarrantTaxRollReader().record_from_raw(v2_line(account="720001", owner1="GARCIA FILIBERTO",
                                                             addr1="9 FAR AWAY LN"), "x", ROLL_DATE)
    good["street_address"] = "100 FIRST ST"
    good["source_reference"] = "TCTAX:720001:20260925"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", good)
    db.commit()
    rep = RF.dry_run(db, org)
    assert [x for x in rep["properties"] if x["property_id"] == p.id][0]["owner"]["action"] == "relink"
    out = RF.apply(db, org, confirm=rep["confirm_with"])
    db.commit()
    cur = db.query(EvoSenseOwnership).filter_by(property_id=p.id, is_current=True).all()
    assert len({l.owner_id for l in cur}) == 1
    RF.rollback(db, org, out["run_id"])
    db.commit()


def test_a_person_with_a_phone_on_file_is_never_removed(world):
    """Review finding: removing a person cascades to their contact points."""
    from app.models.evosense_models import EvoSenseContactPoint, EvoSensePerson
    db, org = world["db"], world["org"]
    owner = EV.CT.primary_owner(db, world["not_owed"])
    people = db.query(EvoSensePerson).filter_by(owner_id=owner.id).all()
    assert people
    for pr in people:
        db.add(EvoSenseContactPoint(organization_id=org, person_id=pr.id, owner_id=owner.id, kind="phone",
                                    value="+18175550199", source="manual"))
    db.commit()
    n = db.query(EvoSenseContactPoint).count()
    out = RF.apply(db, org, confirm=RF.dry_run(db, org)["confirm_with"])
    db.commit()
    assert db.query(EvoSenseContactPoint).count() == n
    assert {p.id for p in people} <= {p.id for p in db.query(EvoSensePerson).filter_by(owner_id=owner.id)}
    RF.rollback(db, org, out["run_id"])
    db.commit()
    assert db.query(EvoSenseContactPoint).count() == n
