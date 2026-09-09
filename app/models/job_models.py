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
    CADENCE_LOOP      = "cadence_loop"
    AI_CONVERSATION   = "ai_conversation_loop"
    REVIEW_REQUEST    = "review_request_loop"


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
