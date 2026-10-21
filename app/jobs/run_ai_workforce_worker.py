"""THE AI WORKFORCE QUEUE WORKER (T6) - the consumer the platform was missing.

WHAT IT DOES. One pass over every active, unpaused AI employee: claim due work
items under a lease and run each one through `runtime.run_employee`, which
re-resolves the employee's activation stage first. An employee that is not in
a stage that may run (off, killed, simulation-only) claims nothing.

WHY IT EXISTS. `run_ai_operations_worker.py` drains T7's `ai_scheduled_actions`
only. Before this file the only callers of the T6 claim/run path were the
God Mode "run" endpoint and the simulator, so a queued work item was never
picked up by anything on a schedule.

NOT WIRED INTO ANYTHING. No `render.yaml` entry and no loop in `app/main.py`:
deploying this starts no background work. Even run by hand it exits at once
unless AI_OPERATIONS_ENABLED is set and no kill switch is engaged, and the
per-employee activation stage still decides whether anything is claimed.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai_workforce.worker")


def run_pass(db, *, per_employee: int = 5) -> dict:
    """Run every active employee once. The caller owns commit/rollback."""
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import runtime

    org_ids = [r[0] for r in
               db.query(AIEmployee.organization_id)
               .filter(AIEmployee.status == "active",
                       AIEmployee.paused_at.is_(None))
               .distinct().all()]
    summary = {"organizations": 0, "employees": 0, "claimed": 0, "ran": 0,
               "errors": 0}
    for org_id in org_ids:
        summary["organizations"] += 1
        try:
            out = runtime.run_organization(db, org_id,
                                           per_employee=per_employee)
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

    if not flags.operations_enabled():
        log.info("AI Workforce worker is disabled in this environment "
                 "(AI_OPERATIONS_ENABLED unset or a kill switch is engaged). "
                 "Nothing was done.")
        return 0

    per_employee = int(os.environ.get("AI_WORKFORCE_WORKER_BATCH", "5"))
    db = SessionLocal()
    try:
        summary = run_pass(db, per_employee=per_employee)
        db.commit()
        log.info("AI workforce worker: %s", summary)
        return 1 if summary["errors"] else 0
    except Exception:                                        # noqa: BLE001
        db.rollback()
        log.exception("AI workforce worker failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
