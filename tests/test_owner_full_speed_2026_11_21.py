"""Owner, Nov 21: Restland and SCI full blast. Restland: a new family every 3
minutes with follow-ups in every minute between; everyone waiting moves up in
the same order. SCI: 200 a day, 2 minutes apart. Each change runs once."""
from datetime import datetime, timedelta

from app.models.models import CadenceState, Lead
from app.models.program_models import OutreachProgram
from app.models.reset_models import OutreachTextSlot, WorkspaceResetItem, WorkspaceResetRun
from app.services.programs import outreach_slots as OS
from app.services.programs import owner_changes as OC
from tests.test_restland_launch_slots import _launch, utc
from tests.test_restland_booking_cadence import rl  # noqa: F401

NOW = utc(2026, 11, 21, 23, 30)                      # Sat 5:30 PM CT


def test_followups_fill_every_minute_between_new_family_slots():
    cfg = OS.config_from_policy({"outreach": {"slots": True, "open": "08:30", "close": "19:00",
                                              "new_every_minutes": 3, "followup_fill": True}})
    day = datetime(2026, 11, 23).date()
    new = OS.day_slots(day, cfg, "new")
    fol = OS.day_slots(day, cfg, "followup")
    assert len(new) == 210 and len(fol) == 420
    assert not set(new) & set(fol)                                     # never two texts in one minute
    assert sorted(new + fol) == [new[0] + timedelta(minutes=i) for i in range(630)]


def test_without_fill_the_old_one_followup_per_gap_stays():
    cfg = OS.config_from_policy({"outreach": {"slots": True, "open": "08:30", "close": "19:00",
                                              "new_every_minutes": 8}})
    assert cfg["followup_fill"] is False
    assert len(OS.day_slots(datetime(2026, 11, 23).date(), cfg, "followup")) == 79


def _waiting(db, org):
    return (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
            .filter(Lead.organization_id == org.id, CadenceState.status == "active",
                    CadenceState.current_touch_number == 0)
            .order_by(CadenceState.next_touch_due_at).all())


def test_restland_moves_everyone_waiting_up_in_order_once(rl):
    db, org = rl["db"], rl["org"]
    _launch(rl)
    before = [s.lead_id for s in _waiting(db, org)]
    assert before
    out = OC.apply_pending(db, now=NOW)
    summary = out[org.id]["full_speed"]
    assert summary["moved_up"] == len(before)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    cfg = OS.config_from_policy(prog.booking_policy_json)
    assert cfg["new_every_minutes"] == 3 and cfg["followup_fill"] is True
    assert (prog.daily_batch_count, prog.daily_batch_spacing) == (200, 3)
    after = _waiting(db, org)
    assert [s.lead_id for s in after] == before                       # same order
    times = [s.next_touch_due_at for s in after]
    gaps = {(b - a) for a, b in zip(times, times[1:]) if (b - a) < timedelta(hours=1)}
    assert gaps <= {timedelta(minutes=3)}                              # every 3 minutes within a day
    for s in after:
        slot = db.query(OutreachTextSlot).filter_by(cadence_state_id=s.id, status="reserved").one()
        assert slot.slot_at == s.next_touch_due_at and slot.kind == "new"
    assert db.query(WorkspaceResetItem).filter_by(action="moved_up").count() == len(before)
    assert OC.apply_pending(db, now=NOW) == {}                         # runs once
    assert db.query(WorkspaceResetRun).filter_by(run_key=OC.RESTLAND_KEY).count() == 1


def test_nothing_runs_after_the_window(rl):
    db, org = rl["db"], rl["org"]
    _launch(rl)
    late = max(OC.APPLY_UNTIL, OC.SUNDAYS_APPLY_UNTIL) + timedelta(minutes=1)
    assert OC.apply_pending(db, now=late) == {}


def test_sci_goes_to_200_a_day_two_minutes_apart(program, monkeypatch):
    from app.services import sms_programs
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db, oid: True)
    db, org = program["db"], program["org"]
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    prog.daily_batch_count, prog.daily_batch_spacing = 40, 10
    db.commit()
    out = OC.apply_pending(db, now=NOW)
    db.refresh(prog)
    assert out[org.id] == {"daily_batch": 200, "spacing": 2}
    assert (prog.daily_batch_count, prog.daily_batch_spacing) == (200, 2)
    item = db.query(WorkspaceResetItem).filter_by(action="daily_batch", entity_id=prog.id).one()
    assert '"daily_batch_count": 40' in item.before_json                # undo-able


from tests.test_outreach_program import program  # noqa: E402,F401 (fixture)
