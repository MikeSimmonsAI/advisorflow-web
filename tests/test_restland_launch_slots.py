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
    assert local(new[-1]) == "17:58" and local(fol[-1]) == "17:52"   # nothing at or after 6 PM
    assert len(new) == 72 and len(fol) == 71
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
    assert "It's been a while since we reviewed your file" in fam.sms_template
    assert "Personal Planning Guide" in fam.sms_template
    d = json.loads(fam.touch_copy_json)
    assert d["draft"] is False and "{time_offer}" in d["sms"]["2"]


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
    down = BP.time_offer(db, rl["mike"], pol, now_local=now, busy_reader=lambda d, a, t: ([], "unreadable"))
    assert down == "What day this week works for you to come by?"
