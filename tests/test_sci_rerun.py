"""SCI: families who didn't reply start the cadence over (owner, Nov 17 2026).
Never-reached first, then everyone else who didn't reply; 40 a day through the
standing daily batch from Fri Nov 20, 9 AM Central; Re-engagement wording.
Never DNC / replied / booked / a person's / test. Synthetic families; Twilio
and the mailbox faked; nothing is sent."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import BookingLink, CadenceState, Message
from app.models.program_models import (CampaignFamily, OutreachProgram, ProgramConversation, ProgramEmailTouch,
                                       ProgramResponse, ProgramSourceRecord)
from app.models.sci_rerun_models import ProgramRerunMember
from app.services.programs import daily_batch, sci_rerun
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)

BEFORE_FRI = datetime(2026, 11, 19, 16, 0)            # Thu 10 AM CT
FRI_9 = datetime(2026, 11, 20, 15, 1)                 # Fri 9:01 AM CT


def _sci(monkeypatch):
    from app.services import sms_programs
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db, oid: True)


@pytest.fixture
def world(program, sample_advisor, monkeypatch):
    _sci(monkeypatch)
    db, org = program["db"], program["org"]
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    prog.daily_batch_count, prog.daily_batch_by, prog.daily_batch_spacing, prog.daily_batch_days = \
        40, program["admin"].id, 10, None
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key="re_engagement").one()
    fam.is_active = False
    fam.sms_template = "Hi {first_name}, it's {primary_contact_name} with {location_name}. Still glad to help."
    fam.email_subject_template = "Following up, {first_name}"
    fam.email_body_template = "Hi {first_name},\n\nStill glad to help.\n\n{primary_contact_name}"
    db.commit()
    made = {}
    spec = [("L001", "never_reached"), ("L002", "no_reply"), ("L003", "replied"), ("L004", "dnc"),
            ("L005", "booked"), ("L006", "person"), ("L007", "test"), ("L008", "email_only_no_reply")]
    for i, (sid, kind) in enumerate(spec):
        try:
            l = _lead_for(db, org, sample_advisor, sid, phone="20555501%02d" % i, email="%s@example.com" % sid.lower())
        except Exception:
            continue
        l.is_test, l.status, l.phone_line_type = (kind == "test"), "sent", "mobile"
        st = db.query(CadenceState).filter_by(lead_id=l.id).one()
        st.status, st.current_touch_number = "completed", 6
        for r in db.query(ProgramSourceRecord).filter_by(lead_id=l.id):
            r.approved_to_send_at, r.on_hold = datetime(2026, 10, 29, 14, 0), False
        sent = datetime(2026, 11, 5, 16, 0)
        db.add(Message(lead_id=l.id, sender_id=sample_advisor.id, body="t1", sent_at=sent, send_source="cadence",
                       delivery_status="undelivered" if kind == "never_reached" else "delivered"))
        if kind == "replied":
            db.add(ProgramResponse(organization_id=org.id, lead_id=l.id, channel="sms", response_class="low",
                                   body_excerpt="maybe later", received_at=sent))
        if kind == "dnc":
            l.status, st.status = "dnc", "stopped_dnc"
        if kind == "booked":
            db.add(BookingLink(lead_id=l.id, user_id=sample_advisor.id, status="booked", token="tok-%s" % sid))
        if kind == "person":
            db.add(ProgramConversation(organization_id=org.id, lead_id=l.id, state="human"))
        if kind == "email_only_no_reply":
            l.phone_line_type = "landline"
            db.query(CadenceState).filter_by(lead_id=l.id).delete()
        made[kind] = l
    db.commit()
    return {**program, "prog": prog, "leads": made, "fam": fam}


def _members(db):
    return {m.lead_id: m for m in db.query(ProgramRerunMember).all()}


def test_staging_queues_only_families_who_may_start_over_never_reached_first(world):
    db, prog, L = world["db"], world["prog"], world["leads"]
    s = sci_rerun.ensure_staged(db, prog)
    m = _members(db)
    assert set(m) == {L[k].id for k in ("never_reached", "no_reply", "email_only_no_reply") if k in L}
    assert m[L["never_reached"].id].group == "never_reached"
    assert m[L["never_reached"].id].priority < m[L["no_reply"].id].priority
    assert all(x.not_before == sci_rerun.NOT_BEFORE for x in m.values())
    left = s["left_out"]
    for why in ("Replied", "Do not contact / STOP", "Booked", "A person has the conversation", "Test record"):
        assert left.get(why, 0) >= 1, why
    db.refresh(world["fam"])
    assert world["fam"].is_active is True                       # the owner's wording, switched on
    assert sci_rerun.ensure_staged(db, prog) == {"already": True}
    assert db.query(ProgramRerunMember).count() == len(m)
    # staging changed no journey
    st = db.query(CadenceState).filter_by(lead_id=L["no_reply"].id).one()
    assert st.status == "completed"


def test_nothing_starts_before_friday_9_am_central(world):
    db, prog = world["db"], world["prog"]
    sci_rerun.ensure_staged(db, prog)
    assert sci_rerun.start_next(db, prog, world["admin"], 40, BEFORE_FRI, 10, now=BEFORE_FRI) == []
    assert daily_batch.run_for(db, prog, BEFORE_FRI).get("rerun_started") is None


def test_friday_batch_starts_them_in_order_on_the_drip_with_history_kept(world):
    db, prog, L = world["db"], world["prog"], world["leads"]
    sci_rerun.ensure_staged(db, prog)
    nr = L["never_reached"]
    db.add(ProgramEmailTouch(organization_id=prog.organization_id, lead_id=nr.id, campaign_family="general_survey",
                             touch_number=1, status="sent", attempted_at=datetime(2026, 11, 5, 17, 0)))
    db.commit()
    res = daily_batch.run_for(db, prog, FRI_9)
    assert res["rerun_started"] == len(_members(db))
    m = _members(db)
    assert all(x.status == "started" for x in m.values())
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=nr.id).first()
    assert rec.campaign_family == "re_engagement" and rec.send_mode == "text_email"
    st = db.query(CadenceState).filter_by(lead_id=nr.id).one()
    assert st.status == "active" and st.current_touch_number == 0
    first = st.next_touch_due_at
    assert FRI_9 < first < FRI_9 + timedelta(minutes=15)       # first on the drip
    later = db.query(CadenceState).filter_by(lead_id=L["no_reply"].id).one().next_touch_due_at
    assert later > first                                          # never-reached first
    assert db.query(ProgramEmailTouch).filter_by(lead_id=nr.id).one().touch_number == 901   # history kept
    assert '"campaign_family": "' in m[nr.id].before_json        # every changed value recorded
    eo = L.get("email_only_no_reply")
    if eo is not None:
        assert db.query(CadenceState).filter_by(lead_id=eo.id).count() == 0              # no texts
        assert db.query(ProgramSourceRecord).filter_by(lead_id=eo.id).first().send_mode == "email_only"
    again = daily_batch.run_for(db, prog, FRI_9 + timedelta(hours=1))
    assert again is None                                          # once a day


def test_a_family_that_replies_before_its_turn_is_left_out(world):
    db, prog, L = world["db"], world["prog"], world["leads"]
    sci_rerun.ensure_staged(db, prog)
    db.add(ProgramResponse(organization_id=prog.organization_id, lead_id=L["no_reply"].id, channel="sms",
                           response_class="low", body_excerpt="call me", received_at=BEFORE_FRI))
    db.commit()
    started = sci_rerun.start_next(db, prog, world["admin"], 40, FRI_9, 10, now=FRI_9)
    assert L["no_reply"].id not in {s["lead_id"] for s in started}
    m = _members(db)[L["no_reply"].id]
    assert m.status == "skipped" and m.reason == "Replied"
    assert db.query(ProgramSourceRecord).filter_by(lead_id=L["no_reply"].id).first().campaign_family != "re_engagement"


def test_the_daily_count_is_shared_with_new_families(world, monkeypatch):
    db, prog = world["db"], world["prog"]
    prog.daily_batch_count = 2
    db.commit()
    sci_rerun.ensure_staged(db, prog)
    monkeypatch.setattr(daily_batch, "pick_entries", lambda db, prog, n: [{"lead_id": "new1", "mode": "text_email"}])
    with patch("app.services.programs.send_approval.approve", return_value={"approved": 1}):
        res = daily_batch.run_for(db, prog, FRI_9)
    assert res["picked"] == 1 and res["rerun_started"] == 1        # 1 new + 1 starting over = 2


def test_the_restarted_family_gets_text_1_in_the_re_engagement_wording(world, monkeypatch):
    from app.services import cadence_service as cs
    from tests.test_cadence_engine import _sending_on
    db, prog, L = world["db"], world["prog"], world["leads"]
    sci_rerun.ensure_staged(db, prog)
    daily_batch.run_for(db, prog, FRI_9)
    _sending_on(monkeypatch)
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True, "reason": ""})
    sent = []

    def fake_send(db=None, advisor=None, lead=None, template=None, **k):
        db_, adv, lead_, text = db, advisor, lead, template
        sent.append((lead_.id, text))
        m = Message(lead_id=lead_.id, sender_id=adv.id, body=text, twilio_sid="SMx%d" % len(sent),
                    twilio_status="queued", sent_at=FRI_9 + timedelta(minutes=30))
        db_.add(m)
        db_.flush()
        return m
    with patch("app.services.sms_service.send_sms", side_effect=fake_send), \
         patch("app.services.cadence_service._spacing_hold_until", return_value=None):
        cs.run_due_cadences(db, now=FRI_9 + timedelta(minutes=30))
        cs.run_due_cadences(db, now=FRI_9 + timedelta(minutes=35))       # never twice
    mine = [t for lid, t in sent if lid == L["never_reached"].id]
    assert len(mine) == 1 and "Still glad to help" in mine[0]


def test_a_family_waits_for_14_quiet_days_and_a_finished_run(world):
    """Owner, Nov 21: only a family that finished and has been quiet 14 days
    starts over; anyone still in a run finishes first."""
    db, prog = world["db"], world["prog"]
    sci_rerun.ensure_staged(db, prog, now=BEFORE_FRI)
    lid = world["leads"]["no_reply"].id
    recent = datetime(2026, 11, 15, 16, 0)                              # 5 days before Friday
    db.add(Message(lead_id=lid, sender_id=world["admin"].id, body="t4", sent_at=recent,
                   send_source="cadence", delivery_status="delivered"))
    db.commit()
    with patch.object(sci_rerun, "_mode", lambda p, l: "text_email"):
        out = sci_rerun.start_next(db, prog, world["admin"], 10, FRI_9, 10, now=FRI_9)
    assert lid not in {o["lead_id"] for o in out}
    m = db.query(ProgramRerunMember).filter_by(lead_id=lid).one()
    assert m.status == "queued" and m.not_before == recent + timedelta(days=14)


def test_families_who_finish_later_are_added_to_wait_their_turn(world):
    db, prog = world["db"], world["prog"]
    sci_rerun.ensure_staged(db, prog, now=BEFORE_FRI)
    lid = world["leads"]["no_reply"].id
    db.query(ProgramRerunMember).filter_by(lead_id=lid).delete()        # as if it joined the journey later
    db.commit()
    assert sci_rerun.top_up(db, prog, now=FRI_9)["added"] == 1
    m = db.query(ProgramRerunMember).filter_by(lead_id=lid).one()
    assert m.status == "queued" and m.priority > 100000
    assert sci_rerun.top_up(db, prog, now=FRI_9)["added"] == 0          # once
