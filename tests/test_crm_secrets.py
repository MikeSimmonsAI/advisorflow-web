"""
CRM-02 — CRM connection API keys must be encrypted at rest.

The column is named `api_key_encrypted` and was storing plaintext.
`app/services/crm_secrets.py` supplies store_secret / read_secret / migrate,
and `app/main.py` runs the migration at startup.

These tests prove:
 1. store_secret returns ciphertext — NOT the plaintext key
 2. read_secret round-trips ciphertext back to plaintext
 3. read_secret also handles legacy plaintext (backward-compat during migration)
 4. The migration converts plaintext rows and skips already-encrypted ones
 5. looks_encrypted discriminates correctly

ENCRYPTION_KEY must be set in the test environment (conftest sets it).
"""
import os
import pytest

from app.services.crm_secrets import (
    store_secret,
    read_secret,
    looks_encrypted,
    migrate_crm_connection_secrets,
    SECRET_COLUMNS,
)


# ─── basic round-trip ─────────────────────────────────────────────────────────

class TestStoreAndRead:

    def test_store_returns_ciphertext_not_plaintext(self):
        """THE CRM-02 PROOF: the stored bytes are NOT the plaintext key."""
        plaintext = "super-secret-api-key-1234"
        stored = store_secret(plaintext)
        assert stored != plaintext, (
            "store_secret must encrypt; the stored value must not equal the input"
        )

    def test_stored_value_is_not_empty(self):
        stored = store_secret("any-key")
        assert stored, "store_secret must not return empty for a non-empty input"

    def test_round_trip(self):
        plaintext = "my-crm-api-key"
        stored = store_secret(plaintext)
        recovered = read_secret(stored)
        assert recovered == plaintext

    def test_none_passes_through_store(self):
        """None means 'caller did not send this field'; preserve that distinction."""
        assert store_secret(None) is None

    def test_empty_string_round_trips(self):
        """Empty string means 'caller cleared the field'."""
        stored = store_secret("")
        recovered = read_secret(stored)
        assert recovered == ""



# ─── read_secret backward compat ──────────────────────────────────────────────

class TestReadSecretLegacy:

    def test_read_legacy_plaintext_returns_it_unchanged(self):
        """During migration old rows still hold plaintext; read_secret must serve them."""
        legacy = "plaintext-key-from-old-row"
        result = read_secret(legacy)
        assert result == legacy

    def test_read_none_returns_empty_string(self):
        assert read_secret(None) == ""

    def test_read_empty_string_returns_empty_string(self):
        assert read_secret("") == ""


# ─── looks_encrypted ──────────────────────────────────────────────────────────

class TestLooksEncrypted:

    def test_ciphertext_looks_encrypted(self):
        stored = store_secret("some-key")
        assert looks_encrypted(stored) is True

    def test_plaintext_does_not_look_encrypted(self):
        assert looks_encrypted("plaintext-key") is False

    def test_none_does_not_look_encrypted(self):
        assert looks_encrypted(None) is False

    def test_empty_does_not_look_encrypted(self):
        assert looks_encrypted("") is False


# ─── migration ────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_engine_for_migration():
    """In-memory SQLite engine with a crm_connections table for migration tests."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.connect() as conn:
        conn.execute(text("""
            CREATE TABLE crm_connections (
                id TEXT PRIMARY KEY,
                organization_id TEXT,
                provider TEXT,
                api_key_encrypted TEXT,
                webhook_secret TEXT,
                created_at TIMESTAMP
            )
        """))
        conn.commit()
    yield eng
    eng.dispose()


class TestMigration:

    def test_migration_converts_plaintext_row(self, db_engine_for_migration):
        """
        Given a crm_connections row with a plaintext api_key_encrypted value,
        migrate_crm_connection_secrets should encrypt it in place.
        """
        engine = db_engine_for_migration
        plaintext_key = "plaintext-crm-key-needs-migration"

        # Insert a row with plaintext
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO crm_connections (id, organization_id, provider, "
                "api_key_encrypted, webhook_secret, created_at) "
                "VALUES ('mig-test-1', 'org-1', 'test', :key, NULL, CURRENT_TIMESTAMP)"
            ), {"key": plaintext_key})
            conn.commit()

        # Run migration
        summary = migrate_crm_connection_secrets(engine)

        # Check the row is now encrypted
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT api_key_encrypted FROM crm_connections WHERE id = 'mig-test-1'"
            )).fetchone()

        stored = row[0]
        assert stored != plaintext_key, "Migration must encrypt the stored value"
        assert looks_encrypted(stored), "Migrated value must be readable as ciphertext"
        assert read_secret(stored) == plaintext_key, "Must round-trip to original"
        assert summary["encrypted"] >= 1

    def test_migration_skips_already_encrypted_row(self, db_engine_for_migration):
        """Already-encrypted rows must not be double-encrypted."""
        engine = db_engine_for_migration
        plaintext_key = "already-encrypted-key"
        ciphertext = store_secret(plaintext_key)

        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO crm_connections (id, organization_id, provider, "
                "api_key_encrypted, webhook_secret, created_at) "
                "VALUES ('mig-test-2', 'org-1', 'test', :key, NULL, CURRENT_TIMESTAMP)"
            ), {"key": ciphertext})
            conn.commit()

        summary = migrate_crm_connection_secrets(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT api_key_encrypted FROM crm_connections WHERE id = 'mig-test-2'"
            )).fetchone()

        # Must be unchanged
        assert row[0] == ciphertext, "Already-encrypted row must not be touched"
        assert summary["already"] >= 1

