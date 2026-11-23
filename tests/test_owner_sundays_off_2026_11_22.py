"""Owner, Nov 22: "turn off the cadence for Restland only on Sundays ... I want
everything to stop on Sundays, only on Sundays." Sunday is a closed day for
Restland: no slots, no texts, no emails, no daily batch. Everything already
reserved for a Sunday moves to Monday in the same order. Synthetic data only."""
import json
from datetime import date, datetime, timedelta

from app.models.models import CadenceState, Lead
from app.models.program_models import OutreachProgram
from app.models.reset_models import OutreachTextSlot, WorkspaceResetRun
from app.services import contact_hours as CH
from app.services.programs import email_touches as ET
from app.services.programs import outreach_slots as OS
from app.services.programs import owner_changes as OC
from app.services.programs import restland_launch as RL
from tests.test_restland_booking_cadence import rl  # noqa: F401
from tests.test_restland_launch_slots import _launch, local, utc

SUN_NOON = utc(2026, 11, 22, 12, 0)
MON_9 = utc(2026, 11, 23, 9, 0)
CFG = OS.config_from_policy({"outreach": RL.OUTREACH})


def test_sunday_has_no_slots_and_monday_is_unchanged():
    assert CFG["closed_days"] == "7"
    assert OS.day_slots(date(2026, 11, 22), CFG, "new") == []
    assert OS.day_slots(date(2026, 11, 22), CFG, "followup") == []
    assert local(OS.day_slots(date(2026, 11, 23), CFG, "new")[0]) == "08:30"
    assert len(OS.day_slots(date(2026, 11, 21), CFG, "new")) == 79          # Saturday as before
    assert not OS.permits(SUN_NOON, CFG) and OS.permits(MON_9, CFG)
    assert OS.next_open(SUN_NOON, CFG) == utc(2026, 11, 23, 8, 30)
    assert OS.days_label(CFG) == "every day except Sunday"


def test_no_closed_days_means_seven_days():
    cfg = OS.config_from_policy({"outreach": {"slots": True}})
    assert cfg["closed_days"] == "" and OS.days_label(cfg) == "every day"
    assert OS.day_slots(date(2026, 11, 22), cfg, "new")


def _dallas(rl):
    return rl["db"].query(Lead).filter(Lead.organization_id == rl["org"].id, Lead.phone.isnot(None)).first()


def test_texts_and_emails_stop_on_sunday_only(rl):
    _launch(rl)
    OS.clear_cache()
    db, org = rl["db"], rl["org"].id
    lead = _dallas(rl)
    assert not CH.is_permitted(lead, SUN_NOON)
    assert CH.is_permitted(lead, MON_9)
    assert CH.is_permitted(lead, utc(2026, 11, 21, 12, 0))                 # Saturday
    # A person sending by hand is not stopped by the program's closed day.
    assert CH.check(lead, SUN_NOON, program_days=False)["permitted"]
    # The next allowed moment is Monday, never Sunday.
    nxt = CH.next_permitted_utc(lead, SUN_NOON)
    assert nxt is not None and nxt >= utc(2026, 11, 23, 0, 0)
    assert not ET.in_sending_hours(db, org, SUN_NOON)
    assert ET.in_sending_hours(db, org, MON_9)
    assert OS.closed_for_org(db, org, SUN_NOON) and not OS.closed_for_org(db, org, MON_9)


def test_a_program_without_a_text_schedule_is_never_closed(rl):
    OS.clear_cache()
    assert not OS.closed_for_org(rl["db"], rl["org"].id, SUN_NOON)          # not launched: SCI-like
    assert not OS.closed_for_org(rl["db"], None, SUN_NOON)


def test_daily_batch_skips_sunday_without_using_up_the_day(rl, monkeypatch):
    from app.services.programs import daily_batch as DB
    _launch(rl)
    OS.clear_cache()
    db = rl["db"]
    prog = db.query(OutreachProgram).filter_by(organization_id=rl["org"].id).one()
    prog.daily_batch_count, prog.daily_batch_by, prog.daily_batch_last_date = 10, rl["mike"].id, None
    db.commit()
    assert DB.run_for(db, prog, now=utc(2026, 11, 22, 10, 0)) is None
    db.refresh(prog)
    assert prog.daily_batch_last_date is None                                # Monday still runs


def _old_launch(rl, monkeypatch):
    """A launch made before Nov 22, with Sunday still open."""
    monkeypatch.setattr(RL, "OUTREACH", {k: v for k, v in RL.OUTREACH.items() if k != "closed_days"})
    _launch(rl, at="2026-11-21T18:30", now=utc(2026, 11, 21, 12, 0))
    OS.clear_cache()


def _first_texts(db, org):
    return (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
            .filter(Lead.organization_id == org.id, CadenceState.status == "active",
                    CadenceState.current_touch_number == 0)
            .order_by(CadenceState.next_touch_due_at).all())


def test_owner_change_closes_sunday_and_moves_sunday_texts_to_monday_in_order(rl, monkeypatch):
    db, org = rl["db"], rl["org"]
    _old_launch(rl, monkeypatch)
    before = _first_texts(db, org)
    on_sunday = [s for s in before if s.next_touch_due_at.date() >= date(2026, 11, 22)
                 and local(s.next_touch_due_at) and s.next_touch_due_at < utc(2026, 11, 23, 0, 0)
                 and s.next_touch_due_at >= utc(2026, 11, 22, 0, 0)]
    assert on_sunday, "the old launch should have families reserved on Sunday"
    order = [s.lead_id for s in before]
    monkeypatch.setattr(OC, "APPLY_UNTIL", datetime(2026, 11, 1))           # only the Sunday change runs
    out = OC.apply_pending(db, now=utc(2026, 11, 22, 10, 20))
    res = out[org.id]["sundays_off"]
    assert res["sunday_first_texts"] == len(on_sunday)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    assert json.loads(prog.booking_policy_json)["outreach"]["closed_days"] == "7"
    sun0, mon0 = utc(2026, 11, 22, 0, 0), utc(2026, 11, 23, 0, 0)
    assert db.query(OutreachTextSlot).filter(OutreachTextSlot.organization_id == org.id,
                                             OutreachTextSlot.status == "reserved",
                                             OutreachTextSlot.slot_at >= sun0,
                                             OutreachTextSlot.slot_at < mon0).count() == 0
    after = _first_texts(db, org)
    assert [s.lead_id for s in after] == order                               # same order
    moved = [s for s in after if s.lead_id in {x.lead_id for x in on_sunday}]
    assert local(moved[0].next_touch_due_at) == "08:30" and moved[0].next_touch_due_at >= mon0
    for s in after:
        slot = db.query(OutreachTextSlot).filter_by(cadence_state_id=s.id, status="reserved").one()
        assert slot.slot_at == s.next_touch_due_at
    assert OC.apply_pending(db, now=utc(2026, 11, 22, 10, 30)) == {}         # runs once
    assert db.query(WorkspaceResetRun).filter_by(run_key=OC.RESTLAND_SUNDAYS_KEY).count() == 1


def test_sunday_change_never_applies_after_its_window(rl, monkeypatch):
    _old_launch(rl, monkeypatch)
    late = max(OC.APPLY_UNTIL, OC.SUNDAYS_APPLY_UNTIL) + timedelta(minutes=1)
    assert OC.apply_pending(rl["db"], now=late) == {}


def test_handoff_change_restores_later_follow_ups_and_adds_remote_calls(rl, monkeypatch):
    from app.models.program_models import LocationProfile, ProgramConversation, ProgramConversationTurn
    from app.services.programs import booking_policy as BP
    from app.services.programs import identity
    db, org = rl["db"], rl["org"]
    _launch(rl)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    pol = json.loads(prog.booking_policy_json)
    pol["meeting_options"] = [o for o in BP.RESTLAND_POLICY["meeting_options"] if o["key"] != BP.REMOTE]
    prog.booking_policy_json = json.dumps(pol)
    lead = _dallas(rl)
    c = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="human",
                            next_followup_at=datetime(2026, 11, 26, 15, 0), followup_reason="after the holiday")
    db.add(c)
    db.flush()
    t0 = datetime(2026, 11, 20, 18, 0)
    db.add(ProgramConversationTurn(conversation_id=c.id, organization_id=org.id, lead_id=lead.id, role="family",
                                   text="Out of town for holiday", created_at=t0))
    db.add(ProgramConversationTurn(conversation_id=c.id, organization_id=org.id, lead_id=lead.id, role="ai",
                                   action="follow_up_later", delivery="blocked",
                                   created_at=t0 + timedelta(seconds=5)))
    db.commit()
    monkeypatch.setattr(OC, "APPLY_UNTIL", datetime(2026, 11, 1))
    out = OC.apply_pending(db, now=utc(2026, 11, 22, 19, 0))
    res = out[org.id]["handoff"]
    assert res["remote_choice_added"] and len(res["follow_ups_restored"]) == 1
    db.refresh(c)
    assert c.state == "waiting_future" and c.next_followup_at == datetime(2026, 11, 28, 15, 0)   # not Thanksgiving
    db.refresh(prog)
    pol = json.loads(prog.booking_policy_json)
    assert [o["key"] for o in pol["meeting_options"]][-1] == BP.REMOTE
    assert pol["handoff_sheet"]["id"] == OC.RESTLAND_HANDOFF_SHEET_ID
    profs = db.query(LocationProfile).filter_by(organization_id=org.id).all()
    assert profs and all("phone" in identity.appointment_ways(p) for p in profs)
    assert OC.apply_pending(db, now=utc(2026, 11, 22, 19, 5)) == {}                          # once


def test_a_thumbs_up_after_agreeing_keeps_the_later_follow_up(rl, monkeypatch):
    from app.models.program_models import ProgramConversation, ProgramConversationTurn
    from app.models.reset_models import WorkspaceResetRun
    db, org = rl["db"], rl["org"]
    _launch(rl)
    lead = _dallas(rl)
    c = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="ai_active",
                            next_followup_at=datetime(2027, 1, 25, 15, 0))
    db.add(c)
    db.flush()
    t0 = datetime(2026, 11, 18, 3, 17)
    for i, (role, action, text) in enumerate((("family", None, "Not until late January"),
                                              ("ai", "follow_up_later", None), ("family", None, "\U0001F44D"))):
        db.add(ProgramConversationTurn(conversation_id=c.id, organization_id=org.id, lead_id=lead.id, role=role,
                                       action=action, text=text, created_at=t0 + timedelta(minutes=i)))
    db.commit()
    # the first restore already ran
    db.add(WorkspaceResetRun(organization_id=org.id, run_key=OC.RESTLAND_HANDOFF_KEY, mode=OC.MODE, status="applied"))
    db.commit()
    monkeypatch.setattr(OC, "APPLY_UNTIL", datetime(2026, 11, 1))
    monkeypatch.setattr(OC, "_ledger", lambda db_, org_id, key: None if key == OC.RESTLAND_HANDOFF_2_KEY else True)
    out = OC.apply_pending(db, now=utc(2026, 11, 23, 2, 0))
    assert len(out[org.id]["handoff_acks"]["follow_ups_restored"]) == 1
    db.refresh(c)
    assert c.state == "waiting_future" and c.next_followup_at == datetime(2027, 1, 25, 15, 0)


def test_a_real_reply_after_agreeing_is_left_alone():
    assert OC._ACK.match("\U0001F44D") and OC._ACK.match("Thanks!")
    assert not OC._ACK.match("Actually call me Monday")
