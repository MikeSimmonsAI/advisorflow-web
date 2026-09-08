"""Public write-path security: CRM inbound auth, secret encryption, the demo
request destination, and the login throttle.

WHAT THESE PROVE, AND WHY EACH ONE EXISTS.

Every test here corresponds to a defect that was live in production at commit
518d22b, found by the capability audit. They are written so that reintroducing
the defect fails a test rather than passing review:

  * `/crm/inbound/{org_id}` treated the organization UUID as its credential.
  * `crm_connections.api_key_encrypted` held plaintext.
  * `POST /leads/demo-request` was registered twice, so the handler that emails
    the team could never run.
  * That endpoint picked its organization with a name `ilike` and, failing that,
    whatever row came back first.
  * `/auth/login` had a per-(IP, email) throttle and no per-address ceiling, so
    password spraying across many accounts was unthrottled.
"""

import uuid

import pytest
from sqlalchemy import text

from app.models.models import Organization, Platform, User, Lead
from app.models.integration_models import (
    IntegrationCredential, IntegrationRequestLog,
    INTEGRATION_CRM_INBOUND, INTEGRATION_RETELL_TENANT, ACTION_INBOUND,
)
from app.services import crm_secrets, integration_auth
from app.services.auth_service import create_access_token, hash_password


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def crm_table(db_session):
    """crm_connections is created by raw SQL in auto_migrate, not by the ORM.

    The test schema comes from create_all(), which knows nothing about it, so a
    test that needs the table has to ask for it. The DDL mirrors auto_migrate's
    (which now uses CURRENT_TIMESTAMP rather than the Postgres-only NOW(), and
    therefore actually runs here).
    """
    db_session.execute(text("""
        CREATE TABLE IF NOT EXISTS crm_connections (
            id VARCHAR PRIMARY KEY,
            organization_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            crm_type VARCHAR NOT NULL DEFAULT 'webhook',
            webhook_url VARCHAR,
            webhook_secret VARCHAR,
            api_key_encrypted VARCHAR,
            api_base_url VARCHAR,
            sync_mode VARCHAR DEFAULT 'push_only',
            push_events TEXT DEFAULT '[]',
            annotation_tag VARCHAR,
            field_mapping TEXT,
            active BOOLEAN DEFAULT TRUE,
            last_push_at TIMESTAMP,
            last_pull_at TIMESTAMP,
            total_pushed INTEGER DEFAULT 0,
            total_pulled INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db_session.commit()
    return True


@pytest.fixture()
def other_org(db_session):
    org = Organization(name="Other Funeral Home", slug="other-fh", plan="standard")
    db_session.add(org)
    db_session.commit()
    return org


def _issue(db_session, org_id, *, kind=INTEGRATION_CRM_INBOUND, active=True):
    """Mint a credential directly. Returns (full_key, credential)."""
    full, prefix, hashed = integration_auth.generate_key()
    cred = IntegrationCredential(
        name="test-%s" % prefix, kind=kind, key_prefix=prefix, key_hash=hashed,
        organization_id=org_id, is_active=active,
    )
    db_session.add(cred)
    db_session.commit()
    db_session.refresh(cred)
    return full, cred


def _bearer(key):
    return {"Authorization": "Bearer %s" % key}


def _configure_intake(db_session, org, *, slug="bookaboost", name="BookaBoost",
                      website_url=None):
    """Wire a platform to an organization as its public intake destination.

    FLUSH BEFORE ASSIGNING. `Platform.id` comes from a column default, so it is
    None until the INSERT — assigning `org.platform_id = platform.id` before a
    flush silently stores NULL, and `public_intake._verify` then correctly
    refuses because the organization does not belong to the platform. Getting
    this wrong the first time is how the fixture proved the check works.
    """
    platform = Platform(name=name, slug=slug, website_url=website_url,
                        public_intake_organization_id=org.id)
    db_session.add(platform)
    db_session.flush()
    org.platform_id = platform.id
    db_session.add(org)
    db_session.commit()
    db_session.refresh(platform)
    return platform


def _open_legacy(db_session, org):
    """Make `org` look like an organization that predates the credential.

    Every organization created from now on is secure by default, which is what
    the model's Python-side default gives a fresh test database. Only rows that
    existed before the migration are open, so a test about the legacy path has
    to say so explicitly rather than relying on a fixture's shape.
    """
    org.crm_inbound_secure_required = False
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture()
def god_headers(db_session):
    god = User(organization_id=None, email="god@platform.test",
               password_hash=hash_password("GodPass123!"),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(god, db_session)}


# ── 1. CRM inbound authentication ───────────────────────────────────────────

def test_inbound_accepts_a_correct_credential_for_its_own_org(
        client, db_session, sample_org, crm_table):
    key, _ = _issue(db_session, sample_org.id)
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550001"},
                    headers=_bearer(key))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["auth_mode"] == "secure"
    # A secure call is never advertised as deprecated.
    assert "deprecation" not in body
    assert "Deprecation" not in r.headers


def test_inbound_rejects_a_wrong_credential(client, db_session, sample_org, crm_table):
    _issue(db_session, sample_org.id)
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550002"},
                    headers=_bearer("evsk_not-a-real-key-at-all-000000"))
    assert r.status_code == 401
    assert db_session.query(Lead).count() == 0


def test_a_token_for_org_a_is_refused_for_org_b(
        client, db_session, sample_org, other_org, crm_table):
    """THE CROSS-TENANT CASE. A valid key, used against the wrong workspace."""
    key, _ = _issue(db_session, sample_org.id)
    r = client.post("/crm/inbound/%s" % other_org.id,
                    json={"first_name": "Ada", "phone": "+12145550003"},
                    headers=_bearer(key))
    assert r.status_code == 401
    assert db_session.query(Lead).filter(
        Lead.organization_id == other_org.id).count() == 0


def test_a_retell_tenant_key_cannot_write_leads(
        client, db_session, sample_org, crm_table):
    """Right organization, right shape, wrong KIND. Scope is not enough."""
    key, _ = _issue(db_session, sample_org.id, kind=INTEGRATION_RETELL_TENANT)
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550004"},
                    headers=_bearer(key))
    assert r.status_code == 401
    assert db_session.query(Lead).count() == 0


def test_a_revoked_token_stops_working(client, db_session, sample_org, crm_table):
    key, cred = _issue(db_session, sample_org.id)
    cred.is_active = False
    db_session.add(cred)
    db_session.commit()
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550005"},
                    headers=_bearer(key))
    assert r.status_code == 401


def test_rotation_invalidates_the_old_key_and_the_new_one_works(
        client, db_session, sample_org, god_headers, crm_table):
    old, cred = _issue(db_session, sample_org.id)

    rot = client.post("/god/crm-inbound/tokens/%s/rotate" % cred.id,
                      headers=god_headers)
    assert rot.status_code == 200, rot.text
    new = rot.json()["key"]
    assert new != old

    assert client.post("/crm/inbound/%s" % sample_org.id,
                       json={"first_name": "Old", "phone": "+12145550006"},
                       headers=_bearer(old)).status_code == 401
    assert client.post("/crm/inbound/%s" % sample_org.id,
                       json={"first_name": "New", "phone": "+12145550007"},
                       headers=_bearer(new)).status_code == 200


def test_an_unknown_organization_answers_exactly_like_a_bad_key(
        client, db_session, sample_org, crm_table):
    """NO EXISTENCE ORACLE. This endpoint must not confirm that an organization
    id is real - that is what made the bare UUID worth harvesting."""
    missing = client.post("/crm/inbound/%s" % uuid.uuid4(),
                          json={"first_name": "Ada"},
                          headers=_bearer("evsk_wrong-key-000000000000000"))
    known_bad = client.post("/crm/inbound/%s" % sample_org.id,
                            json={"first_name": "Ada"},
                            headers=_bearer("evsk_wrong-key-000000000000000"))
    assert missing.status_code == known_bad.status_code == 401
    assert missing.json()["detail"] == known_bad.json()["detail"]


# ── 2. Legacy mode: explicit, observable, never called secure ────────────────

def test_a_new_organization_is_secure_by_default(client, db_session, sample_org,
                                                 crm_table):
    """SECURE BY DEFAULT FOR ANYTHING NEW. Only rows that predate the migration
    are open, and they are opened by the server_default, not by the model."""
    assert sample_org.crm_inbound_secure_required is True
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550020"})
    assert r.status_code == 401
    assert db_session.query(Lead).count() == 0


def test_legacy_is_accepted_but_marked_deprecated(
        client, db_session, sample_org, crm_table):
    """The migration window. Existing customers keep delivering leads."""
    _open_legacy(db_session, sample_org)
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550008"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["auth_mode"] == "legacy"
    assert "deprecation" in body
    assert r.headers.get("Deprecation") == "true"
    assert "Warning" in r.headers


def test_legacy_is_refused_once_the_organization_is_migrated(
        client, db_session, sample_org, crm_table):
    sample_org.crm_inbound_secure_required = True
    db_session.add(sample_org)
    db_session.commit()

    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550009"})
    assert r.status_code == 401
    assert db_session.query(Lead).count() == 0


def test_a_bad_key_is_never_downgraded_to_legacy(
        client, db_session, sample_org, crm_table):
    """Presenting a WRONG credential must not be more permissive than
    presenting none, even where legacy is still open."""
    _open_legacy(db_session, sample_org)
    r = client.post("/crm/inbound/%s" % sample_org.id,
                    json={"first_name": "Ada", "phone": "+12145550010"},
                    headers=_bearer("evsk_definitely-not-valid-00000"))
    assert r.status_code == 401
    assert db_session.query(Lead).count() == 0


def test_every_inbound_call_is_logged_without_the_secret(
        client, db_session, sample_org, crm_table):
    _open_legacy(db_session, sample_org)
    key, cred = _issue(db_session, sample_org.id)
    client.post("/crm/inbound/%s" % sample_org.id,
                json={"first_name": "Ada", "phone": "+12145550011"},
                headers=_bearer(key))
    client.post("/crm/inbound/%s" % sample_org.id,
                json={"first_name": "Bob", "phone": "+12145550012"})

    rows = (db_session.query(IntegrationRequestLog)
            .filter(IntegrationRequestLog.action == ACTION_INBOUND).all())
    assert len(rows) == 2
    modes = sorted((r.key_prefix is not None) for r in rows)
    assert modes == [False, True]          # one legacy, one secure
    for r in rows:
        blob = "%s %s %s" % (r.detail or "", r.key_prefix or "", r.integration_name or "")
        assert key not in blob, "the raw key reached the request log"


def test_god_usage_view_identifies_the_legacy_backlog(
        client, db_session, sample_org, god_headers, crm_table):
    _open_legacy(db_session, sample_org)
    client.post("/crm/inbound/%s" % sample_org.id,
                json={"first_name": "Ada", "phone": "+12145550013"})
    r = client.get("/god/crm-inbound/usage", headers=god_headers)
    assert r.status_code == 200, r.text
    requests = r.json()["requests"]
    assert requests and requests[0]["auth_mode"] == "legacy"
    assert requests[0]["organization_id"] == sample_org.id


def test_token_endpoints_are_god_only(client, db_session, sample_org,
                                      admin_auth_headers):
    for method, path in (
        ("get", "/god/crm-inbound/tokens"),
        ("post", "/god/crm-inbound/tokens"),
        ("get", "/god/crm-inbound/usage"),
    ):
        r = getattr(client, method)(
            path, headers=admin_auth_headers,
            **({"json": {"organization_id": sample_org.id}} if method == "post" else {}))
        assert r.status_code in (401, 403), "%s %s was reachable" % (method, path)


def test_issued_key_is_returned_once_and_never_listed(
        client, db_session, sample_org, god_headers):
    issued = client.post("/god/crm-inbound/tokens",
                         json={"organization_id": sample_org.id},
                         headers=god_headers)
    assert issued.status_code == 200, issued.text
    key = issued.json()["key"]

    listed = client.get("/god/crm-inbound/tokens?organization_id=%s" % sample_org.id,
                        headers=god_headers)
    assert listed.status_code == 200
    body = listed.text
    assert key not in body, "the secret was returned by the listing"
    assert issued.json()["key_prefix"] in body


# ── 3. Secret encryption ────────────────────────────────────────────────────

def test_new_api_keys_are_stored_encrypted(
        client, db_session, sample_org, admin_auth_headers, crm_table):
    secret = "ghl-live-key-abc123"
    r = client.post("/crm/connections",
                    json={"name": "GHL", "crm_type": "gohighlevel",
                          "api_key": secret, "webhook_secret": "hook-secret-xyz"},
                    headers=admin_auth_headers)
    assert r.status_code == 200, r.text

    row = db_session.execute(text(
        "SELECT api_key_encrypted, webhook_secret FROM crm_connections")).mappings().first()
    assert row["api_key_encrypted"] != secret, "api key was stored in plaintext"
    assert row["webhook_secret"] != "hook-secret-xyz"
    assert crm_secrets.looks_encrypted(row["api_key_encrypted"])
    # And it still round-trips to the value the CRM needs.
    assert crm_secrets.read_secret(row["api_key_encrypted"]) == secret


def test_secrets_are_never_returned_by_the_api(
        client, db_session, sample_org, admin_auth_headers, crm_table):
    secret = "ghl-live-key-def456"
    client.post("/crm/connections",
                json={"name": "GHL", "crm_type": "gohighlevel", "api_key": secret,
                      "webhook_secret": "hook-secret-uvw"},
                headers=admin_auth_headers)
    listed = client.get("/crm/connections", headers=admin_auth_headers)
    assert listed.status_code == 200
    assert secret not in listed.text
    assert "hook-secret-uvw" not in listed.text
    assert "api_key_encrypted" not in listed.text


def test_migration_encrypts_plaintext_and_leaves_ciphertext_alone(
        db_session, sample_org, crm_table):
    """IDEMPOTENCY IS THE WHOLE POINT. A second pass must not double-encrypt."""
    from app.services.crm_secrets import migrate_crm_connection_secrets

    plain = "legacy-plaintext-key"
    already = crm_secrets.store_secret("already-encrypted-key")
    db_session.execute(text(
        "INSERT INTO crm_connections (id, organization_id, name, api_key_encrypted) "
        "VALUES ('c1', :org, 'legacy', :v)"), {"org": sample_org.id, "v": plain})
    db_session.execute(text(
        "INSERT INTO crm_connections (id, organization_id, name, api_key_encrypted) "
        "VALUES ('c2', :org, 'modern', :v)"), {"org": sample_org.id, "v": already})
    db_session.commit()

    engine = db_session.get_bind()
    first = migrate_crm_connection_secrets(engine)
    assert first["encrypted"] == 1
    assert first["already"] == 1

    def _read(cid):
        return db_session.execute(
            text("SELECT api_key_encrypted FROM crm_connections WHERE id = :id"),
            {"id": cid}).scalar()

    assert crm_secrets.read_secret(_read("c1")) == plain
    assert crm_secrets.read_secret(_read("c2")) == "already-encrypted-key"
    after_first = (_read("c1"), _read("c2"))

    second = migrate_crm_connection_secrets(engine)
    assert second["encrypted"] == 0, "the migration was not idempotent"
    assert (_read("c1"), _read("c2")) == after_first
    assert crm_secrets.read_secret(_read("c1")) == plain


def test_reads_work_for_both_shapes_during_the_migration():
    assert crm_secrets.read_secret(crm_secrets.store_secret("v")) == "v"
    assert crm_secrets.read_secret("still-plaintext") == "still-plaintext"
    assert crm_secrets.read_secret("") == ""
    assert crm_secrets.read_secret(None) == ""


# ── 4. Demo request: one route, configured destination ──────────────────────

def test_exactly_one_post_route_is_registered_for_demo_request():
    """THE REGRESSION GUARD. Two handlers on one path meant the second could
    never run, and nothing failed to say so."""
    from app.main import app
    posts = [r for r in app.routes
             if getattr(r, "path", "") == "/leads/demo-request"
             and "POST" in (getattr(r, "methods", None) or set())]
    assert len(posts) == 1, "POST /leads/demo-request is registered %d times" % len(posts)


def test_demo_request_lands_in_the_configured_organization(
        client, db_session, sample_org):
    _configure_intake(db_session, sample_org,
                      website_url="https://bookaboost.live")

    r = client.post("/leads/demo-request",
                    json={"first_name": "Dana", "last_name": "Reed",
                          "email": "dana@example.com", "company": "Reed Funeral",
                          "industry": "funeral", "message": "Interested"})
    assert r.status_code == 201, r.text
    body = r.json()
    # BOTH RESPONSE SHAPES, so neither old caller breaks.
    assert body["success"] is True
    assert body["status"] == "created"

    lead = db_session.query(Lead).one()
    assert lead.organization_id == sample_org.id
    # The detail the older live handler silently discarded is kept.
    assert "Reed Funeral" in (lead.notes or "")
    assert "funeral" in (lead.notes or "")


def test_demo_request_refuses_when_no_destination_is_configured(
        client, db_session, sample_org):
    """FAIL CLOSED. The old code took the first organization in the table."""
    db_session.add(Platform(name="BookaBoost", slug="bookaboost"))
    db_session.commit()

    r = client.post("/leads/demo-request", json={"first_name": "Dana"})
    assert r.status_code == 503
    assert db_session.query(Lead).count() == 0


def test_a_name_match_is_not_authority(client, db_session):
    """The old resolver used Organization.name.ilike('%bookaboost%')."""
    org = Organization(name="BookaBoost Internal", slug="bb-internal", plan="standard")
    db_session.add(org)
    db_session.add(Platform(name="BookaBoost", slug="bookaboost"))
    db_session.commit()

    r = client.post("/leads/demo-request", json={"first_name": "Dana"})
    assert r.status_code == 503, "an organization was chosen by its name"
    assert db_session.query(Lead).count() == 0


def test_an_arbitrary_first_organization_is_never_chosen(client, db_session):
    """The old fallback was db.query(Organization).first()."""
    for i, name in enumerate(("Alpha Funeral", "Beta Funeral", "Gamma Funeral")):
        db_session.add(Organization(name=name, slug="org-%d" % i, plan="standard"))
    db_session.commit()

    r = client.post("/leads/demo-request", json={"first_name": "Dana"})
    assert r.status_code == 503
    assert db_session.query(Lead).count() == 0


def test_a_dangling_configured_destination_refuses(client, db_session):
    db_session.add(Platform(name="BookaBoost", slug="bookaboost",
                            public_intake_organization_id=str(uuid.uuid4())))
    db_session.commit()
    r = client.post("/leads/demo-request", json={"first_name": "Dana"})
    assert r.status_code == 503


def test_a_destination_belonging_to_another_brand_refuses(client, db_session):
    """CROSS-BRAND. A misconfiguration must not quietly cross a boundary."""
    other = Platform(name="EvoSys Pro", slug="evosyspro")
    db_session.add(other)
    db_session.commit()
    org = Organization(name="EvoSys Customer", slug="evo-cust", plan="standard",
                       platform_id=other.id)
    db_session.add(org)
    db_session.commit()
    db_session.add(Platform(name="BookaBoost", slug="bookaboost",
                            public_intake_organization_id=org.id))
    db_session.commit()

    r = client.post("/leads/demo-request", json={"first_name": "Dana"})
    assert r.status_code == 503
    assert db_session.query(Lead).count() == 0


def test_two_configured_brands_require_the_caller_to_say_which(
        client, db_session, sample_org, other_org):
    """Ambiguity surfaces as a refusal, not as a lead in the wrong workspace."""
    _configure_intake(db_session, sample_org, slug="bookaboost",
                      name="BookaBoost", website_url="https://bookaboost.live")
    _configure_intake(db_session, other_org, slug="evosyspro",
                      name="EvoSys Pro", website_url="https://evosyspro.live")

    assert client.post("/leads/demo-request",
                       json={"first_name": "Dana"}).status_code == 503

    named = client.post("/leads/demo-request",
                        json={"first_name": "Dana", "platform_slug": "evosyspro"})
    assert named.status_code == 201, named.text
    assert db_session.query(Lead).one().organization_id == other_org.id


def test_origin_selects_among_configured_brands(
        client, db_session, sample_org, other_org):
    _configure_intake(db_session, sample_org, slug="bookaboost",
                      name="BookaBoost", website_url="https://bookaboost.live")
    _configure_intake(db_session, other_org, slug="evosyspro",
                      name="EvoSys Pro", website_url="https://evosyspro.live")

    r = client.post("/leads/demo-request", json={"first_name": "Dana"},
                    headers={"Origin": "https://www.bookaboost.live"})
    assert r.status_code == 201, r.text
    assert db_session.query(Lead).one().organization_id == sample_org.id


def test_both_legacy_payload_shapes_are_accepted(client, db_session, sample_org):
    """Neither old caller breaks: one sent last_name+phone+notes+source, the
    other sent company+industry+message with first_name alone."""
    _configure_intake(db_session, sample_org)

    old_a = client.post("/leads/demo-request", json={
        "first_name": "Ann", "last_name": "Lee", "phone": "+12145557001",
        "email": "ann@example.com", "notes": "from the old form",
        "source": "landing_page", "tier": "demo_request"})
    assert old_a.status_code == 201, old_a.text
    assert old_a.json()["status"] == "created"

    old_b = client.post("/leads/demo-request", json={
        "first_name": "Ben", "company": "Ben Co", "industry": "roofing",
        "message": "call me", "email": "ben@example.com"})
    assert old_b.status_code == 201, old_b.text
    assert old_b.json()["success"] is True

    assert db_session.query(Lead).count() == 2


def test_a_repeat_submission_updates_rather_than_duplicating(
        client, db_session, sample_org):
    _configure_intake(db_session, sample_org)

    body = {"first_name": "Dana", "phone": "+12145557002", "message": "first"}
    assert client.post("/leads/demo-request", json=body).status_code == 201
    second = client.post("/leads/demo-request",
                         json={**body, "message": "second"})
    assert second.status_code == 201
    assert second.json()["status"] == "updated"
    assert db_session.query(Lead).count() == 1
    assert "second" in db_session.query(Lead).one().notes


# ── 4b. SMS opt-in: the same defect, found while fixing the first ───────────

def test_sms_optin_lands_in_the_configured_organization(
        client, db_session, sample_org):
    _configure_intake(db_session, sample_org)
    r = client.post("/leads/sms-optin", json={
        "first_name": "Cara", "phone": "+12145558001", "consent": True})
    assert r.status_code == 201, r.text
    lead = db_session.query(Lead).one()
    assert lead.organization_id == sample_org.id


def test_sms_optin_refuses_rather_than_taking_the_first_active_org(
        client, db_session):
    """A CONSENT RECORD IN THE WRONG COMPANY'S NAME IS WORSE THAN NO RECORD.

    This route read `Organization.is_active == True ... .first()` with a comment
    saying "Route to the first active organization (Restland)". What it writes
    is the TCPA/A2P evidence that a named person agreed to be texted by a named
    business, so a misfiled row is a compliance defect, not an inconvenience.
    """
    for i, name in enumerate(("Alpha Funeral", "Beta Funeral")):
        db_session.add(Organization(name=name, slug="optin-org-%d" % i,
                                    plan="standard", is_active=True))
    db_session.commit()

    r = client.post("/leads/sms-optin", json={
        "first_name": "Cara", "phone": "+12145558002", "consent": True})
    assert r.status_code == 503
    assert db_session.query(Lead).count() == 0


def test_sms_optin_still_requires_consent(client, db_session, sample_org):
    _configure_intake(db_session, sample_org)
    r = client.post("/leads/sms-optin", json={
        "first_name": "Cara", "phone": "+12145558003", "consent": False})
    assert r.status_code == 400
    assert db_session.query(Lead).count() == 0


def test_public_intake_writes_are_throttled(client, db_session, sample_org):
    """Unauthenticated rows in a real customer's workspace need a ceiling."""
    _configure_intake(db_session, sample_org)
    statuses = []
    for i in range(30):
        r = client.post("/leads/demo-request",
                        json={"first_name": "Bot%d" % i,
                              "phone": "+1214555%04d" % (9000 + i)})
        statuses.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in statuses, "public demo-request writes were unthrottled"


# ── 5. Login throttle ───────────────────────────────────────────────────────

def test_login_still_works_normally(client, db_session, sample_advisor):
    r = client.post("/auth/login",
                    data={"username": sample_advisor.email,
                          "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]


def test_wrong_password_still_returns_401_not_429(client, sample_advisor):
    r = client.post("/auth/login",
                    data={"username": sample_advisor.email, "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password"


def test_password_spraying_across_many_accounts_is_throttled(client, db_session):
    """THE ATTACK THE OLD THROTTLE COULD NOT SEE.

    `_login_throttle_check` keys on (IP, email), so one attempt against each of
    a hundred addresses never reaches any bucket's tenth failure. The per-address
    ceiling is what stops it, and this is the test that proves it is there.
    """
    statuses = []
    for i in range(45):
        r = client.post("/auth/login",
                        data={"username": "victim%d@example.com" % i,
                              "password": "Password1!"})
        statuses.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in statuses, "spraying distinct accounts was never throttled"


def test_the_throttle_response_does_not_reveal_whether_an_account_exists(
        client, db_session, sample_advisor):
    real, fake = [], []
    for i in range(60):
        target = real if i % 2 == 0 else fake
        email = sample_advisor.email if i % 2 == 0 else "ghost%d@example.com" % i
        r = client.post("/auth/login",
                        data={"username": email, "password": "WrongPass1!"})
        target.append((r.status_code, r.text))
        if r.status_code == 429:
            break

    limited = [t for t in (real + fake) if t[0] == 429]
    assert limited, "the ceiling never engaged"
    for _, text_body in limited:
        assert sample_advisor.email not in text_body
        assert "exist" not in text_body.lower()


def test_verify_is_throttled_like_login(client, sample_advisor):
    """The alias must not be a documented way around the ceiling."""
    statuses = []
    for i in range(45):
        r = client.post("/auth/verify",
                        data={"username": "spray%d@example.com" % i,
                              "password": "Password1!"})
        statuses.append(r.status_code)
        if r.status_code == 429:
            break
    assert 429 in statuses
