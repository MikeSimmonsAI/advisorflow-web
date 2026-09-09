"""The job ledger has to be able to report its own absence.

`never_run` used to be ambiguous in the worst possible way. It meant EITHER
"the table is fine and this loop is idle" OR "the table does not exist, so
nothing can ever be recorded" — and the screen rendered both identically.

Both failure paths are silent on purpose: the CREATE TABLE in main.py's startup
only logs on failure, and record_job_run() swallows its own write errors so a
metrics problem can never take down the loop it measures. Two deliberate
swallows in series produce a system that cannot report its own breakage, which
is how a production screenshot ends up contradicting the source code with no
way to tell which one is lying.

These tests pin the distinction.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.job_models import JobName, JobRun
from app.models.models import Base as AppBase


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppBase.metadata.create_all(eng)
    from sqlalchemy import inspect

    if "job_runs" not in inspect(eng).get_table_names():
        JobRun.__table__.create(eng)
    yield eng
    eng.dispose()


def _god_client(engine):
    from app.deps import get_db, require_god
    from app.main import app
    from app.models.models import User

    Session = sessionmaker(bind=engine)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    god = User(id="god-ledger-test", email="god@test.local",
               role="god_admin", is_active=True, organization_id=None)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_god] = lambda: god
    return TestClient(app, raise_server_exceptions=True), app


def _seed(engine, job_name, status="success"):
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        s.add(JobRun(
            job_name=job_name,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            status=status,
            duration_ms=12,
        ))
        s.commit()
    finally:
        s.close()


class TestLedgerSelfReporting:
    def test_healthy_empty_ledger_is_distinguishable_from_missing_one(self, engine):
        """Table present, zero rows: jobs read never_run but the ledger says it is fine."""
        client, app = _god_client(engine)
        try:
            body = client.get("/god/job-runs/latest").json()
            assert body["ledger"]["table_present"] is True
            assert body["ledger"]["total_rows"] == 0
            assert body["ledger"]["error"] is None
            # The jobs still say never_run — that is correct and now unambiguous.
            assert body["jobs"][JobName.CADENCE_LOOP]["status"] == "never_run"
        finally:
            app.dependency_overrides.clear()

    def test_row_counts_are_real_numbers_not_inferred(self, engine):
        client, app = _god_client(engine)
        try:
            _seed(engine, JobName.CADENCE_LOOP)
            _seed(engine, JobName.AI_CONVERSATION)
            body = client.get("/god/job-runs/latest").json()
            led = body["ledger"]
            assert led["total_rows"] == 2
            assert set(led["distinct_jobs"]) == {JobName.CADENCE_LOOP, JobName.AI_CONVERSATION}
            assert led["newest_started_at"] is not None
        finally:
            app.dependency_overrides.clear()

    def test_a_job_that_records_but_is_not_enumerated_is_reported_not_hidden(self, engine):
        """A writer nobody lists is invisible on the screen while healthy in the DB."""
        client, app = _god_client(engine)
        try:
            _seed(engine, "email_poller")
            body = client.get("/god/job-runs/latest").json()

            # The server does return its row — the gap is on the render side:
            # GodJobRuns.jsx draws a card per name in its own KNOWN_JOBS list,
            # so a recording job outside that list is dropped by the screen even
            # though the API reported it. `untracked_jobs` is what lets the UI
            # say "recording but not shown above" instead of silently hiding it.
            assert body["jobs"]["email_poller"]["status"] == "success"
            assert "email_poller" in body["ledger"]["untracked_jobs"]

            # And the enumerated three are unaffected by its presence.
            assert body["jobs"][JobName.CADENCE_LOOP]["status"] == "never_run"
        finally:
            app.dependency_overrides.clear()

    def test_missing_table_reports_itself_instead_of_500ing(self, engine):
        """The case that made the screenshot unexplainable."""
        client, app = _god_client(engine)
        try:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE job_runs"))

            resp = client.get("/god/job-runs/latest")
            assert resp.status_code == 200, "a diagnostic that 500s diagnoses nothing"
            body = resp.json()

            assert body["ledger"]["table_present"] is False
            assert body["ledger"]["error"]
            assert body["all_healthy"] is False
            # Crucially NOT "never_run" — that is the word that used to hide this.
            for name in (JobName.CADENCE_LOOP, JobName.AI_CONVERSATION,
                         JobName.REVIEW_REQUEST):
                assert body["jobs"][name]["status"] == "ledger_unavailable"
        finally:
            app.dependency_overrides.clear()


class TestNoDuplicateRegistration:
    def test_job_run_routes_are_registered_exactly_once(self):
        """Two live copies of one path means the screen is debugged against the
        copy FastAPI never serves."""
        from collections import Counter

        from app.routers import god_router

        seen = Counter()
        for route in god_router.router.routes:
            for method in getattr(route, "methods", set()) or set():
                seen[(method, getattr(route, "path", None))] += 1

        assert seen[("GET", "/god/job-runs")] == 1
        assert seen[("GET", "/god/job-runs/latest")] == 1

    def test_module_symbol_points_at_the_live_handler(self):
        """The dead second copy used to rebind this name at import time."""
        from app.routers import god_router

        live = god_router.get_job_runs_latest
        assert "SUPERSEDED" not in (live.__doc__ or "")
