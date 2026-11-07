"""RESTLAND BOOKING RULES, 14-DAY CAMPAIGN AND 30-DAY REST (Issue #507, Nov 7).
Synthetic data only; nothing reaches a provider or a family."""
import json
from datetime import date, datetime, timedelta

import pytest

from app.models.models import BookingLink, CadenceState, Lead, Reply, User
from app.models.program_models import CampaignFamily, OutreachProgram, ProgramEmailTouch, ProgramResponse, \
    ProgramSourceRecord
from app.models.reset_models import CampaignAudienceMember, CampaignCycleMember
from app.services.auth_service import hash_password
from app.services.programs import booking_policy as BP
from app.services.programs import campaigns
from app.services.programs import restland_ffr as F
from app.services.programs import rotation
from app.services.programs import single_location_cutover as C
from app.services.programs import workspace_configure as WC
from app.services.programs import workspace_reset as WR

NO_BUSY = lambda db, adv, day: ([], None)           # noqa: E731 - a connected calendar with nothing on it


def L(db, org, i, **kw):
    d = dict(organization_id=org.id, first_name="Fam%d" % i, last_name="Synth", phone="1214555%04d" % i,
             email="f%d@example.com" % i, status="new", tier="pre_need")
    d.update(kw)
    l = Lead(**d)
    db.add(l)
    db.flush()
    return l


@pytest.fixture()
def rl(db_session, sample_org, monkeypatch):
    db, org = db_session, sample_org
    # Every test runs with "nothing on the calendar" unless it says otherwise.
    import app.routers.calendar_router as CR
    monkeypatch.setattr(CR, "_booking_window_busy", NO_BUSY)
    mike = User(email="booking.account@example.com", full_name="Jerome Simmons", password_hash=hash_password("x" * 12),
                organization_id=org.id, role="org_admin", is_active=True)
    staff = User(email="staff@example.com", full_name="Pat Staff", password_hash=hash_password("x" * 12),
                 organization_id=org.id, role="advisor", is_active=True)
    db.add_all([mike, staff])
    db.flush()
    leads = [L(db, org, i) for i in range(40)]
    leads.append(L(db, org, 900, email=None))                       # texts only
    db.commit()
    C.apply(db, org, hero_title="Restland")
    WR.apply(db, org, "reset-1")
    return {"db": db, "org": org, "mike": mike, "staff": staff, "leads": leads}


def configured(rl):
    return WC.configure(rl["db"], rl["org"], "cfg-1", probe=False, booking_user_id=rl["mike"].id)


def prog_of(rl):
    return rl["db"].query(OutreachProgram).filter_by(organization_id=rl["org"].id).one()


# ── 1. booking account, rules, nothing switched on ──────────────────────────

def test_configure_sets_booking_account_and_rules_and_switches_nothing_on(rl):
    db = rl["db"]
    before_approval = prog_of(rl).require_send_approval
    r = configured(rl)
    assert r["status"] == "applied"
    prog = prog_of(rl)
    db.refresh(rl["mike"])
    assert prog.booking_user_id == rl["mike"].id
    assert rl["mike"].full_name == "Mike Simmons" and rl["mike"].email == "booking.account@example.com"   # email untouched
    pol = BP.for_program(prog)
    assert pol["days"] == "0123456" and pol["duration"] == 45 and pol["gap_after"] == 45
    assert pol["starts"] == [(10, 0), (11, 30), (13, 0), (14, 30), (16, 0)]
    # Nothing on: campaigns off, AI off, no approvals, approval mode as it was, no texts switch.
    assert db.query(CampaignFamily).filter_by(organization_id=rl["org"].id, is_active=True).count() == 0
    assert (prog.ai_setter_mode or "off") == "off" and prog.require_send_approval == before_approval
    assert db.query(ProgramSourceRecord).filter(ProgramSourceRecord.approved_to_send_at.isnot(None)).count() == 0
    from app.models.program_models import WorkspaceSwitch
    assert db.query(WorkspaceSwitch).filter_by(organization_id=rl["org"].id).count() == 0
    # Idempotent per key.
    assert configured(rl)["already"] is True


def test_configure_never_uses_an_account_from_another_workspace(rl, db_session):
    outsider = User(email="outsider@example.com", full_name="Outsider", password_hash="x", organization_id=None,
                    is_active=True)
    db_session.add(outsider)
    db_session.commit()
    r = WC.configure(rl["db"], rl["org"], "cfg-x", probe=False, booking_user_id=outsider.id)
    assert prog_of(rl).booking_user_id is None and r["summary"]["notes"]


def test_configure_refuses_sci(rl, monkeypatch):
    from app.services import sms_programs
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db, oid: True)
    with pytest.raises(WR.ResetRefused):
        configured(rl)


# ── 2. the slot engine ───────────────────────────────────────────────────────

def _slots(rl, day, now=None):
    pol = BP.for_program(prog_of(rl))
    now = now or datetime(day.year, day.month, day.day, 6, 0) - timedelta(days=1)
    return BP.day_slots(rl["db"], rl["mike"], day, pol, now_local=now, busy_reader=NO_BUSY)


def test_all_seven_days_exact_five_starts_and_four_pm_ends_at_four_forty_five(rl):
    configured(rl)
    base = date(2026, 11, 9)                      # a Monday
    for k in range(7):
        res = _slots(rl, base + timedelta(days=k), now=datetime(2026, 11, 8, 8, 0))
        times = [s[11:16] for s in res["slots"]]
        assert times == ["10:00", "11:30", "13:00", "14:30", "16:00"], (k, res)
    assert res["duration_minutes"] == 45 and res["gap_minutes"] == 45
    # Not 45-minute start intervals, not 30-minute appointments.
    assert "10:45" not in times and "10:30" not in times


def test_daylight_saving_days_keep_wall_clock_starts(rl):
    configured(rl)
    for d in (date(2027, 3, 14), date(2026, 11, 1)):       # spring forward / fall back Sundays
        res = _slots(rl, d, now=datetime(d.year, d.month, d.day, 0, 0) - timedelta(days=1))
        assert [s[11:16] for s in res["slots"]] == ["10:00", "11:30", "13:00", "14:30", "16:00"]


def test_existing_booking_and_gap_block_overlapping_starts(rl):
    db = rl["db"]
    configured(rl)
    day = date(2026, 11, 14)                                          # a Saturday
    db.add(BookingLink(lead_id=rl["leads"][0].id, user_id=rl["mike"].id, status="booked",
                       booked_time=datetime(2026, 11, 14, 11, 30), appt_duration=45))
    db.add(BookingLink(lead_id=rl["leads"][1].id, user_id=rl["mike"].id, status="booked",   # an older 60-min visit
                       booked_time=datetime(2026, 11, 14, 15, 0), appt_duration=None))
    db.commit()
    times = [s[11:16] for s in _slots(rl, day, now=datetime(2026, 11, 13, 8))["slots"]]
    # 11:30 taken; 10:00 holds 10:00-11:30 and still fits; the 15:00 legacy visit (60 + 45 gap)
    # blocks 14:30 and 16:00.
    assert times == ["10:00", "13:00"]


def test_calendar_conflict_and_unreadable_calendar(rl):
    configured(rl)
    pol = BP.for_program(prog_of(rl))
    day = date(2026, 11, 15)
    busy = lambda db, a, d: ([(datetime(2026, 11, 15, 12, 0), datetime(2026, 11, 15, 12, 30))], None)   # noqa
    res = BP.day_slots(rl["db"], rl["mike"], day, pol, now_local=datetime(2026, 11, 14, 8), busy_reader=busy)
    assert [s[11:16] for s in res["slots"]] == ["10:00", "13:00", "14:30", "16:00"]       # 11:30 + gap hits 12:00
    down = lambda db, a, d: ([], "unreadable")                                           # noqa
    res = BP.day_slots(rl["db"], rl["mike"], day, pol, now_local=datetime(2026, 11, 14, 8), busy_reader=down)
    assert res["slots"] == [] and res["calendar_error"] == "unreadable"


def test_same_day_notice_and_horizon(rl):
    configured(rl)
    day = date(2026, 11, 14)
    times = [s[11:16] for s in _slots(rl, day, now=datetime(2026, 11, 14, 11, 0))["slots"]]
    assert times == ["13:00", "14:30", "16:00"]                       # 2-hour notice
    assert _slots(rl, date(2026, 12, 30), now=datetime(2026, 11, 14, 8))["slots"] == []


def test_slot_engine_routes_restland_and_leaves_others_alone(rl, db_session):
    from app.routers.calendar_router import compute_day_slots
    configured(rl)
    sat = date.today() + timedelta(days=(5 - date.today().weekday()) % 7 or 7)
    res = compute_day_slots(rl["db"], rl["mike"], sat, program_visit=True)
    assert res.get("duration_minutes") == 45 and "Weekend" not in (res.get("reason") or "")
    other = User(email="other@example.com", full_name="Other", password_hash="x", organization_id=None, is_active=True)
    db_session.add(other)
    db_session.commit()
    assert "Weekend" in (compute_day_slots(rl["db"], other, sat, program_visit=True).get("reason") or "")


# ── 3. office / home, address capture, shared availability, reassignment ────

def test_meeting_choice_requires_complete_home_address(rl):
    pol = BP.RESTLAND_POLICY
    assert BP.meeting_choice(pol, "office", None)["address"] == BP.RESTLAND_ADDRESS
    with pytest.raises(ValueError):
        BP.meeting_choice(pol, "home", "my house")
    c = BP.meeting_choice(pol, "home", "4521 Elm Street, Dallas, TX 75214")
    assert c["key"] == "home" and c["address"].startswith("4521 Elm")
    with pytest.raises(ValueError):
        BP.meeting_choice(pol, None, None)                                      # must choose


def _book(rl, lead, slot, **kw):
    from app.services.programs import appointments as A
    return A.book(rl["db"], prog_of(rl), lead, slot, A.VISIT, family_text=False, **kw)


def _next(dow):
    d = date.today() + timedelta(days=3)
    while d.weekday() != dow:
        d += timedelta(days=1)
    return d


def test_home_and_office_share_one_calendar_and_keep_the_place(rl):
    db = rl["db"]
    configured(rl)
    sun = _next(6)
    r = _book(rl, rl["leads"][2], datetime(sun.year, sun.month, sun.day, 10, 0), meeting_key="home",
              visit_address="4521 Elm Street, Dallas, TX 75214")
    assert r["ok"] is True, r
    b = db.query(BookingLink).filter_by(id=r["booking_id"]).one()
    assert (b.meeting_type, b.appt_duration) == ("home", 45) and b.visit_address.startswith("4521 Elm")
    assert b.booked_time == datetime(sun.year, sun.month, sun.day, 10, 0)
    # The same time is gone for an office visit, and an off-grid time is refused.
    r2 = _book(rl, rl["leads"][3], datetime(sun.year, sun.month, sun.day, 10, 0), meeting_key="office")
    assert r2["ok"] is False
    r3 = _book(rl, rl["leads"][4], datetime(sun.year, sun.month, sun.day, 10, 45), meeting_key="office")
    assert r3["ok"] is False
    r4 = _book(rl, rl["leads"][5], datetime(sun.year, sun.month, sun.day, 11, 30), meeting_key="office")
    assert r4["ok"] is True
    assert db.query(BookingLink).filter_by(id=r4["booking_id"]).one().visit_address == BP.RESTLAND_ADDRESS
    # A home visit with no address is refused before anything is booked.
    r5 = _book(rl, rl["leads"][6], datetime(sun.year, sun.month, sun.day, 13, 0), meeting_key="home")
    assert r5["status"] == "needs_place"


def test_reassignment_checks_the_new_person_and_is_audited(rl):
    db = rl["db"]
    configured(rl)
    sat = _next(5)
    r = _book(rl, rl["leads"][7], datetime(sat.year, sat.month, sat.day, 13, 0), meeting_key="office")
    b = db.query(BookingLink).filter_by(id=r["booking_id"]).one()
    # Pat already has a visit then: refused, nothing changes.
    db.add(BookingLink(lead_id=rl["leads"][8].id, user_id=rl["staff"].id, status="booked",
                       booked_time=datetime(sat.year, sat.month, sat.day, 13, 0), appt_duration=45))
    db.commit()
    with pytest.raises(ValueError):
        BP.reassign(db, prog_of(rl), b, rl["staff"], rl["mike"], busy_reader=NO_BUSY)
    db.refresh(b)
    assert b.user_id == rl["mike"].id
    db.query(BookingLink).filter(BookingLink.user_id == rl["staff"].id).delete()
    db.commit()
    out = BP.reassign(db, prog_of(rl), b, rl["staff"], rl["mike"], busy_reader=NO_BUSY)
    db.refresh(b)
    assert out["ok"] and b.user_id == rl["staff"].id and b.meeting_type == "office"
    from app.models.models import AuditLogEntry
    assert db.query(AuditLogEntry).filter_by(action="program.appointment_reassign", target_id=b.id).count() == 1


# ── 4. the nine-touch sequence ──────────────────────────────────────────────

def test_five_texts_four_emails_on_the_approved_days(rl):
    db = rl["db"]
    configured(rl)
    assert [(n, d) for n, d, _h in campaigns.sms_steps(db, rl["org"].id)] == [(1, 0), (2, 2), (3, 5), (4, 9), (5, 13)]
    assert all(9 <= h < 18 for _n, _d, h in campaigns.sms_steps(db, rl["org"].id))
    fam = db.query(CampaignFamily).filter_by(organization_id=rl["org"].id, key=F.CAMPAIGN_KEY).one()
    assert campaigns.email_steps(fam.key) == [(1, 0), (2, 3), (3, 7), (4, 11)]
    assert campaigns.activation_blocker(fam, db) is None and fam.is_active is False
    assert campaigns.copy_problems(fam) == []
    assert campaigns.email_for(fam, 5) is None                                   # the old 5th draft email is cleared
    assert "45-minute" in campaigns.sms_for(fam, 1) and "home" in campaigns.sms_for(fam, 3)
    assert "weekday or a weekend" in campaigns.sms_for(fam, 4)
    # Other campaigns keep the runner's usual gap.
    assert campaigns.email_steps("central_alabama_ms")[1][1] == 4


def test_rendered_texts_fit_and_name_mike_and_restland(rl):
    fields = {"first_name": "Margaret", "primary_contact_name": "Mike Simmons",
              "location_name": "Restland Cemetery and Funeral Home",
              "booking_line": "Pick a time that works for you: https://restland.evosyspro.live/k7q2x"}
    for copy in (F.SMS, F.RETURN_SMS):
        for n, t in copy.items():
            out = t.format(**fields)
            assert len(out) <= 320, (n, len(out))
            assert "Mike Simmons" in out and "STOP" in out
    for n, (subj, body) in F.EMAIL.items():
        out = body.format(**dict(fields, booking_line=fields["booking_line"] + "\n\n"))
        assert "Restland Cemetery and Funeral Home" in out and "reply" in out.lower()
        assert len(out.split()) <= 170 and "{" not in out
    assert "I wrote" in F.RETURN_SMS[1] and "I wrote" in F.RETURN_EMAIL[1][1]     # never "first contact"


def test_email_schedule_due_days_and_gaps(rl):
    from app.services.programs.email_touches import _due_touch
    sched = campaigns.email_steps(F.CAMPAIGN_KEY)
    start = datetime(2026, 11, 20, 15, 0)

    class T:
        def __init__(self, at, status="sent"):
            self.attempted_at, self.status, self.next_attempt_at = at, status, None
    assert _due_touch({}, start, sched, start) == 1
    t = {1: T(start)}
    assert _due_touch(t, start + timedelta(days=2, hours=23), sched, start) is None
    assert _due_touch(t, start + timedelta(days=3), sched, start) == 2
    # Email 1 went late (day 3): email 2 still waits its own 3-day gap, never sooner.
    late = {1: T(start + timedelta(days=3))}
    assert _due_touch(late, start + timedelta(days=4), sched, start) is None
    assert _due_touch(late, start + timedelta(days=6), sched, start) == 2
    full = {n: T(start + timedelta(days=d)) for n, d in sched}
    assert _due_touch(full, start + timedelta(days=30), sched, start) is None     # never a 5th


def test_missing_email_never_becomes_a_text(rl):
    db = rl["db"]
    configured(rl)
    no_email = rl["leads"][-1]
    assert rotation._emails_done(db, rl["org"].id, no_email, F.CAMPAIGN_KEY) is True
    assert len(campaigns.sms_steps(db, rl["org"].id)) == 5                        # still five texts


# ── 5. replies, bookings and opt-outs come first ────────────────────────────

def _assign(db, lead, key=F.CAMPAIGN_KEY):
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all():
        r.campaign_family = key
    db.commit()


def test_auto_reply_does_not_stop_restland_but_a_real_reply_does(rl):
    db = rl["db"]
    lead = rl["leads"][10]
    _assign(db, lead)
    rep = Reply(lead_id=lead.id, body="I'm driving with Do Not Disturb on", source="sms", received_at=datetime.utcnow())
    db.add(rep)
    db.flush()
    db.add(ProgramResponse(organization_id=rl["org"].id, lead_id=lead.id, reply_id=rep.id, channel="sms",
                           response_class="auto_reply"))
    db.commit()
    assert rotation.reply_counts_for_cadence(db, lead) is False
    assert rotation.genuine_repliers(db, rl["org"].id, [lead.id]) == set()
    rep2 = Reply(lead_id=lead.id, body="Saturday works", source="sms", received_at=datetime.utcnow())
    db.add(rep2)
    db.flush()
    db.add(ProgramResponse(organization_id=rl["org"].id, lead_id=lead.id, reply_id=rep2.id, channel="sms",
                           response_class="active"))
    db.commit()
    assert rotation.reply_counts_for_cadence(db, lead) is True
    assert rotation.genuine_repliers(db, rl["org"].id, [lead.id]) == {lead.id}
    # Any other campaign: any reply stops it (unchanged).
    other = rl["leads"][11]
    _assign(db, other, "existing_family_follow_up")
    db.add(Reply(lead_id=other.id, body="I'm driving", source="sms", received_at=datetime.utcnow()))
    db.commit()
    assert rotation.reply_counts_for_cadence(db, other) is True


def _started(rl, lead, cycle="ffr-batch-1", started=None):
    db = rl["db"]
    started = started or datetime.utcnow() - timedelta(days=20)
    m = db.query(CampaignCycleMember).filter_by(lead_id=lead.id).first()
    if m is None:
        m = rotation.stage(db, rl["org"].id, F.CAMPAIGN_KEY, cycle, [lead])[0]
    for r in db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).all():
        r.journey_started_at = started
        r.approved_to_send_at = started
    db.commit()
    return m, started


def test_round_outcomes_reply_booking_optout_and_finished(rl):
    db = rl["db"]
    configured(rl)
    a, b, c, d = (rl["leads"][i] for i in (12, 13, 14, 15))
    for x in (a, b, c, d):
        _started(rl, x)
    db.add(ProgramResponse(organization_id=rl["org"].id, lead_id=a.id, channel="sms", response_class="active",
                           received_at=datetime.utcnow()))
    db.add(BookingLink(lead_id=b.id, user_id=rl["mike"].id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=2)))
    c.status = "dnc"
    # d: every text and email went; the last delivered text was 6 days ago.
    from app.models.models import Message
    last = datetime.utcnow() - timedelta(days=6)
    db.add(Message(lead_id=d.id, sender_id=rl["mike"].id, body="t5", sent_at=last, delivery_status="delivered"))
    for n, _d in campaigns.email_steps(F.CAMPAIGN_KEY):
        db.add(ProgramEmailTouch(organization_id=rl["org"].id, lead_id=d.id, touch_number=n, status="sent",
                                 attempted_at=last - timedelta(days=1)))
    db.commit()
    rotation.refresh(db, rl["org"].id)
    got = {m.lead_id: (m.status, m.completion_reason) for m in db.query(CampaignCycleMember).all()}
    assert got[a.id] == ("stopped", "replied") and got[b.id] == ("stopped", "booked")
    assert got[c.id] == ("stopped", "opted_out") and got[d.id] == ("completed", "no_reply")
    md = db.query(CampaignCycleMember).filter_by(lead_id=d.id).one()
    assert md.last_outreach_at == last and md.next_eligible_at == last + timedelta(days=30)   # from the real last touch


def test_undeliverable_final_touch_still_ends_with_a_basis(rl):
    db = rl["db"]
    configured(rl)
    lead = rl["leads"][16]
    _, started = _started(rl, lead)
    from app.models.models import Message
    t = datetime.utcnow() - timedelta(days=5)
    db.add(Message(lead_id=lead.id, sender_id=rl["mike"].id, body="t", sent_at=t, delivery_status="undelivered"))
    for n, _d in campaigns.email_steps(F.CAMPAIGN_KEY):
        db.add(ProgramEmailTouch(organization_id=rl["org"].id, lead_id=lead.id, touch_number=n, status="failed",
                                 attempted_at=t))
    db.commit()
    rotation.refresh(db, rl["org"].id)
    m = db.query(CampaignCycleMember).filter_by(lead_id=lead.id).one()
    assert (m.status, m.completion_reason) == ("completed", "undeliverable")
    assert m.next_eligible_at == t + timedelta(days=30)


def test_rest_and_open_rounds_are_enforced_per_destination(rl):
    db = rl["db"]
    configured(rl)
    a = rl["leads"][17]
    twin = L(db, rl["org"], 777, phone=a.phone, first_name="Twin")          # another record, same number
    db.commit()
    m = rotation.stage(db, rl["org"].id, F.CAMPAIGN_KEY, "ffr-batch-1", [a])[0]
    assert rotation.conflicts(db, rl["org"].id, twin)                         # open round on that phone
    m.status, m.next_eligible_at = "completed", datetime.utcnow() + timedelta(days=12)
    db.commit()
    assert "resting until" in rotation.conflicts(db, rl["org"].id, twin)[0]
    m.next_eligible_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    assert rotation.conflicts(db, rl["org"].id, twin) == []                   # rest over: may be REVIEWED
    # ... but approval still needs staging, and a returning family can't be sent a second round yet.
    _assign(db, a)
    assert rotation.approval_refusal(db, rl["org"].id, twin, F.CAMPAIGN_KEY).startswith("Not staged")
    rotation.stage(db, rl["org"].id, F.RETURN_KEY, "ffr-return-1", [a])
    assert "Returning family" in rotation.approval_refusal(db, rl["org"].id, a, F.RETURN_KEY)


def test_approval_refuses_resting_destinations(rl):
    db = rl["db"]
    configured(rl)
    staged = db.query(CampaignCycleMember).filter_by(organization_id=rl["org"].id, status="staged").first()
    lead = db.query(Lead).filter_by(id=staged.lead_id).one()
    assert rotation.approval_refusal(db, rl["org"].id, lead, F.CAMPAIGN_KEY) is None   # staged, no conflicts
    other = L(db, rl["org"], 778, phone=lead.phone)
    db.add(CampaignCycleMember(organization_id=rl["org"].id, campaign_key=F.CAMPAIGN_KEY, cycle_key="old",
                               lead_id=other.id, phone_e164=WR.e164(lead.phone), status="completed",
                               next_eligible_at=datetime.utcnow() + timedelta(days=9)))
    db.commit()
    assert "resting" in rotation.approval_refusal(db, rl["org"].id, lead, F.CAMPAIGN_KEY)


# ── 6. Batch 1 revalidation, groups, rollback ───────────────────────────────

def test_batch_1_revalidated_without_dispatch(rl):
    db = rl["db"]
    staged = [m.lead_id for m in db.query(CampaignAudienceMember).filter_by(is_staged=True).all()]
    assert len(staged) == 25
    gone = staged[0]
    db.add(Reply(lead_id=gone, body="hello", source="sms", received_at=datetime.utcnow()))   # replied since
    db.commit()
    r = configured(rl)
    b1 = r["summary"]["batch_1"]
    assert gone in b1["unstaged"] and len(b1["kept"]) + len(b1["added"]) == 25
    now_staged = {m.lead_id for m in db.query(CampaignAudienceMember).filter_by(is_staged=True).all()}
    assert len(now_staged) == 25 and gone not in now_staged
    rounds = db.query(CampaignCycleMember).filter_by(cycle_key=WC.CYCLE_KEY, status="staged").count()
    assert rounds == 25
    assert {r.campaign_family for r in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.lead_id.in_(now_staged))} \
        == {F.CAMPAIGN_KEY}
    assert db.query(CadenceState).filter(CadenceState.status == "active").count() == 0
    assert db.query(ProgramSourceRecord).filter(ProgramSourceRecord.approved_to_send_at.isnot(None)).count() == 0
    g = rotation.groups(db, rl["org"].id)
    assert g["staged"] == 25 and g["active"] == 0


def test_rollback_restores_everything_the_run_changed(rl):
    db = rl["db"]
    tpl_before = [(n, d) for n, d, _h in campaigns.sms_steps(db, rl["org"].id)]
    r = configured(rl)
    out = WC.rollback(db, rl["org"], r["run_id"])
    assert out["status"] == "rolled_back"
    prog = prog_of(rl)
    db.refresh(rl["mike"])
    assert prog.booking_user_id is None and prog.booking_policy_json is None
    assert rl["mike"].full_name == "Jerome Simmons"
    assert [(n, d) for n, d, _h in campaigns.sms_steps(db, rl["org"].id)] == tpl_before
    assert db.query(CampaignFamily).filter_by(key=F.RETURN_KEY).count() == 0
    assert db.query(CampaignCycleMember).count() == 0


def test_rollback_refused_while_a_campaign_is_on(rl):
    db = rl["db"]
    r = configured(rl)
    fam = db.query(CampaignFamily).filter_by(organization_id=rl["org"].id, key=F.CAMPAIGN_KEY).one()
    fam.is_active = True
    db.commit()
    with pytest.raises(WR.ResetRefused):
        WC.rollback(db, rl["org"], r["run_id"])


def test_booking_page_context_and_weekend_slots_route(rl, client):
    db = rl["db"]
    configured(rl)
    from app.services.sms_service import get_or_create_booking_link
    lead = rl["leads"][20]
    link = get_or_create_booking_link(db, lead, rl["mike"])
    db.commit()
    ctx = client.get("/calendar/booking/%s" % link.token).json()
    assert ctx.get("program_location") is not None, ctx                # location resolves for program leads
    if True:
        assert ctx["appt_duration"] == 45 and ctx["booking_days"] == [0, 1, 2, 3, 4, 5, 6]
        assert {o["key"] for o in ctx["meeting_options"]} == {"office", "home"}
        assert ctx["program_location"]["contact_name"] == "Mike Simmons"
    sat = _next(5)
    res = client.get("/calendar/slots", params={"advisor_id": rl["mike"].id, "date": sat.isoformat(),
                                                 "token": link.token})
    assert res.status_code == 200
    assert "Weekend" not in (res.json().get("reason") or "")
