"""SCI as its own suite: lead files, appointments, program-only logins."""
from datetime import datetime, timedelta

from app.models.models import BookingLink
from tests.test_outreach_program import _h as _hdr, _lead_for, program  # noqa: F401


def test_lead_files_round_trip(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", first="Rosa")
    h = _hdr(db, admin)
    r = client.post(f"/program/leads/{lead.id}/files", headers=h,
                    files={"file": ("form.pdf", b"%PDF-1.4 test", "application/pdf")}, data={"note": "signed form"})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    items = client.get(f"/program/leads/{lead.id}/files", headers=h).json()["items"]
    assert [i["filename"] for i in items] == ["form.pdf"] and items[0]["note"] == "signed form"
    got = client.get(f"/program/leads/{lead.id}/files/{fid}", headers=h)
    assert got.status_code == 200 and got.content == b"%PDF-1.4 test"
    bad = client.post(f"/program/leads/{lead.id}/files", headers=h,
                      files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 415


def test_appointments_upcoming_and_past(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", first="Rosa")
    db.add(BookingLink(lead_id=lead.id, user_id=admin.id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=1)))
    db.add(BookingLink(lead_id=lead.id, user_id=admin.id, status="confirmed",
                       booked_time=datetime.utcnow() - timedelta(days=3)))
    db.add(BookingLink(lead_id=lead.id, user_id=admin.id, status="pending"))
    db.commit()
    h = _hdr(db, admin)
    up = client.get("/program/appointments", headers=h).json()["items"]
    past = client.get("/program/appointments?when=past", headers=h).json()["items"]
    assert len(up) == 1 and up[0]["status"] == "booked" and up[0]["name"].startswith("Rosa")
    assert len(past) == 1 and past[0]["status"] == "confirmed"


def test_program_workspace_flagged_in_contexts(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    from app.services.workspace_access import authorized_contexts
    db.add(Membership(user_id=admin.id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id, role="org_admin",
                      is_active=True))
    db.commit()
    ctx = authorized_contexts(db, admin)
    ws = [c for c in ctx["workspace_contexts"] if c["organization_id"] == org.id]
    assert ws and ws[0]["is_program"] is True
    assert ctx["program_only"] is (not ctx["platform_contexts"] and not ctx["executive_contexts"]
                                   and all(w["is_program"] for w in ctx["workspace_contexts"]))
