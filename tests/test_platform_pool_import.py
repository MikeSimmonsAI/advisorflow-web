# -*- coding: utf-8 -*-
"""#470 Stage B - Universal Intake destination platform_acquisition_pool.
Synthetic businesses on example.test domains only."""
import io
import json
import uuid

import pytest

from app.models.lead_intel_models import LeadIntelProspect
from app.models.models import EmailMessage, Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.intake import platform_pool as PP

CSV = """Company,Type,Website,Phone,Email,Email Verified,Address,City,State,ZIP,Source URL,Researched,Record ID,Notes,Extra Column
Oak Dental Studio,Dental office,https://oakdental.example.test,(214) 555-0101,office@oakdental.example.test,yes,1 Main St,Dallas,TX,75201,https://maps.example.test/oak,2026-11-02,R1,Two locations,keep me
Elm Property Group,Property management company,https://www.elmpg.example.test/about,214-555-0102,,,2 Elm St,Plano,TX,75074,https://maps.example.test/elm,2026-11-02,R2,,
Venice Offices,Office building,https://venice.example.test,310-555-0103,,,9 Ocean Ave,Los Angeles,CA,90001,https://maps.example.test/ven,2026-11-02,R3,,
Mystery Suites,Office,https://mystery.example.test,469-555-0104,,,,,,,,,R4,,
Oak Dental Again,Dental,https://oakdental.example.test/contact,,,,,Dallas,TX,75201,,,R5,,
,Office,https://noname.example.test,,,,,,TX,,,,R6,,
"""


def _god(db):
    if db.query(Organization).filter(Organization.id == PP.PLATFORM_ORG_ID).first() is None:
        db.add(Organization(id=PP.PLATFORM_ORG_ID, name="Platform", slug="platform-%s" % uuid.uuid4().hex[:6],
                            plan="enterprise", industry="funeral", is_active=True))
    u = User(organization_id=None, email="pp-%s@example.test" % uuid.uuid4().hex[:8], full_name="Owner",
             password_hash=hash_password("Pass12345!"), role="god_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _user(db, role="org_admin"):
    org = Organization(name="Tenant %s" % uuid.uuid4().hex[:4], slug="t-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                       industry="cleaning", is_active=True)
    db.add(org)
    db.commit()
    u = User(organization_id=org.id, email="pt-%s@example.test" % uuid.uuid4().hex[:8], full_name="T",
             password_hash=hash_password("Pass12345!"), role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _upload(client, db, god, csv=CSV, source="manual_research"):
    r = client.post("/intake/platform-pool/batches", headers=_h(db, god),
                    files={"file": ("dfw.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"source_system": source, "display_name": "DFW test"})
    assert r.status_code == 200, r.text
    return r.json()


def _run(client, db, god, csv=CSV, source="manual_research", reject_rows=()):
    b = _upload(client, db, god, csv, source)
    m = b["mapping"]
    assert m["business_name"] == "Company" and m["website"] == "Website" and m["state"] == "State"
    m["category"], m["source_record_id"], m["researched_at"], m["evidence"] = "Type", "Record ID", "Researched", "Notes"
    m["email_verified"] = "Email Verified"
    assert client.put("/intake/platform-pool/batches/%s/mapping" % b["id"], json=m, headers=_h(db, god)).status_code == 200
    a = client.post("/intake/platform-pool/batches/%s/analyze" % b["id"], headers=_h(db, god)).json()
    rows = client.get("/intake/platform-pool/batches/%s/rows" % b["id"], headers=_h(db, god)).json()["items"]
    if reject_rows:
        ids = [r["id"] for r in rows if r["row"] in reject_rows]
        client.post("/intake/platform-pool/batches/%s/review" % b["id"], headers=_h(db, god),
                    json={"row_ids": ids, "decision": "reject", "note": "not a fit"})
    c = client.post("/intake/platform-pool/batches/%s/commit" % b["id"], headers=_h(db, god)).json()
    return b, a, rows, c


def test_map_stage_review_commit_qualify(client, db_session):
    db = db_session
    god = _god(db)
    before_msgs, before_leads = db.query(EmailMessage).count(), db.query(Lead).count()
    b, a, rows, c = _run(client, db, god, reject_rows=(2,))
    counts = a["analysis"]["counts"]
    assert counts["rows"] == 6 and counts["invalid"] == 2           # CA row + no-name row
    assert counts["duplicate_in_file"] == 1 and counts["geo_review"] == 1
    by = {r["row"]: r for r in rows}
    assert "outside the supported territory" in json.dumps(by[3]["business"]["errors"])
    assert by[4]["business"]["geo_status"] == "review"               # no location: kept for review, never guessed
    assert by[5]["duplicate"] == "within_batch_duplicate"
    assert by[1]["raw"]["Extra Column"] == "keep me"                  # unmapped column kept
    assert c["committed"] == 2 and c["skipped_rejected"] == 1 and c["skipped_invalid"] == 2
    assert c["skipped_duplicate"] == 1 and c["assigned"] == 0 and c["sent"] == 0 and c["target_org_id"] is None
    ps = db.query(LeadIntelProspect).filter(LeadIntelProspect.id.in_(c["prospect_ids"])).all()
    oak = next(p for p in ps if p.name == "Oak Dental Studio")
    assert oak.stage == "qualified" and oak.bucket and oak.routed_org_id is None and oak.destination_org_id is None
    assert oak.source == "manual_research" and oak.source_record_id == "R1" and oak.import_batch_id == b["id"]
    assert oak.source_url == "https://maps.example.test/oak" and oak.email_verified is True
    assert oak.industry == "medical_dental" and json.loads(oak.raw_json)["Extra Column"] == "keep me"
    assert json.loads(oak.evidence_json)["evidence"] == "Two locations" and oak.researched_at is not None
    assert db.query(EmailMessage).count() == before_msgs and db.query(Lead).count() == before_leads


def test_reimport_is_idempotent(client, db_session):
    db = db_session
    god = _god(db)
    _, _, _, first = _run(client, db, god)
    assert first["committed"] == 3
    b2, a2, rows2, second = _run(client, db, god)
    assert second["committed"] == 0 and a2["analysis"]["counts"]["duplicate_in_pool"] == 4
    # a different source that names the same website is still the same business
    b3, a3, _, third = _run(client, db, god, source="other_vendor")
    assert third["committed"] == 0
    # committing twice changes nothing
    again = client.post("/intake/platform-pool/batches/%s/commit" % b2["id"], headers=_h(db, god)).json()
    assert again["committed"] == 0
    assert db.query(LeadIntelProspect).count() == 3


def test_owner_only(client, db_session):
    db = db_session
    _god(db)
    for u in (_user(db, "org_admin"), _user(db, "super_admin")):
        r = client.post("/intake/platform-pool/batches", headers=_h(db, u),
                        files={"file": ("x.csv", io.BytesIO(CSV.encode()), "text/csv")},
                        data={"source_system": "x"})
        assert r.status_code == 403
        assert client.get("/intake/platform-pool/batches", headers=_h(db, u)).status_code == 403


def test_rollback_keeps_assigned(client, db_session):
    db = db_session
    god = _god(db)
    b, _, _, c = _run(client, db, god)
    p = db.query(LeadIntelProspect).filter(LeadIntelProspect.id == c["prospect_ids"][0]).first()
    p.routed_org_id = "some-client"
    db.commit()
    r = client.post("/intake/platform-pool/batches/%s/rollback" % b["id"], headers=_h(db, god)).json()
    assert r["removed"] == 2 and r["kept_assigned"] == 1
    assert db.query(LeadIntelProspect).filter(LeadIntelProspect.import_batch_id == b["id"]).count() == 1


def test_validation_and_mapping_guards(client, db_session):
    db = db_session
    god = _god(db)
    b = _upload(client, db, god)
    bad = dict(b["mapping"], business_name=None)
    assert client.put("/intake/platform-pool/batches/%s/mapping" % b["id"], json=bad, headers=_h(db, god)).status_code == 422
    bad = dict(b["mapping"], website="Not A Column")
    assert client.put("/intake/platform-pool/batches/%s/mapping" % b["id"], json=bad, headers=_h(db, god)).status_code == 422
    assert client.post("/intake/platform-pool/batches/%s/commit" % b["id"], headers=_h(db, god)).status_code == 409
    r = client.post("/intake/platform-pool/batches", headers=_h(db, god),
                    files={"file": ("x.csv", io.BytesIO(b"Company\n"), "text/csv")}, data={"source_system": "x"})
    assert r.status_code == 400


def test_segment_and_domain_helpers():
    assert PP.segment_for("Dental office") == "medical_dental"
    assert PP.segment_for("Apartment community") == "apartments"
    assert PP.segment_for("Bakery") is None
    assert PP.domain_of("https://www.Example.test/about?x=1") == "example.test"
    assert PP.domain_of("not a site") == ""


def test_excel_upload(client, db_session):
    from openpyxl import Workbook
    db = db_session
    god = _god(db)
    wb = Workbook()
    ws = wb.active
    ws.append(["Business Name", "Website", "City", "State", "Phone"])
    ws.append(["Cedar Apartments", "https://cedar.example.test", "Irving", "TX", "972-555-0109"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/intake/platform-pool/batches", headers=_h(db, god),
                    files={"file": ("list.xlsx", io.BytesIO(buf.getvalue()),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"source_system": "manual_research"})
    assert r.status_code == 200 and r.json()["rows"] == 1 and r.json()["mapping"]["business_name"] == "Business Name"
    bid = r.json()["id"]
    client.post("/intake/platform-pool/batches/%s/analyze" % bid, headers=_h(db, god))
    c = client.post("/intake/platform-pool/batches/%s/commit" % bid, headers=_h(db, god)).json()
    assert c["committed"] == 1
    p = db.query(LeadIntelProspect).filter(LeadIntelProspect.id == c["prospect_ids"][0]).one()
    assert p.industry == "apartments" and p.state == "TX"
