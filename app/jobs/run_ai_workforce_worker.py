"""THE AI WORKFORCE QUEUE WORKER (T6) - the consumer the platform was missing.

WHAT IT DOES. One pass over every active, unpaused AI employee: free any lease
a crashed worker left behind, then claim due work items under a lease and run
each one through `runtime.run_employee`, which re-resolves the employee's
activation stage first. An employee that is not in a stage that may run (off,
killed) claims nothing; one in SIMULATION runs against simulated adapters only.

WHY IT EXISTS. `run_ai_operations_worker.py` drains T7's `ai_scheduled_actions`
only. Before this file the only callers of the T6 claim/run path were the
God Mode "run" endpoint and the simulator, so a queued work item was never
picked up by anything on a schedule.

THE SAME FUNCTION RUNS THE CUSTOMER'S "RUN TEST". `test_mode.run_test` calls
`run_pass(employee_id=...)` so a test is processed by this worker's code, not
by a look-alike. Scoping a pass to one employee changes WHICH employees are
visited, never how one is run.

HEALTH. `main()` records every pass in `job_runs` under WORKER_JOB_NAME, which
is what the customer screen and the admin screens read to say whether the
worker is actually running. A worker that has never recorded a pass is "not
running", never "healthy".

OFF BY DEFAULT. Not scheduled by `app/main.py`. A Render cron entry is the
deployment path (see handoff/ai-workforce/RELEASE_PLAN.md) and is an owner
decision. Even run by hand it exits at once unless AI_OPERATIONS_ENABLED is set
and no kill switch is engaged, and the per-employee activation stage still
decides whether anything is claimed.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai_workforce.worker")

WORKER_JOB_NAME = "ai_workforce_worker"


def run_pass(db, *, per_employee: int = 5, now=None,
             organization_id=None, employee_id=None) -> dict:
    """Run active employees once. The caller owns commit/rollback.

    `organization_id` / `employee_id` narrow the pass (the Run test button
    uses both). Every organization is isolated: one that raises is counted as
    an error and the pass carries on with the next.
    """
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import queue as wf_queue
    from app.services.workforce import runtime

    summary = {"organizations": 0, "employees": 0, "claimed": 0, "ran": 0,
               "errors": 0, "leases_recovered": 0}

    # CRASH RECOVERY FIRST. A worker replaced mid-run leaves claim tokens on
    # its items; this releases the ones whose lease has lapsed. A scoped pass
    # (a customer's test) does not sweep other tenants' rows.
    if employee_id is None and organization_id is None:
        try:
            summary["leases_recovered"] = wf_queue.reclaim_expired(db, now=now)
        except Exception:                                    # noqa: BLE001
            log.exception("workforce worker: lease recovery failed")
            summary["errors"] += 1

    q = (db.query(AIEmployee)
         .filter(AIEmployee.status == "active",
                 AIEmployee.paused_at.is_(None)))
    if organization_id is not None:
        q = q.filter(AIEmployee.organization_id == organization_id)

    if employee_id is not None:
        for emp in q.filter(AIEmployee.id == employee_id).all():
            summary["organizations"] = 1
            summary["employees"] += 1
            try:
                out = runtime.run_employee(db, emp, limit=per_employee,
                                           now=now)
            except Exception:                                # noqa: BLE001
                log.exception("workforce worker: employee %s failed", emp.id)
                summary["errors"] += 1
                continue
            summary["claimed"] += out.get("claimed", 0)
            summary["ran"] += out.get("ran", 0)
        return summary

    org_ids = [r[0] for r in
               q.with_entities(AIEmployee.organization_id).distinct().all()]
    for org_id in org_ids:
        summary["organizations"] += 1
        try:
            out = runtime.run_organization(db, org_id,
                                           per_employee=per_employee,
                                           now=now)
        except Exception:                                    # noqa: BLE001
            log.exception("workforce worker: organization %s failed", org_id)
            summary["errors"] += 1
            continue
        summary["employees"] += out["employees"]
        for r in out["results"]:
            summary["claimed"] += r.get("claimed", 0)
            summary["ran"] += r.get("ran", 0)
    return summary


def main() -> int:
    from app.deps import SessionLocal
    from app.services.ai_operations import flags
    from app.services.job_run_service import record_job_run_sync

    if not flags.operations_enabled():
        log.info("AI Workforce worker is disabled in this environment "
                 "(AI_OPERATIONS_ENABLED unset or a kill switch is engaged). "
                 "Nothing was done.")
        return 0

    per_employee = int(os.environ.get("AI_WORKFORCE_WORKER_BATCH", "5"))
    with record_job_run_sync(WORKER_JOB_NAME, SessionLocal) as metrics:
        db = SessionLocal()
        try:
            summary = run_pass(db, per_employee=per_employee)
            db.commit()
            metrics.update(summary)
            log.info("AI workforce worker: %s", summary)
            return 1 if summary["errors"] else 0
        except Exception:                                    # noqa: BLE001
            db.rollback()
            log.exception("AI workforce worker failed")
            metrics["errors"] = metrics.get("errors", 0) + 1
            return 1
        finally:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
