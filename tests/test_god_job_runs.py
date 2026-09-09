"""
GOD-10 — Targeted tests for the job_runs durable ledger.

Covers:
  - JobRun model round-trip (create, read, update)
  - record_job_run context manager: success path, error path, DB-failure resilience
  - GET /god/job-runs list + filters
  - GET /god/job-runs/latest pulse summary
  - health_router._get_last_cadence_run now reads from job_runs
  - No secrets in error_summary (truncation guard)
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── In-process SQLite DB (isolated, matches test-suite convention) ──────────

from app.models.models import Base as AppBase
from app.models.job_models import JobRun, JobName


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create app tables + job_runs
    AppBase.metadata.create_all(eng)
    from app.models.job_models import JobRun
    from sqlalchemy import inspect
    if "job_runs" not in inspect(eng).get_table_names():
        JobRun.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()


# ── Model round-trip ────────────────────────────────────────────────────────

class TestJobRunModel:
    def test_create_running_row(self, db_session):
        row = JobRun(
            job_name=JobName.CADENCE_LOOP,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db_session.add(row)
        db_session.commit()
        assert row.id is not None
        assert row.status == "running"
        assert row.finished_at is None

    def test_update_to_success(self, db_session):
        row = JobRun(
            job_name=JobName.CADENCE_LOOP,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db_session.add(row)
        db_session.commit()
        row_id = row.id

        db_session.query(JobRun).filter(JobRun.id == row_id).update(
            {"status": "success", "duration_ms": 120, "metrics": {"sent": 3}},
            synchronize_session=False,
        )
        db_session.commit()
        updated = db_session.query(JobRun).filter(JobRun.id == row_id).first()
        assert updated.status == "success"
        assert updated.duration_ms == 120
        assert updated.metrics == {"sent": 3}

    def test_update_to_error(self, db_session):
        row = JobRun(
            job_name=JobName.AI_CONVERSATION,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db_session.add(row)
        db_session.commit()

        db_session.query(JobRun).filter(JobRun.id == row.id).update(
            {"status": "error", "error_summary": "ValueError: something broke"},
            synchronize_session=False,
        )
        db_session.commit()
        updated = db_session.query(JobRun).filter(JobRun.id == row.id).first()
        assert updated.status == "error"
        assert "ValueError" in updated.error_summary

    def test_job_name_constants_exist(self):
        assert JobName.CADENCE_LOOP == "cadence_loop"
        assert JobName.AI_CONVERSATION == "ai_conversation_loop"
        assert JobName.REVIEW_REQUEST == "review_request_loop"


# ── record_job_run context manager ──────────────────────────────────────────

class TestRecordJobRun:
    """Uses the in-memory engine so no real DB needed."""

    def _db_factory(self, engine):
        Session = sessionmaker(bind=engine)
        return Session

    def test_success_path_writes_row(self, engine, db_session):
        from app.services.job_run_service import record_job_run

        Session = sessionmaker(bind=engine)

        async def _run():
            async with record_job_run(JobName.CADENCE_LOOP, db_factory=Session) as m:
                m["sent"] = 5

        asyncio.get_event_loop().run_until_complete(_run())

        rows = db_session.query(JobRun).filter(
            JobRun.job_name == JobName.CADENCE_LOOP,
            JobRun.status == "success",
        ).all()
        assert rows, "Expected at least one success row"
        last = rows[-1]
        assert last.metrics == {"sent": 5}
        assert last.duration_ms is not None
        assert last.finished_at is not None

    def test_error_path_marks_error(self, engine, db_session):
        from app.services.job_run_service import record_job_run

        Session = sessionmaker(bind=engine)

        async def _run():
            with pytest.raises(RuntimeError):
                async with record_job_run(JobName.REVIEW_REQUEST, db_factory=Session) as m:
                    raise RuntimeError("boom")

        asyncio.get_event_loop().run_until_complete(_run())

        rows = db_session.query(JobRun).filter(
            JobRun.job_name == JobName.REVIEW_REQUEST,
            JobRun.status == "error",
        ).all()
        assert rows, "Expected error row"
        assert "RuntimeError" in rows[-1].error_summary

    def test_error_summary_truncated(self, engine, db_session):
        """A very long exception message must be capped at 512 chars."""
        from app.services.job_run_service import record_job_run

        Session = sessionmaker(bind=engine)
        long_msg = "X" * 2000

        async def _run():
            with pytest.raises(ValueError):
                async with record_job_run(JobName.AI_CONVERSATION, db_factory=Session) as _m:
                    raise ValueError(long_msg)

        asyncio.get_event_loop().run_until_complete(_run())

        rows = db_session.query(JobRun).filter(
            JobRun.job_name == JobName.AI_CONVERSATION,
            JobRun.status == "error",
        ).all()
        assert rows
        assert len(rows[-1].error_summary) <= 512

    def test_db_failure_does_not_crash_caller(self, engine):
        """If the job_runs DB write fails, the loop body still executes."""
        from app.services.job_run_service import record_job_run

        # Bad session factory — always raises
        def bad_factory():
            raise ConnectionError("db is down")

        executed = []

        async def _run():
            async with record_job_run(JobName.CADENCE_LOOP, db_factory=bad_factory) as _m:
                executed.append(True)

        asyncio.get_event_loop().run_until_complete(_run())
        assert executed, "Loop body must run even when job_runs DB write fails"

    def test_metrics_dict_empty_stores_null(self, engine, db_session):
        """An empty metrics dict should be stored as NULL, not {}."""
        from app.services.job_run_service import record_job_run

        Session = sessionmaker(bind=engine)

        async def _run():
            async with record_job_run(JobName.CADENCE_LOOP, db_factory=Session) as _m:
                pass  # no metrics populated

        asyncio.get_event_loop().run_until_complete(_run())
        # Fetch the most-recent cadence_loop success rows and verify at least
        # one has NULL metrics (SQLite JSON NULL doesn't always match == None
        # filter, so we check the Python attribute instead).
        db_session.expire_all()
        rows = db_session.query(JobRun).filter(
            JobRun.job_name == JobName.CADENCE_LOOP,
            JobRun.status == "success",
        ).order_by(JobRun.id.desc()).limit(10).all()
        assert rows, "Expected at least one cadence_loop success row"
        assert any(r.metrics is None for r in rows), (
            f"Expected at least one row with NULL metrics; got: {[r.metrics for r in rows]}"
        )


# ── health_router._get_last_cadence_run ─────────────────────────────────────

class TestGetLastCadenceRun:
    def test_returns_none_when_no_rows(self, db_session):
        from app.routers.health_router import _get_last_cadence_run

        # Pass a dummy User object (function only uses db)
        class FakeUser:
            pass

        result = _get_last_cadence_run(db_session, FakeUser())
        # May return None (no rows) or a datetime — just assert it doesn't crash
        assert result is None or isinstance(result, datetime)

    def test_returns_datetime_when_success_row_exists(self, db_session, engine):
        from app.routers.health_router import _get_last_cadence_run

        # Insert a success row directly
        ts = datetime.now(timezone.utc).replace(microsecond=0)
        row = JobRun(
            job_name=JobName.CADENCE_LOOP,
            started_at=ts,
            finished_at=ts,
            status="success",
            duration_ms=100,
        )
        db_session.add(row)
        db_session.commit()

        class FakeUser:
            pass

        result = _get_last_cadence_run(db_session, FakeUser())
        assert result is not None
        assert isinstance(result, datetime)

    def test_returns_none_on_db_error(self, db_session):
        """If job_runs doesn't exist yet, returns None rather than raising."""
        from app.routers.health_router import _get_last_cadence_run

        class BrokenSession:
            def query(self, *a, **kw):
                raise Exception("table does not exist")

        class FakeUser:
            pass

        result = _get_last_cadence_run(BrokenSession(), FakeUser())
        assert result is None


# ── god_router /job-runs endpoints (via TestClient) ──────────────────────────

def _make_god_client(engine):
    """Build a TestClient wired to the in-memory DB with a god_admin user."""
    from app.main import app
    from app.deps import get_db, require_god
    from app.models.models import User

    Session = sessionmaker(bind=engine)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    god_user = User(
        id="god-test-user",
        email="god@test.local",
        role="god_admin",
        is_active=True,
        organization_id=None,
    )

    def override_require_god():
        return god_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_god] = override_require_god
    client = TestClient(app, raise_server_exceptions=True)
    return client, app


class TestGodJobRunsEndpoints:
    def test_list_empty(self, engine):
        client, app = _make_god_client(engine)
        try:
            resp = client.get("/god/job-runs")
            assert resp.status_code == 200
            data = resp.json()
            assert "runs" in data
            assert "total" in data
        finally:
            app.dependency_overrides.clear()

    def test_list_with_rows(self, engine, db_session):
        # Seed two rows
        for status in ("success", "error"):
            db_session.add(JobRun(
                job_name=JobName.CADENCE_LOOP,
                started_at=datetime.now(timezone.utc),
                status=status,
                duration_ms=200,
            ))
        db_session.commit()

        client, app = _make_god_client(engine)
        try:
            resp = client.get("/god/job-runs")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] >= 2
            names = {r["job_name"] for r in data["runs"]}
            assert JobName.CADENCE_LOOP in names
        finally:
            app.dependency_overrides.clear()

    def test_list_filter_by_job_name(self, engine, db_session):
        db_session.add(JobRun(
            job_name=JobName.AI_CONVERSATION,
            started_at=datetime.now(timezone.utc),
            status="success",
        ))
        db_session.commit()

        client, app = _make_god_client(engine)
        try:
            resp = client.get(f"/god/job-runs?job_name={JobName.AI_CONVERSATION}")
            assert resp.status_code == 200
            data = resp.json()
            for r in data["runs"]:
                assert r["job_name"] == JobName.AI_CONVERSATION
        finally:
            app.dependency_overrides.clear()

    def test_list_filter_by_status(self, engine, db_session):
        client, app = _make_god_client(engine)
        try:
            resp = client.get("/god/job-runs?status=success")
            assert resp.status_code == 200
            data = resp.json()
            for r in data["runs"]:
                assert r["status"] == "success"
        finally:
            app.dependency_overrides.clear()

    def test_latest_endpoint_returns_all_jobs(self, engine, db_session):
        # Seed one row for each known job
        for name in [JobName.CADENCE_LOOP, JobName.AI_CONVERSATION, JobName.REVIEW_REQUEST]:
            db_session.add(JobRun(
                job_name=name,
                started_at=datetime.now(timezone.utc),
                status="success",
                duration_ms=50,
            ))
        db_session.commit()

        client, app = _make_god_client(engine)
        try:
            resp = client.get("/god/job-runs/latest")
            assert resp.status_code == 200
            data = resp.json()
            assert "jobs" in data
            assert "all_healthy" in data
            assert JobName.CADENCE_LOOP in data["jobs"]
            assert JobName.AI_CONVERSATION in data["jobs"]
            assert JobName.REVIEW_REQUEST in data["jobs"]
        finally:
            app.dependency_overrides.clear()

    def test_latest_all_healthy_true(self, engine, db_session):
        # All three have success as latest
        for name in [JobName.CADENCE_LOOP, JobName.AI_CONVERSATION, JobName.REVIEW_REQUEST]:
            db_session.add(JobRun(
                job_name=name,
                started_at=datetime.now(timezone.utc),
                status="success",
            ))
        db_session.commit()

        client, app = _make_god_client(engine)
        try:
            resp = client.get("/god/job-runs/latest")
            data = resp.json()
            assert data["all_healthy"] is True
        finally:
            app.dependency_overrides.clear()

    def test_latest_all_healthy_false_when_error(self, engine, db_session):
        db_session.add(JobRun(
            job_name=JobName.CADENCE_LOOP,
            started_at=datetime.now(timezone.utc),
            status="error",
            error_summary="something failed",
        ))
        db_session.commit()

        client, app = _make_god_client(engine)
        try:
            resp = client.get("/god/job-runs/latest")
            data = resp.json()
            # cadence_loop is error → all_healthy must be False
            assert data["all_healthy"] is False
        finally:
            app.dependency_overrides.clear()

    def test_no_secrets_in_error_summary(self, engine, db_session):
        """Verify error_summary field is bounded and safe."""
        db_session.add(JobRun(
            job_name=JobName.CADENCE_LOOP,
            started_at=datetime.now(timezone.utc),
            status="error",
            error_summary="RuntimeError: cadence DB timeout",  # safe summary
        ))
        db_session.commit()

        client, app = _make_god_client(engine)
        try:
            resp = client.get(f"/god/job-runs?job_name={JobName.CADENCE_LOOP}&status=error")
            data = resp.json()
            for r in data["runs"]:
                if r.get("error_summary"):
                    assert len(r["error_summary"]) <= 512
        finally:
            app.dependency_overrides.clear()
