"""EvoSense autonomous hunting (Phase 7.1).

WHAT RUNS IT. The platform's own background-loop machinery: `app/main.py`
starts `_evosense_hunt_loop` for the process that `app/service_role.py` names
as owner of `JobName.EVOSENSE_HUNT` (the backend), and every pass is recorded
in the platform job ledger (`job_runs`) by `record_job_run`, exactly like the
cadence and review-request loops. There is no second daemon.

WHAT A PASS DOES (`run_due`):
    1. every ACTIVE strategy gets a schedule row (default: daily)
    2. a strategy whose `next_due_at` has come is claimed with an atomic lock
    3. the SAME `hunt.run_strategy` the "Run hunt" button calls runs it
    4. the schedule records the result and the next due time
    5. seller replies whose evaluation is pending (AI failure, or held while
       EvoSense was paused) are retried

SAFETY. Kill switches are checked before a hunt is started (and again inside
the hunt). A strategy that is already running is skipped, not doubled. A
failed hunt is recorded, surfaced, and retried an hour later; everything the
hunt does is idempotent, so a retry never duplicates a property, a charge or
a message.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.evosense_models import EvoSenseHuntSchedule, EvoSenseStrategy
from app.services.evosense import common as C

CADENCES = ("manual", "daily", "interval")
DEFAULT_CADENCE = "daily"
LOCK_FOR = timedelta(hours=2)          # longer than any bounded hunt; expires if a worker dies
RETRY_AFTER_FAILURE = timedelta(hours=1)
TICK_SECONDS = 900                     # the platform loop wakes every 15 minutes


def interval_for(sched) -> Optional[timedelta]:
    if sched.cadence == "daily":
        return timedelta(hours=24)
    if sched.cadence == "interval":
        return timedelta(hours=max(1, int(sched.interval_hours or 24)))
    return None


def schedule_for(db, strategy, *, create: bool = True) -> Optional[EvoSenseHuntSchedule]:
    row = (db.query(EvoSenseHuntSchedule)
           .filter(EvoSenseHuntSchedule.organization_id == strategy.organization_id,
                   EvoSenseHuntSchedule.strategy_id == strategy.id).first())
    if row is None and create:
        # Two workers can meet a brand-new strategy at the same moment; the
        # unique strategy_id lets exactly one create the row - the other uses it
        # (same savepoint pattern as the budget counters).
        sp = db.begin_nested()
        try:
            row = EvoSenseHuntSchedule(organization_id=strategy.organization_id,
                                       strategy_id=strategy.id, cadence=DEFAULT_CADENCE)
            # First due time: a day after the last hunt, or now if it never ran.
            last = strategy.last_hunt_at
            row.next_due_at = (last + timedelta(hours=24)) if last else C.now()
            db.add(row)
            db.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            row = (db.query(EvoSenseHuntSchedule)
                   .filter(EvoSenseHuntSchedule.organization_id == strategy.organization_id,
                           EvoSenseHuntSchedule.strategy_id == strategy.id).first())
    return row


def set_cadence(db, strategy, cadence: str, interval_hours: Optional[int] = None) -> EvoSenseHuntSchedule:
    if cadence not in CADENCES:
        raise ValueError("cadence must be manual, daily or interval")
    if cadence == "interval":
        if not interval_hours or int(interval_hours) < 1 or int(interval_hours) > 24 * 30:
            raise ValueError("An interval is between 1 hour and 30 days.")
    row = schedule_for(db, strategy)
    row.cadence = cadence
    row.interval_hours = int(interval_hours) if cadence == "interval" else None
    step = interval_for(row)
    if step is None:
        row.next_due_at = None
    else:
        base = strategy.last_hunt_at or C.now()
        row.next_due_at = max(C.now(), base + step) if strategy.last_hunt_at else C.now()
    return row


# ── the lock ────────────────────────────────────────────────────────────────

def claim(db, strategy) -> Optional[str]:
    """Atomically take this strategy's hunt lock. Returns a token, or None if
    another hunt holds it. Committed immediately so other workers see it."""
    sched = schedule_for(db, strategy)
    db.commit()
    token = uuid.uuid4().hex
    now = C.now()
    res = db.execute(text(
        "UPDATE evosense_hunt_schedules SET lock_token = :tok, locked_until = :until "
        "WHERE id = :id AND (locked_until IS NULL OR locked_until < :now)"),
        {"tok": token, "until": now + LOCK_FOR, "id": sched.id, "now": now})
    db.commit()
    return token if res.rowcount == 1 else None


def release(db, strategy, token: str) -> None:
    db.execute(text("UPDATE evosense_hunt_schedules SET lock_token = NULL, locked_until = NULL "
                    "WHERE strategy_id = :sid AND lock_token = :tok"),
               {"sid": strategy.id, "tok": token})
    db.commit()


def record_result(db, strategy, run) -> None:
    """After any hunt (manual or scheduled): when is the next one due."""
    sched = schedule_for(db, strategy)
    db.refresh(sched)
    sched.last_run_id = run.id
    sched.last_status = run.status
    sched.last_error = (run.error or None) and run.error[:250]
    if run.status == "failed":
        sched.consecutive_failures = (sched.consecutive_failures or 0) + 1
        if interval_for(sched):
            sched.next_due_at = C.now() + RETRY_AFTER_FAILURE
    else:
        sched.consecutive_failures = 0
        step = interval_for(sched)
        if step and run.status in ("succeeded", "partial"):
            sched.next_due_at = C.now() + step
    db.commit()


# ── the pass ────────────────────────────────────────────────────────────────

def _note_skip(db, strategy, sched, status: str, summary: str) -> bool:
    """Record a skip once per state change, not every 15 minutes."""
    if sched.last_status == status:
        return False
    sched.last_status = status
    C.log_event(db, strategy.organization_id, "hunt." + status, strategy_id=strategy.id,
                actor_type=C.ACTOR_AUTOMATION, is_test=strategy.is_test, summary=summary)
    db.commit()
    return True


def run_due(db, *, org_id: Optional[str] = None, now=None) -> Dict[str, Any]:
    """One scheduler pass across every organization (or one). Returns counts
    for the job ledger."""
    from app.services.evosense import conversation as CV
    from app.services.evosense import hunt as HU
    now = now or C.now()
    report = {"strategies": 0, "due": 0, "ran": 0, "failed": 0, "skipped_paused": 0,
              "skipped_lock": 0, "manual_only": 0, "replies_retried": 0}
    q = db.query(EvoSenseStrategy).filter(EvoSenseStrategy.status == "active")
    if org_id:
        q = q.filter(EvoSenseStrategy.organization_id == org_id)
    for strategy in q.order_by(EvoSenseStrategy.organization_id, EvoSenseStrategy.created_at).all():
        report["strategies"] += 1
        try:
            sched = schedule_for(db, strategy)
            db.commit()
            if getattr(strategy, "pilot_mode", False) or interval_for(sched) is None:
                # A PILOT is never hunted automatically, whatever its cadence says.
                report["manual_only"] += 1
                continue
            if sched.next_due_at and sched.next_due_at > max(now, C.now()):
                continue
            report["due"] += 1
            ctl = C.controls(db, strategy.organization_id)
            if ctl.paused_all or ctl.paused_discovery:
                report["skipped_paused"] += 1
                _note_skip(db, strategy, sched, "skipped_paused",
                           "Scheduled hunt not started: %s" % ("EvoSense is paused" if ctl.paused_all
                                                               else "discovery is paused"))
                continue
            sched.last_scheduled_at = now
            C.log_event(db, strategy.organization_id, "hunt.queued", strategy_id=strategy.id,
                        actor_type=C.ACTOR_AUTOMATION, is_test=strategy.is_test,
                        summary="Scheduled hunt due (%s)" % sched.cadence)
            db.commit()
            run = HU.run_strategy(db, strategy.organization_id, strategy, trigger="schedule")
            if run.status == "skipped" and (run.error or "").startswith("Another hunt"):
                report["skipped_lock"] += 1
            elif run.status == "failed":
                report["failed"] += 1
            else:
                report["ran"] += 1
        except Exception:  # noqa: BLE001 - one organization's failure never stops the others
            db.rollback()
            report["failed"] += 1
            C.log.exception("evosense scheduled hunt crashed for strategy %s", strategy.id)
    try:
        report["replies_retried"] = CV.retry_pending(db, org_id=org_id)
    except Exception:  # noqa: BLE001
        db.rollback()
        C.log.exception("evosense pending-reply retry failed")
    return report


def status(db, org_id: str) -> List[Dict[str, Any]]:
    """What the Command Center and Strategies screens say about automation."""
    out = []
    ctl = C.controls(db, org_id)
    for s in (db.query(EvoSenseStrategy)
              .filter(EvoSenseStrategy.organization_id == org_id,
                      EvoSenseStrategy.status.in_(("active", "paused")))
              .order_by(EvoSenseStrategy.created_at).all()):
        sched = schedule_for(db, s, create=False)
        cadence = sched.cadence if sched else DEFAULT_CADENCE
        if getattr(s, "pilot_mode", False):
            cadence = "manual"                 # pilots only run when a person starts them
        running = bool(sched and sched.locked_until and sched.locked_until > C.now())
        if s.status != "active":
            state = "strategy_paused"
        elif ctl.paused_all or ctl.paused_discovery:
            state = "paused"
        elif running:
            state = "running"
        elif cadence == "manual":
            state = "manual"
        else:
            state = "scheduled"
        out.append({"strategy_id": s.id, "name": s.name, "is_test": bool(s.is_test),
                    "cadence": cadence, "interval_hours": sched.interval_hours if sched else None,
                    "state": state,
                    "next_due_at": (sched.next_due_at.isoformat() + "Z")
                    if sched and sched.next_due_at and state in ("scheduled",) else None,
                    "last_hunt_at": s.last_hunt_at.isoformat() + "Z" if s.last_hunt_at else None,
                    "last_status": sched.last_status if sched else None,
                    "last_error": sched.last_error if sched else None})
    return out
