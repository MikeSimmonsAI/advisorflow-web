"""Service-credential authentication for trusted external integrations.

ONE WAY IN. `Authorization: Bearer <key>`. Not a query parameter, because query
strings land in access logs, browser history and referrer headers. Not a custom
header as well as this one, because two accepted forms means two things to keep
correct and one of them eventually rots.

FAIL CLOSED, AND SAY NOTHING. Missing, malformed, unknown, inactive and revoked
all produce the same 401 with the same message. A caller must not be able to
tell "that key does not exist" from "that key is revoked" — the difference is
free intelligence for anyone probing.

THIS IS NOT A LOGIN. It resolves to an `IntegrationCredential`, never to a
`User`. Nothing here can be handed to `get_current_user`, and no route outside
the integration surface accepts it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.integration_models import (
    IntegrationCredential, KEY_PREFIX_LEN, INTEGRATION_RETELL,
    INTEGRATION_RETELL_TENANT, SCOPE_BRAND, SCOPE_TENANT,
)

log = logging.getLogger(__name__)

# Recognisable at a glance in a config screen or a support ticket, and greppable
# if one is ever pasted somewhere it should not be.
KEY_NAMESPACE = "evsk"

# One message for every failure mode. See the module docstring.
_REFUSED = "Invalid or missing integration credential."


def generate_key() -> Tuple[str, str, str]:
    """Mint a new key. Returns (full_key, prefix, sha256_hash).

    The full key is returned to the caller ONCE and is never persisted. 32 bytes
    from `secrets.token_urlsafe` is ~256 bits of entropy; there is nothing here
    for an offline attack to chew on, which is why a plain SHA-256 is the right
    store rather than a deliberately slow password hash.
    """
    body = secrets.token_urlsafe(32)
    full = "%s_%s" % (KEY_NAMESPACE, body)
    return full, full[:KEY_PREFIX_LEN], hash_key(full)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def resolve_credential(db: Session, raw_key: Optional[str],
                       now: Optional[datetime] = None) -> Optional[IntegrationCredential]:
    """Raw key -> credential, or None. Never raises, never explains.

    Looked up by the non-secret prefix so this is one indexed query rather than
    a scan, then verified against the stored hash with a constant-time compare —
    the prefix narrows the search, it never authorises anything by itself.
    """
    if not raw_key or len(raw_key) <= KEY_PREFIX_LEN:
        return None
    try:
        row = (db.query(IntegrationCredential)
               .filter(IntegrationCredential.key_prefix == raw_key[:KEY_PREFIX_LEN])
               .first())
    except Exception:
        log.exception("integration credential lookup failed")
        return None
    if row is None:
        return None
    if not hmac.compare_digest(row.key_hash or "", hash_key(raw_key)):
        return None
    if not row.is_usable(now):
        return None
    return row


def require_integration(request: Request,
                        db: Session = Depends(get_db)) -> IntegrationCredential:
    """FastAPI dependency. The boundary for every integration route."""
    cred = resolve_credential(db, _bearer(request))
    if cred is None:
        # 401 with a WWW-Authenticate header: this is a missing-or-bad
        # credential, which is what 401 means. 403 would imply we know who they
        # are and are refusing them, and we do not know who they are.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=_REFUSED,
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        cred.last_used_at = datetime.utcnow()
        db.add(cred)
        db.flush()
    except Exception:
        # Usage bookkeeping must never be the reason a request fails.
        log.exception("could not stamp last_used_at for %s", cred.key_prefix)
    return cred


def _require_kind(cred: IntegrationCredential, kind: str,
                  scope: str) -> IntegrationCredential:
    """Both halves must agree: the declared kind AND the actual scope columns.

    Checking `kind` alone would trust a single string field to keep the two
    tenancy trees apart. A row mislabelled by a future script — or by a bug in
    the issuing tool — would then be admitted to the wrong tree with the wrong
    scope id, which is the one failure this design exists to prevent. So the
    scope columns are consulted independently and must match.
    """
    if (cred.kind or "") != kind:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=_REFUSED,
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        actual = cred.scope_kind()
    except ValueError:
        # Scoped to both trees or to neither. Unresolvable, so refused.
        log.error("integration credential %s has an unusable scope", cred.key_prefix)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=_REFUSED,
                            headers={"WWW-Authenticate": "Bearer"})
    if actual != scope:
        log.error("integration credential %s declares kind %r but is scoped %r",
                  cred.key_prefix, cred.kind, actual)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=_REFUSED,
                            headers={"WWW-Authenticate": "Bearer"})
    return cred


def require_retell(cred: IntegrationCredential = Depends(require_integration)
                   ) -> IntegrationCredential:
    """A key issued for a different integration cannot drive the Retell routes.

    Same refusal text as an unknown key: a caller holding the wrong kind of key
    learns only that it did not work.
    """
    return _require_kind(cred, INTEGRATION_RETELL, SCOPE_BRAND)


def require_retell_tenant(cred: IntegrationCredential = Depends(require_integration)
                          ) -> IntegrationCredential:
    """The customer-tenant half. A brand-sales key is refused here, and a tenant
    key is refused by `require_retell` — the two surfaces share a key format and
    nothing else."""
    return _require_kind(cred, INTEGRATION_RETELL_TENANT, SCOPE_TENANT)


def rate_limit_key(request: Request) -> str:
    """Rate-limit per CREDENTIAL, falling back to the caller's address.

    Keying on the remote address alone would be wrong in both directions: every
    integration behind one vendor's egress IP would share a bucket, and a key
    used from a rotating IP pool would have no bucket at all. The prefix is the
    non-secret part, so nothing sensitive reaches the limiter's key space.
    """
    token = _bearer(request)
    if token and len(token) > KEY_PREFIX_LEN:
        return "intg:%s" % token[:KEY_PREFIX_LEN]
    client = getattr(request, "client", None)
    return "intg-anon:%s" % (getattr(client, "host", None) or "unknown")
