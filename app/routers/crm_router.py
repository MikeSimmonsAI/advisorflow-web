"""
CRM Integration Router — BookaBoost

Endpoints:
  GET    /crm/connections             list org's CRM connections
  POST   /crm/connections             create a new connection
  PUT    /crm/connections/{id}        update a connection
  DELETE /crm/connections/{id}        remove a connection
  POST   /crm/connections/{id}/test   send a test webhook
  POST   /crm/inbound/{org_id}        receive leads pushed FROM a CRM (pull-in)
"""

import ipaddress
import json
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.deps import get_db, get_current_user, require_tenant_user
from app.models.models import User
from app.services import crm_service

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
    api_key: Optional[str] = None      # stored as api_key_encrypted (plaintext for now)
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
             :annotation_tag, :active, NOW())
    """), {
        "id": conn_id,
        "org_id": org_id,
        "name": payload.name,
        "crm_type": payload.crm_type,
        "webhook_url": _validate_webhook_url(payload.webhook_url),
        "webhook_secret": payload.webhook_secret,
        "api_key": payload.api_key,
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
        updates["webhook_secret"] = payload.webhook_secret
    if payload.api_key is not None:
        updates["api_key_encrypted"] = payload.api_key
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


@router.post("/inbound/{org_id}")
def inbound_leads(
    org_id: str,
    payload: InboundPayload,
    db: Session = Depends(get_db),
):
    """
    CRM pushes contacts here. No auth required — org_id serves as the token
    (it's a UUID, opaque to external parties).
    Accepts either a `records` array or a single contact at the root level.
    """
    from app.models.models import Organization
    org = db.query(Organization).filter_by(id=org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    records: list[dict] = []
    if payload.records:
        records = [r.dict() for r in payload.records]
    else:
        # Single contact at root
        root = payload.dict(exclude={"records"})
        if any(v for v in root.values()):
            records = [root]

    if not records:
        return {"created": 0, "skipped": 0, "total": 0}

    result = crm_service.import_inbound_leads(db, org_id, records)
    return result
