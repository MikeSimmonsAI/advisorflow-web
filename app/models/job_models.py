"""
Job run ledger (GOD-10).

Platform-level table — no tenant/brand scope, because these background loops
run for all orgs simultaneously. The rows record lifecycle metadata only;
no message bodies, no credentials, no sensitive payloads are stored here.

One row per invocation of each background loop. The loop writes a row when it
starts (status='running'), then updates it to 'success' or 'error' when it
finishes, along with duration_ms and a safe error summary when applicable.

Imported by:
  - app/services/job_run_service.py   (writes rows)
  - app/routers/health_router.py      (reads last_cadence_run)
  - app/routers/god_router.py         (exposes summary to God)
"""

from sqlalchemy import Column, BigInteger, String, DateTime, Integer, JSON
from sqlalchemy.sql import func

from app.models.models import Base


# Canonical job-name constants — avoids magic strings scattered across loops.
class JobName:
    # In-process asyncio loops, started by the FastAPI web service at startup.
    CADENCE_LOOP      = "cadence_loop"
    AI_CONVERSATION   = "ai_conversation_loop"
    REVIEW_REQUEST    = "review_request_loop"
    # Support Intelligence's nightly pass: refresh SLA states, correlate
    # incidents across brands, scan for recurring-issue candidates, write the
    # daily briefs. Named here rather than left anonymous for the reason the
    # email poller comment below records — a loop with no constant is a loop
    # nothing can report on, and the first anyone knows it stopped is a brief
    # that quietly never appeared.
    SUPPORT_INTELLIGENCE = "support_intelligence_loop"
    # Retention sweep for `user_sessions`. Per-device sessions mean the table
    # gains a row per sign-in and keeps every revoked and expired one, so it is
    # the first auth table in this codebase that grows without something
    # deleting from it. Named here for the same reason as the loop above: a
    # sweep nobody can report on is one whose failure is invisible until the
    # table is the problem.
    SESSION_CLEANUP   = "session_cleanup_loop"
    # Customer-facing reminders for brand-sales appointments — the 24-hour and
    # one-hour messages before a Discovery / Demo. Named here for the same
    # reason as the two above: this one sends to a PROSPECT, so a silent stop
    # is a customer who turns up to nothing, or does not turn up at all.
    SALES_REMINDERS   = "sales_reminder_loop"
    # Wholesale Phase 7.1: EvoSense runs every ACTIVE acquisition strategy when
    # it is due (daily by default) and retries seller replies whose reading is
    # pending. Named for the same reason as the loops above: a hunt that spends
    # money must be one the ledger can report on.
    EVOSENSE_HUNT     = "evosense_hunt_loop"
    # Wholesale P6: the hourly exception sweep - raises the work items the data
    # already shows need a person (AI handoffs, missing disposition data, ...).
    # Writes only exception rows; contacts nobody. Named so the ledger can say
    # when it stopped.
    WHOLESALE_EXCEPTIONS = "wholesale_exception_sweep_loop"
    # Location outreach programs: re-alert HOT replies nobody has handled
    # within the program's SLA. Writes alerts only; never contacts a customer.
    PROGRAM_SLA = "program_sla_loop"

    # Render cron SERVICES. Separate names on purpose, even where the work
    # overlaps a loop above: cadence runs hourly in the web dyno AND daily as a
    # cron, and folding both into "cadence_loop" would make the ledger unable to
    # answer the only question worth asking when something stops — WHICH of the
    # two stopped. A shared name reads as healthy for as long as either one
    # survives, which is precisely when you most need to know one has died.
    CADENCE_CRON        = "cadence_cron"
    AI_CONVERSATION_CRON = "ai_conversation_cron"
    EMAIL_POLLER        = "email_poller"


# The cron services, kept beside the constants so a new cron cannot be added
# without a name — the email poller ran every minute for the platform's whole
# life with no constant, no writer and no entry in any reader's list, so it was
# invisible by construction rather than by accident.
CRON_JOB_NAMES = (
    JobName.CADENCE_CRON,
    JobName.EMAIL_POLLER,
)

# RETIRED JOBS: no longer scheduled anywhere, so they are never expected to run
# and are never reported stale or in error. Their historical ledger rows are
# still shown (god_router adds them to known_jobs) so the history is not lost.
#
# ai_conversation_cron (Render cron `advisorflow-ai-conversation`) was suspended
# 2026-09-19 and deliberately removed from render.yaml (see the RETIRED block
# there). The in-process ai_conversation_loop runs the same
# process_scheduled_touches per org every 2 minutes. app/jobs/
# run_ai_conversation_job.py remains a manual one-shot entrypoint and still
# records under this name if someone runs it by hand.
RETIRED_JOB_NAMES = (
    JobName.AI_CONVERSATION_CRON,
)

LOOP_JOB_NAMES = (
    JobName.CADENCE_LOOP,
    JobName.AI_CONVERSATION,
    JobName.REVIEW_REQUEST,
    JobName.SUPPORT_INTELLIGENCE,
    JobName.SESSION_CLEANUP,
    JobName.SALES_REMINDERS,
    JobName.EVOSENSE_HUNT,
    JobName.WHOLESALE_EXCEPTIONS,
    JobName.PROGRAM_SLA,
)

ALL_JOB_NAMES = LOOP_JOB_NAMES + CRON_JOB_NAMES

# HOW OFTEN EACH JOB IS EXPECTED TO RUN, in minutes, taken from the loops'
# sleep() calls in app/main.py and the cron schedules in render.yaml. The
# health endpoint marks a job STALE when its last run is older than twice its
# interval (plus a margin for a deploy restart). "Last run: success" nine days
# ago is not healthy, it is stopped - and without this the screen said success.
EXPECTED_INTERVAL_MINUTES = {
    JobName.CADENCE_LOOP: 60,
    JobName.AI_CONVERSATION: 2,
    JobName.REVIEW_REQUEST: 30,
    JobName.SUPPORT_INTELLIGENCE: 6 * 60,
    JobName.SESSION_CLEANUP: 24 * 60,
    JobName.SALES_REMINDERS: 15,
    JobName.EVOSENSE_HUNT: 15,
    JobName.WHOLESALE_EXCEPTIONS: 60,
    JobName.PROGRAM_SLA: 2,
    JobName.CADENCE_CRON: 24 * 60,        # render.yaml: "0 14 * * *"
    JobName.EMAIL_POLLER: 5,              # render.yaml: "*/5 * * * *"
    # ai_conversation_cron is RETIRED (see RETIRED_JOB_NAMES): no interval, so
    # it is never reported stale.
}
STALE_MARGIN_MINUTES = 30


def is_stale(job_name: str, last_started_at, now) -> bool:
    """True when a job that should have run by now has not."""
    every = EXPECTED_INTERVAL_MINUTES.get(job_name)
    if not every or last_started_at is None:
        return False
    from datetime import timedelta
    return now - last_started_at > timedelta(minutes=2 * every + STALE_MARGIN_MINUTES)


class JobRun(Base):
    """One record per background-loop invocation.

    Columns
    -------
    id            Auto-incrementing surrogate key.
    job_name      JobName constant — which loop fired.
    started_at    Wall-clock UTC when the loop body began.
    finished_at   Wall-clock UTC when the loop body ended (NULL while running).
    status        'running' | 'success' | 'error'
    error_summary Truncated exception string on failure — NO secrets, NO stack
                  frames longer than 512 chars, so it is safe to return via API.
    duration_ms   (finished_at - started_at) in milliseconds; NULL while running.
    metrics       JSONB blob of loop-specific counts (sent, completed, orgs, …).
                  Schema is intentionally loose — each loop adds what it has.
    """
    __tablename__ = "job_runs"

    # Use Integer (not BigInteger) so SQLite test fixtures get autoincrement.
    # Production table is built from the DDL migration (BIGSERIAL) in on_startup,
    # not from create_all, so the column type here only matters for tests.
    id            = Column(Integer, primary_key=True, autoincrement=True)
    job_name      = Column(String(64), nullable=False, index=True)
    started_at    = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    finished_at   = Column(DateTime, nullable=True)
    status        = Column(String(16), nullable=False, default="running")
    error_summary = Column(String(512), nullable=True)
    duration_ms   = Column(Integer, nullable=True)
    metrics       = Column(JSON, nullable=True)
