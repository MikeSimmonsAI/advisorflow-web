"""Sign in with Google (#430). ID tokens here are REAL RS256 JWTs signed by a
test key; google-auth verifies them exactly as in production, with only the
key download (_fetch_certs) and the code exchange replaced. No network."""
import datetime as dt
import time
from urllib.parse import parse_qs, urlparse, unquote

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt as gjwt

from app.models.google_signin_models import UserGoogleIdentity
from app.models.models import Organization, Platform, User
from app.services import google_signin_service as G
from app.services.auth_service import hash_password

APP = "https://app.evosyspro.live"
CID = "test-client.apps.googleusercontent.com"
REDIRECT = "https://backend.example.test/auth/google/callback"


def _keypair(kid):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return crypt.RSASigner.from_string(pem_key, key_id=kid), cert.public_bytes(serialization.Encoding.PEM).decode()


SIGNER, CERT = _keypair("k1")
ROGUE, _ROGUE_CERT = _keypair("k1")          # same kid, different key: a forged token


@pytest.fixture()
def google_on(monkeypatch):
    monkeypatch.setenv("GOOGLE_SIGNIN_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_SIGNIN_CLIENT_ID", CID)
    monkeypatch.setenv("GOOGLE_SIGNIN_CLIENT_SECRET", "test-secret-not-real")
    monkeypatch.setenv("GOOGLE_SIGNIN_REDIRECT_URI", REDIRECT)
    monkeypatch.delenv("GOOGLE_SIGNIN_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("GOOGLE_SIGNIN_ALLOW_GOD", raising=False)
    import google.oauth2.id_token as idt
    monkeypatch.setattr(idt, "_fetch_certs", lambda request, url: {"k1": CERT})
    box = {}
    # the browser-binding cookie is Secure (the backend is https), so talk https
    import app.routers.google_signin_router  # noqa: F401
    monkeypatch.setattr(G, "_exchange_code", lambda code, verifier: box["tokens"](code, verifier))
    return box


def _id_token(nonce, sub="g-sub-1", email="person@gmail.example", verified=True, aud=CID, iss="https://accounts.google.com",
              exp_in=600, signer=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub, "email": email, "email_verified": verified, "iat": now - 5,
              "exp": now + exp_in, "nonce": nonce}
    return gjwt.encode(signer or SIGNER, claims).decode()


@pytest.fixture()
def evo(db_session):
    plat = Platform(name="EvoSys Pro", slug="evosyspro", domain="app.evosyspro.live", is_active=True)
    db_session.add(plat); db_session.commit()
    org = Organization(name="Evo Test Co", slug="evo-test", plan="standard", industry="insurance", platform_id=plat.id)
    db_session.add(org); db_session.commit()
    u = User(organization_id=org.id, email="member@evo-test.example", full_name="Evo Member", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u); db_session.commit()
    return u


def _flow(client, *, origin=APP, next_path=None, intent=None):
    client.base_url = "https://testserver"
    q = {"origin": origin}
    if next_path:
        q["next"] = next_path
    if intent:
        q = {"intent": intent}
    r = client.get("/auth/google/start", params=q, follow_redirects=False)
    assert r.status_code == 303, r.text
    loc = urlparse(r.headers["location"])
    if loc.netloc != "accounts.google.com":
        return r, None, None
    qs = parse_qs(loc.query)
    assert qs["code_challenge_method"] == ["S256"] and qs["client_id"] == [CID] and qs["redirect_uri"] == [REDIRECT]
    assert "openid" in qs["scope"][0] and qs["prompt"] == ["select_account"]
    return r, qs["state"][0], qs["nonce"][0]


def _callback(client, state, cookie=True, **extra):
    client.base_url = "https://testserver"
    params = {"state": state, "code": "auth-code-1", **extra}
    if not cookie:
        client.cookies.clear()
    r = client.get("/auth/google/callback", params=params, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]


def _link(db, user, sub="g-sub-1"):
    db.add(UserGoogleIdentity(user_id=user.id, google_sub=sub, email_at_link="person@gmail.example")); db.commit()


def _ticket(location):
    frag = urlparse(location).fragment
    return unquote(parse_qs(frag)["ticket"][0])


# ── off by default ───────────────────────────────────────────────────────
def test_everything_is_inert_until_configured(client, auth_headers, monkeypatch):
    for k in ("GOOGLE_SIGNIN_ENABLED", "GOOGLE_SIGNIN_CLIENT_ID", "GOOGLE_SIGNIN_CLIENT_SECRET", "GOOGLE_SIGNIN_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    assert client.get("/auth/google/config", headers={"Origin": APP}).json() == {"enabled": False}
    assert client.get("/auth/google/start", params={"origin": APP}, follow_redirects=False).status_code == 404
    assert client.post("/auth/google/exchange", json={"ticket": "x.y"}, headers={"Origin": APP}).status_code == 404
    assert client.post("/auth/google/link/intent", json={"origin": APP}, headers=auth_headers).status_code == 404
    monkeypatch.setenv("GOOGLE_SIGNIN_ENABLED", "1")            # flag alone is not enough
    assert client.get("/auth/google/config", headers={"Origin": APP}).json() == {"enabled": False}
    assert set(G.missing_config()) == {"GOOGLE_SIGNIN_CLIENT_ID", "GOOGLE_SIGNIN_CLIENT_SECRET", "GOOGLE_SIGNIN_REDIRECT_URI"}


def test_config_only_for_allowed_origins(client, google_on):
    assert client.get("/auth/google/config", headers={"Origin": APP}).json() == {"enabled": True}
    assert client.get("/auth/google/config", headers={"Origin": "https://app.bookaboost.live"}).json() == {"enabled": False}
    assert client.get("/auth/google/config").json() == {"enabled": False}


# ── sign-in ──────────────────────────────────────────────────────────────
def test_linked_account_signs_in_and_session_behaves_like_password(client, db_session, evo, google_on):
    _link(db_session, evo)
    r, state, nonce = _flow(client, next_path="/pipeline")
    assert G.COOKIE_NAME in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"]
    google_on["tokens"] = lambda code, v: {"id_token": _id_token(nonce)}
    loc = _callback(client, state)
    assert loc.startswith(APP + "/auth/google/complete#ticket=") and "next=%2Fpipeline" in loc
    assert "access_token" not in loc                                   # never a session token in a URL
    t = client.post("/auth/google/exchange", json={"ticket": _ticket(loc)}, headers={"Origin": APP})
    assert t.status_code == 200, t.text
    tok = t.json()["access_token"]; assert t.json()["role"] == "org_admin"
    H = {"Authorization": "Bearer " + tok}
    assert client.get("/auth/my-contexts", headers=H).status_code == 200
    assert client.post("/auth/google/exchange", json={"ticket": _ticket(loc)}, headers={"Origin": APP}).status_code == 401  # replay
    assert client.post("/auth/logout", headers=H).status_code in (200, 204)
    assert client.get("/auth/my-contexts", headers=H).status_code == 401                         # logout ends it
    assert client.post("/auth/login", data={"username": evo.email, "password": "TestPass123!"}).status_code == 200  # password still works


def test_unlinked_google_account_is_refused_even_with_matching_email(client, db_session, evo, google_on):
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda code, v: {"id_token": _id_token(nonce, sub="someone-else", email=evo.email)}
    assert _callback(client, state).endswith("/login?google_error=not_linked")
    assert db_session.query(UserGoogleIdentity).count() == 0                                    # nothing auto-linked


@pytest.mark.parametrize("bad", [
    {"verified": False, "_code": "unverified"},
    {"aud": "someone-elses-client", "_code": "failed"},
    {"iss": "https://evil.example", "_code": "failed"},
    {"exp_in": -120, "_code": "failed"},
    {"signer": ROGUE, "_code": "failed"},
    {"nonce": "wrong-nonce", "_code": "failed"},
])
def test_bad_id_tokens_are_rejected(client, db_session, evo, google_on, bad):
    _link(db_session, evo)
    r, state, nonce = _flow(client)
    code = bad.pop("_code")
    n = bad.pop("nonce", nonce)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(n, **bad)}
    assert _callback(client, state).endswith("/login?google_error=" + code)


def test_state_replay_tamper_and_other_browser(client, db_session, evo, google_on):
    _link(db_session, evo)
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce)}
    assert "/auth/google/complete#" in _callback(client, state)
    assert _callback(client, state).endswith("/login?google_error=failed")                       # replayed state
    r, state, nonce = _flow(client)
    tid, _, sec = state.partition(".")
    assert _callback(client, tid + "." + sec[:-2] + "xx").endswith("/login?google_error=failed")  # tampered
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce)}
    assert _callback(client, state, cookie=False).endswith("/login?google_error=failed")          # login CSRF


def test_cancel_and_provider_errors(client, db_session, evo, google_on):
    r, state, nonce = _flow(client)
    assert _callback(client, state, error="access_denied").endswith("/login?google_error=cancelled")
    r, state, nonce = _flow(client)
    def boom(c, v):
        raise G.GoogleSigninError("failed", "token exchange 400")
    google_on["tokens"] = boom
    assert _callback(client, state).endswith("/login?google_error=failed")


def test_no_open_redirects(client, google_on):
    for origin in ("https://evil.example", "https://app.evosyspro.live.evil.example", "javascript:alert(1)"):
        r = client.get("/auth/google/start", params={"origin": origin}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(APP + "/login?google_error=origin")
    for nxt in ("//evil.example", "https://evil.example", "/\\evil", "/ok\r\nX: 1"):
        assert G.safe_next(nxt) is None
    assert G.safe_next("/pipeline?x=1") == "/pipeline?x=1"


def test_exchange_requires_matching_origin_and_platform(client, db_session, evo, google_on):
    _link(db_session, evo)
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce)}
    tk = _ticket(_callback(client, state))
    assert client.post("/auth/google/exchange", json={"ticket": tk}, headers={"Origin": "https://app.bookaboost.live"}).status_code == 401
    # a BookaBoost-only account linked to Google cannot use it on EvoSys Pro
    bb = Platform(name="BookaBoost", slug="bookaboost", domain="app.bookaboost.live", is_active=True); db_session.add(bb); db_session.commit()
    org = Organization(name="BB Co", slug="bb-co", plan="standard", industry="funeral", platform_id=bb.id); db_session.add(org); db_session.commit()
    u = User(organization_id=org.id, email="bb@bb.example", full_name="BB", role="advisor", password_hash=hash_password("x" * 12), must_change_password=False)
    db_session.add(u); db_session.commit(); _link(db_session, u, sub="bb-sub")
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="bb-sub")}
    tk = _ticket(_callback(client, state))
    assert client.post("/auth/google/exchange", json={"ticket": tk}, headers={"Origin": APP}).status_code == 401


def test_inactive_user_cannot_sign_in(client, db_session, evo, google_on):
    _link(db_session, evo); evo.is_active = False; db_session.commit()
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce)}
    assert _callback(client, state).endswith("/login?google_error=not_linked")


# ── linking ──────────────────────────────────────────────────────────────
def _login_headers(client, email):
    r = client.post("/auth/login", data={"username": email, "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_explicit_link_from_settings_then_sign_in(client, db_session, evo, google_on):
    H = _login_headers(client, evo.email)
    assert client.get("/auth/google/link", headers=H).json()["linked"] is False
    it = client.post("/auth/google/link/intent", json={"origin": APP}, headers=H)
    assert it.status_code == 200, it.text
    url = urlparse(it.json()["url"]); assert url.netloc == "backend.example.test"
    intent = unquote(parse_qs(url.query)["intent"][0])
    r, state, nonce = _flow(client, intent=intent)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="new-sub", email="whatever@gmail.example")}
    assert _callback(client, state) == APP + "/settings?google=linked"
    st = client.get("/auth/google/link", headers=H).json()
    assert st["linked"] is True and st["email"] == "whatever@gmail.example"
    assert client.post("/auth/google/link/intent", json={"origin": APP}, headers=H).status_code == 409   # one at a time
    r, state, nonce = _flow(client, intent=intent)                                                       # spent intent
    assert r.headers["location"].endswith("/settings?google_error=failed")
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="new-sub")}
    assert "/auth/google/complete#" in _callback(client, state)
    assert client.delete("/auth/google/link", headers=H).json() == {"unlinked": True}
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="new-sub")}
    assert _callback(client, state).endswith("/login?google_error=not_linked")


def test_a_google_account_cannot_be_linked_to_two_users(client, db_session, evo, sample_advisor, google_on):
    _link(db_session, sample_advisor, sub="taken-sub")
    H = _login_headers(client, evo.email)
    intent = unquote(parse_qs(urlparse(client.post("/auth/google/link/intent", json={"origin": APP}, headers=H).json()["url"]).query)["intent"][0])
    r, state, nonce = _flow(client, intent=intent)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="taken-sub")}
    assert _callback(client, state).endswith("/settings?google_error=already_linked")


def test_link_intent_needs_a_session(client, google_on):
    assert client.post("/auth/google/link/intent", json={"origin": APP}).status_code == 401


# ── God Mode stays password-only unless explicitly allowed ────────────────
def test_god_admin_cannot_link_or_sign_in_by_default(client, db_session, google_on, monkeypatch):
    god = User(organization_id=None, email="owner@platform.example", full_name="Owner", role="god_admin",
               password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(god); db_session.commit()
    H = _login_headers(client, god.email)
    assert client.post("/auth/google/link/intent", json={"origin": APP}, headers=H).status_code == 403
    _link(db_session, god, sub="god-sub")                       # even if a row existed
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="god-sub")}
    assert _callback(client, state).endswith("/login?google_error=not_linked")
    monkeypatch.setenv("GOOGLE_SIGNIN_ALLOW_GOD", "1")
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="god-sub")}
    tk = _ticket(_callback(client, state))
    t = client.post("/auth/google/exchange", json={"ticket": tk}, headers={"Origin": APP})
    assert t.status_code == 200 and t.json()["role"] == "god_admin"


def test_google_never_changes_role(client, db_session, evo, google_on):
    adv = User(organization_id=evo.organization_id, email="adv@evo-test.example", full_name="Adv", role="advisor",
               password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(adv); db_session.commit()
    _link(db_session, adv, sub="adv-sub")
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="adv-sub")}
    t = client.post("/auth/google/exchange", json={"ticket": _ticket(_callback(client, state))}, headers={"Origin": "http://localhost:5173"})
    assert t.status_code == 401                                   # origin not allowed
    r, state, nonce = _flow(client)
    google_on["tokens"] = lambda c, v: {"id_token": _id_token(nonce, sub="adv-sub")}
    loc = _callback(client, state)
    t = client.post("/auth/google/exchange", json={"ticket": _ticket(loc)}, headers={"Origin": APP})
    assert t.status_code == 200, t.text
    assert t.json()["role"] == "advisor"
    H = {"Authorization": "Bearer " + t.json()["access_token"]}
    assert client.get("/god/orgs", headers=H).status_code in (401, 403)
    assert client.get("/executive/command-center", headers=H).status_code in (401, 403)
