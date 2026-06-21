"""
run_cadence_job.py

Standalone script that runs the daily cadence job across ALL
organizations - the real production replacement for clicking
"Run due touches now" on the admin dashboard every day.

This is designed to be invoked by Render's Cron Job feature (or any
scheduler - cron, Railway's scheduled jobs, etc.), NOT run inside the
web server process itself. Background jobs and web requests should be
separate processes so a slow cadence run never blocks someone trying
to load a page.

USAGE (manual):
    python app/jobs/run_cadence_job.py

USAGE (Render Cron Job):
    1. In Render: New > Cron Job
    2. Command: python app/jobs/run_cadence_job.py
    3. Schedule: 0 14 * * *   (runs daily at 2pm UTC = 9am Eastern,
       adjust for your timezone and when advisors are actually working)
    4. Set the same environment variables as the main backend service
       (DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, BOOKING_BASE_URL, etc.)

This script logs a summary to stdout, which Render captures in the
Cron Job's run history - so a failed run is visible without needing
a separate alerting system.
"""

import os
import sys
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.deps import SessionLocal
from app.models.models import Organization
from app.services.cadence_service import run_due_cadences


def run_for_all_organizations():
    """
    Runs the due-cadence job once per organization, so a slow or failing
    run for one org (e.g. Restland) doesn't block or get mixed up with
    another org's results once North Star Memorial Group or other
    customers are added later.
    """
    db = SessionLocal()
    started_at = datetime.now(timezone.utc)

    try:
        orgs = db.query(Organization).filter(Organization.is_active == True).all()
        overall_summary = {
            "started_at": started_at.isoformat(),
            "organizations_processed": 0,
            "total_sent": 0,
            "total_completed": 0,
            "total_errors": 0,
            "per_org": {},
        }

        for org in orgs:
            try:
                result = run_due_cadences(db, organization_id=org.id)
                overall_summary["per_org"][org.slug] = result
                overall_summary["total_sent"] += result["sent"]
                overall_summary["total_completed"] += result["completed"]
                overall_summary["total_errors"] += result["errors"]
                overall_summary["organizations_processed"] += 1
            except Exception as e:
                # One organization's failure should never stop the others
                # from running - log it and keep going.
                overall_summary["per_org"][org.slug] = {"error": str(e)}
                overall_summary["total_errors"] += 1

            # Engagement temperature recompute - catches the time-based
            # COLD transition (30+ days no reply) that no single event
            # would otherwise trigger. Isolated in its own try/except so
            # a failure here never counts against the cadence run above
            # or blocks other organizations.
            try:
                from app.services.engagement_service import recompute_for_organization
                temp_counts = recompute_for_organization(db, org.id)
                overall_summary["per_org"][org.slug]["engagement_temperature"] = temp_counts
            except Exception as e:
                overall_summary["per_org"].setdefault(org.slug, {})["engagement_temperature_error"] = str(e)

        finished_at = datetime.now(timezone.utc)
        overall_summary["finished_at"] = finished_at.isoformat()
        overall_summary["duration_seconds"] = (finished_at - started_at).total_seconds()

        print(json.dumps(overall_summary, indent=2))
        return overall_summary

    finally:
        db.close()


if __name__ == "__main__":
    summary = run_for_all_organizations()
    # Non-zero exit code if there were errors, so Render's Cron Job
    # dashboard flags the run as failed and you actually notice.
    sys.exit(1 if summary["total_errors"] > 0 else 0)
