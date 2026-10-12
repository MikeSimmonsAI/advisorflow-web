"""Bad phone numbers are caught when the file is checked, before loading."""
from app.services.programs import importer

HDR = "Lead ID,First Name,Last Name,Email,Phone,Lead Status,Primary Campaign,Location Friendly Name\n"


def test_bad_numbers_listed_in_summary():
    rows = importer.parse_csv((HDR +
        "A1,Odies,Donald,od@example.com,1662458988,Qualified,X,Somewhere\n"
        "A2,Rosa,Diaz,rd@example.com,2145550107,Qualified,X,Somewhere\n"
        "A3,Jen,Miller,,7513490943,Qualified,X,Somewhere\n"
        "A4,No,Phone,np@example.com,,Qualified,X,Somewhere\n").encode())
    s = importer.analyze(rows, {}, [])["summary"]
    assert s["bad_phone_rows"] == 2 and s["no_phone_rows"] == 1
    got = {b["lead_id"]: b for b in s["bad_phones"]}
    assert set(got) == {"A1", "A3"}
    assert got["A1"]["has_email"] is True and got["A3"]["has_email"] is False
