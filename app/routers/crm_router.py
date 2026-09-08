"""
CRM Integration Router — BookaBoost

Endpoints:
  GET    /crm/connections             list org's CRM connections
  POST   /crm/connections             create a new connection
  PUT    /crm/connections/{id}        update a connection
  DELETE /crm/connections/{id}        remove a connection
  POST   /crm/connections/{id}/test   send a test webhook
  POST   /crm/inbound/{org_id}        receive leads pushed FROM a CRM (pull-in)

SECRETS. `webhook_secret` and `api_key_encrypted` are encrypted at rest with the
platform's Fernet key (app/services/crm_secrets.py) and are never returned by
any endpoint here. They were plaintext until 2026-09-08 — the api_key one inside
a column whose name said otherwise.

INBOUND AUTH. `/crm/inbound/{org_id}` no longer treats the organization UUID as
a credential. It takes a scoped IntegrationCredential of kind `crm_inbound`, and
temporarily still accepts unauthenticated calls from organizations that have not
yet migrated — explicitly, visibly, and never described as secure. See the
docstring on `inbound_leads`.
"""

import ipaddress
import json
import logging
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.deps import get_db, get_current_user, require_tenant_user
from app.models.models import User
from app.services import crm_service, crm_secrets
from app.services import integration_auth
from app.models.integration_models import (
    IntegrationRequestLog, INTEGRATION_CRM_INBOUND, ACTION_INBOUND, SCOPE_TENANT,
)

router = APIRouter(prefix="/crm", tags=["crm"])

_SAFE_URL_SCHEMES = ("http://", "https://")
# RFC-1918 + link-local + loopback ranges that must never be fetched
_BLOCKED_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS/GCP metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_webhook_url(url: Optional[str]) -> Optional[str]:
    """Validate webhook_url is a public http/https URL (blocks SSRF to internal networks)."""
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(_SAFE_URL_SCHEMES):
        raise HTTPException(status_code=400, detail="webhook_url must be an http or https URL.")
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    # Reject bare IPs in private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _BLOCKED_NETS:
            if addr in net:
                raise HTTPException(status_code=400, detail="webhook_url must point to a public host.")
    except ValueError:
        pass  # hostname is a domain name — allow it (DNS resolution happens at send time, not here)
    return url


# ── Schemas ───────────────────────────────────────────────────────────────────

class CRMConnectionCreate(BaseModel):
    name: str
    crm_type: str = "webhook"          # webhook | gohighlevel | hubspot
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    # Encrypted with the platform's Fernet key before it reaches the database,
    # and never returned by any endpoint. See app/services/crm_secrets.py.
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    sync_mode: str = "push_only"       # push_only | pull_only | two_way
    push_events: list[str] = ["booking", "status_change"]
    annotation_tag: str = ""  # defaults to the org's platform brand name at send time
    active: bool = True


class CRMConnectionUpdate(BaseModel):
    name: Optional[str] = None
    crm_type: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    api_key: Optional[str] = None
    api_base_url: Optional[str] = None
    sync_mode: Optional[str] = None
    push_events: Optional[list[str]] = None
    annotation_tag: Optional[str] = None
    active: Optional[bool] = None


class InboundLeadRecord(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    tier: Optional[str] = None
    tag: Optional[str] = None
    source_year: Optional[str] = None


class InboundPayload(BaseModel):
    records: Optional[list[InboundLeadRecord]] = None
    # Some CRMs send a single contact at root level
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_org_id(current_user: User) -> str:
    return str(current_user.organization_id)


def _require_admin(current_user: User):
    # User model uses `role`, not is_admin/is_super_admin attributes.
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin required")


def _get_connection_or_404(db: Session, conn_id: str, org_id: str) -> dict:
    row = db.execute(
        text("SELECT * FROM crm_connections WHERE id = :id AND organization_id = :org_id"),
        {"id": conn_id, "org_id": org_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="CRM connection not found")
    return dict(row)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/connections")
def list_connections(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    _require_admin(current_user)
    org_id = _get_org_id(current_user)
    rows = db.execute(
        text("SELECT * FROM crm_connections WHERE organization_id = :org_id ORDER BY created_at DESC"),
        {"org_id": org_id},
    ).mappings().all()
    results = []
    for row in rows:
        d = dict(row)
        # Parse push_events JSON
        try:
            d["push_events"] = json.loads(d.get("push_events") or "[]")
        except Exception:
            d["push_events"] = []
        # Hide secrets from response
        d.pop("webhook_secret", None)
        d.pop("api_key_encrypted", None)
        results.append(d)
    return results


@router.post("/connections")
def create_connection(
    payload: CRMConnectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    _require_admin(current_user)
    org_id = _get_org_id(current_user)

    conn_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO crm_connections
            (id, organization_id, name, crm_type, webhook_url, webhook_secret,
             api_key_encrypted, api_base_url, sync_mode, push_events,
             annotation_tag, active, created_at)
        VALUES
            (:id, :org_id, :name, :crm_type, :webhook_url, :webhook_secret,
             :api_key, :api_base_url, :sync_mode, :push_events,
             :annotation_tag, :active, CURRENT_TIMESTAMP)
    """), {
        "id": conn_id,
        "org_id": org_id,
        "name": payload.name,
        "crm_type": payload.crm_type,
        "webhook_url": _validate_webhook_url(payload.webhook_url),
        # ENCRYPTED ON THE WAY IN. Both of these were written as plaintext, the
        # api_key one into a column literally named `api_key_encrypted`.
        "webhook_secret": crm_secrets.store_secret(payload.webhook_secret),
        "api_key": crm_secrets.store_secret(payload.api_key),
        "api_base_url": payload.api_base_url,
        "sync_mode": payload.sync_mode,
        "push_events": json.dumps(payload.push_events),
        "annotation_tag": payload.annotation_tag,
        "active": payload.active,
    })
    db.commit()
    return {"id": conn_id, "message": "CRM connection created"}


@router.put("/connections/{conn_id}")
def update_connection(
    conn_id: str,
    payload: CRMConnectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    _require_admin(current_user)
    org_id = _get_org_id(current_user)
    _get_connection_or_404(db, conn_id, org_id)  # verify ownership

    updates = {}
    if payload.name is not None:
        updates["name"] = payload.name
    if payload.crm_type is not None:
        updates["crm_type"] = payload.crm_type
    if payload.webhook_url is not None:
        updates["webhook_url"] = _validate_webhook_url(payload.webhook_url)
    if payload.webhook_secret is not None:
        updates["webhook_secret"] = crm_secrets.store_secret(payload.webhook_secret)
    if payload.api_key is not None:
        updates["api_key_encrypted"] = crm_secrets.store_secret(payload.api_key)
    if payload.api_base_url is not None:
        updates["api_base_url"] = payload.api_base_url
    if payload.sync_mode is not None:
        updates["sync_mode"] = payload.sync_mode
    if payload.push_events is not None:
        updates["push_events"] = json.dumps(payload.push_events)
    if payload.annotation_tag is not None:
        updates["annotation_tag"] = payload.annotation_tag
    if payload.active is not None:
        updates["active"] = payload.active

    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = conn_id
        db.execute(text(f"UPDATE crm_connections SET {set_clause} WHERE id = :id"), updates)
        db.commit()

    return {"message": "Updated"}


@router.delete("/connections/{conn_id}")
def delete_connection(
    conn_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    _require_admin(current_user)
    org_id = _get_org_id(current_user)
    _get_connection_or_404(db, conn_id, org_id)

    db.execute(text("DELETE FROM crm_connections WHERE id = :id"), {"id": conn_id})
    db.commit()
    return {"message": "Deleted"}


@router.post("/connections/{conn_id}/test")
def test_connection(
    conn_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    _require_admin(current_user)
    org_id = _get_org_id(current_user)
    conn = _get_connection_or_404(db, conn_id, org_id)

    # Build a fake lead object for the test payload
    class FakeLead:
        id = "test-lead-id"
        first_name = "Test"
        last_name = "Lead"
        email = "test@example.com"
        phone = "+15555550001"
        status = "new"
        tier = "A"
        source_year = str(datetime.utcnow().year)

    fake = FakeLead()

    if conn.get("crm_type") == "gohighlevel":
        result = crm_service._push_gohighlevel(
            conn, "test", fake, {"note": "BookaBoost test webhook"}, conn.get("annotation_tag") or "BookaBoost"
        )
    elif conn.get("crm_type") == "hubspot":
        result = crm_service._push_hubspot(
            conn, "test", fake, {"note": "BookaBoost test webhook"}, conn.get("annotation_tag") or "BookaBoost"
        )
    else:
        payload = crm_service._build_payload(
            "test", fake, {"note": "BookaBoost test webhook"}, conn.get("annotation_tag") or "BookaBoost"
        )
        result = crm_service._push_webhook(conn, payload)

    return {
        "success": result.get("success", False),
        "detail": result,
    }


MODE_SECURE = "secure"
MODE_LEGACY = "legacy"

# One refusal for every way of failing. A caller must not be able to tell
# "wrong key" from "no such organization" from "legacy is closed here" — each
# distinction is free reconnaissance for whoever is probing.
_INBOUND_REFUSED = "Invalid or missing integration credential."


def _log_inbound(db: Session, *, org_id: str, cred, mode: str,
                 success: bool, status_code: int, detail: str) -> None:
    """Append-only record of one inbound push. Never writes a secret.

    Only the non-secret key prefix is stored, exactly as the Retell integration
    surface does. A legacy call has no credential at all, and says so, which is
    what makes "who is still on the old path?" a query instead of a guess.
    """
    try:
        db.add(IntegrationRequestLog(
            credential_id=cred.id if cred else None,
            integration_name=(cred.name if cred else "crm-inbound-legacy"),
            key_prefix=(cred.key_prefix if cred else None),
            action=ACTION_INBOUND,
            organization_id=org_id,
            success=success,
            status_code=status_code,
            detail="mode=%s %s" % (mode, detail),
        ))
        db.commit()
    except Exception:
        db.rollback()
        # Observability must never be the reason an accepted lead is lost.
        logging.getLogger(__name__).exception(
            "crm inbound: could not write request log for org %s", org_id)


@router.post("/inbound/{org_id}")
def inbound_leads(
    org_id: str,
    payload: InboundPayload,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """A customer's CRM pushes contacts into that customer's workspace.

    TWO MODES, AND THEY ARE NEVER CONFUSED FOR EACH OTHER.

    SECURE — `Authorization: Bearer <key>`, an IntegrationCredential of kind
    `crm_inbound` scoped to THIS organization. The key is matched by its
    non-secret prefix and then verified with a constant-time compare against a
    stored SHA-256; the secret itself is not in the database to be stolen. A key
    issued for another organization is refused here, and a Retell key of either
    kind is refused outright — scope is fixed at issue time and cannot be
    widened by the caller.

    LEGACY — no credential, admitted only while this organization still has
    `crm_inbound_secure_required` unset. This exists because the endpoint used
    to accept the organization UUID as its entire credential, and a customer's
    CRM may be posting that way right now; closing it on deploy would break a
    live integration silently. Every legacy call is logged, audited, and
    answered with `Deprecation` and `Warning` headers plus `auth_mode: legacy`
    in the body, so the mode is visible to the caller and countable by us. It is
    never described as secure.

    A BAD CREDENTIAL IS NEVER DOWNGRADED TO LEGACY. If a Bearer token is present
    it must be valid for this organization, whatever the organization's mode —
    otherwise an attacker could get in simply by sending a wrong key badly.
    """
    from app.models.models import Organization

    org = db.query(Organization).filter_by(id=org_id).first()
    presented = integration_auth.presented_bearer(request)

    # ORGANIZATION EXISTENCE IS NOT LEAKED. An unknown org id and a known one
    # the caller has no key for return the same 401, so this endpoint cannot be
    # used to confirm that an organization exists.
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=_INBOUND_REFUSED,
                            headers={"WWW-Authenticate": "Bearer"})

    cred = None
    mode = MODE_LEGACY

    if presented:
        cred = integration_auth.resolve_credential(db, presented)
        scope_ok = False
        if cred is not None:
            try:
                scope_ok = (cred.kind == INTEGRATION_CRM_INBOUND
                            and cred.scope_kind() == SCOPE_TENANT
                            and str(cred.organization_id) == str(org_id))
            except ValueError:
                # Scoped to both trees or neither: unresolvable, so refused.
                scope_ok = False
        if not scope_ok:
            _log_inbound(db, org_id=org_id, cred=None, mode=MODE_SECURE,
                         success=False, status_code=401,
                         detail="credential rejected")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=_INBOUND_REFUSED,
                                headers={"WWW-Authenticate": "Bearer"})
        mode = MODE_SECURE
        try:
            cred.last_used_at = datetime.utcnow()
            db.add(cred)
            db.flush()
        except Exception:
            pass  # bookkeeping only

    elif getattr(org, "crm_inbound_secure_required", False):
        _log_inbound(db, org_id=org_id, cred=None, mode=MODE_LEGACY,
                     success=False, status_code=401,
                     detail="legacy closed for this organization")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=_INBOUND_REFUSED,
                            headers={"WWW-Authenticate": "Bearer"})

    records: list[dict] = []
    if payload.records:
        records = [r.dict() for r in payload.records]
    else:
        # Single contact at root
        root = payload.dict(exclude={"records"})
        if any(v for v in root.values()):
            records = [root]

    if not records:
        result = {"created": 0, "skipped": 0, "total": 0}
    else:
        result = crm_service.import_inbound_leads(db, org_id, records)

    _log_inbound(
        db, org_id=org_id, cred=cred, mode=mode, success=True, status_code=200,
        detail="created=%s skipped=%s held=%s total=%s" % (
            result.get("created"), result.get("skipped"),
            result.get("held_over_capacity"), result.get("total")),
    )

    result = dict(result)
    result["auth_mode"] = mode
    if mode == MODE_LEGACY:
        # Say it in the protocol, not only in our logs. A CRM vendor reading
        # response headers finds out before we have to write to them.
        response.headers["Deprecation"] = "true"
        response.headers["Warning"] = (
            '299 - "Unauthenticated CRM inbound is deprecated. Request a '
            'per-organization integration key and send it as an Authorization: '
            'Bearer header."')
        result["deprecation"] = (
            "This endpoint was called without an integration credential. "
            "Unauthenticated inbound is deprecated and will be closed for this "
            "organization once its integration is migrated.")
    return result
