"""Owner, Nov 17: families who got the OLD File Review opening and never
answered go back in line for the new one, behind the families waiting."""
from datetime import datetime, timedelta

import pytest

from app.models.models import BookingLink, CadenceState, Lead
from app.models.program_models import ProgramEmailTouch, ProgramResponse, ProgramSourceRecord
from app.models.reset_models import OutreachTextSlot, WorkspaceResetItem
from app.services.programs import restland_ffr as F
from app.services.programs import restland_restart as RR
from tests.test_restland_launch_slots import _launch, utc
from tests.test_restland_booking_cadence import rl  # noqa: F401

NOW = utc(2026, 11, 17, 20, 0)


def _texted(db, rl):
    """Families the launch started, sorted by first slot."""
    return (db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
            .filter(Lead.organization_id == rl["org"].id, CadenceState.status == "active")
            .order_by(CadenceState.next_touch_due_at).all())


def _got_old_text(db, st):
    st.current_touch_number = 1
    st.next_touch_due_at = NOW + timedelta(days=1)
    db.flush()


def test_unanswered_go_back_in_line_answered_and_booked_stay(rl):
    db, org = rl["db"], rl["org"]
    _launch(rl)
    states = _texted(db, rl)
    a, b, c, d = states[0], states[1], states[2], states[3]
    for st in (a, b, c, d):
        _got_old_text(db, st)
    # b answered for real; c's reply was automatic (doesn't count); d booked.
    db.add(ProgramResponse(organization_id=org.id, lead_id=b.lead_id, response_class="active", channel="sms",
                           received_at=NOW))
    db.add(ProgramResponse(organization_id=org.id, lead_id=c.lead_id, response_class="auto_reply", channel="sms",
                           received_at=NOW))
    db.add(BookingLink(lead_id=d.lead_id, user_id=rl["mike"].id, status="booked", booked_time=NOW + timedelta(days=2)))
    # e: email-only family that got the old email 1.
    e = states[4]
    db.add(ProgramEmailTouch(organization_id=org.id, lead_id=e.lead_id, campaign_family=F.CAMPAIGN_KEY,
                             touch_number=1, status="sent", attempted_at=NOW - timedelta(hours=3)))
    db.add(ProgramEmailTouch(organization_id=org.id, lead_id=a.lead_id, campaign_family=F.CAMPAIGN_KEY,
                             touch_number=1, status="sent", attempted_at=NOW - timedelta(hours=3)))
    db.commit()
    last_waiting = max(t for (t,) in db.query(OutreachTextSlot.slot_at).filter(
        OutreachTextSlot.organization_id == org.id, OutreachTextSlot.kind == "new",
        OutreachTextSlot.status == "reserved").all())

    dry = RR.run(db, org, "r1", apply=False, now=NOW)
    assert dry["dry_run"] and dry["summary"]["families"] == 3           # a, c (auto reply), e
    assert db.query(CadenceState).filter_by(id=a.id).one().current_touch_number == 1   # dry run changed nothing

    out = RR.run(db, org, "r1", apply=True, now=NOW)
    assert out["applied"] and out["summary"]["families"] == 3
    for st in (a, c):
        db.refresh(st)
        assert st.current_touch_number == 0 and st.status == "active"
        assert st.next_touch_due_at > last_waiting                     # behind everyone already waiting
        assert st.cadence_started_at == st.next_touch_due_at           # old texts are history
        slot = db.query(OutreachTextSlot).filter_by(cadence_state_id=st.id, status="reserved").one()
        assert slot.touch_number == 1 and slot.kind == "new" and slot.slot_at == st.next_touch_due_at
    for st in (b, d):
        db.refresh(st)
        assert st.current_touch_number == 1                            # answered / booked: untouched
    nums = sorted(t.touch_number for t in db.query(ProgramEmailTouch).filter(
        ProgramEmailTouch.lead_id.in_([a.lead_id, e.lead_id])).all())
    assert nums == [901, 901]                                          # old email 1s kept as history
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=a.lead_id).first()
    assert rec.scheduled_start_at == db.query(CadenceState).filter_by(id=a.id).one().next_touch_due_at
    assert db.query(WorkspaceResetItem).filter(WorkspaceResetItem.action == "restart_text_1").count() == 2
    assert RR.run(db, org, "r1", apply=True, now=NOW)["already"] is True   # once per run key


def test_sci_is_refused(rl, monkeypatch):
    monkeypatch.setenv("SMS_PROGRAM_ORG_SCI", rl["org"].id)
    with pytest.raises(RR.RestartRefused):
        RR.run(rl["db"], rl["org"], "r1", apply=False, now=NOW)


def test_front_of_the_line_and_everyone_waiting_moves_back_in_order(rl):
    """Owner's choice, Nov 17: front of the line. They go first from the next
    opening; the families still waiting keep their order behind them; nobody
    is texted again within 20 hours; no two families share a slot."""
    db, org = rl["db"], rl["org"]
    _launch(rl)
    states = _texted(db, rl)
    a, b = states[0], states[1]
    for st in (a, b):
        _got_old_text(db, st)
    a.last_touch_sent_at = NOW - timedelta(hours=2)          # texted 2 hours ago
    b.last_touch_sent_at = NOW - timedelta(days=1)
    db.commit()
    waiting = [s for s in _texted(db, rl) if s.current_touch_number == 0]
    before = [s.id for s in sorted(waiting, key=lambda s: s.next_touch_due_at)]
    out = RR.run(db, org, "front-1", apply=True, now=NOW, position="front")
    s = out["summary"]
    assert s["position"] == "front" and s["families"] == 2 and s["waiting_moved_back"] == len(waiting)
    db.refresh(a), db.refresh(b)
    assert a.next_touch_due_at >= NOW - timedelta(hours=2) + timedelta(hours=20)   # the 20-hour gap holds
    assert b.current_touch_number == 0
    for w in waiting:
        db.refresh(w)
    moved = sorted(waiting, key=lambda s: s.next_touch_due_at)
    assert [x.id for x in moved] == before                              # same order as before
    assert b.next_touch_due_at < moved[0].next_touch_due_at             # put back at the front
    slots = [x.slot_at for x in db.query(OutreachTextSlot).filter(OutreachTextSlot.organization_id == org.id,
                                                                    OutreachTextSlot.status == "reserved").all()]
    assert len(slots) == len(set(slots))                                # no slot handed out twice
    for st in [a, b] + moved:
        r = db.query(OutreachTextSlot).filter_by(cadence_state_id=st.id, touch_number=1, status="reserved").one()
        assert r.slot_at == st.next_touch_due_at == st.cadence_started_at
    assert RR.run(db, org, "front-1", apply=True, now=NOW, position="front")["already"] is True
