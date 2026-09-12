"""ADVERSARIAL PRESSURE ON PER-DEVICE SESSIONS.

`test_mobile_sessions.py` proves the FEATURE: a phone and a desktop coexist,
and revocation still ends things. This file attacks it. Every test here is a
way somebody might try to turn "one row per device" into "one more credential
than anybody is counting", or to make a session row say something about
AUTHORITY that it has no business saying.

Three rules are being defended:

  1. A CREDENTIAL IS ONLY AS LIVE AS ITS ROW. Substituting an id, borrowing
     another person's jti, reviving a revoked row, racing a refresh against a
     logout - all of it ends in 401.
  2. DEVICE METADATA IS A LABEL. `X-Device-Id`, `X-Device-Name`,
     `X-Client-Platform` are caller-supplied strings that name a row for a
     human reading a list. They are not identity, they are not authority, and
     colliding them with somebody else's must not touch somebody else's row.
  3. GOD IS GOD, AND NOT BECAUSE OF A HEADER. No combination of client
     platform, device name, app version or session shape promotes anybody.
"""

import threading
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.models.models import User
from app.models.session_models import (REVOKE_LOGOUT, REVOKE_LOGOUT_ALL,
                                       UserSession)
from app.services import session_service
from app.services.auth_service import (ACCESS_TOKEN_PURPOSE, JWT_ALGORITHM,
                                       JWT_SECRET, create_access_token)

PASSWORD = "TestPass123!"

PHONE = {"X-Client-Platform": "ios", "X-Device-Id": "adv-phone",
         "X-Device-Name": "A Phone", "X-App-Version": "1.0.0"}
LAPTOP = {"X-Client-Platform": "web", "X-Device-Id": "adv-laptop",
          "X-Device-Name": "A Laptop", "X-App-Version": "web"}
TABLET = {"X-Client-Platform": "android", "X-Device-Id": "adv-tablet",
          "X-Device-Name": "A Tablet", "X-App-Version": "1.0.0"}


def _bearer(token):
    return {"Authorization": "Bearer %s" % token}


def _login(client, email, headers=None):
    r = client.post("/auth/login",
                    data={"username": email, "password": PASSWORD},
                    headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _alive(client, headers):
    return client.get("/auth/my-contexts", headers=headers).status_code == 200


def _mint(sub, jti, *, purpose=ACCESS_TOKEN_PURPOSE, key=JWT_SECRET,
          algorithm=JWT_ALGORITHM, hours=1, **extra):
    """Sign a token by hand so a claim can be wrong on purpose."""
    payload = {"sub": sub, "jti": jti, "purpose": purpose,
               "exp": datetime.now(timezone.utc) + timedelta(hours=hours)}
    payload.update(extra)
    return jwt.encode(payload, key, algorithm=algorithm)


def _live_rows(db, user_id):
    return session_service.live_sessions_for_user(db, user_id)


# ── 1-5, 15: naming a session that is not yours, or not alive ───────────────

def test_a_deleted_session_row_is_refused(client, db_session, sample_advisor):
    """Not merely revoked - GONE. A row that has been deleted outright must not
    fall through to the legacy column and authenticate anyway."""
    token = _login(client, sample_advisor.email, PHONE)
    db_session.query(UserSession).filter(
        UserSession.user_id == sample_advisor.id).delete()
    sample_advisor.session_token = None
    db_session.commit()
    assert not _alive(client, _bearer(token))


def test_a_jti_that_belongs_to_another_users_session_is_refused(
        client, db_session, sample_advisor, second_advisor):
    """SESSION ID SUBSTITUTION AT THE TOKEN LAYER.

    Advisor Two signs in; advisor One takes Two's jti and signs it under their
    own `sub`. Both halves are individually real. `deps` refuses because the
    row's user_id is not the subject's - the check exists precisely because
    `jti` being unique makes this impossible by accident and therefore only
    ever deliberate.
    """
    _login(client, second_advisor.email, LAPTOP)
    victim_row = _live_rows(db_session, second_advisor.id)[0]
    forged = _mint(sample_advisor.id, victim_row.jti)
    assert not _alive(client, _bearer(forged))


def test_the_victims_session_is_untouched_by_the_attempt(
        client, db_session, sample_advisor, second_advisor):
    theirs = _login(client, second_advisor.email, LAPTOP)
    victim_row = _live_rows(db_session, second_advisor.id)[0]
    _alive(client, _bearer(_mint(sample_advisor.id, victim_row.jti)))
    assert _alive(client, _bearer(theirs)), "a failed forgery ended a good session"


def test_a_jti_naming_nothing_at_all_is_refused(client, sample_advisor):
    assert not _alive(client, _bearer(_mint(sample_advisor.id,
                                            "not-a-session-that-exists")))


def test_a_revoked_session_is_refused_and_stays_refused(
        client, db_session, sample_advisor):
    token = _login(client, sample_advisor.email, PHONE)
    row = _live_rows(db_session, sample_advisor.id)[0]
    session_service.revoke(db_session, row, REVOKE_LOGOUT)
    assert not _alive(client, _bearer(token))
    assert not _alive(client, _bearer(token)), "a retry succeeded on a dead row"


def test_an_expired_row_is_refused_even_with_a_valid_signature(
        client, db_session, sample_advisor):
    """`exp` on the JWT and `expires_at` on the row are two clocks on purpose.
    Shortening the row's expiry must end the session without waiting for the
    signature to age out."""
    token = _login(client, sample_advisor.email, PHONE)
    row = _live_rows(db_session, sample_advisor.id)[0]
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.add(row)
    db_session.commit()
    assert not _alive(client, _bearer(token))


# ── 6-8: the row is live, the person is not ────────────────────────────────

def test_a_deactivated_user_is_refused_while_the_row_is_still_live(
        client, db_session, sample_advisor):
    """A live session must not outlive the account it belongs to. The row is
    deliberately left alone here - deactivation is checked before it."""
    token = _login(client, sample_advisor.email, PHONE)
    assert _alive(client, _bearer(token))
    sample_advisor.is_active = False
    db_session.add(sample_advisor)
    db_session.commit()
    assert not _alive(client, _bearer(token))
    assert _live_rows(db_session, sample_advisor.id), "precondition: row still live"


def test_a_role_change_is_read_from_the_user_not_the_token(
        client, db_session, sample_advisor):
    """STALE ROLE. The JWT carries `role` as a convenience claim. Authority must
    come from the row in `users`, so demoting somebody mid-session must not be
    survivable by holding a token that still says the old thing."""
    token = _login(client, sample_advisor.email, PHONE)
    claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert claims["role"] == "advisor"
    forged = _mint(sample_advisor.id,
                   _live_rows(db_session, sample_advisor.id)[0].jti,
                   role="god_admin", org_id=sample_advisor.organization_id)
    r = client.get("/god/users", headers=_bearer(forged))
    assert r.status_code in (401, 403, 404), (
        "a role claim in the token bought god access: %s" % r.status_code)


# ── 11-14: tokens that are not tokens ──────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "", "   ", "not.a.jwt", "a.b", "....", "Bearer", "eyJhbGciOiJIUzI1NiJ9",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.", "null", "undefined",
])
def test_a_malformed_token_is_refused_without_an_exception(client, bad):
    """Every one of these must be a clean 401. A 500 here is an information
    leak and a denial-of-service handle at the same time."""
    r = client.get("/auth/my-contexts", headers={"Authorization": "Bearer %s" % bad})
    assert r.status_code in (401, 403), "%r produced %s" % (bad, r.status_code)


def test_a_token_signed_with_the_wrong_key_is_refused(client, db_session,
                                                      sample_advisor):
    _login(client, sample_advisor.email, PHONE)
    jti = _live_rows(db_session, sample_advisor.id)[0].jti
    forged = _mint(sample_advisor.id, jti, key="x" * 64)
    assert not _alive(client, _bearer(forged))


def test_an_unsigned_token_is_refused(client, db_session, sample_advisor):
    """alg=none. The classic. A live row and a correct subject must not save a
    token that nobody signed."""
    _login(client, sample_advisor.email, PHONE)
    jti = _live_rows(db_session, sample_advisor.id)[0].jti
    payload = {"sub": sample_advisor.id, "jti": jti,
               "purpose": ACCESS_TOKEN_PURPOSE,
               "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    unsigned = jwt.encode(payload, None, algorithm="none")
    assert not _alive(client, _bearer(unsigned))


@pytest.mark.parametrize("purpose", [None, "", "setup", "access ", "ACCESS",
                                     "refresh", "reset"])
def test_a_wrong_or_missing_purpose_is_refused_even_with_a_live_session(
        client, db_session, sample_advisor, purpose):
    """FAIL CLOSED ON ABSENCE. The live row is the point: this is not the
    signature being wrong, it is a correctly signed credential for some other
    job being pointed at the API."""
    _login(client, sample_advisor.email, PHONE)
    jti = _live_rows(db_session, sample_advisor.id)[0].jti
    payload = {"sub": sample_advisor.id, "jti": jti,
               "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    if purpose is not None:
        payload["purpose"] = purpose
    forged = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert not _alive(client, _bearer(forged))


def test_a_session_cannot_be_named_without_a_jti(client, sample_advisor):
    """No jti, no session, no request - even with a perfect signature and a
    real subject."""
    payload = {"sub": sample_advisor.id, "purpose": ACCESS_TOKEN_PURPOSE,
               "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    forged = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert not _alive(client, _bearer(forged))


# ── 22-23: device metadata is a label, not a claim ─────────────────────────

def test_another_persons_device_id_does_not_revoke_their_session(
        client, db_session, sample_advisor, second_advisor):
    """THE COLLISION ATTACK. `start_session` revokes an install's own previous
    rows so a phone does not accumulate credentials. If that match were not
    scoped by user_id, anybody could end anybody's session by guessing - or
    copying - a device id."""
    theirs = _login(client, second_advisor.email, LAPTOP)
    assert _alive(client, _bearer(theirs))
    _login(client, sample_advisor.email, LAPTOP)      # same X-Device-Id
    assert _alive(client, _bearer(theirs)), (
        "a colliding device id signed somebody else out")
    assert len(_live_rows(db_session, second_advisor.id)) == 1


def test_a_device_id_replaces_only_this_persons_own_row(
        client, db_session, sample_advisor):
    _login(client, sample_advisor.email, PHONE)
    _login(client, sample_advisor.email, PHONE)
    assert len(_live_rows(db_session, sample_advisor.id)) == 1


def test_a_login_with_no_device_id_never_replaces_anything(
        client, db_session, sample_advisor):
    """A browser or a script has nothing to match on. Guessing would revoke the
    wrong row, so it gets its own."""
    _login(client, sample_advisor.email, PHONE)
    _login(client, sample_advisor.email, {})
    _login(client, sample_advisor.email, {})
    assert len(_live_rows(db_session, sample_advisor.id)) == 3


def test_forged_device_metadata_is_stored_as_a_label_and_nothing_more(
        client, db_session, sample_advisor):
    """Claim to be god's own console. It buys a string in a column."""
    token = _login(client, sample_advisor.email, {
        "X-Client-Platform": "god_admin",
        "X-Device-Id": "' OR 1=1 --",
        "X-Device-Name": "role=god_admin;is_superuser=true",
        "X-App-Version": "99.99.99",
    })
    row = _live_rows(db_session, sample_advisor.id)[0]
    assert row.client == "unknown", "an unknown platform was stored verbatim"
    assert row.user_id == sample_advisor.id
    claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert claims["role"] == "advisor"
    assert claims["sub"] == sample_advisor.id


def test_absurd_metadata_is_clipped_rather_than_refused_or_stored_whole(
        client, db_session, sample_advisor):
    """A caller-supplied string that reaches a column gets a ceiling. Refusing
    the login instead would make a label into an availability problem."""
    _login(client, sample_advisor.email, {
        "X-Client-Platform": "ios",
        "X-Device-Id": "d" * 5000,
        "X-Device-Name": "n" * 5000,
        "X-App-Version": "v" * 5000,
    })
    row = _live_rows(db_session, sample_advisor.id)[0]
    assert len(row.device_id) <= 200
    assert len(row.device_name) <= 200
    assert len(row.app_version) <= 50


# ── 24-26: the session endpoint as a place to learn things ─────────────────

@pytest.mark.parametrize("candidate", [
    "00000000-0000-0000-0000-000000000000", "1", "../../etc/passwd",
    "%2e%2e", "null", "*", "' OR '1'='1",
])
def test_a_session_id_that_is_not_yours_is_always_the_same_404(
        client, db_session, sample_advisor, second_advisor, candidate):
    """ENUMERATION. A real-but-other id and a nonsense id must be
    indistinguishable, or the endpoint answers "does this session exist"."""
    mine = _bearer(_login(client, sample_advisor.email, PHONE))
    _login(client, second_advisor.email, LAPTOP)
    real_other = _live_rows(db_session, second_advisor.id)[0].id

    a = client.delete("/auth/sessions/%s" % real_other, headers=mine)
    b = client.delete("/auth/sessions/%s" % candidate, headers=mine)
    assert a.status_code == 404
    assert b.status_code == a.status_code
    if "/" not in candidate and "%2e" not in candidate.lower():
        # Candidates containing a path separator never reach the route at all —
        # the router 404s them first, with its own wording. That difference
        # says nothing about whether any session exists, so it is not the leak
        # this test is about. For everything that DOES reach the endpoint, a
        # real-but-other id and a nonsense id must be indistinguishable.
        assert b.json() == a.json(), "the response distinguished a real id"


def test_a_failed_revocation_leaves_the_other_person_signed_in(
        client, db_session, sample_advisor, second_advisor):
    mine = _bearer(_login(client, sample_advisor.email, PHONE))
    theirs = _bearer(_login(client, second_advisor.email, LAPTOP))
    target = _live_rows(db_session, second_advisor.id)[0].id
    client.delete("/auth/sessions/%s" % target, headers=mine)
    assert _alive(client, theirs)


def test_the_session_list_carries_no_credential_material(
        client, db_session, sample_advisor):
    """Not just "no jti" - no key anywhere in the payload that looks like a
    credential, so a field added later cannot quietly become one."""
    headers = _bearer(_login(client, sample_advisor.email, PHONE))
    _login(client, sample_advisor.email, LAPTOP)
    body = client.get("/auth/sessions", headers=headers).json()
    blob = repr(body).lower()
    for row in _live_rows(db_session, sample_advisor.id):
        assert row.jti not in repr(body), "a live jti was returned"
    for banned in ("jti", "token", "secret", "password", "session_token",
                   "authorization", "refresh"):
        assert banned not in blob, "session list exposed %r" % banned


def test_the_session_list_shows_only_the_callers_own_devices(
        client, sample_advisor, second_advisor):
    mine = _bearer(_login(client, sample_advisor.email, PHONE))
    _login(client, second_advisor.email, LAPTOP)
    _login(client, second_advisor.email, TABLET)
    body = client.get("/auth/sessions", headers=mine).json()
    assert body["count"] == 1
    assert body["sessions"][0]["is_current"] is True


def test_the_session_endpoints_require_authentication(client):
    assert client.get("/auth/sessions").status_code in (401, 403)
    assert client.delete("/auth/sessions/anything").status_code in (401, 403)
    assert client.post("/auth/logout-all").status_code in (401, 403)


# ── 16-21: races, replays and things arriving out of order ─────────────────

def _refresh(client, token):
    return client.post("/auth/refresh", headers=_bearer(token))


def test_a_refresh_cannot_be_replayed(client, sample_advisor):
    """The token that was rotated away is spent. Presenting it again must not
    mint a second live credential from one session."""
    first = _login(client, sample_advisor.email, PHONE)
    assert _refresh(client, first).status_code == 200
    assert _refresh(client, first).status_code == 401


def test_a_replayed_refresh_does_not_create_a_second_row(
        client, db_session, sample_advisor):
    first = _login(client, sample_advisor.email, PHONE)
    _refresh(client, first)
    _refresh(client, first)
    _refresh(client, first)
    assert len(_live_rows(db_session, sample_advisor.id)) == 1


def test_refresh_after_logout_is_refused(client, sample_advisor):
    """LOGOUT/REFRESH RACE. The phone's refresh timer fires just after the
    holder signed that device out. A dead session must not rotate itself back
    into a live one - that is the failure the whole revocation model exists to
    prevent."""
    token = _login(client, sample_advisor.email, PHONE)
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 200
    assert _refresh(client, token).status_code == 401


def test_refresh_after_logout_all_is_refused(client, db_session,
                                             sample_advisor):
    """REVOKE-ALL/REFRESH RACE. "I lost my phone" then the phone refreshes."""
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)
    assert client.post("/auth/logout-all", headers=_bearer(laptop)).status_code == 200
    assert _refresh(client, phone).status_code == 401
    assert _refresh(client, laptop).status_code == 401
    assert _live_rows(db_session, sample_advisor.id) == []


def test_a_logged_out_session_cannot_be_refreshed_into_a_new_row(
        client, db_session, sample_advisor):
    """The dangerous fix for the test above is to fall through and mint a fresh
    session when the row will not rotate. This asserts it does not."""
    token = _login(client, sample_advisor.email, PHONE)
    client.post("/auth/logout", headers=_bearer(token))
    _refresh(client, token)
    assert _live_rows(db_session, sample_advisor.id) == []


def test_refresh_after_a_password_change_is_refused(client, sample_advisor):
    """PASSWORD-RESET/SESSION RACE. Changing the password ends every session;
    a refresh arriving a moment later must not survive it."""
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)
    r = client.post("/auth/change-password", headers=_bearer(laptop),
                    json={"current_password": PASSWORD,
                          "new_password": "AnotherPass456!"})
    assert r.status_code == 200, r.text
    assert _refresh(client, phone).status_code == 401


def test_a_session_revoked_by_id_cannot_refresh(client, db_session,
                                                sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)
    phone_row = [r for r in _live_rows(db_session, sample_advisor.id)
                 if r.device_id == "adv-phone"][0]
    r = client.delete("/auth/sessions/%s" % phone_row.id, headers=_bearer(laptop))
    assert r.status_code == 200, r.text
    assert _refresh(client, phone).status_code == 401
    assert _refresh(client, laptop).status_code == 200


def test_two_logins_in_flight_leave_two_independent_sessions(
        client, db_session, sample_advisor):
    """CONCURRENT LOGIN. Two devices signing in at once is the ordinary case
    now, not a conflict."""
    a = _login(client, sample_advisor.email, PHONE)
    b = _login(client, sample_advisor.email, LAPTOP)
    assert _alive(client, _bearer(a))
    assert _alive(client, _bearer(b))
    assert len(_live_rows(db_session, sample_advisor.id)) == 2


def test_an_interleaved_refresh_cannot_strand_a_live_token(
        client, db_session, sample_advisor):
    """THE REFRESH RACE, REPRODUCED BY HAND.

    Two rotations of one row overlap. The row ends up holding the SECOND jti
    while `users.session_token` ends up holding the FIRST, because the two
    writes landed in opposite orders — which is exactly what concurrent
    requests can do, and what the test below proves with real threads.

    The first token now names no row. Before the fence in
    `session_service.user_has_sessions`, it matched the column and
    authenticated: one session, two live credentials, and revoking the row
    reached only one of them. It must be refused.
    """
    from app.models.models import User as _User

    token_a = _login(client, sample_advisor.email, PHONE)
    row = _live_rows(db_session, sample_advisor.id)[0]
    jti_a = row.jti

    jti_b = session_service.rotate(db_session, row)         # the winner
    assert jti_b != jti_a
    # The losing request's column write lands last — the interleaving.
    db_session.query(_User).filter(_User.id == sample_advisor.id).update(
        {"session_token": jti_a}, synchronize_session=False)
    db_session.commit()

    assert not _alive(client, _bearer(token_a)), (
        "a rotated-away token was resurrected by the legacy column")
    assert len(_live_rows(db_session, sample_advisor.id)) == 1


def test_a_deleted_row_is_not_resurrected_by_the_legacy_column(
        client, db_session, sample_advisor):
    """The same fence, from the other direction. A demo reset deletes
    `user_sessions` rows and the retention sweep will too. Deleting a revoked
    row must not hand its token back."""
    token = _login(client, sample_advisor.email, PHONE)
    _login(client, sample_advisor.email, LAPTOP)     # the user still has a row
    phone_row = [r for r in _live_rows(db_session, sample_advisor.id)
                 if r.device_id == "adv-phone"][0]
    db_session.query(UserSession).filter(
        UserSession.id == phone_row.id).delete()
    sample_advisor.session_token = phone_row.jti     # column still names it
    db_session.commit()
    assert not _alive(client, _bearer(token))


def test_a_genuinely_pre_migration_token_still_works(client, db_session,
                                                     sample_advisor):
    """THE FENCE IS NOT A WALL. The fallback still exists for the case it was
    built for: a token minted before this table shipped, for somebody with no
    rows at all. Tightening it must not turn the next deploy into a forced
    sign-out for the whole customer base."""
    token = create_access_token(sample_advisor, db_session)
    db_session.query(UserSession).filter(
        UserSession.user_id == sample_advisor.id).delete()
    db_session.commit()
    assert _alive(client, _bearer(token))


def test_concurrent_refresh_never_leaves_more_than_one_live_credential(
        db_session, sample_advisor):
    """TRUE CONCURRENCY, AT THE LAYER THAT CAN ACTUALLY BE RACED.

    The TestClient shares ONE SQLAlchemy session across every request, so
    threading HTTP calls races the test harness rather than the code. This
    races the code: eight threads, eight independent sessions against one
    file-backed database, all rotating the SAME row at once.

    Two things must hold afterwards. The session is still ONE row - a race must
    not grow the table. And at most one of the jtis handed back is the row's -
    the others are spent, and the fence in `user_has_sessions` is what stops
    the column handing any of them back.
    """
    import tempfile, os as _os
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm
    from app.models.models import Base, User as _User
    from app.services.auth_service import hash_password as _hash

    path = _os.path.join(tempfile.mkdtemp(), "race.db")
    engine = _ce("sqlite:///%s" % path, connect_args={"timeout": 30})
    Base.metadata.create_all(bind=engine)
    Maker = _sm(autocommit=False, autoflush=False, bind=engine)

    setup = Maker()
    racer = _User(organization_id=None, email="racer@example.com",
                  password_hash=_hash("x"), full_name="Racer", role="advisor",
                  must_change_password=False)
    setup.add(racer)
    setup.commit()
    row = session_service.start_session(setup, racer, client="ios",
                                        device_id="race-device")
    row_id, original_jti, racer_id = row.id, row.jti, racer.id
    setup.close()

    issued, errors = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def go():
        s = Maker()
        try:
            barrier.wait()
            r = s.query(UserSession).filter(UserSession.id == row_id).first()
            new_jti = session_service.rotate(s, r)
            with lock:
                issued.append(new_jti)
        except Exception as exc:                       # noqa: BLE001
            with lock:
                errors.append(repr(exc))
        finally:
            s.close()

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    check = Maker()
    try:
        rows = check.query(UserSession).filter(
            UserSession.user_id == racer_id).all()
        assert len(rows) == 1, "a refresh race grew the session table"
        winner = rows[0].jti
        assert winner != original_jti, "nothing rotated at all"
        assert sum(1 for j in issued if j == winner) <= 1
        live = [j for j in issued if session_service.find_by_jti(check, j)]
        assert len(live) <= 1, (
            "%d jtis from one racing refresh still name a row" % len(live))
    finally:
        check.close()
        engine.dispose()


def test_a_racing_refresh_does_not_outlive_revocation(
        client, db_session, sample_advisor):
    """The consequence that actually matters. Whatever the race produced,
    signing out everywhere must kill all of it."""
    token = _login(client, sample_advisor.email, PHONE)
    tokens = [token]
    for _ in range(3):
        r = client.post("/auth/refresh", headers=_bearer(tokens[-1]))
        if r.status_code == 200:
            tokens.append(r.json()["access_token"])
    live = [t for t in tokens if _alive(client, _bearer(t))]
    assert live, "precondition: something was live"
    client.post("/auth/logout-all", headers=_bearer(live[-1]))
    db_session.expire_all()
    for t in tokens:
        assert not _alive(client, _bearer(t)), (
            "a token survived sign-out-everywhere")


# ── 27-29: authority is not something a client brings with it ──────────────

def test_a_mobile_client_gets_the_same_contexts_as_a_browser(
        client, sample_advisor):
    """THE SERVER DECIDES. Claiming to be an iPhone, an Android tablet or the
    platform console must not change one entry in what the caller may enter."""
    web = _bearer(_login(client, sample_advisor.email, LAPTOP))
    ios = _bearer(_login(client, sample_advisor.email, PHONE))
    android = _bearer(_login(client, sample_advisor.email, TABLET))
    a = client.get("/auth/my-contexts", headers=web).json()
    b = client.get("/auth/my-contexts", headers=ios).json()
    c = client.get("/auth/my-contexts", headers=android).json()
    assert a == b == c


def test_client_headers_on_the_request_do_not_widen_contexts(
        client, sample_advisor):
    headers = _bearer(_login(client, sample_advisor.email, PHONE))
    plain = client.get("/auth/my-contexts", headers=headers).json()
    loud = client.get("/auth/my-contexts", headers=dict(
        headers, **{"X-Client-Platform": "god_admin",
                    "X-Device-Name": "Platform Console",
                    "X-App-Version": "god"})).json()
    assert plain == loud


def test_an_org_override_from_a_non_god_caller_changes_nothing(
        client, db_session, sample_advisor):
    """CROSS-ORG CONTEXT BLEED. X-Org-Override is read only for god_admin.
    Sending it as an advisor must be inert, not merely unauthorised-looking."""
    from app.models.models import Organization
    other = Organization(name="Somebody Else Inc", slug="somebody-else")
    db_session.add(other)
    db_session.commit()
    headers = _bearer(_login(client, sample_advisor.email, PHONE))
    plain = client.get("/auth/my-contexts", headers=headers).json()
    bled = client.get("/auth/my-contexts", headers=dict(
        headers, **{"X-Org-Override": other.id})).json()
    assert plain == bled


def test_a_brand_override_from_a_non_god_caller_changes_nothing(
        client, sample_advisor):
    """CROSS-BRAND CONTEXT BLEED. Same shape, one level up."""
    headers = _bearer(_login(client, sample_advisor.email, PHONE))
    plain = client.get("/auth/my-contexts", headers=headers).json()
    bled = client.get("/auth/my-contexts", headers=dict(
        headers, **{"X-Brand-Override": "some-other-brand"})).json()
    assert plain == bled


def test_a_session_row_confers_nothing(db_session, sample_advisor):
    """The model itself. A row says a credential is live. It has no column an
    authorisation decision could read even if somebody wanted one."""
    create_access_token(sample_advisor, db_session)
    row = _live_rows(db_session, sample_advisor.id)[0]
    public = row.to_public_dict()
    for forbidden in ("role", "is_god", "org_id", "organization_id",
                      "permissions", "scopes", "jti"):
        assert forbidden not in public
    assert not any(c.name in ("role", "permissions", "scopes", "is_admin")
                   for c in UserSession.__table__.columns)


def test_god_authority_is_not_reachable_by_holding_a_gods_jti(
        client, db_session, sample_advisor):
    """GOD REGRESSION. Even the real credential identifier of a god session,
    presented under somebody else's subject, is refused."""
    from app.services.auth_service import hash_password
    god = User(organization_id=None, email="root@evosyspro.live",
               password_hash=hash_password("not-used"),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    create_access_token(god, db_session)
    god_jti = _live_rows(db_session, god.id)[0].jti
    forged = _mint(sample_advisor.id, god_jti, role="god_admin")
    assert not _alive(client, _bearer(forged))
    assert client.get("/god/users", headers=_bearer(forged)).status_code in (
        401, 403, 404)


# ── retention: the table has to stop growing, without ending a session ─────

def _age(db, row, *, revoked_days=None, expired_days=None):
    now = datetime.now(timezone.utc)
    if revoked_days is not None:
        row.revoked_at = now - timedelta(days=revoked_days)
        row.revoked_reason = REVOKE_LOGOUT
    if expired_days is not None:
        row.expires_at = now - timedelta(days=expired_days)
    db.add(row)
    db.commit()


def test_the_sweep_never_deletes_a_live_session(client, db_session,
                                                sample_advisor):
    """The only unforgivable bug in a retention job. Two live sessions, a sweep
    with a retention window of one day, and both must still be signed in."""
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)
    removed = session_service.purge_dead_sessions(db_session, retain_days=1)
    assert removed == 0
    assert _alive(client, _bearer(phone))
    assert _alive(client, _bearer(laptop))


def test_the_sweep_keeps_a_recently_revoked_session(client, db_session,
                                                    sample_advisor):
    """`revoked_reason` is the answer to "why did my phone sign itself out".
    Deleting it the moment it is revoked throws that answer away."""
    _login(client, sample_advisor.email, PHONE)
    row = _live_rows(db_session, sample_advisor.id)[0]
    session_service.revoke(db_session, row, REVOKE_LOGOUT)
    assert session_service.purge_dead_sessions(db_session, retain_days=30) == 0
    assert db_session.query(UserSession).count() == 1


def test_the_sweep_removes_a_long_dead_session(client, db_session,
                                               sample_advisor):
    _login(client, sample_advisor.email, PHONE)
    row = db_session.query(UserSession).first()
    _age(db_session, row, revoked_days=60)
    assert session_service.purge_dead_sessions(db_session, retain_days=30) == 1
    assert db_session.query(UserSession).count() == 0


def test_the_sweep_removes_a_long_expired_session_that_was_never_revoked(
        client, db_session, sample_advisor):
    _login(client, sample_advisor.email, PHONE)
    row = db_session.query(UserSession).first()
    _age(db_session, row, expired_days=90)
    assert session_service.purge_dead_sessions(db_session, retain_days=30) == 1


def test_the_sweep_is_bounded(client, db_session, sample_advisor):
    """A ceiling per pass. A sweep nobody ran for a year must not become one
    enormous DELETE on the authentication table."""
    for i in range(6):
        _login(client, sample_advisor.email, {"X-Device-Id": "dev-%d" % i})
    for row in db_session.query(UserSession).all():
        _age(db_session, row, revoked_days=99)
    assert session_service.purge_dead_sessions(
        db_session, retain_days=30, batch_limit=2) == 2
    assert db_session.query(UserSession).count() == 4


def test_a_swept_session_does_not_come_back_as_a_legacy_token(
        client, db_session, sample_advisor):
    """The sweep and the fence are one change, not two. Deleting a revoked row
    while `users.session_token` still names it must not re-authenticate it."""
    token = _login(client, sample_advisor.email, PHONE)
    _login(client, sample_advisor.email, LAPTOP)
    phone_row = [r for r in db_session.query(UserSession).all()
                 if r.device_id == "adv-phone"][0]
    sample_advisor.session_token = phone_row.jti
    session_service.revoke(db_session, phone_row, REVOKE_LOGOUT)
    _age(db_session, phone_row, revoked_days=99)
    assert session_service.purge_dead_sessions(db_session, retain_days=30) == 1
    assert not _alive(client, _bearer(token))


def test_a_nonsense_retention_window_deletes_nothing(client, db_session,
                                                     sample_advisor):
    """Fail safe on a misconfigured environment variable: 0 or negative days
    must mean "do nothing", not "delete everything"."""
    _login(client, sample_advisor.email, PHONE)
    row = db_session.query(UserSession).first()
    _age(db_session, row, revoked_days=400)
    assert session_service.purge_dead_sessions(db_session, retain_days=0) == 0
    assert session_service.purge_dead_sessions(db_session, retain_days=-5) == 0
    assert session_service.purge_dead_sessions(
        db_session, retain_days=30, batch_limit=0) == 0
    assert db_session.query(UserSession).count() == 1
