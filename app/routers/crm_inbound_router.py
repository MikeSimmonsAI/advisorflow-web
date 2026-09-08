"""Issue, rotate and revoke the per-organization CRM inbound credentials.

WHY THIS IS AN ENDPOINT AND NOT A SCRIPT. The Retell keys that share this model
are minted by an operator script, which is fine for two keys created once. A CRM
inbound key is per customer, and the standing rule for this platform is that
onboarding a customer must never need a shell, a seed script, or a developer.
So the operator does it from God Mode.

THE SECRET IS SHOWN EXACTLY ONCE. `issue` and `rotate` return the full key in
their response body and it is never recoverable afterwards - only a SHA-256 of
it is stored. Every list and detail response carries the non-secret prefix and
nothing else. If an operator loses a key they rotate it; there is no "show me
that key again", because a system that can show it to them can show it to
someone else.

ROTATION IS NOT REVOCATION. `rotate` replaces the secret on the SAME credential
row, so the integration keeps its identity, its scope and its whole request
history; the old secret stops working the instant the new one is stored.
`revoke` ends the credential permanently and is never undone by editing a flag -
issue a new one instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, User
from app.models.integration_models import (
    IntegrationCredential, IntegrationRequestLog,
    INTEGRATION_CRM_INBOUND, ACTION_INBOUND,
)
from app.routers.audit_log_router import log_action
from app.services import integration_auth

router = APIRouter(prefix="/god/crm-inbound", tags=["god-crm-inbound"])


class IssueRequest(BaseModel):
    organization_id: str
    # A human name, because "who was this?" is the first question anyone asks of
    # a log. Defaulted rather than required so issuing is one field in practice.
    name: Optional[str] = None
    note: Optional[str] = None


def _org_or_404(db: Session, organization_id: str) -> Organization:
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org


def _out(cred: IntegrationCredential) -> dict:
    """The safe view of a credential. There is no unsafe view."""
    return {
        "id": cred.id,
        "name": cred.name,
        "organization_id": cred.organization_id,
        "key_prefix": cred.key_prefix,
        "is_active": cred.is_active,
        "revoked_at": cred.revoked_at,
        "last_used_at": cred.last_used_at,
        "created_at": cred.created_at,
        "note": cred.note,
    }


@router.get("/tokens")
def list_tokens(organization_id: Optional[str] = Query(default=None),
                db: Session = Depends(get_db),
                current_user: User = Depends(require_god)):
    """Every CRM inbound credential, or one organization's.

    Prefixes only. Revoked credentials are included deliberately - "this key was
    revoked on that date" is exactly what someone investigating an incident
    needs, and hiding it would make the trail look like it never existed.
    """
    q = (db.query(IntegrationCredential)
         .filter(IntegrationCredential.kind == INTEGRATION_CRM_INBOUND))
    if organization_id:
        q = q.filter(IntegrationCredential.organization_id == organization_id)
    rows = q.order_by(IntegrationCredential.created_at.desc()).all()
    return {"tokens": [_out(c) for c in rows]}


@router.get("/usage")
def inbound_usage(organization_id: Optional[str] = Query(default=None),
                  limit: int = Query(default=100, le=500),
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_god)):
    """Recent inbound pushes, so "who is still on the legacy path?" is a query.

    `detail` carries the mode and the counts. A row whose `key_prefix` is NULL
    arrived with no credential at all - that is the legacy path, and the list of
    organizations appearing that way is the migration backlog.
    """
    q = (db.query(IntegrationRequestLog)
         .filter(IntegrationRequestLog.action == ACTION_INBOUND))
    if organization_id:
        q = q.filter(IntegrationRequestLog.organization_id == organization_id)
    rows = (q.order_by(IntegrationRequestLog.occurred_at.desc())
            .limit(limit).all())
    return {"requests": [{
        "occurred_at": r.occurred_at,
        "organization_id": r.organization_id,
        "key_prefix": r.key_prefix,
        "auth_mode": "legacy" if not r.key_prefix else "secure",
        "success": r.success,
        "status_code": r.status_code,
        "detail": r.detail,
    } for r in rows]}


@router.post("/tokens")
def issue_token(payload: IssueRequest,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_god)):
    """Mint a credential for one organization. The key is returned ONCE."""
    org = _org_or_404(db, payload.organization_id)

    full, prefix, hashed = integration_auth.generate_key()
    cred = IntegrationCredential(
        name=(payload.name or "CRM inbound — %s" % (org.name or org.id)),
        kind=INTEGRATION_CRM_INBOUND,
        key_prefix=prefix,
        key_hash=hashed,
        # Tenant scope only. Setting brand_sales_org_id here as well would make
        # `scope_kind()` raise and the credential unusable, which is the model
        # refusing to be ambiguous rather than picking a tree.
        organization_id=org.id,
        created_by=current_user.id,
        note=payload.note,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)

    # The audit row records the PREFIX. Writing the key here would put the
    # secret in the one table built to be read by people.
    log_action(db, org.id, current_user.id,
               action="crm_inbound.token.issue",
               target_type="integration_credential", target_id=cred.id,
               details={"key_prefix": cred.key_prefix, "name": cred.name})

    out = _out(cred)
    out["key"] = full
    out["key_shown_once"] = (
        "Store this now. It is hashed on the server and cannot be shown again; "
        "if it is lost, rotate the credential.")
    return out


@router.post("/tokens/{token_id}/rotate")
def rotate_token(token_id: str,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_god)):
    """Replace the secret, keep the credential.

    The old secret stops working immediately. There is no overlap window: a CRM
    that has not been updated will start failing, loudly, which is the correct
    outcome for a rotation an operator chose to perform.
    """
    cred = (db.query(IntegrationCredential)
            .filter(IntegrationCredential.id == token_id,
                    IntegrationCredential.kind == INTEGRATION_CRM_INBOUND)
            .first())
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found.")
    if not cred.is_usable():
        raise HTTPException(status_code=400,
                            detail="This credential is revoked. Issue a new one.")

    full, prefix, hashed = integration_auth.generate_key()
    old_prefix = cred.key_prefix
    cred.key_prefix = prefix
    cred.key_hash = hashed
    db.add(cred)
    db.commit()
    db.refresh(cred)

    log_action(db, cred.organization_id, current_user.id,
               action="crm_inbound.token.rotate",
               target_type="integration_credential", target_id=cred.id,
               details={"old_key_prefix": old_prefix,
                        "new_key_prefix": cred.key_prefix})

    out = _out(cred)
    out["key"] = full
    out["key_shown_once"] = (
        "Store this now. The previous key stopped working the moment this one "
        "was created.")
    return out


@router.post("/tokens/{token_id}/revoke")
def revoke_token(token_id: str,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(require_god)):
    """End a credential. Permanent, and it keeps its history."""
    cred = (db.query(IntegrationCredential)
            .filter(IntegrationCredential.id == token_id,
                    IntegrationCredential.kind == INTEGRATION_CRM_INBOUND)
            .first())
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found.")

    cred.is_active = False
    cred.revoked_at = cred.revoked_at or datetime.utcnow()
    db.add(cred)
    db.commit()
    db.refresh(cred)

    log_action(db, cred.organization_id, current_user.id,
               action="crm_inbound.token.revoke",
               target_type="integration_credential", target_id=cred.id,
               details={"key_prefix": cred.key_prefix})
    return _out(cred)


class SecureModeRequest(BaseModel):
    secure_required: bool


@router.post("/organizations/{org_id}/secure-mode")
def set_secure_mode(org_id: str,
                    payload: SecureModeRequest,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_god)):
    """Close (or reopen) the legacy unauthenticated path for one organization.

    Closing it is the last step of a migration, not the first: do it once the
    customer's CRM is demonstrably sending the key, which `GET /god/crm-inbound/
    usage` will show as `auth_mode: secure` for that organization.

    Reopening is allowed because a migration can go wrong at the customer's end
    and leaving them unable to deliver leads is worse than a few more days on
    the old path. Both directions are audited.
    """
    org = _org_or_404(db, org_id)
    was = bool(getattr(org, "crm_inbound_secure_required", False))
    org.crm_inbound_secure_required = bool(payload.secure_required)
    db.add(org)
    db.commit()

    log_action(db, org.id, current_user.id,
               action="crm_inbound.secure_mode.set",
               target_type="organization", target_id=org.id,
               details={"from": was, "to": bool(payload.secure_required)})
    return {
        "organization_id": org.id,
        "crm_inbound_secure_required": bool(payload.secure_required),
        "legacy_accepted": not bool(payload.secure_required),
    }
