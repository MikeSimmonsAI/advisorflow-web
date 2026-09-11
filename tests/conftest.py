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
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── THE TEST CLIENT'S WORKER THREAD NEEDS A BIGGER STACK ────────────────────
#
# THE SYMPTOM. Running the full suite on Windows died partway through with
#
#     Windows fatal exception: stack overflow
#
# and a native crash — not a test failure, not a RecursionError, no summary
# line. It landed on a different test each run (test_billing_authorization one
# run, test_billing_plan_change the next), always a few hundred tests in, and
# every one of those files passed when run on its own. That pattern is the
# signature of an exhausted thread stack rather than a defect in any test:
# a test that is genuinely broken fails in isolation too.
#
# WHY IT HAPPENS HERE. Starlette's TestClient does not run the ASGI app in the
# main thread — it runs it in an anyio portal WORKER thread. On Windows a
# worker thread gets a 1 MB stack by default, while the main thread gets 8 MB.
# SQLAlchemy's statement compiler recurses once per node in the expression
# tree, and its frames are large; a compile that is comfortable on the main
# thread's stack can overflow a 1 MB one. Python's recursion limit never
# catches it because the C stack runs out first, which is why there is no
# RecursionError to see.
#
# THE FIX, AND WHAT IT IS NOT. This asks for a larger stack for threads created
# after this point, which is the portal thread. It changes NO assertion, skips
# nothing, and relaxes no gate — the suite runs the same tests it always did.
# It has to be set before the first TestClient is constructed, so it lives at
# conftest import time rather than in a fixture.
#
# WHY 4 MB AND NOT MORE. The first version of this asked for 16 MB and traded
# one failure mode for another: across a full run of ~1,650 tests, each with
# its own TestClient and portal thread, the reservations added up until Windows
# refused with
#
#     RuntimeError: can't start new thread
#
# on two tests near the end — which looked like a billing defect and was not.
# The requirement was only ever "more than the 1 MB default", because that is
# what SQLAlchemy's compiler overflows. Do not raise this to fix an unrelated
# failure - raising it is what caused the second failure mode.
#
# WHY IT CAME DOWN AGAIN, FROM 4 MB TO 2 MB. The suite kept growing and the
# same `can't start new thread` returned - one test, deep in the run, passing
# in isolation. The note written when 4 MB was chosen said this would happen:
# "if the suite grows much further, the stack size will need revisiting rather
# than the tests." It grew, so this is that revision rather than another round
# of trimming tests to fit.
#
# 2 MB is still double the Windows default that overflows, and halves the
# reservation again. The floor is 1 MB - below that the original stack overflow
# comes back - so this is the last halving available. If it returns after that,
# the answer is fewer live portal threads (reusing one TestClient across a
# file's requests), not a smaller stack.
try:                                    # pragma: no cover - platform dependent
    threading.stack_size(2 * 1024 * 1024)
except (ValueError, RuntimeError):      # pragma: no cover
    pass

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-32chars!!")
# A BRANDED host, deliberately.
#
# public_identity.booking_url() refuses infrastructure hosts - *.vercel.app,
# *.onrender.com and friends - because a booking link is the one URL a family
# actually sees, and it must carry the funeral home's name, not the platform's
# hosting provider. This used to be "https://advisorflow-booking.vercel.app",
# which is precisely a host that guard rejects, so booking_url() returned ""
# and every test that built a booking link was asserting against an empty
# string. Two of them failed on it outright; the rest passed VACUOUSLY, since
# `"" in anything` is True. A branded host here means the tests exercise the
# real URL-building path instead of the refusal path.
os.environ.setdefault("BOOKING_BASE_URL", "https://book.restland.com")
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


@pytest.fixture(autouse=True)
def _fresh_rate_limit_allowance():
    """Give every test its own login-throttle budget. BOTH throttles.

    Under TestClient every request in the entire suite comes from one address
    ("testclient"), so anything that counts per client really counts per SUITE.
    Two independent mechanisms do that on the login path and both keep their
    state in module-level memory that no test clears:

      * slowapi, behind app.state.limiter, for the per-address ceiling.
      * auth_router._login_failures, the hand-rolled per-(IP, email) brute-force
        throttle - 10 failures per 15 minutes, and the suite runs in about 11,
        so nothing in a single run ever ages out of that window.

    The second one is the one that actually bit. Tests that deliberately send
    wrong passwords for `sample_advisor` filled its bucket, and five unrelated
    tests much later - all of which log in as that same advisor - got 429 where
    they expected 200 or 401. Every one of them passes alone, which is exactly
    how this kind of coupling hides.

    This is NOT a weakened limit. Neither threshold changes, and the tests that
    assert a throttle exhaust their budget inside one test, which is how it
    behaves in production too. What is removed is one test's failures leaking
    into another test's login.

    tests/test_self_password_change.py has carried a local copy of the slowapi
    half since /auth/change-password was limited; that copy is now redundant
    rather than wrong.
    """
    from app.main import app
    from app.routers import auth_router

    def _clear():
        limiter = getattr(app.state, "limiter", None)
        if limiter is not None:
            try:
                limiter.reset()
            except Exception:
                pass
        try:
            with auth_router._login_lock:
                auth_router._login_failures.clear()
        except Exception:
            pass

    _clear()
    yield
    # Also on the way out, so a test that fills a bucket does not hand it to
    # whatever runs next even if that test failed part-way through.
    _clear()


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

    THE ENGINE IS DISPOSED, NOT JUST THE SESSION - AND THAT IS LOAD-BEARING.

    StaticPool keeps ONE SQLite connection open for the life of the engine, and
    with sqlite:///:memory: that connection IS the database: close it and the
    whole thing is freed, keep it and every table and row this test created
    stays resident. Closing only the session left the engine - and therefore
    that entire in-memory database, ~150 tables of schema plus its data - alive
    for the remainder of the run, once per test.

    Across a 1700-test suite that is a genuine leak, and it had already started
    costing real runs: pytest died with MemoryError in the last tenth of the
    suite, taking a dozen unrelated tests with it, and the failures moved around
    between runs the way memory-pressure failures do. Every one of those tests
    passes alone. Disposing here is not a workaround for that - it is the thing
    that was missing.
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
    try:
        yield session
    finally:
        session.close()
        # Releases the StaticPool connection and with it the in-memory database.
        engine.dispose()


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


@pytest.fixture(autouse=True)
def no_real_twilio_calls(monkeypatch):
    """THE TEST SUITE MUST NEVER REACH api.twilio.com.

    This is not hypothetical. sms_service.send_sms used to resolve its client
    through get_twilio_client(); commit ee051e7 moved it to
    _resolve_twilio_creds() for the org-shared-number work, and two tests in
    test_message_review_flow.py went on patching the old name. Nothing failed
    loudly - the patch simply stopped intercepting anything, and every run of
    those tests made a real outbound HTTPS request to Twilio with the fake
    credentials above, got a 401, and reported it as a skipped send. A stale
    mock that silently becomes a live network call is the worst failure mode
    available here: on a real account it would be real messages to real
    families, and the only symptom was an assertion about a count.

    So the construction of a Twilio REST client is refused at the source. A
    test that needs to exercise sending patches the resolver (or send_sms
    itself) deliberately; a test that reaches this has an out-of-date patch
    target and gets told so by name instead of quietly going to the network.
    """
    def _refuse(*args, **kwargs):
        raise RuntimeError(
            "A test tried to construct a real Twilio REST client. Nothing in "
            "this suite may talk to Twilio. Patch "
            "app.services.sms_service._resolve_twilio_creds (which is what "
            "send_sms/send_mms actually call) rather than an older helper."
        )

    try:
        import twilio.rest
        monkeypatch.setattr(twilio.rest, "Client", _refuse)
    except ImportError:
        # Twilio not installed in this environment - nothing to guard.
        pass


@pytest.fixture()
def sample_org(db_session):
    from app.utils.crypto import encrypt_value
    # `industry` is STATED, not inherited. It used to come from the column
    # default, which was "funeral" — so every organization in the platform
    # silently claimed to be one. The default is neutral now, and a funeral
    # home says so, here and everywhere else.
    org = Organization(name="Restland Cemetery & Funeral Home", slug="restland", plan="standard",
                       industry="funeral",
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
def import_auth_headers(db_session, sample_org, sample_advisor):
    """An advisor who actually HOLDS the two lead-import capabilities.

    POST /leads/upload/confirm requires both lead_import_stage and
    lead_import_commit (the legacy adapter performs both operations on the
    caller's behalf). A plain advisor holds neither and correctly gets 403 -
    that gate is deliberate and must not be weakened to make a test pass, so
    the test grants the capability instead of the route dropping it.

    Grants rather than promotion to org_admin, deliberately: role escalation
    would also hand the fixture every other admin power and quietly stop these
    tests from proving anything about import authorization specifically.
    """
    from app.services.auth_service import create_access_token
    from app.models.models import UserCapabilityGrant

    # scope_type/scope_id are set explicitly rather than left to the column
    # default. capabilities.grants_for() filters on scope_type == customer_org
    # and NULL never means global here, so a grant written without its scope
    # is not a permissive grant - it is an inert one, and a fixture that
    # silently produced inert grants would make these tests pass or fail for
    # reasons unrelated to what they check.
    for key in ("lead_import_stage", "lead_import_commit"):
        db_session.add(UserCapabilityGrant(
            user_id=sample_advisor.id,
            organization_id=sample_org.id,
            scope_type="customer_org",
            scope_id=sample_org.id,
            capability=key,
            is_active=True,
        ))
    db_session.commit()

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
