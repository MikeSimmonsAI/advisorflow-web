"""
run_email_poller.py

Standalone script that polls Microsoft 365 inboxes for all connected
advisors across all organizations, matches incoming emails to leads,
and triggers the AI pipeline for each match.

Designed to be invoked by Render's Cron Job feature every 2 minutes.

USAGE (manual):
    python app/jobs/run_email_poller.py

USAGE (Render Cron Job):
    1. In Render: New > Cron Job
    2. Name: advisorflow-email-poller
    3. Command: python app/jobs/run_email_poller.py
    4. Schedule: */2 * * * *  (every 2 minutes)
    5. Set the same env vars as the main backend:
       DATABASE_URL, ENCRYPTION_KEY, JWT_SECRET,
       MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET

Logs a JSON summary to stdout, which Render captures in Cron Job
run history so failed runs are visible without a separate alerting system.

Non-zero exit code on any errors so Render flags the run as failed.
"""

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.deps import SessionLocal
from app.services.email_poller_service import poll_all_orgs


def main():
    db = SessionLocal()
    started_at = datetime.now(timezone.utc)

    try:
        result = poll_all_orgs(db)
        finished_at = datetime.now(timezone.utc)

        summary = {
            "job": "email_poller",
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": (finished_at - started_at).total_seconds(),
            **result,
        }

        print(json.dumps(summary, indent=2))
        return summary

    except Exception as e:
        error_summary = {
            "job": "email_poller",
            "started_at": started_at.isoformat(),
            "error": str(e),
            "total_errors": 1,
        }
        print(json.dumps(error_summary, indent=2))
        return error_summary

    finally:
        db.close()


if __name__ == "__main__":
    summary = main()
    sys.exit(1 if summary.get("errors", 0) > 0 else 0)
