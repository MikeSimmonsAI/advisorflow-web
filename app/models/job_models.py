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
    JobName.AI_CONVERSATION_CRON,
    JobName.EMAIL_POLLER,
)

LOOP_JOB_NAMES = (
    JobName.CADENCE_LOOP,
    JobName.AI_CONVERSATION,
    JobName.REVIEW_REQUEST,
    JobName.SUPPORT_INTELLIGENCE,
    JobName.SESSION_CLEANUP,
)

ALL_JOB_NAMES = LOOP_JOB_NAMES + CRON_JOB_NAMES


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
