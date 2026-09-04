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

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-32chars!!")
os.environ.setdefault("BOOKING_BASE_URL", "https://advisorflow-booking.vercel.app")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")
if "ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, Organization, User, Lead, LeadTier, LeadStatus, MessageTrack
# EVERY model module, on the same Base, before any create_all() below.
#
# models.py alone is not the schema. `proposals` is declared there but has
# foreign keys into `opportunities` and `brand_sales_orgs`, which live in
# sales_models.py - so a metadata built from models.py alone cannot resolve
# them, and create_all() raised NoReferencedTableError during fixture setup.
# app/models/registry.py is the same list app/main.py uses; importing it here
# is what makes the test schema match the application's.
import app.models.registry  # noqa: F401  (imported for side effects)
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


# ── Twilio in tests ─────────────────────────────────────────────────────────
#
# These are FAKE and deterministic. Nothing here reaches Twilio: the SID is a
# well-formed placeholder and the token exists only so a signature can be
# computed and verified locally.
#
# They live on the ORGANIZATION because that is where production keeps them.
# sms_service._resolve_twilio_creds resolves an advisor's assigned NUMBER
# against the ORG's account, and twilio_webhook_guard resolves an inbound
# webhook's AccountSid the same way. A test org with no credentials could
# therefore neither send nor receive, which is what several SMS tests were
# actually failing on - not on the behaviour they were written to check.
TEST_TWILIO_ACCOUNT_SID = "ACtest00000000000000000000000000"
TEST_TWILIO_AUTH_TOKEN = "test-auth-token-not-a-real-secret"
# The organization's SHARED sending number, stored in E.164 exactly as the
# settings screen and the A2P flow store it. This is the configuration a
# funeral home whose advisors do not each carry a number actually runs, and
# it is the one the inbound webhook has to be able to resolve.
TEST_ORG_TWILIO_NUMBER = "+19998887777"


@pytest.fixture()
def sample_org(db_session):
    from app.utils.crypto import encrypt_value
    org = Organization(name="Restland Cemetery & Funeral Home", slug="restland", plan="standard",
                       org_twilio_account_sid=TEST_TWILIO_ACCOUNT_SID,
                       org_twilio_auth_token_encrypted=encrypt_value(TEST_TWILIO_AUTH_TOKEN),
                       org_twilio_phone_number=TEST_ORG_TWILIO_NUMBER)
    db_session.add(org)
    db_session.commit()
    # A PROVISIONED ORGANIZATION HAS ITS TIER DEFINITIONS.
    #
    # TierDefinition rows are data, not code: tier_definitions_router seeds them
    # per organization from the industry default set, and validate_tier_key /
    # get_tone_context_for_track read them at runtime. A test org with none is
    # not a realistic organization - it is one that was never provisioned - and
    # the tier tests were failing on that absence rather than on the behaviour
    # they were written to check. "funeral" is Restland's own set, all eight.
    from app.services.tier_config_service import seed_default_tier_definitions
    seed_default_tier_definitions(db_session, org.id, industry="funeral")
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
        must_change_password=False,
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
        must_change_password=False,
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
def auth_headers(db_session, sample_advisor):
    """Authorization header for sample_advisor, for hitting protected routes."""
    from app.services.auth_service import create_access_token
    token = create_access_token(sample_advisor, db_session)
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
        must_change_password=False,
    )
    db_session.add(admin)
    db_session.commit()
    token = create_access_token(admin, db_session)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Signed Twilio webhooks.
#
# app/utils/twilio_webhook_guard.py authenticates every inbound webhook per
# ACCOUNT and fails closed: no AccountSid, no signature, or a signature that
# does not verify all mean 403 with zero side effects. That is deliberate
# production hardening - a forged POST must not be able to create a Reply,
# stop a cadence or add a DNC entry - so the tests sign their requests
# instead of the guard being relaxed for them.
#
# The signature is computed here from Twilio's published algorithm rather
# than by importing the app's own helper, so a bug in that helper still fails
# these tests instead of being cancelled out on both sides.
# ---------------------------------------------------------------------------

def _twilio_signature(auth_token: str, url: str, params: dict) -> str:
    """HMAC-SHA1 over url + each key and value concatenated in key order."""
    import base64, hashlib, hmac
    payload = url + "".join("%s%s" % (k, v) for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"),
                      hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


@pytest.fixture()
def twilio_webhook(client):
    """POST a correctly-signed Twilio webhook, the way Twilio would.

    AccountSid defaults to the test organization's, so the guard resolves the
    org credential and verifies against the same token. Pass account_sid /
    auth_token explicitly to exercise the refusal paths.

    The signed URL is https://testserver<path>: the guard rebuilds candidate
    URLs from server-controlled values (the Host header, https) rather than
    trusting the request's own scheme."""
    def _post(path, data=None, account_sid=TEST_TWILIO_ACCOUNT_SID,
              auth_token=TEST_TWILIO_AUTH_TOKEN, sign=True):
        payload = dict(data or {})
        if account_sid is not None:
            payload.setdefault("AccountSid", account_sid)
        headers = {}
        if sign:
            headers["X-Twilio-Signature"] = _twilio_signature(
                auth_token, "https://testserver" + path, payload)
        return client.post(path, data=payload, headers=headers)
    return _post
