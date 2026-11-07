"""SCI seven-day operation (owner, Nov 7 2026): outreach goes out every day,
Monday through Sunday, 9:00 AM - 6:00 PM Central - journey starts, the daily
batch, every text touch and every email touch. The window only narrows the
family's own clock; intervals are never compressed; a touch outside it waits
for the next opening, spread out (no 9 AM burst); STOP/DNC still stop it.
Synthetic families; Twilio and the email provider are faked; nothing is sent."""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.models.models import CadenceState, Lead, Message
from app.models.program_models import OutreachProgram, ProgramSourceRecord
from app.services import cadence_service as cs
from app.services.programs import daily_batch, email_touches, journey, send_approval, send_window
from tests.test_cadence_engine import _logs, _sending_on, _twilio
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)

CT, UTC = ZoneInfo("America/Chicago"), ZoneInfo("UTC")


def _utc(y, m, d, hh, mm=0):
    """Naive UTC for a Central wall-clock time."""
    return datetime(y, m, d, hh, mm, tzinfo=CT).astimezone(UTC).replace(tzinfo=None)


def _ct(dt):
    return dt.replace(tzinfo=UTC).astimezone(CT)


SAT_10 = _utc(2026, 11, 7, 10)            # Saturday 10:00 AM CST
SUN_10 = _utc(2026, 11, 8, 10)            # Sunday
SAT_815 = _utc(2026, 11, 7, 8, 15)        # inside the family's clock, before 9 AM Central
SAT_1830 = _utc(2026, 11, 7, 18, 30)


# ── the window ──────────────────────────────────────────────────────────────

def test_the_window_is_every_day_9_to_6_central():
    assert send_window.permits(SAT_10) and send_window.permits(SUN_10)
    assert send_window.permits(_utc(2026, 11, 9, 9, 0)) and send_window.permits(_utc(2026, 11, 9, 17, 59))
    assert not send_window.permits(_utc(2026, 11, 9, 8, 59)) and not send_window.permits(_utc(2026, 11, 9, 18, 0))
    assert send_window.describe()["days_label"] == "Every day"


def test_next_opening_same_morning_next_day_and_across_daylight_saving():
    assert _ct(send_window.next_open(SAT_815)).strftime("%a %H:%M") == "Sat 09:00"
    assert _ct(send_window.next_open(SAT_1830)).strftime("%a %H:%M") == "Sun 09:00"     # Sunday is a sending day
    # Sat Oct 31 2026 evening -> Sun Nov 1 (clocks fall back at 2 AM): still 9:00 AM local
    nxt = send_window.next_open(_utc(2026, 10, 31, 19, 0))
    assert _ct(nxt).strftime("%a %m-%d %H:%M") == "Sun 11-01 09:00" and nxt.hour == 15   # CST = UTC-6
    # spring forward (Sun Mar 14 2027): 9:00 AM local is 14:00 UTC
    spring = send_window.next_open(_utc(2027, 3, 13, 19, 0))
    assert _ct(spring).strftime("%a %H:%M") == "Sun 09:00" and spring.hour == 14
    # a weekdays-only choice still rolls Friday evening to Monday
    assert _ct(send_window.next_open(_utc(2026, 11, 6, 18, 30), days="12345")).strftime("%a") == "Mon"


def test_stagger_is_stable_and_under_45_minutes():
    vals = [send_window.stagger_minutes("lead-%d" % i) for i in range(200)]
    assert all(0 <= v < 45 for v in vals) and len(set(vals)) > 20
    assert send_window.stagger_minutes("same") == send_window.stagger_minutes("same")


# ── journey starts ──────────────────────────────────────────────────────────

def test_journey_starts_run_on_saturday_and_sunday_inside_the_window():
    fri_late = _utc(2026, 11, 6, 17, 55)
    slots = [_ct(s) for s in journey.release_slots(fri_late, 3, timedelta(minutes=5))]
    assert [s.strftime("%a %H:%M") for s in slots] == ["Fri 17:55", "Sat 09:00", "Sat 09:05"]
    many = [_ct(s) for s in journey.release_slots(SAT_10, 200, timedelta(minutes=5))]
    assert {s.strftime("%a") for s in many} == {"Sat", "Sun"}
    assert all((9, 0) <= (s.hour, s.minute) < (18, 0) for s in many)
    assert len({s for s in many}) == 200                                   # one start per slot


# ── daily batch ─────────────────────────────────────────────────────────────

def _prog(db, org):
    return db.query(OutreachProgram).filter_by(organization_id=org.id).one()


def test_daily_batch_runs_on_weekends_and_never_past_6_pm(program, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    p = _prog(db, org)
    p.daily_batch_count, p.daily_batch_by, p.daily_batch_spacing = 40, admin.id, 10
    p.daily_batch_days = None                                               # never chosen: every day
    db.commit()
    calls = []
    monkeypatch.setattr(daily_batch, "pick", lambda db, prog, n: ["x%d" % i for i in range(n)])
    monkeypatch.setattr(send_approval, "approve",
                        lambda db, prog, user, ids, **kw: calls.append((ids, kw)) or {"approved": len(ids)})
    assert daily_batch.run_for(db, p, SAT_10)["approved"] == 40             # Saturday is a sending day
    assert daily_batch.run_for(db, p, SAT_10) is None                       # once a day
    # 40 x 10 min from 9:02 ends 15:32 - inside. A 2:50 PM start fits only 19.
    assert daily_batch.fits_today(_ct(_utc(2026, 11, 8, 9, 0)), 10) == 54
    assert daily_batch.fits_today(_ct(_utc(2026, 11, 8, 14, 50)), 10) == 19
    res = daily_batch.run_for(db, p, _utc(2026, 11, 8, 14, 50))             # Sunday, late start
    assert res["approved"] == 19 and len(calls[-1][0]) == 19
    p.daily_batch_days = "12345"                                            # a manager's weekday choice holds
    p.daily_batch_last_date = None
    db.commit()
    assert daily_batch.run_for(db, p, _utc(2026, 11, 14, 10)) is None       # Saturday Nov 14


def test_choosing_every_day_late_in_the_day_raises_no_missed_batch_alarm(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    p = _prog(db, org)
    p.daily_batch_days, p.daily_batch_last_date = "12345", None
    db.commit()
    h = _h(db, admin)
    late_sat = _ct(_utc(2026, 11, 7, 16, 0))
    with patch.object(daily_batch, "_local_now", lambda now=None: late_sat):
        r = client.post("/program/daily-batch", headers=h, json={"count": 40, "confirm": "DAILY 40", "days": "1234567"})
    assert r.status_code == 200 and r.json()["days"] == "1234567"
    db.refresh(p)
    assert p.daily_batch_last_date == "2026-11-07"                          # today could not run: tomorrow's is next


# ── text touches ────────────────────────────────────────────────────────────

@pytest.fixture
def sci_text(program, sample_advisor, monkeypatch):
    db, org = program["db"], program["org"]
    _sending_on(monkeypatch)
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True, "reason": ""})
    lead = _lead_for(db, org, sample_advisor, "L001", phone="2055550111")
    lead.is_test = False
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    st.current_touch_number, st.cadence_started_at = 2, _utc(2026, 11, 4, 10)
    st.next_touch_due_at = _utc(2026, 11, 7, 8, 0)
    db.commit()
    return {**program, "lead": lead, "state": st}


def _run(db, at):
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)), \
         patch("app.services.cadence_service._spacing_hold_until", return_value=None):
        r = cs.run_due_cadences(db, now=at)
    return r, tw


def test_a_touch_due_before_9_waits_for_the_opening_then_goes_once(sci_text):
    db, st = sci_text["db"], sci_text["state"]
    r, tw = _run(db, SAT_815)
    assert tw.messages.create.call_count == 0 and r["sent"] == 0
    db.refresh(st)
    due = _ct(st.next_touch_due_at)
    assert due.strftime("%a") == "Sat" and (9, 0) <= (due.hour, due.minute) < (9, 45)   # spread, not 9:00 sharp
    assert st.current_touch_number == 2                                    # nothing skipped or advanced
    held = [l for l in _logs(db, sci_text["lead"]) if l.outcome == cs.OUTCOME_BLOCKED]
    assert held and "SCI sending window" in held[-1].reason
    with patch("app.services.sms_service.send_sms") as send:
        send.return_value = Message(lead_id=sci_text["lead"].id, body="t3", twilio_sid="SMok", twilio_status="queued",
                                    sent_at=SAT_10)
        cs.run_due_cadences(db, now=SAT_10)
        cs.run_due_cadences(db, now=SAT_10 + timedelta(minutes=5))         # the next pass sends nothing more
    assert send.call_count == 1
    db.refresh(st)
    assert st.current_touch_number == 3


def test_sunday_touches_go_and_keep_their_interval(sci_text):
    db, st = sci_text["db"], sci_text["state"]
    st.next_touch_due_at = SUN_10 - timedelta(minutes=1)
    db.commit()
    with patch("app.services.sms_service.send_sms") as send:
        send.return_value = Message(lead_id=sci_text["lead"].id, body="t3", twilio_sid="SMok", twilio_status="queued",
                                    sent_at=SUN_10)
        cs.run_due_cadences(db, now=SUN_10)
    assert send.call_count == 1
    db.refresh(st)
    # touch 4 is day 5 of the schedule (or the 2-day gap after this send) - never pulled in
    assert st.next_touch_due_at >= SUN_10 + timedelta(days=2) - timedelta(minutes=1)


def test_after_6_pm_waits_for_sunday_morning(sci_text):
    db, st = sci_text["db"], sci_text["state"]
    st.next_touch_due_at = SAT_1830 - timedelta(minutes=1)
    db.commit()
    r, tw = _run(db, SAT_1830)
    assert tw.messages.create.call_count == 0
    db.refresh(st)
    assert _ct(st.next_touch_due_at).strftime("%a") == "Sun" and _ct(st.next_touch_due_at).hour == 9


def test_stop_and_dnc_still_win_on_a_weekend(sci_text):
    db, st, lead = sci_text["db"], sci_text["state"], sci_text["lead"]
    lead.status = "dnc"
    st.next_touch_due_at = SAT_10 - timedelta(minutes=1)
    db.commit()
    r, tw = _run(db, SAT_10)
    assert tw.messages.create.call_count == 0
    db.refresh(st)
    assert st.status.startswith("stopped")


def test_other_workspaces_keep_their_own_hours(db_session, sample_org, sample_advisor, monkeypatch):
    """No program in this workspace: the SCI window does not apply."""
    from tests.test_cadence_engine import _due, _lead
    _sending_on(monkeypatch)
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True, "reason": ""})
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552777")
    _due(db_session, lead)
    early = datetime.utcnow().replace(hour=13, minute=30, second=0, microsecond=0) + timedelta(days=1)  # ~7:30 CT
    _r, tw = _run(db_session, early)
    assert tw.messages.create.call_count == 1


# ── email touches ───────────────────────────────────────────────────────────

def test_email_hours_are_the_same_window_every_day(program):
    db, org = program["db"], program["org"]
    assert email_touches.in_sending_hours(db, org.id, SUN_10)
    assert not email_touches.in_sending_hours(db, org.id, _utc(2026, 11, 8, 7, 45))   # 8:45 ET, 7:45 CT
    assert not email_touches.in_sending_hours(db, org.id, _utc(2026, 11, 8, 18, 15))  # 7:15 PM ET, 6:15 CT
    assert email_touches.in_sending_hours(db, org.id, _utc(2026, 11, 8, 17, 50))


def test_email_followups_go_on_sunday_and_never_before_the_interval(program, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramEmailTouch
    from tests.test_outreach_program import _fake_provider, _touch_ready
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    sent = []
    with _fake_provider(sent):
        et.run(db, org.id, now=_utc(2026, 11, 8, 7, 45))                  # Sunday, before 9 AM Central
        assert sent == []
        r = et.run(db, org.id, now=SUN_10)                                 # Sunday 10 AM: goes
        et.run(db, org.id, now=SUN_10 + timedelta(hours=3))                # touch 2 is days away
    assert r["sent"] == 1 and len(sent) == 1
    rows = db.query(ProgramEmailTouch).filter_by(lead_id=lead.id).all()
    assert [(t.touch_number, t.status) for t in rows] == [(1, "sent")]


# ── the progress view ───────────────────────────────────────────────────────

def test_progress_puts_every_family_in_exactly_one_group(client, program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    a = _lead_for(db, org, sample_advisor, "L001", phone="2055550101")
    b = _lead_for(db, org, sample_advisor, "L002", phone="2055550102", email="b@example.com")
    b.status = "dnc"
    for sid in ("L001", "L002"):
        r = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id=sid).one()
        r.approved_to_send_at = datetime.utcnow()
    db.commit()
    fam_key = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one().campaign_family
    body = client.get("/program/command/campaign-progress?campaign=%s" % fam_key, headers=_h(db, admin)).json()
    rep = body["report"]
    ro = rep["roster"]
    groups = sum(ro[g] for g in ("excluded", "data_review", "not_started", "active", "stopped", "completed", "held"))
    assert groups == ro["families"]
    assert ro["stopped"] >= 1 and rep["stopped_why"]["stop_dnc"] >= 1
    assert rep["window"]["days_label"] == "Every day" and len(rep["daily"]) == 7 and len(rep["next_7_days"]) == 7
    text = str(body)
    for pii in ("Ollie", "Testcase", "2055550101", "ollie@example.com", "b@example.com"):
        assert pii not in text
    assert a.id not in text                                                # counts only


def test_progress_is_tenant_scoped(client, program, db_session):
    from app.models.models import Organization
    from tests.test_outreach_program import _admin
    other = Organization(name="Elsewhere 7d", slug="elsewhere-7d")
    db_session.add(other)
    db_session.commit()
    stranger = _admin(db_session, other, email="stranger7d@example.com")
    r = client.get("/program/command/campaign-progress", headers=_h(db_session, stranger))
    assert r.status_code in (403, 404)


def test_the_screen_shows_seven_days_and_the_progress_panel():
    send = open("frontend/src/pages/program/sci/Send.jsx", encoding="utf-8").read()
    assert "Every day (Mon–Sun)" in send and "useState('1234567')" in send and "Saturdays too" not in send
    dash = open("frontend/src/pages/program/sci/Dashboard.jsx", encoding="utf-8").read()
    assert "<CampaignProgress />" in dash
    panel = open("frontend/src/pages/program/sci/CampaignProgress.jsx", encoding="utf-8").read()
    for label in ("Total roster", "Approved", "Excluded", "Not started", "Active", "Stopped", "Completed", "Held",
                  "Texts attempted", "Delivered", "Emails sent", "Bounced", "Blocked", "Replies", "Handoffs", "Booked",
                  "Next 7 days", "overdue"):
        assert label in panel, label
