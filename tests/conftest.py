"""
Shared pytest fixtures for the AdvisorFlow backend test suite.

Each test gets a fresh in-memory SQLite database, so tests never leak
state into each other and never touch a real database. This matters a
lot now that 5 advisors are about to start using the real system -
these tests are the safety net that catches a regression before it
ships, not after someone's real leads get mishandled.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("BOOKING_BASE_URL", "https://advisorflow-booking.vercel.app")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, Organization, User, Lead, LeadTier, LeadStatus, MessageTrack
from app.services.auth_service import hash_password


@pytest.fixture()
def db_session():
    """
    Fresh in-memory SQLite DB per test - fully isolated, no shared state
    between tests.

    StaticPool is required here: plain sqlite:///:memory: gives each new
    connection a SEPARATE, empty in-memory database. Without StaticPool,
    the router-level tests (which go through FastAPI's TestClient and may
    check out a new connection per request) would silently hit a
    different, table-less database than the one this fixture set up -
    this was caught for real during testing ("no such table: users")
    before adding StaticPool fixed it.
    """
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def sample_org(db_session):
    org = Organization(name="Restland Cemetery & Funeral Home", slug="restland", plan="standard")
    db_session.add(org)
    db_session.commit()
    from app.services.tier_config_service import seed_default_tier_definitions
    seed_default_tier_definitions(db_session, org.id)
    return org


@pytest.fixture()
def sample_advisor(db_session, sample_org):
    advisor = User(
        organization_id=sample_org.id,
        email="advisor1@restland.com",
        password_hash=hash_password("TestPass123!"),
        full_name="Advisor One",
        role="advisor",
        twilio_phone_number="+12145551111",
    )
    db_session.add(advisor)
    db_session.commit()
    return advisor


@pytest.fixture()
def second_advisor(db_session, sample_org):
    advisor = User(
        organization_id=sample_org.id,
        email="advisor2@restland.com",
        password_hash=hash_password("TestPass123!"),
        full_name="Advisor Two",
        role="advisor",
        twilio_phone_number="+12145552222",
    )
    db_session.add(advisor)
    db_session.commit()
    return advisor


@pytest.fixture()
def sample_lead(db_session, sample_org, sample_advisor):
    lead = Lead(
        organization_id=sample_org.id,
        assigned_to_id=sample_advisor.id,
        first_name="Jane",
        last_name="Doe",
        phone="12145559999",
        email="jane@example.com",
        tier=LeadTier.PRE_NEED,
        message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.commit()
    return lead


@pytest.fixture()
def real_restland_file():
    """Path to the real Restland CRM export used throughout testing."""
    path = "/mnt/user-data/uploads/All_Active_Leads__2012_.xlsx"
    if not os.path.exists(path):
        pytest.skip("Real Restland test file not available in this environment")
    return path


@pytest.fixture()
def client(db_session):
    """
    FastAPI TestClient wired to the SAME isolated in-memory db_session
    used by every other fixture, via dependency override on get_db.
    Without the override, the app would try to open its own real
    database connection per the DATABASE_URL env var, which is wrong
    in a test context and disconnected from the data set up by other
    fixtures (sample_org, sample_advisor, etc.).
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.deps import get_db

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_headers(sample_advisor):
    """Authorization header for sample_advisor, for hitting protected routes."""
    from app.services.auth_service import create_access_token
    token = create_access_token(sample_advisor)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_auth_headers(db_session, sample_org):
    """
    Authorization header for an org_admin account, for hitting routes
    restricted to admin/super_admin roles (e.g. /admin, /templates).
    """
    from app.services.auth_service import create_access_token, hash_password
    from app.models.models import User

    admin = User(
        organization_id=sample_org.id, email="admin@restland.com",
        password_hash=hash_password("AdminPass123!"), full_name="Org Admin", role="org_admin",
    )
    db_session.add(admin)
    db_session.commit()
    token = create_access_token(admin)
    return {"Authorization": f"Bearer {token}"}
