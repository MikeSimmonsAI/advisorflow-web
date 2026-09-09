"""
run_ai_conversation_job.py

Processes scheduled AI conversation touches every 15 minutes.
Sends any cadence emails that are due based on the Day 1/2/4/6/8/10/12/14 schedule.

USAGE (Render Cron Job):
    Command: python app/jobs/run_ai_conversation_job.py
    Schedule: */15 * * * *  (every 15 minutes)
"""

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.deps import SessionLocal
from app.models.job_models import JobName
from app.services.ai_conversation_service import process_scheduled_touches
from app.services.job_run_service import record_job_run_sync


def main():
    db = SessionLocal()
    started_at = datetime.now(timezone.utc)
    try:
        # Recorded under CADENCE-style cron naming, NOT under the in-process
        # ai_conversation_loop name. The two are genuinely different runs: the
        # loop calls process_scheduled_touches per-org every 2 minutes from the
        # web dyno, this cron calls it unscoped every 15. One name for both
        # would read "healthy" whenever either survives.
        with record_job_run_sync(JobName.AI_CONVERSATION_CRON, db_factory=SessionLocal) as m:
            result = process_scheduled_touches(db)
            finished_at = datetime.now(timezone.utc)
            summary = {
                "job": JobName.AI_CONVERSATION_CRON,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": (finished_at - started_at).total_seconds(),
                **result,
            }
            m.update({k: v for k, v in result.items() if isinstance(v, (int, float, str))})
        print(json.dumps(summary, indent=2))
        return summary
    except Exception as e:
        summary = {"job": JobName.AI_CONVERSATION_CRON, "error": str(e)}
        print(json.dumps(summary, indent=2))
        return summary
    finally:
        db.close()


if __name__ == "__main__":
    summary = main()
    sys.exit(1 if summary.get("errors", 0) > 0 or "error" in summary else 0)
