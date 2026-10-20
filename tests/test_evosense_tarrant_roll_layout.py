"""Tarrant tax roll: the Oct 2026 layout change must be read correctly, and a
layout the reader does not understand must stop the source - never become tax
evidence.

Production Oct 17-19, 2026: the roll widened its owner fields from 30 to 50
characters; every later field moved 40 characters right. Read with the old
layout, the land value became "prior-year taxes due $534,600,000,012" and a
date fragment became a "tax suit" on 1,017 properties. Re-read correctly, 12
of those 1,017 were delinquent.
"""
from datetime import datetime

import pytest

from app.services.evosense.sources import base as B
from app.services.evosense.sources import tarrant as TT

from tests.test_evosense_dfw_sources import master_line, no_network, write_zip  # noqa: F401 (fixture)

ROLL_DATE = datetime(2026, 10, 16)


def v2_line(**f):
    """A line in the layout published from Oct 16, 2026 (781 characters)."""
    buf = [" "] * 781

    def put(name, value, zero=False):
        _, a, n = next(x for x in TT.MASTER_V2 if x[0] == name)
        v = str(value)
        v = v.rjust(n, "0") if zero else v.ljust(n)
        buf[a - 1:a - 1 + n] = list(v[:n])
    put("account", f.get("account", "143057"), zero=True)
    put("sptb", f.get("sptb", "039"))
    put("roll", "01")
    put("legal", "TEST ADDITION BLOCK 3 LOT 13")
    put("street_name", "MC KINLEY AVE")
    put("street_no", "2914", zero=True)
    put("year_built", "1950")
    put("owner1", f.get("owner1", "GARCIA FILIBERTO"))
    put("owner2", f.get("owner2", ""))
    put("addr1", f.get("addr1", "2914 MCKINLEY AVE"))
    put("ocity", "FORT WORTH")
    put("ostate", "TX")
    put("ozip", "76106")
    put("def_start", "01/01/9999")
    put("def_end", "01/01/9999")
    put("deed_date", "03/03/2017")
    put("delq_date", f.get("delq_date", "02/01/2027"))
    put("land", "53460", zero=True)
    put("impr", "125633", zero=True)
    put("levy", "000003988.48")
    put("cur_due", f.get("cur_due", "000003988.48"))
    put("prior_due", f.get("prior_due", "000000000.00"))
    put("litig", f.get("litig", ""))
    return "".join(buf)


def test_new_layout_is_recognised_and_this_years_bill_is_not_delinquency():
    line = v2_line()
    r, layout, _ = TT.parse_with_layout(line)
    assert layout == "2026-10"
    assert (r["owner1"], r["addr1"], r["ozip"], r["delq_date"]) == (
        "GARCIA FILIBERTO", "2914 MCKINLEY AVE", "76106", "02/01/2027")
    rec = TT.TarrantTaxRollReader().record_from_raw(line, "x", ROLL_DATE)
    # The 2026 bill is due next February: not delinquency, no signal, no "suit".
    assert rec["signals"] == []
    assert rec["owner"]["mailing_zip"] == "76106" and rec["owner"]["name_truncated"] is False


def test_new_layout_prior_year_balance_is_read_from_the_right_columns():
    rec = TT.TarrantTaxRollReader().record_from_raw(v2_line(prior_due="000001250.40", litig="Y"), "x", ROLL_DATE)
    sig = {s["type"]: s for s in rec["signals"]}
    assert "prior-year taxes due $1,250.40" in sig["TAX_DELINQUENT"]["value"]
    assert sig["TAX_SUIT"]["value"] == "tax-roll litigation indicator set (Y)"


def test_fifty_character_owner_names_are_whole_and_cut_ones_are_still_caught():
    long_name = "PRESTON HOYLE GILLHAM AND LETHA DIANNE PIRKLE"
    assert TT.name_truncated(v2_line(owner1=long_name)) is False
    assert TT.name_truncated(v2_line(owner1="X" * 50)) is True
    rec = TT.TarrantTaxRollReader().record_from_raw(v2_line(owner1=long_name), "x", ROLL_DATE)
    assert rec["owner"]["name"] == long_name


def test_old_layout_lines_still_read_as_before():
    line = master_line(prior_due="000001250.40")
    r, layout, _ = TT.parse_with_layout(line)
    assert layout == "2025" and r["owner1"] == "SMITH JOHN A"
    assert TT.name_truncated(master_line(owner1="ANDRADE ANGEL AND MURILLO GLOR")) is True


def test_a_new_line_read_with_the_old_layout_would_be_refused():
    """The exact failure of Oct 17: forcing the old layout on a new line
    produces fields the validator rejects."""
    line = v2_line(prior_due="000001250.40")
    forced = {k: line[a - 1:a - 1 + n].strip() for k, a, n in TT.MASTER}
    assert TT.layout_problems(forced), "the old layout must not read a new line as valid"


def test_a_line_neither_layout_reads_is_refused_not_guessed():
    line = v2_line()
    broken = line[:600] + "XX/XX/XXXX" + line[610:]               # delinquency date garbage (v2 at 612)
    broken = broken[:571] + "ZZ" + broken[573:]                    # and the old position too
    with pytest.raises(B.SourceError) as e:
        TT.parse_with_layout(broken)
    assert e.value.code == B.SOURCE_FORMAT_CHANGED


def test_a_file_of_unreadable_lines_stops_the_source(tmp_path, monkeypatch, no_network):
    junk = ("9" * 780 + "\n") * 80
    bad = write_zip(tmp_path / "bad.zip", {"Master.dat": junk})
    monkeypatch.setenv("EVOSENSE_SRC_TARRANT_TAX_ROLL_PATH", bad)
    with pytest.raises(B.SourceError) as e:
        TT.TarrantTaxRollReader().discover(limit=5)
    assert e.value.code == B.SOURCE_FORMAT_CHANGED


def test_discovery_reads_a_new_layout_file(tmp_path, monkeypatch, no_network):
    lines = [v2_line(account=str(1000 + i), prior_due="000000900.00" if i % 2 else "000000000.00")
             for i in range(6)]
    f = write_zip(tmp_path / "TaxRoll20261016.zip", {"Master.dat": "\n".join(lines) + "\n"})
    monkeypatch.setenv("EVOSENSE_SRC_TARRANT_TAX_ROLL_PATH", f)
    res = TT.TarrantTaxRollReader().discover(limit=10, today=ROLL_DATE)
    assert [r["parcel_apn"] for r in res["records"]] == ["1001", "1003", "1005"]   # prior-year balances only
    assert res["stats"]["layout_2026-10"] == 6
    for r in res["records"]:
        assert "$900.00" in r["signals"][0]["value"]


def test_rederive_cleans_up_records_read_with_the_wrong_layout(db_session, sample_org):
    """What production holds today: a new-layout line saved as raw evidence,
    with the signals the OLD layout invented. The existing re-derive tool
    (dry run -> apply, with rollback) re-reads the raw line with this fix and
    retracts them."""
    from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
    from app.services.evosense import ingest as IN
    from app.services.evosense import providers as PV
    from app.services.evosense import reprocess as RP
    db, org = db_session, sample_org.id
    line = v2_line(account="143057")                       # this year's bill only: not delinquent
    old = {k: line[a - 1:a - 1 + n].strip() for k, a, n in TT.MASTER}   # the Oct 17 misread
    old["litig"] = "14"                                   # as read in production from the shifted line
    c = TT.classify(old, ROLL_DATE)
    assert c["delinquent"] and c["prior_due"] > 1e9         # "$534,600,000,012" class of nonsense
    bad = TT.TarrantTaxRollReader().record(old, c, line, "https://example/TaxRoll20261016.zip", ROLL_DATE)
    bad["street_address"] = "2914 MC KINLEY AVE"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", bad)
    db.commit()
    prop = db.query(EvoSenseProperty).filter_by(organization_id=org, parcel_apn="143057").one()
    kinds = {s.signal_type for s in db.query(EvoSenseSignal).filter_by(property_id=prop.id, active=True)}
    assert {"TAX_DELINQUENT", "TAX_SUIT"} <= kinds
    run = RP.dry_run(db, org)
    db.commit()
    assert db.query(EvoSenseSignal).filter_by(property_id=prop.id, active=True,
                                              signal_type="TAX_SUIT").count() == 1       # dry run wrote nothing
    RP.apply(db, org, run, user=None)
    db.commit()
    left = {s.signal_type for s in db.query(EvoSenseSignal).filter_by(property_id=prop.id, active=True)}
    assert not ({"TAX_DELINQUENT", "TAX_SUIT"} & left), left
