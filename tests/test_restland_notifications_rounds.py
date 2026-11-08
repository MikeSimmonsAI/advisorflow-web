"""RESTLAND: second campaign rounds, appointment confirmations, reminders and
the 24-hour survey (Issue #507). Synthetic data only; the senders are
replaced by recorders - nothing reaches a provider or a family."""
from datetime import date, datetime, timedelta

import pytest

from app.models.models import BookingLink, CadenceState, CadenceTouchLog, Lead, Message, User
from app.models.program_models import CampaignFamily, ProgramEmailTouch, ProgramResponse, ProgramSourceRecord
from app.models.reset_models import AppointmentNotification, AppointmentSurvey, CampaignCycleMember
from app.services.programs import appointment_notifications as AN
from app.services.programs import appointment_ops as OPS
from app.services.programs import campaigns, journey, rotation
from app.services.programs import restland_ffr as F
from app.services.programs import send_approval
from app.services.programs import visit_survey as VS
from app.services.programs import workspace_configure as WC
from tests.test_restland_booking_cadence import L, NO_BUSY, configured, prog_of, rl  # noqa: F401 (fixture)


class Sent:
    def __init__(self):
        self.calls = []

    def __call__(self, db, row, booking, lead, msg):
        self.calls.append((row.purpose, row.channel, msg))
        return "msg-%d" % len(self.calls), "prov-%d" % len(self.calls)


def _enable(rl, survey=False):
    import json
    prog = prog_of(rl)
    raw = json.loads(prog.booking_policy_json)
    raw.setdefault("notifications", {}).update(enabled=True, survey_enabled=survey)
    prog.booking_policy_json = json.dumps(raw)
    rl["db"].commit()


def _activate_since(rl, days_ago=10):
    import json
    prog = prog_of(rl)
    raw = json.loads(prog.booking_policy_json)
    raw.setdefault("notifications", {})["active_since"] = (datetime.utcnow() - timedelta(days=days_ago)).isoformat() + "Z"
    prog.booking_policy_json = json.dumps(raw)
    rl["db"].commit()


def _booking(rl, lead, local_start, *, status="booked", meeting="office", created=None):
    b = BookingLink(lead_id=lead.id, user_id=rl["mike"].id, status=status, booked_time=local_start,
                    appt_duration=45, meeting_type=meeting,
                    visit_address="4521 Elm Street, Dallas, TX 75214" if meeting == "home" else
                    "13005 Greenville Ave, Dallas, TX 75243", appt_label="Family File Review")
    rl["db"].add(b)
    rl["db"].commit()
    if created is not None:
        b.created_at = created
        rl["db"].commit()
    return b


def _rows(rl, b, **kw):
    q = rl["db"].query(AppointmentNotification).filter_by(booking_id=b.id, **kw)
    return q.all()


# ── settings off after configuration ────────────────────────────────────────

def test_configuration_leaves_every_appointment_message_off(rl):
    configured(rl)
    s = AN.settings(__import__("app.services.programs.booking_policy", fromlist=["x"]).for_program(prog_of(rl)))
    assert s["enabled"] is False and s["survey_enabled"] is False and s["active_since"]
    assert s["reminder_minutes"] == [1440, 120] and s["window"] == ["09:00", "18:00"]
    # The approved booking rules are untouched by the new settings.
    from app.services.programs import booking_policy as BP
    pol = BP.for_program(prog_of(rl))
    assert pol["starts"] == [(10, 0), (11, 30), (13, 0), (14, 30), (16, 0)] and pol["days"] == "0123456"
    assert (pol["duration"], pol["gap_after"]) == (45, 45)


# ── confirmations ───────────────────────────────────────────────────────────

def _next(dow, min_days=3):
    d = date.today() + timedelta(days=min_days)
    while d.weekday() != dow:
        d += timedelta(days=1)
    return d


def test_one_confirmation_per_channel_and_nothing_sent_while_off(rl):
    db = rl["db"]
    configured(rl)
    from app.services.programs import appointments as A
    lead = rl["leads"][2]
    sat = _next(5)
    r = A.book(db, prog_of(rl), lead, datetime(sat.year, sat.month, sat.day, 13, 0), A.VISIT, family_text=True,
               meeting_key="office")
    assert r["ok"], r
    b = db.query(BookingLink).filter_by(id=r["booking_id"]).one()
    conf = _rows(rl, b, purpose="confirmation")
    assert sorted(x.channel for x in conf) == ["email", "sms"]
    assert all(x.status == "scheduled" for x in conf)                 # held: messages are off
    # Repeats (webhook retry, second worker) never add another.
    AN.on_booked(db, b)
    AN.on_booked(db, b)
    db.commit()
    assert len(_rows(rl, b, purpose="confirmation")) == 2
    rec = Sent()
    assert AN.run(db, rl["org"].id, sender=rec)["held_off"] >= 2 and rec.calls == []
    # Switched on: each channel exactly once, then never again.
    _enable(rl)
    AN.run(db, rl["org"].id, sender=rec)
    AN.run(db, rl["org"].id, sender=rec)
    confs = [c for c in rec.calls if c[0] == "confirmation"]
    assert sorted(c[1] for c in confs) == ["email", "sms"]
    sms = next(c[2]["body"] for c in confs if c[1] == "sms")
    assert "Family File Review" in sms and "45 minutes" in sms and "Central" in sms
    assert "ask for Mike Simmons" in sms and "13005 Greenville Ave" in sms and "reply" in sms


def test_home_visit_confirmation_says_the_advisor_comes_to_the_address(rl):
    db = rl["db"]
    configured(rl)
    _enable(rl)
    b = _booking(rl, rl["leads"][3], datetime.combine(_next(6), datetime.min.time()).replace(hour=11, minute=30),
                 meeting="home")
    AN.on_booked(db, b)
    db.commit()
    rec = Sent()
    AN.run(db, rl["org"].id, sender=rec)
    body = next(c[2]["body"] for c in rec.calls if c[1] == "sms")
    assert "will come to you at 4521 Elm Street, Dallas, TX 75214" in body and "ask for" not in body


def test_opt_out_after_booking_stops_the_message(rl):
    db = rl["db"]
    configured(rl)
    _enable(rl)
    lead = rl["leads"][4]
    b = _booking(rl, lead, datetime.combine(_next(2), datetime.min.time()).replace(hour=13))
    AN.on_booked(db, b)
    db.commit()
    lead.status = "dnc"
    db.commit()
    rec = Sent()
    out = AN.run(db, rl["org"].id, sender=rec)
    assert rec.calls == [] and out["skipped"] >= 2


# ── reminders ───────────────────────────────────────────────────────────────

def _due_local(row):
    return AN._local(row.due_at)


def test_reminders_24h_and_2h_and_the_early_morning_move(rl):
    db = rl["db"]
    configured(rl)
    now = datetime(2026, 11, 9, 15, 0)                               # Mon 9:00 AM Central
    b = _booking(rl, rl["leads"][5], datetime(2026, 11, 13, 14, 30))  # Fri 2:30 PM
    AN.on_booked(db, b, now=now)
    r24 = _rows(rl, b, purpose="reminder_24h", channel="sms")[0]
    r2 = _rows(rl, b, purpose="reminder_2h", channel="sms")[0]
    assert _due_local(r24) == datetime(2026, 11, 12, 14, 30) and _due_local(r2) == datetime(2026, 11, 13, 12, 30)
    early = _booking(rl, rl["leads"][6], datetime(2026, 11, 13, 10, 0))   # 2 hours before = 8:00 AM
    AN.on_booked(db, early, now=now)
    r2e = _rows(rl, early, purpose="reminder_2h", channel="sms")[0]
    # 8:00 AM would move to 5:45 PM the evening before - 7 h 45 m after the
    # 24-hour reminder. The collision rule skips it; it is never called "2 hours".
    assert r2e.status == "skipped" and _due_local(r2e) == datetime(2026, 11, 12, 17, 45)
    assert "7 h 45 m" in r2e.reason and "8:00 AM" in r2e.reason
    assert _rows(rl, early, purpose="reminder_24h", channel="sms")[0].status == "scheduled"


def test_collision_rule_keeps_the_near_reminder_and_drops_the_one_after_the_confirmation(rl):
    db = rl["db"]
    configured(rl)
    start = datetime(2026, 11, 13, 14, 30)                            # Fri 2:30 PM
    now = AN._utc(start) - timedelta(hours=26)                        # booked Thu 12:30 PM
    b = _booking(rl, rl["leads"][10], start)
    AN.on_booked(db, b, now=now)
    r24 = _rows(rl, b, purpose="reminder_24h", channel="sms")[0]
    r2 = _rows(rl, b, purpose="reminder_2h", channel="sms")[0]
    assert r24.status == "skipped" and "2 h 00 m" in r24.reason       # 2 hours after the confirmation
    assert r2.status == "scheduled" and _due_local(r2) == datetime(2026, 11, 13, 12, 30)


def test_a_moved_reminder_that_does_not_collide_is_described_accurately(rl):
    db = rl["db"]
    configured(rl)
    import json
    prog = prog_of(rl)
    raw = json.loads(prog.booking_policy_json)
    raw.setdefault("notifications", {})["reminder_minutes"] = [120]
    prog.booking_policy_json = json.dumps(raw)
    db.commit()
    start = datetime(2026, 11, 13, 10, 0)
    b = _booking(rl, rl["leads"][11], start)
    AN.on_booked(db, b, now=AN._utc(start) - timedelta(days=5))
    r2 = _rows(rl, b, purpose="reminder_2h", channel="sms")[0]
    assert r2.status == "scheduled" and _due_local(r2) == datetime(2026, 11, 12, 17, 45)
    assert "16 h 15 m before the visit" in r2.reason and "instead of 2 h 00 m" in r2.reason
    policy, lead = AN._policy_for_booking(db, b)
    text = AN.compose(db, r2, b, lead, policy)["body"]
    assert "is tomorrow:" in text and "coming up" not in text


def test_close_in_booking_skips_elapsed_reminders_instead_of_dumping_them(rl):
    db = rl["db"]
    configured(rl)
    now = datetime(2026, 11, 13, 16, 0)                              # Fri 10:00 AM Central
    b = _booking(rl, rl["leads"][7], datetime(2026, 11, 13, 14, 30))  # same day 2:30 PM
    AN.on_booked(db, b, now=now)
    assert _rows(rl, b, purpose="reminder_24h", channel="sms")[0].status == "skipped"
    r2 = _rows(rl, b, purpose="reminder_2h", channel="sms")[0]
    assert r2.status == "scheduled" and _due_local(r2) == datetime(2026, 11, 13, 12, 30)
    later = datetime(2026, 11, 14, 14, 0)                            # booked Sat 8 AM for 10 AM
    b2 = _booking(rl, rl["leads"][8], datetime(2026, 11, 14, 10, 0))
    AN.on_booked(db, b2, now=later)
    assert all(r.status == "skipped" for r in _rows(rl, b2) if r.purpose.startswith("reminder"))


def test_reminders_across_daylight_saving_are_exactly_24_hours(rl):
    db = rl["db"]
    configured(rl)
    for start in (datetime(2026, 11, 2, 11, 30), datetime(2027, 3, 14, 11, 30)):   # day after fall-back / DST day
        b = _booking(rl, rl["leads"][9], start)
        AN.on_booked(db, b, now=AN._utc(start) - timedelta(days=5))
        r24 = _rows(rl, b, purpose="reminder_24h", channel="sms")[0]
        assert AN._utc(start) - r24.due_at == timedelta(hours=24)
        assert AN.in_window(r24.due_at, AN.DEFAULTS)


def test_reminder_missed_while_off_is_not_sent_late(rl):
    db = rl["db"]
    configured(rl)
    now = datetime(2026, 11, 9, 15, 0)
    b = _booking(rl, rl["leads"][10], datetime(2026, 11, 13, 14, 30))
    AN.on_booked(db, b, now=now)
    db.commit()
    _enable(rl)
    rec = Sent()
    AN.run(db, rl["org"].id, now=AN._utc(datetime(2026, 11, 12, 19, 0)), sender=rec)   # 4.5 h after the 24h time
    assert not [c for c in rec.calls if c[0] == "reminder_24h"]
    assert _rows(rl, b, purpose="reminder_24h", channel="sms")[0].reason.startswith("missed its time")


# ── reschedule / cancel ─────────────────────────────────────────────────────

def test_reschedule_supersedes_old_messages_and_cancel_cancels_the_rest(rl):
    db = rl["db"]
    configured(rl)
    from app.services.programs import appointments as A
    sun = _next(6)
    r = A.book(db, prog_of(rl), rl["leads"][11], datetime(sun.year, sun.month, sun.day, 10, 0), A.VISIT,
               family_text=True, meeting_key="office")
    b = db.query(BookingLink).filter_by(id=r["booking_id"]).one()
    old_version = AN._version(b)
    OPS.reschedule(db, prog_of(rl), b, datetime(sun.year, sun.month, sun.day, 14, 30), rl["staff"],
                   busy_reader=NO_BUSY)
    db.refresh(b)
    olds = [x for x in _rows(rl, b) if x.version == old_version]
    assert olds and all(x.status in ("superseded", "skipped") for x in olds)
    assert len(_rows(rl, b, purpose="updated")) == 2 and len(_rows(rl, b, purpose="confirmation")) == 2
    OPS.cancel(db, prog_of(rl), b, rl["staff"])
    db.refresh(b)
    assert b.status == "cancelled"
    assert not [x for x in _rows(rl, b) if x.status == "scheduled" and x.purpose != "cancelled"]
    assert len(_rows(rl, b, purpose="cancelled")) == 2


# ── the 24-hour survey ──────────────────────────────────────────────────────

def _completed(rl, lead, start_local, meeting="office", now=None, ended=None):
    db = rl["db"]
    b = _booking(rl, lead, start_local, meeting=meeting, created=datetime.utcnow() - timedelta(days=1))
    OPS.attendance(db, prog_of(rl), b, "completed", rl["staff"], ended_at=ended,
                   now=now or AN._utc(start_local) + timedelta(hours=2))
    return b


def _survey_row(rl, b, ch="sms"):
    return _rows(rl, b, purpose="survey", channel=ch)[0]


def test_survey_only_after_completed_visits_and_never_before_24_hours(rl):
    db = rl["db"]
    configured(rl)
    _activate_since(rl)
    future = datetime.utcnow() + timedelta(days=2)
    for meeting, idx in (("office", 12), ("home", 13)):
        start = AN._local(future).replace(hour=14, minute=30, second=0, microsecond=0)
        b = _completed(rl, rl["leads"][idx], start, meeting=meeting, now=AN._utc(start) + timedelta(hours=1))
        row = _survey_row(rl, b)
        end = AN._utc(start) + timedelta(minutes=45)
        assert row.due_at >= end + timedelta(hours=24) and AN.in_window(row.due_at, AN.DEFAULTS)
        assert len(_rows(rl, b, purpose="survey")) == 2                 # text + email, same survey
        assert db.query(AppointmentSurvey).filter_by(booking_id=b.id).count() == 1
    # A 4:00 PM visit ends 4:45; 24 hours later is 4:45 PM - inside the window.
    start = AN._local(future).replace(hour=16, minute=0, second=0, microsecond=0)
    b = _completed(rl, rl["leads"][14], start, now=AN._utc(start) + timedelta(hours=1))
    assert _due_local(_survey_row(rl, b)) == start + timedelta(days=1, minutes=45)


def test_no_survey_for_no_show_unknown_cancelled_or_old_visits(rl):
    db = rl["db"]
    configured(rl)
    start = AN._local(datetime.utcnow() + timedelta(days=2)).replace(hour=13, minute=0, second=0, microsecond=0)
    ns = _booking(rl, rl["leads"][15], start, created=AN._utc(start) - timedelta(days=1))
    OPS.attendance(db, prog_of(rl), ns, "no_show", rl["staff"], now=AN._utc(start) + timedelta(hours=2))
    assert not _rows(rl, ns, purpose="survey")
    unknown = _booking(rl, rl["leads"][16], start, created=AN._utc(start) - timedelta(days=1))
    assert not _rows(rl, unknown, purpose="survey")                      # attendance never recorded
    assert unknown.id in {b.id for b in OPS.pending_attendance(db, rl["org"].id, now=AN._utc(start) + timedelta(hours=3))}
    old = _booking(rl, rl["leads"][17], datetime(2026, 8, 12, 10, 0), created=datetime(2026, 8, 1))
    old.status = "completed"
    db.commit()
    assert AN.schedule_survey(db, old)["scheduled"] == 0                 # predates appointment messages
    with pytest.raises(OPS.OpError):                                     # the visit hasn't happened
        OPS.attendance(db, prog_of(rl), _booking(rl, rl["leads"][18], start), "completed", rl["staff"],
                       now=AN._utc(start) - timedelta(hours=5))


def test_late_attendance_marking_waits_for_the_next_permitted_time(rl):
    db = rl["db"]
    configured(rl)
    _activate_since(rl)
    start = AN._local(datetime.utcnow() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    marked = AN._utc(start) + timedelta(days=3, hours=11)                 # marked 3 days later, 9 PM Central
    b = _completed(rl, rl["leads"][19], start, now=marked)
    row = _survey_row(rl, b)
    assert row.due_at >= marked and _due_local(row).time() == datetime(2000, 1, 1, 9, 0).time()


def test_survey_send_link_one_answer_task_and_no_leaks(rl, client):
    db = rl["db"]
    configured(rl)
    _activate_since(rl)
    _enable(rl, survey=True)
    start = AN._local(datetime.utcnow() - timedelta(days=3)).replace(hour=13, minute=0, second=0, microsecond=0)
    lead = rl["leads"][20]
    b = _booking(rl, lead, start, created=datetime.utcnow() - timedelta(hours=1))
    OPS.attendance(db, prog_of(rl), b, "completed", rl["staff"], now=datetime.utcnow())
    sv = db.query(AppointmentSurvey).filter_by(booking_id=b.id).one()
    assert len(sv.token) >= 30 and lead.phone not in sv.token
    row = _survey_row(rl, b)
    rec = Sent()
    AN.run(db, rl["org"].id, now=row.due_at + timedelta(minutes=1), sender=rec)
    bodies = [c[2]["body"] for c in rec.calls if c[0] == "survey"]
    assert len(bodies) == 2 and all("/visit-survey/" + sv.token in x for x in bodies)
    ctx = client.get("/visit-survey/%s" % sv.token).json()
    assert ctx["state"] == "open" and lead.phone not in str(ctx) and (lead.email or "x") not in str(ctx)
    assert client.get("/visit-survey/" + "x" * 32).status_code == 404
    bad = client.post("/visit-survey/%s" % sv.token, json={"rating": 9, "helpful": "yes"})
    assert bad.status_code == 422
    ok = client.post("/visit-survey/%s" % sv.token, json={"rating": 5, "helpful": "somewhat", "comments": "Kind",
                                                          "follow_up": True, "follow_up_method": "phone"})
    assert ok.status_code == 200 and ok.json()["follow_up_task"] is True
    assert client.post("/visit-survey/%s" % sv.token, json={"rating": 1, "helpful": "no"}).status_code == 409
    db.refresh(sv)
    assert (sv.rating, sv.helpful, sv.follow_up_method) == (5, "somewhat", "phone")
    from app.models.work_models import LeadTask
    t = db.query(LeadTask).filter_by(id=sv.task_id).one()
    assert t.assigned_to_id == b.user_id and t.status == "open" and t.lead_id == lead.id
    # Nothing was sent to the family because of the answer.
    assert db.query(Message).filter_by(lead_id=lead.id).count() == 0


def test_expired_survey_link_is_explained(rl, client):
    db = rl["db"]
    configured(rl)
    _activate_since(rl)
    start = AN._local(datetime.utcnow() - timedelta(days=3)).replace(hour=13, minute=0, second=0, microsecond=0)
    b = _booking(rl, rl["leads"][21], start, created=datetime.utcnow() - timedelta(hours=1))
    OPS.attendance(db, prog_of(rl), b, "completed", rl["staff"], now=datetime.utcnow())
    sv = db.query(AppointmentSurvey).filter_by(booking_id=b.id).one()
    sv.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.get("/visit-survey/%s" % sv.token).json()["state"] == "expired"
    assert client.post("/visit-survey/%s" % sv.token, json={"rating": 4, "helpful": "yes"}).status_code == 410


# ── two complete campaign rounds for one family ─────────────────────────────

def _finish_round(rl, lead, base, last_at):
    db = rl["db"]
    db.add(Message(lead_id=lead.id, sender_id=rl["mike"].id, body="t5", sent_at=last_at, delivery_status="delivered"))
    for n, _d in campaigns.email_steps(F.CAMPAIGN_KEY):
        db.add(ProgramEmailTouch(organization_id=rl["org"].id, lead_id=lead.id, touch_number=base + n, status="sent",
                                 attempted_at=last_at - timedelta(days=1)))
    st = db.query(CadenceState).filter_by(lead_id=lead.id).first()
    st.status, st.current_touch_number = "completed", 5
    db.commit()


def test_two_complete_rounds_for_one_family_keep_round_one_history(rl, monkeypatch):
    db = rl["db"]
    configured(rl)
    org, prog = rl["org"], prog_of(rl)
    m1 = db.query(CampaignCycleMember).filter_by(organization_id=org.id, status="staged").first()
    lead = db.query(Lead).filter_by(id=m1.lead_id).one()
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=F.CAMPAIGN_KEY).one()
    fam.is_active = True                                    # test only: the campaign is off in production
    db.commit()
    assert send_approval.approve(db, prog, rl["mike"], [lead.id])["approved"] == 1
    journey.start(db, prog, fam, rl["mike"])
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    db.add(CadenceTouchLog(cadence_state_id=st.id, lead_id=lead.id, organization_id=org.id, touch_number=1,
                           attempt_seq=1, outcome="sent", attempted_at=datetime.utcnow() - timedelta(days=58),
                           cycle_key="ffr-batch-1"))
    db.commit()
    last1 = datetime.utcnow() - timedelta(days=45)
    _finish_round(rl, lead, 0, last1)
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all():
        r.journey_started_at = last1 - timedelta(days=14)
    db.commit()
    rotation.refresh(db, org.id)
    db.refresh(m1)
    assert (m1.status, m1.completion_reason) == ("completed", "no_reply")
    assert m1.next_eligible_at == last1 + timedelta(days=30)
    # During the rest: refused. After it: staged for review only - never approved by itself.
    early = rotation.stage_return(db, org.id, [lead], "ffr-return-x", now=last1 + timedelta(days=29))
    assert early["staged"] == [] and early["refused"][0]["reason"] == "still resting"
    res = rotation.stage_return(db, org.id, [lead], "ffr-return-1")
    assert res["staged"] == [lead.id]
    m2 = db.query(CampaignCycleMember).filter_by(cycle_key="ffr-return-1", lead_id=lead.id).one()
    assert m2.round_number == 2 and m2.status == "staged"
    assert all(r.approved_to_send_at is None and r.campaign_family == F.RETURN_KEY
               for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all())
    # Round 2: approve, start - the text journey restarts from text 1, round 1 kept.
    ret = db.query(CampaignFamily).filter_by(organization_id=org.id, key=F.RETURN_KEY).one()
    ret.is_active = True
    db.commit()
    assert send_approval.approve(db, prog, rl["mike"], [lead.id])["approved"] == 1
    journey.start(db, prog, ret, rl["mike"])
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert (st.status, st.current_touch_number) == ("active", 0)
    assert db.query(CadenceTouchLog).filter_by(lead_id=lead.id, cycle_key="ffr-batch-1").count() == 1
    from app.services.programs import identity
    assert "again" in identity.cadence_text(db, lead, 1)                  # the Return copy, not "first contact"
    # Its emails are 101-104; round 1's 1-4 stay as they were.
    from app.services.programs import email_touches
    start2 = min(r.journey_started_at for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all())
    plan = email_touches.plan(db, prog, now=start2 + timedelta(hours=5), with_skip=False)
    mine = [p for p in plan if p["lead"].id == lead.id]
    assert mine and mine[0]["touch"] == 101
    assert sorted(t.touch_number for t in db.query(ProgramEmailTouch).filter_by(lead_id=lead.id)) == [1, 2, 3, 4]
    _finish_round(rl, lead, 100, start2 + timedelta(days=13))
    rotation.refresh(db, org.id)
    db.refresh(m2)
    assert (m2.status, m2.completion_reason) == ("completed", "no_reply")
    assert sorted(t.touch_number for t in db.query(ProgramEmailTouch).filter_by(lead_id=lead.id)) == \
        [1, 2, 3, 4, 101, 102, 103, 104]
    db.refresh(m1)
    assert m1.status == "completed"                                      # round 1 record unchanged


def test_return_staging_refuses_replies_bookings_optouts_and_shared_numbers(rl):
    db = rl["db"]
    configured(rl)
    org = rl["org"]
    past = datetime.utcnow() - timedelta(days=60)

    def done(lead, reason="no_reply"):
        db.add(CampaignCycleMember(organization_id=org.id, campaign_key=F.CAMPAIGN_KEY, cycle_key="r1-%s" % lead.id[:6],
                                   lead_id=lead.id, phone_e164=rotation.destinations(lead)["phone"],
                                   status="completed" if reason in ("no_reply",) else "stopped",
                                   completion_reason=reason, next_eligible_at=past + timedelta(days=30)))
        db.commit()
    a, b, c, d = (L(db, org, 500 + i) for i in range(4))
    done(a, "replied")
    done(b, "booked")
    done(c)
    c.status = "dnc"
    done(d)
    twin = L(db, org, 600, phone=d.phone)
    db.commit()
    rotation.stage(db, org.id, F.CAMPAIGN_KEY, "ffr-batch-7", [twin])           # its number is in an open round
    res = rotation.stage_return(db, org.id, [a, b, c, d], "ffr-return-2")
    assert res["staged"] == []
    why = {x["lead_id"]: x["reason"] for x in res["refused"]}
    assert why[a.id] == why[b.id] == "no finished, unanswered earlier round"
    assert why[c.id] == "do not contact" and "open round" in why[d.id]


def test_copy_never_states_what_the_permission_form_authorizes(rl):
    import re
    texts = list(F.SMS.values()) + list(F.RETURN_SMS.values())
    texts += [s + b for s, b in F.EMAIL.values()] + [s + b for s, b in F.RETURN_EMAIL.values()]
    for t in texts:
        assert not re.search(r"records who|who (you give|may) |complete the permission|sign the permission", t, re.I)
    assert "explain any permission-to-access paperwork that applies" in F.SMS[1]
    configured(rl)
    fam = rl["db"].query(CampaignFamily).filter_by(organization_id=rl["org"].id, key=F.CAMPAIGN_KEY).one()
    assert "records who" not in (campaigns.email_for(fam, 3) or {}).get("body", "")
    assert campaigns.copy_problems(fam) == []


def test_other_programs_get_no_appointment_messages(rl, db_session):
    other = User(email="sci-adv@example.com", full_name="SCI Advisor", password_hash="x", organization_id=None,
                 is_active=True)
    db_session.add(other)
    lead = Lead(organization_id=rl["org"].id, first_name="X", last_name="Y", phone="12145559999", status="new")
    db_session.add(lead)
    db_session.commit()
    b = BookingLink(lead_id=lead.id, user_id=other.id, status="booked", booked_time=datetime(2026, 12, 1, 10))
    db_session.add(b)
    db_session.commit()
    # Without program rules on the calendar owner's side nothing is scheduled.
    import json
    prog = prog_of(rl)
    prog.booking_policy_json = None
    db_session.commit()
    assert AN.on_booked(db_session, b)["scheduled"] == 0


def test_forecast_uses_the_corrected_formula():
    f = F.forecast("https://app.example.com/book/" + "A" * 22, email_share=0.5)
    row = next(r for r in f["by_volume"] if r["new_families_per_day"] == 15)
    assert (row["texts_per_day"], row["emails_per_day_max"]) == (75, 60)
    assert row["text_segments_per_day"] == 15 * f["segments_per_family_first_round"]
    assert F.segments("a" * 160)["segments"] == 1 and F.segments("a" * 161)["segments"] == 2
    assert F.segments("é" * 10)["encoding"] == "GSM-7" and F.segments("✓")["encoding"] == "UCS-2"



def test_the_rest_counts_from_the_last_attempt_and_never_gets_shorter(rl):
    """A late retry, a failed last attempt or a late 'undelivered' receipt must
    not shorten the 30-day rest - for this family or a shared phone/email."""
    db = rl["db"]
    configured(rl)
    org = rl["org"]
    m1 = db.query(CampaignCycleMember).filter_by(organization_id=org.id, status="staged").first()
    lead = db.query(Lead).filter_by(id=m1.lead_id).one()
    started = datetime.utcnow() - timedelta(days=50)
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all():
        r.journey_started_at, r.approved_to_send_at = started, started
    if db.query(CadenceState).filter_by(lead_id=lead.id).first() is None:
        db.add(CadenceState(lead_id=lead.id, status="active"))
    db.commit()
    delivered = started + timedelta(days=13)
    _finish_round(rl, lead, 0, delivered)
    # The last text attempt after it FAILED (a retry); its receipt says undelivered.
    failed = delivered + timedelta(days=2)
    db.add(Message(lead_id=lead.id, sender_id=rl["mike"].id, body="t5 retry", sent_at=failed,
                   delivery_status="undelivered"))
    db.commit()
    rotation.refresh(db, org.id)
    db.refresh(m1)
    assert m1.status == "completed" and m1.next_eligible_at == failed + timedelta(days=30)
    # A retry logged AFTER the round closed pushes the rest later at every check.
    late = failed + timedelta(days=5)
    st = db.query(CadenceState).filter_by(lead_id=lead.id).first()
    db.add(CadenceTouchLog(cadence_state_id=st.id, lead_id=lead.id, organization_id=org.id, touch_number=5,
                           attempt_seq=2, outcome="failed", attempted_at=late))
    db.commit()
    assert rotation.rest_until(db, m1) == late + timedelta(days=30)
    probe = late + timedelta(days=29)
    assert rotation.stage_return(db, org.id, [lead], "ffr-return-z", now=probe)["refused"][0]["reason"] == "still resting"
    assert any("resting until" in c for c in rotation.conflicts(db, org.id, lead, now=probe))
    # A receipt arriving later can flip delivery state; the stored rest never moves earlier.
    for m in db.query(Message).filter_by(lead_id=lead.id).all():
        m.delivery_status = "undelivered"
    m1.status = "active"
    db.commit()
    rotation.refresh(db, org.id)
    db.refresh(m1)
    assert m1.next_eligible_at >= late + timedelta(days=30)


def test_survey_invitation_names_restland_only_for_office_visits(rl):
    db = rl["db"]
    configured(rl)
    _activate_since(rl)
    for meeting, expect in (("office", " at Restland for your"), ("home", "Mike Simmons for your")):
        start = AN._local(datetime.utcnow() - timedelta(days=3)).replace(hour=13, minute=0, second=0, microsecond=0)
        b = _booking(rl, rl["leads"][23 if meeting == "office" else 24], start, meeting=meeting,
                     created=datetime.utcnow() - timedelta(hours=1))
        OPS.attendance(db, prog_of(rl), b, "completed", rl["staff"], now=datetime.utcnow())
        row = _rows(rl, b, purpose="survey", channel="sms")[0]
        policy, lead = AN._policy_for_booking(db, b)
        body = AN.compose(db, row, b, lead, policy)["body"]
        assert expect in body
