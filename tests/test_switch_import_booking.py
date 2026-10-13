"""Per-workspace text switch, Import leads inside the workspace (CSV and
Excel), booking page activity, and 60-minute program visit slots."""
import io
from datetime import date, datetime, timedelta

import pandas as pd

from app.models.models import BookingLink, Lead, User
from app.models.program_models import BookingPageEvent
from app.services.auth_service import create_access_token, hash_password
from tests.test_program_load_source import CSV, _program


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def test_kerry_can_check_then_load_leads(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    h = _h(db_session, admin)
    r = client.post("/program/leads/import", headers=h,
                    files={"file": ("list.csv", CSV.encode(), "text/csv")}, data={"apply": "false"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"]["source_rows"] == 5
    assert db_session.query(Lead).filter_by(organization_id=sample_org.id).filter(Lead.first_name == "Ann").count() == 0
    r = client.post("/program/leads/import", headers=h,
                    files={"file": ("list.csv", CSV.encode(), "text/csv")}, data={"apply": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["promotion"]["created"] == 2
    assert db_session.query(Lead).filter_by(organization_id=sample_org.id).filter(Lead.first_name == "Ann").count() == 1


def test_excel_file_is_read(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    frame = pd.read_csv(io.StringIO(CSV), dtype=str)
    buf = io.BytesIO()
    frame.to_excel(buf, index=False)
    r = client.post("/program/leads/import", headers=_h(db_session, admin),
                    files={"file": ("list.xlsx", buf.getvalue(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"apply": "false"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"]["source_rows"] == 5


def test_text_switch_is_per_workspace_and_owner_only(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    h = _h(db_session, admin)
    assert client.get("/program/text-switch", headers=h).json() == {"on": False, "can_change": False}
    assert client.post("/program/text-switch", headers=h, json={"on": True}).status_code == 403
    god = User(organization_id=sample_org.id, email="owner-sw@example.com", password_hash=hash_password("x"),
               full_name="Owner", role="god_admin", is_active=True, must_change_password=False)
    db_session.add(god)
    db_session.commit()
    gh = dict(_h(db_session, god), **{"X-Org-Override": sample_org.id})
    r = client.post("/program/text-switch", headers=gh, json={"on": True})
    assert r.status_code == 200 and r.json()["on"] is True
    from app.services import cadence_service as cs
    assert cs.workspaces_sending(db_session) == [sample_org.id]


def _next_weekday():
    d = date.today() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def test_booking_page_records_activity_and_offers_hour_visits(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    client.post("/program/leads/import", headers=_h(db_session, admin),
                files={"file": ("list.csv", CSV.encode(), "text/csv")}, data={"apply": "true"})
    lead = db_session.query(Lead).filter_by(organization_id=sample_org.id, first_name="Ann").one()
    link = BookingLink(lead_id=lead.id, user_id=admin.id, token="tok-activity-1", status="pending")
    db_session.add(link)
    db_session.commit()

    ctx = client.get("/calendar/booking/tok-activity-1").json()
    assert ctx["appt_duration"] == 60 and ctx["program_location"]["name"] == "Eastern Gate Memorial Gardens"
    day = _next_weekday().isoformat()
    slots = client.get("/calendar/slots", params={"advisor_id": admin.id, "date": day,
                                                   "token": "tok-activity-1"}).json()["slots"]
    assert slots and all(s.endswith(":00:00") and s[14:16] == "00" for s in slots)
    assert "%sT16:00:00" % day in slots and not any(s[11:13] == "16" and s[14:16] == "30" for s in slots)

    ev = [e.event for e in db_session.query(BookingPageEvent).filter_by(lead_id=lead.id)]
    assert ev.count("opened") == 1 and ev.count("day_viewed") == 1
    summary = client.get("/program/booking-activity", headers=_h(db_session, admin)).json()
    assert summary["opened_people"] == 1 and summary["booked_people"] == 0
    assert summary["recent"][0]["name"].startswith("Ann")


def test_owner_gets_a_test_booking_link(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    from app.models.program_models import OutreachProgram
    prog = db_session.query(OutreachProgram).filter_by(organization_id=sample_org.id).one()
    prog.booking_user_id = admin.id
    god = User(organization_id=sample_org.id, email="owner-tl@example.com", password_hash=hash_password("x"),
               full_name="Mike Simmons", role="god_admin", is_active=True, must_change_password=False)
    db_session.add(god)
    db_session.commit()
    assert client.post("/program/booking-test-link", headers=_h(db_session, admin), json={}).status_code == 403
    gh = dict(_h(db_session, god), **{"X-Org-Override": sample_org.id})
    r = client.post("/program/booking-test-link", headers=gh, json={})
    assert r.status_code in (200, 422), r.text
    test_leads = db_session.query(Lead).filter_by(organization_id=sample_org.id, is_test=True).all()
    assert len(test_leads) == 1 and test_leads[0].phone is None
    client.post("/program/booking-test-link", headers=gh, json={})
    assert db_session.query(Lead).filter_by(organization_id=sample_org.id, is_test=True).count() == 1
