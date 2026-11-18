"""PUT UNANSWERED FAMILIES BACK IN LINE (owner, Nov 17).

The File Review opening changed on Nov 17. Families who got the OLD opening -
a text, an email or both - and have not answered "haven't gotten one" as far
as the owner is concerned: they go back in line for the NEW opening, behind
the families already waiting, exactly like a family starting fresh.

Where in line (owner's choice, Nov 17: FRONT): position="front" puts them
first - they were first in line originally - starting at the next opening,
and every family still waiting for its first message moves back the same
number of places, in the same order. No family is texted again sooner than
20 hours after its last message (the spacing backstop). position="back"
puts them behind everyone waiting.

Who: Restland File Review families whose journey started and who received at
least one round-1 text or email, with NO genuine reply (automatic replies do
not count), not booked, not do-not-contact / held / opted out, not test.

What, per family (every before-value recorded in one WorkspaceResetRun, mode
"restart_unanswered", so it can be undone; idempotent per run key):
- texts: their reserved slots are freed and their text journey restarts at
  text 1 in the next free new-family slot after everyone already queued
  (cadence_started_at moves there, so earlier texts are history - the
  texting engine already counts only attempts since the start);
- emails: their round-1 email rows are set aside as history (touch 901-904),
  so email 1 goes again with the new wording on the new start;
- the program record's scheduled start moves to the new slot.
Nothing is sent from here. SCI is refused.
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import BookingLink, CadenceState, Lead, Organization
from app.models.program_models import ProgramEmailTouch, ProgramSourceRecord
from app.models.reset_models import WorkspaceResetItem, WorkspaceResetRun

MODE = "restart_unanswered"
ARCHIVE = 900                       # round-1 email n becomes 900 + n (history)


class RestartRefused(Exception):
    pass


def _candidates(db: Session, org_id: str) -> List[Dict]:
    from app.services.programs import restland_ffr as F
    from app.services.programs import rotation
    from app.services.programs.journey import _may_enrol
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.campaign_family == F.CAMPAIGN_KEY,
                    ProgramSourceRecord.lead_id.isnot(None), ProgramSourceRecord.journey_started_at.isnot(None))
            .all())
    by_lead: Dict[str, List] = {}
    for r in recs:
        by_lead.setdefault(r.lead_id, []).append(r)
    ids = list(by_lead)
    if not ids:
        return []
    leads, states, emails, booked = {}, {}, {}, set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        leads.update({l.id: l for l in db.query(Lead).filter(Lead.id.in_(chunk)).all()})
        states.update({s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all()})
        for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.lead_id.in_(chunk),
                                                    ProgramEmailTouch.touch_number.between(1, 99)).all():
            emails.setdefault(t.lead_id, []).append(t)
        booked.update(x for (x,) in db.query(BookingLink.lead_id).filter(
            BookingLink.lead_id.in_(chunk), BookingLink.status.in_(("booked", "confirmed"))).all())
    replied = rotation.genuine_repliers(db, org_id, ids)
    out = []
    for lid in ids:
        lead, st = leads.get(lid), states.get(lid)
        if lead is None or getattr(lead, "is_test", False) or lid in replied or lid in booked:
            continue
        if (lead.status or "") in ("dnc", "replied", "booked", "hot"):
            continue
        texted = st is not None and st.status in ("active", "completed") and (st.current_touch_number or 0) >= 1
        emailed = bool(emails.get(lid))
        if not (texted or emailed):
            continue                                   # still waiting for the first message: keeps its place
        retext = texted and _may_enrol(lead)
        start = min((r.scheduled_start_at for r in by_lead[lid] if r.scheduled_start_at), default=None)
        out.append({"lead": lead, "state": st if retext else None, "recs": by_lead[lid],
                    "emails": emails.get(lid, []), "texted": texted, "emailed": emailed, "start": start})
    out.sort(key=lambda c: (c["start"] or datetime.min, c["lead"].id))
    return out


def _waiting(db: Session, org_id: str, skip) -> List[Dict]:
    """Families started but still waiting for their FIRST message (no text,
    no email yet), in their current order."""
    from app.services.programs import restland_ffr as F
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.campaign_family == F.CAMPAIGN_KEY,
                    ProgramSourceRecord.lead_id.isnot(None), ProgramSourceRecord.journey_started_at.isnot(None))
            .all())
    by_lead: Dict[str, List] = {}
    for r in recs:
        if r.lead_id not in skip:
            by_lead.setdefault(r.lead_id, []).append(r)
    ids = list(by_lead)
    states, emailed = {}, set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        states.update({x.lead_id: x for x in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all()})
        emailed.update(x for (x,) in db.query(ProgramEmailTouch.lead_id).filter(
            ProgramEmailTouch.lead_id.in_(chunk), ProgramEmailTouch.touch_number.between(1, 99)).all())
    out = []
    for lid, rows in by_lead.items():
        st = states.get(lid)
        if lid in emailed:
            continue
        if st is not None and not (st.status == "active" and (st.current_touch_number or 0) == 0):
            continue
        start = (st.next_touch_due_at if st is not None else None) or \
            min((r.scheduled_start_at for r in rows if r.scheduled_start_at), default=None)
        out.append({"lead_id": lid, "state": st, "recs": rows, "start": start})
    out.sort(key=lambda c: (c["start"] or datetime.max, c["lead_id"]))
    return out


def _last_outreach(c: Dict) -> Optional[datetime]:
    st = c.get("state")
    times = [t.attempted_at for t in c.get("emails") or [] if t.attempted_at]
    if st is not None and st.last_touch_sent_at:
        times.append(st.last_touch_sent_at)
    return max(times) if times else None


def _front(db: Session, org: Organization, run_key: str, cands: List[Dict], apply: bool, now: datetime) -> Dict:
    from app.services.programs import outreach_slots as OS
    from app.services.programs.spacing import MIN_GAP
    cfg = OS.config_for_org(db, org.id)
    waiting = _waiting(db, org.id, {c["lead"].id for c in cands})
    S = OS._model()
    waiting_state_ids = {w["state"].id for w in waiting if w["state"] is not None}
    # Their own new-family slots are given back out; everything else stays taken.
    taken = {t for (t, sid, kind) in db.query(S.slot_at, S.cadence_state_id, S.kind).filter(
        S.organization_id == org.id, S.slot_at >= now, S.status == "reserved").all()
        if not (kind == "new" and sid in waiting_state_ids)}
    order = [("restart", c) for c in cands] + [("waiting", w) for w in waiting]
    first = OS.next_open(now + timedelta(minutes=5), cfg)
    pool = OS.new_family_slots(first, len(order) + 200, cfg, taken)
    used = [False] * len(pool)
    plan = []
    for kind, c in order:
        floor = None
        if kind == "restart":
            last = _last_outreach(c)
            floor = (last + MIN_GAP) if last else None
        k = next(i for i, sl in enumerate(pool) if not used[i] and (floor is None or sl >= floor))
        used[k] = True
        plan.append((kind, c, pool[k]))
    r_slots = [at for kind, _c, at in plan if kind == "restart"]
    w_moves = [(c, at) for kind, c, at in plan if kind == "waiting"]
    summary = {"position": "front", "families": len(cands),
               "texts_restarted": sum(1 for c in cands if c["state"] is not None),
               "email_only_or_no_text": sum(1 for c in cands if c["state"] is None),
               "had_old_text": sum(1 for c in cands if c["texted"]),
               "had_old_email": sum(1 for c in cands if c["emailed"]),
               "first_new_slot_utc": min(r_slots).isoformat() if r_slots else None,
               "last_new_slot_utc": max(r_slots).isoformat() if r_slots else None,
               "waiting_moved_back": len(w_moves),
               "waiting_last_slot_utc": max(at for _c, at in w_moves).isoformat() if w_moves else None}
    if not apply:
        return {"dry_run": True, "summary": summary}
    done = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode=MODE).first()
    run = done or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode=MODE)
    db.add(run)
    db.flush()

    def item(entity, eid, action, before, after, reason):
        db.add(WorkspaceResetItem(run_id=run.id, organization_id=org.id, entity=entity, entity_id=eid,
                                  action=action, before_json=json.dumps(before, default=str),
                                  after_json=json.dumps(after, default=str), reason=reason))

    # Free every slot being handed out again first, so no reservation clashes.
    for kind, c, _at in plan:
        st = c["state"]
        if st is not None:
            OS.release(db, st.id, None if kind == "restart" else 1)
    db.flush()
    for kind, c, at in plan:
        st = c["state"]
        if kind == "restart":
            if st is not None:
                before = {"status": st.status, "current_touch_number": st.current_touch_number,
                          "cadence_started_at": st.cadence_started_at, "next_touch_due_at": st.next_touch_due_at,
                          "completed_at": st.completed_at}
                st.status, st.current_touch_number, st.completed_at = "active", 0, None
                st.cadence_started_at, st.next_touch_due_at = at, at
                OS.reserve(db, org.id, st, 1, at, "new")
                item("cadence_state", st.id, "restart_text_1", before,
                     {"current_touch_number": 0, "next_touch_due_at": at}, "old opening, no reply: front of the line")
            for t in sorted(c["emails"], key=lambda t: -t.touch_number):
                item("program_email_touch", t.id, "set_aside", {"touch_number": t.touch_number},
                     {"touch_number": ARCHIVE + t.touch_number}, "old opening email kept as history")
                t.touch_number = ARCHIVE + t.touch_number
            db.flush()
        else:
            if st is not None:
                item("cadence_state", st.id, "moved_back",
                     {"cadence_started_at": st.cadence_started_at, "next_touch_due_at": st.next_touch_due_at},
                     {"next_touch_due_at": at}, "made room for the families put back in line")
                st.cadence_started_at, st.next_touch_due_at = at, at
                OS.reserve(db, org.id, st, 1, at, "new")
        for r in c["recs"]:
            item("program_source_record", r.id, "new_start", {"scheduled_start_at": r.scheduled_start_at},
                 {"scheduled_start_at": at}, "new place in line")
            r.scheduled_start_at = at
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return {"applied": True, "run_id": run.id, "summary": summary}


def run(db: Session, org: Organization, run_key: str, *, apply: bool = False,
        now: Optional[datetime] = None, position: str = "back") -> Dict:
    from app.services import sms_programs
    from app.services.programs import outreach_slots as OS
    if org is None:
        raise RestartRefused("Organization not found.")
    if sms_programs.is_sci_org(db, org.id):
        raise RestartRefused("Refused: this is the SCI organization.")
    now = now or datetime.utcnow()
    done = db.query(WorkspaceResetRun).filter_by(organization_id=org.id, run_key=run_key, mode=MODE).first()
    if done is not None and done.status == "applied":
        return {"already": True, "run_id": done.id, "summary": json.loads(done.summary_json or "{}")}
    cfg = OS.config_for_org(db, org.id)
    if cfg is None:
        raise RestartRefused("This program has no text schedule of its own.")
    cands = _candidates(db, org.id)
    if position == "front":
        return _front(db, org, run_key, cands, apply, now)
    S = OS._model()
    last_new = (db.query(S.slot_at).filter(S.organization_id == org.id, S.kind == "new", S.status == "reserved",
                                           S.slot_at >= now).order_by(S.slot_at.desc()).first())
    first = (last_new[0] + timedelta(minutes=1)) if last_new else OS.next_open(now + timedelta(minutes=5), cfg)
    taken = OS.taken_slots(db, org.id, now)
    slots = OS.new_family_slots(first, len(cands), cfg, taken) if cands else []
    summary = {"families": len(cands), "texts_restarted": sum(1 for c in cands if c["state"] is not None),
               "email_only_or_no_text": sum(1 for c in cands if c["state"] is None),
               "had_old_text": sum(1 for c in cands if c["texted"]),
               "had_old_email": sum(1 for c in cands if c["emailed"]),
               "first_new_slot_utc": slots[0].isoformat() if slots else None,
               "last_new_slot_utc": slots[-1].isoformat() if slots else None}
    if not apply:
        return {"dry_run": True, "summary": summary}
    run = done or WorkspaceResetRun(organization_id=org.id, run_key=run_key, mode=MODE)
    db.add(run)
    db.flush()

    def item(entity, eid, action, before, after, reason):
        db.add(WorkspaceResetItem(run_id=run.id, organization_id=org.id, entity=entity, entity_id=eid,
                                  action=action, before_json=json.dumps(before, default=str),
                                  after_json=json.dumps(after, default=str), reason=reason))

    for c, at in zip(cands, slots):
        lead, st = c["lead"], c["state"]
        if st is not None:
            before = {"status": st.status, "current_touch_number": st.current_touch_number,
                      "cadence_started_at": st.cadence_started_at, "next_touch_due_at": st.next_touch_due_at,
                      "completed_at": st.completed_at}
            OS.release(db, st.id)
            st.status, st.current_touch_number, st.completed_at = "active", 0, None
            st.cadence_started_at = at
            if not OS.assign_new(db, org.id, st, at):
                OS.place_followup(db, org.id, st, 1, at, cfg)
            item("cadence_state", st.id, "restart_text_1", before,
                 {"current_touch_number": 0, "cadence_started_at": st.cadence_started_at,
                  "next_touch_due_at": st.next_touch_due_at}, "old opening, no reply: back in line")
        for t in sorted(c["emails"], key=lambda t: -t.touch_number):
            item("program_email_touch", t.id, "set_aside", {"touch_number": t.touch_number},
                 {"touch_number": ARCHIVE + t.touch_number}, "old opening email kept as history")
            t.touch_number = ARCHIVE + t.touch_number
        db.flush()
        for r in c["recs"]:
            item("program_source_record", r.id, "new_start", {"scheduled_start_at": r.scheduled_start_at},
                 {"scheduled_start_at": at}, "back in line for the new opening")
            r.scheduled_start_at = at
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return {"applied": True, "run_id": run.id, "summary": summary}
