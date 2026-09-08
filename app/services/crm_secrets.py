"""Encryption at rest for CRM connection secrets, and the migration to it.

WHAT WAS WRONG. `crm_connections.api_key_encrypted` held PLAINTEXT. The column
name said otherwise, which is worse than an honestly-named plaintext column:
anyone reading the schema, or reviewing a change near it, would reasonably
conclude the value was already protected and look no further. `webhook_secret`
sat beside it in the same state.

The platform has had the primitive the whole time - `app/utils/crypto.py`,
Fernet keyed from ENCRYPTION_KEY - and uses it for Twilio auth tokens,
Microsoft and Google refresh tokens and Zoom host URLs. This path simply never
called it.

TWO PROPERTIES THIS FILE EXISTS TO GUARANTEE.

1. READS WORK THROUGHOUT THE MIGRATION. `read_secret` accepts both shapes. A
   row encrypted by the boot migration and a row still holding plaintext both
   return the same usable string, so no customer's CRM push breaks between the
   deploy and the migration finishing.

2. THE MIGRATION IS IDEMPOTENT. `looks_encrypted` decides by ATTEMPTING A
   DECRYPT, not by pattern-matching the string. A plaintext API key that
   happened to start with Fernet's `gAAAAA` prefix would be double-encrypted by
   a prefix check and then be unrecoverable; a real ciphertext that failed a
   prefix check would be encrypted twice and read back as ciphertext. Trying the
   operation that actually matters is the only check that cannot be fooled.

NOTHING HERE LOGS A VALUE. Not on success, not on failure, not in an exception
message. The only identifiers that reach a log line are connection ids.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text

from app.utils.crypto import encrypt_value, decrypt_value

log = logging.getLogger(__name__)

# Every secret column on crm_connections. Adding one here is all it takes for
# the boot migration to cover it.
SECRET_COLUMNS = ("api_key_encrypted", "webhook_secret")


def looks_encrypted(value: Optional[str]) -> bool:
    """True when `value` is ciphertext this deployment's key can open.

    Deliberately answers by decrypting. See the module docstring for why a
    prefix check is not good enough. An empty value is not encrypted and is not
    plaintext either - it is nothing - so it reports False and is skipped by
    every caller.
    """
    if not value:
        return False
    try:
        decrypt_value(value)
        return True
    except Exception:
        # InvalidToken for plaintext; RuntimeError when ENCRYPTION_KEY is unset.
        # Both mean "do not treat this as ciphertext", which is the safe answer
        # for a migration: it will simply decline to convert.
        return False


def store_secret(plaintext: Optional[str]) -> Optional[str]:
    """Plaintext -> ciphertext for writing.

    None passes through as None so "the caller did not send this field" stays
    distinguishable from "the caller cleared it", which is the difference
    between leaving a key alone and deleting it. An empty string encrypts to an
    empty string via `encrypt_value`, which is how a cleared field is stored.
    """
    if plaintext is None:
        return None
    return encrypt_value(plaintext)


def read_secret(stored: Optional[str]) -> str:
    """Ciphertext or legacy plaintext -> the usable secret.

    Returns "" for a missing value, matching what every existing caller already
    did with `conn.get(...) or ""`.
    """
    if not stored:
        return ""
    try:
        return decrypt_value(stored)
    except Exception:
        # Legacy plaintext, or a value written under a different key. Returning
        # it unchanged is what kept working before this module existed; the boot
        # migration is what removes this branch's remaining work.
        return stored


def migrate_crm_connection_secrets(engine) -> dict:
    """Encrypt every plaintext secret still sitting in crm_connections.

    Safe to run on every boot: rows already encrypted are recognised and left
    alone, so this converges to zero work. Never raises into the caller - a
    startup migration must not be the reason the service fails to come up - and
    reports what it did so the boot log says so plainly.
    """
    summary = {"scanned": 0, "encrypted": 0, "already": 0, "skipped": 0}
    cols = ", ".join(SECRET_COLUMNS)
    try:
        with engine.connect() as conn:
            try:
                rows = conn.execute(
                    text("SELECT id, %s FROM crm_connections" % cols)
                ).mappings().all()
            except Exception as exc:
                # No table yet (a fresh database on its very first boot, or a
                # SQLite test run where the Postgres-only DDL was skipped).
                log.info("crm_secrets: crm_connections not readable yet (%s)",
                         type(exc).__name__)
                return summary

            for row in rows:
                summary["scanned"] += 1
                updates = {}
                for col in SECRET_COLUMNS:
                    value = row.get(col)
                    if not value:
                        continue
                    if looks_encrypted(value):
                        summary["already"] += 1
                        continue
                    try:
                        updates[col] = encrypt_value(value)
                    except Exception:
                        # ENCRYPTION_KEY missing or unusable. Leave the row
                        # exactly as it is: a half-converted secret column is
                        # worse than an unconverted one.
                        summary["skipped"] += 1
                        log.error("crm_secrets: cannot encrypt %s on connection "
                                  "%s - leaving it unchanged", col, row.get("id"))
                if updates:
                    set_clause = ", ".join("%s = :%s" % (k, k) for k in updates)
                    params = dict(updates)
                    params["id"] = row.get("id")
                    conn.execute(
                        text("UPDATE crm_connections SET %s WHERE id = :id"
                             % set_clause),
                        params,
                    )
                    summary["encrypted"] += len(updates)
            conn.commit()
    except Exception:
        log.exception("crm_secrets: migration pass failed")
        return summary

    if summary["encrypted"]:
        log.warning("crm_secrets: encrypted %d previously-plaintext secret(s) "
                    "across %d connection(s)", summary["encrypted"], summary["scanned"])
    return summary
