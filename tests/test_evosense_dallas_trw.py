"""Dallas County Tax Office TRW file (layout TRW-02): delinquent residential
accounts for the county taxing unit, added up across tax years. No network:
a synthetic zip in the published layout is pointed to by the local override."""
import zipfile
from datetime import datetime

import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import dallas_trw as T

MEMBER = "usr2/spool/act/flat404.DALLASCOUNTY.20261005.754635"


def line(**kw):
    d = dict(account="00000111111000000", year="2024", jur="1002", cad="00000111111000000",
             levy=100000, hs="N", paid="", due="20250131", bal=100000, suit=" ", cause="",
             bank=" ", deferral=" ", cat="A11", owner="SMITH JOHN", a2="", a3="123 ELM ST",
             a4="", city="DALLAS", st="TX", zip="75201", roll="R", sno="00004930",
             sname="BROOKVIEW DR, DA", agree="N", total=150000)
    d.update(kw)
    buf = [" "] * 520

    def put(start, length, v, right=False):
        v = str(v)
        v = v.rjust(length, "0") if right else v.ljust(length)
        buf[start - 1:start - 1 + length] = list(v[:length])

    put(1, 34, d["account"]); put(35, 4, d["year"]); put(39, 4, d["jur"]); put(43, 34, d["cad"])
    put(77, 11, d["levy"], True); put(88, 1, d["hs"]); put(93, 8, d["paid"]); put(101, 8, d["due"])
    put(111, 11, d["bal"], True); put(122, 1, d["suit"]); put(123, 40, d["cause"]); put(163, 1, d["bank"])
    put(219, 1, d["deferral"]); put(222, 4, d["cat"]); put(226, 40, d["owner"]); put(266, 40, d["a2"])
    put(306, 40, d["a3"]); put(346, 40, d["a4"]); put(386, 40, d["city"]); put(426, 2, d["st"])
    put(428, 12, d["zip"]); put(440, 1, d["roll"]); put(441, 8, d["sno"]); put(449, 40, d["sname"])
    put(489, 1, d["agree"]); put(490, 11, d["total"], True)
    return "".join(buf)


@pytest.fixture
def trw(tmp_path, monkeypatch):
    def build(lines):
        p = tmp_path / "trwfile.754635.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(MEMBER, "\r\n".join(lines) + "\r\n")
            z.writestr("usr2/spool/act/trwfile_layout.txt.754635", "TRW-02")
        monkeypatch.setenv("EVOSENSE_SRC_DALLAS_TRW_PATH", str(p))
        return p
    return build


TODAY = datetime(2026, 10, 11)

# Sorted like the real file: jurisdiction, then TAX YEAR, then account.
ROWS = [
    line(account="A1", year="2022", total=50000, suit="J", cause="TX-23-01234"),
    line(account="A2", year="2022", total=90000, deferral="D"),          # 65+/disabled deferral
    line(account="A1", year="2023", total=60000),
    line(account="A3", year="2023", total=80000, bank="Y"),              # bankruptcy stay
    line(account="A4", year="2023", total=10000),                        # under the minimum
    line(account="A5", year="2023", total=70000, cat="F10"),             # commercial
    line(account="A6", year="2023", total=70000, roll="P"),              # personal property
    line(account="A1", year="2024", total=70000, owner="SMITH JANE", hs="Y"),
    line(account="A7", year="2024", total=40000, agree="Y"),
    line(account="A8", year="2025", total=99000, due="20270131"),        # not yet due
    line(account="A9", year="2024", total=0, bal=0),                     # paid
    line(account="B1", year="2024", total=900000, jur="1021"),           # another unit
]


def test_adds_up_years_per_account_and_skips_protected_accounts(trw):
    trw(ROWS)
    res = T.TrwReader().discover(limit=10, today=TODAY)
    by = {r["source_reference"]: r for r in res["records"]}
    assert set(by) == {"DALTRW:A1", "DALTRW:A7"}
    a1 = by["DALTRW:A1"]
    ev = a1["_evidence"]
    assert ev["years"] == ["2022", "2023", "2024"] and ev["total_due_county_unit"] == 1800.0
    assert a1["owner"]["name"] == "SMITH JANE"                           # newest year's owner
    assert a1["occupancy"] == "owner_occupied"
    assert a1["street_address"] == "4930 BROOKVIEW DR" and a1["city"] == "Dallas" and a1["county"] == "Dallas"
    types = [s["type"] for s in a1["signals"]]
    assert types == ["TAX_DELINQUENT", "TAX_SUIT"]
    assert "at least $1,800.00" in a1["signals"][0]["value"] and "2022-2024" in a1["signals"][0]["value"]
    assert "TX-23-01234" in a1["signals"][1]["value"]
    assert "payment agreement" in by["DALTRW:A7"]["signals"][0]["value"]
    st = res["stats"]
    assert st["deferred_skipped"] == 1 and st["bankruptcy_skipped"] == 1 and st["below_min_due"] == 1
    assert st["delinquent_accounts"] == 2
    assert res["source_updated_at"] == datetime(2026, 10, 5)


def test_rotation_window_wraps(trw):
    trw([line(account="C%02d" % i, year="2024") for i in range(5)])
    a = T.TrwReader().discover(limit=2, skip=0, today=TODAY)["records"]
    b = T.TrwReader().discover(limit=2, skip=4, today=TODAY)
    assert [r["source_reference"] for r in a] == ["DALTRW:C00", "DALTRW:C01"]
    assert [r["source_reference"] for r in b["records"]] == ["DALTRW:C04", "DALTRW:C00"]
    assert b["stats"]["wrapped"] == 1


def test_format_change_is_reported(trw):
    trw(["too short"] * 60)
    from app.services.evosense.sources import base as B
    with pytest.raises(B.SourceError):
        T.TrwReader().discover(limit=5, today=TODAY)


def test_provider_registered_and_scoped_to_dallas(trw):
    trw(ROWS)
    p = PV.PROVIDERS["dallas_trw"]
    assert p.discovery and p.counties == ("Dallas",)
    skipped = p.search(PV.C.TAX, {"counties": ["Tarrant"], "limit": 5})
    assert len(skipped) == 0 and "skipped" in skipped.stats
    got = p.search(PV.C.TAX, {"counties": ["Dallas County"], "limit": 5})
    assert len(got) == 2
    v = p.verify()
    assert v["county_unit"] == "1002" and any("flat404" in n for n in v["members"])



def test_prior_year_balance_is_dated_by_the_file_not_the_oldest_year(trw):
    trw(ROWS)
    by = {r["source_reference"]: r for r in T.TrwReader().discover(limit=10, today=TODAY)["records"]}
    multi = by["DALTRW:A1"]["signals"][0]                                # 2022-2024 still owed
    assert multi["effective_at"] is None and "still owed" in multi["evidence_basis"]
    single = by["DALTRW:A7"]["signals"][0]                               # only 2024
    assert single["effective_at"] == "2025-02-01"


def test_reprocess_rebuilds_v2_from_raw_and_redates_v1(trw):
    import json
    from types import SimpleNamespace
    from app.services.evosense import ingest as IN
    from app.services.evosense import reprocess as RP
    trw(ROWS)
    rec = {r["source_reference"]: r for r in T.TrwReader().discover(limit=10, today=TODAY)["records"]}["DALTRW:A1"]
    raw = IN.raw_evidence(rec)
    obs = SimpleNamespace(provider_key="dallas_trw", raw_payload=raw["raw_payload"],
                          payload=json.dumps(IN._sanitize(rec), default=str), source_url=T.PAGE)
    again, how = RP.rebuild_record(obs, None)
    assert how == "raw" and again["_evidence"]["years"] == ["2022", "2023", "2024"]
    # a dallas_trw/1 observation: raw was one row, the signal dated by the oldest year
    v1 = dict(IN._sanitize(rec))
    v1["signals"] = [dict(v1["signals"][0], effective_at="2023-02-01")]
    old = SimpleNamespace(provider_key="dallas_trw", raw_payload=json.dumps({"account": "A1"}),
                          payload=json.dumps(v1, default=str), source_url=T.PAGE)
    fixed, how = RP.rebuild_record(old, None)
    assert fixed["signals"][0]["effective_at"] is None and how.startswith("stored payload")
