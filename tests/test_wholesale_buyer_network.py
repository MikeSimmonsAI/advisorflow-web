"""Buyer network import, verified with CLEARLY SYNTHETIC buyer files (example.test
addresses, 555 numbers). The real buyer file has not been imported by these tests."""
import io

from app.models.wholesale_models import WholesaleBuyer, WholesaleBuyBox

ES = "/wholesale/evosense"
IMP = "/wholesale/buyers/imports"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:400])
    return r.json()


CSV = (
    "Company,Contact Name,Phone,E-mail,Counties,States,Cities,Zip Codes,Property Types,Max Price,Strategy,"
    "Financing,Close Days,POF,Prior Purchases,Last Contact,Status,Favorite Color\n"
    "SYNTH Dallas Flip LLC,Ann Test,(214) 555-0101,ann@synth-dfw.example.test,Dallas County; Tarrant,TX,,,"
    "SFR,$250k,fix and flip,cash,10,on file,1418 Elm St 03/2026,03/01/2026,active,blue\n"
    "SYNTH Tampa Holdings,Bob Test,813-555-0102,bob@synth-fl.example.test,,FL,Tampa,,SFR;duplex,400000,"
    "buy and hold,hard money,21,,,,active,\n"
    "SYNTH Texas Wide Co,Cal Test,512-555-0103,cal@synth-tx.example.test,,Texas,,,,300000,flip,cash,,,,,,\n"
    "SYNTH Zip Buyer,Dee Test,,dee@synth-zip.example.test,,,,\"76104, 76105\",land,90000,land,,,,,,,\n"
    "SYNTH Dup In File,Ann Again,214-555-0101,,,,,,,,,,,,,,,\n"
    "SYNTH Bad Price,Eve Test,972-555-0104,eve@synth.example.test,Collin,TX,,,,500000,,,,,,,,\n"
    ",,,,,,,,,,,,,,,,,\n"
    "SYNTH Range Wrong,Fay Test,972-555-0105,fay@synth.example.test,Denton,TX,,,,100000,,,,,,,,\n"
)


def _upload(client, H, content=CSV.encode(), name="synthetic_buyers.csv", is_test=True):
    return ok(client.post(IMP, headers=H, files={"file": (name, io.BytesIO(content), "text/csv")},
                          data={"list_name": "SYNTH network test", "is_test": str(is_test).lower()}))


def test_import_preview_mapping_dedupe_commit_retry_rollback(client, auth_headers, db_session):
    H = auth_headers
    # an existing buyer the file will match by email, and one it will be unsure about
    existing = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "SYNTH Tampa Holdings", "email": "BOB@synth-fl.example.test", "is_test": True}))
    other = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "Someone Else", "phone": "5125550103", "is_test": True}))
    ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "SYNTH Texas Wide Co", "email": "cal@synth-tx.example.test", "is_test": True}))

    b = _upload(client, H)
    assert b["row_count"] == 7 and b["file_stats"]["blank_rows"] == 1
    m = b["mapping"]
    assert m["E-mail"] == "email" and m["Zip Codes"] == "zips" and m["Favorite Color"] is None
    assert len(b["sample"]) == 7

    # make Fay's row an error by mapping min price too (min > max)
    m2 = dict(m)
    ok(client.put("%s/%s/mapping" % (IMP, b["id"]), headers=H, json={"mapping": m2}))
    a = ok(client.post("%s/%s/analyze" % (IMP, b["id"]), headers=H))
    c = a["counts"]
    assert c.get("update") == 1          # Bob: same email as an existing buyer (any case)
    assert c.get("conflict") == 1        # Cal: email says one buyer, phone says another
    assert c.get("duplicate_in_file") == 1
    rows = {r["normalized"].get("company_name"): r for r in
            ok(client.get("%s/%s/rows" % (IMP, b["id"]), headers=H))["rows"]}
    assert rows["SYNTH Tampa Holdings"]["match"]["candidates"][0]["buyer_id"] == existing["id"]
    conflict = rows["SYNTH Texas Wide Co"]
    assert conflict["state"] == "conflict" and "different buyers" in conflict["match"]["why"]
    assert {x["buyer_id"] for x in conflict["match"]["candidates"]} >= {other["id"]}
    assert rows["SYNTH Dup In File"]["state"] == "duplicate_in_file"
    assert "Favorite Color: blue" in rows["SYNTH Dallas Flip LLC"]["normalized"]["notes"]
    assert rows["SYNTH Dallas Flip LLC"]["normalized"]["counties"] == ["Dallas", "Tarrant"]

    # break one row on purpose, commit: conflicts wait, errors stay out
    ok(client.patch("%s/%s/rows/%s" % (IMP, b["id"], rows["SYNTH Range Wrong"]["id"]), headers=H,
                    json={"fix": {"Contact Name": "", "Company": ""}}))
    res = ok(client.post("%s/%s/commit" % (IMP, b["id"]), headers=H))["result"]
    assert res["created"] == 3 and res["updated"] == 1 and res["waiting"] == 1 and res["errors"] == 1
    assert res["status"] == "partially_committed"

    # nothing about the file made anyone "verified"
    ann = db_session.query(WholesaleBuyer).filter(WholesaleBuyer.email == "ann@synth-dfw.example.test").first()
    assert ann.file_pof_status == "on file" and ann.proof_of_funds_on_file is False and ann.cash_verified is False
    assert ann.financing_preference == "cash" and ann.typical_close_days == 10
    box = db_session.query(WholesaleBuyBox).filter(WholesaleBuyBox.buyer_id == ann.id).first()
    assert box is not None and "dallas" in box.counties
    # the existing buyer kept what it had and gained only blanks
    bob = db_session.query(WholesaleBuyer).filter(WholesaleBuyer.id == existing["id"]).first()
    db_session.refresh(bob)
    assert bob.email == "BOB@synth-fl.example.test" and bob.phone == "8135550102"

    # retry: fix the error row, decide the conflict, commit again - applied rows are not applied twice
    ok(client.patch("%s/%s/rows/%s" % (IMP, b["id"], rows["SYNTH Range Wrong"]["id"]), headers=H,
                    json={"fix": {"Contact Name": "Fay Test", "Company": "SYNTH Range Wrong"}}))
    ok(client.patch("%s/%s/rows/%s" % (IMP, b["id"], conflict["id"]), headers=H, json={"decision": "create"}))
    res2 = ok(client.post("%s/%s/commit" % (IMP, b["id"]), headers=H))["result"]
    assert res2["created"] == 2 and res2["updated"] == 0 and res2["status"] == "committed"
    assert db_session.query(WholesaleBuyer).filter(WholesaleBuyer.email == "ann@synth-dfw.example.test").count() == 1

    hist = ok(client.get(IMP, headers=H))["imports"]
    assert hist[0]["id"] == b["id"] and hist[0]["status"] == "committed"
    errs = client.get("%s/%s/errors.csv" % (IMP, b["id"]), headers=H)
    assert errs.status_code == 200 and "Same phone as row 2 of this file" in errs.text

    # the network and its DFW segment
    net = ok(client.get("/wholesale/buyers/network", headers=H, params={"segment": "dfw", "include_test": True}))
    names = {x["name"]: x for x in net["buyers"]}
    assert "SYNTH Dallas Flip LLC" in names and "SYNTH Tampa Holdings" not in names
    assert names["SYNTH Dallas Flip LLC"]["dfw"]["priority"] is True
    assert names["SYNTH Zip Buyer"]["dfw"]["territory"] == ["2 DFW ZIP codes"]
    assert names["SYNTH Texas Wide Co"]["dfw"]["statewide_only"] is True
    assert names["SYNTH Dallas Flip LLC"]["status"]["status"] == "contactable"   # a file never makes "verified"
    assert names["SYNTH Dallas Flip LLC"]["from_file"]["purchases"] == "1418 Elm St 03/2026"

    # rollback: created buyers go, the updated one gets its old values back
    rb = ok(client.post("%s/%s/rollback" % (IMP, b["id"]), headers=H))["rollback"]
    assert len(rb["removed"]) == 5 and len(rb["restored"]) == 1 and rb["boxes_removed"] >= 5
    assert db_session.query(WholesaleBuyer).filter(WholesaleBuyer.email == "ann@synth-dfw.example.test").count() == 0
    db_session.refresh(bob)
    assert bob.phone is None and bob.email == "BOB@synth-fl.example.test"
    assert client.post("%s/%s/commit" % (IMP, b["id"]), headers=H).status_code == 409


def test_excel_files_import_too(client, auth_headers):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Buyer Name", "Email", "County", "Max Price"])
    ws.append(["SYNTH Excel Buyer", "xl@synth.example.test", "Johnson", 175000])
    buf = io.BytesIO()
    wb.save(buf)
    H = auth_headers
    b = ok(client.post(IMP, headers=H, files={"file": ("synthetic.xlsx", io.BytesIO(buf.getvalue()),
                                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                       data={"is_test": "true"}))
    assert b["file_format"] == "xlsx" and b["mapping"]["County"] == "counties"
    ok(client.post("%s/%s/analyze" % (IMP, b["id"]), headers=H))
    assert ok(client.post("%s/%s/commit" % (IMP, b["id"]), headers=H))["result"]["created"] == 1
    net = ok(client.get("/wholesale/buyers/network", headers=H, params={"segment": "dfw", "include_test": True,
                                                                         "q": "excel"}))
    assert net["buyers"][0]["dfw"]["territory"] == ["Buys in Johnson County"]


def test_bad_files_are_refused_with_a_reason(client, auth_headers):
    H = auth_headers
    r = client.post(IMP, headers=H, files={"file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")})
    assert r.status_code == 400
    r = client.post(IMP, headers=H, files={"file": ("x.csv", io.BytesIO(b"name,email\n"), "text/csv")})
    assert r.status_code == 400 and "no buyers" in r.json()["detail"]
    b = _upload(client, H)
    assert client.post("%s/%s/commit" % (IMP, b["id"]), headers=H).status_code == 409   # analyze first
    r = client.put("%s/%s/mapping" % (IMP, b["id"]), headers=H, json={"mapping": {"Company": "shoe_size"}})
    assert r.status_code == 400


def test_another_company_cannot_see_or_touch_an_import(client, auth_headers, db_session):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    b = _upload(client, auth_headers)
    org = Organization(name="Other Net Co", slug="other-net", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="net.other@example.test", full_name="O", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    for method, path in (("get", ""), ("post", "/analyze"), ("post", "/commit"), ("post", "/rollback"),
                         ("get", "/rows"), ("get", "/errors.csv")):
        assert getattr(client, method)("%s/%s%s" % (IMP, b["id"], path), headers=h).status_code in (403, 404), path
    assert all(x["id"] != b["id"] for x in ok(client.get(IMP, headers=h))["imports"])
