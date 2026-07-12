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
from app.services.ai_conversation_service import process_scheduled_touches


def main():
    db = SessionLocal()
    started_at = datetime.now(timezone.utc)
    try:
        result = process_scheduled_touches(db)
        finished_at = datetime.now(timezone.utc)
        summary = {
            "job": "ai_conversation_scheduler",
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": (finished_at - started_at).total_seconds(),
            **result,
        }
        print(json.dumps(summary, indent=2))
        return summary
    except Exception as e:
        summary = {"job": "ai_conversation_scheduler", "error": str(e)}
        print(json.dumps(summary, indent=2))
        return summary
    finally:
        db.close()


if __name__ == "__main__":
    summary = main()
    sys.exit(1 if summary.get("errors", 0) > 0 else 0)
