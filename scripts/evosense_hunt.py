"""EvoSense hunts from the command line — operations, testing, debugging.

AUTOMATIC HUNTING DOES NOT NEED THIS SCRIPT (Phase 7.1). The backend process
(`SERVICE_ROLE=backend`) runs `_evosense_hunt_loop` every 15 minutes through
the platform's own loop machinery and job ledger. This script is for a human
who wants to do by hand what that loop does:

    python scripts/evosense_hunt.py              ONE scheduler pass — exactly the
                                                 loop's body: every due strategy,
                                                 kill switches, locks, pending
                                                 replies. Recorded in job_runs.
    python scripts/evosense_hunt.py --org <id>   the same, one organization
    python scripts/evosense_hunt.py --all-now    hunt every active strategy now,
                                                 due or not (still locked, still
                                                 kill-switch aware)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import app.models.registry  # noqa: E402,F401  (every table)
from app.deps import SessionLocal  # noqa: E402
from app.models.job_models import JobName  # noqa: E402
from app.services.evosense import common as C  # noqa: E402
from app.services.evosense import hunt as HU  # noqa: E402
from app.services.evosense import scheduler as SCH  # noqa: E402
from app.services.job_run_service import record_job_run_sync  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EvoSense hunts")
    ap.add_argument("--org", help="only this organization id")
    ap.add_argument("--all-now", action="store_true", help="hunt every active strategy now")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        if args.all_now:
            runs = HU.run_all(db, trigger="manual", org_id=args.org)
            for r in runs:
                print(json.dumps({"run": r.id, "strategy": r.strategy_id, "status": r.status,
                                  "error": r.error, "counts": C.jload(r.counts, {})}))
            return 0 if all(r.status != "failed" for r in runs) else 1
        with record_job_run_sync(JobName.EVOSENSE_HUNT, db_factory=SessionLocal) as metrics:
            report = SCH.run_due(db, org_id=args.org)
            metrics.update(report)
        print(json.dumps(report))
        return 0 if not report.get("failed") else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
