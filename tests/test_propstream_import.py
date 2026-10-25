"""PropStream CSV compatibility. The file below is a SYNTHETIC fixture in the
column layout of a PropStream export (made up for this test - not a real
PropStream file, and nothing here calls PropStream)."""
import io

from app.models.evosense_models import EvoSenseProperty
from app.services.evosense import propstream_csv as PS

SYNTHETIC = (
    "Address,Unit #,City,State,Zip,County,APN,Property Type,Owner 1 First Name,Owner 1 Last Name,"
    "Mailing Address,Mailing City,Mailing State,Mailing Zip,Bedrooms,Total Bathrooms,Building Sqft,"
    "Year Built,Est. Value,Total Loan Balance,Est. Equity,Last Sale Date,Vacant?,Pre-Foreclosure,"
    "Probate,Tax Delinquent,PropStream ID,Some New Column\n"
    "2620 Kirby St,,Dallas,TX,75203,Dallas,00000123,Single Family,PAT,SAMPLE,100 Elm St,Plano,TX,75024,"
    "3,2,1450,1952,\"$245,000\",\"$80,000\",\"$165,000\",2004-05-01,Yes,Notice of Default,No,,PS-1,x\n"
    "77 Oak Ave,,Fort Worth,TX,76104,Tarrant,,Single Family,JO,EXAMPLE,77 Oak Ave,Fort Worth,TX,76104,"
    "2,1,900,1940,150000,,150000,1999-01-01,No,,Yes,Y,PS-2,\n"
    ",,,TX,,Dallas,,,NO,ADDRESS,,,,,,,,,,,,,,,,,PS-3,\n")


def test_header_mapping_and_flags():
    import csv
    names = next(csv.reader(io.StringIO(SYNTHETIC)))
    assert PS.is_propstream(names)
    assert not PS.is_propstream(["street_address", "city", "zip_code"])        # already EvoSense's own
    text, rep = PS.convert(SYNTHETIC)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["street_address"] == "2620 Kirby St" and rows[0]["owner_name"] == "PAT SAMPLE"
    assert rows[0]["estimated_value"] == "245000" and rows[0]["mortgage_balance"] == "80000"
    assert rows[0]["signals"] == "VACANT;PRE_FORECLOSURE"                      # "No" asserts nothing
    assert rows[1]["signals"] == "PROBATE;TAX_DELINQUENT"
    assert rows[0]["record_id"] == "propstream:PS-1"
    assert "Some New Column" in rep["not_used"] and rep["signals_found"]["VACANT"] == 1


def test_preview_then_import_then_reimport(client, auth_headers, db_session):
    files = {"file": ("propstream_export.csv", SYNTHETIC.encode(), "text/csv")}
    pv = client.post("/wholesale/evosense/import/preview", headers=auth_headers, files=files).json()
    assert pv["ok"] and pv["format"] == "propstream" and pv["rows"] == 3
    assert pv["new"] == 2 and pv["would_reject"] == 1 and pv["already_imported"] == 0
    assert pv["signals"] == {"VACANT": 1, "PRE_FORECLOSURE": 1, "PROBATE": 1, "TAX_DELINQUENT": 1}
    assert db_session.query(EvoSenseProperty).count() == 0                    # a preview writes nothing

    r = client.post("/wholesale/evosense/import", headers=auth_headers,
                    files={"file": ("propstream_export.csv", SYNTHETIC.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["format"]["format"] == "propstream"
    assert out["counts"]["created"] == 2 and out["counts"]["rejected"] == 1
    p = db_session.query(EvoSenseProperty).filter(EvoSenseProperty.street_address == "2620 Kirby St").one()
    assert float(p.bedrooms) == 3 and p.square_feet == 1450 and p.year_built == 1952

    again = client.post("/wholesale/evosense/import", headers=auth_headers,
                        files={"file": ("propstream_export.csv", SYNTHETIC.encode(), "text/csv")}).json()
    assert again["counts"]["created"] == 0 and again["counts"]["seen"] == 2   # no duplicates
    pv2 = client.post("/wholesale/evosense/import/preview", headers=auth_headers,
                      files={"file": ("p.csv", SYNTHETIC.encode(), "text/csv")}).json()
    assert pv2["already_imported"] == 2 and pv2["new"] == 0


WITH_PHONES = (
    "Address,City,State,Zip,Owner 1 First Name,Owner 1 Last Name,Mailing Address,Mailing City,Mailing State,"
    "Mailing Zip,Phone 1,Mobile Phone 2,Landline 1,Email 1,PropStream ID\n"
    "5 Synth Ln,Dallas,TX,75216,PAT,SAMPLE,5 Synth Ln,Dallas,TX,75216,(214) 555-0101,214-555-0102,,"
    "pat@example.test,PS-10\n"
    "6 Synth Ln,Dallas,TX,75216,JO,EXAMPLE,6 Synth Ln,Dallas,TX,75216,2145550103,,2145550103,,PS-11\n")


def test_skip_traced_phone_and_email_columns_are_mapped():
    import csv
    text, rep = PS.convert(WITH_PHONES)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["phones"] == "2145550101|unknown;2145550102|mobile"
    assert rows[0]["emails"] == "pat@example.test"
    assert rows[1]["phones"] == "2145550103|unknown"                         # the same number once
    assert rep["contacts_found"] == {"rows": 2, "phones": 3, "emails": 1}


def test_list_contacts_attach_only_to_the_owner_the_list_names(db_session, sample_org):
    from app.models.evosense_models import EvoSenseContactPoint, EvoSenseOwner, EvoSenseOwnership
    from app.services.evosense import hunt as HU
    db, org = db_session, sample_org.id
    out = HU.import_csv(db, org, WITH_PHONES, user=None, filename="synthetic_propstream.csv")
    assert out["counts"]["created"] == 2 and out["counts"].get("contacts_attached", 0) == 4
    # now a county owner of record who is SOMEONE ELSE replaces the list's owner on 5 Synth Ln
    p = db.query(EvoSenseProperty).filter(EvoSenseProperty.street_address == "5 Synth Ln").one()
    for link in db.query(EvoSenseOwnership).filter(EvoSenseOwnership.property_id == p.id).all():
        link.is_current = False
    other = EvoSenseOwner(organization_id=org, display_name="LEE DIFFERENT", owner_type="individual")
    db.add(other)
    db.flush()
    db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=other.id, is_current=True,
                             source="dcad"))
    db.flush()
    counts = {}
    HU._attach_list_contacts(db, p, {"phones": "2145550199|mobile", "owner_name": "PAT SAMPLE"}, "x", counts)
    assert counts == {"contacts_refused": 1}
    assert db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == other.id).count() == 0


def test_an_import_can_be_undone_and_restored_without_deleting_anything(db_session, sample_org):
    from app.services.evosense import hunt as HU
    db, org = db_session, sample_org.id
    out = HU.import_csv(db, org, WITH_PHONES, user=None, filename="synthetic_propstream.csv")
    assert out["import_id"] and out["counts"]["created"] == 2
    p = db.query(EvoSenseProperty).filter(EvoSenseProperty.street_address == "5 Synth Ln").one()
    p.promoted_deal_id = "deal-x"                                    # worked since: kept
    db.flush()
    u = HU.undo_import(db, org, out["import_id"])
    assert u["archived"] == 1 and u["kept"][0]["why"] == "promoted into Wholesale"
    assert db.query(EvoSenseProperty).count() == 2                  # archived, not deleted
    r = HU.undo_import(db, org, out["import_id"], restore=True)
    assert r["restored"] == 1
    assert db.query(EvoSenseProperty).filter(EvoSenseProperty.archived_at.isnot(None)).count() == 0
    import pytest as _pt
    with _pt.raises(LookupError):
        HU.undo_import(db, "another-org", out["import_id"])
