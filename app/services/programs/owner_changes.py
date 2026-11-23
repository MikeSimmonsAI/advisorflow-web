"""ONE-TIME CHANGES THE OWNER ORDERED IN CHAT, applied once at deploy.

Each change runs at most once per organization (a WorkspaceResetRun row with
mode "owner_change" and its own run_key is the ledger), records every value it
changes (WorkspaceResetItem before/after) so it can be undone, and never
stops a deploy. Later choices made in the app are never overwritten: once the
ledger row exists the change never runs again.

2026-11-21, owner ("Restland full blast, no slow drip ... SCI up and running,
full blast ... running them at the maximum capacity"):
  * RESTLAND: a new family's first text every 3 minutes (8:30 AM - 7 PM
    Central, about 210 a day; was every 8 minutes, 79 a day) with follow-up
    texts in every minute between (was one per gap, 78 a day, already full).
    Every family still waiting for its first text moves up into the new
    slots, in the same order. Daily batch 200 a day, 3 minutes apart.
  * SCI: daily batch 200 a day (the most allowed), 2 minutes apart (was 40,
    10 minutes apart): the remaining new families and the start-over queue.

2026-11-22, owner ("I need you to turn off the cadence for Restland only on
Sundays ... I want everything to stop on Sundays, only on Sundays"):
  * RESTLAND: Sunday becomes a closed day (outreach_slots closed_days "7"):
    no texts, emails, AI answers, daily batch or survey go out on Sundays.
    Every text already reserved for a Sunday slot moves to the next open
    day in the same order - first texts to the next new-family slots,
    follow-ups to the next free follow-up slots. SCI is not touched.

2026-11-22 evening, owner (Restland handoff):
  * RESTLAND: families who asked us to follow up later ("after the
    holidays", "late January") go back on the follow-up list with their date
    (state waiting_future; a holiday or Sunday moves to the next working
    day). They had been parked as "needs a person", where the date was
    never acted on.
  * RESTLAND: out-of-town families can book a remote call (phone or video)
    - the "By phone or video call" choice is added to Restland's booking
    choices and the location offers phone appointments.
"""
import json
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

MODE = "owner_change"
RESTLAND_KEY = "restland-full-speed-2026-11-21"
SCI_KEY = "sci-full-speed-2026-11-21"
RESTLAND_NEW_EVERY = 3
RESTLAND_DAILY, RESTLAND_SPACING = 200, 3
SCI_DAILY, SCI_SPACING = 200, 2
RESTLAND_SUNDAYS_KEY = "restland-sundays-off-2026-11-22"
RESTLAND_CLOSED_DAYS = "7"
# Only before these moments: an old build restarted weeks later never
# applies a stale order. Nov 21 changes: Mon Nov 23 midnight Central.
APPLY_UNTIL = datetime(2026, 11, 23, 6, 0)
SUNDAYS_APPLY_UNTIL = datetime(2026, 12, 6, 6, 0)
RESTLAND_HANDOFF_KEY = "restland-followups-remote-2026-11-22"
# The team's handoff board (Google Sheet, owner's Drive; shared with the
# sync service account as Editor). Not a secret.
RESTLAND_HANDOFF_SHEET_ID = "1o8gPHz0-tLcrN_IxSQsSQE3Mt6IUX0bQ1U4jlmDfPgo"
RESTLAND_HANDOFF_2_KEY = "restland-followups-acks-2026-11-22"
RESTLAND_HANDOFF_3_KEY = "restland-jan4-remote-on-request-2026-11-22"
# Owner, Nov 22 9:48 PM: "this was a test so undo everything about this test".
# His sheet test at 9:43 PM marked Adolfo Vila's Nov 19 appointment confirmed +
# showed (scheduling a thank-you survey) and closed Aftab Naseer's follow-up.
RESTLAND_SHEET_TEST_UNDO_KEY = "restland-sheet-test-undo-2026-11-22"
TEST_BOOKING_ID = "eccad87b-e484-4a29-959a-7023a9f4dbde"
TEST_CONV_ID = "3bff2f95-1cda-4ce9-8fe8-b8ac5c5b2665"
TEST_CONV_REASON = ("The family is upset due to receiving messages after Aftab's passing, and immediate attention "
                    "is needed to update records and stop further messages.")
TEST_NOTE_IDS = ("ef0e99d5-4d16-4a79-9b70-1a0e1d5ee4de", "e72cc136-01e8-41c5-9fff-a4debd909136")
TEST_SURVEY_ID = "ac5f7d15-9e3e-484f-90c0-946be2decab3"
# Owner, Nov 23 9:09 AM: Carmelita Alcala asked "Do have any around 3 today?" -
# "if the customer requested 3 o'clock, sure, we can do 3 o'clock ... book it
# yourself". Booked once, through the normal booking path, only while 3 PM
# today is still bookable (2-hour same-day notice) and she has nothing booked.
CARMELITA_KEY = "restland-carmelita-3pm-2026-11-23"
CARMELITA_LEAD = "4f7ef8fe-5007-4ccb-aadb-7957154eec00"
CARMELITA_SLOT = datetime(2026, 11, 23, 15, 0)          # Central
CARMELITA_UNTIL = datetime(2026, 11, 23, 19, 0)          # 1 PM Central, in UTC
# Owner, Nov 22 8:18 PM: these four "after the holidays" families are followed
# up the first Monday after New Year's - Mon Jan 4 2027, 9 AM Central.
AFTER_NEW_YEAR = datetime(2027, 1, 4, 15, 0)
AFTER_NEW_YEAR_LEADS = (
    "e129cb24-c807-42c9-bec5-447efb494538",   # Annette Birt
    "07dc8e9d-6ccb-4923-9245-58bdb1c86aaa",   # Brian Fehr
    "13d1df8b-4829-4f11-9755-45b49bcfc245",   # Alleyne Henderson
    "f4c9f1bf-9697-4183-bb46-5e29c4cc839c",   # Albert Faulkner
)
# A reply that only acknowledges what was agreed ("👍", "ok", "thanks") is not
# a new message: the later follow-up still stands.
_ACK = re.compile(r"^\W*(ok(ay)?|k|thanks?( you)?|thx|ty|sounds good|great|perfect|will do|yes|yep|sure|cool)?\W*$", re.I)


def _ledger(db: Session, org_id: str, key: str):
    from app.models.reset_models import WorkspaceResetRun
    return db.query(WorkspaceResetRun).filter_by(organization_id=org_id, run_key=key, mode=MODE).first()


def _open_run(db: Session, org_id: str, key: str):
    from app.models.reset_models import WorkspaceResetRun
    run = WorkspaceResetRun(organization_id=org_id, run_key=key, mode=MODE, status="started")
    db.add(run)
    db.flush()

    def item(entity, eid, action, before, after, reason):
        from app.models.reset_models import WorkspaceResetItem
        db.add(WorkspaceResetItem(run_id=run.id, organization_id=org_id, entity=entity, entity_id=eid,
                                  action=action, before_json=json.dumps(before, default=str),
                                  after_json=json.dumps(after, default=str), reason=reason))
    return run, item


def _batch(prog, item, count: int, spacing: int, reason: str) -> None:
    before = {"daily_batch_count": prog.daily_batch_count, "daily_batch_spacing": prog.daily_batch_spacing}
    prog.daily_batch_count, prog.daily_batch_spacing = count, spacing
    item("outreach_program", prog.id, "daily_batch", before,
         {"daily_batch_count": count, "daily_batch_spacing": spacing}, reason)


def restland_full_speed(db: Session, prog, now: datetime) -> Dict:
    from app.services.programs import outreach_slots as OS
    from app.services.programs import restland_restart as RR
    org_id = prog.organization_id
    pol = json.loads(prog.booking_policy_json or "{}")
    o = pol.get("outreach")
    if not isinstance(o, dict) or not o.get("slots"):
        return {"skipped": "no text schedule"}
    run, item = _open_run(db, org_id, RESTLAND_KEY)
    before = dict(o)
    o["new_every_minutes"], o["followup_fill"] = RESTLAND_NEW_EVERY, True
    pol["outreach"] = o
    prog.booking_policy_json = json.dumps(pol)
    item("outreach_program", prog.id, "text_schedule", before, dict(o), "owner Nov 21: full blast")
    _batch(prog, item, RESTLAND_DAILY, RESTLAND_SPACING, "owner Nov 21: full blast")
    db.flush()
    OS.clear_cache()
    cfg = OS.config_from_policy(prog.booking_policy_json)
    # Everyone still waiting for a first text, in their current order.
    waiting = [w for w in RR._waiting(db, org_id, set()) if w["state"] is not None]
    S = OS._model()
    ids = {w["state"].id for w in waiting}
    for w in waiting:
        OS.release(db, w["state"].id, 1)
    db.flush()
    taken = {t for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= now,
                                                     S.status == "reserved").all()}
    first = OS.next_open(now + timedelta(minutes=5), cfg)
    slots = OS.new_family_slots(first, len(waiting), cfg, taken) if waiting else []
    for w, at in zip(waiting, slots):
        st = w["state"]
        item("cadence_state", st.id, "moved_up",
             {"cadence_started_at": st.cadence_started_at, "next_touch_due_at": st.next_touch_due_at},
             {"next_touch_due_at": at}, "new family every 3 minutes")
        st.cadence_started_at, st.next_touch_due_at = at, at
        OS.reserve(db, org_id, st, 1, at, "new")
        for r in w["recs"]:
            r.scheduled_start_at = at
    summary = {"new_every_minutes": RESTLAND_NEW_EVERY, "followup_fill": True,
               "daily_batch": RESTLAND_DAILY, "spacing": RESTLAND_SPACING, "moved_up": len(slots),
               "first_slot_utc": slots[0].isoformat() if slots else None,
               "last_slot_utc": slots[-1].isoformat() if slots else None, "moved_ids": len(ids)}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    OS.clear_cache()
    return summary


def restland_sundays_off(db: Session, prog, now: datetime) -> Dict:
    """Close Sundays for Restland and move every text reserved for a Sunday
    slot (from today on) to the next open day, keeping its order."""
    from app.models.models import CadenceState
    from app.services.programs import outreach_slots as OS
    from app.services.programs import restland_restart as RR
    org_id = prog.organization_id
    pol = json.loads(prog.booking_policy_json or "{}")
    o = pol.get("outreach")
    if not isinstance(o, dict) or not o.get("slots"):
        return {"skipped": "no text schedule"}
    run, item = _open_run(db, org_id, RESTLAND_SUNDAYS_KEY)
    before = dict(o)
    o["closed_days"] = RESTLAND_CLOSED_DAYS
    pol["outreach"] = o
    prog.booking_policy_json = json.dumps(pol)
    item("outreach_program", prog.id, "text_schedule", before, dict(o), "owner Nov 22: nothing on Sundays")
    db.flush()
    OS.clear_cache()
    cfg = OS.config_from_policy(prog.booking_policy_json)
    S = OS._model()
    today_local = OS._local(now).date()
    since = OS._utc(datetime.combine(today_local, datetime.min.time()).replace(tzinfo=OS._zone()))
    rows = (db.query(S).filter(S.organization_id == org_id, S.status == "reserved", S.slot_at >= since)
            .order_by(S.slot_at).all())
    closed = [r for r in rows if OS.closed_on(OS._local(r.slot_at).date(), cfg)]
    first = OS.next_open(now, cfg)
    # First texts keep their order: every first text from the first Sunday
    # one onward is re-laid on the new-family grid from the next opening, so a
    # Sunday family is never put behind Monday's.
    closed_new = [r for r in closed if r.kind == "new"]
    new_rows = [r for r in rows if r.kind == "new" and r.slot_at >= closed_new[0].slot_at] if closed_new else []
    fu_rows = [r for r in closed if r.kind != "new"]
    recs_by_state = {w["state"].id: w["recs"] for w in RR._waiting(db, org_id, set()) if w["state"] is not None}
    states = {st.id: st for st in db.query(CadenceState).filter(
        CadenceState.id.in_([r.cadence_state_id for r in closed + new_rows] or ["-"])).all()}
    moves_new = [(r.cadence_state_id, r.touch_number, r.slot_at) for r in new_rows]
    moves_fu = [(r.cadence_state_id, r.touch_number, r.slot_at) for r in fu_rows]
    for sid, touch, _at in moves_new:
        OS.release(db, sid, touch)
    db.flush()
    taken = {t for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= first,
                                                     S.status == "reserved").all()}
    slots = OS.new_family_slots(first, len(moves_new), cfg, taken) if moves_new else []
    moved_new = 0
    for (sid, touch, was), at in zip(moves_new, slots):
        st = states.get(sid)
        if st is None:
            continue
        item("cadence_state", st.id, "sunday_moved",
             {"next_touch_due_at": st.next_touch_due_at, "slot_at": was}, {"next_touch_due_at": at},
             "owner Nov 22: nothing on Sundays")
        if (st.current_touch_number or 0) == 0:
            st.cadence_started_at = at
            for r in recs_by_state.get(st.id, []):
                r.scheduled_start_at = at
        st.next_touch_due_at = at
        OS.reserve(db, org_id, st, touch, at, "new")
        moved_new += 1
    moved_fu = 0
    for sid, touch, was in moves_fu:
        st = states.get(sid)
        if st is None:
            continue
        before_due = st.next_touch_due_at
        at = OS.place_followup(db, org_id, st, touch, first, cfg)
        item("cadence_state", st.id, "sunday_moved", {"next_touch_due_at": before_due, "slot_at": was},
             {"next_touch_due_at": at}, "owner Nov 22: nothing on Sundays")
        moved_fu += 1
    summary = {"closed_days": RESTLAND_CLOSED_DAYS, "sunday_first_texts": len(closed_new),
               "first_texts_relaid": moved_new, "moved_follow_ups": moved_fu,
               "next_open_utc": first.isoformat(),
               "first_moved_utc": slots[0].isoformat() if slots else None,
               "last_moved_utc": slots[-1].isoformat() if slots else None}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    OS.clear_cache()
    return summary


def restland_jan4_and_remote_on_request(db: Session, prog, now: datetime) -> Dict:
    """Owner, Nov 22 8:18 PM: (1) the four families above follow up Mon Jan 4
    2027; (2) a phone/video call ONLY when the family asks - off the booking
    page, and the location no longer offers phone appointments itself."""
    from app.models.program_models import LocationProfile, ProgramConversation
    from app.services.programs import booking_policy as BP
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_HANDOFF_3_KEY)
    moved = []
    for c in (db.query(ProgramConversation)
              .filter(ProgramConversation.organization_id == org_id,
                      ProgramConversation.lead_id.in_(AFTER_NEW_YEAR_LEADS),
                      ProgramConversation.state == "waiting_future").all()):
        item("program_conversation", c.id, "follow_up_date", {"next_followup_at": c.next_followup_at},
             {"next_followup_at": AFTER_NEW_YEAR}, "owner Nov 22: first Monday after New Year's")
        c.next_followup_at, c.followups_sent = AFTER_NEW_YEAR, 0
        moved.append(c.lead_id)
    pol = json.loads(prog.booking_policy_json or "{}")
    opts = pol.get("meeting_options")
    if isinstance(opts, list):
        new = []
        for o in opts:
            if o.get("key") == BP.REMOTE and not o.get("on_request"):
                item("outreach_program", prog.id, "remote_on_request", dict(o), None, "owner Nov 22: only on request")
                o = dict(BP.REMOTE_OPTION)
            new.append(o)
        pol["meeting_options"] = new
        prog.booking_policy_json = json.dumps(pol)
    phone_off = 0
    for prof in db.query(LocationProfile).filter(LocationProfile.organization_id == org_id).all():
        try:
            bs = json.loads(prof.brand_settings or "{}") if isinstance(prof.brand_settings, str) else dict(prof.brand_settings or {})
        except ValueError:
            bs = {}
        if bs.get("offer_phone_alternative"):
            item("location_profile", prof.id, "brand_settings", dict(bs), None, "owner Nov 22: calls only on request")
            bs.pop("offer_phone_alternative", None)
            prof.brand_settings = json.dumps(bs)
            phone_off += 1
    summary = {"moved_to_jan_4": moved, "locations_phone_off": phone_off}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def restland_book_carmelita(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import BookingLink, Lead
    from app.models.program_models import ProgramResponse
    from app.services.programs import ai_setter
    from app.services.programs import appointments as A
    from app.services.programs import responses as R
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, CARMELITA_KEY)
    lead = db.query(Lead).filter(Lead.id == CARMELITA_LEAD, Lead.organization_id == org_id).first()
    out: Dict = {}
    if lead is None:
        out["skipped"] = "family not found"
    elif db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                      BookingLink.status.in_(("booked", "confirmed"))).count():
        out["skipped"] = "already booked"
    else:
        res = A.book(db, prog, lead, CARMELITA_SLOT, A.VISIT, meeting_key="office")
        out = {"status": res.get("status"), "booking_id": res.get("booking_id"), "reason": res.get("reason")}
        if res.get("ok") and not res.get("simulated"):
            conv = ai_setter.conversation_for(db, lead)
            item("program_conversation", conv.id, "booked", {"state": conv.state}, {"state": "booked"},
                 "owner Nov 23: she asked for 3 PM today")
            conv.state, conv.booked_time = "booked", CARMELITA_SLOT
            if res.get("booking_id"):
                conv.booking_link_id = res.get("booking_id")
            newest = (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                                       ProgramResponse.lead_id == lead.id,
                                                       ProgramResponse.handling_status != "closed")
                      .order_by(ProgramResponse.received_at.desc()).first())
            actor = A.program_advisor(db, prog)
            if newest is not None and actor is not None:
                R.mark(db, newest, "responded", actor)
            try:
                A.notify_team_booked(db, prog, lead, CARMELITA_SLOT, A.VISIT)
            except Exception:                            # noqa: BLE001 - the booking stands
                log.exception("Carmelita booked alert failed")
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out, default=str)
    db.commit()
    return out


def restland_sheet_test_undo(db: Session, prog, now: datetime) -> Dict:
    """Put both families back exactly as they were before the owner's test."""
    from app.models.models import BookingLink
    from app.models.program_models import HandoffSheetRow, ProgramConversation
    from app.models.reset_models import AppointmentNotification, AppointmentSurvey
    from app.models.work_models import LeadNote
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_SHEET_TEST_UNDO_KEY)
    done = {}
    b = db.query(BookingLink).filter(BookingLink.id == TEST_BOOKING_ID).first()
    if b is not None and b.status == "completed":
        item("booking_link", b.id, "undo_test", {"status": b.status, "confirmed_at": b.confirmed_at,
                                                 "completed_at": b.completed_at}, {"status": "booked"},
             "owner Nov 22: undo sheet test")
        b.status, b.completed_at, b.confirmed_at = "booked", None, None
        b.attendance_recorded_at, b.attendance_recorded_by = None, None
        done["appointment"] = "back to booked, not confirmed"
    n = 0
    for r in (db.query(AppointmentNotification).filter(AppointmentNotification.booking_id == TEST_BOOKING_ID,
                                                       AppointmentNotification.purpose == "survey",
                                                       AppointmentNotification.status.in_(("scheduled", "retry"))).all()):
        item("appointment_notification", r.id, "undo_test", {"status": r.status}, {"status": "superseded"},
             "owner Nov 22: undo sheet test")
        r.status, r.reason = "superseded", "owner's sheet test undone - never sent"
        n += 1
    done["surveys_stopped"] = n
    sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.id == TEST_SURVEY_ID).first()
    if sv is not None:
        item("appointment_survey", sv.id, "undo_test", {"booking_id": sv.booking_id}, None, "owner Nov 22: undo sheet test")
        db.delete(sv)
    c = db.query(ProgramConversation).filter(ProgramConversation.id == TEST_CONV_ID).first()
    if c is not None and c.state == "closed":
        item("program_conversation", c.id, "undo_test", {"state": c.state, "handoff_reason": c.handoff_reason},
             {"state": "human"}, "owner Nov 22: undo sheet test")
        c.state, c.handoff_reason = "human", TEST_CONV_REASON
        done["follow_up"] = "open again (needs a person)"
    notes = db.query(LeadNote).filter(LeadNote.id.in_(TEST_NOTE_IDS)).all()
    for nt in notes:
        item("lead_note", nt.id, "undo_test", {"body": nt.body}, None, "owner Nov 22: undo sheet test")
        db.delete(nt)
    done["notes_removed"] = len(notes)
    for r in db.query(HandoffSheetRow).filter(HandoffSheetRow.organization_id == org_id,
                                              HandoffSheetRow.row_id.in_((TEST_BOOKING_ID, "FU-" + TEST_CONV_ID))).all():
        r.assigned_to, r.auto_assigned, r.assigned_at, r.staff_changed_at = None, False, None, None
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(done)
    db.commit()
    return done


def restland_followups_acks(db: Session, prog, now: datetime) -> Dict:
    """Second pass of the Nov 22 restore: families whose only later reply was
    an acknowledgement ("👍") also go back on the follow-up list."""
    from app.models.program_models import ProgramConversation, ProgramConversationTurn
    from app.services.programs import holidays as H
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_HANDOFF_2_KEY)
    back = []
    for c in (db.query(ProgramConversation)
              .filter(ProgramConversation.organization_id == org_id,
                      ProgramConversation.next_followup_at.isnot(None),
                      ProgramConversation.next_followup_at > now,
                      ProgramConversation.state.in_(("human", "ai_active"))).all()):
        turns = (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == c.id)
                 .order_by(ProgramConversationTurn.created_at).all())
        fu = [t for t in turns if t.role == "ai" and t.action == "follow_up_later"]
        if not fu:
            continue
        later = [t for t in turns if t.role == "family" and t.created_at > fu[-1].created_at]
        if not later or not all(_ACK.match(t.text or "") for t in later):
            continue
        when = c.next_followup_at
        new_when = datetime.combine(H.next_working_day(when.date()), when.time())
        item("program_conversation", c.id, "follow_up_list",
             {"state": c.state, "next_followup_at": when}, {"state": "waiting_future", "next_followup_at": new_when},
             "owner Nov 22: an acknowledgement keeps the agreed follow-up")
        c.state, c.next_followup_at, c.followups_sent = "waiting_future", new_when, 0
        back.append({"lead_id": c.lead_id, "follow_up_at": new_when.isoformat()})
    summary = {"follow_ups_restored": back}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def restland_followups_and_remote(db: Session, prog, now: datetime) -> Dict:
    from app.models.program_models import LocationProfile, ProgramConversation, ProgramConversationTurn
    from app.services.programs import booking_policy as BP
    from app.services.programs import holidays as H
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_HANDOFF_KEY)
    # 1. the remote-call booking choice
    pol = json.loads(prog.booking_policy_json or "{}")
    opts = pol.get("meeting_options")
    added_remote = False
    if isinstance(opts, list) and opts and not any(o.get("key") == BP.REMOTE for o in opts):
        item("outreach_program", prog.id, "meeting_options", list(opts), None, "owner Nov 22: remote calls")
        pol["meeting_options"] = list(opts) + [dict(BP.REMOTE_OPTION)]
        prog.booking_policy_json = json.dumps(pol)
        added_remote = True
    if not (pol.get("handoff_sheet") or {}).get("id"):
        item("outreach_program", prog.id, "handoff_sheet", pol.get("handoff_sheet"), None, "owner Nov 22: handoff board")
        pol["handoff_sheet"] = {"id": RESTLAND_HANDOFF_SHEET_ID}
        prog.booking_policy_json = json.dumps(pol)
    phone_on = 0          # superseded the same evening: calls only on the family's request
    # 2. later follow-ups back on the follow-up list
    back = []
    convs = (db.query(ProgramConversation)
             .filter(ProgramConversation.organization_id == org_id,
                     ProgramConversation.next_followup_at.isnot(None),
                     ProgramConversation.state.in_(("human", "ai_active"))).all())
    for c in convs:
        turns = (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == c.id)
                 .order_by(ProgramConversationTurn.created_at).all())
        fu = [t for t in turns if t.role == "ai" and t.action == "follow_up_later"]
        if not fu:
            continue
        if any(t.role == "family" and t.created_at > fu[-1].created_at and not _ACK.match(t.text or "")
               for t in turns):
            continue                                   # they wrote again since: that reply rules
        when = c.next_followup_at
        day = H.next_working_day(when.date())
        new_when = datetime.combine(day, when.time())
        item("program_conversation", c.id, "follow_up_list",
             {"state": c.state, "next_followup_at": when}, {"state": "waiting_future", "next_followup_at": new_when},
             "owner Nov 22: never lose 'after the holidays'")
        c.state, c.next_followup_at, c.followups_sent = "waiting_future", new_when, 0
        back.append({"lead_id": c.lead_id, "follow_up_at": new_when.isoformat()})
    summary = {"remote_choice_added": added_remote, "locations_phone_on": phone_on, "follow_ups_restored": back}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def sci_full_speed(db: Session, prog) -> Dict:
    run, item = _open_run(db, prog.organization_id, SCI_KEY)
    _batch(prog, item, SCI_DAILY, SCI_SPACING, "owner Nov 21: SCI full blast")
    summary = {"daily_batch": SCI_DAILY, "spacing": SCI_SPACING}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def apply_pending(db: Session, now: Optional[datetime] = None) -> Dict:
    """Run every owner change not yet applied. Never raises."""
    from app.models.program_models import OutreachProgram
    from app.services import sms_programs
    from app.services.programs import outreach_slots as OS
    now = now or datetime.utcnow()
    out: Dict = {}
    if now >= max(APPLY_UNTIL, SUNDAYS_APPLY_UNTIL):
        return out
    for prog in db.query(OutreachProgram).all():
        try:
            if sms_programs.is_sci_org(db, prog.organization_id):
                if now < APPLY_UNTIL and _ledger(db, prog.organization_id, SCI_KEY) is None:
                    out[prog.organization_id] = sci_full_speed(db, prog)
            elif OS.config_from_policy(prog.booking_policy_json) is not None:
                res = {}
                if now < APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_KEY) is None:
                    res["full_speed"] = restland_full_speed(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_SUNDAYS_KEY) is None:
                    res["sundays_off"] = restland_sundays_off(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_HANDOFF_KEY) is None:
                    res["handoff"] = restland_followups_and_remote(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_HANDOFF_2_KEY) is None:
                    res["handoff_acks"] = restland_followups_acks(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_HANDOFF_3_KEY) is None:
                    res["jan4_remote"] = restland_jan4_and_remote_on_request(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_SHEET_TEST_UNDO_KEY) is None:
                    res["sheet_test_undo"] = restland_sheet_test_undo(db, prog, now)
                if now < CARMELITA_UNTIL and _ledger(db, prog.organization_id, CARMELITA_KEY) is None:
                    res["carmelita_3pm"] = restland_book_carmelita(db, prog, now)
                if res:
                    out[prog.organization_id] = res
        except Exception as exc:                         # noqa: BLE001 - never stops a deploy
            db.rollback()
            log.exception("owner change failed for program %s", prog.id)
            out[prog.organization_id] = {"error": type(exc).__name__}
    return out
