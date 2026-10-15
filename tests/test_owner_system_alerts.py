"""Oct 14 (Mike): internal system alerts go to the OWNER ONLY. Family-response
alerts still go to the whole team."""
import json
from unittest.mock import patch

from app.services.programs import responses, self_check
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)

OWNER_PHONE, OWNER_EMAIL = "+18435550000", "owner@example.com"
TEAM = [{"name": "Kerry", "phone": "+14105550100", "role": "team"},
        {"name": "Mike S", "phone": "+15405550100", "email": "ms@example.com", "role": "team"}]


def _setup(db, prog):
    prog.alert_title = "EvoSys Pro SCI POC"
    prog.alert_phone, prog.alert_email = OWNER_PHONE, OWNER_EMAIL
    prog.management_recipients = json.dumps(TEAM)
    prog.staff_sms_alerts_enabled = True
    db.commit()


def _capture():
    calls = []

    def fake(db_, prog_, ch, to, msg, **kw):
        calls.append((ch, to, kw.get("kind")))
        return True, None
    return calls, fake


def test_self_check_alert_goes_to_owner_only(program):
    db, prog = program["db"], program["prog"]
    _setup(db, prog)
    self_check._alerted.clear()
    fake_run = {"checks": [{"key": "texts_spacing", "ok": False, "title": "Texts spaced as planned",
                            "detail": "2 people got two texts too close", "people": ["A", "B"]}]}
    calls, fake = _capture()
    with patch.object(self_check, "run", return_value=fake_run), \
         patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        assert self_check.alert_all(db) == 1
    assert sorted((c, t) for c, t, k in calls) == [("email", OWNER_EMAIL), ("sms", OWNER_PHONE)]


def test_mailbox_reconnect_alert_goes_to_owner_only(program):
    db, prog = program["db"], program["prog"]
    _setup(db, prog)
    calls, fake = _capture()
    with patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        responses.mailbox_reconnect_alert(db, "replies@example.com", "token expired")
    assert sorted((c, t) for c, t, k in calls) == [("email", OWNER_EMAIL), ("sms", OWNER_PHONE)]
    assert not [1 for c, t, k in calls if t in ("+14105550100", "+15405550100", "ms@example.com")]


def test_system_alert_to_a_team_member_is_blocked_at_send(program):
    db, prog = program["db"], program["prog"]
    _setup(db, prog)
    with patch("app.services.sms_service.Client") as C:
        ok, why = responses._deliver_staff_alert(db, prog, "sms", "+14105550100", "x", kind="self_check")
        ok2, _ = responses._deliver_staff_alert(db, prog, "email", "ms@example.com", "x", kind="mailbox_reconnect")
    assert ok is False and ok2 is False and "owner only" in why
    assert C.return_value.messages.create.call_count == 0


def test_family_reply_still_alerts_the_whole_team(program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    _setup(db, prog)
    lead = _lead_for(db, org, sample_advisor, "L001", email="fam@example.com")
    calls, fake = _capture()
    with patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        responses.on_inbound(db, lead, "What does the guide include?", "sms")
    got = sorted((c, t) for c, t, k in calls)
    assert got == sorted([("sms", OWNER_PHONE), ("email", OWNER_EMAIL), ("sms", "+14105550100"),
                          ("sms", "+15405550100"), ("email", "ms@example.com")])


def test_a_restart_does_not_repeat_todays_self_check_alert(program):
    db, prog = program["db"], program["prog"]
    _setup(db, prog)
    self_check._alerted.clear()
    fake_run = {"checks": [{"key": "texts_too_close", "ok": False, "title": "Texts spaced as planned",
                            "detail": "8 people", "people": []}]}
    calls, fake = _capture()
    with patch.object(self_check, "run", return_value=fake_run), \
         patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        assert self_check.alert_all(db) == 1
        self_check._alerted.clear()                      # a deploy restarts the process
        assert self_check.alert_all(db) == 0
    assert len(calls) == 2                               # one email + one text, once


def test_the_simulator_reaches_nobodys_bell(program, sample_advisor):
    from app.models.models import Notification
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True
    db.commit()
    before = db.query(Notification).count()
    responses.on_inbound(db, lead, "Can I schedule a visit?", "sms", staff_alerts=False, run_setter=False)
    assert db.query(Notification).count() == before
