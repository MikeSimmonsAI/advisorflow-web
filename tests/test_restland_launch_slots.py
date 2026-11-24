"""RESTLAND LAUNCH (Issue #507, Nov 15): the text slot schedule, the launch
routine, the 855 sender's content rules, the day-1 email gap, the shared
calendar account and the verified two-time offer. Synthetic data only."""
import json
from datetime import date, datetime, timedelta

import pytest
from zoneinfo import ZoneInfo

from app.models.models import CadenceState, CadenceTouchLog, Lead, Message, User
from app.models.program_models import ProgramSourceRecord, WorkspaceSwitch
from app.models.reset_models import OutreachTextSlot
from app.services.programs import outreach_slots as OS
from app.services.programs import restland_launch as RL
from tests.test_restland_booking_cadence import configured, prog_of, rl  # noqa: F401

CT = ZoneInfo("America/Chicago")
CFG = OS.config_from_policy({"outreach": RL.OUTREACH})


def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=CT).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def local(t):
    return t.replace(tzinfo=ZoneInfo("UTC")).astimezone(CT).strftime("%H:%M")


# ── the grid ────────────────────────────────────────────────────────────────

def test_the_grid_is_830_every_8_minutes_with_followups_2_minutes_after():
    new = OS.day_slots(date(2026, 11, 16), CFG, "new")
    fol = OS.day_slots(date(2026, 11, 16), CFG, "followup")
    assert [local(t) for t in new[:3]] == ["08:30", "08:38", "08:46"]
    assert [local(t) for t in fol[:3]] == ["08:32", "08:40", "08:48"]
    # Owner, Nov 16: open until 7 PM Central - nothing at or after 7 PM.
    assert local(new[-1]) == "18:54" and local(fol[-1]) == "18:56"
    assert len(new) == 79 and len(fol) == 79
    merged = sorted([(t, "n") for t in new[:2]] + [(t, "f") for t in fol[:2]])
    assert [(local(t), k) for t, k in merged] == [("08:30", "n"), ("08:32", "f"), ("08:38", "n"), ("08:40", "f")]


def test_grid_is_daylight_saving_safe():
    # Mar 8 2026 is the spring-forward day in Central time.
    assert local(OS.day_slots(date(2026, 3, 9), CFG, "new")[0]) == "08:30"


def test_a_program_without_the_outreach_block_is_untouched():
    assert OS.config_from_policy({"starts": ["10:00"]}) is None
    assert OS.config_from_policy("not json") is None


# ── the launch ──────────────────────────────────────────────────────────────

def _launch(rl, at="2026-11-16T08:30", now=None):
    configured(rl)
    return RL.launch(rl["db"], rl["org"], "launch-1", booking_user_id=rl["mike"].id, calendar_user_id=None,
                     start_local=at, now=now or utc(2026, 11, 15, 17, 0))


def test_launch_stages_approves_and_starts_every_ready_family_on_the_grid(rl):
    db = rl["db"]
    r = _launch(rl)
    s = r["summary"]
    ready = s["audience"]["ready"]
    assert ready > 25                                   # the pilot's 25 is no longer the boundary
    assert s["approved"] == s["journey"]["started"] == s["readback"]["journeys_started"]
    texting = s["journey"]["texting"]
    states = (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
              .filter(Lead.organization_id == rl["org"].id, CadenceState.status == "active")
              .order_by(CadenceState.next_touch_due_at).all())
    assert len(states) == texting
    times = [local(st.next_touch_due_at) for st in states[:4]]
    assert times == ["08:30", "08:38", "08:46", "08:54"]
    # every texting family holds exactly its own new-family slot; no duplicates
    slots = db.query(OutreachTextSlot).filter_by(organization_id=rl["org"].id, kind="new").all()
    assert len(slots) == texting == len({x.slot_at for x in slots})
    # the 25 already staged are not duplicated in the round
    from app.models.reset_models import CampaignCycleMember
    from sqlalchemy import func
    dup = (db.query(CampaignCycleMember.lead_id, func.count()).filter_by(organization_id=rl["org"].id)
           .group_by(CampaignCycleMember.lead_id).having(func.count() > 1).all())
    assert dup == []
    rb = s["readback"]
    assert rb["texts_switch_on"] is True and rb["daily_batch_count"] == 0
    assert rb["campaigns_on"]["restland_family_file_review"] is True
    assert rb["campaigns_on"]["restland_family_file_review_return"] is False
    assert rb["notifications"]["enabled"] is True and rb["notifications"]["survey_enabled"] is True
    assert rb["outreach_slots"]["open"] == "08:30"


def test_launch_is_idempotent_per_run_key(rl):
    db = rl["db"]
    _launch(rl)
    n = db.query(OutreachTextSlot).count()
    again = RL.launch(db, rl["org"], "launch-1", booking_user_id=rl["mike"].id, calendar_user_id=None,
                      start_local="2026-11-16T08:30")
    assert again["already"] is True and db.query(OutreachTextSlot).count() == n


def test_new_copy_is_saved_and_no_longer_a_draft(rl):
    db = rl["db"]
    _launch(rl)
    from app.models.program_models import CampaignFamily
    fam = db.query(CampaignFamily).filter_by(organization_id=rl["org"].id, key="restland_family_file_review").one()
    assert "few things that must be taken care of with every family" in fam.sms_template   # owner, Nov 17
    assert "Personal Planning Guide" in fam.sms_template
    d = json.loads(fam.touch_copy_json)
    assert d["draft"] is False and "{tomorrow_offer}" in d["sms"]["2"]          # owner, Nov 17


# ── the dispatcher ──────────────────────────────────────────────────────────

@pytest.fixture()
def sender(monkeypatch):
    from app.services import cadence_service, contact_hours, entitlements
    import app.services.sms_service as _sms
    sent = []

    class _M:
        id = "m"
        twilio_sid = "SM"
        error_code = None
        error_message = None

    def fake(*a, **k):
        sent.append({"lead": k["lead"].id, "body": k["template"]})
        return _M()
    monkeypatch.setattr(_sms, "send_sms", fake)
    monkeypatch.setattr(cadence_service, "org_sending_enabled", lambda db, oid: True)
    monkeypatch.setattr(entitlements, "org_has_feature", lambda org, f: True)
    monkeypatch.setattr(contact_hours, "check", lambda lead, now=None: {"permitted": True})
    import app.services.programs.identity as I
    monkeypatch.setattr(I, "time_offer_for", lambda db, lead, channel="sms": "What day this week works?")
    OS.clear_cache()
    return sent


def _run(db, at):
    from app.services import cadence_service
    OS.clear_cache()
    return cadence_service.run_due_cadences(db, now=at)


def test_texts_leave_in_their_own_slots_and_never_early(rl, sender):
    db = rl["db"]
    _launch(rl)
    _run(db, utc(2026, 11, 16, 8, 29))
    assert sender == []                                  # nothing before 8:30
    _run(db, utc(2026, 11, 16, 8, 30))
    assert len(sender) == 1
    _run(db, utc(2026, 11, 16, 8, 33))
    assert len(sender) == 1                              # 8:38 family not yet
    _run(db, utc(2026, 11, 16, 8, 38))
    assert len(sender) == 2


def test_followup_takes_the_slot_two_minutes_after_and_overflow_queues(rl, sender):
    db = rl["db"]
    _launch(rl)
    _run(db, utc(2026, 11, 16, 8, 30))
    _run(db, utc(2026, 11, 16, 8, 38))
    states = (db.query(CadenceState).filter(CadenceState.current_touch_number == 1)
              .order_by(CadenceState.cadence_started_at).all())
    assert len(states) == 2
    # text 2 is due day 3 at the start time; its slot is 2 minutes later
    assert states[0].next_touch_due_at == utc(2026, 11, 18, 8, 32)
    assert states[1].next_touch_due_at == utc(2026, 11, 18, 8, 40)
    # Two follow-ups wanting the same moment: the second waits for the next free follow-up slot.
    a, b = states
    OS.place_followup(db, rl["org"].id, b, 2, utc(2026, 11, 18, 8, 32), CFG)
    db.commit()
    assert b.next_touch_due_at == utc(2026, 11, 18, 8, 40)    # its own slot was free again
    from app.services.programs.outreach_slots import place_followup
    c = db.query(CadenceState).filter(CadenceState.current_touch_number == 0).first()
    place_followup(db, rl["org"].id, c, 9, utc(2026, 11, 18, 8, 32), CFG)
    db.commit()
    assert c.next_touch_due_at == utc(2026, 11, 18, 8, 48)    # 8:32 and 8:40 are taken
    # never a new-family slot
    assert db.query(OutreachTextSlot).filter_by(cadence_state_id=c.id, touch_number=9).one().kind == "followup"


def test_followups_still_go_when_no_new_family_is_queued(rl, sender):
    db = rl["db"]
    _launch(rl)
    _run(db, utc(2026, 11, 16, 8, 30))
    first = db.query(CadenceState).filter(CadenceState.current_touch_number == 1).one()
    # every later new family is stopped: the follow-up queue alone remains
    for st in db.query(CadenceState).filter(CadenceState.current_touch_number == 0).all():
        from app.services.cadence_service import stop_cadence_for_lead
        stop_cadence_for_lead(db, st.lead_id, "cancelled")
    _run(db, utc(2026, 11, 18, 8, 32))
    assert len(sender) == 2 and sender[-1]["lead"] == first.lead_id


def test_a_missed_slot_is_never_caught_up_in_a_burst(rl, sender):
    db = rl["db"]
    _launch(rl)
    # The server was down from 8:25 to 9:14: six new-family slots (8:30-9:10) passed.
    _run(db, utc(2026, 11, 16, 9, 14))
    assert len(sender) == 0
    queued = (db.query(OutreachTextSlot).filter(OutreachTextSlot.organization_id == rl["org"].id,
                                                OutreachTextSlot.status == "reserved",
                                                OutreachTextSlot.kind == "followup").order_by(OutreachTextSlot.slot_at)
              .all())
    times = [local(q.slot_at) for q in queued[:5]]
    assert times == ["09:20", "09:28", "09:36", "09:44", "09:52"]   # one per follow-up slot, from now
    assert len(queued) == 6
    _run(db, utc(2026, 11, 16, 9, 18))       # the 9:18 new family goes on time
    assert len(sender) == 1
    _run(db, utc(2026, 11, 16, 9, 20))
    assert len(sender) == 2


def test_a_failed_send_retry_never_doubles_or_bursts(rl, sender, monkeypatch):
    db = rl["db"]
    _launch(rl)
    import app.services.sms_service as _sms

    def boom(*a, **k):
        raise RuntimeError("provider 500")
    monkeypatch.setattr(_sms, "send_sms", boom)
    _run(db, utc(2026, 11, 16, 8, 30))
    st = db.query(CadenceState).order_by(CadenceState.cadence_started_at).first()
    assert st.current_touch_number == 0
    monkeypatch.setattr(_sms, "send_sms", lambda *a, **k: sender.append(k) or type("M", (), {"id": "x", "twilio_sid": "s", "error_code": None, "error_message": None})())
    _run(db, utc(2026, 11, 16, 9, 30))               # backoff passed: queued into a follow-up slot first
    n_after_requeue = len(sender)
    slot = OS.reservation(db, st.id, 1)
    assert slot is not None and slot.kind == "followup"
    _run(db, slot.slot_at)
    assert sum(1 for s in sender if s.get("lead") is not None and s["lead"].id == st.lead_id) == 1 \
        or len(sender) == n_after_requeue + 1


def test_stopping_a_family_gives_its_slot_back(rl, sender):
    db = rl["db"]
    _launch(rl)
    st = db.query(CadenceState).order_by(CadenceState.cadence_started_at).first()
    from app.services.cadence_service import stop_cadence_for_lead
    stop_cadence_for_lead(db, st.lead_id, "stopped_replied")
    assert db.query(OutreachTextSlot).filter_by(cadence_state_id=st.id, status="reserved").count() == 0


def test_dispatch_rechecks_eligibility_a_dnc_family_gets_nothing(rl, sender):
    db = rl["db"]
    _launch(rl)
    st = db.query(CadenceState).order_by(CadenceState.cadence_started_at).first()
    lead = db.query(Lead).filter_by(id=st.lead_id).one()
    lead.status = "dnc"
    db.commit()
    _run(db, utc(2026, 11, 16, 8, 30))
    assert sender == []


def test_other_workspaces_keep_their_own_schedule(db_session):
    assert OS.config_for_org(db_session, "some-other-org") is None


# ── sender content, calendar, email gap, time offer ─────────────────────────

def test_the_855_sender_may_carry_links_and_the_callback_number():
    from app.services import sms_campaigns as SC
    e = SC.for_sender("+18553601164")
    assert e and SC.is_approved(e) and SC.features(e) == {"links": True, "phone": True}
    assert SC.content_refusal("Call (469) 553-7417 or https://restland.evosyspro.live/ABCDE", e) == []
    # SCI's line is unchanged
    assert SC.for_sender("+18449172171")["programs"] == ["sci"]


def test_restland_texts_keep_their_booking_link(rl):
    db = rl["db"]
    _launch(rl)
    from app.models.models import Organization
    from app.services import sms_programs
    org = db.query(Organization).filter_by(id=rl["org"].id).one()
    org.org_twilio_phone_number = "+18553601164"
    db.commit()
    lead = rl["leads"][0]
    assert sms_programs.content_allowance(db, lead) == {"allow_links": True, "allow_phone": True}


def test_bookings_use_the_shared_calendar_account(rl):
    db = rl["db"]
    from app.services.calendar_service import booking_calendar_id, calendar_owner
    shared = User(email="shared.calendar@example.com", full_name="EvoSys Platform User",
                  password_hash="x", is_active=True, google_oauth_refresh_token_encrypted="enc")
    db.add(shared)
    db.flush()
    prog = prog_of(rl)
    pol = json.loads(prog.booking_policy_json or "{}")
    pol["calendar_user_id"] = shared.id
    prog.booking_policy_json = json.dumps(pol)
    db.commit()
    assert calendar_owner(rl["mike"]) is shared
    assert calendar_owner(rl["staff"]) is shared                # office and home share one calendar
    assert booking_calendar_id(rl["mike"]) == "primary"
    other = User(email="nobody@example.com", full_name="Other", password_hash="x", is_active=True)
    db.add(other)
    db.commit()
    assert calendar_owner(other) is other                       # no program: unchanged


def test_the_day1_email_waits_four_hours_after_the_first_text(rl):
    db = rl["db"]
    _launch(rl)
    from app.services.programs import email_touches as ET
    st = db.query(CadenceState).order_by(CadenceState.cadence_started_at).first()
    lead = db.query(Lead).filter_by(id=st.lead_id).one()
    t0 = utc(2026, 11, 16, 8, 30)
    assert ET.first_email_hold(db, lead, t0, t0) == "waits for the first text"
    db.add(Message(lead_id=lead.id, sender_id=rl["mike"].id, body="t1", sent_at=t0 + timedelta(seconds=20)))
    db.commit()
    assert ET.first_email_hold(db, lead, t0, t0 + timedelta(hours=3, minutes=59)) is not None
    assert ET.first_email_hold(db, lead, t0, t0 + timedelta(hours=4, minutes=1)) is None


def test_time_offer_names_two_verified_times_or_asks_for_a_day(rl):
    db = rl["db"]
    configured(rl)
    from app.services.programs import booking_policy as BP
    pol = BP.for_program(prog_of(rl))
    now = datetime(2026, 11, 16, 8, 30)
    line = BP.time_offer(db, rl["mike"], pol, now_local=now, busy_reader=lambda d, a, t: ([], None))
    assert "Monday, Nov 16 at 10:30 AM" not in line
    assert line.startswith("I have Monday, Nov 16 at 11:30 AM or Tuesday, Nov 17 at 10:00 AM open")
    # Restland double-books (owner, Nov 24), so an unreadable calendar no longer
    # stops the offer; a program without that rule still asks for a day.
    down = BP.time_offer(db, rl["mike"], dict(pol, allow_overlap=False), now_local=now,
                         busy_reader=lambda d, a, t: ([], "unreadable"))
    assert down == "What day this week works for you to come by?"


def test_records_without_a_campaign_are_approved_and_a_second_run_continues_after_taken_slots(rl):
    """Production (Nov 15): only the 25 pilot records were on the campaign;
    the rest had none. A first run approved just the 25; a corrected second
    run must approve the rest and schedule them after the 25's slots."""
    db = rl["db"]
    configured(rl)
    staged = {m.lead_id for m in db.query(__import__("app.models.reset_models", fromlist=["x"]).CampaignCycleMember)
              .filter_by(organization_id=rl["org"].id).all()}
    for r in db.query(ProgramSourceRecord).filter_by(organization_id=rl["org"].id).all():
        if r.lead_id not in staged:
            r.campaign_family = None
    db.commit()
    first = RL.launch(db, rl["org"], "launch-a", booking_user_id=rl["mike"].id, calendar_user_id=None,
                      start_local="2026-11-16T08:30", now=utc(2026, 11, 15, 17, 0))["summary"]
    assert first["not_approved"].get("No campaign", 0) == 0
    assert first["approved"] == first["journey"]["started"] > len(staged)
    n1 = db.query(OutreachTextSlot).filter_by(kind="new").count()
    second = RL.launch(db, rl["org"], "launch-b", booking_user_id=rl["mike"].id, calendar_user_id=None,
                       start_local="2026-11-16T08:30", now=utc(2026, 11, 15, 17, 30))["summary"]
    assert second["journey"]["started"] == 0                 # nobody started twice
    assert db.query(OutreachTextSlot).filter_by(kind="new").count() == n1
    slots = [s.slot_at for s in db.query(OutreachTextSlot).filter_by(kind="new").all()]
    assert len(slots) == len(set(slots))


def test_a_later_start_takes_the_next_free_new_family_slots():
    taken = set(OS.new_family_slots(utc(2026, 11, 16, 8, 30), 25, CFG))
    nxt = OS.new_family_slots(utc(2026, 11, 16, 8, 30), 2, CFG, taken=taken)
    assert [local(t) for t in nxt] == ["11:50", "11:58"]


def test_pause_stops_every_restland_send_and_nothing_goes_at_830(rl, sender):
    db = rl["db"]
    _launch(rl)
    out = RL.pause(db, rl["org"], "pause-1")
    assert out["campaigns_turned_off"] and out["text_journeys_paused"] > 0
    assert db.query(OutreachTextSlot).filter_by(status="reserved").count() == 0
    rb = out["readback"]
    assert rb["texts_switch_on"] is False and not any(rb["campaigns_on"].values())
    _run(db, utc(2026, 11, 16, 8, 30))
    _run(db, utc(2026, 11, 16, 12, 0))
    assert sender == []
    from app.services.programs import email_touches as ET
    assert ET.plan(db, prog_of(rl), now=utc(2026, 11, 16, 13, 0), with_skip=False) == []
    again = RL.pause(db, rl["org"], "pause-1")
    assert again["text_journeys_paused"] == 0


# ── a missed FIRST text goes in the next open minute (owner, Nov 23) ─────────
# Production runs "full blast": a new family every 3 minutes and follow-ups in
# every other minute. One skipped minute used to push a first text behind the
# whole follow-up line (Charles Dooley: 9:03 slot, rescheduled to 11:05).

FULL = OS.config_from_policy({"outreach": {"slots": True, "open": "08:30", "close": "19:00",
                                           "new_every_minutes": 3, "followup_fill": True}})


def _first_state(db):
    return db.query(CadenceState).order_by(CadenceState.cadence_started_at).first()


def _hold(db, org_id, at, state_id, touch, kind="followup"):
    db.add(OutreachTextSlot(organization_id=org_id, slot_at=at, kind=kind, cadence_state_id=state_id,
                            touch_number=touch))
    db.flush()


def test_missed_first_text_takes_an_open_new_family_minute_before_the_followup_line(rl):
    db = rl["db"]
    _launch(rl)
    org = rl["org"].id
    st = _first_state(db)
    others = [s for s in db.query(CadenceState).all() if s.id != st.id]
    now = utc(2026, 11, 17, 9, 0)
    # every follow-up minute for the next 15 minutes is held by text-2 follow-ups
    fol = [t for t in OS.slots_from(now, FULL, "followup")][:10]
    for i, t in enumerate(fol):
        _hold(db, org, t, others[i % len(others)].id, 2)
    s = OS.place_first_late(db, org, st, now, FULL)
    db.commit()
    assert s is not None and now <= s < now + timedelta(minutes=15)
    assert s not in fol                                   # an open new-family minute, not a held one
    assert db.query(OutreachTextSlot).filter_by(cadence_state_id=st.id, touch_number=1).one().slot_at == s


def test_missed_first_text_moves_one_followup_when_every_near_minute_is_held(rl):
    db = rl["db"]
    _launch(rl)
    org = rl["org"].id
    st = _first_state(db)
    others = [s for s in db.query(CadenceState).all() if s.id != st.id]
    now = utc(2026, 11, 17, 9, 0)
    end = now + timedelta(minutes=15)
    minutes = sorted({t for t in OS.slots_from(now, FULL, "new") if t < end} |
                     {t for t in OS.slots_from(now, FULL, "followup") if t < end})
    # first minute held by ANOTHER family's first text (must never be moved),
    # the rest by text-2 follow-ups
    _hold(db, org, minutes[0], others[0].id, 1, kind="new")
    for i, t in enumerate(minutes[1:]):
        _hold(db, org, t, others[1 + i % (len(others) - 1)].id, 2)
    bumped_state = db.query(OutreachTextSlot).filter_by(organization_id=org, slot_at=minutes[1]).one().cadence_state_id
    s = OS.place_first_late(db, org, st, now, FULL)
    db.commit()
    assert s == minutes[1]                                # earliest follow-up minute, not the first text's
    assert db.query(OutreachTextSlot).filter_by(organization_id=org, slot_at=minutes[0]).one().cadence_state_id \
        == others[0].id
    # the moved follow-up still holds a slot, later on
    moved = db.query(OutreachTextSlot).filter_by(cadence_state_id=bumped_state, touch_number=2,
                                                 status="reserved").all()
    assert len(moved) >= 1 and all(m.slot_at > minutes[1] for m in moved if m.slot_at != minutes[1])
    # one text per minute still holds: no duplicate slot times
    from sqlalchemy import func
    dup = (db.query(OutreachTextSlot.slot_at, func.count()).filter_by(organization_id=org)
           .group_by(OutreachTextSlot.slot_at).having(func.count() > 1).all())
    assert dup == []


def test_gate_sends_a_missed_first_text_via_the_new_rule(rl, sender):
    db = rl["db"]
    _launch(rl)
    st = _first_state(db)
    # 8:30 slot missed by 4 minutes
    _run(db, utc(2026, 11, 16, 8, 34))
    r = OS.reservation(db, st.id, 1)
    assert r is not None and r.slot_at > utc(2026, 11, 16, 8, 34)


def test_a_day_the_owner_opened_books_even_on_a_closed_sunday_past_the_window(rl):
    db = rl["db"]
    configured(rl)
    from app.services.programs import booking_policy as BP
    pol = dict(BP.for_program(prog_of(rl)), days="012345")         # Sundays closed
    free = lambda d, a, t: ([], None)                               # noqa: E731
    today = date(2026, 11, 23)
    opened = BP.day_slots(db, rl["mike"], date(2026, 12, 13), pol, today=today, busy_reader=free, any_time=15)
    assert "2026-12-13T14:00:00" in opened["slots"]
    other = BP.day_slots(db, rl["mike"], date(2026, 12, 6), pol, today=today, busy_reader=free)
    assert other["slots"] == [] and other["reason"] == "No appointments on this day"
    far = BP.day_slots(db, rl["mike"], date(2026, 12, 14), pol, today=today, busy_reader=free)
    assert far["slots"] == [] and "days out" in far["reason"]
