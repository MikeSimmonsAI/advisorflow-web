"""Distress lists are a LIST KIND of the one EvoSense import, not new importers.

A tax-sale list, a code export, clerk postings, a driving-for-dollars sheet: the
same ingest -> identity/dedupe -> signals -> scoring as every provider, with the
row's own evidence attached and labelled as the list's claim.
"""
from datetime import datetime

import pytest

from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
from app.models.models import Organization
from app.services.evosense import hunt as HU

TAX = ("street_address,city,state,zip_code,case_number,signal_date,case_status,amount\n"
       "101 Distress Ln,Dallas,TX,75215,TX-2026-001,2026-05-01,open,4312.55\n"
       "102 Distress Ln,Dallas,TX,75215,,03/15/2026,,\n")


def _sigs(db, org_id, street):
    p = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == org_id,
                 EvoSenseProperty.street_address.ilike("%" + street + "%")).one())
    return p, (db.query(EvoSenseSignal)
               .filter(EvoSenseSignal.property_id == p.id, EvoSenseSignal.active.is_(True)).all())


def test_a_tax_list_asserts_its_signal_with_the_rows_own_evidence(db_session, sample_org, sample_advisor):
    out = HU.import_csv(db_session, sample_org.id, TAX, user=sample_advisor, filename="tax.csv",
                        list_kind="tax_delinquent", list_source="Dallas County tax office 2026-09")
    assert out["counts"]["created"] == 2 and out["list_kind"] == "tax_delinquent"
    p, sigs = _sigs(db_session, sample_org.id, "101 DISTRESS")
    (s,) = [x for x in sigs if x.signal_type == "TAX_DELINQUENT"]
    assert s.source_reference == "list:tax_delinquent:TX-2026-001"
    assert s.effective_at.date() == datetime(2026, 5, 1).date()
    assert s.case_status == "open" and s.raw_value == "4312.55"
    assert "Dallas County tax office 2026-09" in s.normalized_value
    assert s.confidence == 60                                    # a list's claim, not a verified record
    assert "not verified" in (s.provenance or "")
    _, sigs2 = _sigs(db_session, sample_org.id, "102 DISTRESS")
    assert [x.signal_type for x in sigs2 if x.signal_type == "TAX_DELINQUENT"]


def test_a_rows_own_signals_column_wins_over_the_list_default(db_session, sample_org, sample_advisor):
    csv_text = "street_address,city,state,zip_code,signals\n7 Vacant Ct,Dallas,TX,75215,VACANT\n"
    HU.import_csv(db_session, sample_org.id, csv_text, user=sample_advisor, list_kind="driving_for_dollars")
    _, sigs = _sigs(db_session, sample_org.id, "7 VACANT")
    types = {s.signal_type for s in sigs}
    assert "VACANT" in types and "DISTRESSED_CONDITION" not in types


def test_the_same_house_on_two_lists_is_two_pieces_of_evidence(db_session, sample_org, sample_advisor):
    row = "street_address,city,state,zip_code\n55 Twice Rd,Dallas,TX,75215\n"
    HU.import_csv(db_session, sample_org.id, row, user=sample_advisor, list_kind="tax_delinquent")
    HU.import_csv(db_session, sample_org.id, row, user=sample_advisor, list_kind="code_enforcement")
    props = (db_session.query(EvoSenseProperty)
             .filter(EvoSenseProperty.organization_id == sample_org.id,
                     EvoSenseProperty.street_address.ilike("%55 TWICE%")).all())
    assert len(props) == 1                                       # deduped to one property
    _, sigs = _sigs(db_session, sample_org.id, "55 TWICE")
    assert {"TAX_DELINQUENT", "CODE_VIOLATION"} <= {s.signal_type for s in sigs}
    again = HU.import_csv(db_session, sample_org.id, row, user=sample_advisor, list_kind="tax_delinquent")
    assert again["counts"].get("seen") == 1                      # re-import is idempotent


def test_bad_dates_are_reported_and_unknown_kinds_refused(db_session, sample_org, sample_advisor):
    bad = "street_address,city,state,zip_code,signal_date\n9 Date St,Dallas,TX,75215,sometime\n"
    out = HU.import_csv(db_session, sample_org.id, bad, user=sample_advisor, list_kind="lien")
    assert out["counts"]["rejected"] == 1 and "signal_date" in out["rejected"][0]["reason"]
    with pytest.raises(ValueError):
        HU.import_csv(db_session, sample_org.id, bad, user=sample_advisor, list_kind="facebook_scrape")


def test_every_list_kind_maps_to_a_catalog_signal():
    from app.services.evosense.signals import CATALOG
    kinds = HU.distress_list_kinds()
    assert {k["key"] for k in kinds} >= {"tax_delinquent", "tax_sale", "code_enforcement",
                                         "pre_foreclosure", "foreclosure_filing", "tax_suit",
                                         "lien", "probate", "driving_for_dollars"}
    assert all(k["signal"] in CATALOG for k in kinds)


def test_api_list_kinds_and_import_are_tenant_scoped(client, db_session, auth_headers, sample_org):
    r = client.get("/wholesale/evosense/import/list-kinds", headers=auth_headers)
    assert r.status_code == 200 and r.json()["list_kinds"]
    files = {"file": ("tax.csv", TAX.encode(), "text/csv")}
    r = client.post("/wholesale/evosense/import", headers=auth_headers, files=files,
                    data={"list_kind": "tax_delinquent", "list_source": "Test list"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["created"] == 2
    r = client.post("/wholesale/evosense/import", headers=auth_headers, files=files,
                    data={"list_kind": "not_a_kind"})
    assert r.status_code == 422
    other = Organization(name="Other EvoSense", slug="other-es-lists", plan="enterprise")
    db_session.add(other)
    db_session.commit()
    assert (db_session.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == other.id).count()) == 0
