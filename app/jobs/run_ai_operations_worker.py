"""THE AI OPERATIONS WORKER — and the reason it does nothing today.

WHAT IT DOES. One pass over `ai_scheduled_actions` whose time has come:
claim each under a lease, re-run the whole gate chain at THIS instant, act or
refuse, and record either way. It is the only thing in the platform that
makes an AI employee act on a schedule.

WHY IT IS NOT WIRED INTO ANYTHING. Deploying this change starts no new
background work: there is no entry for it in `render.yaml` and no asyncio
loop for it in `app/main.py`. A dark launch that quietly started a worker
would not be a dark launch, and "it only runs when the flag is on" is a
weaker guarantee than "nothing runs it".

FIRST LINE OF DEFENCE, ANYWAY. Even run by hand, this exits immediately
unless AI_OPERATIONS_ENABLED is set — and even then every action it picks up
passes the same authority, eligibility, ownership, cap and idempotency gates
as any other operation, and resolves the simulated provider unless live
sending has been switched on as well.

ACTIVATING IT LATER, HONESTLY: add a Render cron service running
`python app/jobs/run_ai_operations_worker.py` at whatever cadence the
follow-up policy needs (five minutes is the sensible floor — the actions
carry their own due times, so a slower cron delays work rather than dropping
it), and set the environment variables deliberately, one at a time, watching
the operations console between each.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai_operations.worker")


def main() -> int:
    from app.deps import SessionLocal
    from app.services.ai_operations import flags, followup

    if not flags.operations_enabled():
        log.info("AI Operations is disabled in this environment "
                 "(AI_OPERATIONS_ENABLED unset or the kill switch is "
                 "engaged). Nothing was done.")
        return 0

    limit = int(os.environ.get("AI_OPS_WORKER_BATCH", "50"))
    db = SessionLocal()
    try:
        summary = followup.run_due_actions(db, limit=limit)
        db.commit()
        log.info("AI operations worker: %s", summary)
        # A non-empty error list is worth a non-zero exit so a cron's own
        # failure reporting notices, without failing the whole pass for one
        # bad row.
        return 1 if summary.get("errors") else 0
    except Exception:                                        # noqa: BLE001
        db.rollback()
        log.exception("AI operations worker failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
