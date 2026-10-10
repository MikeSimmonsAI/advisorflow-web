"""Lead workflow redesign (Oct 2026): the data the new screens read, and the
guards the Lead Command Center must keep for internal test records."""
import io
import re
from datetime import datetime, timedelta

from app.models.models import BookingLink, Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password


def _world(db):
    org = Organization(name="Redesign Org", slug="redesign-org", plan="standard")
    db.add(org)
    db.commit()
    admin = User(organization_id=org.id, email="a@redesign.test", password_hash=hash_password("TestPass123!"),
                 full_name="Admin", role="org_admin", must_change_password=False)
    db.add(admin)
    db.commit()
    real = Lead(organization_id=org.id, first_name="Real", last_name="Family", phone="+12145550111", email="r@x.test")
    test = Lead(organization_id=org.id, first_name="Staff", last_name="Tester", phone="+12145550112", is_test=True)
    db.add_all([real, test])
    db.commit()
    return admin, real, test


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def test_leads_list_marks_test_records(client, db_session):
    admin, real, test = _world(db_session)
    items = client.get("/leads/", headers=_h(db_session, admin)).json()["items"]
    flags = {i["first_name"]: i["is_test"] for i in items}
    assert flags == {"Real": False, "Staff": True}


def test_appointments_pending_links_only_when_asked_and_never_test_records(client, db_session):
    admin, real, test = _world(db_session)
    now = datetime.utcnow()
    db_session.add_all([
        BookingLink(lead_id=real.id, user_id=admin.id, status="pending", created_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=6)),
        BookingLink(lead_id=test.id, user_id=admin.id, status="pending", created_at=now - timedelta(days=1)),
    ])
    db_session.commit()
    h = _h(db_session, admin)
    assert "pending" not in client.get("/pipeline/appointments", headers=h).json()
    pend = client.get("/pipeline/appointments", headers=h, params={"include_pending": True}).json()["pending"]
    assert [p["lead_name"] for p in pend] == ["Real Family"]
    assert pend[0]["expired"] is False and pend[0]["link_sent_at"]


def _src(path):
    return io.open(path, encoding="utf-8").read()


def test_command_center_blocks_every_outbound_path_for_test_records():
    src = _src("frontend/src/pages/LeadDetail.jsx")
    for chan in ("sms", "email", "both", "voice"):
        assert re.search(r"!isTest && \(ch \? ch\.%s\.available" % chan, src), chan
    # dialer, AI start/resume, booking link and the composer each refuse
    assert "blockedReason={isTest ? TEST_REASON : null}" in src
    assert "const aiStartBlockedReason = isTest ? TEST_REASON" in src
    assert "const canBookLink = !isTest" in src
    assert 'data-testid="lcc-composer-blocked"' in src
    assert "outreachBlocked={isTest ? TEST_REASON : null}" in src
    # Human Active stops the AI conversation from being started or resumed here
    assert "humanActive ? 'A person is handling this conversation." in src


def test_bulk_selection_on_the_directory_never_includes_test_records():
    src = _src("frontend/src/pages/Leads.jsx")
    assert "&& !l.is_test && l.manual_flag !== 'remove_all')" in src


def test_booking_link_resend_refuses_a_test_record_in_production(client, db_session, monkeypatch):
    admin, real, test = _world(db_session)
    test.email = "staff@x.test"
    db_session.commit()
    monkeypatch.delenv("APP_ENV", raising=False)
    r = client.post("/leads/%s/resend-booking-link" % test.id, headers=_h(db_session, admin))
    assert r.status_code == 409 and "test record" in r.json()["detail"].lower()
    assert db_session.query(BookingLink).filter(BookingLink.lead_id == test.id).count() == 0


def test_call_history_shows_an_inbound_call_and_its_voicemail_once(db_session):
    from app.models.models import VoiceCall
    from app.models.telephony_models import Voicemail
    from app.services import telephony_service as TS
    admin, real, test = _world(db_session)
    db_session.add_all([
        VoiceCall(lead_id=real.id, advisor_id=admin.id, organization_id=real.organization_id, to_phone="+18449172171",
                  direction="inbound", call_sid="CA-1", outcome="voicemail_received"),
        Voicemail(organization_id=real.organization_id, lead_id=real.id, call_sid="CA-1", status="new"),
    ])
    db_session.commit()
    h = TS.lead_call_history(db_session, real)
    assert h["calls"][0]["voicemail_id"] == h["voicemails"][0]["id"]
    assert h["voicemails"][0]["call_id"] == h["calls"][0]["id"]
