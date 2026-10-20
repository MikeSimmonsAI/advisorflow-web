"""Repair of owner names a county file cut short (owner_names.py).

Owners cut BEFORE the ingest fix stay cut. The repair:
  * previews every cut owner as SAFE / AMBIGUOUS / UNRECOVERABLE and writes nothing;
  * is SAFE only with one full name, same mailing address, every cut word in it;
  * refuses family members, other addresses and competing names;
  * applies exactly the plan behind a typed code, changes only name fields,
    skips anything changed since the preview, and can be undone.
"""
import json

import pytest

from app.models.evosense_models import (EvoSenseEvent, EvoSenseObservation, EvoSenseOwner,
                                        EvoSenseOwnership, EvoSenseProperty)
from app.services.evosense import contacts as CT
from app.services.evosense import enrichment as EN
from app.services.evosense import ingest as IN
from app.services.evosense import owner_names as ON
from app.services.evosense import providers as PV

from tests.test_wholesale_source_to_deal import county_records


def _cut_property(db, org, apn, street, mailing="1 EXAMPLE CT 200", cut="SAMPLE ROBIN AND SAMPLE P"):
    tax = json.loads(json.dumps(county_records()[0]["record"]))
    tax.update(source_reference="TCTAX:%s" % apn, parcel_apn=apn, street_address=street)
    tax["owner"].update(name=cut, mailing_street=mailing)
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", tax)
    db.flush()
    return db.query(EvoSenseProperty).filter_by(organization_id=org, parcel_apn=apn).one()


def _old_style_record(db, org, prop, name, mailing="1 EXAMPLE CT 200", ref=None):
    """A full-name record stored the way ingest did BEFORE the fix: observation
    kept, owner left cut (as in production today)."""
    rec = {"source_reference": ref or "TAD:%s:%s" % (prop.parcel_apn, name[:6]),
           "street_address": prop.street_address,
           "owner": {"name": name, "mailing_street": mailing, "mailing_city": "ARLINGTON",
                     "mailing_state": "VA", "mailing_zip": "22204"}}
    db.add(EvoSenseObservation(organization_id=org, property_id=prop.id, provider_key="tad",
                               connector_kind="real", capability="ASSESSOR",
                               source_reference=rec["source_reference"], payload=json.dumps(rec),
                               match_type="apn"))
    db.flush()


@pytest.fixture()
def cases(db_session, sample_org):
    db, org = db_session, sample_org.id
    safe = _cut_property(db, org, "A1", "1 SAFE ST")
    _old_style_record(db, org, safe, "ROBIN & PAT ETAL SAMPLE")
    kin = _cut_property(db, org, "A2", "2 KIN ST", cut="DOE MARY AND DOE J", mailing="2 KIN ST")
    _old_style_record(db, org, kin, "DOE JOHN", mailing="2 KIN ST")                  # a relative
    away = _cut_property(db, org, "A3", "3 AWAY ST", cut="ROE ANN AND ROE B", mailing="3 AWAY ST")
    _old_style_record(db, org, away, "ANN & BOB ROE", mailing="99 ELSEWHERE RD")     # other address
    two = _cut_property(db, org, "A4", "4 TWO ST", cut="LEE KIM AND LEE T", mailing="4 TWO ST")
    _old_style_record(db, org, two, "KIM & TOM LEE", mailing="4 TWO ST", ref="TAD:A4:1")
    _old_style_record(db, org, two, "KIM & TINA LEE", mailing="4 TWO ST", ref="TAD:A4:2")  # competing
    lone = _cut_property(db, org, "A5", "5 LONE ST", cut="POE EDGAR AND POE V", mailing="5 LONE ST")
    db.commit()
    return {"safe": safe, "kin": kin, "away": away, "two": two, "lone": lone}


def _owner(db, prop):
    return CT.primary_owner(db, prop)


def test_preview_classifies_strictly_and_writes_nothing(db_session, sample_org, cases):
    db, org = db_session, sample_org.id
    before = {o.id: (o.display_name, o.name_truncated) for o in db.query(EvoSenseOwner).all()}
    events = db.query(EvoSenseEvent).count()
    pv = ON.preview(db, org)
    assert pv["counts"] == {"safe": 1, "ambiguous": 3, "unrecoverable": 1}
    by = {r["properties"][0]: r for r in pv["rows"]}
    assert by["1 SAFE ST"]["verdict"] == "safe" and by["1 SAFE ST"]["proposed"] == "ROBIN & PAT ETAL SAMPLE"
    assert "relative" in by["2 KIN ST"]["reason"]
    assert "different mailing address" in by["3 AWAY ST"]["reason"]
    assert "More than one" in by["4 TWO ST"]["reason"]
    assert by["5 LONE ST"]["verdict"] == "unrecoverable"
    db.expire_all()
    assert {o.id: (o.display_name, o.name_truncated) for o in db.query(EvoSenseOwner).all()} == before
    assert db.query(EvoSenseEvent).count() == events


def test_apply_needs_the_code_changes_only_names_and_lifts_the_lookup_hold(
        client, db_session, sample_org, cases, admin_auth_headers):
    db, org = db_session, sample_org.id
    safe_owner = _owner(db, cases["safe"])
    assert "cut this owner's name" in EN.decide(db, cases["safe"], None).reasons
    keep = (safe_owner.mailing_street, safe_owner.mailing_key, safe_owner.resolution)
    links = db.query(EvoSenseOwnership).count()
    pv = client.get("/wholesale/evosense/owner-names/preview", headers=admin_auth_headers).json()
    code = pv["plan"]["confirm_with"]
    assert pv["plan"]["changes"] == 1
    assert client.post("/wholesale/evosense/owner-names/apply", headers=admin_auth_headers,
                       json={"confirm": "APPLY wrong"}).status_code == 422
    r = client.post("/wholesale/evosense/owner-names/apply", headers=admin_auth_headers, json={"confirm": code})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 1
    db.expire_all()
    o = _owner(db, cases["safe"])
    assert o.display_name == "ROBIN & PAT ETAL SAMPLE" and not o.name_truncated
    assert (o.mailing_street, o.mailing_key, o.resolution) == keep                  # nothing else touched
    assert db.query(EvoSenseOwnership).count() == links
    for k in ("kin", "away", "two", "lone"):                                        # ambiguous untouched
        assert _owner(db, cases[k]).name_truncated
    assert "cut this owner's name" not in EN.decide(db, cases["safe"], None).reasons
    ev = db.query(EvoSenseEvent).filter_by(action="owner.full_name_repaired").one()
    assert json.loads(ev.details)["before"]["display_name"] == "SAMPLE ROBIN AND SAMPLE P"

    # Undo puts it back exactly.
    plan_code = code.split()[1]
    bad = client.post("/wholesale/evosense/owner-names/revert", headers=admin_auth_headers,
                      json={"plan": plan_code, "confirm": "nope"})
    assert bad.status_code == 422
    r = client.post("/wholesale/evosense/owner-names/revert", headers=admin_auth_headers,
                    json={"plan": plan_code, "confirm": "REVERT %s" % plan_code})
    assert r.status_code == 200 and r.json()["reverted"] == 1
    db.expire_all()
    o = _owner(db, cases["safe"])
    assert o.display_name == "SAMPLE ROBIN AND SAMPLE P" and o.name_truncated


def test_owner_changed_since_preview_is_skipped_not_forced(db_session, sample_org, cases):
    db, org = db_session, sample_org.id
    pl = ON.plan(db, org)
    o = _owner(db, cases["safe"])
    o.display_name = "TYPED BY A PERSON"
    db.flush()
    out = ON.apply(db, org, pl, confirm=pl["hash"])
    assert out["applied"] == 0 and out["skipped_changed_since_preview"] == [o.id]
    assert o.display_name == "TYPED BY A PERSON"


def test_plan_is_bound_to_its_workspace_and_its_code(db_session, sample_org, cases):
    db, org = db_session, sample_org.id
    pl = ON.plan(db, org)
    with pytest.raises(ValueError):
        ON.apply(db, "another-org", pl, confirm=pl["hash"])
    tampered = dict(pl, changes=[dict(pl["changes"][0], after=dict(pl["changes"][0]["after"], display_name="X"))])
    with pytest.raises(ValueError):
        ON.apply(db, org, tampered, confirm=pl["hash"])


def test_a_plain_advisor_cannot_apply(client, db_session, sample_org, cases, auth_headers):
    r = client.post("/wholesale/evosense/owner-names/apply", headers=auth_headers, json={"confirm": "APPLY x"})
    assert r.status_code == 403


@pytest.mark.parametrize("cut,full,same", [
    ("SAMPLE ROBIN AND SAMPLE P", "ROBIN & PAT ETAL SAMPLE", True),
    ("VIGIL SABINO SR AND VIGIL MARG", "SABINO & MARGARIT VIGIL", True),     # suffix dropped
    ("GRISHAM LINDA M AND GRISHAM JE", "LINDA & JEFF GRISHAM", True),       # initial dropped
    ("OLIVER JOHN ROSS AND OLIVER FR", "JOHN & FRANCIE OLIVER", True),      # middle name dropped
    ("WHITE LYDIA AND WHITE THOMAS E", "LYDIA & THOMAS WHITE", True),       # trailing initial
    ("PULIDO ALEJANDRO ZARAGOZA ETAL", "PULIDO ALEJANDRO ZARAGOZA and QUINTERO SANDRA", True),
    ("GONZALEZ-ENRIQUEZ LEOBARDO ETA", "GONZALEZ-ENRIQUEZ LEOBARDO and KOBEL YESICA", True),
    # different people stay different
    ("SAMPLE ROBIN AND SAMPLE P", "SAMPLE JOHN", False),
    ("SAMPLE ROBIN AND SAMPLE P", "ROBIN & JOHN SAMPLE", False),           # P is not JOHN
    ("DOE MARY AND DOE J", "DOE JOHN", False),
    ("GARCIA MARIA", "LOPEZ MARIA", False),
    ("MAROOF JACQUELINE E EST THE JACQUELINE E MAROOF REV TR", "MAROOF JACQUELINE E EST", False),
])
def test_completion_rule(cut, full, same):
    assert IN.completes_cut_name(cut, full) is same


def test_a_misread_tax_roll_owner_is_completed_from_its_own_raw_line(db_session, sample_org):
    """Owners created Oct 17-19 from tax-roll lines read under the wrong
    layout: the preview re-reads the saved raw line with the current reader
    and uses the full name the county actually published."""
    from tests.test_evosense_tarrant_roll_layout import ROLL_DATE, v2_line
    from app.services.evosense.sources import tarrant as TT
    db, org = db_session, sample_org.id
    full = "PRESTON HOYLE GILLHAM AND LETHA DIANNE PIRKLE"
    line = v2_line(account="555001", owner1=full, prior_due="000000900.00")
    old = {k: line[a - 1:a - 1 + n].strip() for k, a, n in TT.MASTER}      # the misread
    old["litig"] = ""
    rec = TT.TarrantTaxRollReader().record(old, TT.classify(old, ROLL_DATE), line, "x", ROLL_DATE)
    rec["street_address"] = "2914 MC KINLEY AVE"
    rec["owner"]["name_truncated"] = True                                     # as stored in production
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", rec)
    db.commit()
    prop = db.query(EvoSenseProperty).filter_by(organization_id=org, parcel_apn="555001").one()
    cut = _owner(db, prop)
    assert cut.name_truncated and cut.display_name != full
    pv = ON.preview(db, org)
    row = [r for r in pv["rows"] if r["owner_id"] == cut.id][0]
    assert row["verdict"] == "safe" and row["proposed"] == full, row
