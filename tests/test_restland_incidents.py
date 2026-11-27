"""RESTLAND SYSTEM-CHECK INCIDENTS (Nov 27). Synthetic data and test
recipients only - the production case is rebuilt with a made-up family.

Nov 27 12:12 AM CT: texts_never_started and first_messages_late flagged one
family whose first text was held by the Thanksgiving no-contact day and then
placed in the 2:26 PM follow-up slot. The owner got "The system check found a
problem. Please check your email." and a link to the Responses inbox."""
import json
from datetime import datetime, timedelta

import pytest

from app.models.models import CadenceState, CadenceTouchLog, Lead, Message, User
from app.models.program_models import ProgramAlert, ProgramIncident, ProgramSourceRecord
from app.models.reset_models import OutreachTextSlot
from app.services.auth_service import hash_password
from app.services.programs import incidents as INC
from app.services.programs import self_check as SC
from app.services.programs import setup
from tests.test_switch_import_booking import _h

OWNER_PHONE, OWNER_EMAIL = "+18435550000", "owner-test@example.com"
BASE = "https://app.example.test"
# Nov 27 2026, 06:12:16 UTC = 12:12 AM CT (the alert); the slot is 20:26 UTC = 2:26 PM CT.
NOW = datetime(2026, 11, 27, 6, 12, 16)
START = datetime(2026, 11, 26, 17, 42)
SLOT = datetime(2026, 11, 27, 20, 26)


@pytest.fixture()
def rl(db_session, sample_org, monkeypatch):
    db = db_session
    monkeypatch.setenv("APP_BASE_URL", BASE)
    admin = User(organization_id=sample_org.id, email="rl-admin@example.com", password_hash=hash_password("x" * 12),
                 full_name="Admin", role="org_admin", is_active=True, must_change_password=False)
    db.add(admin)
    db.commit()
    prog = setup.ensure_program(db, sample_org, name="Restland Cemetery and Funeral Home")
    setup.ensure_locations(db, sample_org, admin, ["Restland Cemetery and Funeral Home"])
    from app.models.program_models import LocationProfile
    for p in db.query(LocationProfile).filter_by(organization_id=sample_org.id).all():
        p.brand_settings = json.dumps({"program_theme": "restland"})
    prog.hero_title = "Restland"
    prog.alert_title = "EvoSys Pro Restland"
    prog.alert_phone, prog.alert_email = OWNER_PHONE, OWNER_EMAIL
    prog.management_recipients = json.dumps([{"name": "Team", "phone": "+14105550100", "email": "team@example.com",
                                               "role": "team"}])
    prog.staff_sms_alerts_enabled = True
    db.commit()
    SC._alerted.clear()
    return {"db": db, "org": sample_org, "prog": prog, "admin": admin}


def _family(db, org, admin, first="Testa", last="Family", phone="18085550123", slot=SLOT):
    """The Nov 27 record shape: held on the no-contact day, then queued."""
    lead = Lead(organization_id=org.id, assigned_to_id=admin.id, first_name=first, last_name=last,
                phone=phone, email="%s@example.com" % first.lower(), status="new")
    db.add(lead)
    db.flush()
    st = CadenceState(lead_id=lead.id, status="active", current_touch_number=0, cadence_started_at=START,
                      next_touch_due_at=slot)
    db.add(st)
    db.flush()
    db.add(ProgramSourceRecord(organization_id=org.id, source_lead_id="S-%s" % first, raw_json="{}", first_name=first,
                               last_name=last, lead_id=lead.id, approved_to_send_at=START - timedelta(days=10),
                               scheduled_start_at=START, send_mode="text_email"))
    db.add(CadenceTouchLog(cadence_state_id=st.id, lead_id=lead.id, organization_id=org.id, touch_number=1,
                           attempt_seq=1, channel="sms", outcome="blocked", scheduled_for=START,
                           reason="quiet hours: Nothing is sent today (a program's closed day such as Sunday, or a "
                                  "no-contact day such as Thanksgiving); it waits for the next open day.",
                           created_at=START + timedelta(seconds=16)))
    db.add(CadenceTouchLog(cadence_state_id=st.id, lead_id=lead.id, organization_id=org.id, touch_number=1,
                           attempt_seq=2, channel="sms", outcome="skipped", scheduled_for=NOW,
                           reason="slot: queued for the follow-up slot at 2:26 PM Nov 27",
                           created_at=NOW - timedelta(seconds=14)))
    db.add(OutreachTextSlot(organization_id=org.id, slot_at=slot, kind="followup", cadence_state_id=st.id,
                            lead_id=lead.id, touch_number=1, status="reserved"))
    db.commit()
    return lead


def _capture(fail_email=False):
    calls = []

    def fake(db_, prog_, ch, to, msg, **kw):
        content = kw.get("content") or {}
        calls.append({"ch": ch, "to": to, "kind": kw.get("kind"), "sms": content.get("sms"),
                      "subject": content.get("subject"), "html": content.get("html")})
        if ch == "email" and fail_email:
            return False, "Graph send failed: 503"
        if ch == "sms" and kw.get("receipt") is not None:
            kw["receipt"].update(sid="SMtest%d" % len(calls), status="queued")
        return True, None
    return calls, fake


@pytest.fixture()
def permitted(monkeypatch):
    # first_messages_late asks the family's contact hours; the 808 number is
    # Hawaii (8:12 PM there). Pin the answer so the test does not depend on
    # the zone tables of the machine running it.
    from app.services import contact_hours
    monkeypatch.setattr(contact_hours, "is_permitted", lambda lead, now=None: True)
    # The checks about outreach timing - the job and mailbox checks would
    # report this test database's missing background jobs.
    monkeypatch.setattr(SC, "CHECKS", [SC.texts_overdue, SC.texts_never_started, SC.first_messages_late])


def _alert(db, when, fake):
    from unittest.mock import patch
    with patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        return SC.alert_all(db, when)


def _save_sample(event, sms, subj, html):
    """INCIDENT_SAMPLE_DIR=... writes the exact words for the PR (docs/)."""
    import os
    d = os.environ.get("INCIDENT_SAMPLE_DIR")
    if not d:
        return
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "%s-sms.txt" % event), "w") as f:
        f.write(sms + "\n")
    with open(os.path.join(d, "%s-email.html" % event), "w") as f:
        f.write("<!doctype html><meta charset=utf-8><title>%s</title><p><b>Subject:</b> %s</p><hr>%s" % (
            subj, subj, html))


# ── the Nov 27 case ──────────────────────────────────────────────────────────

def test_nov27_two_checks_one_incident_one_text_one_email(rl, permitted):
    db, org = rl["db"], rl["org"]
    lead = _family(db, org, rl["admin"])
    checks = {c["key"]: c for c in SC.run(db, org.id, NOW)["checks"]}
    assert checks["texts_never_started"]["ok"] is False and checks["first_messages_late"]["ok"] is False
    calls, fake = _capture()
    assert _alert(db, NOW, fake) == 1
    incs = db.query(ProgramIncident).filter_by(organization_id=org.id).all()
    assert len(incs) == 1
    inc = incs[0]
    assert inc.category == "first_outreach_delayed" and inc.status == "open"
    assert json.loads(inc.check_keys) == ["first_messages_late", "texts_never_started"]
    assert json.loads(inc.lead_ids) == [lead.id]
    hist = [e for e in json.loads(inc.history) if e["type"] == "check"]
    assert sorted(e["key"] for e in hist) == ["first_messages_late", "texts_never_started"]
    # One text, one email, owner only.
    assert sorted((c["ch"], c["to"]) for c in calls) == [("email", OWNER_EMAIL), ("sms", OWNER_PHONE)]
    sms = next(c for c in calls if c["ch"] == "sms")["sms"]
    em = next(c for c in calls if c["ch"] == "email")
    url = "%s/program/incident/%s" % (BASE, inc.id)
    assert sms == ("Restland | First outreach delayed | 1 lead | No action needed - next try Fri Nov 27, "
                   "2:26 PM CT | %s %s" % (inc.incident_number, url))
    # No family detail in the text or the link.
    for secret in ("Testa", "Family", "8085550123", "@example.com", lead.id):
        assert secret not in sms
    assert em["subject"] == "Restland | First outreach delayed | 1 lead | %s" % inc.incident_number
    assert inc.incident_number.startswith("RES-261127-0012-")
    html = em["html"]
    _save_sample("opened", sms, em["subject"], html)
    for want in ("What happened", "When it started", "Who or what is affected", "Cause", "Actions already taken",
                 "What the system will do next", "Who follows up", "Do you need to act?", "Next update",
                 "Thu Nov 26, 11:42 AM CT", "Fri Nov 27, 2:26 PM CT", "moved 26 hours 44 minutes later",
                 "Thu Nov 26 was a no-contact day", "Scheduled for Fri Nov 27, 2:26 PM CT", "No email sent",
                 "From the send log", "No - the system will send on its own", url, "Testa Family",
                 "No one is assigned"):
        assert want in html, want
    assert "Cause unconfirmed" not in html
    # Delivery recorded per channel, linked to the incident.
    rows = db.query(ProgramAlert).filter(ProgramAlert.dedupe_key == "incident:%s:opened" % inc.id).all()
    assert sorted((r.channel, r.send_state) for r in rows) == [("email", "sent"), ("sms", "sent")]
    sms_row = next(r for r in rows if r.channel == "sms")
    assert sms_row.provider_id and sms_row.delivered is False          # accepted, not yet delivered
    assert not [c for c in calls if c["to"] in ("+14105550100", "team@example.com")]


def test_repeat_checks_never_send_twice(rl, permitted):
    db, org = rl["db"], rl["org"]
    _family(db, org, rl["admin"])
    calls, fake = _capture()
    assert _alert(db, NOW, fake) == 1
    for mins in (15, 30, 60, 240):
        SC._alerted.clear()                               # a restart between runs
        assert _alert(db, NOW + timedelta(minutes=mins), fake) == 0
    assert len(calls) == 2
    assert db.query(ProgramIncident).count() == 1


def test_more_families_add_to_the_incident_with_one_update_email(rl, permitted):
    db, org = rl["db"], rl["org"]
    _family(db, org, rl["admin"])
    calls, fake = _capture()
    _alert(db, NOW, fake)
    _family(db, org, rl["admin"], first="Second", phone="18085550124", slot=SLOT + timedelta(minutes=1))
    _alert(db, NOW + timedelta(minutes=15), fake)        # inside the hour: recorded, not sent
    assert len(calls) == 2
    _alert(db, NOW + timedelta(minutes=75), fake)
    new = calls[2:]
    assert [c["ch"] for c in new] == ["email"]
    assert new[0]["subject"].startswith("Restland | Update: First outreach delayed | 2 leads |")
    inc = db.query(ProgramIncident).one()
    assert inc.affected_count == 2 and inc.status == "open"


def test_resolution_is_sent_once_when_the_outcome_is_verified(rl, permitted):
    db, org = rl["db"], rl["org"]
    lead = _family(db, org, rl["admin"])
    calls, fake = _capture()
    _alert(db, NOW, fake)
    # 2:26 PM: the first text goes and the carrier reports it delivered.
    db.add(Message(lead_id=lead.id, sender_id=rl["admin"].id, body="x", twilio_sid="SMfirst", sent_at=SLOT,
                   twilio_status="delivered", delivery_status="delivered", send_source="cadence"))
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    st.current_touch_number, st.next_touch_due_at = 1, SLOT + timedelta(days=3)
    db.commit()
    later = SLOT + timedelta(minutes=5)
    assert _alert(db, later, fake) == 1
    inc = db.query(ProgramIncident).one()
    assert inc.status == "resolved" and inc.resolution == "first message: 1 delivered"
    res = calls[2:]
    assert sorted(c["ch"] for c in res) == ["email", "sms"]
    sms = next(c for c in res if c["ch"] == "sms")["sms"]
    assert sms.startswith("Restland | Resolved: First outreach delayed | 1 lead | first message: 1 delivered | ")
    em = next(c for c in res if c["ch"] == "email")
    assert "Resolved" in em["subject"]
    _save_sample("resolved", sms, em["subject"], em["html"])
    assert _alert(db, later + timedelta(minutes=15), fake) == 0
    assert len(calls) == 4


def test_checks_clearing_without_a_recorded_outcome_is_not_resolved(rl, permitted, monkeypatch):
    db, org = rl["db"], rl["org"]
    _family(db, org, rl["admin"])
    calls, fake = _capture()
    _alert(db, NOW, fake)
    ok = [{"key": k, "ok": True, "title": k, "detail": "", "people": [], "lead_ids": []}
          for k in ("texts_never_started", "first_messages_late")]
    monkeypatch.setattr(SC, "run", lambda db_, org_, now=None: {"checks": [dict(c) for c in ok]})
    _alert(db, NOW + timedelta(hours=1), fake)
    inc = db.query(ProgramIncident).one()
    assert inc.status == "open"                          # still only scheduled: nothing to verify
    assert json.loads(inc.history)[-1]["event"] == "monitoring"
    assert len(calls) == 2


def test_email_failure_still_texts_and_records_each_channel(rl, permitted):
    db, org = rl["db"], rl["org"]
    _family(db, org, rl["admin"])
    calls, fake = _capture(fail_email=True)
    _alert(db, NOW, fake)
    inc = db.query(ProgramIncident).one()
    rows = {r.channel: r for r in db.query(ProgramAlert).filter(ProgramAlert.dedupe_key.like("incident:%")).all()}
    assert rows["sms"].send_state == "sent" and rows["email"].send_state == "failed"
    assert "503" in rows["email"].reason
    sms = next(c for c in calls if c["ch"] == "sms")["sms"]
    assert "First outreach delayed" in sms and "No action needed" in sms and inc.id in sms   # useful alone
    v = INC.view(db, rl["prog"], inc)
    assert sorted((n["channel"], n["state"]) for n in v["notices"]) == [("email", "failed"), ("sms", "sent")]


def test_failed_email_is_retried_on_its_own_row(rl, permitted):
    from unittest.mock import patch
    from app.services.programs import alert_ledger
    db, org = rl["db"], rl["org"]
    _family(db, org, rl["admin"])
    calls, fake = _capture(fail_email=True)
    _alert(db, NOW, fake)
    row = db.query(ProgramAlert).filter(ProgramAlert.dedupe_key.like("incident:%"),
                                        ProgramAlert.channel == "email").one()
    row.last_attempt_at = datetime.utcnow() - timedelta(minutes=5)
    row.created_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()
    calls2, fake2 = _capture()
    with patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake2):
        assert alert_ledger.retry_failed(db) == 1
    db.refresh(row)
    assert row.send_state == "sent" and row.attempts == 2
    assert [c["ch"] for c in calls2] == ["email"] and "Restland | First outreach delayed" in calls2[0]["subject"]


def test_pending_batch_is_action_needed_not_an_outage(rl):
    db, prog = rl["db"], rl["prog"]
    calls, fake = _capture()
    found = {"key": "daily_batch_ran", "ok": False, "title": "Daily batch went out",
             "detail": "1 daily batch problem.", "people": ["Today's batch of 200 has not been approved"],
             "lead_ids": [], "signature": "sig1"}
    from unittest.mock import patch
    with patch("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        assert INC.process(db, prog, [found], NOW) == 1
    inc = db.query(ProgramIncident).one()
    assert inc.severity == "action_needed" and inc.category == "batch_approval_pending"
    sms = next(c for c in calls if c["ch"] == "sms")["sms"]
    _save_sample("batch-pending", sms, next(c for c in calls if c["ch"] == "email")["subject"],
                 next(c for c in calls if c["ch"] == "email")["html"])
    assert sms.startswith("Restland | Action needed: today's batch is waiting for your approval (not an outage) | ")
    html = next(c for c in calls if c["ch"] == "email")["html"]
    assert "not a system outage" in html and "approve today" in html.lower()
    v = INC.view(db, prog, inc)
    assert v["next_action"]["to"] == "/program?tab=send" and v["act"]["needed"] is True


def test_already_alerted_by_the_old_notice_creates_the_incident_quietly(rl, permitted):
    """Deploy day: the 12:12 AM notice already went - no second page."""
    db, org = rl["db"], rl["org"]
    _family(db, org, rl["admin"])
    res = SC.run(db, org.id, NOW)
    markers = " ".join("[%s:%s]" % (c["key"], c["signature"]) for c in res["checks"] if not c["ok"])
    db.add(ProgramAlert(organization_id=org.id, kind="self_check", audience="owner", channel="email",
                        recipient=OWNER_EMAIL, delivered=True, message="System self-check found 2 problem(s) " + markers,
                        created_at=NOW - timedelta(minutes=10)))
    db.commit()
    calls, fake = _capture()
    assert _alert(db, NOW, fake) == 0 and calls == []
    inc = db.query(ProgramIncident).one()
    assert any(e.get("event") == "already_notified" for e in json.loads(inc.history))
    # Families who were NOT in that notice still get one update email.
    _family(db, org, rl["admin"], first="Later", phone="18085550125", slot=SLOT + timedelta(minutes=2))
    _alert(db, NOW + timedelta(minutes=75), fake)
    assert [c["ch"] for c in calls] == ["email"] and "Update: First outreach delayed | 2 leads" in calls[0]["subject"]


def test_central_time_in_both_seasons():
    assert INC.ct(datetime(2026, 11, 27, 20, 26)) == "Fri Nov 27, 2:26 PM CT"     # CST, UTC-6
    assert INC.ct(datetime(2026, 7, 4, 15, 0)) == "Sat Jul 4, 10:00 AM CT"        # CDT, UTC-5


def test_cause_is_unconfirmed_without_a_recorded_reason(rl):
    db, org = rl["db"], rl["org"]
    lead = Lead(organization_id=org.id, first_name="No", last_name="Log", phone="12145550199", status="new")
    db.add(lead)
    db.flush()
    db.add(CadenceState(lead_id=lead.id, status="active", current_touch_number=0, cadence_started_at=START,
                        next_touch_due_at=NOW - timedelta(hours=2)))
    db.commit()
    inc = ProgramIncident(organization_id=org.id, incident_number="RES-T-1", category="first_outreach_delayed",
                          title="First outreach delayed", lead_ids=json.dumps([lead.id]), affected_count=1,
                          started_at=NOW)
    db.add(inc)
    db.commit()
    s = INC.summarize(db, rl["prog"], inc, NOW)
    assert s["cause"]["confirmed"] is False and s["cause"]["text"].startswith("Cause unconfirmed")
    assert s["act"][0] is True                            # due in the past, never attempted: owner must look


# ── who may open it ─────────────────────────────────────────────────────────

def _incident(db, org, lead_ids=()):
    inc = ProgramIncident(organization_id=org.id, incident_number="RES-T-2", category="first_outreach_delayed",
                          title="First outreach delayed", lead_ids=json.dumps(list(lead_ids)),
                          affected_count=len(lead_ids), started_at=NOW)
    db.add(inc)
    db.commit()
    return inc


def test_the_incident_page_opens_for_a_manager_of_that_workspace(client, rl, permitted):
    db, org = rl["db"], rl["org"]
    lead = _family(db, org, rl["admin"])
    inc = _incident(db, org, [lead.id])
    r = client.get("/program/incidents/%s" % inc.id, headers=_h(db, rl["admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["organization_id"] == org.id and body["number"] == "RES-T-2"
    assert body["people"][0]["name"] == "Testa Family"
    assert body["people"][0]["record"] == "/program?person=%s" % lead.id
    assert body["people"][0]["sms"]["state"] == "scheduled"
    assert body["next_action"]["to"] == "/program?person=%s" % lead.id
    lst = client.get("/program/incidents", headers=_h(db, rl["admin"]))
    assert lst.status_code == 200 and lst.json()["items"][0]["id"] == inc.id


def test_the_incident_page_is_refused_outside_the_workspace(client, rl):
    from app.models.models import Organization
    db, org = rl["db"], rl["org"]
    inc = _incident(db, org)
    other = Organization(name="Service Corporation International", slug="sci-test", plan="standard",
                         industry="funeral")
    db.add(other)
    db.commit()
    outsider = User(organization_id=other.id, email="sci-admin@example.com", password_hash=hash_password("x" * 12),
                    full_name="SCI Admin", role="org_admin", is_active=True, must_change_password=False)
    advisor = User(organization_id=org.id, email="rl-advisor@example.com", password_hash=hash_password("x" * 12),
                   full_name="Advisor", role="advisor", is_active=True, must_change_password=False)
    db.add_all([outsider, advisor])
    db.commit()
    assert client.get("/program/incidents/%s" % inc.id, headers=_h(db, outsider)).status_code == 404
    assert client.get("/program/incidents/%s" % inc.id, headers=_h(db, advisor)).status_code == 403
    assert client.get("/program/incidents/%s" % inc.id).status_code in (401, 403)
    assert client.get("/program/incidents/not-a-real-id", headers=_h(db, rl["admin"])).status_code == 404


def test_sci_keeps_its_own_notice(db_session, sample_org):
    from app.models.models import Organization
    org = Organization(name="Service Corporation International", slug="sci-x", plan="standard", industry="funeral")
    db_session.add(org)
    db_session.commit()
    prog = setup.ensure_program(db_session, org, name="Service Corporation International")
    db_session.commit()
    assert INC.enabled_for(db_session, prog) is False


def test_restland_is_enabled(rl):
    assert INC.enabled_for(rl["db"], rl["prog"]) is True
