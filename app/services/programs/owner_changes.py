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
"""
import json
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
# Only before this moment (Mon Nov 23 2026, midnight Central): an old build
# restarted weeks later never applies a stale order.
APPLY_UNTIL = datetime(2026, 11, 23, 6, 0)


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
    if now >= APPLY_UNTIL:
        return out
    for prog in db.query(OutreachProgram).all():
        try:
            if sms_programs.is_sci_org(db, prog.organization_id):
                if _ledger(db, prog.organization_id, SCI_KEY) is None:
                    out[prog.organization_id] = sci_full_speed(db, prog)
            elif OS.config_from_policy(prog.booking_policy_json) is not None:
                if _ledger(db, prog.organization_id, RESTLAND_KEY) is None:
                    out[prog.organization_id] = restland_full_speed(db, prog, now)
        except Exception as exc:                         # noqa: BLE001 - never stops a deploy
            db.rollback()
            log.exception("owner change failed for program %s", prog.id)
            out[prog.organization_id] = {"error": type(exc).__name__}
    return out
