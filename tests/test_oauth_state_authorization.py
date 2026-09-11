"""THE OAUTH CALLBACK MUST NOT LET THE BROWSER NAME THE ACCOUNT.

THE CONFIRMED DEFECT THESE GUARD. `/calendar/oauth/callback` and
`/microsoft/oauth/callback` used to read the advisor's user_id straight out of
the OAuth `state` parameter — raw, or behind a "setup:" prefix — and store the
resulting refresh token against it. Reproduced independently with synthetic
users: a valid provider authorization, redirected back with somebody else's id
in `state`, overwrote that person's stored integration credentials. Their
calendar writes and their outbound mail would then have run through an
attacker-controlled grant.

WHAT THE TESTS ASSERT. That the identity comes from a server-side transaction
row written at initiation, and that the row is expiring, single-use, bound to
one provider and one tenant, and useless if tampered with. Six attacks, plus
the legitimate flows that must keep working — because the cheap way to pass
every attack test is to break the connect button, and that is not a fix.

NOTHING HERE REACHES GOOGLE OR MICROSOFT. The token exchange is replaced with a
recorder, so each test can assert WHOSE record the grant would have been
written to. That is the actual question the defect was about.
"""

import pytest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from app.models.models import Organization, User
from app.models.oauth_models import (FLOW_SETTINGS, FLOW_SETUP,
                                     PROVIDER_GOOGLE, PROVIDER_MICROSOFT,
                                     OAuthAuthorizationTransaction)
from app.services import oauth_state_service
from app.services.auth_service import create_access_token, hash_password
from app.services.oauth_state_service import OAuthStateError

GOOGLE_CALLBACK = "/calendar/oauth/callback"
MS_CALLBACK = "/microsoft/oauth/callback"


# ── recorders: who would the grant have been stored against? ────────────────

class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, db, advisor_user_id=None, **kwargs):
        self.calls.append(advisor_user_id)
        return None

    @property
    def subject(self):
        return self.calls[-1] if self.calls else None


@pytest.fixture()
def google_exchange(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr("app.routers.calendar_router.handle_oauth_callback", rec)
    return rec


@pytest.fixture()
def microsoft_exchange(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(
        "app.routers.microsoft_router.handle_microsoft_oauth_callback", rec)
    return rec


@pytest.fixture()
def microsoft_configured(monkeypatch):
    monkeypatch.setattr(
        "app.services.microsoft_email_service.MICROSOFT_CLIENT_ID", "test-client")
    monkeypatch.setattr(
        "app.services.microsoft_email_service.MICROSOFT_CLIENT_SECRET", "test-secret")


@pytest.fixture()
def victim(db_session, sample_org):
    """A second, entirely separate advisor — the account an attacker wants the
    grant written onto."""
    u = User(organization_id=sample_org.id, email="victim@restland.com",
             password_hash=hash_password("TestPass123!"),
             full_name="Victim Advisor", role="advisor",
             must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture()
def attacker_headers(db_session, sample_advisor):
    return {"Authorization": "Bearer %s" % create_access_token(sample_advisor,
                                                               db_session)}


def _state_from(url):
    return parse_qs(urlparse(url).query)["state"][0]


def _start_google(client, headers):
    r = client.get("/calendar/connect", headers=headers)
    assert r.status_code == 200, r.text
    return _state_from(r.json()["authorization_url"])


def _start_microsoft(client, headers):
    r = client.get("/microsoft/connect", headers=headers)
    assert r.status_code == 200, r.text
    return _state_from(r.json()["authorization_url"])


# ══ ATTACK 1 — IDENTITY SUBSTITUTION ════════════════════════════════════════

def test_google_callback_ignores_a_raw_user_id_in_state(
        client, db_session, victim, google_exchange):
    """The original exploit verbatim: put the victim's id in `state`."""
    r = client.get(GOOGLE_CALLBACK, params={"state": victim.id, "code": "real-code"},
                   follow_redirects=False)
    assert google_exchange.calls == [], (
        "the callback exchanged a code against a user id supplied in the URL")
    assert "calendar_connected=true" not in r.headers.get("location", "")


def test_google_callback_ignores_the_setup_prefixed_form(
        client, victim, google_exchange):
    """`setup:{user_id}` was a second, equally unauthenticated way to say the
    same thing, and the report confirmed it."""
    r = client.get(GOOGLE_CALLBACK,
                   params={"state": "setup:%s" % victim.id, "code": "real-code"},
                   follow_redirects=False)
    assert google_exchange.calls == []
    assert "calendar_connected=true" not in r.headers.get("location", "")


def test_microsoft_callback_ignores_a_raw_user_id_in_state(
        client, victim, microsoft_exchange):
    r = client.get(MS_CALLBACK, params={"state": victim.id, "code": "real-code"},
                   follow_redirects=False)
    assert microsoft_exchange.calls == []
    assert "microsoft_connected=true" not in r.headers.get("location", "")


def test_a_grant_lands_on_the_initiator_not_on_a_substituted_id(
        client, db_session, sample_advisor, victim, attacker_headers,
        google_exchange):
    """THE WHOLE DEFECT IN ONE TEST.

    The attacker starts a legitimate flow as themselves, then tries to redirect
    the finished authorization onto the victim. The state is bound to the
    initiator, so the only thing they can do with it is connect their own
    account — which is exactly the intended behaviour.
    """
    state = _start_google(client, attacker_headers)
    client.get(GOOGLE_CALLBACK, params={"state": state, "code": "real-code"},
               follow_redirects=False)
    assert google_exchange.subject == sample_advisor.id
    assert google_exchange.subject != victim.id


# ══ ATTACK 2 — STATE TAMPERING ══════════════════════════════════════════════

def test_swapping_the_transaction_id_is_refused(
        client, db_session, sample_advisor, victim, attacker_headers,
        google_exchange):
    """Two real transactions exist. Presenting the victim's transaction id with
    the attacker's secret must fail the digest comparison rather than select
    the victim."""
    attacker_state = _start_google(client, attacker_headers)
    victim_state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=victim)

    victim_txn_id = victim_state.split(".", 1)[0]
    attacker_secret = attacker_state.split(".", 1)[1]
    spliced = "%s.%s" % (victim_txn_id, attacker_secret)

    client.get(GOOGLE_CALLBACK, params={"state": spliced, "code": "real-code"},
               follow_redirects=False)
    assert google_exchange.calls == []


def test_altering_the_secret_is_refused(client, attacker_headers, google_exchange):
    state = _start_google(client, attacker_headers)
    txn_id, secret = state.split(".", 1)
    tampered = "%s.%sX" % (txn_id, secret[:-1])

    client.get(GOOGLE_CALLBACK, params={"state": tampered, "code": "real-code"},
               follow_redirects=False)
    assert google_exchange.calls == []


def test_a_malformed_state_is_refused(client, google_exchange):
    for bad in ("", "nodot", ".", "a.", ".b"):
        client.get(GOOGLE_CALLBACK, params={"state": bad, "code": "c"},
                   follow_redirects=False)
    assert google_exchange.calls == []


def test_the_stored_row_is_not_itself_a_usable_state(
        db_session, sample_advisor):
    """Only the DIGEST is stored. A database read must not yield a state that
    can be presented."""
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)
    txn_id, secret = state.split(".", 1)
    row = db_session.query(OAuthAuthorizationTransaction).filter_by(id=txn_id).one()
    assert row.secret_hash != secret
    assert secret not in row.secret_hash
    with pytest.raises(OAuthStateError):
        oauth_state_service.consume_state(
            db_session, "%s.%s" % (txn_id, row.secret_hash),
            provider=PROVIDER_GOOGLE)


# ══ ATTACK 3 — REPLAY ═══════════════════════════════════════════════════════

def test_the_same_state_cannot_be_used_twice(
        client, sample_advisor, attacker_headers, google_exchange):
    state = _start_google(client, attacker_headers)

    first = client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
                       follow_redirects=False)
    assert "calendar_connected=true" in first.headers["location"]
    assert google_exchange.calls == [sample_advisor.id]

    second = client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
                        follow_redirects=False)
    assert "calendar_connected=true" not in second.headers["location"]
    assert google_exchange.calls == [sample_advisor.id], (
        "a replayed callback URL was honoured a second time")


def test_a_state_is_spent_even_when_the_provider_reported_an_error(
        client, sample_advisor, attacker_headers, google_exchange):
    """A denied consent must not leave a live single-use credential behind in
    the browser's history."""
    state = _start_google(client, attacker_headers)
    client.get(GOOGLE_CALLBACK, params={"state": state, "error": "access_denied"},
               follow_redirects=False)
    client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
               follow_redirects=False)
    assert google_exchange.calls == []


def test_consume_marks_the_row_consumed(db_session, sample_advisor):
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)
    txn = oauth_state_service.consume_state(db_session, state,
                                            provider=PROVIDER_GOOGLE)
    assert txn.consumed_at is not None
    assert txn.is_consumed
    with pytest.raises(OAuthStateError):
        oauth_state_service.consume_state(db_session, state,
                                          provider=PROVIDER_GOOGLE)


# ══ ATTACK 4 — EXPIRY ═══════════════════════════════════════════════════════

def test_an_expired_transaction_is_refused(db_session, sample_advisor):
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)
    txn_id = state.split(".", 1)[0]
    row = db_session.query(OAuthAuthorizationTransaction).filter_by(id=txn_id).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(OAuthStateError):
        oauth_state_service.consume_state(db_session, state,
                                          provider=PROVIDER_GOOGLE)


def test_an_expired_transaction_is_refused_at_the_callback(
        client, db_session, attacker_headers, google_exchange):
    state = _start_google(client, attacker_headers)
    row = db_session.query(OAuthAuthorizationTransaction).filter_by(
        id=state.split(".", 1)[0]).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
               follow_redirects=False)
    assert google_exchange.calls == []


def test_a_transaction_is_short_lived_by_default(db_session, sample_advisor):
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)
    row = db_session.query(OAuthAuthorizationTransaction).filter_by(
        id=state.split(".", 1)[0]).one()
    lifetime = row._aware(row.expires_at) - datetime.now(timezone.utc)
    assert timedelta(minutes=1) < lifetime <= timedelta(minutes=60), (
        "an authorization transaction must not be a long-lived credential")


# ══ ATTACK 5 — WRONG PROVIDER / WRONG CONTEXT ═══════════════════════════════

def test_a_google_state_is_refused_by_the_microsoft_callback(
        client, attacker_headers, microsoft_exchange):
    state = _start_google(client, attacker_headers)
    client.get(MS_CALLBACK, params={"state": state, "code": "code-1"},
               follow_redirects=False)
    assert microsoft_exchange.calls == []


def test_a_microsoft_state_is_refused_by_the_google_callback(
        client, attacker_headers, microsoft_configured, google_exchange):
    state = _start_microsoft(client, attacker_headers)
    client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
               follow_redirects=False)
    assert google_exchange.calls == []


def test_the_redirect_context_comes_from_the_row_not_from_the_url(
        client, db_session, sample_advisor, attacker_headers, google_exchange):
    """`flow` used to be inferred from a "setup:" prefix on the state string.
    A settings-flow transaction must land on /settings however the caller
    decorates the URL."""
    state = _start_google(client, attacker_headers)
    r = client.get(GOOGLE_CALLBACK,
                   params={"state": state, "code": "code-1", "setup": "1"},
                   follow_redirects=False)
    location = r.headers["location"]
    assert "/settings" in location and "/setup-integrations" not in location


def test_an_unknown_transaction_id_is_refused(client, google_exchange):
    client.get(GOOGLE_CALLBACK,
               params={"state": "00000000-0000-0000-0000-000000000000.whatever",
                       "code": "code-1"},
               follow_redirects=False)
    assert google_exchange.calls == []


# ══ ATTACK 6 — TENANT / SUBJECT BOUNDARIES ══════════════════════════════════

def test_a_subject_moved_between_organizations_mid_flow_is_refused(
        db_session, sample_advisor):
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)

    other = Organization(name="Another Tenant", slug="another-tenant",
                         plan="standard")
    db_session.add(other)
    db_session.commit()
    sample_advisor.organization_id = other.id
    db_session.commit()

    with pytest.raises(OAuthStateError):
        oauth_state_service.consume_state(db_session, state,
                                          provider=PROVIDER_GOOGLE)


def test_a_deactivated_subject_is_refused(db_session, sample_advisor):
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)
    sample_advisor.is_active = False
    db_session.commit()

    with pytest.raises(OAuthStateError):
        oauth_state_service.consume_state(db_session, state,
                                          provider=PROVIDER_GOOGLE)


def test_the_transaction_records_the_tenant_it_was_issued_in(
        db_session, sample_advisor, sample_org):
    state = oauth_state_service.issue_state(
        db_session, provider=PROVIDER_GOOGLE, subject=sample_advisor)
    row = db_session.query(OAuthAuthorizationTransaction).filter_by(
        id=state.split(".", 1)[0]).one()
    assert row.organization_id == sample_org.id
    assert row.user_id == sample_advisor.id


def test_starting_a_flow_requires_authentication(client):
    assert client.get("/calendar/connect").status_code == 401
    assert client.get("/microsoft/connect").status_code == 401


# ══ LEGITIMATE FLOWS MUST STILL WORK ════════════════════════════════════════

def test_google_settings_flow_succeeds_end_to_end(
        client, sample_advisor, attacker_headers, google_exchange):
    state = _start_google(client, attacker_headers)
    r = client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
                   follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "calendar_connected=true" in r.headers["location"]
    assert "/settings" in r.headers["location"]
    assert google_exchange.subject == sample_advisor.id


def test_microsoft_settings_flow_succeeds_end_to_end(
        client, sample_advisor, attacker_headers, microsoft_configured,
        microsoft_exchange):
    state = _start_microsoft(client, attacker_headers)
    r = client.get(MS_CALLBACK, params={"state": state, "code": "code-1"},
                   follow_redirects=False)
    assert "microsoft_connected=true" in r.headers["location"]
    assert "/settings" in r.headers["location"]
    assert microsoft_exchange.subject == sample_advisor.id


def test_setup_link_google_flow_succeeds_and_lands_on_the_setup_page(
        client, db_session, sample_advisor, sample_org, google_exchange):
    """The admin-emailed setup link, all the way through. The setup page
    redirect must come from the transaction's `flow`, not from a prefix."""
    admin = User(organization_id=sample_org.id, email="flowadmin@restland.com",
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Flow Admin", role="org_admin",
                 must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    admin_headers = {"Authorization": "Bearer %s" % create_access_token(
        admin, db_session)}

    made = client.post("/admin/setup-link/%s" % sample_advisor.id,
                       headers=admin_headers)
    assert made.status_code == 200, made.text
    token = made.json()["link"].split("token=", 1)[1]

    started = client.get("/setup/google-connect", params={"token": token})
    assert started.status_code == 200, started.text
    state = _state_from(started.json()["authorization_url"])

    row = db_session.query(OAuthAuthorizationTransaction).filter_by(
        id=state.split(".", 1)[0]).one()
    assert row.flow == FLOW_SETUP
    assert row.user_id == sample_advisor.id

    r = client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
                   follow_redirects=False)
    assert "/setup-integrations" in r.headers["location"]
    assert "calendar_connected=true" in r.headers["location"]
    assert google_exchange.subject == sample_advisor.id


def test_setup_link_microsoft_flow_succeeds(
        client, db_session, sample_advisor, sample_org, microsoft_configured,
        microsoft_exchange):
    admin = User(organization_id=sample_org.id, email="msadmin@restland.com",
                 password_hash=hash_password("AdminPass123!"),
                 full_name="MS Admin", role="org_admin",
                 must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    admin_headers = {"Authorization": "Bearer %s" % create_access_token(
        admin, db_session)}

    token = client.post("/admin/setup-link/%s" % sample_advisor.id,
                        headers=admin_headers).json()["link"].split("token=", 1)[1]
    started = client.get("/setup/microsoft-connect", params={"token": token})
    assert started.status_code == 200, started.text
    state = _state_from(started.json()["authorization_url"])

    r = client.get(MS_CALLBACK, params={"state": state, "code": "code-1"},
                   follow_redirects=False)
    assert "/setup-integrations" in r.headers["location"]
    assert "microsoft_connected=true" in r.headers["location"]
    assert microsoft_exchange.subject == sample_advisor.id


def test_a_denied_consent_still_redirects_somewhere_sensible(
        client, attacker_headers, google_exchange):
    state = _start_google(client, attacker_headers)
    r = client.get(GOOGLE_CALLBACK,
                   params={"state": state, "error": "access_denied"},
                   follow_redirects=False)
    assert "calendar_error=access_denied" in r.headers["location"]
    assert google_exchange.calls == []


def test_the_state_is_opaque_and_carries_no_identity(
        client, sample_advisor, attacker_headers):
    """A state that contains the user id — even alongside a signature — is a
    regression: it invites the next reader to parse it."""
    state = _start_google(client, attacker_headers)
    assert sample_advisor.id not in state
    assert sample_advisor.email not in state
    assert not state.startswith("setup:")


def test_two_starts_produce_two_independent_transactions(
        client, attacker_headers, sample_advisor, google_exchange):
    """Opening the connect button twice must not invalidate the first tab in a
    way that loses the advisor's work — each start is its own transaction."""
    first = _start_google(client, attacker_headers)
    second = _start_google(client, attacker_headers)
    assert first != second

    client.get(GOOGLE_CALLBACK, params={"state": second, "code": "c"},
               follow_redirects=False)
    assert google_exchange.subject == sample_advisor.id
    client.get(GOOGLE_CALLBACK, params={"state": first, "code": "c"},
               follow_redirects=False)
    assert google_exchange.calls == [sample_advisor.id, sample_advisor.id]


def test_god_mode_does_not_break_its_own_calendar_connection(
        client, db_session, sample_org, google_exchange):
    """A TENANT CHECK THAT FIRES ON LEGITIMATE USE IS A BUG.

    `deps.get_current_user` detaches a god_admin and rewrites
    `organization_id` on the copy — NULL in neutral God Mode, the override
    value when a customer is selected. Recording that request-scoped fiction on
    the transaction would make the boundary check at the callback compare it
    against the real column and refuse the owner's own connect flow. The
    transaction records what the database says.
    """
    god = User(organization_id=sample_org.id, email="godconnect@evosyspro.live",
               password_hash=hash_password("owner-pass"),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    headers = {"Authorization": "Bearer %s" % create_access_token(god, db_session)}

    state = _start_google(client, headers)
    row = db_session.query(OAuthAuthorizationTransaction).filter_by(
        id=state.split(".", 1)[0]).one()
    assert row.organization_id == sample_org.id, (
        "the transaction recorded God Mode's request-scoped org, not the row's")

    r = client.get(GOOGLE_CALLBACK, params={"state": state, "code": "code-1"},
                   follow_redirects=False)
    assert "calendar_connected=true" in r.headers["location"]
    assert google_exchange.subject == god.id


def test_issue_state_refuses_a_deactivated_subject_at_initiation(
        db_session, sample_advisor):
    sample_advisor.is_active = False
    db_session.commit()
    with pytest.raises(OAuthStateError):
        oauth_state_service.issue_state(db_session, provider=PROVIDER_GOOGLE,
                                        subject=sample_advisor)


def test_issue_state_refuses_an_unknown_provider(db_session, sample_advisor):
    with pytest.raises(OAuthStateError):
        oauth_state_service.issue_state(db_session, provider="dropbox",
                                        subject=sample_advisor)


def test_issue_state_refuses_an_unknown_flow(db_session, sample_advisor):
    with pytest.raises(OAuthStateError):
        oauth_state_service.issue_state(db_session, provider=PROVIDER_GOOGLE,
                                        subject=sample_advisor, flow="whatever")
