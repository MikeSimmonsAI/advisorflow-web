"""
Tests for app/jobs/run_cadence_job.py

Verifies the multi-org runner correctly isolates failures per
organization, and that the summary structure matches what Render's
Cron Job logging would actually capture.

IMPORTANT: run_for_all_organizations() opens its own database session
via SessionLocal() (it's a standalone script, not running inside a
request context where get_db() would apply). To test it against the
same isolated in-memory database the other fixtures use, we patch
SessionLocal to return a callable that yields THIS test's db_session
instead of opening a real connection - otherwise the job script would
try to talk to whatever real DATABASE_URL is configured in the
environment, which is wrong in a test context and was confirmed to
fail with "no such table" before this fix.
"""

from unittest.mock import patch
from app.jobs.run_cadence_job import run_for_all_organizations
from app.models.models import Organization, User, Lead, LeadStatus
from app.services.auth_service import hash_password
from app.services.cadence_service import start_cadence


def _patched_run(db_session):
    """
    Returns a context manager that makes run_for_all_organizations()
    use the test's db_session instead of opening a new real connection,
    and prevents it from closing that shared session afterward (since
    pytest's fixture teardown owns that lifecycle, not the job script).
    """
    class _FakeSessionLocal:
        def __call__(self):
            return db_session

    patcher = patch("app.jobs.run_cadence_job.SessionLocal", _FakeSessionLocal())
    return patcher


def test_runs_with_no_organizations(db_session):
    """Empty database shouldn't crash - just reports zero orgs processed."""
    with _patched_run(db_session), patch.object(db_session, "close"):
        summary = run_for_all_organizations()
    assert summary["organizations_processed"] == 0
    assert summary["total_errors"] == 0


def test_one_organizations_failure_does_not_block_another(db_session):
    """
    If org A's cadence run throws for some reason, org B should still
    get processed - failures must be isolated per organization.
    """
    org_a = Organization(name="Org A", slug="org-a", plan="trial", is_active=True)
    org_b = Organization(name="Org B", slug="org-b", plan="trial", is_active=True)
    db_session.add_all([org_a, org_b])
    db_session.commit()

    advisor_b = User(organization_id=org_b.id, email="b@test.com", password_hash=hash_password("x"),
                      full_name="Advisor B", role="advisor", twilio_phone_number="+12145551111")
    db_session.add(advisor_b)
    db_session.commit()

    lead_b = Lead(organization_id=org_b.id, assigned_to_id=advisor_b.id, first_name="Lead", last_name="B",
                   phone="12145559999", status=LeadStatus.NEW)
    db_session.add(lead_b)
    db_session.commit()
    start_cadence(db_session, lead_b)

    with _patched_run(db_session), patch.object(db_session, "close"):
        with patch("app.jobs.run_cadence_job.run_due_cadences") as mock_run:
            def side_effect(db, organization_id):
                if organization_id == org_a.id:
                    raise Exception("Simulated failure for org A")
                return {"evaluated": 1, "sent": 1, "completed": 0, "errors": 0, "error_details": []}

            mock_run.side_effect = side_effect
            summary = run_for_all_organizations()

    assert summary["organizations_processed"] == 1  # only org B succeeded
    assert "error" in summary["per_org"]["org-a"]
    assert summary["per_org"]["org-b"]["sent"] == 1
    assert summary["total_errors"] == 1


def test_inactive_organizations_are_skipped(db_session):
    inactive_org = Organization(name="Inactive", slug="inactive", plan="trial", is_active=False)
    db_session.add(inactive_org)
    db_session.commit()

    with _patched_run(db_session), patch.object(db_session, "close"):
        summary = run_for_all_organizations()
    assert "inactive" not in summary["per_org"]
    assert summary["organizations_processed"] == 0


def test_summary_includes_timing_fields(db_session):
    with _patched_run(db_session), patch.object(db_session, "close"):
        summary = run_for_all_organizations()
    assert "started_at" in summary
    assert "finished_at" in summary
    assert "duration_seconds" in summary
    assert summary["duration_seconds"] >= 0
