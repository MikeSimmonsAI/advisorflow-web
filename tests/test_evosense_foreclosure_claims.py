"""Foreclosure-sale claims on imported lists (#340 / #341).

A list's NAME is a label. Scores, dates and matches come only from the rows
and from public records; a scheduled sale is a CLAIM until a clerk / trustee
notice confirms it; property-tax status never cancels a mortgage foreclosure.
"""
from datetime import date, datetime, timedelta

import pytest

from app.models.evosense_models import EvoSenseOwnership, EvoSenseProperty, EvoSenseSignal
from app.models.models import Organization
from app.services.evosense import cohort as CO
from app.services.evosense import common as C
from app.services.evosense import evaluate as EV
from app.services.evosense import hunt as HU
from app.services.evosense import scoring as SC

SOON = (date.today() + timedelta(days=11))
HEAD = "Address,City,State,Zip,Recipient Name,Property Type,Loan Type,Estimated Equity,Property Value,Unpaid Balance"
ROWS = ["5329 SPANISH OAKS,FRISCO,TX,75034,JOYCE A/KENNETH W JONES,Single Family Residential,"
        "Fannie Mae / Freddie Mac,2524938,3362577,837639",
        "899 S FM 1138,ROYSE CITY,TX,75189,TRACY W/WENDY L PETERS,Single Family Residential,ARM,858938,994723,135785"]
LIST = "\n".join([HEAD] + ROWS) + "\n"                      # the real file's columns: no sale date
WITH_DATES = ("street_address,city,state,zip_code,sale_date\n"
              "1 Elm St,Dallas,TX,75201,11/3/2026\n"
              "2 Elm St,Dallas,TX,75201,\"Nov 3, 2026\"\n"
              "3 Elm St,Dallas,TX,75201,2026-11-03\n"
              "4 Elm St,Dallas,TX,75201,\n")


def _props(db, org_id):
    return {p.street_address.upper(): p for p in db.query(EvoSenseProperty).filter_by(organization_id=org_id).all()}


def _score(db, p):
    return SC.property_opportunity(p, EV.stacked_signals(db, p), EV.strategy_for(db, p),
                                   weights=EV.score_weights(db, p.organization_id), owner=EV.CT.primary_owner(db, p),
                                   options=EV.score_options(db, p.organization_id))


def _fc(db, p):
    return [s for s in db.query(EvoSenseSignal).filter_by(property_id=p.id, active=True).all()
            if s.signal_type == "PRE_FORECLOSURE"]


# 1 + 2 ── the file name changes nothing; "2010" is never a year ───────────────

def test_renaming_the_file_changes_nothing(db_session, sample_org, sample_advisor):
    other = Organization(name="Twin Org", slug="twin-org", plan="standard", platform_id=sample_org.platform_id)
    db_session.add(other)
    db_session.commit()
    HU.import_csv(db_session, sample_org.id, LIST, user=sample_advisor, filename="Top100-100K-2010-Nov03-Dallas.csv",
                  declared_sale_date=SOON.isoformat())
    HU.import_csv(db_session, other.id, LIST, user=sample_advisor, filename="Boo Boo Shushu.csv",
                  declared_sale_date=SOON.isoformat())
    db_session.commit()
    a, b = _props(db_session, sample_org.id), _props(db_session, other.id)
    assert set(a) == set(b)
    for k in a:
        sa, sb = _score(db_session, a[k]), _score(db_session, b[k])
        assert sa["value"] == sb["value"] and [f["label"] for f in sa["factors"]] == [f["label"] for f in sb["factors"]]
        assert a[k].estimated_value_source == b[k].estimated_value_source
        assert a[k].equity_pct == b[k].equity_pct


def test_2010_in_a_file_name_is_never_a_date(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, LIST, user=sample_advisor, filename="Top100-100K-2010-Nov03-Dallas.csv")
    db_session.commit()
    for p in _props(db_session, sample_org.id).values():
        assert _fc(db_session, p) == []                     # no sale date in the rows -> no claim
        assert "2010" not in (p.estimated_value_source or "")
        assert "Nov03" not in (p.estimated_value_source or "")
    assert HU._parse_list_date("2010") is None and HU._parse_list_date("Top100-100K-2010-Nov03-Dallas") is None


# 3 + 4 ── dates in the rows parse; missing stays missing ─────────────────────

def test_november_3_2026_parses_in_every_common_spelling_and_missing_stays_missing(db_session, sample_org,
                                                                                    sample_advisor):
    out = HU.import_csv(db_session, sample_org.id, WITH_DATES, user=sample_advisor)
    db_session.commit()
    assert out["counts"]["sale_dates"] == 3 and out["counts"]["sale_dates_declared"] == 0
    props = _props(db_session, sample_org.id)
    for street in ("1 ELM ST", "2 ELM ST", "3 ELM ST"):
        (s,) = _fc(db_session, props[street])
        assert C.jload(s.provenance, {})["sale_date"] == "2026-11-03"
    assert _fc(db_session, props["4 ELM ST"]) == []
    for v in ("11/3/2026", "11/03/2026", "Nov 3, 2026", "November 3rd, 2026", "2026-11-03", "11-03-2026"):
        assert HU._parse_list_date(v).date() == date(2026, 11, 3), v


# 5 ── a claim keeps its provenance and reads as a claim ─────────────────────

def test_an_imported_sale_date_is_a_claim_with_provenance(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code,sale_date\n"
                  "1 Elm St,Dallas,TX,75201,%s\n" % SOON.isoformat(), user=sample_advisor)
    db_session.commit()
    p = _props(db_session, sample_org.id)["1 ELM ST"]
    (s,) = _fc(db_session, p)
    prov = C.jload(s.provenance, {})
    assert s.source == "csv_import" and prov["claim"] == "imported" and s.case_status == "scheduled"
    assert "not verified" in s.normalized_value
    fc = CO.foreclosure_status([s])
    assert fc["status"] == "CLAIMED" and fc["verified"] is False and fc["days"] == 11
    terms = SC.foreclosure_terms({"evidence": [{"source": "csv_import", "provenance": prov}]})
    assert terms["urgency"] == 8 and "claimed, unverified" in terms["urgency_label"]


# 6 ── current taxes never cancel a foreclosure ───────────────────────────────

def test_current_taxes_do_not_cancel_a_scheduled_foreclosure(db_session, sample_org, sample_advisor):
    owner = {"status": "VERIFIED", "name": "SAMPLE OWNER", "flags": []}
    fc = {"status": "CLAIMED", "days": 11, "sale_date": SOON.isoformat(), "verified": False}
    tax = {"status": "CURRENT"}
    assert CO._priority(20, owner, tax, fc, ["PRE_FORECLOSURE"]) == "A"
    assert "Confirm the" in CO._next_action("A", owner, tax, {"phones": 0, "emails": 0}, dict(fc, county="Dallas"))


def test_a_passed_sale_date_is_not_current_distress():
    prov = {"claim": "imported", "sale_date": (date.today() - timedelta(days=2)).isoformat()}
    t = SC.foreclosure_terms({"evidence": [{"source": "csv_import", "provenance": prov}]})
    assert t["passed"] is True and t["urgency"] == 0 and "has passed" in t["label"]


# 7 + 8 ── re-reading the list: no duplicates, corrections survive ────────────

def test_reimport_and_reattach_create_no_duplicates_and_keep_owner_corrections(db_session, sample_org,
                                                                                sample_advisor, monkeypatch):
    from app.services.evosense import identity_fix as IF
    addresses_only = ("street_address,city,state,zip_code,record_id\n"
                      "5329 SPANISH OAKS,Frisco,TX,75034,top100-x:1\n899 S FM 1138,Royse City,TX,75189,top100-x:2\n")
    HU.import_csv(db_session, sample_org.id, addresses_only, user=sample_advisor)
    again = HU.import_csv(db_session, sample_org.id, addresses_only, user=sample_advisor)
    assert again["counts"]["seen"] == 2
    p = _props(db_session, sample_org.id)["899 S FM 1138"]
    IF.correct(db_session, sample_org.id, p, {"county": "Collin", "parcel_apn": "2077765",
                                              "street_address": "899 S FM 1138 RD"},
               user=sample_advisor, evidence_url="https://data.texas.gov/x", note="county record", lookup=False)
    res = CO.reattach(db_session, sample_org.id, "top100-x", LIST, user=sample_advisor, filename="anything.csv",
                      declared_sale_date=SOON.isoformat())
    assert res["matched_properties"] == 2 and res["counts"].get("unmatched", 0) == 0
    res2 = CO.reattach(db_session, sample_org.id, "top100-x", LIST, user=sample_advisor, filename="renamed.csv",
                       declared_sale_date=SOON.isoformat())
    assert res2["counts"].get("seen") == 2                                  # idempotent
    props = _props(db_session, sample_org.id)
    assert len(props) == 2
    p = props["899 S FM 1138 RD"]
    assert p.parcel_apn == "2077765" and p.county == "Collin"              # the correction survived
    assert p.estimated_value == 994723 and p.mortgage_balance == 135785 and p.equity_pct == 86
    (s,) = _fc(db_session, p)
    assert C.jload(s.provenance, {})["sale_date"] == SOON.isoformat()
    rows = {r["street_address"]: r for r in CO.rows(db_session, sample_org.id, "top100-x")}
    assert rows["899 S FM 1138 RD"]["list_claims"]["list_owner_name"] == "TRACY W/WENDY L PETERS"
    assert rows["899 S FM 1138 RD"]["foreclosure"]["status"] == "CLAIMED"


# 9 ── verified evidence raises the score above the claim ─────────────────────

def test_a_verified_notice_scores_above_the_claim(db_session, sample_org, sample_advisor):
    from types import SimpleNamespace
    from app.services.evosense import ingest as IN
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code,sale_date\n"
                  "1 Elm St,Dallas,TX,75201,%s\n" % SOON.isoformat(), user=sample_advisor)
    db_session.commit()
    p = _props(db_session, sample_org.id)["1 ELM ST"]
    before = _score(db_session, p)["value"]
    clerk = SimpleNamespace(key="clerk_notice_test", connector_kind="real", label="Clerk notice (test)",
                            capabilities=("FORECLOSURE",))
    IN.ingest(db_session, sample_org.id, clerk, "FORECLOSURE",
              {"source_reference": "CLERK:2026-0001", "street_address": "1 Elm St", "city": "Dallas", "state": "TX",
               "zip_code": "75201", "signals": [{"type": "PRE_FORECLOSURE", "confidence": 90, "sale_date": SOON.isoformat(),
                                                  "value": "Notice of Substitute Trustee Sale", "case_status": "scheduled"}]},
              target=p)
    db_session.commit()
    after = _score(db_session, p)
    assert after["value"] > before
    assert any("verified notice" in f["label"] for f in after["factors"])
    assert CO.foreclosure_status(_fc(db_session, p))["status"] == "VERIFIED NOTICE"


# 10 ── nothing changes for a property with no foreclosure claim ──────────────

def test_scoring_without_a_foreclosure_signal_is_unchanged(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code,signals\n"
                  "7 Oak St,Dallas,TX,75201,VACANT\n", user=sample_advisor)
    db_session.commit()
    p = _props(db_session, sample_org.id)["7 OAK ST"]
    sc = _score(db_session, p)
    assert not any("oreclosure" in f["label"] for f in sc["factors"])
    assert sc["value"] is not None
