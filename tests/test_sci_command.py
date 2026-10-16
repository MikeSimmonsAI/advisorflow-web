"""SCI Command phone screens: priority inbox, family view, real slots,
scheduling through the appointment engine, assign; test contacts excluded."""
from datetime import datetime
from unittest.mock import patch

from app.services.programs import appointments as A
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)

SLOTS = [datetime(2030, 1, 8, 10, 0), datetime(2030, 1, 8, 14, 0)]


def test_home_puts_families_first_and_hides_test_contacts(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    hot = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    low = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Preston", email="p@example.com")
    test = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Synthetic", email="s@example.com")
    test.is_test = True
    db.commit()
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, low, "I'm driving with Do Not Disturb on. I'll see your message when I get where I'm going.", "sms")
        R.on_inbound(db, hot, "Can I schedule a visit this week?", "sms")
        R.on_inbound(db, test, "Can I schedule a visit?", "sms")
    r = client.get("/program/command", headers=_h(db, program["admin"])).json()
    names = [i["name"] for i in r["inbox"]]
    assert names[0] == "Lester Testcase" and "Synthetic Testcase" not in names
    assert r["counts"]["needs_attention"] == 1 and r["counts"]["replies_today"] == 1
    assert r["owner_alert"] is None                       # owner-only: a manager never sees it
    s = client.get("/program/command?q=lester", headers=_h(db, program["admin"])).json()
    assert [i["name"] for i in s["inbox"]] == ["Lester Testcase"]


def test_family_view_slots_schedule_and_assign(program, sample_advisor, client, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, "Would really like to receive the guide, any questions I would contact you?", "sms")
    h = _h(db, program["admin"])
    f = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert f["name"] == "Lester Testcase" and f["thread"][-1]["who"] == "family"
    assert "Send planning guide" in f["suggestion"]["steps"]
    monkeypatch.setattr(A, "available_slots", lambda db, prog, **kw: (list(SLOTS), None))
    sl = client.get("/program/command/slots?lead_id=%s" % lead.id, headers=h).json()
    assert [s["value"] for s in sl["slots"]] == [s.isoformat() for s in SLOTS]
    with patch.object(A, "book", return_value={"ok": True, "status": "confirmed", "booking_id": "b1"}) as bk, \
         patch.object(A, "notify_team_booked") as note:
        r = client.post("/program/command/schedule", headers=h,
                        json={"lead_id": lead.id, "slot": SLOTS[0].isoformat(), "kind": "visit"}).json()
    assert r["status"] == "confirmed" and bk.call_args.args[3] == SLOTS[0] and note.call_count == 1
    a = client.post("/program/command/assign", headers=h, json={"lead_id": lead.id, "user_id": sample_advisor.id})
    assert a.status_code == 200 and a.json()["assigned_to_id"] == sample_advisor.id


def test_assign_lists_people_with_a_seat_in_the_workspace(program, sample_advisor, client):
    from app.models.models import User
    from app.services import workspace_access
    db, org = program["db"], program["org"]
    staff = User(email="kerry.seat@example.com", full_name="Kerry Seat", role="advisor", organization_id=None,
                 is_active=True, password_hash="x")
    db.add(staff)
    db.commit()
    workspace_access.grant_workspace_membership(db, staff.id, org.id, role="manager", granted_by=program["admin"].id)
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, "Can I schedule a visit?", "sms")
    h = _h(db, program["admin"])
    f = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert "Kerry Seat" in [t["name"] for t in f["team"]]
    a = client.post("/program/command/assign", headers=h, json={"lead_id": lead.id, "user_id": staff.id})
    assert a.status_code == 200 and a.json()["assigned_to_id"] == staff.id


def test_mail_task_is_a_persons_job_never_a_claim(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lawrence")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, "Is it possible to mail it to my home?", "sms")
    h = _h(db, program["admin"])
    r = client.post("/program/command/mail-task", headers=h, json={"lead_id": lead.id, "note": "address in his text"})
    assert r.status_code == 200 and r.json()["mail_task"]["done"] is False
    home = client.get("/program/command", headers=h).json()
    assert [t["name"] for t in home["tasks"]] == ["Lawrence Testcase"]
    f = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert f["mail_task"]["note"] == "address in his text" and not f["mail_task"]["done"]
    client.post("/program/command/mail-task", headers=h, json={"lead_id": lead.id, "done": True})
    assert client.get("/program/command", headers=h).json()["tasks"] == []


def test_search_finds_every_family_not_only_repliers(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    _lead_for(db, org, sample_advisor, "L001", first="Alvin", last="Major")
    t = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Alvina", last="Test", email="t@example.com")
    t.is_test = True
    db.commit()
    h = _h(db, program["admin"])
    names = [i["name"] for i in client.get("/program/command/search?q=alvin", headers=h).json()["items"]]
    assert names == ["Alvin Major"]                      # never replied, still found; test contact hidden
    assert client.get("/program/command/search?q=alvin%20maj", headers=h).json()["items"][0]["replied"] is False
    assert client.get("/program/command/search?q=5550101", headers=h).json()["items"][0]["name"] == "Alvin Major"
    assert client.get("/program/command/search?q=a", headers=h).json()["items"] == []


def test_a_voicemail_in_the_family_thread_has_a_play_button(program, sample_advisor, client):
    from app.models.telephony_models import Voicemail
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, "Hi, please call me back about planning.", "voicemail")
    db.add(Voicemail(organization_id=org.id, lead_id=lead.id, recording_sid="RE" + "a" * 32,
                     recording_url="https://api.twilio.com/x", duration_seconds=21,
                     transcript="Hi, please call me back about planning.", received_at=datetime.utcnow()))
    db.commit()
    fam = client.get("/program/command/family/%s" % lead.id, headers=_h(db, program["admin"])).json()
    vm = [t for t in fam["thread"] if t["channel"] == "voicemail"]
    assert len(vm) == 1 and vm[0]["audio_path"].startswith("/voicemails/") and vm[0]["duration"] == 21
    assert "api.twilio.com" not in str(fam)                      # the provider URL never reaches the browser


def test_unknown_senders_and_their_voicemail_show_in_sci_live(program, sample_advisor, client):
    from app.models.program_models import ProgramUnmatchedReply
    from app.models.telephony_models import Voicemail
    db, org = program["db"], program["org"]
    db.add(Voicemail(organization_id=org.id, recording_sid="RE" + "b" * 32, recording_url="https://api.twilio.com/y",
                     duration_seconds=30, transcript="Please call me", received_at=datetime.utcnow()))
    db.add(ProgramUnmatchedReply(organization_id=org.id, from_address="+12515550123", subject="Voicemail",
                                 body_excerpt="Please call me", received_at=datetime.utcnow(),
                                 mailbox_message_id="vm:RE" + "b" * 32, reason="no contact", status="open"))
    db.commit()
    r = client.get("/program/command", headers=_h(db, program["admin"])).json()
    u = r["unknown"][0]
    assert u["channel_label"] == "VOICEMAIL" and u["audio_path"].startswith("/voicemails/") and u["duration"] == 30
    assert "api.twilio.com" not in str(r)


def test_an_inbound_text_matches_a_number_stored_with_a_plus(program, sample_advisor):
    from app.routers.sms_router import process_inbound_sms
    from app.models.models import Lead
    db, org = program["db"], program["org"]
    lead = Lead(organization_id=org.id, first_name="Owner", last_name="Plus", phone="+18435550177", status="sent")
    db.add(lead)
    db.commit()
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        process_inbound_sms(db, org_id=org.id, advisor=None, From="+18435550177", Body="Tuesday at 1 works",
                            MessageSid="SMx1")
    from app.models.program_models import ProgramResponse, ProgramUnmatchedReply
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 1
    assert db.query(ProgramUnmatchedReply).count() == 0


def test_a_voicemail_on_the_sci_line_plays_with_the_platform_account(program, sample_advisor, client, monkeypatch):
    from app.models.telephony_models import Voicemail
    db, org = program["db"], program["org"]
    vm = Voicemail(organization_id=org.id, recording_sid="RE" + "c" * 32, to_e164="+18449172171",
                   from_e164="+12515550123", received_at=datetime.utcnow())
    db.add(vm)
    db.commit()
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "1" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    seen = {}

    def fake_fetch(creds, sid):
        seen["creds"] = creds
        return b"ID3audio", "audio/mpeg"
    monkeypatch.setattr("app.services.telephony_twilio.fetch_recording", fake_fetch)
    r = client.get("/voicemails/%s/audio" % vm.id, headers=_h(db, program["admin"]))
    assert r.status_code == 200 and r.content == b"ID3audio" and seen["creds"], seen


def test_sci_texts_carry_no_links_unless_switched_on(program, sample_advisor, monkeypatch):
    from app.services import sms_programs
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db_, oid: True)
    monkeypatch.setattr(sms_programs, "sci_sender_entry", lambda: {
        "kind": "toll_free", "verification_status": "TWILIO_APPROVED", "programs": ["sci"],
        "has_embedded_links": True, "has_embedded_phone": True, "approved_scope": ["informational"]})
    monkeypatch.setattr(sms_programs, "message_category", lambda db_, l: "informational")
    monkeypatch.delenv("SCI_SMS_LINKS", raising=False)
    assert sms_programs.content_allowance(db, lead)["allow_links"] is False
    monkeypatch.setenv("SCI_SMS_LINKS", "on")
    assert sms_programs.content_allowance(db, lead)["allow_links"] is True


def test_pipeline_counts_real_families_only(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    a = _lead_for(db, org, sample_advisor, "L001")
    t = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Test", email="t@example.com")
    t.is_test = True
    db.commit()
    r = client.get("/program/command/pipeline", headers=_h(db, program["admin"]))
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["loaded"] == 1 and p["not_yet_contacted"] == 1 and "daily_batch" in p
    assert set(p["first_touches_today"]) == {"sent", "delivered", "failed"}


def test_inbound_window_audit_is_owner_only(program, sample_advisor, client):
    db = program["db"]
    r = client.get("/program/audit/inbound-window?start=2026-10-16T01:35:00&end=2026-10-16T01:55:00",
                   headers=_h(db, program["admin"]))
    assert r.status_code == 403


def test_inbox_filters_for_ai_human_and_unread(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    hot = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, hot, "Can I schedule a visit this week?", "sms")
    h = _h(db, program["admin"])
    r = client.get("/program/command?filter=unread", headers=h).json()
    assert r["filter"] == "unread" and [i["name"] for i in r["inbox"]] == ["Lester Testcase"]
    assert {"ai", "human", "appointments", "unread"} <= set(r["filter_counts"])
    total = r["filter_counts"]["ai"] + r["filter_counts"]["human"]
    assert total == r["filter_counts"]["all"]


def test_restart_texts_reopens_only_a_no_consent_stop(program, sample_advisor, client):
    """Oct 16: a job without the SCI send switches stopped a family's texts
    as "no consent". A manager can restart exactly that state, and the
    family page says it in plain words."""
    from app.models.models import CadenceState
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Arthur")
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    st.status, st.current_touch_number = "stopped_no_sms_consent", 0
    st.cadence_started_at, st.completed_at = datetime(2026, 10, 16, 2, 57), datetime(2026, 10, 16, 14, 3)
    db.commit()
    h = _h(db, program["admin"])
    j = client.get(f"/program/command/analytics/journey/{lead.id}", headers=h).json()
    assert j["texts_restartable"] is True and "stopped_no_sms_consent" not in j["stage"]
    assert j["stage"] == "Texts stopped - no SMS consent on record"
    r = client.post("/program/command/restart-texts", json={"lead_id": lead.id}, headers=h)
    assert r.status_code == 200 and r.json()["next_touch"] == 1
    db.refresh(st)
    assert st.status == "active" and st.completed_at is None and st.next_touch_due_at is not None
    # a second try (now active) and any other stopped state are refused
    assert client.post("/program/command/restart-texts", json={"lead_id": lead.id}, headers=h).status_code == 409
    st.status = "opted_out"; db.commit()
    assert client.post("/program/command/restart-texts", json={"lead_id": lead.id}, headers=h).status_code == 409


def test_an_on_hold_family_waits_instead_of_failing(program, sample_advisor):
    """Oct 16: an on-hold family was logged "failed" every hour, spending the
    retry budget. A hold is a wait: skipped, no budget spent; release makes
    the touch due again."""
    from app.models.models import CadenceState, CadenceTouchLog
    from app.models.program_models import ProgramSourceRecord
    from app.services import cadence_service as C
    from app.services.programs import holds
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Alvin")
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, lead_id=lead.id).one()
    holds.set_hold(db, rec, True, None, reason="location identity review")
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    st.next_touch_due_at = datetime.utcnow()
    db.commit()
    with patch("app.services.cadence_service.org_sending_enabled", return_value=True), \
         patch("app.services.entitlements.org_has_feature", return_value=True), \
         patch("app.services.sms_service.send_sms",
               side_effect=Exception("Lead x: ON HOLD: this contact was put on hold by a person.")):
        C.run_due_cadences(db, organization_id=org.id)
    logs = db.query(CadenceTouchLog).filter_by(lead_id=lead.id).all()
    assert logs and all(l.outcome == C.OUTCOME_SKIPPED for l in logs)
    db.refresh(st)
    assert st.status == "active" and st.next_touch_due_at > datetime.utcnow()
    holds.set_hold(db, rec, False, None)
    db.commit(); db.refresh(st)
    assert st.next_touch_due_at <= datetime.utcnow()
