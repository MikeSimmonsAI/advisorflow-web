"""Test records never show up as real numbers anywhere: the booking-page test
contact and its booking stay in the database (so the test link keeps working)
but every SCI screen and every shared appointment count leaves them out."""
from datetime import datetime, timedelta

from app.models.models import BookingLink, Lead, User
from app.models.program_models import OutreachProgram
from app.services.auth_service import hash_password
from tests.test_switch_import_booking import _h
from tests.test_program_load_source import _program


def _with_test_booking(db, org):
    admin = _program(db, org)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    prog.booking_user_id = admin.id
    god = User(organization_id=org.id, email="owner-td@example.com", password_hash=hash_password("x"),
               full_name="Mike Simmons", role="god_admin", is_active=True, must_change_password=False)
    db.add(god)
    db.commit()
    return admin, god


def test_test_contact_is_left_out_of_every_count(client, db_session, sample_org):
    admin, god = _with_test_booking(db_session, sample_org)
    h = _h(db_session, admin)
    before = client.get("/program/dashboard", headers=h).json()
    held_before = client.get("/program/records?queue=on_hold", headers=h).json()

    gh = dict(_h(db_session, god), **{"X-Org-Override": sample_org.id})
    r = client.post("/program/booking-test-link", headers=gh, json={})
    assert r.status_code in (200, 422), r.text
    from app.models.program_models import ProgramSourceRecord
    assert db_session.query(ProgramSourceRecord).filter(
        ProgramSourceRecord.source_lead_id.like("TEST-BOOKING-%")).count() == 1
    test_lead = db_session.query(Lead).filter_by(organization_id=sample_org.id, is_test=True).one()
    test_lead.status = "booked"
    db_session.add(BookingLink(lead_id=test_lead.id, user_id=admin.id, status="booked",
                               booked_time=datetime.utcnow() + timedelta(days=1)))
    db_session.commit()

    after = client.get("/program/dashboard", headers=h).json()
    assert after["metrics"]["leads"] == before["metrics"]["leads"]
    assert after["attention"]["on_hold"] == before["attention"]["on_hold"]
    assert after["reporting"]["appointments"] == 0
    assert client.get("/program/records?queue=on_hold", headers=h).json() == held_before
    assert client.get("/program/appointments", headers=h).json()["items"] == []
    # Leads page cards
    assert client.get("/leads/?page=1&page_size=1&exclude_test=true&status=booked", headers=h).json()["total"] == 0
    # Upcoming appointments (shared calendar widgets)
    up = client.get("/availability/upcoming?org_wide=true", headers=h).json()
    items = up["items"] if isinstance(up, dict) else up
    assert all(i.get("lead_id") != test_lead.id for i in items)
    mine = client.get("/availability/upcoming", headers=h).json()
    items = mine["items"] if isinstance(mine, dict) else mine
    assert all(i.get("lead_id") != test_lead.id for i in items)

    # The test link itself still works and reuses the same contact.
    assert client.post("/program/booking-test-link", headers=gh, json={}).status_code in (200, 422)
    assert db_session.query(Lead).filter_by(organization_id=sample_org.id, is_test=True).count() == 1
